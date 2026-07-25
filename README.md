# SDG&E Rate Plan Analysis

## 📊 [➡️ View the live report](https://ookla-ariel-ride.github.io/SDGE-Analysis/)

**Click the link above** to open the interactive report in your browser:
**https://ookla-ariel-ride.github.io/SDGE-Analysis/**

That page *is* `index.html`, served by GitHub Pages — charts render automatically, nothing to
install. (Alternate ways to view it: clone/download this repo and double-click `index.html`,
which is fully self-contained with data inlined and Chart.js from CDN. Note that GitHub's own
file viewer shows the HTML *source*, not the rendered report — use the link above instead.)

> **How this report was produced:** generated with **Claude Cowork (Fable 5)**, independently
> reviewed with **Claude Code (Fable 5)** and adversarially reviewed with **Codex (GPT-5.6 Sol)**,
> then re-worked in Claude Cowork to incorporate the findings of both reviews.

**Companion documents:**
[**TECHNICAL.md**](TECHNICAL.md) — the full methods documentation: every script, data schema, algorithm, and validation chain, written so the analysis can be audited or rebuilt ·
[**GLOSSARY.md**](GLOSSARY.md) — every term (NEM, TOU, PCIA, CAISO, phantom load…) in plain English ·
[**DATA-SOURCES-CHEATSHEET.md**](DATA-SOURCES-CHEATSHEET.md) — the data-gathering checklist for running this on your own home ·
[**reusable-prompt.md**](reusable-prompt.md) — the AI prompt that rebuilds the entire analysis.

An interactive, evidence-based report for a solar home with two EVs (all-electric transportation) in the SDG&E Coastal climate zone (NEM 2.0, CCA generation), built from 365 days of 15-minute Green Button interval data, a full-year detailed-bill audit, six years of production records, per-vehicle charging telemetry, and real weather + grid data.

## What the report covers

