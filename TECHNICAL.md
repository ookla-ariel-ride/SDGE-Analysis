# TECHNICAL.md — Methods and Reproduction Reference

This document specifies, at reproduction-grade detail, every script in `analysis/`, the exact
schema of every input and output file, and the provenance of every chart in `index.html`. It is
written for someone who wants to run the same analysis on their own home. It deliberately does
not repeat the narrative context in `README.md` (what the project is), `DATA-SOURCES-CHEATSHEET.md`
(where to download each input), or `CLAUDE.md` (operating rules and lessons learned) — read those
first; this file is the methods section.

Subject system, stated once: a single-family home in the SDG&E Coastal climate zone with a
10.05 kW DC rooftop array (30 microinverters, ~9.45 kW AC), one EV, NEM 2.0, and Clean Energy
Alliance (CEA) generation on the EV-TOU-5 rate. Analysis window: the 365 days 2025-07-24 through
2026-07-23. All raw inputs containing personal identifiers live in `private/` (gitignored); only
de-identified aggregates are committed under `data/`.

---

## 1. Pipeline overview

The pipeline has three stages: raw exports from utility/monitoring portals (private), Python
scripts that reduce them to de-identified aggregates and simulation results (`analysis/` →
`data/`), and a single self-contained HTML report whose charts read an inlined JavaScript object
`D = {...}` (`index.html`).

```
private/1-raw-data/  (gitignored — never committed)
│
├── Electric_15_Minute_<range>.csv   SDG&E Green Button, 15-min imports/exports
├── enphase_sam8760_2025.csv         Enphase SAM 8760 hourly whole-home load, cal. 2025
├── enphase_sam8760_2026.csv         Enphase SAM 8760 hourly whole-home load, cal. 2026
├── gas_daily_jul25-jul26.csv        SDG&E gas Green Button, daily therms
├── electric-bills/*.pdf             12 detailed electric bill PDFs
├── gas-bills/*.pdf                  12 detailed gas bill PDFs
└── bill_summary.json                parsed bill lines (pdfplumber output)
        │
        │  analysis/ scripts (pandas/numpy; rate tables hard-coded from tariff PDFs)
        ▼
┌────────────────────────────┬──────────────────────────────────────────────┐
│ analyze.py /               │ plan_results.csv, hourly_profile.csv,        │
│ analyze_norelief.py        │ monthly.csv, stats.json (superset:           │
│                            │ report_data.json from the as-run variant)    │
│ battery_backup_sims.py     │ battery_sim.json, backup_endurance.json      │
│ package_sims.py            │ package_results.json                        │
│ deep_analyses.py           │ deep_results.json                           │
│ billing_model_nem.py       │ (stdout: bill-validated annual baseline)     │
│ lifetime_payback.py        │ (stdout: cumulative-value table, crossovers) │
│ behavior_rebuild.py        │ behavior_rebuild.json                        │
│ battery_dispatch_policies.py│ battery_dispatch_policies.json              │
│ soiling_analysis.py        │ soiling_results.json                         │
│ carbon_timing.py           │ carbon_results.json                          │
│ in-session steps (§3.7/§3.11)│ extra_results.json,                        │
│                            │ cleaning_study_daily.csv,                    │
│                            │ report_data.json, weather_results.json,     │
│                            │ threeway_production_validation.csv,          │
│                            │ electric/gas_bill_summary.csv, pvoutput_*,   │
│                            │ enphase_daily_production.csv,                │
│                            │ gas_monthly_therms.csv, weather_daily_*.csv  │
└────────────────────────────┴──────────────────────────────────────────────┘
        │
        │  numbers hand-transcribed into the inlined `const D = {...}` block
        ▼
index.html  →  five Chart.js canvases: #hourly, #battery, #monthly, #periods, #carbon
               (plus HTML tables/cards populated from the same data/ artifacts)
```

Two things to understand about the flow:

1. **The report does not fetch `data/` at runtime.** `index.html` is fully self-contained: the
   chart arrays are copied into the `D` object, and every table/card number is static HTML. The
   `data/` folder is the audit trail. When you rerun the scripts you must manually refresh both
   the `D` block and the prose figures (see `CLAUDE.md` §3 on keeping figures consistent).
2. **Two generations of billing model coexist.** `analyze*.py` price every 15-minute interval
   independently (interval netting). `billing_model_nem.py` implements true NEM 2.0 monthly
   per-TOU-period netting with non-bypassable charges on gross imports, and was validated
   against the 12 actual bills; the two methods agree to ~0.5% on this dataset ($4,861 vs
   $4,884 — §6). Plan *rankings* and battery/behavior *deltas* come from the interval models;
   absolute dollar levels in the report are anchored to actual bills, and forward projections
   are stated on the single §3.6 basis at constant 6/1/2026 rates.

---

## 2. Input data formats

### 2.1 SDG&E Green Button electric interval CSV (`Electric_15_Minute_<range>.csv`)

The file begins with **13 metadata rows** (two comma-separated fields each: Name, Address,
Account Number, Disclaimer, Title, Resource, Meter Number, Interval UOM, Reading Start, Reading
End, Total Duration, Total Usage, UOM). These rows contain the personal identifiers that make
the file private. Every script skips them with `pd.read_csv(..., skiprows=13)`.

Row 14 is the header; each subsequent row is one 15-minute interval, all fields quoted:

| Column | Format | Meaning |
|---|---|---|
| `Meter Number` | string | meter ID (unused by the scripts) |
| `Date` | `M/D/YYYY` | interval date, local time |
| `Start Time` | `h:mm AM/PM` | interval start, local time |
| `Duration` | `15` | minutes |
| `Consumption` | kWh, 4 decimals | **grid imports** during the interval |
| `Generation` | kWh, 4 decimals | **grid exports** (solar surplus) during the interval |
| `Net` | kWh | `Consumption − Generation` |

Critical semantics: `Consumption` is *grid import*, not household load, and `Generation` is
*grid export*, not solar production. Self-consumed solar is invisible to this file — that is why
the Enphase consumption data (§2.2) is required for load/production analysis. The export used
here covered 2025-07-01 through 2026-07-23 (37,248 interval rows); all scripts then window to
the last 365 days. Timestamps are parsed with
`pd.to_datetime(Date + " " + Start Time, format="%m/%d/%Y %I:%M %p")`.

### 2.2 Enphase SAM 8760 hourly consumption (`enphase_sam8760_<year>.csv`)

One file per calendar year. A single column with header `kWh` and exactly 8,760 hourly values
(8,761 lines including the header), representing whole-home consumption from Jan 1 00:00 to
Dec 31 23:00 **local time**; hours in the future of the current year are zero. No identifiers.
`battery_backup_sims.py` and `deep_analyses.py` assign the values to
`pd.date_range("<year>-01-01", periods=8760, freq="h")` and stitch the two years into the
rolling 365-day window. Requires Enphase consumption CTs ("load with solar" metering).

### 2.3 Enphase daily production (`data/enphase_daily_production.csv`)

Committed (de-identified). Columns: `Date/Time` (`MM/DD/YYYY`), `Energy Delivered (kWh)` —
revenue-grade production-CT daily totals, one row per day of the analysis window (366 data rows).

### 2.4 PVOutput records (committed under `data/`)

- `pvoutput_daily.csv`: `date` (ISO), `generated_kwh` — daily gross generation from the
  microinverter fleet as published to PVOutput, same window.
- `pvoutput_5min_sample.csv`: one sample day (288 five-minute rows) with columns
  `DATE, TIME, ENERGY_OUT, POWER_OUT, ENERGY_IN, POWER_IN, TEMPERATURE, VOLTAGE` (PVOutput
  donation-API intraday format; `POWER_OUT` in watts is the field used for the clipping check).
- `pvoutput_yearly_2020-2025.csv`: `year, kwh_generated, kwh_exported, avg_eff_kwh_per_kw_day,
  days` — per-year statistics used for the degradation trend (e.g. 2021 efficiency 4.749,
  2025 4.501 kWh/kW/day).

