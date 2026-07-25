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

An interactive, evidence-based report for a solar + EV home in the SDG&E Coastal climate zone
(NEM 2.0, CCA generation), built from 365 days of 15-minute Green Button interval data,
a full-year detailed-bill audit, six years of production records, and real weather + grid data.

## What the report covers

| § | Section | What's in it |
|---|---|---|
| — | Bottom line | Integrated recommendation: plan, EV-timing fix, baseload hunt, battery verdict, solar-expansion verdict, payback status, carbon tip |
| 1 | The data | Triple-verified inputs: meter flows, whole-home load, production (3 independent sources, ±2%, 0.9999 correlation) |
| 2 | Your solar system today | Hardware inventory, size verification against measured peak power, health/degradation signals |
| 3 | Rate plan comparison | All eligible SDG&E plans priced against actual 15-min usage, CCA vs bundled, validated within 1% of SDG&E's own tool |
| 4 | Battery × plan matrix | Whether a battery changes the best-plan answer (it doesn't — it strengthens it) |
| 5 | Usage profile | Where the money goes by hour/period/month, with charts; EV-charging findings |
| 6 | Battery hardware | Arbitrage simulations of 6 real configurations + outage-endurance tiers |
| 7 | Three costed packages | Low ($0 behavior) / Mid (+1 battery) / High (+expansion): savings, projected bills, honest asset-alone paybacks |
| 8 | Array upgrades | More panels? Higher-capacity panels? Microinverter upgrade for clipping? All answered with measured data |
| 9 | Deeper analyses | 6-yr degradation, weather-normalized cooling, EV session report card, plan wildcards, vacation detection |
| 10 | Actual bills | 365-day bill audit, model-vs-actual reconciliation (rate-vintage, not methodology), gas usage + electrification (HPWH) |
| 11 | Lifetime payback | Install invoice vs cumulative production value by year — gross and net-of-ITC break-even dates |
| 12 | Cleaning & soiling | Measured cleaning effect (multi-year diff-in-diff), rain-recovery soiling study, optimal cleaning month & cadence economics |
| 13 | Carbon · NEM · prices | Grid-carbon timing from real CAISO data (with chart), $/yr value of NEM 2.0 grandfathering, battery vs rate-escalation ladder, phantom-baseload decomposition, marginal price map |
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
| `data/carbon_results.json` | Grid-carbon timing results (CAISO hourly intensity, household footprint, EV-timing deltas) |
| `data/extra_results.json` | Phantom-baseload decomposition, rate-escalation ladder, marginal price map, NBT re-billing, cleaning-cadence model |
| `TECHNICAL.md` | **Full technical/reproducibility documentation** — every script, data schema, algorithm, and chart pipeline, methods-section style |
| `CLAUDE.md` | Operating rules for AI-assisted reruns (evidence-based mandate, validation order, privacy gates, known pitfalls) |
| `analysis/analyze.py` | The plan billing model (Python/pandas) — rerun against a fresh Green Button CSV |
| `analysis/analyze_norelief.py` | Variant: prices CEA generation without the Rate Relief Credit |
| `analysis/billing_model_nem.py` | Bill-validated NEM 2.0 monthly per-TOU-period netting model |
| `analysis/behavior_rebuild.py` | Session-based EV/behavior shift model — physically moves kWh and re-bills (supersedes the crude cap approach) |
| `analysis/battery_backup_sims.py` | Battery arbitrage + backup endurance simulations |
| `analysis/soiling_analysis.py` | Soiling from rain-recovery events + days-since-rain regression (NOAA/RCC ACIS precipitation) |
| `analysis/carbon_timing.py` | Grid-carbon timing from CAISO Today's Outlook history data (CO2 + demand) |
| `research/rates-reference.md` | Every rate figure used: SDG&E UDC + EECC per plan, CEA generation, PCIA, fixed charges, baselines, TOU windows — with sources |
| `research/battery-research-notes.md` | 2026 battery prices/specs, incentive status, simulation summary |
| `research/sdge-plan-comparison-capture.md` | SDG&E's own plan-tool output vs this model |
| `reusable-prompt.md` | Full prompt to reproduce this entire analysis (plan + solar + battery + gas + bill audit) in Claude Cowork |
| `DATA-SOURCES-CHEATSHEET.md` | Fill-in-the-blanks checklist of every data source needed (links, which PDFs/exports to gather) for your own home |

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
> anything under `private/`.** Before any push, sanity-check with:
> `git status --ignored` and `git ls-files | grep -i -E "private|electric_15|sam_8760"`
> (the second command should return nothing). The published report mentions city/climate
> zone only. A **private repo + GitHub Pages** requires GitHub Pro; on a free account,
> Pages means the site (and repo) are public.

## Repository layout

| Path | Pushed to GitHub? | Contents |
|---|---|---|
| `index.html`, `README.md`, `reusable-prompt.md` | ✅ yes | Report + docs (PII-free) |
| `data/`, `analysis/`, `research/` | ✅ yes | Data, scripts, and rate research (PII-free) |
| `private/1-raw-data/` | ❌ gitignored | Raw SDGE Green Button CSV (contains name/address/account/meter); Enphase SAM 8760 hourly consumption (no identifiers, but reveals household occupancy patterns) |
| `private/3-analysis-extras/` | ❌ gitignored | As-run script copy with personal header |
| `private/README.md` | ❌ gitignored | Map of the private archive |

## The private inputs — and how to obtain your own

Only two input datasets are withheld, and anyone can pull their own equivalents in minutes:

**1. SDGE Green Button 15-minute interval CSV** (`Electric_15_Minute_<range>.csv`)
- Get yours: My Energy Center (myenergycenter.com) → Usage → **Green Button Download** →
  set date range (13 months recommended) → format `.csv`.
- Format: 13 metadata lines (name, address, account, meter — this is why it's private),
  then a header row and one row per 15-minute interval:
  `Meter Number, Date (M/D/YYYY), Start Time (h:mm AM/PM), Duration (15), Consumption (kWh imported), Generation (kWh exported), Net`.
- `analysis/analyze.py` reads it with `skiprows=13`.

**2. Enphase SAM 8760 hourly consumption** (`<system_id>_sam_8760_report.csv`, one per calendar year)
- Get yours: Enlighten (enlighten.enphaseenergy.com) → Reports → **SAM 8760** → pick year →
  Submit (report is emailed). Requires Enphase consumption metering (CTs installed).
- Format: single column `kWh`, exactly 8,760 hourly values, Jan 1 00:00 → Dec 31 23:00,
  local time; future hours of the current year are zero. No identifiers in the file —
  it's withheld only because hourly whole-home load reveals occupancy patterns.
- `analysis/battery_backup_sims.py` stitches two calendar years into a rolling 365 days.

Everything else needed to reproduce the analysis — daily production, PVOutput records,
all rate tables (`research/rates-reference.md`), and both models — is in this repo.
With your own two files above plus current rates, the scripts regenerate every number.

## Refreshing the analysis

1. My Energy Center → Usage → Green Button Download → last 12 months, CSV.
2. Replace the CSV path at the top of `analysis/analyze.py`.
3. Update the rate tables in the script if SDG&E (Jan/Jun) or CEA (Feb/Jun) have issued new rates.
4. `python3 analyze.py`, then update the `D = {...}` data block in `index.html`.

Or just paste `reusable-prompt.md` into a Claude Cowork session and let it redo everything.
