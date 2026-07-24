# SDG&E Rate Plan Analysis

## 📊 [➡️ View the live report](https://ookla-ariel-ride.github.io/SDGE-Analysis/)

**Click the link above** to open the interactive report in your browser:
**https://ookla-ariel-ride.github.io/SDGE-Analysis/**

That page *is* `index.html`, served by GitHub Pages — charts render automatically, nothing to
install. (Alternate ways to view it: clone/download this repo and double-click `index.html`,
which is fully self-contained with data inlined and Chart.js from CDN. Note that GitHub's own
file viewer shows the HTML *source*, not the rendered report — use the link above instead.)

Interactive report comparing all eligible SDG&E residential rate plans against 365 days of
15-minute Green Button interval data for a solar + EV home in the SDG&E Coastal climate zone (NEM 2.0, CEA generation),
plus behavior-savings analysis, a home-battery deep-dive (Enphase vs Tesla Powerwall), solar-expansion and
inverter-clipping verdicts, weather-normalized cooling, a 12-month detailed-bill audit, and gas/electrification analysis.

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
| `data/pvoutput_yearly_2020-2025.csv` | PVOutput per-year production stats, 2020–2025 |
| `data/enphase_daily_production.csv` | Enphase daily production (CT meter), Jul 2025–Jul 2026 |
| `data/pvoutput_5min_sample.csv` | PVOutput 5-minute production sample day |
| `data/weather_daily_tmean.csv`, `data/weather_results.json` | Open-Meteo daily temperatures + weather-normalized cooling results |
| `data/gas_monthly_therms.csv`, `data/gas_bill_summary.csv` | Monthly gas usage and aggregated gas bill summary |
| `data/electric_bill_summary.csv` | Aggregated electric bill summary (12-month audit) |
| `data/deep_results.json`, `data/package_results.json` | Deep-analysis and package-simulation outputs |
| `analysis/analyze.py` | The plan billing model (Python/pandas) — rerun against a fresh Green Button CSV |
| `analysis/analyze_norelief.py` | Variant: prices CEA generation without the Rate Relief Credit |
| `analysis/battery_backup_sims.py` | Battery arbitrage + backup endurance simulations |
| `analysis/deep_analyses.py` | Weather normalization, bill audit, solar-expansion and clipping analyses |
| `analysis/package_sims.py` | Combined behavior + battery package simulations |
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
| `private/1-raw-data/` | ❌ gitignored | Raw SDGE Green Button CSV (contains name/address/account/meter); Enphase SAM 8760 hourly consumption (no identifiers, but reveals household occupancy patterns); bills and billing history |
| `private/3-analysis-extras/` | ❌ gitignored | As-run script copy with personal header |
| `private/README.md` | ✅ yes (only file in `private/` that is) | Explains what belongs in the private archive |

## Privacy & secret scanning (gitleaks)

Every commit and push is screened by [gitleaks](https://github.com/gitleaks/gitleaks):

- **CI:** `.github/workflows/gitleaks.yml` scans full history on every push and PR.
- **Locally:** a pre-commit hook blocks commits containing secrets or account data.
  One-time setup on a fresh clone:

  ```bash
  brew install gitleaks            # or see gitleaks docs for other platforms
  git config core.hooksPath .githooks
  ```

- **Rules:** `.gitleaks.toml` (committed) extends the default credential rules with
  generic patterns — SDGE bill-formatted account numbers, labeled account/meter
  numbers, and raw Green Button export headers. Person-specific patterns (actual
  name, address, account numbers) belong in `private/pii-rules.toml`, which is
  gitignored, chains onto `.gitleaks.toml`, and is picked up automatically by the
  hook when present. Never commit that file — it contains the values it guards.

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