### 2.5 Gas Green Button daily CSV (`gas_daily_jul25-jul26.csv`)

Same envelope as §2.1: 13 metadata rows (with `Interval UOM = Day`, `UOM = Therms`), then header
`Meter Number, Date, Start Time, Duration, Consumption` with `Duration = "Day"` and
`Consumption` in therms per day (read at ~6:59 AM). Aggregated in-session to
`data/gas_monthly_therms.csv` (`month` as `YYYY-MM`, `therms`); the window total is 342 therms.

### 2.6 Open-Meteo daily temperature

Daily mean temperatures were pulled from the Open-Meteo archive API (JSON response with parallel
daily arrays of dates and temperatures) for the home's approximate coordinates, and persisted as
`data/weather_daily_tmean.csv`: an unnamed date index column plus one unnamed value column
(header `0`) holding the daily mean temperature in °F (e.g. 2025-07-24 → 67.6). The cooling
regression (§3.7) uses degree-day bases of 65 °F (CDD) and 60 °F (HDD).

### 2.7 Detailed bill PDFs

Twelve electric and twelve gas monthly "detailed bill" PDFs from the utility portal, parsed with
**pdfplumber** in-session. The extracted electric fields are archived privately as
`private/1-raw-data/bill_summary.json` (a list of 12 objects with keys `period` (e.g.
`"10/28/25 - 11/25/25"`), `days`, `net_kwh`, `gross_kwh`, `sdge_delivery`, `cca_generation`,
`current_charges`) and committed de-identified as `data/electric_bill_summary.csv` (same columns).
Gas bills reduce to `data/gas_bill_summary.csv`: `file_month, therms, total_gas_service,
baseline_rate, nonbaseline_rate` (e.g. Jan 2026: 72 therms, $203.25, baseline $2.02136/therm,
non-baseline $2.37552/therm).

---

## 3. Script-by-script reference

All scripts are Python 3 requiring only `pandas` and `numpy` (`billing_model_nem.py` needs only
those two; nothing else imports anything beyond the standard library). None takes command-line
arguments; input paths are constants at the top of each file. `analyze*.py` contain an absolute
path from the original session — edit `CSV = ...` to your Green Button file. The other four
scripts expect files in the working directory under fixed names:

- `usage.csv` → your Green Button 15-minute export (§2.1)
- `samA.csv` → SAM 8760 for the **current** calendar year (here 2026)
- `samB.csv` → SAM 8760 for the **prior** calendar year (here 2025)

Also edit the window anchor `end = dt.datetime(2026,7,24)` (the exclusive end of the 365-day
window) in every script to the day after your last full data day.

### 3.0 Shared preprocessing and constants (identical logic in the six original scripts; §3.8–3.10 reuse the same loading, window, and TOU rules)

**Loading.** `read_csv(skiprows=13)`; strip column whitespace; build `dt` from `Date` +
`Start Time`; coerce `Consumption`/`Generation` (and `Net` in `analyze*.py`) to numeric; filter
to `end − 365 days ≤ dt < end`.

**Season.** `summer ("S")` = months **June–October** (`month in {6,7,8,9,10}`); all other months
are winter (`"W"`).

**TOU period assignment** (3-period plans; windows effective May 2026, identical for SDG&E and
CEA), computed per 15-minute interval from fractional hour `h`:

- **on-peak**: 16:00–21:00 (4–9 pm), every day of the year;
- **super-off-peak (sop)**: weekdays `h < 6` or `10 ≤ h < 14`; weekends `h < 14`;
- **off-peak**: everything else (weekdays 6–10, 14–16, 21–24; weekends 14–16, 21–24).

For the 2-period plan TOU-DR2, `analyze*.py` use simply on = 16–21, off = otherwise.

**Holidays.** `analyze.py`/`analyze_norelief.py` treat seven holidays as weekend days for TOU
purposes (New Year's, Presidents' Day = 3rd Mon Feb, Memorial Day = last Mon May, July 4, Labor
Day = 1st Mon Sep, Veterans Day, Thanksgiving = 4th Thu Nov, Christmas). The four newer scripts
use only `weekday >= 5` — a known, dollar-negligible inconsistency (§6.5).

**Rate constants** (all $/kWh unless noted; sources and effective dates in
`research/rates-reference.md`):

| Constant | Value | Meaning |
|---|---|---|
| `WFNBC_DWR` | 0.00591 | Wildfire Fund NBC + DWR bond charge |
| `PCIA` | 0.02828 | Power Charge Indifference Adjustment, 2023-vintage CCA customer |
| `NBC` | 0.01515 + 0.00000 − 0.00007 + 0.00591 ≈ 0.02099 | non-bypassable charges (PPP + ND + CTC + WF-NBC/DWR) — **not** credited on exports |
| `BSC` | 0.79343 $/day | Base Services Charge, all residential plans |
| `CEA_RELIEF` | −0.03871 | CEA Clean Impact rate-relief credit (bill audit later proved it does **not** apply to this account — see §3.2/§6.2) |
| `BASELINE_CREDIT` | −0.10663 | credit on net consumption up to 130% of baseline (TOU-DR1/DR2/DR-P only) |
| `BASELINE` | S 10.4 / W 9.6 kWh/day | baseline allowance **as coded** — an initial climate-zone assumption; the bill audit fixed the home in the SDG&E Coastal zone, whose allowances the report states as 9.0/9.2. Immaterial to conclusions because the winning plan (EV-TOU-5) has no baseline credit; substitute your own zone's allowance from Schedule DR. |