| § | Section | What's in it |
|---|---|---|
| — | Bottom line | Integrated recommendation: plan, EV-timing fix, baseload hunt, battery verdict, solar-expansion verdict, payback status, carbon tip |
| 1 | The data | Triple-verified inputs: meter flows, whole-home load, production (3 independent sources, ±2%, 0.9999 correlation) |
| 2 | Your solar system today | Hardware inventory, size verification against measured peak power, health/degradation signals |
| 3 | Rate plan comparison | All eligible SDG&E plans priced against actual 15-min usage, CCA vs bundled, validated within 1% of SDG&E's own tool |
| 4 | Battery × plan matrix | Whether a battery changes the best-plan answer (it doesn't — it strengthens it) |
| 5 | Usage profile | Where the money goes by hour/period/month, with charts; EV-charging findings |
| 6 | Battery hardware | Arbitrage simulations of 6 real configurations, a three-policy dispatch comparison (evening-only / two-window / price-aware — the published basis), and outage-endurance tiers |
| 7 | Three costed packages | Low ($0 behavior) / Mid (+1 battery) / High (+expansion): savings, projected bills, honest asset-alone paybacks |
| 8 | Array upgrades | More panels? Higher-capacity panels? Microinverter upgrade for clipping? All answered with measured data |
| 9 | Deeper analyses | 6-yr degradation, clipping check, weather-normalized cooling, EV session report card + fleet cross-check (meter-derived; cross-checked by Tesla-app and wall-charger telemetry), electrification dividend (~$3,230/yr vs gasoline counterfactual), away-day/weekend/representative-year workups, TOU-DR-P wildcard, phantom-load flag |
| 10 | Actual bills | 365-day bill audit, model-vs-actual reconciliation (rate vintage the leading explanation), gas usage + electrification (HPWH) |
| 11 | Lifetime payback | Install invoice vs cumulative production value by year — gross and net-of-ITC break-even dates (simple, undiscounted) |
| 12 | Cleaning & soiling | Measured cleaning effect (multi-year diff-in-diff), rain-recovery soiling study, optimal cleaning month & cadence economics |
| 13 | Carbon · NEM · prices | Grid-carbon timing from real CAISO data (28-day hourly sampling, with chart), $/yr value of NEM 2.0 grandfathering (flat-rate sensitivity) + the 2039 transition strategy, battery vs rate-escalation ladder, phantom-baseload decomposition, marginal price map |
| — | What to do Monday | Implementation appendix: charger schedule, pre-battery checklist, pre-registered EV-fix success metrics, re-run triggers |
| 14 | Methodology | Every model, source, and caveat — plus the validation chain |

> **Note on solar monitoring:** this analysis happened to pull production data from **Enphase
> Enlighten**, but the method is vendor-agnostic. SolarEdge, Tesla, SMA, Fronius, PVOutput, and
> other platforms all expose equivalent production feeds (gross generation + system specs). The
> `DATA-SOURCES-CHEATSHEET.md` describes the *data* you need, not one vendor's menu — substitute
> your own monitoring platform's export.

## Contents

| File | What it is |
|---|---|
| `index.html` | The interactive report (plan comparison, charts, behavior findings, SDG&E-tool comparison, battery deep-dive) |
| `report-template.html` | De-personalized report skeleton (`{{TOKEN}}` placeholders) — start here when regenerating `index.html` (see `reusable-prompt.md` Phase D and `CLAUDE.md` §10) |
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
| `data/wall_charger_daily.csv` | Tesla Wall Connector daily delivered kWh (wall-side) — cross-check of the EV session detector (99.6% aggregate agreement over the 20-day clean window) |
| `TECHNICAL.md` | **Full technical/reproducibility documentation** — every script, data schema, algorithm, and chart pipeline, methods-section style |
| `CLAUDE.md` | Operating rules for AI-assisted reruns (evidence-based mandate, validation order, privacy gates, known pitfalls) |
| `analysis/analyze.py` | The plan billing model (Python/pandas) — rerun against a fresh Green Button CSV |
| `analysis/analyze_norelief.py` | Variant: prices CEA generation without the Rate Relief Credit |
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
| `data/battery_dispatch_policies.json` | Dispatch-policy results: savings, kWh served, cycles/day, hourly profiles, escalation ladder, §6 serviceable-load inputs |
| `data/battery_plan_matrix.json` | The §4 battery × plan matrix: no-battery / with-battery / battery-value per top-3 plan (table rates, cross-plan ranking; canonical-engine cross-check included) |
| `research/rates-reference.md` | Every rate figure used: SDG&E UDC + EECC per plan, CEA generation, PCIA, fixed charges, baselines, TOU windows — with sources |
| `research/battery-research-notes.md` | 2026 battery prices/specs, incentive status, simulation summary |
| `research/extended-research-notes.md` | AB 205 / DSGS-VPP / outage-exposure / fuel-constant research (sources + captured figures) backing the extended findings |
| `research/sdge-plan-comparison-capture.md` | SDG&E's own plan-tool output vs this model |
| `reusable-prompt.md` | Full prompt to reproduce this entire analysis (plan + solar + battery + gas + bill audit) in Claude Cowork |
| `DATA-SOURCES-CHEATSHEET.md` | Fill-in-the-blanks checklist of every data source needed (links, which PDFs/exports to gather) for your own home |
| `GLOSSARY.md` | Plain-English definitions of every term of art (NEM, PCIA, CAISO, phantom load, dispatch policy…), with links to authoritative sources |
| `requirements.txt` | Python dependencies for the analysis scripts (pandas, numpy, pyyaml) |
| `household.example.yaml` | Commented schema template for the per-house config — copy to gitignored `private/household.yaml` and replace every placeholder (the intake interview in `DATA-SOURCES-CHEATSHEET.md` walks each field) |
| `analysis/household.py` | Loader for `private/household.yaml` — analysis scripts read per-house facts (invoice, dates, charger kW, vehicle specs…) through it and **fail closed** with a run-the-intake-interview message if the file or a required key is missing |

## Reproduce this for your own home — start here

Nothing here is specific to one house; the machinery is reusable. The committed `data/*` files
and `index.html` are **this house's results** — yours get regenerated from your own data, so
don't edit them; replace them.

**0 · Blank slate.** Clone the machinery, drop the history and results, protect yourself:

```bash
git clone https://github.com/ookla-ariel-ride/SDGE-Analysis.git my-energy-analysis
cd my-energy-analysis
rm -rf .git && git init                 # fresh repo: keep the tooling, not this house's history
mkdir -p private/1-raw-data             # your raw exports land here (gitignored, never pushed)
brew install gitleaks                   # or see gitleaks docs for other platforms
git config core.hooksPath .githooks     # secret/PII scan now blocks every commit
```

**1 · Run the intake interview.** Work through [`DATA-SOURCES-CHEATSHEET.md`](DATA-SOURCES-CHEATSHEET.md)
— it is now a per-field interview spec (`id` / `question` / `type` / `required_if` / `where` /
`privacy` tier). Raw files (interval export, 12 months of detailed bills, production records,
rate tables) go in `private/1-raw-data/`; per-house facts go in `private/household.yaml`
(copy `household.example.yaml` and replace every placeholder); log progress per field id in
`private/intake-status.md`. Secrets (API keys, monitoring tokens) go ONLY into a gitignored
`.env`. Analysis may not start while any required field is missing — the scripts fail closed
without `household.yaml`. (The "private inputs" section below shows what the withheld files
look like.)

**2 · Add your personal PII patterns.** Create `private/pii-rules.toml` with your own name,
address, and account/meter numbers (see `CLAUDE.md` §4 for the format and the rule chain).
It stays local — the pre-commit hook picks it up automatically and blocks any commit
containing those values.

**3 · Run the analysis — two routes:**
- **AI route (how this repo was built):** paste [`reusable-prompt.md`](reusable-prompt.md)
  into a Claude Cowork or Claude Code session and hand it your gathered files. `CLAUDE.md` is
  the operating manual the agent follows; `report-template.html` is the report shell it fills.
- **Manual route:** `python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt`;
  make sure `private/household.yaml` exists (step 1 — the scripts fail closed without it);
  update `analysis/rates.py` from **your** bills (it is the single source of truth — non-SDG&E
  users replace the TOU windows and rate tables wholesale); place your Green Button CSV as
  `usage.csv` next to the scripts (`CLAUDE.md` "Commands" shows the `private/verify/` sandbox
  pattern); run `behavior_rebuild.py`, `battery_dispatch_policies.py`, `billing_model_nem.py`,
  `lifetime_payback.py`; then fill `report-template.html`'s `{{TOKEN}}`s from your regenerated
  `data/*.json`.

**4 · Validate before you trust it.** The gates in `CLAUDE.md` §9, in order: your billing
model must reproduce your actual bills before you quote any absolute dollar; every committed
artifact must regenerate from its committed script; report deltas, not levels.

**5 · Publish (optional).** Follow the GitHub Pages section below — after reading the
privacy note.

## Publish with GitHub Pages

```bash
cd sdge-rate-analysis
git init && git add . && git commit -m "SDGE rate analysis"
# create a repo on github.com (private recommended - see privacy note), then:
git remote add origin https://github.com/<you>/sdge-rate-analysis.git
git push -u origin main
```

Then on GitHub: **Settings → Pages → Source: Deploy from a branch → Branch: `main` / root → Save.**
Your report will be live at `https://<you>.github.io/sdge-rate-analysis/` within a minute or two.

> ### ⚠️ Privacy note
> All sensitive material (raw Green Button CSV with name/address/account number, Enphase
> exports, rate-research notes containing account details) lives in **`private/`**, which is
> excluded by `.gitignore` along with defensive filename patterns. **Never `git add -f`
> anything under `private/`.** Enforcement is mechanical, not manual: a gitleaks pre-commit
> hook (`git config core.hooksPath .githooks`) blocks commits containing secrets or account
> data, `.github/workflows/gitleaks.yml` re-scans full history on every push, and
> person-specific patterns live in the local-only `private/pii-rules.toml` (see `CLAUDE.md`
> §4). Belt-and-suspenders sanity check before any push:
> `git status --ignored` and `git ls-files | grep -i -E "private|electric_15|sam_8760"`
> (the second command should return nothing). The published report mentions city/climate
> zone only. A **private repo + GitHub Pages** requires GitHub Pro; on a free account,
> Pages means the site (and repo) are public.

## Repository layout

| Path | Pushed to GitHub? | Contents |
|---|---|---|
| `index.html`, `report-template.html`, `README.md`, `TECHNICAL.md`, `CLAUDE.md`, `reusable-prompt.md`, `DATA-SOURCES-CHEATSHEET.md` | ✅ yes | Report, template, and docs (PII-free) |
| `data/`, `analysis/`, `research/` | ✅ yes | Data, scripts, and rate research (PII-free) |
| `private/1-raw-data/` | ❌ gitignored | Raw SDGE Green Button CSV (contains name/address/account/meter); Enphase SAM 8760 hourly consumption (no identifiers, but reveals household occupancy patterns); CAISO raw day-cache |
| `private/household.yaml`, `private/intake-status.md` | ❌ gitignored | Per-house config written by the intake interview (invoice, dates, vehicle specs…) + the per-field gathered/skipped log that gates Phase B |
| `.env` | ❌ gitignored | Secrets only (PVOutput API key, monitoring tokens) — never in `household.yaml`, never committed |
| `private/3-analysis-extras/` | ❌ gitignored | As-run script copy with personal header |
| `private/README.md` | ✅ yes (placeholder) | Map of the private archive — the one file under `private/` that is committed, so the repo documents what's withheld |

## The private inputs — and how to obtain your own

Only two input datasets are withheld (plus the small `private/household.yaml` config the
intake interview writes — its schema is public in `household.example.yaml`), and anyone can
pull their own equivalents in minutes:

**1. Utility 15-minute interval export** (`Electric_15_Minute_<range>.csv`)
- SDG&E customers: My Energy Center (myenergycenter.com) → Usage → **Green Button Download** →
  set date range (13 months recommended) → format `.csv`. Other utilities: look for
  "Green Button" or "interval data" download in your usage portal — the standard is
  industry-wide, though column layouts vary slightly.
- Format: 13 metadata lines (name, address, account, meter — this is why it's private),
  then a header row and one row per 15-minute interval:
  `Meter Number, Date (M/D/YYYY), Start Time (h:mm AM/PM), Duration (15), Consumption (kWh imported), Generation (kWh exported), Net`.
- `analysis/analyze.py` reads it with `skiprows=13`.

**2. Hourly whole-home consumption, one year** — your total electrical *load*, which is not
the same as grid imports when you have solar. It powers the backup-endurance simulation, the
no-solar counterfactual behind the lifetime-payback numbers, and the load/production splits.
- **How this analysis got it (Enphase-specific):** Enlighten (enlighten.enphaseenergy.com) →
  Reports → **SAM 8760** → pick year → Submit (report is emailed). Requires Enphase
  consumption metering (CTs installed). Format: single column `kWh`, exactly 8,760 hourly
  values per calendar year; `analysis/battery_backup_sims.py` stitches two years into a
  rolling 365 days. No identifiers in the file — it's withheld only because hourly
  whole-home load reveals occupancy patterns.
- **Different solar or monitoring hardware? Use your platform's equivalent:** SolarEdge
  (consumption-meter export), Tesla (Powerwall/app energy history), SMA, Fronius, and others
  expose the same consumption feed if consumption metering is installed — as does any
  standalone circuit monitor (Emporia Vue, Sense, IoTaWatt). Any source works if you can
  shape it into hourly kWh for the year; adjust the two-column loader in
  `analysis/battery_backup_sims.py` to your export's format.
- **No consumption metering at all?** Derive it: `load = production + imports − exports`,
  using your production records (dataset the solar platform always has) and the utility
  interval export from item 1. Hourly resolution keeps the energy balance honest; the
  derivation caveats are covered in `TECHNICAL.md`.

Everything else needed to reproduce the analysis — daily production, PVOutput records,
all rate tables (`research/rates-reference.md`), and both models — is in this repo.
With your own two files above plus current rates, the scripts regenerate every number.

## Refreshing this analysis (same house, new data)

1. My Energy Center → Usage → Green Button Download → last 13 months, CSV → `private/1-raw-data/`.
2. If SDG&E (Jan/Jun) or CEA (Feb/Jun) issued new rates, update `analysis/rates.py` — the
   single source of truth all current models import. If the household changed (vehicle,
   charger, cleaning event, appliance), update `private/household.yaml` too.
3. Re-run the pipeline scripts (`CLAUDE.md` "Commands" has the exact invocations) and confirm
   each `data/*.json` regenerates cleanly — that diff-check is the acceptance gate.
4. Regenerate the report from `report-template.html` per `reusable-prompt.md` Phase D — or
   paste `reusable-prompt.md` into a Claude Cowork session and let it redo everything.
