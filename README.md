# SDG&E Rate Plan Analysis

[![gitleaks](https://github.com/ookla-ariel-ride/SDGE-Analysis/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/ookla-ariel-ride/SDGE-Analysis/actions/workflows/gitleaks.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## [View the live report](https://ookla-ariel-ride.github.io/SDGE-Analysis/)

**https://ookla-ariel-ride.github.io/SDGE-Analysis/**

The report is one HTML file with inline data. Fonts and Chart.js load from CDNs.
Read it online, or open `index.html` locally. No package installation is needed to read it.
GitHub's file viewer shows the HTML source; the link above opens the report.

**Choose your path:**

- Just want to see the report? [Read it live](https://ookla-ariel-ride.github.io/SDGE-Analysis/).
- Want to run this on your own home? Jump to [Reproduce this for your own home](#reproduce-this-for-your-own-home--start-here).
- Auditing the methods? Start with [TECHNICAL.md](TECHNICAL.md).

> **How this report was produced:** generated with **Claude Cowork (Fable 5)**, independently
> reviewed with **Claude Code (Fable 5)** and adversarially reviewed with **Codex (GPT-5.6 Sol)**,
> then re-worked in Claude Cowork to incorporate the findings of both reviews.

**Companion documents:**
[**TECHNICAL.md**](TECHNICAL.md) — the full methods documentation: every script, data schema, algorithm, and validation chain, written so the analysis can be audited or rebuilt ·
[**GLOSSARY.md**](GLOSSARY.md) — every term (NEM, TOU, PCIA, CAISO, phantom load…) in plain English ·
[**DATA-SOURCES-CHEATSHEET.md**](DATA-SOURCES-CHEATSHEET.md) — the intake interview: every data source you need, field by field, to run this on your own home ·
[**reusable-prompt.md**](reusable-prompt.md) — the AI prompt that rebuilds the entire analysis.

This report compares utility costs, solar, EV charging, hypothetical batteries, and electrification for one SDG&E Coastal home with two EVs.
**No battery is installed.** Battery savings, backup duration, and purchase cases are modeled.

The annual model covers **July 24, 2025–July 23, 2026**, at frozen June 2026 tariffs.
The fixed electric bill audit covers June 27, 2025–June 26, 2026.
The broader bill corpus contains 28 electric statements, dated June 27, 2024–October 1, 2026,
covering service from May 25, 2024 through September 25, 2026.
Production records span 2020–2026; 2026 is partial. These windows serve different calculations.
The repository includes the scripts, aggregate results, intake guide, report template, and privacy checks.

**In this README:**
[What the report covers](#what-the-report-covers) ·
[Reproduce this for your own home](#reproduce-this-for-your-own-home--start-here) ·
[Publish with GitHub Pages](#publish-with-github-pages) ·
[Repository layout](#repository-layout) ·
[The private inputs](#the-private-inputs--and-how-to-obtain-your-own) ·
[Refreshing this analysis](#refreshing-this-analysis-same-house-new-data)

## What the report covers

The report covers five decision areas:

- Rate plans, CCA versus bundled generation, and reconciliation with actual bills.
- EV charging schedules and hypothetical battery savings, dispatch, sizing, and backup duration.
- Solar production, limits on degradation and clipping conclusions, cleaning, and added-panel scenarios.
- Gas appliances, heat-pump scenarios, and electrical service constraints.
- Grid-carbon estimates, NEM economics, and uncertain future tariff changes.

Evidence labels separate measured records, modeled scenarios, and estimates. Some archived weather
and cleaning estimates lack a reproducible calculation. Their limits appear beside them and in
[TECHNICAL.md](TECHNICAL.md). Component health and purchase suitability need evidence beyond aggregate output.

The method needs gross production, system specifications, and appropriately defined load data.
Monitoring vendors use different formats. Check quantity definitions and time coverage before substitution;
[DATA-SOURCES-CHEATSHEET.md](DATA-SOURCES-CHEATSHEET.md) describes the required inputs.

## Reproduce this for your own home — start here

The validators, billing engine, audit method, and report shell are reusable.
Each household needs results from its own inputs. The committed `data/*` files and `index.html` are
**this house's results**, `analysis/rates.py` carries this house's bill-derived tariff, and a
handful of per-house parameters live as labeled constants in the scripts (each is marked at
the point of use; the manual route below names the ones you must change). Yours get
regenerated from your own data, so replace them rather than editing them.

**0 · Blank slate.** Start a separate repository for your household:

```bash
git clone https://github.com/ookla-ariel-ride/SDGE-Analysis.git my-energy-analysis
cd my-energy-analysis
rm -rf .git && git init -b main         # fresh repo: keep the tooling, not this house's history
mkdir -p private/1-raw-data             # your raw exports land here (gitignored, never pushed)
brew install gitleaks                   # or see gitleaks docs for other platforms
git config core.hooksPath .githooks     # run the local secret/PII scan on each commit
```

**1 · Run the intake interview.** Work through [`DATA-SOURCES-CHEATSHEET.md`](DATA-SOURCES-CHEATSHEET.md),
a per-field interview spec (`id` / `question` / `type` / `required_if` / `where` /
`privacy` tier). You can read it yourself, or hand `reusable-prompt.md` to an assistant and
have it walk you through the questions section by section, operating the portals while you
handle the logins. Raw files (interval export, 12 months of detailed bills, production records,
rate tables) go in `private/1-raw-data/`; per-house facts go in `private/household.yaml`
(copy `household.example.yaml` and replace every placeholder); log progress per field id in
`private/intake-status.md`. The `has_ev` and `has_gas` flags in that file decide which
analyses apply, and a wrong flag stops the pipeline: the scripts fail closed both ways. A
flag set true with its inputs missing is an incomplete intake, and a flag set false with
those inputs present is a contradiction. Absence of data is never read as "you don't have
one". Secrets (API keys, monitoring tokens) go only into a gitignored
`.env`. Analysis may not start while any required field is missing: the scripts fail closed
without `household.yaml`. (The "private inputs" section below shows what the withheld files
look like.)

**2 · Add your personal PII patterns.** Create `private/pii-rules.toml` with your own name,
address, and account/meter numbers (see `CLAUDE.md` §4 for the format and the rule chain).
It stays local; the pre-commit hook picks it up automatically and checks staged text against those patterns. It does not check every filename or image.

**3 · Run the analysis.** Three routes:
- **AI route (how this repo was built):** paste [`reusable-prompt.md`](reusable-prompt.md)
  into a Claude Cowork or Claude Code session and hand it your gathered files. `CLAUDE.md` is
  the operating manual the agent follows; `report-template.html` is the report shell it fills.
- **Manual route:** `python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt`;
  make sure `private/household.yaml` exists (step 1; the scripts fail closed without it);
  update `analysis/rates.py` from your own bills. Read your tariff from your bill and interconnection documents.
  The committed engine models NEM 2.0 monthly retail netting. The separate NBT counterfactual is not a general replacement billing engine.
  Check `WINDOW_END` in `analysis/behavior_rebuild.py`, the other window anchors in TECHNICAL.md §3,
  and your household/rate inputs before a run.

  This bounded example runs **one generator**, from the repository root:

  ```bash
  mkdir -p private/verify
  # Copy your checked utility interval export to private/verify/usage.csv first.
  cd private/verify
  ../../.venv/bin/python ../../analysis/behavior_rebuild.py
  # Owned output: private/verify/behavior_rebuild.json
  ```

  Its inputs are `usage.csv` in that working directory, `private/household.yaml`, and the source rate/window settings.
  For a new household, check the output, then promote it into your own result set:

  ```bash
  cp behavior_rebuild.json ../../data/behavior_rebuild.json
  cd ../..
  ```

  For unchanged inputs, compare instead of promoting:

  ```bash
  cmp behavior_rebuild.json ../../data/behavior_rebuild.json
  cd ../..
  ```

  The full report needs the other owners and their dependencies in
  [`analysis/test_scripts_runnable.py`](analysis/test_scripts_runnable.py) and [TECHNICAL.md](TECHNICAL.md).
  That owner suite starts with staged dependencies; it is not an empty-tree generation pipeline.
  `analyze_norelief.py` still owns legacy table-rate plan outputs. `billing_model_nem.py` is a compatibility library
  with a diagnostic CLI; it writes no artifact. Report tokens read JSON, CSV, rates, and public-ok household fields.
- **Your-own-LLM-key route (no agentic coding tool required):** once `data/*.json` is
  regenerated, `analysis/generate_report.py` fills `report-template.html` and writes
  `index.generated.html` using a paid API key you already have — Anthropic, OpenAI, or
  Google Gemini — instead of an agent harness. If `.env` does not exist, copy `.env.example` to `.env`. Add the one
  key you plan to use (never committed; never passed as a CLI argument). Run
  `./.venv/bin/python analysis/generate_report.py --dry-run` first to see every request body
  it would send, written under `private/llm_dry_run/`, with zero sockets opened and zero cost.
  A real run needs `--provider` and `--model` (`--list-models` calls the vendor's own
  model-list endpoint so you never type a stale snapshot id). The model is handed one
  `TODO` block at a time and returns prose for that block only; it never sees the
  surrounding HTML. Every returned fragment is rejected if it contains a bare digit
  outside a `{{TOKEN}}` or `§N` reference. This blocks bare numeric figures; it does not establish prose truth. A committed
  classification map (`analysis/report_blocks.py`) marks every block `prose` (LLM-written),
  `data` (filled mechanically from an artifact, e.g. one table row per plan), or `human`
  (hardware price quotes, incentive-program status, and the provenance note's review claim:
  facts this pipeline has no way to measure); pass researched answers for
  the `human` blocks with `--human-answers your-answers.json`. The run does not promote the generated report while any block is unresolved. It can still write manifests
  and cache successful blocks under
  `private/report_cache/` so a re-run with nothing changed makes zero new API calls.
  `--humanize` adds an optional second de-AI-writing rewrite pass per block; a rewrite that
  doesn't clear the same checks silently falls back to the original, and the run
  continues. See `TECHNICAL.md` §8 for the full provider/egress design.

**4 · Validate before you trust it.** The gates in `CLAUDE.md` §9, in order: your billing
model must reproduce your actual bills before you quote any absolute dollar; every committed
artifact must regenerate from its committed script; report deltas, not levels. Run the test
suites too (`for t in analysis/test_*.py; do ./.venv/bin/python "$t"; done`); they carry
the fail-closed guards that stop a partial corpus or a stale artifact from being published
as a complete result. CI runs the public-input suites in [the test workflow](.github/workflows/tests.yml).
Real archive reproduction needs staged private data and can skip without it.
The NBC owner specifically needs one canonical root interval export, one export in
`private/1-raw-data/sdge-downloads-20261022/`, and its bill PDFs. Its fixed archive and window
do not automatically adapt to another household’s dates; check the owner contract before use.
The October 22, 2026 staging check reproduced the NBC reconciliation artifact twice in a fresh registered checkout.
That bounded check does not establish a full current-branch clean-room or publication pass.

**5 · State your own provenance.** The report's methodology section ends by naming who
produced and reviewed it. Those names are this household's answers, not part of the template:
they come from `provenance` in your `private/household.yaml`. Which of the three routes you
took decides what reads them. The **AI route** and the **manual route** both fill
`report-template.html`, whose provenance tokens resolve from these fields, so there they are
published; an unedited `REPLACE ME` or a null review field stops the render before it can
ship a false claim. The **your-own-LLM-key route** is the exception:
`analysis/generate_report.py` ignores all three and writes its own provenance (the provider
and model of that run, plus an explicit statement that no review of it happened), so filling
them changes nothing it publishes. Set `generation_tool` to the tool you ran. Leave both
review fields `null` unless all three of these happened: an independent review, an
adversarial review, and a re-work incorporating their findings. The published sentence
asserts all three. With the fields null, `analysis/generate_report.py` writes "no independent
or adversarial review of this specific run has been performed" instead, and never emits
review-claim text. If you fill `report-template.html` by hand, replace that sentence
yourself. Publishing this repo's three tool names for a run that did not use them is a false
claim about how your document was checked.

**6 · Publish (optional).** Follow the GitHub Pages section below, after reading the
privacy note.

## Publish with GitHub Pages

```bash
# Run from the root of the repository you initialized in step 0.
git add . && git commit -m "My energy analysis"
# Create your repository on GitHub after the privacy checks below, then:
git remote add origin https://github.com/<you>/my-energy-analysis.git
git push -u origin main
```

Use GitHub's [publishing-source instructions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site).
For branch publishing, select Settings → Pages → Deploy from a branch, then `main` and the root folder.
A private repository does not by itself make its Pages site private. Check site access before publication.

> ### Privacy note
> Keep raw intervals, bill PDFs, monitoring exports, and private-only intake fields in gitignored `private/`.
> Keep secrets in gitignored `.env`. Never force-add raw private files.
> Enable the pre-commit hook with `git config core.hooksPath .githooks`.
> The hook runs gitleaks and the intake-tier check. Add personal patterns to local-only `private/pii-rules.toml`.
> [CI](.github/workflows/gitleaks.yml) scans history with generic rules; it cannot check your local personal patterns.
> Check tracked private paths with `git ls-files private/ | rg -v '^private/README\.md$'`; expect no output.
> This path check establishes placement only. It does not establish the absence of PII.
> Complete the privacy and publication gates in CLAUDE.md §4 and §9 before publishing.

## Repository layout

| Path | Pushed to GitHub? | Contents |
|---|---|---|
| `index.html`, `report-template.html`, `README.md`, `TECHNICAL.md`, `GLOSSARY.md`, `CLAUDE.md`, `reusable-prompt.md`, `DATA-SOURCES-CHEATSHEET.md`, `household.example.yaml`, `requirements.txt`, `stage-private-data.sh`, `LICENSE` | ✅ yes | Report, template, docs, config schema, reproduction tooling, and license (PII-free) |
| `data/`, `analysis/`, `research/` | ✅ yes | Data, scripts, and rate research (PII-free) |
| `.githooks/`, `.gitleaks.toml`, `.github/` | ✅ yes | The mechanical privacy enforcement (pre-commit gitleaks hook, generic scan rules, CI full-history re-scan) and the issue templates |
| `private/1-raw-data/` | ❌ gitignored | Raw SDGE Green Button CSV (contains name/address/account/meter); Enphase SAM 8760 hourly load series (physical sensing point unresolved) (no identifiers, but reveals household occupancy patterns); CAISO raw day-cache |
| `private/household.yaml`, `private/intake-status.md` | ❌ gitignored | Per-house config written by the intake interview (invoice, dates, vehicle specs…) + the per-field gathered/skipped log that gates Phase B |
| `.env` | ❌ gitignored | Secrets only (PVOutput API key, monitoring tokens) — never in `household.yaml`, never committed |
| `private/3-analysis-extras/` | ❌ gitignored | As-run script copy with personal header |
| `private/README.md` | ✅ yes (placeholder) | Map of the private archive — the one file under `private/` that is committed, so the repo documents what's withheld |

Selected files follow; `TECHNICAL.md` documents every artifact's
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
| `requirements.txt` | Authoritative Python dependency list for the analysis scripts |
| `household.example.yaml` | Commented schema template for the per-house config — copy to gitignored `private/household.yaml` and replace every placeholder (the intake interview in `DATA-SOURCES-CHEATSHEET.md` walks each field) |
| `stage-private-data.sh` | Stages the gitignored private inputs (`household.yaml`, interval/SAM/gas exports) from an existing working copy into a fresh clone so the `private/verify` regeneration flow can run there — the script behind the clean-room verification |

</details>

<details>
<summary><strong>Data artifacts</strong> (this house's results; regenerate, don't edit)</summary>

| File | What it is |
|---|---|
| `data/plan_results.csv` | Modeled annual cost per plan (CEA and SDG&E-bundled scenarios) |
| `data/report_data.json` | Core chart arrays and usage statistics |
| `data/hourly_profile.csv`, `data/monthly.csv` | Aggregated usage profiles |
| `data/battery_sim.json` | Battery arbitrage simulation results (6 configurations) |
| `data/backup_endurance.json` | Outage-endurance simulation (config × backup tier) |
| `data/threeway_production_validation.csv` | Daily production series from Enphase, PVOutput, and `meter_derived` (SAM hourly load minus utility signed net). Their agreement does not establish independent sensing. |
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
| `data/cca_generation_rates.csv` | CEA's own charged per-(statement, billing period, season, TOU-period) generation rate, line-parsed directly from all 21 CCA-era bill PDFs; the per-statement series that tests whether the charged generation rate changed across the corpus |
| `data/cca_bundled_counterfactual.json` | Two-directional repricing of every CCA-era and bundled-era billing period against the other provider's own same-date printed rate, segment by segment, to test whether switching to the CCA was a net win |
| `data/package_results.json` | LOW/MID/HIGH package figures from the integrated pipeline — savings, asset-alone paybacks (regenerated by `analysis/package_results.py`) |
| `data/deep_results.json` | Deep-dive outputs: TOU-DR-P wildcard, phantom baseload, EV sessions, vacation detection, Monte Carlo battery ROI |
| `data/gas_monthly_therms.csv`, `data/gas_bill_summary.csv` | Monthly gas usage and aggregated gas bill summary (electrification analysis inputs) |
| `data/weather_daily_tmean.csv`, `data/weather_results.json` | Open-Meteo daily temperatures + weather-normalized cooling results |
| `data/pvoutput_yearly_2020-2025.csv` | PVOutput per-year production stats, 2020–2025 (degradation analysis input) |
| `data/cleaning_study_peaks_2024.csv` | Peak-day production windows around the 2024 cleaning (diff-in-diff companion) |
| `data/wall_charger_daily.csv` | Tesla Wall Connector daily delivered kWh (wall-side) — the independent cross-check of the EV session detector |
| `data/battery_dispatch_policies.json` | Dispatch-policy results: savings, kWh served, cycles/day, hourly profiles, escalation ladder, §6 serviceable-load inputs |
| `data/battery_plan_matrix.json` | The §4 battery × plan matrix: no-battery / with-battery / battery-value per top-3 plan, each also carried as integer cents (`*_cents`, the fields the ranking reads; the whole-dollar cells are for display), plus the mid package (EV shift, then battery) re-billed end-to-end under each plan (table rates, cross-plan ranking; canonical-engine cross-checks included) |
| `data/battery_sizing_curve.json` | Battery capacity and discharge-power swept independently through the same price-aware dispatch engine used elsewhere, with both shipping Powerwall 3 configurations landing as exact grid points rather than interpolated |
| `data/perfect_foresight_dispatch.json` | The true annual-bill-minimizing battery dispatch, solved as a linear program on the full measured year at identical hardware constraints — bounds how much value the published dispatch policy leaves on the table |
| `data/bill_periods_electric.csv` | One row per electric billing period across the whole downloaded corpus: days, signed-net kWh, printed NBC usage (`gross_kwh`, a compatibility key), delivery/generation charges, generation provider (regenerated by `analysis/parse_bills.py`) |
| `data/bill_periods_gas.csv` | One row per gas billing period: therms, total gas service, baseline/non-baseline $/therm (day-weighted blend across rate segments), baseline allowance (therms), and the flat Gas Energy Charge $/therm (also day-weighted blended) |
| `data/bill_tou_detail.csv` | Per period × section × season × rate segment: the kWh and $/kWh exactly as printed on each bill — the per-bill rate evidence |
| `data/bill_gas_detail.csv` | Per period × charge type (Gas Service tiered baseline/non-baseline, the flat Gas Energy Charge, or Public Purpose Programs + State Regulatory Fee combined) × rate segment: each segment's own day or therm count and $/therm rate(s), exactly as printed on each bill — what `bill_periods_gas.csv`'s blended columns collapse, and what a true marginal-tier gas rebilling needs |
| `data/bill_corpus_boundary.json` | Where the electric bill corpus stops and why: the billing-history export that defines it, any statement parsed from a PDF but not published (with the reason and what would end the exclusion), and the resulting day coverage of the analysis window. A run with no billing-history export staged records a `boundary_not_derived` block instead — where the export was looked for, what was published unchecked as a result, and what would derive the boundary — so an empty exclusion list can never be read as a checked corpus. Written by `analysis/parse_bills.py` in the same atomic set as the bill artifacts it describes |
| `data/rate_vintages.csv` | Every rate cell's constant-rate spans across the bill corpus, each labelled with its evidence tier (directly observed / carried across a gap / absent) and its authority (a charged tariff, or a printed comparison that is not one) |
| `data/rate_rebilling_residuals.csv` | Per-statement reconstruction and corroboration table behind the historical rate engine: how each statement re-prices through the engine's timeline, and which of its printed lines an independent statement can corroborate |
| `data/tou_spread.json` | Rate escalation per season × TOU cell and for the on-peak-to-super-off-peak spread itself: each with its vintage count, its fit uncertainty both before and after widening for a data-chosen breakpoint, and the verdict that follows. When a season's evidence cannot support a trend, the file records a not-determined verdict, why, and what would settle it |
| `data/bill_decomposition.json` | Why the bill changed between two matched periods: billing-mode evidence, per season × TOU cell price/quantity bounds, the settlement component, and the provider-vs-vintage split |
| `data/tou_audit.csv`, `data/tou_audit_summary.json` | The utility's billed TOU buckets reconciled against the raw 15-minute export, per statement, plus a corpus-wide summary |
| `data/lifetime_payback.json` | Cumulative value of metered production against the install invoice, with the crossover dates and the blended rates it was derived from |
| `data/service_headroom.json` | Electrical service headroom under NEC 220.87: the measured demand basis, the calculated existing load it implies, what is left against the main breaker and the busbar, and the 120%-rule check on the existing PV backfeed |
| `data/irreducible_bill.json` | The strict floor of the annual electric bill that no purchase can remove (the per-day fixed charge alone), reported separately from non-bypassable charges, which are currently owed and usage-dependent: per-period extraction (cross-checked against an independently sourced TOU-table computation), the trailing-12-month figures, each component's share of each `package_results.json` package's projected bill, and the minimum-bill-provision checks and NBC model convention behind it (positive physical interval net; reconciliation limits appear in TECHNICAL.md §14) |
| `data/tou_structure_stress.json` | Today's $/kWh rates held fixed while the TOU window structure (on-peak start/end, midday super-off-peak window, summer month set) is perturbed across scenarios individually labeled measured / historically-motivated / hypothetical |
| `data/uncertainty_results.json` | A 7-input Monte Carlo on the battery's own payback/NPV — rate escalation, degradation, install cost, EV-behavior persistence, production-measurement disagreement, soiling ambiguity, round-trip efficiency — reproducing the older 3-input Monte Carlo bit-for-bit as a verified special case |
| `data/gross_import_decomposition.json` | Modeled attribution of printed NBC-usage growth between two early-summer bill periods, under two diurnal-shape assumptions and endpoint calibration |
| `data/reprice_by_vintage.json` | An eight-term, exact-to-the-cent decomposition of the gap between the current-rate-everywhere model and the real billed year — window alignment, TOU-window-shape confounds, CCA/state-surcharge adders, and rate-vintage effects |
| `data/quiet_night_floor.json` | The overnight "phantom load" floor re-measured directly from interval data (not the older method's proxy), priced two independent ways, reconciled against `extra_results.json`'s own earlier phantom-load figure |
| `data/heat_pump_conversion.json` | Replacing the gas furnace + AC with a heat pump: furnace therms isolated two independent ways and cross-checked, the added electric load placed into real 15-minute intervals using the same capacity-capped daily shape the gas savings themselves are priced against, then re-billed across the measured year, payback and NPV (standalone and marginal-over-AC-replacement) across a COP × install-cost × gas-price sensitivity grid |
| `data/marginal_capacity_value.json` | What one more kW of panels would earn: the derived hourly production scaled by one added kW, placed into each real 15-minute interval (import offset first, export after), the year re-billed through the canonical NEM engine, with the settlement decomposition, the import-offset/export split, both clipping cases (the intake's inverter architecture decides which is published), an assumed-$/W payback ladder and a re-check of the §8 verdict |
| `data/all_electric_endgame.json` | The full transition off gas: fixed charges isolated per statement, remaining gas end uses enumerated from the daily series, each conversion (water heater, furnace) costed and sequenced with the fixed-charge release credited only to the final step, added electric load re-billed jointly (not summed independently) with a quantified tier/solar double-count correction, service headroom checked against the panel, meter-removal research, and reconciliation against the furnace conversion and the HDD decomposition |

</details>

<details>
<summary><strong>Analysis scripts &amp; research</strong></summary>

| File | What it is |
|---|---|
| `analysis/analyze.py` | **Legacy** cross-plan ranking model (table rates, kept labeled — `CLAUDE.md` §9); current models import `analysis/rates.py` instead |
| `analysis/analyze_norelief.py` | Legacy variant: prices CEA generation without the Rate Relief Credit |
| `analysis/rates.py` | **Canonical rate constants + billing engine** for the current tariff (bill-derived; imported by all current models). Also owns the tariff calendar — TOU windows, season, and the holiday rule |
| `analysis/rates_history.py` | The tariff in force on any past date the bill corpus covers, read out of the committed bill artifacts. Every value carries its evidence tier and its authority, so a printed comparison rate can never be returned as a charged tariff → `data/rate_vintages.csv` |
| `analysis/tou_audit.py` | Reconciles the utility's billed TOU buckets against the raw interval export, scoring alternative day-type and window rules against the statements |
| `analysis/tou_spread.py` | Tests whether the on-peak-to-super-off-peak gap is widening, per season, from the per-bill rate evidence. Counts distinct rate changes rather than statement reprints, refits after the largest single step, and widens the interval for having chosen that step from the data. Neither one tariff redesign nor a densely reprinted tariff can be published as an ongoing trend → `data/tou_spread.json` |
| `analysis/bill_decomposition.py` | Decomposes a year-over-year bill change into price, quantity, TOU mix and generation provider, per season × TOU cell, after establishing from statement text whether the energy was billed monthly or accrued to the annual true-up |
| `analysis/report_data.py` | Builds the report's chart arrays from the committed artifacts, so every series on the page is regenerable |
| `analysis/publish.py` | Crash-consistent multi-artifact publication (lock, backup, restore) used where several artifacts must land as one set |
| `analysis/billing_model_nem.py` | Supporting historical billing-comparison library; canonical billing delegates to `rates.py`, with NBC reconciliation limits documented in TECHNICAL.md |
| `analysis/behavior_rebuild.py` | Session-based EV/behavior shift model — places each shifted kWh into its destination interval and re-bills the modified year |
| `analysis/battery_backup_sims.py` | Battery arbitrage + backup endurance simulations |
| `analysis/threeway_production_validation.py` | Regenerates the three-column production-validation series from committed daily records: PVOutput and Enphase-meter totals passed through unchanged, plus a third `meter_derived` series computed from SAM hourly load minus utility net imports; SAM physical provenance and acquisition independence remain unresolved, with explicit DST-day reconciliation between the flat-clock SAM export and the wall-clock Green Button meter → `data/threeway_production_validation.csv` |
| `analysis/soiling_analysis.py` | Soiling from rain-recovery events + days-since-rain regression (NOAA/RCC ACIS precipitation) |
| `analysis/carbon_timing.py` | Retired four-day grid-carbon study; the report uses `carbon_fullyear.py` |
| `analysis/carbon_fullyear.py` | Full-year carbon measurement: 364 of 365 CAISO days measured, one day filled by month-hour-mean → `data/carbon_fullyear_results.json` |
| `analysis/carbon_dispatch_tradeoff.py` | Runs a hypothetical battery three ways on the same measured year — cost-minimizing, carbon-minimizing, and a policy serving either condition — and compares all three on dollars and CO2 against the same no-battery baseline → `data/carbon_dispatch_tradeoff.json` |
| `analysis/extended_findings.py` | Extended-findings computations (AB 205, electrification dividend, gas HDD decomposition, 2039 strategy, tornado) → `data/extended_results.json` |
| `analysis/nem3_grandfathering.py` | NEM 2.0 grandfathering value re-billed against SDG&E's real hourly Net Billing Tariff export schedule (both rate vintages), plus the price-aware battery's own marginal value under that same real schedule, reconciled against `extended_findings.py`'s flat-credit `nbt_2039` figures → `data/nem3_grandfathering.json` |
| `analysis/dsgs_vpp_backtest.py` | Replays a hypothetical Powerwall 3's DSGS Option 3 VPP revenue against the real 2025 CEC event calendar (`data/dsgs_event_calendar_2025.csv`, 68 scheduled test-event date-hour slots, none a real emergency dispatch) and this household's own measured load/solar, inferring which anonymized UDC is SDG&E from enrollment scale and corroborating that against a named-utility report — every figure explicitly hypothetical, since the household owns no battery today → `data/dsgs_vpp_backtest.json` |
| `analysis/cca_rate_extraction.py` | Parses every CCA-era bill PDF's own "CCA Electric Generation Charges" section, matched to its billing period and cross-footed against the bill's own printed total, to extract CEA's charged per-season × TOU-period generation rate directly from each of the 21 statements → `data/cca_generation_rates.csv` |
| `analysis/cca_bundled_counterfactual.py` | Was switching to the CCA a win? Reprices the CCA-billed periods at SDG&E's own same-date bundled-generation comparison table, and the bundled-era baseline at the CCA's own charged rate, at segment level (excluding mixed-sign cells) rather than one representative date per period → `data/cca_bundled_counterfactual.json` |
| `analysis/deep_analyses.py` | Deep-dive script: TOU-DR-P wildcard, phantom load, EV sessions, vacation detection, Monte Carlo |
| `analysis/battery_dispatch_policies.py` | Battery dispatch-policy comparison — evening-only vs two-window vs price-aware discharge vs price-aware on both sides (the report's battery basis, `PUBLISHED_POLICY`) |
| `analysis/battery_plan_matrix.py` | Battery × plan matrix (§4): the price-aware PW3 dispatch billed under each top-3 plan's rate-table values → `data/battery_plan_matrix.json` |
| `analysis/battery_sizing_curve.py` | Sweeps battery capacity (5–40 kWh at fixed discharge power) and discharge power (5–15 kW at fixed capacity) through the same price-aware dispatch engine used elsewhere, so both shipping Powerwall 3 configurations land as exact grid points on a continuous sizing curve → `data/battery_sizing_curve.json` |
| `analysis/perfect_foresight_dispatch.py` | How much is a smarter controller worth? Solves the annual-bill-minimizing battery dispatch as a linear program (35,040 15-minute intervals/year, same hardware constraints and NEM-netting engine as the shipping heuristic) to bound how much value the published dispatch policy leaves on the table → `data/perfect_foresight_dispatch.json` |
| `analysis/package_results.py` | Composes `data/package_results.json` from the behavior + dispatch artifacts (no new computation) |
| `analysis/lifetime_payback.py` | Lifetime solar payback: cumulative production value vs install invoice, with crossover dates |
| `analysis/service_headroom.py` | Electrical service headroom from measured demand: takes the peak interval demand out of the Green Button export, applies the NEC 220.87 existing-dwelling method (measured maximum demand × 125%), and checks the result and the existing PV backfeed against the panel facts in `private/household.yaml` → `data/service_headroom.json` |
| `analysis/irreducible_bill.py` | Splits every electric billing period into a fixed daily charge, non-bypassable charges extracted from the bills, taxes/fees and a residual energy bucket; cross-checks the residual against an independently sourced TOU-table computation, states the fixed daily charge alone as the strict floor over the trailing 12-month bill window (non-bypassable charges, which are usage-dependent, are reported separately), and expresses each component as a share of each package's projected bill → `data/irreducible_bill.json` |
| `analysis/tou_structure_stress.py` | Holds frozen June 2026 $/kWh rates fixed and instead perturbs the TOU window structure (on-peak start/end, midday super-off-peak window, summer month set) across scenarios individually labeled measured / historically-motivated / hypothetical by their own evidentiary basis, to test a redrawn-boundary risk the escalation-only sensitivity can't see → `data/tou_structure_stress.json` |
| `analysis/uncertainty_propagation.py` | A 7-input Monte Carlo (rate escalation, battery degradation, install cost, EV-behavior persistence, production-measurement disagreement, soiling-rate ambiguity, round-trip efficiency), each bound to a specific committed artifact, reproducing the older 3-input Monte Carlo (`deep_analyses.py`) bit-for-bit as a verified special case, to report the battery's payback/NPV as a distribution rather than a point estimate → `data/uncertainty_results.json` |
| `analysis/gross_import_decomposition.py` | Attributes the change in printed NBC usage under two modeled load/production shapes. Earlier collected history needs a separate quality check before use → `data/gross_import_decomposition.json` |
| `analysis/reprice_by_vintage.py` | Chains eight terms — window alignment, TOU-window-shape/PCIA-restart confound, separated CCA product-adder and state-surcharge terms, and rate-vintage effects — to decompose the gap between the current-rate-everywhere estimate and the real billed year exactly to the cent; it reads `cca_rate_extraction.py`'s per-statement generation rates and fails closed if the CCA's charged generation rate moved within the fixed 13-period audit, since the decomposition assumes it did not → `data/reprice_by_vintage.json` |
| `analysis/quiet_night_floor.py` | Re-measures the overnight "phantom load" floor directly from interval data (the 15-minute Green Button import series plus the SAM hourly load series), prices its removal two independent ways, and reconciles the small gap against `extra_results.json`'s own earlier phantom-load figure → `data/quiet_night_floor.json` |
| `analysis/heat_pump_conversion.py` | Heat-pump conversion scenario model: isolates furnace therms from the gas meter two independent ways, sizes the heat pump's replacement electricity by COP scenario, places it into real 15-minute intervals using the same capacity-capped daily heating shape gas savings are priced against, re-bills the measured year with the canonical NEM engine, and prices displaced gas at each real billing period's own realized rate → `data/heat_pump_conversion.json` |
| `analysis/marginal_capacity_value.py` | Prices one more kW of panels by an interval-level counterfactual rather than a rate times a quantity: derives hourly production from the SAM hourly load series and the revenue meter (the same identity `threeway_production_validation.py` uses, tied out against its committed column), scales it by the added kW, places the increment into each 15-minute interval and re-bills the year through `rates.bill_nem_monthly()`; states every assumption (DC/AC ratio, clipping ceiling, degradation, DST days) and labels retrofit $/W as an assumption → `data/marginal_capacity_value.json` |
| `analysis/all_electric_endgame.py` | Costs cancelling the gas meter entirely: isolates the fixed charge, enumerates remaining gas end uses, sequences and costs each conversion with the fixed-charge release credited only to the final step, jointly re-bills the combined added electric load (not two independent rebills summed, which double-claims solar), checks service headroom, researches meter removal, and reconciles against the furnace conversion and §10's existing figures → `data/all_electric_endgame.json` |
| `analysis/extra_results.py` | Regenerates `data/extra_results.json`'s `escalation` block — a retired, evening-only-dispatch scenario, distinct by design from the current published escalation ladder in `data/battery_dispatch_policies.json` (TECHNICAL.md §3.11) — from a documented historical constant instead of an orphaned hand-typed figure; the file's other six keys (phantom baseload, price map, NBT bracket, cleaning, true-up, EV fleet) are frozen records and estimates whose original calculations are not reproduced by this owner |
| `research/rates-reference.md` | Every rate figure used: SDG&E UDC + EECC per plan, CEA generation, PCIA, fixed charges, baselines, TOU windows — with sources |
| `research/battery-research-notes.md` | 2026 battery prices/specs, incentive status, simulation summary |
| `research/extended-research-notes.md` | AB 205 / DSGS-VPP / outage-exposure / fuel-constant research (sources + captured figures) backing the extended findings |
| `research/sdge-plan-comparison-capture.md` | SDG&E's own plan-tool output vs this model |
| `analysis/parse_bills.py` | Parses the detailed bill PDFs into the per-period and TOU artifacts, and regenerates the two legacy bill summaries as its own reproduction gate. Reads `household.has_gas` — the flag, not the presence of a directory, decides whether gas is expected |
| `analysis/generate_report.py` | Orchestrates the "your-own-LLM-key" report route (step 3 above, `TECHNICAL.md` §8): resolves `report-template.html`'s TODO blocks per `report_blocks.py`'s classification, runs LLM output through a numeral guard and `prose_lint.py` with one corrective retry, caches successful fragments, and writes only `index.generated.html` — never `index.html` — and only when every block resolves and the assembled page clears the whole-page prose-rhythm gate (`prose_rhythm.py`; a refusal is recorded in the run manifest as a `prose-rhythm` failure and nothing is promoted) |
| `analysis/report_blocks.py` | Parses every actionable TODO block out of `report-template.html` and classifies each as `data` (mechanical row-builder over a committed artifact), `human` (needs a fact this repo has never measured), or `prose` (answerable from `report_tokens.py`'s own token map) |
| `analysis/report_tokens.py` | Parses every `{{TOKEN}}` in `report-template.html` and resolves each against a committed, explicit source map (`data/*.json`, `data/*.csv`, `analysis/rates.py`, or public-ok `private/household.yaml` fields only) — raising `SystemExit` naming any token whose source goes missing rather than inventing a value |
| `analysis/llm_providers.py` | Vendor-native REST adapters (Anthropic, OpenAI, Gemini) for the report-generation LLM calls, funneling every outbound request through one chokepoint for egress auditing, with credentials loaded from a hand-parsed `.env` and never accepted as a CLI argument |
| `analysis/stamp_report_version.py` | Writes the header ledger's Version row (`YYYY-MM-DD · build <sha256[:10]>`): the date the content last changed plus a fingerprint of the page as dated, with only the build digits normalized. `--check` recomputes it for the row's date without writing and exits 1 when `index.html` was edited and not restamped; `generate_report.py` stamps `index.generated.html` the same way |
| `analysis/prose_lint.py` | Mechanical linter gating `generate_report.py`'s LLM-generated prose: flags `CLAUDE.md`'s banned process-narrative phrases, negative parallelisms, filler transitions, promotional adjectives, the intensifiers in its `INTENSIFIERS` list that claim candor instead of showing it, and rule-of-three padding (a narrow check for triads of generic quality adjectives, not a general list-of-three ban); any violation hard-fails that block rather than publishing it |
| `analysis/prose_blocks.py` | Measures `index.html`'s running-prose blocks against `CLAUDE.md` §10's ~800-character paragraph cap: extracts every `p`/`li`/`div`/heading block with its source line and visible-text length (`<code>` spans, pills, tables, nav, the day-band and the meta ledger excluded), splits basic from advanced tier at `<details id="advanced">`, and with `--max-chars N` lists each block over the cap and exits 1 (`TECHNICAL.md` §5.2) |
| `analysis/prose_rhythm.py` | Gates the rhythm of `index.html`'s running prose, per tier, on the blocks `prose_blocks.py` extracts minus the headings (CLAUDE.md §10 makes a heading verdict design language): em dashes ≤ 3.0 per 1,000 words, `"X, not Y"` tails ≤ 1.5 per 1,000, zero non-acronym ALL-CAPS words against a documented allowlist, zero intensifiers (the word list imported from `prose_lint.py`), and no block over 800 characters. `--strict` exits 1 and names each offender with its source line (`TECHNICAL.md` §5.3) |
| `analysis/privacy_tiers.py` | Enforces `CLAUDE.md` §4's private-only/secret intake tiers mechanically: parses field tiers from `DATA-SOURCES-CHEATSHEET.md`, resolves each to its `private/household.yaml` path, and scans every tracked file for private-only values — run via `.githooks/pre-commit` as the enforcement gate |
| `analysis/dry_run.py` | Runs this repository's generator on copied inputs in a temporary tree and reports artifact differences. It checks the source checkout before and after the run. It is not an OS security boundary or a sandbox for untrusted code. |
| `analysis/test_*.py` | Public-input checks run through [the test workflow](.github/workflows/tests.yml). `test_scripts_runnable.py` checks registered owners against staged dependencies and compares their outputs. Real archive reproduction needs private inputs. |
| `analysis/check_coverage.sh` | Local coverage gate (≥90% statement coverage across the analysis package); needs the private archive, so it does not run in CI |
| `analysis/household.py` | Loader for `private/household.yaml` — analysis scripts read per-house facts (invoice, dates, charger kW, vehicle specs…) through it and **fail closed** with a run-the-intake-interview message if the file or a required key is missing |

</details>

## The private inputs — and how to obtain your own

Raw household inputs stay in gitignored `private/`. Gather the sources needed for the sections you intend to reproduce.

1. **Utility intervals:** electric Green Button exports and, for gas studies, daily gas therms.
   The current shared electric loader is `behavior_rebuild.load()`. Its input has 13 metadata rows,
   then dated 15-minute Consumption, Generation, and Net channels. Check your export's actual layout.
2. **Hourly whole-home load:** battery backup and load/production studies use two saved SAM files,
   each with one `kWh` column and 8,760 rows. The single-column loader in `battery_backup_sims.py`
   reads the first column of each file. These exports do not establish the sensing point or installed CT configuration.
   Check those definitions for your source. A utility-plus-production energy balance is another calculation;
   agreement does not establish independent instruments.
3. **Detailed bills and history:** retain electric/gas PDFs and the billing-history export needed to define the corpus.
   Reconciliation can need overlapping preserved interval exports when a rolling download drops older dates.
4. **Household, solar, and vehicle evidence:** complete `private/household.yaml` from the intake guide.
   Retain production records, equipment evidence, purchase dates, and applicable charging records.
5. **Study-specific sources:** weather, grid-carbon records, and frozen service telemetry apply to their respective studies.
   Newer live production and hourly weather exist for this project, but need aligned quality checks before a new annual model.

The committed aggregates document this household's results. They do not replace your own raw evidence.
Follow the intake checklist and each generator's input contract.
To move your own copy to a second machine or a fresh clone, run the **destination's own
copy** of the script (`cd <new-clone> && ./stage-private-data.sh <old-working-copy> .`);
it places the gitignored inputs where the `private/verify` flow expects them. Each copy
of the script stages only into a working tree of the checkout it lives in, and refuses
anything else (including a destination that does not exist yet) before writing a byte, so a
mistyped path or a failed `git worktree add` cannot leave one household's raw archive in an
unrelated repository.

## Refreshing this analysis (same house, new data)

1. My Energy Center → Usage → Green Button Download → last 13 months, CSV → `private/1-raw-data/`.
2. If SDG&E (Jan/Jun) or CEA (Feb/Jun) issued new rates, update `analysis/rates.py`, the
   single source of truth all current models import. If the household changed (vehicle,
   charger, cleaning event, appliance), update `private/household.yaml` too.
3. Re-run the pipeline scripts (`CLAUDE.md` "Commands" has the exact invocations) and confirm
   each `data/*.json` regenerates cleanly; that diff-check is the acceptance gate. Run the test
   suites as well — `test_scripts_runnable.py` performs the byte-diff across every owned
   artifact in one pass. For the strictest check, clone fresh, stage the private inputs from
   inside that clone with its own `./stage-private-data.sh <old-working-copy> .`, and run the
   same gates there. Record which owners pass, which skip, and which sources remain unavailable.
4. Regenerate the report from `report-template.html` per `reusable-prompt.md` Phase D, or
   paste `reusable-prompt.md` into a Claude Cowork session and let it redo everything.
5. Restamp the page: `./.venv/bin/python analysis/stamp_report_version.py`. The header's
   Version row carries the date and a fingerprint of the page's content, date included, so
   editing either by hand goes stale; a row that already matches is left alone, on any later
   day. `analysis/test_stamp_report_version.py` fails in CI when `index.html` was edited
   without this step. `--check` reports the state without writing.

## License

[MIT](LICENSE). The scripts, template, and documentation are free to reuse and adapt.
The committed `data/*` artifacts and `index.html` are one household's results, included
as worked examples; regenerate them from your own data rather than republishing these.

To report a method error or a number that does not reproduce,
[Open an issue](https://github.com/ookla-ariel-ride/SDGE-Analysis/issues/new/choose);
there are templates for figure errors, reproduction problems, and documentation questions,
each with a privacy checklist so nothing personal lands in a public thread.

---

*README source review: October 22, 2026.*