**SDG&E delivery (UDC) totals, effective 6/1/2026** (from `analyze*.py`; S = summer, W = winter;
same value on/off where the plan doesn't differentiate delivery):

| Plan | S on | S off | S sop | W on | W off | W sop |
|---|---|---|---|---|---|---|
| EV-TOU-5 | 0.31711 | 0.31711 | 0.04114 | 0.31711 | 0.31711 | 0.04114 |
| EV-TOU-2 | 0.30372 | 0.30372 | 0.16275 | 0.30372 | 0.30372 | 0.16275 |
| TOU-DR1 | 0.32948 | 0.32948 | 0.32948 | 0.32948 | 0.32948 | 0.32948 |
| TOU-DR2 | 0.33396 | 0.32750 | — | 0.32948 | 0.32948 | — |
| TOU-DR-P | 0.32948 | 0.32948 | 0.32948 | 0.32948 | 0.32948 | 0.32948 |
| TOU-ELEC | 0.25317 | 0.25317 | 0.25317 | 0.25317 | 0.25317 | 0.25317 |

**CEA generation, effective 6/1/2026** (Clean Impact schedule):

| Plan | S on | S off | S sop | W on | W off | W sop |
|---|---|---|---|---|---|---|
| EV-TOU-5 / EV-TOU-2 / TOU-ELEC | 0.51684 | 0.15975 | 0.04961 | 0.24430 | 0.15782 | 0.05187 |
| TOU-DR1 | 0.55397 | 0.22298 | 0.04914 | 0.19791 | 0.08433 | 0.05138 |
| TOU-DR2 | 0.53685 | 0.14663 | — | 0.19180 | 0.06703 | — |
| TOU-DR-P | 0.38778 | 0.15609 | 0.04914 | 0.13854 | 0.05903 | 0.05138 |

**SDG&E bundled generation (EECC), effective 6/1/2026** (used only for the "what if you left the
CCA" comparison):

| Plan | S on | S off | S sop | W on | W off | W sop |
|---|---|---|---|---|---|---|
| EV-TOU-5 / EV-TOU-2 | 0.47019 | 0.17311 | 0.08147 | 0.19990 | 0.14337 | 0.07410 |
| TOU-DR1 | 0.34920 | 0.12853 | 0.04121 | 0.27475 | 0.19304 | 0.10228 |
| TOU-DR2 | 0.34920 | 0.08432 | — | 0.27475 | 0.13777 | — |
| TOU-DR-P | 0.19848 | 0.15523 | 0.08247 | 0.25057 | 0.17606 | 0.09329 |
| TOU-ELEC | 0.45690 | 0.12945 | 0.08637 | 0.24311 | 0.11774 | 0.07856 |

The all-in retail rate per interval is `UDC + WFNBC_DWR + PCIA + CEA_gen` for a CCA customer, or
`UDC + WFNBC_DWR + EECC` bundled. Export credit per kWh is `max(rate − NBC, 0)` (NEM 2.0:
retail minus non-bypassable charges).

### 3.1 `analysis/analyze.py` — plan billing model (interval netting, with relief credit)

**Purpose.** Price the 365-day usage record under all six eligible TOU plans, for both CEA and
bundled-SDG&E generation, and emit the aggregate usage profiles.

**Inputs.** The Green Button CSV only (path constant `CSV`).

**Algorithm.**
1. Load and window as in §3.0; assign `p3` (3-period TOU with holiday handling) and `p2`
   (2-period, for TOU-DR2).
2. For each of the six plans × {CEA, SDGE}: build the per-interval rate vector; charges =
   Σ(`Consumption` × rate); credits = Σ(`Generation` × max(rate − NBC, 0)); energy = charges −
   credits. The CEA runs in this script add `CEA_RELIEF` to the generation rate.
3. Baseline credit (TOU-DR1/DR2/DR-P only): group by calendar month; if monthly `Net` sum > 0,
   credit `min(net, 1.3 × BASELINE[season] × days) × BASELINE_CREDIT`.
4. Fixed charge = `BSC × 365`. Total = energy + baseline credit + fixed.
5. Usage statistics: annual import/export/net totals; import/export kWh by (season, period);
   the all-in EV-TOU-5+CEA rate applied per interval to get on-peak import cost; night
   (<6 am) import kWh; mean import/export/net per 15-minute interval grouped by hour of day;
   calendar-month sums.

**Outputs.** `plan_results.csv` (columns `plan, provider, energy, baseline_credit, fixed,
total`), `hourly_profile.csv` (`dt` = hour 0–23; `imp, exp, net` = **mean kWh per 15-minute
interval** at that hour — multiply by 4 for average kW), `monthly.csv` (`dt` = `YYYY-MM`;
`imp, exp, net` kWh — 13 rows because the window makes the first and last calendar months
partial), and `stats.json` (as-run superset committed as `data/report_data.json`, §3.7).

**Run:** edit `CSV`, then `python3 analysis/analyze.py`.

### 3.2 `analysis/analyze_norelief.py` — the published variant (no relief credit)

Byte-for-byte the same model as `analyze.py` with one change: the CEA runs call
`bill(p, "CEA", relief=False)`, i.e. the −$0.03871/kWh relief credit is **not** applied. The
bill audit later confirmed this is the correct model for this account (the bills show CEA
product "Clean Impact Plus" with only a +$0.001/kWh adder and no relief line), so **the
committed `data/plan_results.csv` is this script's output** — e.g. EV-TOU-5/CEA energy
$4,559.04 + fixed $289.60 = total $4,848.65, matching the report's $4,849. Keep `analyze.py` if
your CCA product does earn a credit; otherwise run this one.

### 3.3 `analysis/battery_backup_sims.py` — arbitrage value + outage endurance

**Inputs.** `usage.csv`, `samA.csv` (2026), `samB.csv` (2025).

**Rates.** EV-TOU-5 only: `UDC + WFNBC + PCIA + CEA` per interval, no relief credit; NBC as in
§3.0.

**Part 1 — arbitrage `sim(cap, pwr, name, eff=0.90)`.** Dispatch is described in §4. Simulated
configurations `(usable kWh, power kW)`: 1× Enphase IQ 5P (5, 3.84); 1× IQ 10C (10, 7.08);
1× Tesla Powerwall 3 (13.5, 11.5); 3× IQ 5P (15, 7.68); 2× IQ 10C (20, 7.08); PW3 + 1 Expansion
(27, 11.5). Output `battery_sim.json`: per config, `onpeak_offset_value`,
`forgone_export_credits`, `grid_charge_cost`, `net_annual_savings` (= offset − forgone − grid),
`equiv_full_cycles` (Σ discharge ÷ capacity). Example: 1× PW3 → offset $2,252, forgone $382,
grid $201, net $1,669/yr, 228 cycles.

**Part 2 — backup endurance.** (a) Stitch the two SAM 8760 series onto hourly indexes for 2025
and 2026 and slice the window 2025-07-24 → 2026-07-23 23:00 → hourly whole-home load `load`.
(b) Resample the Green Button data to hourly sums of imports/exports. (c) Derive hourly solar
production as `prod = clip(load − imports + exports, 0)` — the identity load = production +
imports − exports. (d) EV heuristic: hours with `load > 7` kWh are EV-charging hours; EV
component = `load − 1.5`; `nonev = load − ev`. (e) Two backup tiers: `t1` (essentials) =
`min(nonev, 0.7)` — a 0.7 kW cap; `t2` = whole house minus EV. (f) Endurance loop per §4.
Output `backup_endurance.json`: keys `"<config>|<tier>"` → `{median_h, p10_h}` over all
simulated outage starts (e.g. `"PW3|t1"` → median 336 h = the 14-day cap, 10th percentile 90 h;
`"PW3|t2"` → median 7 h). Configs here: IQ 5P, IQ 10C, PW3, PW3+Exp.

**Run:** `python3 battery_backup_sims.py` from a directory containing the three input files.

### 3.4 `analysis/package_sims.py` — plan × battery matrix and LOW/MID/HIGH packages

**Inputs.** `usage.csv` only. **Baseline scenario:** EV-TOU-5, CEA generation **without** relief
credit, current behavior.

**Behavior adjustment `behavior_adjust(cons)`.** Cap = 0.625 kWh per 15-minute interval
(= 2.5 kW). Two edits to the import series: (1) any **on-peak** interval above the cap is
trimmed to the cap; (2) any **off-peak 6–9 am** interval above the cap (charging spill-over past
6 am) is trimmed to the cap. All trimmed energy (`moved_kwh` = 2,507 kWh/yr on this dataset) is
assumed re-consumed overnight and billed at the plan's super-off-peak all-in rate, averaged
across seasons. See §6.4 for the honesty caveat.

**Battery dispatch.** Same greedy logic as §4 (charge condition here is "any non-on-peak period
with exports", which is equivalent to the other scripts' sop/off condition since exports are
zero after 4 pm in practice). Configurations: PW3 (13.5, 11.5) and PW3+Expansion (27, 11.5).

**Annual cost `annual_cost(plan, cons, gen, battery, moved_kwh)`.** Interval net cost =
charges − export credits − battery on-peak offset + forgone export credit + grid-charge cost;
monthly series = interval net grouped by month + days × BSC; annual total = interval net sum +
moved-load cost + 365 × BSC + baseline credit (TOU-DR1 only, same rule as §3.1).

**Outputs (script stdout/JSON).**
- `plan_battery_matrix`: EV-TOU-5 / EV-TOU-2 / TOU-DR1 × {no_battery, with_PW3, battery_value}
  (e.g. EV-TOU-5: $4,861 → $3,192, battery worth $1,669/yr).
- `baseline`: annual cost $4,861, average/min/max modeled monthly bill.
- `moved_kwh`: 2507.
- `battery_marginal_after_behavior`: the battery's own savings measured *after* behavior fixes
  (PW3 $1,347/yr; PW3X $1,351/yr) — the honest asset-alone figure required by `CLAUDE.md` §2.

**The committed `data/package_results.json` was REGENERATED** on the superseding basis
(session-based shifts from `behavior_rebuild.py` §3.8 + the **price-aware dispatch** from
`battery_dispatch_policies.py` §3.13, NBC on gross imports, 6/1/2026 rates) after the
pre-publication artifact–prose gate (`CLAUDE.md` §9) caught the stale script output still
carrying a retired package-payback framing. Its schema:

- `basis` — provenance string (also records the actual 365-day billed baseline $3,282 on
  2025-vintage tariffs);
- `model_baseline_current_rates`: **4884** (= §3.6 output);
- `packages.LOW`: `cost` 0, `savings_yr` 1193, `savings_range` [1012, 1672], `note`,
  `projected_bill_current_rates_yr` **3691**;
- `packages.MID`: `cost` 14500, `savings_yr` **3368**, `battery_alone_yr` **2259**
  (price-aware; `battery_alone_post_ev_fix_yr` **2175**), `battery_alone_payback_yr` **6.4**
  (`battery_alone_payback_evening_only_yr` 8.8), `battery_monte_carlo_median_yr` 9.4,
  `projected_bill_current_rates_yr` **1516**, `note` (single integrated shift-then-battery run);
- `packages.HIGH`: `cost` 20400, `marginal_vs_mid_yr` **186** post-behavior (~32-yr marginal payback on the
  $5,900 expansion — buys outage endurance, not savings);
- `superseded` — records that dividing hardware cost by combined behavior+battery savings is
  invalid; battery-alone payback is the honest hardware metric.

**Run:** `python3 package_sims.py` next to `usage.csv` (script output only; rebuild the
committed artifact from §3.6/§3.8 results as above).

### 3.5 `analysis/deep_analyses.py` — five targeted studies

**Inputs.** `usage.csv`, `samB.csv`, `samA.csv`. Rates: EV-TOU-5 + CEA (no relief). All results
land in `deep_results.json`.

1. **TOU-DR-P + battery wildcard.** TOU-DR-P priced with UDC 0.32948 flat and the CEA TOU-DR-P
   generation row (§3.0), plus a Reduce-Your-Use surcharge of **$1.16/kWh** on 15 assumed event
   days (the 15 summer days with the highest on-peak imports), 4–9 pm. Three scenarios: TOU-DR-P
   with a PW3 that dodges every event ($6,719), EV-TOU-5 with the same PW3 ($3,192), TOU-DR-P
   with no battery and all events hit ($7,483).
2. **Phantom/baseload.** Take 3–5 am intervals with `Consumption ≤ 0.5` kWh (excludes EV
   charging); baseload kW = 25th percentile × 4 → 1.02 kW; annualized at a blended $0.20/kWh →
   $1,787/yr (flagged in the report as an upper bound, not recoverable waste).
3. **EV charging sessions.** Interval kW = `Consumption × 4`; intervals with kW > 6.5 are
   charger-on; contiguous runs form sessions; session kWh = Σ imports − 0.4 kW house base ×
   duration; sessions < 3 kWh discarded. Results: 576 sessions, 14,158 kWh, $3,081/yr at actual
   timing vs $1,780 if all charging were at a $0.1257/kWh blended super-off-peak rate → $1,301/yr
   lost to mistimed charging; 931 kWh of session energy fell on-peak, 1,718 kWh off-peak.
4. **Vacation detection.** Daily sums of the SAM hourly load excluding hours > 7 kWh (crude
   non-EV load); away threshold = max(10th percentile, 20 kWh/day) = 26.3; 37 away-days detected
   against a 37.7 kWh/day non-EV median.
5. **Monte Carlo battery ROI.** N = 5,000 draws, `numpy` RNG seed 42; annual rate escalation ~
   U(0, 10%); capacity fade ~ U(0.5%, 2.5%)/yr; installed price ~ U($12,500, $17,000); year-1
   marginal savings fixed at **$1,347** (the PW3 after-behavior figure from §3.4 — update this
   constant if you rerun `package_sims.py`); 25-year horizon; payback linearly interpolated;
   NPV over 10 years at 4% discount. Results: median payback 9.4 yr (p10 8.1, p90 11.6), 64.5%
   probability of payback within a 10-year warranty, median 10-yr NPV −$2,158. The report
   keeps this deliberately conservative Monte Carlo as the downside bracket alongside the
   §3.13 price-aware basis (~6–7 yr simple payback at $2,259/yr).

**Run:** `python3 deep_analyses.py` next to the three inputs.

### 3.6 `analysis/billing_model_nem.py` — bill-validated NEM 2.0 monthly netting

**Purpose.** Replace the interval-netting approximation with the netting SDG&E actually
performs under NEM 2.0, and reconcile the model against the 12 real bills.

**Rates (read off the detailed bills, EV-TOU-5 + CEA "Clean Impact Plus", 6/1/2026).** UDC:
summer on/off 0.30203, winter on/off 0.31174, super-off-peak 0.02606 both seasons; CEA
generation as in §3.0; NBC 0.021 flat; PCIA 0.02828; BSC 0.79343/day. `energy(s,p) =
UDC + CEA + PCIA` (the netted energy rate — NBC is handled separately); `credit(s,p) =
UDC + CEA` (exports are credited at delivery + generation only — PCIA and NBC are never
refunded).

**Algorithm (`bill()`).** For each billing month (calendar month here): add `days × BSC` plus
**NBC × GROSS imported kWh** for the month — non-bypassable charges (~$0.021/kWh incl. the
wildfire fund) are levied on gross imports and are NOT netted against exports, matching the
bills' line items (e.g. a "Wildfire Fund Charge 308 kWh" line on a period with 224 kWh net
usage). Then for each (season, period) cell within the month compute `net = Σ imports −
Σ exports`; if `net ≥ 0` charge `net × energy(s,p)`, else credit `net × credit(s,p)`. Sum
across months. This is the "monthly per-TOU-period NEM netting" referred to throughout the
report. An earlier revision netted the NBC along with the energy charges — a
code-vs-docstring bug caught by the `CLAUDE.md` §9 gate; the correction adds
NBC × (gross imports − Σ positive period nets) ≈ **+$208/yr** on this dataset.

**Output.** Prints the modeled annual baseline **$4,884** at 6/1/2026 rates (the retired
netted-NBC variant gave $4,675) against the actual billed $3,282 (365-day audit) — the
reconciliation of that gap is §6.3. Adapt `bill()` (it accepts arbitrary import/export column
names) to re-score behavior or battery scenarios on the validated netting.

**Run:** `python3 billing_model_nem.py` next to `usage.csv`.

### 3.7 In-session artifacts (no committed generator script)

Several `data/` files were produced by short ad-hoc steps during the analysis session rather
than by a committed script; they are documented here so they can be regenerated:

- **`report_data.json`** — output of the as-run extended variant of `analyze.py` (archived
  privately in `private/3-analysis-extras/`). Superset of `stats.json`: all-in EV-TOU-5 rates
  per season/period (`rates_ev5`, e.g. summer on-peak 0.86814, sop 0.12494 — these *include*
  the relief credit, an as-run artifact); seasonal 24-hour average-kW import/export profiles
  (`hourly_S`, `hourly_W`); monthly labels/imports/exports/modeled cost; per-(season, period)
  import/export/cost split (`period_split`); on-peak summary (3,989 kWh imported, $2,951 gross
  cost, 41.5% of energy cost); EV proxy stats (kWh above 2.5 kW by period and by start hour);
  early battery estimate (`battery.net` $1,939 — superseded by `battery_sim.json`'s $1,669);
  annual totals (imports 23,278, exports 9,922 kWh).
- **`threeway_production_validation.csv`** — date-indexed daily `pvoutput` vs `enphase_meter`
  production (the third series, derived production = load − imports + exports from §3.3, is
  computed in-script and summarized in the report: 16,660 kWh vs 16,839 and 16,502).
- **`weather_results.json`** — OLS regression of daily non-EV load on Open-Meteo degree-days:
  base 42.2 kWh/day, +3.0 kWh per CDD65, −1.23 per HDD60, R² 0.45, 1,738 kWh/yr cooling,
  pre-cool shift value $233/yr, setpoint value $104/yr.
- **`electric_bill_summary.csv` / `gas_bill_summary.csv`** — pdfplumber extractions (§2.7).
- **`pvoutput_daily.csv`, `pvoutput_5min_sample.csv`, `pvoutput_yearly_2020-2025.csv`,
  `enphase_daily_production.csv`** — portal exports/scrapes, reformatted to the schemas in §2.
- **`gas_monthly_therms.csv`, `weather_daily_tmean.csv`** — monthly resample of the gas Green
  Button file; Open-Meteo pull (§2.5–2.6).

### 3.8 `analysis/behavior_rebuild.py` — session-based EV/behavior shift model

**Purpose.** Supersedes the crude 2.5 kW-cap shift (§3.4/§6.4): detects EV sessions
explicitly, physically moves their energy into destination intervals, and re-bills the
modified year on the bill-validated NEM netting.

**Inputs.** `usage.csv` only. Rates are identical to `billing_model_nem.py` (bill-read
EV-TOU-5 + CEA "Clean Impact Plus", 6/1/2026); billing uses the same monthly per-TOU-period
NEM netting (`bill_monthly()`).

**EV detection.** Import power (kW = Consumption × 4) minus a centered rolling-24 h
(96-interval) 20th-percentile baseline (tracks the always-on house floor, immune to
multi-hour charge blocks); candidate intervals have excess ≥ 2.5 kW; a session is a
contiguous candidate run ≥ 30 min whose *peak* excess ≥ 8 kW (the EV charges at ~11.5 kW;
nothing else in the house sustains 8 kW); EV kWh per interval = clip(excess, 0, 11.5 kW) ×
0.25 h, capped at the interval's actual import. Detected: 560 sessions, 13,723 kWh/yr
(vs ~13,100 expected), of which 878 kWh on-peak, 1,697 off-peak, 11,148 already
super-off-peak.

**Scenario ladder (energy conserved, not lump-summed).** Shifted kWh are removed from their
source intervals and poured into super-off-peak intervals starting at the next midnight,
honoring the 11.5 kW charger cap net of EV charging already present in the destination.
(a) EV-only, 100% compliance: 2,575 kWh moved, **$1,193/yr** saved; (b) EV-only, 80%
compliance (seeded RNG): $1,012; (c) + 25% of remaining on-peak house load: $1,672;
(d) stretch, + 50%: $2,151. (Canonical engine, NBC on gross imports — the script imports
`rates.py`.) A 13.5 kWh / 11.5 kW / 90%-RTE battery re-simulated on top of
(a): $1,876/yr marginal on the baseline → **$1,752/yr after behavior** ($124/yr of
double-counting avoided). NOTE: these battery figures are the **evening-only dispatch
variant, retired as the published basis** — the report's battery economics now come from the
price-aware dispatch of §3.13 ($2,259/yr baseline, $2,175/yr post-EV-fix after deducting the
same $130 overlap). The $130 overlap measurement is what this script still contributes.

**Output `data/behavior_rebuild.json`.** Keys: `window`; `baseline` (`model_bill` $4,675.20
— the pre-correction netted-NBC baseline; the published baseline is §3.6's **$4,884** with
NBC on gross imports. The scenario *deltas* are unaffected because load shifts preserve gross
imports, so the NBC term cancels — vs `actual_billed` $3,282 — use deltas, per the in-file
note; `month_min/max`;
`imports_kwh`, `exports_kwh`, `onpeak_import_kwh`); `detection` (rule string, `sessions`,
`ev_kwh_total/expected/onpeak/offpeak/sop_already`, `avg_session_kwh`); `scenarios.a–d`
(`label`, `bill`, `saved`, `month_min/max`, `kwh_moved`, plus `sessions_moved` /
`house_kwh_moved` where applicable); `battery` (`spec`, `marginal_on_baseline`,
`marginal_after_scenario_a`, `double_count_avoided`); `note`.

### 3.9 `analysis/soiling_analysis.py` — soiling loss from rain-recovery events

**Purpose.** Quantify panel-soiling losses without a dedicated soiling station, using rain
events as natural cleanings. Pure Python (stdlib only — its own OLS and t-distribution
p-values via the incomplete beta function).

**Inputs.** `pvoutput_daily.csv` and the Enphase daily production export; daily
precipitation transcribed verbatim into the script from the **NOAA/RCC ACIS** web service
(`data.rcc-acis.org/StnData`, nearest airport gauge — free, no key). ACIS was used because
the Open-Meteo archive API returned empty bodies through the sanctioned fetch proxy for
every query variant, so no satellite irradiance exists and normalization is deterministic.

**Algorithm.**
1. Normalize each day's kWh by a deterministic clear-sky GHI (Haurwitz model, pure solar
   geometry with Spencer declination, 120 s integration) → daily performance index; drop
   near-zero outage days.
2. Flag **clear days**: performance ≥ 95% of the local ±10-day 90th percentile.
3. **Rain events** (≥ 5 mm after a dry spell; wet clusters merged): compare pre vs post
   10-day clear-day medians, raw and seasonally adjusted (harmonic-regression residuals).
   Seasonally adjusted recoveries across the four events: **0 to +3.4%**.
4. **Days-since-rain regression** on clear days: log(perf) ~ seasonal harmonics (sin/cos of
   day-of-year, two orders) + days-since-rain, with a VIF diagnostic (≈ 5.2 — the soiling
   term is partly collinear with season, hence the humility). Result: 0.45%/month (PVOutput
   series) / 0.64%/month (Enphase series) — vs **2.4%/month implied by the verified 2024
   cleaning** (+11.8% gain after 134 dry days). The two lines of evidence disagree and are
   honestly reported as a **~0.45–2.4%/month bracket**.
5. **Annual economics**, modeling loss(t) = rate × days-since-rain (capped): scenario A
   (this year's evidence) 216 kWh ≈ $68/yr; scenario B (2024-cleaning evidence) 1,106 kWh ≈
   $348/yr, both at the script's $0.315/kWh blended value (an earlier blended estimate,
   retained in the committed artifact; `lifetime_payback.py` §3.12 later refined the
   current-TOU blended value to $0.3025/kWh — immaterial to the order-of-magnitude verdict).

**Output `data/soiling_results.json`.** Keys: `meta` (window, sources, thresholds);
`production_crosscheck`; `pvoutput` / `enphase` (each with `n_days_used`, `n_clear_days`,
`monthly_median_clearday_perf_kwh_per_kwhm2`, `events[]` — id, wet window, event_mm,
dry_days_before, n_clear_pre/post, `recovery_pct_raw`, `recovery_pct_seasonal_adj`,
`implied_soiling_rate_pct_per_month` — and `regression`); `sanity_check_2024_cleaning`;
`annual_economics` (both scenarios + caveat).

### 3.10 `analysis/carbon_timing.py` — grid-carbon timing (real CAISO data)

**Purpose.** When is a grid kWh cleanest for this household, and what do EV-charging-time
choices cost in CO2? Imports `behavior_rebuild.py` and reuses its `load()` and
`detect_sessions()` exactly.

**Intensity source (real ISO data, no synthetic curves).** CAISO "Today's Outlook" history
endpoints — `https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv` (5-min CO2 by source,
metric tonnes/hour) and `.../demand.csv` (5-min demand, MW) — for four mid-season sample
days inside the analysis year (mid-Oct 2025; mid-Jan, mid-Apr, mid-Jul 2026). Hourly
grid-average intensity: kg CO2/MWh = 1000 × mean(total CO2 mT/h, all sources incl. imports)
÷ mean(demand MW), applied to the household's 15-minute data by season and hour of day. Raw
CSVs are cached in a local `caiso_data/` directory (not committed).

**Results (in `data/carbon_results.json`).** Overnight 00–06 h averages **279 kg CO2/MWh**
vs **125** at solar midday 10–14 h (on-peak 16–21 h: 164) — overnight charging is the
dirtiest window, midday the cleanest. Household import footprint: **5,490 kg CO2/yr**.
Moving the 2,575 mistimed EV kWh to midday saves 153 kg/yr, while moving it overnight would
ADD 239 kg/yr (midday beats overnight by 391 kg/yr). Solar exports avoid ~**1,217 kg/yr**.
Cost cross-check: on the post-May-2026 EV-TOU-5 windows, weekday 10–14 h is super-off-peak
at the SAME price as overnight — the cleaner choice is free on weekdays.

**Output schema.** `source` (endpoints, sample days, method); `intensity_kg_per_mwh`
(`annual_avg_by_hour` [24], `by_season_by_hour` {DJF/MAM/JJA/SON × 24},
`window_means_annual`); `household_inputs`; `footprints_kg_co2_per_yr` (scenarios a/b/c +
`detail` deltas); `solar_exports_avoided_kg_co2_per_yr`; `cost_vs_carbon` (simple reprice
vs the netting-correct $1,179.93 from §3.8 scenario a); `caveats` (4 sample days, not 365;
grid-average not marginal intensity; displacement assumption for exports).

### 3.11 In-session studies: cleaning effect, lifetime payback, and `extra_results.json`

**Panel-cleaning diff-in-diff (`data/cleaning_study_daily.csv`).** The array was
professionally cleaned on **2024-08-12** ($200). Daily generation for windows around that
date was pulled for 2021–2024 via the PVOutput **`getoutput`** API (donor feature; the
public daily list works too). CSV schema: `date` (YYYYMMDD), `generated_kwh` — 60 rows
(30-day pre + post windows) per control year 2021–2023, a wider 100-day window for the
2024 cleaning year (280 data rows); the 2025 control window is already in
`data/pvoutput_daily.csv`. Method: compute the post/pre production ratio across the Aug 12
boundary in the cleaned year and in each uncleaned control year — the controls estimate the
pure seasonal decline (they fall 5–8%); the cleaned year *rose* 5%. Diff-in-diff result:
**+11.8% median cleaning effect** (+10.9% on clear-sky p90 days; peak power +8.6%). The
array logged 0 kWh on the cleaning day itself (panels offline during the wash),
corroborating the date.

**Lifetime payback.** Install invoice: **$37,845 paid Dec 2019** (PTO 2019-12-27). Initially
an in-session computation; now reproduced by the committed `analysis/lifetime_payback.py`
(§3.12 — the "script per headline number" gate, `CLAUDE.md` §9). Cumulative value = each
year's ACTUAL production × a blended $/kWh value under the TOU structure in force that year,
scaled by the utility's rate history (a rate index — NOT today's rates back-cast over
history). The curve crosses $37,845 in **~fall 2025** (gross), or **~early 2024** if the 30%
federal ITC was claimed (net cost $26,492). Current-year value of solar: **$4,992/yr** — a
no-solar counterfactual re-billed on the validated netting model (NBC on gross imports) costs
$9,876/yr vs the modeled $4,884/yr baseline. Caveat: the rate-index scaling of historical
value is approximate; treat the crossover dates as **±10%** (several months either way).

**`data/extra_results.json` keys** (all from in-session computations on the same
15-minute dataset and bill-validated rates):

- `phantom` — baseload decomposed from **44 EV-free quiet nights**: median **1.025 kW**
  (p10 0.785, p90 1.36); `monthly_kw` seasonal profile peaking Sep–Oct (1.37 kW) with a May
  low (0.845); `cycling_std_kw` 0.142 (compressor-like duty cycling present);
  `annual_kwh_at_median` 8,979; `lowest5_daily_import_kwh` — the lowest occupied-day import
  floor is ~10.7–11.7 kWh/day.
- `escalation` — **RETIRED variant** of the battery rate-escalation ladder ($14,500
  installed, the superseded $1,743/yr evening-only base saving from §3.8, 1%/yr capacity
  fade, 5% discount): 3%/yr → 7.8 yr payback / NPV10 +$102; 5% → 7.3 / +$1,373;
  8% → 6.8 / +$3,535; 12% → 6.2 / +$6,973. The **published ladder** (report §13) is
  `data/battery_dispatch_policies.json → escalation_greedy_pw3`, rebased on the §3.13
  price-aware $2,259/yr saving: 3% → 5.9 yr / +$5,020; 5% → 5.7 / +$6,718;
  8% → 5.3 / +$9,608; 12% → 5.0 / +$14,205.
- `price_map` — all-in **import and export $/kWh for all six season × TOU-period cells**
  from bill-validated rates (e.g. `S_on` 0.8681/0.8189, `S_sop` 0.125/0.0757, `W_on`
  0.6053/0.556).
- `nbt` — the same year re-billed under NBT-style flat export credits at 3/5/8¢, NBC charged
  on gross imports in all variants (`note`): `nbt3c` $7,151 / `nbt5c` $6,953 / `nbt8c` $6,655
  vs `nem2_nbc_gross` **$4,884** → `gf_value` [1772, 2268]: **NEM 2.0 grandfathering is
  worth ~$1,772–2,268/yr** at current rates.
- `cleaning` — optimal-cadence model at soiling rates 0.45 / 1.5 / 2.4%/month (the §3.9
  bracket): no-clean season soiling loss **$59 / $195 / $283/yr** at the earlier $0.315/kWh
  blended estimate (see the §3.9 note); best single cleaning ~mid-July (saves $29 / $97 /
  $126); best pair ~Jun 12 + Aug 21 (saves $39 / $129 / $177; the second cleaning's marginal
  value is only $10 / $32 / $51). Since a post-2026-TOU *marginal* midday kWh is worth only
  ~$0.08–0.13 (see `price_map` sop cells), a $200 professional cleaning is break-even at best.
- `trueup` — annual true-up cross-check (charges $1,005.31, credits $492.91, net $512.40).
- `lifetime` — headline outputs of §3.12: `blended_old_tou` 0.4866, `blended_new_tou` 0.3025
  ($/kWh), `solar_value_today` 4992, `nosolar_bill` 9876, `crossover_gross`
  "~Sep–Oct 2025", `crossover_net_itc` "~Mar 2024".

**System-size verification (in-session).** Registration: 30 × Panasonic 335 W modules =
**10.05 kW DC**; 30 × Enphase IQ7X microinverters ≈ **9.45 kW AC**. Measured multi-year
peak powers of 9,204–9,233 W ≈ 97–98% of the AC ceiling — registration and physics agree.

### 3.12 `analysis/lifetime_payback.py` — lifetime solar payback (blended-value method)

**Purpose.** The committed reproduction of the lifetime-payback headline (§3.11): when did
cumulative solar value cross the install invoice? Stdlib-only; prints a year-by-year table
with crossover markers.

**Method (documented in the docstring).**
1. **Blended $/kWh today** = (no-solar counterfactual bill − with-solar bill) ÷ annual
   production, both computed with the §3.6 netting model (`billing_model_nem.bill`) at
   current rates — computed under BOTH TOU structures: the pre-2026 windows (sop
   midnight–6 am, plus 10 am–2 pm in Mar/Apr only) for historical years, and the current
   windows for the present year. The no-solar load series is the hourly whole-home
   consumption (monitoring consumption meter) re-billed as if all imported.
2. **Each historical year's value** = that year's ACTUAL metered production × the
   old-structure blended $/kWh × (that year's utility average residential rate ÷ the current
   average rate) — a published-rate index, never today's rates back-cast.
3. **Crossovers**: cumulative value vs invoice gross, and vs invoice × 0.70 (30% federal ITC
   for 2019 systems).

**Constants at the top of the file** (edit for your system): `INVOICE` 37845.0, `ITC` 0.30,
`PROD` {2020–2026 actual kWh; 2026 partial}, `RATE_IDX` {approx. SDG&E average residential
¢/kWh by year, 32→48}, `BLENDED_OLD` **0.4866** $/kWh (pre-2026 TOU structure at current
rates), `BLENDED_NEW` **0.3025** $/kWh (current TOU structure).

**Results.** Gross crossover **~fall 2025** (~Sep–Oct, 73% through the year); net-of-ITC
crossover **~early 2024** (~Mar). Headline values are mirrored in
`data/extra_results.json → lifetime` (§3.11). Caveat printed with the results: the rate
index is approximate — crossover dates carry roughly ±10% (a few months).

**Run:** `python3 analysis/lifetime_payback.py` (no inputs; constants inline).

---


### 3.12b `rates.py` — canonical rate constants + billing engine

Single source of truth for bill-derived rate constants and the NEM billing mechanics
(energy netted per month/season/period; NBC on gross imports; BSC per day). Imported by
`billing_model_nem.py`, `behavior_rebuild.py`, and `battery_dispatch_policies.py`.
Legacy `analyze.py` retains published rate-table values (differ slightly from billed
values, e.g. UDC on-peak 0.31711 vs 0.30203) — acceptable for cross-plan RANKING, not for
absolute dollars; this is why its $4,861 differs from the canonical $4,884 baseline.

### 3.13 `battery_dispatch_policies.py` — dispatch-policy comparison (published battery basis)

Simulates three dispatch policies per 15-minute interval for both configurations
(13.5 kWh Powerwall 3 and 27 kWh PW3+Expansion, 11.5 kW, 90% RTE): **evening-only**
(discharge 4–9pm; overnight top-up to 60%), **two-window** (+6–9am house load), and
**price-aware** (discharge against every non-super-off-peak import; top-up toward full in
any super-off-peak gap). Rationale: stored energy costs ~8.4¢/kWh (surplus) to ~13.9¢
(grid top-up) while all non-super-off-peak imports price at 51–87¢, so every such import
is worth serving. Ordering matters: solar surplus charges first (10am–2pm is both
super-off-peak and peak solar). EV-spillover intervals (≥2.5 kW outside on-peak) are
excluded from service. Results (`data/battery_dispatch_policies.json`): 1×PW3
$1,729 / $1,980 / $2,259 per year; 27 kWh $2,062 / $2,312 / $2,726; price-aware runs
~0.99 / 0.58 cycles per day. The report's battery economics use the price-aware policy
($2,175/yr post-EV-fix after the measured $130 overlap deduction); the §4 plan matrix
retains the older evening-only interval sim ($1,669) for cross-plan comparison. The
escalation ladder in report §13 is rebased on the $2,259 figure.

## 4. Battery simulation methodology

**Arbitrage dispatch (identical greedy policy in `battery_backup_sims.py`, `package_sims.py`,
`deep_analyses.py`).** State: `soc` (kWh), starts at 0. For each 15-minute interval
(`step = 0.25` h), in order:

1. **Charge from would-be exports first.** If the period is not on-peak, `Generation > 0`, and
   `soc < cap`: charge `min(Generation, power × 0.25, cap − soc)`. This energy is not free — the
   sim books its **forgone export credit** `charge × max(rate − NBC, 0)` as a cost.
2. **Otherwise, overnight super-off-peak top-up to a 60% cap.** If the period is super-off-peak,
   the hour is < 6, and `soc < 0.6 × cap`: charge `min(power × 0.25, 0.6 × cap − soc)` from the
   grid at the full super-off-peak retail rate (booked as **grid-charge cost**). The 60% ceiling
   reserves headroom for the next day's free solar surplus.
3. **Discharge on-peak.** If the period is on-peak, `Consumption > 0`, and `soc > 0`: discharge
   `min(Consumption, power × 0.25, soc × eff)` with `eff = 0.90`; `soc` decreases by
   `discharge / eff` (the 90% round-trip efficiency is applied entirely at discharge); the
   avoided cost `discharge × rate` is booked as **on-peak offset value**.

Net annual savings = offset − forgone credits − grid-charge cost. The battery never exports to
the grid, never discharges off-peak, and rates are EV-TOU-5 + CEA (no relief). Configurations
simulated are listed in §3.3 (arbitrage: six configs from 5 to 27 kWh usable) and §3.4
(packages: PW3 and PW3+Expansion only).

**Backup endurance (Part 2 of `battery_backup_sims.py`) differs in kind.** It is an hourly
outage survival simulation, not a tariff simulation. An outage is started at **18:00 (6 pm) of
every day** in the window. Each run: `soc` starts at **full capacity**; each hour the backed-up
load (capped at inverter power) nets against derived solar production; a deficit drains
`net × 1.05` from the battery (5% inverter loss) and the run ends ("lights out") when the
battery cannot cover the hour; a surplus recharges at 90% efficiency up to capacity. The run is
**capped at 14 days (336 h)**. Reported per config × tier: median and 10th-percentile endurance
hours across all ~365 outage starts.

Two honest caveats, also printed in the report: the **full state-of-charge at outage start is
optimistic** (a battery doing daily arbitrage will often be partially discharged at 6 pm), and
median values equal to 336 h mean "hit the 14-day simulation cap", not literally 14 days —
treat them as "rides out typical outages," not fortnights.

---

## 5. Chart generation (`index.html`)

Chart.js 4.4.3 is loaded from CDN; the five canvases read the inlined `const D = {...}` object.
Mapping of every canvas id → `D` arrays → producing computation:

| Canvas id | Type | `D` arrays (length) | Units | Produced by |
|---|---|---|---|---|
| `hourly` | line, 4 series | `hourlyS_imp`, `hourlyS_exp`, `hourlyW_imp`, `hourlyW_exp` (24 each) | average kW by hour of day, split summer/winter | `report_data.json → hourly_S / hourly_W` (as-run `analyze.py` variant; equals `hourly_profile.csv` values × 4, split by season) |
| `battery` | line, 3 series | `bat_now_S`, `bat_pw3_S`, `bat_pw3x_S` (24 each) | summer average grid-import kW by hour: today, with 1× PW3, with PW3+Expansion | `bat_now_S` is `hourlyS_imp` rounded; the two battery series are the **§3.13 price-aware dispatch** applied to summer intervals and re-averaged by hour — committed as `data/battery_dispatch_policies.json → pw3/pw3x.greedy_profile_S` (on-peak imports fall 3,989 → 883 kWh/yr with PW3, → 414 with the expansion) |
| `monthly` | bar ×2 + line | `mLabels` (13), `mImp`, `mExp` (kWh), `mCost` ($) | calendar months Jul 2025*–Jul 2026* (* = partial) | `mImp`/`mExp` = `monthly.csv` (from `analyze.py`), rounded; `mCost` = `report_data.json → monthly.cost`, the modeled EV-TOU-5+CEA energy cost per month (excludes the daily BSC) |
| `periods` | horizontal bar ×2 | inline literals: kWh `[14811, 4478, 3989]`, $ `[1869, 2284, 2951]` | annual import kWh and gross import cost (imports × all-in rate, before export credits) for super-off-peak / off-peak / on-peak | `report_data.json → period_split` summed across seasons for kWh (sop 6,628+8,183; off 2,238+2,240; on 2,109+1,880); the on-peak $2,951 matches `report_data.json → onpeak.import_cost` |
| `carbon` | line, 1 series | `carb` (24) | CAISO grid CO₂ intensity, kg/MWh, annual average by hour of day | `data/carbon_results.json → intensity_kg_per_mwh.annual_avg_by_hour` (from `carbon_timing.py`, real CAISO Today's Outlook history CSVs) |

Everything else in the report (plan table §3, battery tables §4/§6, package cards §7, deep-dive
figures §9, bill audit §10) is static HTML transcribed from `plan_results.csv`,
`package_results.json`, `battery_sim.json`, `backup_endurance.json`, `deep_results.json`,
`weather_results.json`, and the bill summaries. After any rerun, update both the `D` block and
the prose numbers, then grep the HTML for the superseded figures (`CLAUDE.md` §3).

**Report structure conventions (preserve on regeneration; specs in `CLAUDE.md` §§9–11).**

- **One rate vintage per projection:** the §7 package cards state projected bills **at
  constant 6/1/2026 rates** — LOW ~$3,700/yr (~$309/mo), MID ~$1,500/yr (~$125/mo) vs the
  ~$4,880/yr no-change model baseline — never against the $3,282 actual (billed largely on
  2025 tariffs), which is noted as non-comparable.
- **Confidence labels:** inline pills tag claims as `measured` (meters/bills/multi-source),
  `modeled` (validated model at current rates), or `estimated` (rate index, single cleaning
  event, four sampled CAISO days). §§1–10 are measured/modeled throughout; pills appear in
  §§11–13 where evidence is thinner, and §14 defines the legend.
- **Navigation:** sticky TOC grouped Verdict / Evidence / Audit with scroll-spy (one
  IntersectionObserver); the three heaviest audit sections (§9 deep dives, §12 cleaning,
  §13 carbon/NEM) are native `<details>` blocks, closed by default with one-line conclusion
  teasers; charts inside them lazy-init on first open; back-to-top button; JS-off degradation.
- **Provenance note:** the closing small-print of §14 (and the equivalent README blockquote)
  carries the required "How this report was produced" statement — generation, independent
  review, adversarial review, rework. It must survive every regeneration.

---

## 6. Validation and known limitations

**6.1 Three-way production cross-validation.** Annual solar production agrees across three
independent measurements: the Enphase revenue-grade production CT (16,502 kWh), PVOutput's
microinverter-reported records (16,839 kWh), and a derived series (load − imports + exports)
that touches neither (16,660 kWh) — within ±2%, with 0.9999 daily correlation and near-zero
nighttime residual in the derived series (`data/threeway_production_validation.csv`; report §1).

**6.2 Bill-audit validation.** All 12 detailed electric bills were parsed. The modeled CEA
generation rates matched the billed rates **to the penny** ($0.51684 / $0.15975 / $0.04961
summer on/off/sop). The audit corrected two model assumptions: the CEA relief credit does not
apply (product is "Clean Impact Plus" — hence `analyze_norelief.py` is the published model), and
the climate zone is Coastal (affects only the baseline-credit plans, which lose regardless).

**6.3 Model-vs-actual reconciliation.** The audit initially covered 338 days ($3,004) with a
27-day gap; the October 2025 statement closed it, giving a complete 365-day actual of
**$3,282/yr** (12 statements, 13 billing periods — see `data/electric_bill_summary.csv`,
whose `days` column sums to 365 and whose delivery+generation columns sum to $3,282.22).
The interval model said
$4,861; proper monthly per-TOU-period netting with NBC on gross imports
(`billing_model_nem.py`) gives **$4,884** — i.e. the **netting method itself agrees to
~0.5%** and is not the source of the gap. The remaining gap is
mostly that the model prices the *entire* year at current **6/1/2026 rates**, while the actual
bills were mostly rendered on cheaper 2025 tariffs (rates rose through the period, most in
summer, exactly where the model runs high). The model is therefore a forward-looking "at
today's rates" estimate and reads high against history. Consequence adopted throughout:
absolute dollars are anchored to actual bills; the model is trusted for **rankings and deltas**
(savings), which are driven by on-peak arbitrage priced identically in every variant.

**6.4 The 2.5 kW behavior cap is partly aspirational.** `behavior_adjust()` moves *all* import
energy above 2.5 kW in the on-peak and 6–9 am windows, but only ~931 kWh of on-peak session
energy is identifiably the EV (§3.5); the rest includes house loads (HVAC, cooking) that may not
be movable. The report therefore brackets the behavior saving (~$300–800/yr reliable EV-timing
floor, ~$1,330/yr stretch) rather than promising the full modeled figure.

**6.5 Holiday handling is inconsistent by design debt.** `analyze.py`/`analyze_norelief.py`
treat seven holidays as weekends for TOU assignment; `battery_backup_sims.py`,
`package_sims.py`, `deep_analyses.py`, and `billing_model_nem.py` do not. Seven days × the
sop/off rate difference is dollar-negligible, but expect tiny discrepancies if you diff outputs.

**6.6 Other limitations** (from report §14): rate tables go stale on SDG&E (Jan/Jun) and CEA
(Feb/Jun) revision cycles; TOU-DR-P event surcharges are only modeled in the §3.5 wildcard;
battery installed prices are estimates; the simple 6.2-yr price-aware battery-alone payback in
`package_results.json` (8.4 yr evening-only) uses no discounting or escalation (the Monte
Carlo in §3.5 and the published escalation ladder in `battery_dispatch_policies.json` §3.13
handle both); the endurance sims' full-SOC
and 14-day-cap assumptions (§4); `deep_analyses.py` hard-codes `base_save=1347` from a prior
`package_sims.py` run — rerun order matters (§7).

---

## 7. Reproduction checklist

1. **Gather inputs** per `DATA-SOURCES-CHEATSHEET.md`: Green Button 15-minute electric CSV
   (13 months), two calendar years of hourly whole-home consumption (Enphase SAM 8760 or your
   monitoring platform's equivalent), daily production export, ~12 detailed electric (and gas)
   bill PDFs, gas Green Button daily CSV, and your tariff PDFs (SDG&E Total Rates Tables +
   CCA schedule, current revision).
2. **Place raw files in `private/1-raw-data/`** and verify `.gitignore` covers them
   (`git status --ignored`). Nothing in `private/` is ever committed.
3. **Update the constants** at the top of each script: the `CSV` path (`analyze*.py`) or copy
   your files to `usage.csv` / `samA.csv` / `samB.csv` in the working directory; the window
   anchor `end = dt.datetime(...)`; the UDC/EECC/CEA rate tables, `PCIA` (your vintage),
   `NBC`/`WFNBC_DWR`, `BSC`, baseline allowances for *your* climate zone, and whether any CCA
   credit applies (check your detailed bill, not the CCA marketing page). Battery configs and
   hardware prices in `battery_backup_sims.py`/`package_sims.py` as quoted to you.
4. **Run the scripts in dependency order** (Python 3 with `pandas` and `numpy`):
   1. `analyze_norelief.py` (and/or `analyze.py` if your CCA credit applies) → plan table,
      profiles;
   2. `battery_backup_sims.py` → arbitrage + endurance;
   3. `package_sims.py` → matrix + packages; note `battery_marginal_after_behavior`;
   4. edit `base_save` in `deep_analyses.py` to that marginal value, then run it;
   5. `billing_model_nem.py` → compare its output to your actual bills before quoting any
      absolute dollar figure (per `CLAUDE.md` §1; expect it to read high if it prices history
      at current rates). Verify against a bill line that non-bypassable charges are billed on
      GROSS imported kWh, and that the code does the same (`CLAUDE.md` §9);
   6. `behavior_rebuild.py` → session-based shift ladder + the behavior/battery overlap;
   7. `battery_dispatch_policies.py` → the three-policy comparison whose **price-aware**
      results are the published battery basis and escalation ladder (§3.13);
   8. `lifetime_payback.py` (if you have solar and the install invoice) → update its
      `INVOICE`/`PROD`/`RATE_IDX`/blended constants first (§3.12).
5. **Rebuild the derived artifacts** (§3.7): daily-production cross-validation, weather
   regression, bill-summary CSVs — or skip them for a plan/battery-only analysis.
6. **Refresh `index.html`** — when regenerating from scratch, start from
   `report-template.html` (the de-personalized skeleton; see `CLAUDE.md` §10 and
   `reusable-prompt.md` Phase D) and fill its `{{TOKEN}}` placeholders; when updating in
   place, replace every array in the `D = {...}` block from the new
   `report_data.json`/`monthly.csv`/dispatch output, then update the static tables and prose
   figures; grep the HTML for each superseded number.
7. **Before committing anything**, run the PII grep required by `CLAUDE.md` §4 over every
   push-bound file (names, addresses, account/meter numbers, coordinates, system IDs, API
   keys) and confirm zero matches.
