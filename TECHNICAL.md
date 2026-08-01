# TECHNICAL.md — Methods and Reproduction Reference

This document specifies, at reproduction-grade detail, every script in `analysis/`, the exact
schema of every input and output file, and the provenance of every chart in `index.html`. It is
written for someone who wants to run the same analysis on their own home. It deliberately does
not repeat the narrative context in `README.md` (what the project is), `DATA-SOURCES-CHEATSHEET.md`
(where to download each input), or `CLAUDE.md` (operating rules and lessons learned) — read those
first; this file is the methods section.

Subject system, stated once: a single-family home in the SDG&E Coastal climate zone with a
10.05 kW DC rooftop array (30 microinverters, ~9.45 kW AC), two EVs (all-electric transportation), NEM 2.0, and Clean Energy
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
│ analyze_norelief.py        │ plan_results.csv, hourly_profile.csv,        │
│ (analyze.py = relief       │ monthly.csv, stats.json (gitignored);        │
│  variant, *_relief files)  │ report_data.json: see report_data.py         │
│ battery_backup_sims.py     │ battery_sim.json, backup_endurance.json      │
│ deep_analyses.py           │ deep_results.json                           │
│ billing_model_nem.py       │ (stdout: bill-validated annual baseline)     │
│ lifetime_payback.py        │ (stdout: cumulative-value table, crossovers) │
│ behavior_rebuild.py        │ behavior_rebuild.json                        │
│ battery_dispatch_policies.py│ battery_dispatch_policies.json              │
│ battery_plan_matrix.py     │ battery_plan_matrix.json (§4 matrix)         │
│ soiling_analysis.py        │ soiling_results.json                         │
│ carbon_timing.py RETIRED   │ carbon_results.json (now SOURCE data)        │
│ carbon_fullyear.py         │ carbon_fullyear_results.json,                │
│                            │ caiso_hourly_intensity.csv                   │
│ extended_findings.py       │ extended_results.json                        │
│ package_results.py         │ package_results.json (recomposition)         │
│ in-session steps (§3.7/§3.11)│ extra_results.json,                        │
│                            │ cleaning_study_daily.csv,                    │
│                            │ weather_results.json,                        │
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
   against the 12 actual bills; the two methods agree to ~0.5% on this dataset ($4,882 vs
   $4,904 — §6). Plan *rankings* and battery/behavior *deltas* come from the interval models;
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
arguments; input paths are constants at the top of each file (`analyze*.py` read
`CSV = "usage.csv"` from the working directory like the rest). The scripts expect files in
the working directory under fixed names:

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

**Holidays.** Eight tariff holidays take weekend TOU windows (New Year's, Presidents' Day =
3rd Mon Feb, Memorial Day = last Mon May, July 4, Labor Day = 1st Mon Sep, Veterans Day,
Thanksgiving = 4th Thu Nov, Christmas), each confirmed individually against the bills by
`analysis/tou_audit.py`. The rule lives in `rates.holidays()`/`rates.off_peak_day()` and every
pipeline script gets it through `rates.period_at` — the historical per-script inconsistency
(§6.5) is closed. Weekend-falling holidays carry the documented observed-day rule (SDG&E
holiday-TOU page, read 2026-07-29): a Sunday holiday is also observed the following Monday, a
Saturday holiday does not shift. That rule is documentation-sourced, not yet bill-confirmed —
no weekend holiday sits in the audited corpus, and its first effective date (2027-07-05) is
outside every current analysis window, so it changes no committed artifact. Adjudication is
executable: `tou_audit.weekend_shift_evidence()` scores the no-shift / observed-Friday /
observed-Monday variants against each statement's printed buckets whenever a weekend holiday
enters an audited period, with an identifiability bound of 0.5 kWh per moved bucket
(sub-rounding separations report `indeterminate`, untested dates are listed, and a decisive
contradiction of the documented rule fails the audit verdict). `tou_audit`'s own `as_billed`
baseline keeps actual dates only, since it reproduces what the statements demonstrate.

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
partial), and `stats.json` (gitignored run product; `data/report_data.json` is produced by `analysis/report_data.py`, §3.7).

**Run:** from the private/verify sandbox (usage.csv present), `python3 analyze_norelief.py` — the live variant that reproduces the committed artifacts. `analyze.py` is the superseded relief-credit variant; it writes gitignored `*_relief` outputs for comparison only.

### 3.2 `analysis/analyze_norelief.py` — the published variant (no relief credit)

Byte-for-byte the same model as `analyze.py` with one change: the CEA runs call
`bill(p, "CEA", relief=False)`, i.e. the −$0.03871/kWh relief credit is **not** applied. The
bill audit later confirmed this is the correct model for this account (the bills show CEA
product "Clean Impact Plus" with only a +$0.001/kWh adder and no relief line), so **the
committed `data/plan_results.csv` is this script's output** — e.g. EV-TOU-5/CEA energy
$4,592.12 + fixed $289.60 = total $4,881.73, matching the report's $4,882. Keep `analyze.py` if
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
`equiv_full_cycles` (Σ discharge ÷ capacity). Example: 1× PW3 → offset $2,262, forgone $381,
grid $201, net $1,680/yr, 229 cycles.

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

### 3.4 `analysis/package_sims.py` — RETIRED, kept as historical record

> **This script is no longer in the repository** (deleted 2026-07-24, commit `15f14bb`). Its
> plan × battery matrix is superseded by `battery_plan_matrix.py` (§3.16) and its package
> figures by the integrated pipeline (§3.13). The section is kept because two things still
> trace to it: the legacy matrix quoted below as historical record, and the `$1,347/yr`
> Monte Carlo base still carried as a fixed constant in `deep_analyses.py` (§3.9) — the
> report labels that figure's provenance in §13. Its behavior method — trimming intervals to
> a 2.5 kW cap and re-billing the trimmed energy at an averaged super-off-peak rate — is the
> year-end lump-sum shortcut `CLAUDE.md` §1b now forbids; `behavior_rebuild.py` (§3.12)
> replaced it with physical energy movement. Do not reuse the method described here.

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
  (e.g. EV-TOU-5: $4,861 → $3,192, battery worth $1,680/yr). **Superseded:** report §4 now
  publishes the regenerable matrix from `battery_plan_matrix.py` (§3.16 — the price-aware
  dispatch billed under each top-3 plan's table rates); this legacy evening-only matrix is
  historical record only. The published battery economics remain the §3.13
  integrated-pipeline figures.
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
- `packages.MID`: `cost` 14500, `savings_yr` **3438**, `battery_alone_yr` **2325**
  (price-aware; `battery_alone_post_ev_fix_yr` **2245**), `battery_alone_payback_yr` **6.2**
  (`battery_alone_payback_evening_only_yr` 8.5),
  `projected_bill_current_rates_yr` **1445**, `note` (single integrated shift-then-battery run);
- `packages.HIGH`: `cost` 20400, `marginal_vs_mid_yr` **216** post-behavior (~27-yr marginal payback on the
  $5,900 expansion — buys outage endurance, not savings);
- `superseded` — records that dividing hardware cost by combined behavior+battery savings is
  invalid; battery-alone payback is the honest hardware metric.

**Run (historical):** the generating script was removed; regenerate package figures with the integrated pipeline (`behavior_rebuild.py` + `battery_dispatch_policies.py`) instead (script output only; rebuild the
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
   marginal savings read from `data/battery_dispatch_policies.json`
   (`post_behavior.mid.battery_marginal`, currently **$2,238** — the integrated
   shift-then-battery marginal; the input was previously a hardcoded $1,347 carried from the
   retired `package_sims.py` and went stale across two pipeline reruns, which is why it now
   reads the artifact); 25-year horizon; payback linearly interpolated; NPV over 10 years at
   4% discount. Results: median payback 6.0 yr (p10 5.3, p90 7.0), 100% probability of
   payback within a 10-year warranty, median 10-yr NPV +$6,186. This distribution brackets
   the §3.13 price-aware basis (~6.2–6.5 yr simple payback at $2,238–2,329/yr).

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

**Output.** Prints the modeled annual baseline **$4,904** at 6/1/2026 rates (the retired
netted-NBC variant gave $4,675) against the actual billed $3,282 (365-day audit) — the
reconciliation of that gap is §6.3. Adapt `bill()` (it accepts arbitrary import/export column
names) to re-score behavior or battery scenarios on the validated netting.

**Run:** `python3 billing_model_nem.py` next to `usage.csv`.

### 3.7 In-session artifacts (no committed generator script)

Several `data/` files were produced by short ad-hoc steps during the analysis session rather
than by a committed script; they are documented here so they can be regenerated:

- **`report_data.json`** — **now produced by `analysis/report_data.py`** (2026-07-27). It
  was previously the output of an uncommitted as-run variant of `analyze.py`, which is why
  it went stale through every refresh while the prose around it moved; `analysis/
  test_report_consistency.py` now fails if the report's chart arrays drift from it.
  **Basis change at the same time:** every series is computed from the canonical module
  (`rates.allin`/`energy`/`credit`, day types from `rates.off_peak_day`) instead of the
  legacy published rate tables. kWh is unaffected — the old and new generators agree
  exactly on imports, exports and the hour-of-day profiles — but the money moves, because
  the charts had been drawn on table rates while every dollar in the prose was bill-derived.
  On the same input the annual netted energy cost reads $4,093 canonical against $4,559
  legacy, and gross on-peak import cost $2,969 against $2,951 — both bases run on the superseded pre-correction export (private/1-raw-data/superseded/), the last input both generators existed for; the current artifact reads $4,124 / $2,998 on the corrected input. Contents: all-in EV-TOU-5
  rates per season/period (`rates_ev5`); seasonal 24-hour average import/export profiles
  (`hourly_S`, `hourly_W`); monthly labels/imports/exports/netted cost; the `periods_chart`
  block the §5 chart is drawn from; per-(season, period)
  import/export/cost split (`period_split`); on-peak summary (4,022 kWh imported, $2,998 gross
  cost, 41.7% of energy cost); annual totals (imports 23,376, exports 9,964 kWh). The old
  `ev`, `battery` and `shift_*` keys are gone: behavior_rebuild.json and
  battery_dispatch_policies.json model those properly, and report_data.py dropped the
  duplicates deliberately.
- **`threeway_production_validation.csv`** — date-indexed daily `pvoutput` vs `enphase_meter`
  production (the third series, derived production = load − imports + exports from §3.3, is
  computed in-script and summarized in the report: 16,660 kWh vs 16,839 and 16,502).
- **`weather_results.json`** — OLS regression of daily non-EV load on Open-Meteo degree-days:
  base 42.2 kWh/day, +3.0 kWh per CDD65, −1.23 per HDD60, R² 0.45, 1,738 kWh/yr cooling,
  pre-cool shift value $233/yr, setpoint value $104/yr.
- **`electric_bill_summary.csv` / `gas_bill_summary.csv`** — regenerated by `analysis/parse_bills.py` (§9 of this file); originally in-session pdfplumber extractions (§2.7).
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
0.25 h, capped at the interval's actual import. Detected: 563 sessions, 13,806 kWh/yr
(vs ~13,100 expected), of which 911 kWh on-peak, 1,708 off-peak, 11,188 already
super-off-peak.

**Scenario ladder (energy conserved, not lump-summed).** Shifted kWh are removed from their
source intervals and poured into super-off-peak intervals starting at the next midnight,
honoring the 11.5 kW charger cap net of EV charging already present in the destination.
(a) EV-only, 100% compliance: 2,618 kWh moved, **$1,221/yr** saved; (b) EV-only, 80%
compliance (seeded RNG): $1,009; (c) + 25% of remaining on-peak house load: $1,700;
(d) stretch, + 50%: $2,179. (Canonical engine, NBC on gross imports — the script imports
`rates.py`.) A 13.5 kWh / 11.5 kW / 90%-RTE battery re-simulated on top of
(a): $1,887/yr marginal on the baseline → **$1,753/yr after behavior** ($135/yr of
double-counting avoided). NOTE: these battery figures are the **evening-only dispatch
variant, retired as the published basis**. The published battery economics come from the
integrated pipeline in `battery_dispatch_policies.py` (§3.13), which runs the EV shift
first and then the price-aware battery on the shifted load, re-billing end-to-end
($2,329/yr baseline marginal; $2,238/yr post-behavior marginal) — no overlap subtraction
is involved anywhere. This script's evening-only overlap figures remain only as a
workpaper illustration of *why* behavior and hardware must be simulated in one pipeline.

**Output `data/behavior_rebuild.json`.** Keys: `window`; `baseline` (`model_bill` $4,904.13
— regenerated on the canonical NBC-on-gross engine, matching §3.6's **$4,904**; earlier netted-NBC builds carried $4,675.20 (
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
   (this year's evidence) 217 kWh ≈ $68/yr; scenario B (2024-cleaning evidence) 1,106 kWh ≈
   $348/yr, both at the script's $0.315/kWh blended value (an earlier blended estimate,
   retained in the committed artifact; `lifetime_payback.py` §3.12 later refined the
   current-TOU blended value to $0.3009/kWh — immaterial to the order-of-magnitude verdict).

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

> **`carbon_timing.py` is RETIRED (2026-07-27)** and cannot run: the raw CAISO day
> files it read from `caiso_data/` were never committed. It is kept for provenance,
> because it documents where the four sample days came from and how their numbers
> were derived. **`data/carbon_results.json` must not be deleted with it** — that file
> is not a derived artifact awaiting regeneration but the preserved record of those
> four days, and `carbon_fullyear.py` still reads it (`build_covered_from_raw`) to
> reconstruct them. Retiring the pair together, which is the obvious-looking cleanup,
> would break the live carbon pipeline. Use `carbon_fullyear.py` for grid-carbon work.

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
vs the netting-correct §3.8 scenario-a saving — the artifact's stored $1,179.93 is the
scenario-a value as of that run; the current committed `behavior_rebuild.json` says
$1,220.85); `caveats` (4 sample days, not 365;
grid-average not marginal intensity; displacement assumption for exports).

**Status.** Superseded as the report's §13 carbon basis by the 364/365-day full year in
`carbon_fullyear.py` (§3.15); this 4-day study remains the workpaper and supplies the four
legacy seasonal days that `carbon_fullyear.py` reconstructs from `carbon_results.json`.

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
**+11.8% median cleaning effect** (+10.9% on clear-sky p90 days; peak power +8.6% —
daily peak-power records in `data/cleaning_study_peaks_2024.csv`, columns `date,peak_w`).
The array logged 0 kWh on the cleaning day itself (panels offline during the wash),
corroborating the date.

**Lifetime payback.** Install invoice: **$37,845 paid Dec 2019** (PTO 2019-12-27). Initially
an in-session computation; now reproduced by the committed `analysis/lifetime_payback.py`
(§3.12 — the "script per headline number" gate, `CLAUDE.md` §9). Cumulative value = each
year's ACTUAL production × a blended $/kWh value under the TOU structure in force that year,
scaled by the utility's rate history (a rate index — NOT today's rates back-cast over
history). The curve crosses $37,845 in **~fall 2025** (gross), or **~early 2024** if the 30%
federal ITC was claimed (net cost $26,492). Current-year value of solar: **$4,949/yr** — a
no-solar counterfactual re-billed on the validated netting model (NBC on gross imports) costs
$9,853/yr vs the modeled $4,904/yr baseline. Caveat: the rate-index scaling of historical
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
  `data/battery_dispatch_policies.json → escalation_greedy_pw3_post_behavior`, rebased on the §3.13
  post-behavior $2,238/yr marginal: 3% → 6.2 yr / +$4,249; 5% → 5.9 / +$5,880;
  8% → 5.5 / +$8,656; 12% → 5.2 / +$13,072.
- `price_map` — all-in **import and export $/kWh for all six season × TOU-period cells**
  from bill-validated rates (e.g. `S_on` 0.8681/0.8189, `S_sop` 0.125/0.0757, `W_on`
  0.6053/0.556).
- `nbt` — the same year re-billed under NBT-style flat export credits at 3/5/8¢, NBC charged
  on gross imports in all variants (`note`): `nbt3c` $7,151 / `nbt5c` $6,953 / `nbt8c` $6,655
  vs `nem2_nbc_gross` **$4,904** → `gf_value` [1772, 2268]: **NEM 2.0 grandfathering is
  worth ~$1,772–2,268/yr** at current rates.
- `cleaning` — optimal-cadence model at soiling rates 0.45 / 1.5 / 2.4%/month (the §3.9
  bracket): no-clean season soiling loss **$59 / $195 / $283/yr** at the earlier $0.315/kWh
  blended estimate (see the §3.9 note); best single cleaning ~mid-July (saves $29 / $97 /
  $126); best pair ~Jun 12 + Aug 21 (saves $39 / $129 / $177; the second cleaning's marginal
  value is only $10 / $32 / $51). Since a post-2026-TOU *marginal* midday kWh is worth only
  ~$0.08–0.13 (see `price_map` sop cells), a $200 professional cleaning is break-even at best.
- `trueup` — annual true-up cross-check (charges $1,005.31, credits $492.91, net $512.40).
- (the former `lifetime` key moved to its own script-owned artifact,
  `data/lifetime_payback.json`, written by `analysis/lifetime_payback.py` on every derived
  run and byte-diffed by the §9 gate — the hand-recorded copy here had drifted.)

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

**Where its inputs come from.** The per-house facts are read from `private/household.yaml`
through `analysis/household.py`, which fails closed when the file or a required key is
missing: `INVOICE` ← `solar.install_invoice_usd` (the figure published as `invoice_usd` in
`data/lifetime_payback.json` — `public-ok`, §11), `PAID` ← `solar.install_paid_date` (printed
in the run header, nothing computes from it), `PTO` ← `household.pto_date`. None of the three
is a literal in the script.

**Constants at the top of the file** (edit for your system): `ITC` 0.30 (a public tariff
rate, not a household fact), `PROD` {2020–2026 actual kWh; 2026 partial}, `RATE_IDX` {approx.
SDG&E average residential ¢/kWh by year, 32→48}, `FALLBACK_OLD` **0.4869** $/kWh (pre-2026
TOU structure at current rates), `FALLBACK_NEW` **0.3009** $/kWh (current TOU structure) —
fallbacks only; when the raw inputs are present both blended values and the production
denominator are derived from the data and written to `data/lifetime_payback.json`.

**Results.** Gross crossover **~fall 2025** (~Sep–Oct, 73% through the year); net-of-ITC
crossover **~early 2024** (~Mar). Headline values live in
`data/lifetime_payback.json` (script-owned, §9-gated). Caveat printed with the results: the rate
index is approximate — crossover dates carry roughly ±10% (a few months).

**Run:** `python3 analysis/lifetime_payback.py` (no data files needed; it reads
`private/household.yaml` for the three per-house facts and carries the rest as constants).

---


### 3.12b `rates.py` — canonical rate constants + billing engine

Single source of truth for bill-derived rate constants and the NEM billing mechanics
(energy netted per month/season/period; NBC on gross imports; BSC per day). Imported by
`billing_model_nem.py`, `behavior_rebuild.py`, and `battery_dispatch_policies.py`.
Legacy `analyze.py` retains published rate-table values (differ slightly from billed
values, e.g. UDC on-peak 0.31711 vs 0.30203) — acceptable for cross-plan RANKING, not for
absolute dollars; this is why its $4,861 differs from the canonical $4,904 baseline.

### 3.12c EV-fleet validation data (in `extra_results.json → ev_fleet` + `wall_charger_daily.csv`)

The session detector's EV attribution is meter-derived and cross-checked by vehicle and
charger telemetry (two energy cross-checks plus an odometer scale sanity-check — not three
independent energy measurements):

1. **Tesla app Charge Stats** (trailing 12 months, Aug 2025–Jul 2026, battery-side kWh,
   captured 2026-07-24): combined home 12,828 kWh across 612 sessions, supercharging
   1,300 kWh (9% of total), rated miles added 41,221. Against the detector's 13,806 kWh
   wall-side, the implied wall-to-battery gap is 6.5% — plausibly (not provably) charging
   loss, since typical AC loss is 8–12% and the app window and meter year overlap heavily
   but are not identical. Blended app peak share ~5.8% vs detector on-peak 6.4%.
2. **Tesla Wall Connector daily export** (`data/wall_charger_daily.csv`: columns `date,kwh`,
   wall-side, Jul 1–24 2026): a short clean window — 20 July days (Jul 1–20) — charger
   708.9 vs detector 706.1 kWh: 99.6% aggregate agreement, r = 0.985 daily; individual
   days scatter more (mean |diff| 2.4 kWh/day, ~7% of a typical charging day), so the
   agreement is at the totals level. The export's final rows show the Wall Connector's
   batched-upload lag (excluded from the clean window; full aligned period agrees to 94.6%).
3. **Owner odometers** (Model 3 LR AWD, Model Y LR AWD; ~34,000 driven mi/yr lifetime
   average): a scale sanity-check only. With the app's energy totals this yields ~420 Wh/mi
   battery-side effective consumption (~83% of rated) — consistent with real-world AWD
   consumption rather than detector over-attribution.
The `ev_fleet` JSON block records all three plus per-car TOU splits and the household's
zero-ICE status.

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
$1,720 / $1,955 / $2,329 per year; 27 kWh $2,067 / $2,298 / $2,795; price-aware runs
~1.01 / 0.60 cycles per day. The report's battery economics use the price-aware policy
($2,238/yr post-EV-fix from the integrated shift-then-battery run); the §4 plan matrix is
regenerated by `battery_plan_matrix.py` (§3.16). The
escalation ladder in report §13 is seeded from the post-behavior $2,238 marginal. In the 6.3% of
intervals carrying both import and export, discharge-window imports are served rather than
banking low-value surplus. The artifact also carries an **`inputs` block** — the report §6
sentence's serviceable-load inputs on the canonical period assignment: non-super-off-peak
import **8,521 kWh**, on-peak **4,022**, servable off-peak house load (< 2.5 kW) **1,950**,
serviceable total **5,972**. Reconciliation: the §5 periods chart's non-SOP total reads
**8,520 kWh** (from `report_data.json`, off-peak + on-peak) against **8,521** here. Both
now use the canonical period assignment including the eight bill-confirmed tariff holidays,
so the ~40 kWh convention gap that used to separate them is closed and the 1 kWh residual is
independent rounding of the same quantity. `analysis/test_report_consistency.py` asserts the
two stay within 1 kWh of each other.

### 3.14 `analysis/extended_findings.py` — extended findings batch (`data/extended_results.json`)

One script computes the report's extended findings (§6 VPP/resilience framing, §9 dividend
workups, §10 AB 205 + gas HDD decomposition, §13 2039 strategy, battery-payback tornado).
It is built **fail-closed**: it computes, validates, and only then publishes — a partial or
failed run changes nothing on disk.

**Inputs.** `usage.csv` (the Green Button export) beside the script, the two SAM-8760 files
and the gas Green Button CSV under `private/1-raw-data/`, `data/weather_daily_tmean.csv`
(HDD base 65°F), the committed `data/battery_dispatch_policies.json` (as the
cross-artifact assertion target), and the imported modules `behavior_rebuild.py`,
`battery_dispatch_policies.py`, and `rates.py` — plus **cited external constants** recorded
with sources in `research/extended-research-notes.md`: EIA California gasoline 12-month mean
$4.65/gal, FHWA Highway Statistics VM-1 on-road fleet economy 23.4 mpg, supercharger price
estimate $0.45/kWh (labeled estimate), DSGS/Tesla VPP program terms ($150–350/season),
SDG&E 2024 reliability report SAIDI figures, CPUC D.24-05-028 / Resolution E-5355 (BSC
$24.15/mo = $0.79343/day, matching `rates.py` exactly).
All repo paths (`data/`, `private/1-raw-data/`) resolve against the repo root, found by
walking up from the CWD (then from the script's location), so the documented
`private/verify` copy-and-run sandbox needs no path edits.

**Fail-closed design (the load-bearing part):**

- **Battery figures are computed, never hard-coded.** The script re-runs the dispatch
  engine itself (`bp.run_batt`/`bp.billed` from the imported `battery_dispatch_policies`
  module) for all three PW3 policies, plus the integrated post-behavior case (EV shift via
  `behavior_rebuild.shift_ev` first, then the price-aware battery, re-billed end-to-end),
  and **asserts every result within ±$1.50 of the committed
  `battery_dispatch_policies.json`** (`pw3.{evening,twowin,greedy}.save` and
  `post_behavior.mid.battery_marginal`). A mismatch aborts with "regenerate
  battery_dispatch_policies.json first" — the tornado and NBT sections are then built only
  from those computed values.
- **Input validation fails closed:** empty/truncated SAM-8760 files, a gas/weather merge
  under 300 days, or a non-physical gas floor/heating split each abort the run.
- **Publication gate + atomic write:** before writing, all nine required output sections
  must exist (`ab205`, `electrification_dividend`, `away_days`, `supercharge_delta`,
  `weekend_sop`, `representative_year`, `gas_decomposition`, `nbt_2039`,
  `tornado_battery`), the dividend must be positive, `rates.py`'s BSC must equal the
  adopted $24.15/mo fixed charge, and each NBT battery marginal must be within sanity
  bounds of the NEM 2.0 marginal. The JSON is then written to a temp file and `os.replace`d
  into `data/extended_results.json` — never a partial artifact. The `CLAUDE.md` Commands
  regeneration gate re-runs the script and requires `git diff --exit-code` on the
  committed artifact.

**Outputs**
(`data/extended_results.json` keys): `ab205`, `electrification_dividend`, `away_days`,
`supercharge_delta`, `weekend_sop` (CLAUDE.md §1b compliant: weekend non-EV import in
hours ≥14 is **physically moved** into the same day's 0–14 super-off-peak intervals,
spread uniformly, and both the baseline and the shifted year are re-billed with
`rates.bill_nem` — half-shift $387/yr, full shift $772/yr; never kWh × rate-delta),
`representative_year`, `gas_decomposition` (364-day
HDD regression: floor 0.376 therms/day → 137 therms/yr; slope 0.1812 therms/HDD → 206
therms/yr), `nbt_2039` (price-aware battery marginal $2,506–2,540/yr under 3–8¢ flat
exports vs $2,329 under NEM 2.0), and `tornado_battery` (payback swings: dispatch 2.2 yr >
install quote 2.1 > escalation 0.9 > DSGS 0.8 > EV-fix interaction 0.3 around the 6.2-yr
base). Figures derived purely from external program terms (DSGS dollars, outage-hour
exposure) carry **estimated** pills in the report; artifact-derived ones carry
**modeled**/**measured** per source. The report's "What to do Monday" appendix is
**content-only** — it cites §5/§6/§9/§13 figures and introduces no new artifacts.

### 3.15 `analysis/carbon_fullyear.py` — full-year CAISO carbon sampling (`data/carbon_fullyear_results.json`)

Upgrades the §13 carbon workup from 4 seasonal days (and this issue's own prior 28-day
sample) to the **full analysis year**: all 365 days from 2025-07-24 to 2026-07-23 were
fetched individually from CAISO's public per-day history endpoints —
`https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv` (5-min CO₂ by source, mT/h) and
`.../demand.csv` (5-min CAISO demand, MW) — by direct HTTP fetch; no proxy or allowlist
barrier was encountered fetching a full year in this environment. 364 of the 365 requests
came back usable and are cached individually in `private/1-raw-data/caiso_raw/`
(gitignored). Per covered day: hourly kg CO₂/MWh = 1000 × mean(total CO₂ mT/h, all sources
incl. imports) ÷ mean(CAISO demand MW). The one uncovered day is interpolated with that
month's hour-of-day mean of the covered days, then the per-date-per-hour intensity table
(`data/caiso_hourly_intensity.csv`, committed) is applied to the household's 15-minute
import/export data by date and hour.

**The one gap is a finding, not a fetch failure.** 2026-03-08 (the spring-forward DST date)
carries a row labeled 02:00 in CAISO's own raw CO₂ and demand files, but every 5-minute
value in that row is genuinely blank in both files — confirmed by inspecting the raw files
directly, not assumed. The script's existing all-24-hours validity check correctly refuses
to trust a day it cannot fully verify, so it drops the whole date rather than accepting
twenty-three good hours beside one bad one, and 2026-03-08 falls back to March's month-hour
mean like any other uncovered day. The fall-back date, 2025-11-02, needed no such fallback:
CAISO publishes a flat 24-hour grid for it, so the household's two real 1am quarter-hour
blocks both match against CAISO's single reported 1am value — an approximation, stated as
one, not a second gap.

**Intensity-source resolution order (fail-closed, atomic).** The script resolves its
intensity input in this order: (1) the raw per-day CAISO cache
`private/1-raw-data/caiso_raw/` (gitignored local archive; the 4 legacy seasonal days are
reconstructed from `carbon_results.json` as before); (2) if the raw cache is absent, the
**committed `data/caiso_hourly_intensity.csv`** — which holds every covered day × 24 h and
is sufficient to recompute `carbon_fullyear_results.json` **byte-identically** (covered-day
arrays are canonicalized to the CSV's 0.1 kg/MWh resolution in both modes, so the two paths
produce the same artifacts). **Fail-closed:** if the available coverage (either source) has
fewer covered days than the committed results artifact records, the script aborts rather
than silently rebuilding a degraded artifact; a truncated/empty aggregate CSV also aborts.
**Atomic dual-write:** all outputs are validated first, then the CSV and JSON are written
to temp files and `os.replace`d together — a failed run changes nothing on disk. All paths
resolve against the repo root (found by walking up from the CWD, then from the script), so
the `private/verify` sandbox pattern needs no path edits.

**One coverage number, not two.** `COVERAGE_MIN = 350` (of 365) is now the single constant
that both gates the run (fewer than 350 covered days is a hard abort, even on a first-ever
run with no prior artifact to regress against — the missing dates are named individually,
never just a count) and decides the `measured` label (≥350 covered days). Earlier drafts of
this script carried two different constants for those two decisions (a soft 300-day label
threshold, and before that no hard floor at all) with no stated reason for the gap; there is
now exactly one number, and it means both things at once. At 364/365 covered, this run
clears it by 14 days and is labeled `measured`.

**Headline outputs** (`data/carbon_fullyear_results.json`): import footprint 5,402.4 kg/yr;
export displacement 915.1 kg/yr; mistimed-EV shift +232.8 kg/yr (to overnight) vs −182.8
(to midday), gap 415.6 kg/yr; window means 270.1 / 109.1 / 158.4 kg/MWh (overnight 00–06 /
midday 10–14 / on-peak 16–21). Grid-average (not marginal) intensity; the dollar side of EV
retiming is unchanged (both destination windows are super-off-peak on EV-TOU-5).

**AC3 reproduction check (`test_carbon_fullyear.py::case_ac3_28day_reproduction_within_2pct`).**
Before the 28-day intensity source was retired in favor of the full 365-day raw cache, the
new source had to be shown to agree with the old one — CLAUDE.md's evidence-based principle
(0) demands the measured gap, not an asserted boolean. For each of the 28 dates the old,
committed 28-day `data/caiso_hourly_intensity.csv` covered, the test recomputes hourly
kg/MWh from the fresh 365-day raw cache and compares hour-by-hour against the old snapshot
(embedded in the test so the check survives the file's shape changing to 365 days). Result:
overall mean absolute relative difference **0.039%**, worst date 2026-05-08 at **0.137%** —
both far inside the test's 2% tolerance, confirming the new source before the old one was
retired.

### 3.16 `analysis/battery_plan_matrix.py` — battery × plan matrix (`data/battery_plan_matrix.json`)

The regenerable basis of report §4. For the top-3 plans in `data/plan_results.csv`
(EV-TOU-5 plus the two nearest competitors, EV-TOU-2 and TOU-ELEC; CEA generation, no
relief credit), it bills the same 365 days **without** a battery and **with** the §3.13
price-aware 13.5 kWh / 11.5 kW PW3 dispatch (`run_batt` imported from
`battery_dispatch_policies.py`) under each plan's own rates. Rates are **published
rate-table values** (the `CLAUDE.md` §9 canonical-rates exception for cross-plan ranking),
and billing replicates `analyze_norelief.py` exactly (interval netting, export credit =
max(rate − NBC, 0), BSC × 365, holiday-as-weekend TOU assignment) so the no-battery column
**ties out to the committed `data/plan_results.csv`** — asserted in-script. All three plans
share the same 2026 three-period TOU windows, so a single dispatch trace is billed under
each plan. Results: EV-TOU-5 $4,849 → $2,564 (battery value **$2,318/yr**), EV-TOU-2
$5,843 → $4,176 ($1,667), TOU-ELEC $6,356 → $5,349 ($1,007) — the battery is worth the most
on EV-TOU-5, so it strengthens (not changes) the plan answer. The artifact also records a
`canonical_crosscheck_ev_tou_5` block ($4,904 no-battery / $2,329 battery value from
`battery_dispatch_policies.json`, asserted within $100): the small differences vs the
table-rate column are the rate basis (published tables vs bill-derived) and the holiday
convention (§6.5). Run from `private/verify` (repo root found by walking up); writes
`data/battery_plan_matrix.json` atomically.

### 3.17 `analysis/carbon_dispatch_tradeoff.py` — does cost-minimizing dispatch fight carbon-minimizing dispatch? (`data/carbon_dispatch_tradeoff.json`)

§3.15 established that the cheapest grid hours are also the dirtiest. This script asks the
question that observation implies but §3.13's dispatch comparison never answered: does the
battery's own cost-minimizing schedule make the household's grid CO₂ worse than not having a
battery at all, and what would fixing that cost in dollars?

**Three runs, one battery model.** All three share the exact constants `battery_dispatch_
policies.py` already uses (13.5 kWh usable, 11.5 kW, 90% round-trip efficiency), imported
rather than re-declared, and the same billing engine (`rates.bill_nem` via `battery_
dispatch_policies.billed()`); the charge/discharge decision differs between them by design,
but that decision difference also changes how much load each policy serves (Run A cycles
4,968 kWh/yr against Run B's 3,176), so this does not isolate a pure objective effect at
matched utilization — see the throughput caveat below.

- **Run A (cost-minimizing).** Calls `battery_dispatch_policies.run_batt(..., "greedy")`
  directly, unmodified — the same policy `data/battery_dispatch_policies.json`'s published
  `pw3.greedy` figure comes from. A cross-check inside the artifact asserts Run A's computed
  saving against that committed figure within $5, so the two are guaranteed to agree rather
  than coincidentally match.
- **Run B (carbon-minimizing, new).** Mirrors Run A's control structure with the TOU-period
  decision replaced by an intensity-based one: discharge when the measured per-interval grid
  intensity is above a threshold ("dirty"), grid-charge only when below it ("clean"). One
  deliberate narrowing, stated rather than left implicit: Run A's actual discharge window is
  the OR of an unconditional on-peak carve-out and a non-super-off-peak/low-kW clause: a
  binary clean/dirty split has no analogue for the unconditional carve-out, so Run B omits it.
- **Run C (union/efficient, new).** Discharges whenever either Run A's or Run B's condition
  holds; grid-charges only when both cheap AND clean hold. This isolates the genuinely
  conflicting hours (cheap-but-dirty, clean-but-expensive) from the hours both objectives
  would serve anyway.

**Run B's threshold is derived, not invented.** Its discharge window is sized to the same
fraction of the year as Run A's non-super-off-peak discharge window, measured directly from
this household's own TOU assignment (`rates.period_at` via `behavior_rebuild.load()`) rather
than hardcoded — so the comparison isolates *which* hours get served, not how many. Threshold
= the intensity value at that fraction's quantile of the year's per-interval intensity array.
Target clean/dirty split 46.74%/53.26%; achieved 46.75%/53.25% (ties at the underlying data's
0.1 kg/MWh resolution keep the achieved split close to, not exactly on, the target).

**Net, not gross, CO₂.** The three policies consume different amounts of exportable solar via
battery charging (Run A's own solar-charging displaces 829.4 kg/yr of exports, Run C's only
493.9), so ranking policies on gross import CO₂ alone silently drops that difference and can
invert which policy is actually cleaner for the atmosphere — an adversarial review caught
exactly this: the gross-import figures published in an earlier draft ranked Run C as cleaner
than Run B (4,826.4 vs 4,917.9 kg), but net accounting (import minus that policy's own
export-avoided) reverses it (Run C 4,332.5 vs Run B 4,258.2 — Run B is actually cleaner).
Every comparison below is therefore NET; gross import and gross export-avoided are still
reported per policy in the artifact as a breakdown, never discarded.

**Results** (all figures against the no-battery baseline: $4,904.13/yr, 4,487.2 kg net
CO₂/yr): Run A saves $2,328.66/yr but *raises* net CO₂ by 413.9 kg/yr above the baseline —
grid-charging during super-off-peak means charging during the year's dirtiest hours (270.1
kg/MWh overnight vs. 158.4 on-peak, §3.15's own window means). Run B avoids 229.0 kg/yr net
but keeps only $228.39 of the saving. Run C recovers $2,023.91/yr (87% of Run A's saving)
while still avoiding 154.8 kg/yr net — 74.2 kg/yr less than Run B, a real but small carbon
cost for capturing nine times more of the dollar saving. Run C is still judged a genuinely
distinct third outcome (not merely a blend reducible to A or B) by requiring BOTH its $ and
its CO₂ to be within 2% of a policy's own figures to count as "not meaningfully different":
its bill sits 38.4% from Run B's, so the test fails against B even though its net CO₂ now
sits within 1.7% of Run B's own.

**Tradeoff figures.** Cost penalty of the clean policy (Run B's bill minus Run A's bill):
$2,100.27/yr. CO₂ penalty of the cheap policy (Run A's net CO₂ minus Run B's net CO₂): 642.9
kg/yr. Both are also expressed per kWh cycled (51.6¢/kWh and 0.158 kg/kWh respectively),
normalized by the **mean** of Run A's and Run B's own kWh cycled-through figures — the two
runs are different dispatch schedules with different total throughput, so no single run's
throughput is uniquely "the" right denominator; the mean is used and stated explicitly rather
than silently picking one side. Because Run A and Run B are not throughput-matched (see
above), these $/kg figures describe these two specific heuristic policies as implemented, not
a bound on what a jointly re-optimized, throughput-matched carbon-vs-cost tradeoff would show.

**Average vs. marginal basis (GLOSSARY: average vs. marginal emissions rate).** Every CO₂
figure here, like §3.15's, values energy at grid-average intensity — the only figure CAISO's
public history endpoints support; they do not publish a marginal rate or per-hour
marginal-fuel identification. Direction, stated rather than quantified: marginal generation
is disproportionately gas overnight (where average intensity is already gas-heavy, so the
marginal-vs-average gap there may be small) and increasingly displaces cleaner marginal
resources at solar-heavy midday — so a marginal accounting would likely show an even larger
clean-midday-vs-dirty-overnight gap than the average-based figures above already show, the
same directional caveat §3.15 states for its own figures. Exports (including the export
reduction each policy's own solar-charging causes) are valued at the same average intensity
at the export hour, the identical "avoided emissions" convention §3.15 uses for its own
export-avoided figure.

**Inputs and provenance.** Intensity: the committed `data/caiso_hourly_intensity.csv`
(364/365 real days), read via `carbon_fullyear.build_covered_from_committed_csv()` (imported,
not re-implemented); the raw CAISO day-cache is not touched by this script. The one uncovered
day (2026-03-08, §3.15) is filled by that month's hour-of-day mean, mirroring `carbon_
fullyear.py`'s own construction (not exposed there as a standalone function, so mirrored here
rather than imported — the underlying CSV reader is shared). Household side: private Green
Button 15-min data via `behavior_rebuild.load()`. Run from `private/verify` with `usage.csv`,
`behavior_rebuild.py`, `battery_dispatch_policies.py`, `carbon_fullyear.py` and `rates.py`
beside it; writes `data/carbon_dispatch_tradeoff.json`.

## 4. Battery simulation methodology

**Arbitrage dispatch (identical greedy policy in `battery_backup_sims.py`, `package_sims.py` (REMOVED — superseded by the integrated pipeline),
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
| `hourly` | line, 4 series | `hourlyS_imp`, `hourlyS_exp`, `hourlyW_imp`, `hourlyW_exp` (24 each) | average kW by hour of day, split summer/winter | `report_data.json → hourly_S / hourly_W` (from `analysis/report_data.py`; mean kWh per hour-of-day, split by season) |
| `battery` | line, 3 series | `bat_now_S`, `bat_pw3_S`, `bat_pw3x_S` (24 each) | summer average grid-import kW by hour: today, with 1× PW3, with PW3+Expansion | `bat_now_S` is `hourlyS_imp` rounded; the two battery series are the **§3.13 price-aware dispatch** applied to summer intervals and re-averaged by hour — committed as `data/battery_dispatch_policies.json → pw3/pw3x.greedy_profile_S` (on-peak imports fall 4,022 → 870 kWh/yr with PW3, → 329 with the expansion) |
| `monthly` | bar ×2 + line | `mLabels` (13), `mImp`, `mExp` (kWh), `mCost` ($) | calendar months Jul 2025*–Jul 2026* (* = partial) | `mImp`/`mExp` = `monthly.csv` (from `analyze.py`), rounded; `mCost` = `report_data.json → monthly.cost`, the canonical per-month netted energy cost (bill-derived rates; excludes the non-bypassable charges and the daily BSC) |
| `periods` | horizontal bar ×2 | inline literals: kWh `[14856, 4498, 4022]`, $ `[1875, 2316, 2998]` | annual import kWh and gross import cost (imports × all-in rate, before export credits) for super-off-peak / off-peak / on-peak | `report_data.json → periods_chart`, produced by `analysis/report_data.py`; the on-peak $2,998 matches `report_data.json → onpeak.import_cost`, and `analysis/test_report_consistency.py` asserts both arrays against the artifact |
| `carbon` | line, 1 series | `carb` (24) | CAISO grid CO₂ intensity, kg/MWh, annual average by hour of day | `data/carbon_fullyear_results.json → intensity_kg_per_mwh.annual_avg_by_hour` (from `carbon_fullyear.py` §3.15 — 364/365 real CAISO days + one month-hour-mean interpolated day; the original 4-day `carbon_results.json` series from `carbon_timing.py` remains as the §3.10 workpaper) |

Everything else in the report (plan table §3, battery tables §4/§6, package cards §7, deep-dive
figures §9, bill audit §10) is static HTML transcribed from `plan_results.csv`,
`battery_plan_matrix.json` (the §4 matrix), `package_results.json`, `battery_sim.json`,
`backup_endurance.json`, `deep_results.json`,
`weather_results.json`, and the bill summaries. The extended findings woven into §6
(VPP/resilience, tornado), §9 (dividend, away-days, supercharge/weekend workups), §10
(AB 205, gas HDD decomposition), §13 (2039 NBT strategy, full-year carbon) and the
"What to do Monday" appendix are transcribed the same way from `extended_results.json` and
`carbon_fullyear_results.json`. After any rerun, update both the `D` block and
the prose numbers, then grep the HTML for the superseded figures (`CLAUDE.md` §3).

**Report structure conventions (preserve on regeneration; specs in `CLAUDE.md` §§9–11).**

- **One rate vintage per projection:** the §7 package cards state projected bills **at
  constant 6/1/2026 rates** — LOW ~$3,700/yr (~$309/mo), MID ~$1,445/yr (~$120/mo) vs the
  ~$4,880/yr no-change model baseline — never against the $3,282 actual (billed largely on
  2025 tariffs), which is noted as non-comparable.
- **Confidence labels:** inline pills tag claims as `measured` (meters/bills/multi-source),
  `modeled` (validated model at current rates), or `estimated` (rate index, single cleaning
  event, cited external program terms). §§1–10 are measured/modeled
  throughout; pills appear in §§11–13 where evidence is thinner, plus on the extended
  findings inside §6 (VPP revenue / resilience value — estimated), §9 (dividend —
  estimated) and §10 (AB 205 — measured), and §14 defines the legend.
- **Navigation:** sticky TOC grouped Verdict / Evidence / Audit with scroll-spy (one
  IntersectionObserver); the three heaviest audit sections (§9 deep dives, §12 cleaning,
  §13 carbon/NEM) are native `<details>` blocks, OPEN by default (collapsible) with one-line conclusion
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
(`billing_model_nem.py`) gives **$4,904** — i.e. the **netting method itself agrees to
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
be movable. That crude cap is superseded: the published behavior bracket comes from the
§3.8 session-based model — **$1,009–1,221/yr** for EV-timing alone (80–100% compliance),
**$1,700/yr** adding a 25% flexible-house-load shift ($2,179/yr at the aggressive 50%
stretch) — rather than the full crude-cap figure.

**6.5 Holiday handling — RESOLVED.** This section used to record a per-script inconsistency:
the legacy ranking pair applied a holiday-as-weekend rule while the pipeline scripts used bare
`weekday >= 5`. The bills settled it (`analysis/tou_audit.py` confirms all eight tariff
holidays individually), the rule now lives in `rates.off_peak_day`/`period_at`, and every
pipeline script was migrated; `analysis/test_rates.py` and `analysis/test_scripts_runnable.py`
guard against a private copy reappearing. Kept as a heading because other sections cite §6.5.

**6.6 Other limitations** (from report §14): rate tables go stale on SDG&E (Jan/Jun) and CEA
(Feb/Jun) revision cycles; TOU-DR-P event surcharges are only modeled in the §3.5 wildcard;
battery installed prices are estimates; the simple 6.2-yr price-aware battery-alone payback in
`package_results.json` (8.5 yr evening-only) uses no discounting or escalation (the Monte
Carlo in §3.5 and the published escalation ladder in `battery_dispatch_policies.json` §3.13
handle both); the endurance sims' full-SOC
and 14-day-cap assumptions (§4); `deep_analyses.py` reads `base_save` from
`battery_dispatch_policies.json` (`post_behavior.mid.battery_marginal`) — rerun order matters
(§7): regenerate the dispatch artifact before the Monte Carlo.

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
   hardware prices in `battery_backup_sims.py`/`package_sims.py` (REMOVED — superseded by the integrated pipeline) as quoted to you.
4. **Run the scripts in dependency order** (Python 3 with `pandas` and `numpy`):
   1. `analyze_norelief.py` (and/or `analyze.py` if your CCA credit applies) → plan table,
      profiles;
   2. `battery_backup_sims.py` → arbitrage + endurance;
   3. `battery_dispatch_policies.py` → dispatch policies and the post-behavior battery
      marginal (`post_behavior.mid.battery_marginal`);
   4. `deep_analyses.py` — it reads that marginal from the dispatch artifact itself, so no
      constant needs editing; just run it after step 3;
   5. `billing_model_nem.py` → compare its output to your actual bills before quoting any
      absolute dollar figure (per `CLAUDE.md` §1; expect it to read high if it prices history
      at current rates). Verify against a bill line that non-bypassable charges are billed on
      GROSS imported kWh, and that the code does the same (`CLAUDE.md` §9);
   6. `behavior_rebuild.py` → session-based shift ladder (imported by the integrated
      battery pipeline; its standalone evening-only overlap figures are workpaper-only);
   7. `battery_dispatch_policies.py` → the three-policy comparison whose **price-aware**
      results are the published battery basis and escalation ladder (§3.13);
      then `battery_plan_matrix.py` → regenerates `data/battery_plan_matrix.json` (the §4
      battery × plan matrix; ties out to `plan_results.csv`, cross-checks the dispatch
      artifact); then `package_results.py` → composes `data/package_results.json` (the
      LOW/MID/HIGH package artifact) from the behavior + dispatch artifacts, no new
      computation;
   8. `lifetime_payback.py` (if you have solar and the install invoice) → the invoice, the
      date it was paid and the PTO date come from `private/household.yaml`; update the
      script's own `PROD`/`RATE_IDX`/blended constants first (§3.12);
   9. `extended_findings.py` → the extended-findings batch (§3.14). Run it only after
      `behavior_rebuild.json` and `battery_dispatch_policies.json` are current: it
      recomputes the battery dispatch figures from the engine and aborts if they drift
      more than ±$1.50 from the committed dispatch artifact, then writes
      `extended_results.json` atomically (tmp + `os.replace`);
   10. `carbon_timing.py` (4 seasonal days) and/or `carbon_fullyear.py` (§3.15 — the
      report's carbon basis; uses the raw day-cache `private/1-raw-data/caiso_raw/` when
      present, else rebuilds exactly from the committed `data/caiso_hourly_intensity.csv`;
      `carbon_results.json` supplies the reconstructed legacy days in raw mode) → the §13
      carbon artifacts.
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

## 8. LLM configuration and AI workflow

This analysis was produced with LLM agents operating under written, committed rules. The
configuration, so a reader can audit the process or reproduce it:

- **Tools and roles.** Generated with Claude Cowork (Fable 5); independently reviewed with
  Claude Code (Fable 5), which also runs the repo's operations (regeneration gates, privacy
  scans, deployments); adversarially reviewed with Codex (GPT-5.6 Sol), invoked with an
  explicit `--base` so the review scope covers everything since the last reviewed commit.
  The report's §14 provenance note records this chain and survives every regeneration
  (`CLAUDE.md` §11).
- **Agent contract.** `CLAUDE.md` is the binding operating manual for any agent working in
  this repo: the evidence-only mandate (§0), bill-validation-first ordering (§1), payback
  honesty (§2), privacy enforcement (§4), the pre-publication gates (§9), and the report
  spec (§10). `reusable-prompt.md` is the entry-point prompt that rebuilds the entire
  analysis for a new household; `report-template.html` is the report shell it fills.
- **Review loop.** Work lands on main; adversarial reviews run against the last-reviewed
  base; every finding is either fixed and verified (with the fix's own regeneration-gate
  run) or explicitly rejected with a reason. A finding is never closed by description
  alone — the §9 "code implements its docs" gate exists because that failure recurred.
- **Prose control.** Report and README prose must end each regeneration with a
  de-AI-writing pass — the [humanizer skill](https://github.com/blader/humanizer) in
  Claude Code, whose checklist is Wikipedia's
  ["Signs of AI writing"](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)
  (maintained by WikiProject AI Cleanup). Structural em dashes in the report's ledger
  design (day-band, tables, meta rows) are exempt; the pass targets running prose.
- **What is NOT configured here.** Model choice, skills, and agent settings live in the
  operator's environment, not this repo; the repo carries everything an agent needs to
  behave correctly (`CLAUDE.md`, the cheatsheet, the template, the gates), so the analysis
  is not tied to any one vendor's tooling.

---

## 9. Bill PDF parsing (`analysis/parse_bills.py`)

Added 2026-07-27, when the bill corpus was extended from 12 to 25 statements per fuel.
It also closes a `CLAUDE.md` §9 gap: `electric_bill_summary.csv` and `gas_bill_summary.csv` had been
produced by an in-session pdfplumber extraction with no committed script.

**Inputs** (gitignored): `private/1-raw-data/electric-bills/sdge_electric_<statement-date>.pdf`
and `.../gas-bills/sdge_gas_<statement-date>.pdf`. Filenames carry the STATEMENT date;
billing periods are read from the PDF text, never inferred from the filename.

**Outputs** (committed, de-identified):

| File | Contents |
|---|---|
| `data/bill_periods_electric.csv` | one row per electric billing period: `statement_date, period, days, generation_provider, net_kwh, gross_kwh, sdge_delivery, cca_generation, current_charges, base_services_charge` |
| `data/bill_periods_gas.csv` | one row per gas period: `statement_date, period, period_end_month, therms, total_gas_service, billed_amount, baseline_rate, nonbaseline_rate` |
| `data/bill_tou_detail.csv` | long format: `statement_date, period, section (delivery/generation), season, tou_period, kwh, rate_per_kwh` — the rates as printed on each bill |
| `data/electric_bill_summary.csv`, `data/gas_bill_summary.csv` | regenerated in their original schemas |

**Reproduction gate.** The script rewrites the two legacy summaries from the same parse.
`gas_bill_summary.csv` regenerates byte-identically to the version committed before the
script existed, and `electric_bill_summary.csv` reproduces 12 of its 13 rows exactly. The
regeneration command in `CLAUDE.md` runs the parser and diffs all five artifacts.

**One correction the gate surfaced.** The thirteenth row differed: the original in-session
extraction recorded `gross_kwh` = 1,344 for the `10/1/25 - 10/27/25` period, but the
statement prints 1,904 twice — once as "Non Bypassable Charges Usage" and again as the kWh
basis of the Wildfire Fund Charge. The committed artifact was wrong and is corrected here.
The error was inert: no analysis script reads this file (it is reference evidence for §10),
and the value appears in no published figure. Net kWh, all charge columns, the 365-day
coverage, and the $3,282.22 annual total are unchanged.

**Parsing decisions that matter.**
- *Periods, not files* (§1). One statement can hold two billing periods when a rate change
  splits it: the Oct 2025 statement yields `9/26/25 - 9/30/25` and `10/1/25 - 10/27/25`.
  26 electric periods come from 25 statements. Duplicate periods raise `SystemExit`.
- *Gross vs net.* `net_kwh` is the bill's "Total Usage"; `gross_kwh` is its
  "Non Bypassable Charges Usage" — the gross imported kWh that NBCs are levied on
  (987 net vs 1,934 gross on the Jun 2026 period). This is the per-bill evidence behind
  the netting model's NBC-on-gross treatment (§14).
- *Generation provider changes mid-corpus.* Through the 11/26/24 - 12/26/24 period the
  account was on SDG&E bundled generation with no CCA pages, so generation is inside
  `sdge_delivery` and `cca_generation` is 0. From 12/27/24 - 1/27/25 onward CEA bills
  generation separately. `generation_provider` records which, because the columns mean
  different things across that boundary.
- *Printed-number quirks.* Negative quantities use U+2212 MINUS SIGN, and rates omit the
  leading zero (`$.04013`). The number pattern accepts both.
- *Two-column extraction.* `extract_text()` interleaves right-column charge lines between a
  TOU block's "kWh used" and "Rate/kWh" rows, and the phrase "kWh used" also appears in the
  usage graphic and the glossary. TOU rows are therefore anchored on the
  `<SEASON> USAGE On-Peak` headers and read within a bounded window.
- *Rate segments inside a period.* When a tariff change lands mid-cycle the bill splits
  the period into segments ("3 Days Charge ...", then "26 Days Charge ..."), each with its
  own $/kWh for the same season. Those segments are kept as separate rows, keyed by
  `segment` with the segment's `segment_days`, because collapsing them would discard the
  per-bill evidence of a rate vintage change.
- *Fail closed, corpus-wide.* Per-period checks are not enough: a statement that is absent
  or misnamed simply would not appear, so the artifacts would be rewritten a few periods
  short with no error. Before anything is written the script requires every statement the
  committed summaries are built from, rejects duplicate periods, requires each fuel's
  periods to tile its window exactly (one day between a period's inclusive end and the
  next period's start — more is a missing statement, less is an overlap that would
  double-count days, usage and dollars, and overlapping period strings are distinct so the
  duplicate check cannot see them), requires TOU rows for every period with unique
  `period/section/season/segment/tou_period` keys, and reconciles delivery TOU kWh against
  each period's net usage.
- *Transactional publication.* The five artifacts are one evidence set. Each is staged to a
  `.tmp` and swapped in only after every validation passes; if any swap fails, the ones
  already swapped are restored from backups, so a failed run leaves the committed set
  exactly as it was rather than half-updated. Each rollback is attempted independently, so
  one failure cannot abort the rest, and the backups are deleted only once the set is known
  consistent (all published, or all restored). If a restore itself fails the surviving
  `.bak` files are left on disk on purpose and the error names every stale artifact and
  where its previous contents are — deleting them would turn a partial publication into
  unrecoverable evidence loss. Because those `.bak` files are then the only copy, the
  function refuses to run at all while any of them exists: a retry would back the stale
  artifact up over its own recovery copy and destroy the previous evidence for good.
  Recovery is a deliberate manual step.
- *Serialized across processes.* That leftover-backup check is a check-then-act, and every
  run would otherwise stage through identically-named files, so two concurrent runs could
  consume or delete each other's staging and backup files. Publication therefore holds an
  exclusive `flock` on `data/.parse_bills.lock` across the whole publish/rollback/cleanup
  sequence, and stages through pid-qualified temp paths. Measured before the lock existed:
  two simultaneous publishers corrupted the artifact set in 11 of 15 runs, several times
  leaving a mix of both runs' output while one reported success; with the lock, 0 of 15.

**Negative tests** (`analysis/test_parse_bills.py`, run with the venv python). Each builds a
throwaway repo from the real corpus, breaks one thing, and asserts the parser exits non-zero
*and* leaves the artifacts untouched: a missing summary statement, an electric statement
missing from the middle of the window, a GAS statement missing from the middle of its window
(the fuels bill on different cycles, so each needs its own continuity check), and TOU headers
that stop matching. Three further cases drive the publication step directly — a writer that
fails before any swap, an `os.replace` that fails part-way through the swaps (asserting every
published file is restored), one that fails during the restore too (asserting the backups
survive and the error explains manual recovery), and a retry after that failure (asserting it
refuses and leaves the recovery copies byte-intact). Two cases cover concurrency — one
publishing while the lock is held, and two genuinely simultaneous processes asserting that
the set ends internally consistent with nothing left behind — and two feed `_validate`
overlapping electric and gas periods. Thirteen cases in total. Only the five corpus-dependent
ones skip when the gitignored PDFs are absent; the eight covering publication, rollback and
concurrency use temp files or the committed `data/` artifacts and therefore run in a clean
checkout and in CI (`.github/workflows/tests.yml`) — skipping the whole suite would have let
a broken lock pass the documented command with exit code 0.

**Validation at time of writing.** 26 electric periods spanning 763 continuous days with no
gaps between consecutive periods; the 13 periods of the report's audited year sum to 365
days and $3,282.22, matching §10; delivery TOU kWh ties to `net_kwh` in all 26 periods.

**Privacy.** The PDFs carry name, service address, account and meter numbers, and the CCA
service-delivery-point id; none are extracted. How the household pays its bills (arrangements, schedules,
balances owed) is private-tier and never emitted.

## 10. Year-over-year bill decomposition (`analysis/bill_decomposition.py`)

Added for issue #3. Separates the change between two comparable early-summer billing
periods — `5/25/24 - 6/25/24` (32 days, bundled, net 346 kWh, $48.25) and
`5/29/26 - 6/26/26` (29 days, CCA, net 987 kWh, $398.56) — into price, quantity, TOU mix
and provider. They are the first and last statements in the corpus, both straddle the 6/1
winter→summer boundary, and one provider break sits between them.

**The cost series is the accrual, not the payment column.** SDG&E's billing-history export
(`private/1-raw-data/electric_billing_history_2024-2026.csv`) reports `current_charges` of
$0.00 for the 2024 statement and for every statement through 2025-04-02. Under NEM 2.0 the
energy component accrues to the annual true-up rather than being billed monthly, so that
column is a payment series. `billing_mode_scan()` reads every statement before any arithmetic
runs and hands each one to `classify_statement()`, which establishes its mode from its own
text or refuses:

* **`Payment Required This Month: No`** must carry one of the two recognised true-up deferral
  sentences (`*Payment not required for NEM charges. Your account will true up on …` /
  `Payment is not required at this time. Your account will true-up on …`), which name the date
  the charge is accruing towards. The payment flag alone is not accepted as evidence of
  accrual — a wording change or a real billing-mode change would flip it silently — so a
  statement without one stops the run, named.
* **`Payment Required This Month: Yes`** must prove it is the annual settlement rather than an
  ordinary payable bill. It has to say so (`Net Energy Metering Annual True-Up Bill`, `Your
  account has been settled and all applicable generation credits have been applied.`), it has
  to carry a billing period ending on the true-up date the *preceding* statements printed, and
  its own `True-Up Date:` field has to agree. Any of the four failing stops the run.

Scanning runs in date order so each claimed settlement is matched against the true-up date
that was actually being accrued towards, and that expectation is cleared once a settlement
consumes it. Every count and every sentence in `billing_mode_finding()` is then derived from
the validated rows — nothing is a constant, so the artifact cannot go on asserting
uninterrupted accrual against its own scan. Findings: the mode never changed — 23 of the 25
statements accrue and carry their deferral sentence, the other two (2024-12-30 and 2026-01-06)
are annual settlements, each proved against the true-up date the earlier statements named —
and both compared periods are accruing statements. What changed, on **2025-05-02**, is
presentation: the separate NEM ledger block disappears and the same accrued charge starts
appearing on the Account Summary line the export follows. `export_reconciliation()` explains
every export row as `period accrual − the part deferred into the NEM ledger + the
account-level California Climate Credit`; all 25 statements reconcile to $0.00.

**Sources, and the CCA authority boundary.** Delivery and the 2024 generation table come from
`data/bill_tou_detail.csv`, cross-checked line by line against the PDF. The **charged** CCA
generation is read from the statement's Community Choice Aggregation page
(`Generation On-Peak Summer 205 kWh X $0.51684`), which is the only place it appears anywhere
in the repository — `parse_bills.py` does not extract it, which is why
`rates_history.cca_generation()` fails closed. The bundled-generation table SDG&E also prints
on a CCA statement is never priced as supply: the statement cancels it to the cent on the next
line (`Electricity Generation Credit -180.46` against $180.46 of table), the script asserts
that cancellation, and the table is used only as the same-date bundled counterfactual in the
provider term.

**A settlement $0 is not a price, and the type makes it unusable as one.** Under NEM 2.0 a TOU
bucket billed as a net export prints `Rate/kWh $.00000` and is charged $0: the export went to
the annual true-up, so no price was charged and none can be read off the statement. Coercing
that to `0.0` and multiplying reconciles perfectly while measuring the settlement change and
printing it as a price. Three successive reviews found the same defect in three different
published figures here — the per-cell price split, the headline provider figure, and the price
and quantity bounds themselves — so it is now closed at the representation rather than by
convention. A cell whose rate is not observable carries a `Settlement` object instead of a
number. Every arithmetic operator on it raises `SettlementNotAPrice` (a `SystemExit`
subclass), and so do `float()`, `int()`, `round()`, `bool()` and the comparisons — which kills
the `rate or 0.0` idiom as well as `q(p₁−p₀)`. `json.dumps` refuses it too, so it cannot reach
the artifact by accident. There are exactly three deliberate readings: `observed_rate()`, which
returns the float or refuses by name (a `Settlement`, a missing comparison, or a $0 on a cell
that *was* an import — on an import cell a $0 is a parsing artefact, never a tariff);
`is_observed()`, which tests; and `json_price()`, which renders `null`. Every rate in the
module is constructed by `_effective_rate()` and every dollar counterfactual by
`_counterfactual_usd()`, so there is no path that produces a bare zero.

**Method.** A cell is **priced** when it was billed as a net import in **both** periods — the
only condition under which a rate is observable at both ends (three of six here, the same set
`like_for_like` reports). Per priced cell `c = (season, TOU period)`, with `q` the net kWh and
`p` the *effective billed* rate (charge ÷ net kWh):

| term | formula | computed over |
|---|---|---|
| Laspeyres price / quantity | `Σ q₀(p₁−p₀)` / `Σ p₀(q₁−q₀)` | priced cells |
| Paasche price / quantity | `Σ q₁(p₁−p₀)` / `Σ p₁(q₁−q₀)` | priced cells |
| interaction | `Σ (p₁−p₀)(q₁−q₀)` | priced cells |
| scale / TOU mix (of the Laspeyres quantity term) | `(Q₁−Q₀)Σp₀w₀` / `Q₁Σp₀(w₁−w₀)` | priced cells |
| delivery vintage / supply vintage / provider | `q(d₁−d₀)` / `q(s₁−g₀)` / `q(g₁−s₁)`, `s₁` = the same-date bundled comparison | priced cells |
| netting/settlement | `Σ (current − base)`, the cell's whole dollar change | the other cells |

so the energy change reads

```
energy change = (price + quantity + interaction, over the priced cells)
              +  netting/settlement          (the export-flipped cells, whole)
```

`Q` in the scale/mix split is the **priced cells'** net kWh (952 → 798), not the periods' 346
and 987. Every `p₀` in it is a rate the 2024 statement actually charged, so no kWh is valued at
a settlement $0 — and none can be, because the arithmetic on a `Settlement` raises.

**Three presentation decisions, all because balancing algebra is not the same as a true
statement.**

*No index term is computed where no price exists — and the flipped cells' money is carried
outside the index, not renamed inside it.* On a cell billed as a net export at either end there
is no rate to compare, so there is no price effect, no quantity effect and no vintage or
provider term for it. Giving such a cell's contribution its own name **inside** the price split
would still leave it inside the published price figure — a renamed term is still a term — so
it is taken out instead. Each flipped cell's complete dollar change is a top-level component,
`decomposition.aggregate.netting_settlement`, published undecomposed beside the price and
quantity figures. The price split has three terms and no fourth.
`decomposition.aggregate.scope` names which cells are in which set, and
`priced_cells.reading.covers` repeats it on the figure itself.

*The provider comparison is published twice, because its two halves cover different scopes.*
SDG&E's printed bundled table prices a cell only where that cell was billed as a net import; on
a current net-export cell it prints $0 by deferred settlement, which is a `Settlement` here, so
it cannot be netted against the credit CEA books there. Meanwhile the CCA-only riders (PCIA,
the incremental procurement cost adjustment, the economic development program credit) and CEA's
product adders are charged **once per period on the period's own kWh**, not per TOU cell, and
the statements support no allocation of them to a cell set — inventing a pro-rata rule and
presenting it as evidence is not an option. Adding whole-period riders to a cell-restricted CEA
total therefore compares two quantity scopes at once. `provider_effect_whole_period()` publishes
two figures instead, each stating what it covers: `energy_only_on_the_common_cells` (CEA's
per-TOU charges against the printed bundled table, over exactly the cells that table prices —
same cells, same kWh, energy only, no riders on either side) and `whole_period_arrangement`
(everything the CCA arrangement charged for supply over the period against the whole printed
table, with the cells the table does not price named along with the CEA dollars booked on them).
A net-export cell carrying a *non-zero* printed comparison would be a genuine observed bundled
export counterfactual, so the function raises rather than discarding it.

*No per-year price figure is published.* The index compares two matched endpoints 2.01 years
apart with no comparable pair between them, so a compound-equivalent per-year rate would be a
transformation of two points rather than an observed annual change. `price_index` carries
`is_total_change_not_a_rate` and `no_annual_rate_path` saying exactly that, and every
percentage it publishes is a total change over the window.

*Price and quantity are published as intervals, not points.* Paasche price ≡ Laspeyres price +
interaction, so quoting the Paasche figure as "the" price effect hands the entire interaction
to price while the note beside it says the interaction is not allocated. The artifact publishes
each effect as the interval between its two readings, whose width is exactly the interaction
term, plus both exact pairings (`reading.exact_pairings`). There is no bare `price_usd` or
`quantity_usd` key, and no figure anywhere in the artifact or the report is described as the
amount price "accounts for".

Everything outside the TOU energy lines is carried as its own named term (fixed charge,
non-bypassable + wildfire, CCA unbundling riders, applied NEM generation credit, taxes, CEA
product adders). A statement line the script does not name breaks the reconciliation against
`bill_periods_electric.current_charges` and the run fails — nothing is absorbed silently.

**Results.** Observed +$350.31, components +$350.31, residual **$0.00**, on the identity

```
+350.31  =  (−18.68 price + 68.99 quantity + 34.16 interaction)   priced cells, +84.47
          +   94.46                                               netting/settlement
          +  171.38                                               non-energy bridge
```

TOU energy is +$178.93 of that: **+$84.47 over the three priced cells** (summer on-peak,
summer super-off-peak, winter super-off-peak — imports in both periods) and **+$94.46 of
netting/settlement** over the three that flipped (summer off-peak, winter on-peak, winter
off-peak). Across the priced cells the two exact pairings read base-weighted price −$18.68
with current-weighted quantity +$103.15, and current-weighted price +$15.48 with base-weighted
quantity +$68.99; each sums to $84.47, and the **$34.16** width of each interval is the
interaction term. Splitting the base-weighted quantity term: scale −$20.77, TOU mix +$89.76,
on the priced cells' 952 → 798 net kWh. The largest single line in the whole comparison is the
**applied NEM generation credit, +$123.33**: the 2024 statement applied $128.39 of credit
against a $128.39 energy charge, cancelling it exactly and leaving the bill equal to its fixed
and non-bypassable block; the 2026 statement applied $5.06.

The provider comparison, both scopes: **energy only over the five cells the printed bundled
table prices**, CEA $170.59 against $180.46 of bundled SDG&E supply, **−$9.87 (−5.5%)**; and
the **whole-period arrangement**, CEA's per-TOU charges on all six cells plus its product
adders plus the CCA-only riders, $197.97 against the same $180.46, **+$17.51 (+9.7%)**. The
gap between the two scopes is the winter off-peak cell — a net export in 2026, where CEA
booked **−$2.25** and the bundled table prints nothing because the export settled at the
annual true-up. The per-cell price split, on 2026 weights, is −$0.63 delivery vintage /
+$25.11 supply vintage / −$9.00 provider over the three priced cells (−$10.81 / +$16.49 /
−$24.36 on 2024 weights); those three terms are the whole of the priced cells' price effect
and there is no fourth. On the same three cells the fixed-weight price index is −14.6%
(Laspeyres) / +7.8% (Paasche) / −4.0% (Fisher) over 2.01 years, with super-off-peak delivery
−35.1%, on-peak delivery +14.2% and SDG&E bundled generation +20.6% to +21.1%. A blended
$/kWh over the same cells would read +97.8%, almost all of it mix.

**Output** `data/bill_decomposition.json`, written atomically; run twice → byte-identical.
Registered in `test_scripts_runnable.py` under `NEEDS_PRIVATE_ARCHIVE` (it needs the PDFs), so
the §9 byte-for-byte gate covers it locally.

**Tests** `analysis/test_bill_decomposition.py`, 30 cases. Twenty-nine run in a clean checkout
against the committed artifact and the committed bill artifacts; only the regeneration case
needs the private archive, and it skips with the reason named. The synthetic fixtures build
their cells through the generator's own rate constructors, so an export cell in a fixture
carries a `Settlement` exactly as a real one does.

*The type-level rule.* `case_a_settlement_non_price_refuses_every_arithmetic_use` walks twenty
arithmetic, coercion and comparison routes on a `Settlement` — including `s or 0.0`, which is
how the coercion used to happen — and asserts each raises naming the cell and the rate, that
`observed_rate()` refuses it, that `json_price()` renders `null`, and that `json.dumps` cannot
emit it. `case_an_import_cell_priced_at_zero_is_refused_as_a_tariff` drives the other side: an
import-in-both cell whose base delivery, base supply or same-date comparison rate is $0, or is
a `Settlement`, is refused rather than attributed.

*Scope of the published figures.* `case_the_published_index_covers_only_the_priced_cells`
asserts every aggregate — both price readings, both quantity readings, the interaction, the
scale/mix split and all six vintage/provider terms — equals the sum over the priced rows and
nothing else, that the price split has exactly three keys with no `netting_regime_usd` among
them, that the quantity split's kWh totals are the priced cells' rather than the periods', and
that the split's dollars are the same dollars `like_for_like` publishes.
`case_the_energy_change_is_priced_cells_plus_settlement` asserts each flipped cell's complete
change is carried as the top-level component, that both exact pairings close on the priced
cells' change with nothing left over for it, and that the report states it separately.
`case_a_flipped_cell_is_outside_every_index_term` drives the same rule from a synthetic pair
where the exporting cell would otherwise have produced a large spurious supply vintage, and
asserts its row publishes no index key at all. `case_the_decomposition_is_per_cell_not_only_
aggregate` asserts a settlement row carries no index term — not a zero, not a null — and
`case_the_quantity_split_prices_no_kwh_at_a_settlement_zero` asserts the scale/mix split now
values every kWh at an observed tariff and that the disclosure it used to need is gone.

*The two provider scopes.* `case_the_provider_comparison_publishes_two_scopes` asserts both
readings exist and state what they cover, that the energy-only one is cell-matched on both
sides with the riders and adders nowhere in it, that the whole-period one carries the ledger's
own rider and adder lines and names the export cell as the scope gap with its dollar size,
that the two differ, and that `index.html` quotes both and no longer carries the retired
mixed-scope figure. `case_a_current_export_cell_cannot_enter_the_provider_comparison` drives
the exclusion synthetically — the export cell's row carries `null` on the bundled side rather
than a $0, its −$2.00 appears only inside the whole-period CCA total, a cell with no
counterfactual is refused, a period with no current import cell is refused, and an export cell
carrying a *non-zero* printed comparison raises instead of being dropped.

*The billing mode.* `case_an_accruing_statement_needs_its_deferral_sentence` feeds synthetic
statement text with `Payment Required This Month: No` and no deferral sentence and asserts the
run stops by name, accepts both printed wordings, and checks every accruing statement in the
corpus carries one. `case_a_payable_statement_must_prove_it_is_the_annual_settlement` drives
four refusals — a payable statement that never says it is a settlement, one with no earlier
true-up date to close, one whose period does not end on that date, and one whose own
`True-Up Date:` field disagrees — then asserts the valid case records what proved it and that
both committed settlements carry the matching quotes, period end and printed date.
`case_the_billing_mode_counts_come_from_the_validated_rows` asserts every published count and
the prose itself are derived from the scan, and that the retired hard-coded "the two annual
settlement statements" is gone.

*The identities and the reading.* Three synthetic fixtures pin what the identities are
supposed to do (price-only and quantity-only movement collapse the two readings; the
interaction term equals the spread when both move), one feeds a cell whose dollars contradict
its rate and asserts the identity check refuses it, one removes a cell's same-date comparison
rate and asserts the split is refused rather than estimated, and
`case_the_published_reading_allocates_none_of_the_interaction` asserts the published intervals
are exactly the two readings, that their width equals the interaction, that no bare
`price_usd`/`quantity_usd` key exists, that the reading names the cells it covers, and that
`index.html` carries both bounds, states no point attribution, and carries none of the retired
all-cell figures.

## 11. Intake privacy tiers: what may appear in a committed artifact

Every intake field in [`DATA-SOURCES-CHEATSHEET.md`](DATA-SOURCES-CHEATSHEET.md) carries one
of three tiers, and the tier decides where the answer may go:

| tier | may appear in `index.html`, `data/`, `analysis/`, `README.md`, commit messages | where it lives |
|---|---|---|
| `public-ok` | yes | `private/household.yaml`, and in artifacts as needed |
| `private-only` | never | `private/household.yaml` only (gitignored) |
| `secret` | never | a gitignored `.env` only — never `household.yaml` |

`CLAUDE.md` §4 states the rule as binding. This section records where the boundary actually
sits, why it sits there, and what a reader should not expect the enforcement to catch.

### 11.1 What enforces it

- **The pre-commit gate**, which runs two scans over the staged content and refuses to
  commit if either cannot run. `git config core.hooksPath .githooks` turns on a `gitleaks`
  run over staged changes, chaining the committed generic rules (`.gitleaks.toml`) with the
  local-only person-specific rules (`private/pii-rules.toml`, gitignored, never committed —
  it contains the values it guards). CI re-scans full history on every push with the
  committed rules only, so the local hook is the real gate for anything person-specific.
  The hook then runs `analysis/privacy_tiers.py --staged`, which is the same tier scan the
  suites run, restricted to the blobs this commit would add or change — read out of the
  index, not the working tree, since those differ. Before it scans anything it checks that
  every tiered field still resolves to a locatable subject and refuses if one does not
  (§11.6): a rule pointed at nothing reports every tree as clean. It then blocks on a hit,
  naming the field id, the yaml path and the file and never the value. A hit inside a file's
  own declared literal is excused only where the same file already declared that literal in
  the baseline this change is measured against (§11.5), which needs no configuration and no
  committed count, so the hook behaves the same way in a fork with somebody else's intake in
  it. Where `private/household.yaml` is absent (somebody else's clone) the value half cannot
  run: the hook says so in terms and allows the commit, because refusing would make the repo
  uncommittable for everyone but its owner, and the key/path rules of §11.5 and the resolver
  check both still run. Cost on this tree: about 0.30 s end to end for an ordinary commit,
  gitleaks included, and about 1.0 s for the scan over all 107 files. The baseline costs
  nothing until a coincidence exists — the blob is fetched only for a file whose own literals
  hold a private answer, which on an ordinary commit is none of them; a commit that re-stages
  all three files where this tree does coincide pays 0.15 s more.
- **The commit-msg gate.** The tier table above puts commit messages inside the boundary, and
  a pre-commit hook cannot see one: it runs before the message exists. `.githooks/commit-msg`
  runs `analysis/privacy_tiers.py --message` over the proposed message with the same locally
  resolved needles, blocks on a hit naming the field id and never the value, and fails closed
  — a scanner that cannot run refuses the commit rather than passing it unscanned. It follows
  the hook beside it in every other respect: the same interpreter search, the same exit codes,
  and the same "say so and allow" where `private/household.yaml` is absent. `core.hooksPath`
  already points at `.githooks`, so the file needs no separate enabling step. Cost: about
  0.17 s end to end.
- **A tier scan over one artifact.** `analysis/test_service_headroom.py` parses the
  cheatsheet's field blocks, reads the matching values out of `private/household.yaml`, and
  fails if any `private-only` value turns up in `data/service_headroom.json` — as a value or
  anywhere in its prose. It is the case that would have caught the meter class, which had
  reached the artifact quoted inside a provenance sentence. It refuses to pass when its
  needle universe comes back empty, so a broken resolver reads as a broken scan and not as a
  clean bill.
- **The id → path contract**, in the cheatsheet under "The `id` → `private/household.yaml`
  path contract". A tier is attached to a field id; the value sits at a yaml path; anything
  enforcing the tiers has to resolve one to the other, and a resolver that quietly finds
  nothing reports every field clean. The contract fixes the resolution order, lists every id
  whose path cannot be derived, declares the ids that store no yaml value at all, and
  requires a gate to distinguish *checked* from *unchecked* in what it prints.

- **A repo-wide tier gate.** `analysis/test_privacy_tiers.py` runs the same scan over every
  tracked file `git ls-files` reports, minus `private/`. It matters that the scope is the
  tracked tree rather than `data/`: of the eleven places the install figure had reached, six
  were in `index.html` and this file, neither of which has a generator, so a gate scoped to
  generator output would have caught fewer than half of them. The suite also fails on an
  intake key with no cheatsheet block, because a field with no tier contributes no needle and
  is invisible to the scan by construction.
- **What CI can and cannot be.** `private/household.yaml` is gitignored, so no runner holds a
  household answer, and shipping one there would be the disclosure this whole section exists
  to prevent. The two cases that need real values — the repo-wide value scan and the reverse
  gate that every intake key is tiered — therefore SKIP on a runner. They skip loudly: the
  suite prints a banner naming each skipped case and the reason, and the workflow step is
  named for what it actually covers, so a green check cannot be read as real-value coverage.
  Everything else runs there in full, including both halves of the §11.5 key/path rule, which
  need no private data. The real-value scan runs on every commit in the pre-commit hook,
  which is what `CLAUDE.md` §4 means by the local hook being the real gate.

### 11.2 Two fields re-tiered to `public-ok` (issue #38)

**`solar.install_invoice_usd`** — the total installed price of the array, read off the
install invoice. It was tagged `private-only` while its value was committed in
`data/lifetime_payback.json`, in this file, and in five places in the published report. The
tier was re-examined rather than the value removed, and it moved to `public-ok`:

- The repo publishes the array's kW DC, module count, inverter model and count, the PTO date
  to the day, the climate zone, the rate plan, the NEM vintage, a year of 15-minute metered
  consumption and twelve months of billed dollars. An install price for a residential array
  is a market fact about a transaction next to that, and it is attached to no name, address,
  account number, meter number or coordinate.
- It is reconstructible to within about $5 from figures published in the same artifact. The
  crossover fractions and the cumulative-value series in `data/lifetime_payback.json` bracket
  it directly, so deleting the key would have removed the label and left the number. A tier
  that only holds while nobody does the arithmetic is not a tier.
- Keeping it private would have meant removing the lifetime-payback audit trail, and
  `CLAUDE.md` §9 requires a committed script that reproduces every headline figure. The
  invoice is the denominator of the payback headline.

The invoice DOCUMENT stays `private-only`: it carries the name, the address and the account
details that the price does not.

**`solar.install_paid_date`** — its year-month is the `pto_date`'s year-month, and `pto_date`
is `public-ok` and published to the day. Holding the paid month private while publishing the
interconnection day was an inconsistency rather than a rule. `analysis/lifetime_payback.py`
prints it in its run header and computes nothing from it.

Neither re-tiering changed a published figure. Nothing was added to any artifact and nothing
was removed from one; what changed is that the artifacts and the tiers now say the same
thing.

**`solar.itc_claimed` stayed `private-only`.** It is a fact about the household's tax return
rather than about the array, and no artifact needs it: `lifetime_payback.py` publishes both
the gross and the net-of-ITC crossover as scenarios, so a `null` answer costs nothing.

### 11.3 The panel fields (issue #6)

Section E3 of the cheatsheet is tiered field by field, not as a block, and the split is
between a rating and an identity:

- **`public-ok`: the bare equipment ratings** — service amps, busbar amps, meter-socket
  continuous amps, assembly kA, space and circuit counts, backfeed breaker amps, and which
  end of the busbar a breaker sits on. Each comes from a short list of standard values that
  millions of dwellings share, and the published headroom and NEC 705.12(B)(3)(2) verdicts
  cannot be audited without them.
- **`private-only`: the identifying detail** — catalog numbers for the main breaker and the
  enclosure, the breaker family, the enclosure description, the meter class, and the whole
  circuit schedule, whose `device` markings and door-legend `label`s describe the inside of
  one particular house.
- **One derived exception**, admitted deliberately: the ampere rating of the branch
  overcurrent device serving the existing air conditioning, published as
  `noncoincident_loads.existing_ac_ocpd_a`. It is a standard NEC 240.6(A) ampere size, and it
  is load-bearing — the NEC 220.60 noncoincident credit bound is 125% of it. The label that
  selected it, and the words searched for, stay private.

Issue #38 tiered the three panel keys that had no field block, on the same principle.
`panel.tandem_density` is `public-ok`: it summarises the schedule more coarsely than
`data/service_headroom.json` already does, since the artifact publishes the exact
`twin_density_devices` count derived from the same list. `panel.schedule_confidence` and
`panel.no_dryer_or_water_heater_circuit` are `private-only`: both are read off the door
legend, no script reads either, and the exception above admits a schedule-derived value only
where it is load-bearing and a standard rating. The same fact as the second is available
publicly from `appliance_fuels`, which is where an artifact takes it from.

### 11.4 The monitoring feeds (issue #38)

The `monitoring` list had no field block at all, so none of its keys had a tier and no gate
could see them. Section E4 now tiers them per key, on the line between a capability and an
address:

- **`public-ok`** — `source`, `measures`, `resolution`, `finest_history_interval`,
  `solar_cts_fitted`, `history_depth_verified`, `live_since`, `status`. These are properties
  of a product, or measurements of what it returned when probed. Every owner of the same
  monitor has the same answers.
- **`private-only`** — `url`, `api`, `owned_by`. A monitoring site URL is usually built
  around the system id (`/systems/<id>`, `?sid=<id>`), which is the "utility/solar/PVOutput
  account id" `CLAUDE.md` §4 keeps out of committed artifacts; `api` is an access path into
  one household's account; `owned_by` is a path on the operator's own machine. The list as a
  container is `private-only` for the same reason: publishing it publishes those three.
- **`secret`, and therefore not in `household.yaml` at all** — the credentials themselves.
  `api` records the access method and the NAMES of the `.env` variables. A key, token,
  password or session cookie written into that field is a secret in the wrong file.

A tier here follows what the field can hold, not what one household's answer happens to be.
`monitoring[].url` is `private-only` even where a particular entry holds a bare dashboard
link with no id in it.

### 11.5 What literal enforcement cannot see

A value scan searches committed files for the answer as it appears in `private/household.yaml`.
It catches a copy-paste, which is the common failure. It does not catch these, and a gate
that reports "clean" is reporting only that it found no literal match:

- **Reformatting.** A price held as `30000.0` in the intake is `30,000` in prose, `30000` in
  JSON and `$30k` in a chart label. A date held as `2021-04` is `Apr 2021` in a sentence. A
  scan for the intake spelling sees none of those.
- **Rendering in English.** A month name, a spelled-out number, a rounded figure — all
  publish the value without publishing the string.
- **Arithmetic.** A derived figure carries the input without quoting it. The net-of-ITC cost
  in `data/lifetime_payback.json` is 0.70 × the invoice; the crossover fractions and the
  cumulative-value series bracket the invoice to about $5. This is not hypothetical — it is
  the reason `solar.install_invoice_usd` could not have been kept private by deleting the
  key, and it is recorded in §11.2 as part of that decision.
- **Non-distinctive values.** A boolean is `true`. A one-word enum is `high`. A scan for
  either flags every file in the repo, so a literal check on those fields is worthless in
  both directions. They are not left uncovered: the cheatsheet states a greppable substitute
  — no committed artifact carries a key of that name, and no committed script reads that
  path — and `privacy_tiers.unsearchable_fields()` derives the class it applies to from the
  tiers and the declared field types rather than from a list, so a private-only boolean added
  later is covered without anyone remembering. The two mechanisms are made to partition off
  one constant: `Needle.text_searchable` decides whether the scanner can search for a value
  in unstructured text at all, and both the scanner's skip and this derivation read it, so a
  field cannot look searched to one and unsearchable to the other. Say plainly what the
  substitute reaches, because it is not the value: structured keys and script reads. The
  sub-floor word itself goes on being unsearched in running prose and in a commit message,
  which is what the floor is for — dropping it and searching this household's two sub-floor
  door legends in value position returns four matches on a tree that discloses nothing, in
  `index.html` and in the two service-headroom modules. The derivation is per FIELD, so a field
  holding one searchable answer stays with the value scan and a short answer beside it is
  reached by neither half; pushing it down to the leaf was measured and rejected, since the
  leaf here is `panel.schedule[].label` and banning the key `label` fires on nine committed
  artifacts whose labels are chart series and issue-form fields. `scan_artifact_keys` checks JSON and YAML keys
  at any depth and CSV headers; `scan_script_reads` checks the accessor call, a read of a
  container above the key, and the path written as a bare string literal — matched by exact
  equality, so a docstring that merely mentions the path is not a read — and, in the shell and
  yaml this repo also tracks, the dotted path as a whole token outside comments. Markdown and
  HTML are deliberately not searched for a path: several of them have to name one in order to
  document this rule. Neither half needs a private value, so both run in CI as well as in the
  hooks. `household.example.yaml` is exempt from the KEY half in writing, in
  `KEY_RULE_EXEMPT` — carrying every intake key is what makes it the schema — and stays
  subject to every value rule.
- **Anything the tracked tree does not hold.** The gate reads `git ls-files` minus
  `private/`, so it sees every committed text file, and nothing else. An image, a PDF, and
  anything not yet added are outside it, covered by the gitleaks hook's patterns and by
  review — a different net with different holes. Commit messages are inside the boundary and
  outside this scan; `.githooks/commit-msg` is what covers them (§11.1).
- **A literal the repository already carried.** A test module and the example template are
  scanned like any other file — there is no file class the scan skips, and an earlier version
  of this gate that exempted both was blind to exactly the copy-paste it existed to catch,
  since neither exemption could tell an invented fixture from a pasted answer. What excuses a
  hit is the repository's own history: a needle found inside a literal the file DECLARES is
  excused only where the baseline version of that same file already declared the same
  literal, and only as many times as the baseline declared it. A literal that was already
  there was written by somebody who did not have these answers; a literal this change
  introduces or rewords, and that matches a private answer, is the paste the gate exists to
  catch. The baseline is the merge base with the trunk, falling back to `HEAD` — so a value
  pasted in an earlier commit on the same branch cannot launder itself into the next commit's
  excuse — and a repository with no commits excuses nothing. Only three file classes declare
  literals at all: a `test_*.py` module, `household.example.yaml`, and the cheatsheet's
  `question:` scalars. Everywhere else — every artifact in `data/`, every line of prose in
  `index.html` and `TECHNICAL.md`, which is where the meter-class leak actually landed —
  nothing is eligible and nothing is excused for being old.

  A span is the AST node position of a python string, the composer mark of a yaml scalar or
  the cheatsheet's `question:` scalar, so the same value in a comment, a docstring or prose
  still fails; a python string standing alone as a statement is prose rather than a fixture
  literal, so a docstring is never eligible. The comparison is on what the literal says
  rather than on its source bytes, so re-quoting a fixture or moving it to another line does
  not make it new. Nothing about the rule is household-specific, and that is the point: what
  it replaces was a committed table of how many of THIS household's answers coincided with
  the fixtures in each file. That table held for one house. Anyone following the
  "Reproduce this for your own home" flow in `README.md` has their own labels coinciding a
  different number of times, and every commit they made would have been blocked as a stale
  row until they edited shared scanner code to match their own private answers — hostile, and
  an invitation to write a private figure into a committed file. The baseline rule is also
  strictly stronger: a count cannot see a swap that keeps the total, and it excused every
  further occurrence of a value once one was declared, so a fixture label pasted a second
  time into a sentence that says what it is moved no count. A second occurrence is a second
  span, and it fails. What it costs, stated plainly: a private value already committed inside
  one of those three classes' literals before this rule existed goes on being excused, which
  is exactly what the count rule excused too.

The practical consequence: **a literal scan is a floor, not a proof.** The tier list in the
cheatsheet is the record of what may be published, and a value's tier is a decision, made
once and written down with its reasoning — the way the two decisions in §11.2 are written
down. A gate checks that a decision is being kept where it can see it; it does not make the
decision, and it does not see everywhere.

### 11.6 A tier whose subject cannot be located

The path contract in [`DATA-SOURCES-CHEATSHEET.md`](DATA-SOURCES-CHEATSHEET.md) requires that
anything enforcing the tiers **fail loudly** when a field id resolves to no path and is not
declared path-less. The reason is stated there: a tier whose subject cannot be located is a
broken rule, and a gate that reports it clean is the exact failure the contract exists to
prevent. A mistyped or renamed id resolves to a key the schema has no room for, so it yields
no needle, bans no key and removes itself from the universe the scan searches — and the scan
then returns clean, in the reassuring way.

`privacy_tiers.gate()` now makes that check before it scans anything and raises rather than
continuing; `main()` reports it as a block, naming the ids and the paths they derived to.
Resolution is a property of the cheatsheet and the committed `household.example.yaml` alone,
so the check runs identically in CI, in a clone with no private file, and in the hook — the
one place it had been missing, which is the place CLAUDE.md §4 calls the real gate.

## 12. The irreducible bill (`analysis/irreducible_bill.py`)

Added for issue #7. Every payback the report quotes is against a *projected* annual bill.
Only ONE component of that bill is owed no matter what the household buys or does: a per-day
fixed charge (Base Services Charge, or the Monthly Service Fee it replaced). This script states
that STRICT floor in dollars, as a share of the trailing 12 months of actual bills and of each
`package_results.json` package's own projected bill.

Non-bypassable charges (NBC), billed on gross imported kWh, are reported alongside it as a
**separate** figure — real money, currently owed, that cannot be avoided by switching
generation provider or NEM structure — but NOT summed into the floor. A third adversarial
review of this script (issue #7 follow-up) found an earlier version conflated the two: NBC
"cannot be avoided by switching provider" does not mean its DOLLAR AMOUNT is fixed regardless
of usage. NBC is billed *per gross-imported kWh*, so a purchase that reduces gross imports
(a bigger battery, more solar, a load reduction) reduces the dollar amount too — this script's
own `compute_package_gross_imports()` proves it: MID and HIGH import less gross power than the
baseline, and `build_package_floor_fractions()` correctly recomputes a *lower* NBC dollar figure
for both as a direct result. Only the fixed charge is invariant under every purchase, by
construction — that invariance is what a genuine floor requires.

**The four-bucket classification.** Every one of the 26 electric periods in
`data/bill_periods_electric.csv` is split into exactly four buckets that sum to
`current_charges` to within the reconciliation tolerance (below). Note that this sum
(`four_bucket_arithmetic_check_pass` in the artifact) is a TAUTOLOGY, not independent
verification: bucket 4 (`netted_energy`) is *defined* as the residual of the other three, so
adding all four back together is algebraically guaranteed to reproduce `current_charges`
regardless of whether buckets 1–3 were extracted from the bill correctly — it can only ever
miss by cent-rounding. It is published only as a coding-error sanity check. The actual
independent verification is the cross-check described next.

| bucket | source | genuinely irreducible? |
|---|---|---|
| `fixed_charge` | `bill_periods_electric.fixed_charge_total` — whichever of Base Services Charge or the Monthly Service Fee it replaced that period actually billed | **yes** — accrues per day regardless of usage; this is the only bucket the artifact calls a floor |
| `non_bypassable_gross` | printed "Non-Bypassable Charges" + "Wildfire Fund Charge", both billed on **gross** imported kWh | no — real, currently owed, and cannot be avoided by switching generation provider, but its dollar amount scales with gross imports and a purchase that reduces them reduces this bucket too |
| `taxes_and_fees` | printed "Total Taxes & Fees on Electric Charges" | no — its two largest components (Franchise Fee Equivalent Surcharge, State Regulatory Fee) are levied on the energy charge or on kWh counted elsewhere, so they shrink roughly in proportion to whatever a purchase reduces |
| `netted_energy` | the residual: `current_charges − fixed_charge − non_bypassable_gross − taxes_and_fees` | no — the bucket a battery, behavior change or bigger panel can most directly touch, though `non_bypassable_gross` also moves under those same purchases |

`STRICTLY_IRREDUCIBLE = fixed_charge`, summed only over the window below — the other three
buckets are excluded by construction, not netted out after the fact. `non_bypassable_gross`
is reported alongside it as its own figure, not summed in; `combined` (both added together) is
kept for reference but is explicitly not itself called a floor (see below).

**The independent cross-check.** A residual proves nothing by construction — bucket 4 could
silently absorb an extraction error in any of the other three. So `netted_energy` is checked
against a *second*, independently sourced computation per period: the printed delivery TOU
table plus the actual charged supply (the CCA page total on a CCA period, or the SDG&E
generation TOU table on a bundled period) plus the riders that ride on top of supply (PCIA,
the Incremental Procurement Cost Adjustment, the Economic Development Program Credit, the
Applied Generation Credit). On a CCA period SDG&E also prints its own bundled-generation
comparison table plus a matching credit that cancels it to the cent — checked per period as
`generation_credit_cancel_usd` and excluded from the supply term (it nets to zero by
construction; the CCA page total is the actual charge). The two computations must agree to
within **$0.50** (the issue's own stated tolerance, never widened) on every one of the 26
periods; the worst observed residual is $0.02, on the 9/26/24 – 10/25/24 period, so the
tolerance was never tested against its own edge.

**Two parsing gaps found while building this, fixed locally.** `bill_decomposition._LINE_PATTERNS`
anchors its per-kWh-rate lines on a literal `x $`, with no allowance for a minus sign printed
*before* the dollar sign — SDG&E prints PCIA that way routinely (`PCIA 2023 802 kWh x -$.03161
-25.35`, 2025-03-04 statement), and the unmodified pattern simply fails to match, silently
undercounting PCIA. Also, those same per-kWh-rate lines (Wildfire Fund Charge, PCIA, the
Incremental Procurement Cost Adjustment) can reprint more than once *within a single period*
when a mid-cycle rate change splits it into segments (confirmed: wildfire on 2025-03-04 and
2026-02-02; PCIA on 2025-03-04 and 2026-05-04) — each reprint is a portion of the same charge
and must be summed, not conflated with a genuine conflict. `irreducible_bill.py` carries its
own patterns (`_OWN_PATTERNS`) that allow the leading sign and sum same-name segments
(`_SUM_ACROSS_SEGMENTS`); `bill_decomposition.py` itself is untouched — it is owned by a
sibling phase — and its own conflict guard still correctly refuses a *genuine* same-period
duplicate with differing values (e.g. two different "Non-Bypassable Charges" totals).

**Scoping a two-period statement.** The 2025-10-31 statement carries two billing periods (a
5-day stub then a 27-day remainder) in one PDF. Rather than allocate the statement's totals
across the two periods by a formula, `period_text_chunks()` splits the statement's own text at
each period's `Billing Period: … Total Days: N` anchor through to that period's own closing
`Total Electric Service $X` line, and fails closed if the anchor count found does not match
what `bill_periods_electric.csv` says the statement carries. Each period's Non-Bypassable
Charges / Wildfire Fund Charge / Total Taxes & Fees are then read from its own chunk only.

**The 12-month floor.** `build_floor()` sums `fixed_charge` alone over
`parse_bills.SUMMARY_STATEMENTS_ELEC` — this repo's own existing definition of "the most
recent 12 months of bills," reused rather than re-derived (12 statements; 2025-10-31 splits
into two periods, both in-window, so the window covers 13 periods). **One rate vintage per
period, not one vintage for the whole sum:** each period's `fixed_charge` is what that period
actually billed — $16.00/month before 2025-10-01, the per-day Base Services Charge from
2025-10-01 (the CPUC Resolution E-5355 transition; see `analysis/rates_history.py`) — never
today's rate applied backward onto an older period. In the current window, 4 of 13 periods
billed the flat Monthly Service Fee and 9 billed the per-day Base Services Charge. On the most
recent 12-month window the STRICT floor is `strictly_irreducible_usd` = **$264.10, 8.05%** of
that window's $3,282.22 billed total. `non_bypassable_usd_historical` is reported alongside it,
not summed in — **$479.76, 14.62%** — real money actually billed over the same window, but not
itself a floor (see below). `combined_usd_historical` (**$743.86, 22.66%**) sums the two for
reference only; it is explicitly not the answer to "what can never be removed."

**Per-package floor fractions.** `build_package_floor_fractions()` reports what share of each
LOW/MID/HIGH package's `projected_bill_current_rates_yr` (`data/package_results.json`, itself a
fully current-rate, 365-day model via `rates.bill_nem_monthly()`) is the STRICT floor, and
SEPARATELY what share is non-bypassable charges at that package's own modeled usage — how much
of a package's headline saving is even reachable, and how much of what's left is real-but-
movable rather than structural. Built at a **single, current, rate vintage throughout**,
matching how the denominator was constructed (CLAUDE.md §9 — a second adversarial review found
the first fix only carried this halfway, see below):

- `strictly_irreducible_usd` is `annual_days × rates.BSC` — the SAME construction
  `rates.bill_nem_monthly()` itself uses to price the fixed charge inside every package's own
  `projected_bill_current_rates_yr` (`days.nunique() × BSC`, summed month by month).
  `annual_days` is `compute_package_gross_imports()`'s own distinct-calendar-day count of the
  365-day interval frame the package models were built from, not a hardcoded 365. This term is
  invariant across packages (a per-day charge does not depend on how much energy is imported) —
  the ONLY figure here that is a genuine floor.
- `non_bypassable_usd` is `compute_package_gross_imports()`'s actual per-package modeled
  gross-import kWh × `rates.NBC` — re-running the EXACT package definitions
  `battery_dispatch_policies.py` already committed to (LOW = `behavior_rebuild.shift_ev()`'s
  100%-EV-shift scenario; MID/HIGH = `battery_dispatch_policies.run_batt()` at 13.5/27.0 kWh
  usable, policy `"greedy"`), read-only against the raw interval export. A first-review finding
  had shown a retired "held constant, conservative understatement" claim was asserted, not
  computed — the real direction is case-by-case (LOW's imports are unchanged by construction;
  MID's and HIGH's move in either direction depending on dispatch, not only upward from
  round-trip loss). This term is NOT held constant across packages, and it is NOT itself
  irreducible — a different or larger purchase would move it further.
- `combined_usd` sums both for reference; it is not itself a floor.

A second adversarial review (issue #7 follow-up) found that the first review's fix left the
fixed-charge term at the household's ACTUAL historical 12-month total, a real mix of the
pre-2025-10-01 flat Monthly Service Fee and the post-transition per-day Base Services Charge —
mixing a third rate vintage into a fraction whose other term and denominator were already
current-vintage, exactly what CLAUDE.md §9 forbids. Fixed by pricing the fixed-charge term at
the same current vintage as everything else being divided, per above.

A **third** adversarial review (issue #7 follow-up) found that, even after both terms were
vintage-consistent, they were still being SUMMED into one `floor_usd` and both called
irreducible. That is wrong for the non-bypassable term: "non-bypassable" means a charge cannot
be avoided by switching generation provider, not that its dollar amount is fixed regardless of
usage — NBC is billed per gross-imported kWh, and `non_bypassable_usd` is a per-package
recomputation that, on this corpus, comes out LOWER for MID and HIGH than for LOW/the baseline,
proving a purchase moves it. Fixed by reporting `strictly_irreducible_usd` and
`non_bypassable_usd` as separate figures, with only the former called a floor.
`twelve_month_floor` is untouched by any of this and stays entirely historical on purpose — it
answers "what did these components actually cost over the last 12 real months," a different
question from "what fraction of a current-rate projected bill is each component."

On the current artifacts, the STRICT floor (identical dollars, $289.60/yr, across all three
packages) is **LOW 7.9%** of its $3,683/yr projected bill, **MID 20.0%** of $1,445/yr, **HIGH
23.6%** of $1,229/yr. Non-bypassable charges at each package's own modeled usage add **LOW
13.3%** ($490.91), **MID 33.6%** ($485.78), **HIGH 39.6%** ($487.02) — MID and HIGH both lower
in dollars than LOW despite the larger percentage, because their projected bills are smaller and
their gross imports fall relative to the baseline. Combined (**LOW 21.2%, MID 53.7%, HIGH
63.2%**), the figure is a larger fraction of a *smaller* projected bill by arithmetic necessity,
not a claim about which package is better — and, per the correction above, not itself a floor.

**Baseline floor fraction (`build_baseline_floor_fraction()`).** A fourth adversarial review
(issue #7 follow-up, this time on the already-merged script) found that §12's report prose
compared `model_baseline_current_rates` — `data/package_results.json`'s fully current-rate,
365-day MODEL of the no-package/baseline scenario, **$4,904/yr** — against `twelve_month_floor`'s
ACTUAL HISTORICAL 12-month split (`$264.10`/`$479.76`, a real mix of pre- and
post-2025-10-01 tariffs). That is the exact CLAUDE.md §9 vintage-mixing violation already fixed
for the LOW/MID/HIGH package fractions above, just missed in the different paragraph about the
no-purchase baseline. `baseline_floor` prices the same split at the SAME current vintage as the
$4,904 total it is actually compared against: `strictly_irreducible_usd` = `annual_days ×
rates.BSC` (identical to every package's fixed term — same 365-day frame, **$289.60/yr**, 5.9%
of $4,904), `non_bypassable_usd` = `compute_package_gross_imports()`'s own `baseline_gross_kwh` ×
`rates.NBC` (**$490.91/yr**, 10.0% — the same figure as LOW's, since a 100%-compliance EV shift
does not change annual gross imports). Combined **$780.51/yr, 15.9%**. This is a new,
distinct top-level artifact entry, parallel to but separate from `package_floor_fractions`'
LOW/MID/HIGH — the baseline is "no package," not one of the three purchase options.
`twelve_month_floor`'s historical split is untouched and still the right figure for §7's
opening historical-vs-historical sentence (last year's actual $3,282 bill); only the paragraph
comparing against the current-rate $4,904 total needed the current-rate split.

**Minimum-bill provision.** This household's pre-2025-10-01 statements (the template dropped
the block after that date) carry a "Minimum Charge Adjustment" concept in their Net Metering
Summary glossary: if the household is a net generator **for the year**, basic service fees plus
taxes represent all it owes. No statement in the 26-period corpus ever prints this as an
actual dollar line item (checked by regex over every statement's text, not by inspection of
one). The provision's own trigger condition is the **annual sum** of `net_kwh` — but "the year"
means the utility's own annual NEM TRUE-UP year, not any single period being net-negative and
not an arbitrary 12-statement billing window either. A second adversarial review (issue #7
follow-up) found the first version tested `any(net_kwh < 0)` across the 13
`SUMMARY_STATEMENTS_ELEC` window periods and reported that as the annual answer, which a future
window with one export month and eleven larger import months would have gotten wrong even though
the annual sum stayed positive; fixed by summing first, then testing the sign.

A **fourth** adversarial review then found that "the window" being summed was itself wrong: every
statement prints its own "True-Up Date:" field (`bill_decomposition._TRUE_UP_FIELD`, reused
read-only), and this household's true-up date is **12/26 (2024, 2025) then 12/28 (2026)** —
anchored to the 12/27/2019 PTO date, not to any billing-statement window.
`SUMMARY_STATEMENTS_ELEC`'s 13 periods straddle TWO different true-up years on this corpus (its
first 6 periods print true-up date 12/26/2025, its last 6 print 12/28/2026), so summing across
the whole window mixed partial data from two different actual settlement years — not "net
generator for the year" in the sense the provision means it. Fixed:
`statement_true_up_date()` reads each statement's printed field, and
`group_periods_by_true_up_year()` groups every period in the corpus (not just the rolling
window — a true-up year can, and here does, sit partly outside it) by which true-up year it
accrues toward, then checks whether the corpus covers a COMPLETE cycle: contiguous periods,
ending exactly on the true-up date, totalling a real year's days (365, or 366 across a leap
February). On this corpus exactly one true-up year is complete — **12/26/2025**, running
2024-12-27 through 2025-12-26 (365 days, 13 periods, spanning statements 2025-01-31 through
2026-01-06) — a DIFFERENT set of statements than `SUMMARY_STATEMENTS_ELEC`'s own window
(2025-08-01 through 2026-07-02), which only partially overlaps it. That true-up year's own
`annual_net_kwh_sum` is **12,124 kWh**, positive, so the provision's trigger condition never held
— a different number from the naive `SUMMARY_STATEMENTS_ELEC` sum (13,102 kWh), proving the fix
changes the actual figure, not just its label. `annual_net_kwh_sum`/`annual_net_generator` now
report that true-up-year figure; `annual_trigger_basis`/`annual_trigger_limitation` say whether a
complete cycle was found (here: yes) or the closest available approximation is being used
instead, with the limitation stated rather than silently treated as complete; `true_up_years`
carries the full per-year detail (including the two incomplete years — 2024-12-26, only 216 of
365 days covered since the corpus starts mid-cycle, and 2026-12-28, still accruing at 182 days).
`monthly_net_position_window` and `any_period_net_generator_in_window` are kept as before —
separate, explicitly month-level, informational detail over the rolling billing window — but
were never the trigger and still are not. A test with a mixed-sign synthetic true-up year
(`test_irreducible_bill.py`) proves the sum-not-any fix independently of any PDF, and a
corpus-dependent test proves the true-up-year grouping itself separates the printed dates
correctly and that its annual sum differs from the naive window sum. Separately,
`research/rates-reference.md`'s $0.413/day minimum-bill figure belongs to a different,
separately-metered legacy EV-TOU variant, confirmed not applicable to this household's
bundled-meter EV-TOU-5 plan by every statement's own "Rate: Time of Use - EVTOU5-Residential"
header. No EV-TOU-5-specific minimum-bill dollar figure was found anywhere in the bills or in
`research/rates-reference.md`.

**NBC-on-gross re-verification.** `rates.py`'s docstring already claims NBC is billed on
gross imported kWh, never netted. This script re-derives that claim independently rather than
citing it: it locates the printed Wildfire Fund Charge line on the 9/26/25 – 9/30/25 period of
the 2025-10-31 statement (`Wildfire Fund Charge 308 kWh x $.00595 1.83`) and confirms the
printed 308 kWh matches `bill_periods_electric.csv`'s `gross_kwh` (308) for that period and
differs from its `net_kwh` (224) — CONFIRMED. `test_irreducible_bill.py` proves this is a live
re-derivation rather than a hardcoded constant by feeding a fabricated statement text with a
different kWh figure and checking the reported value moves with it.

**Output** `data/irreducible_bill.json`, written atomically; run twice → byte-identical.
Registered in `test_scripts_runnable.py` under `NEEDS_PRIVATE_ARCHIVE` (it needs the bill PDF
corpus, the same dependency shape as `parse_bills.py` and `bill_decomposition.py`), so the §9
byte-for-byte gate covers it locally.

**Tests** `analysis/test_irreducible_bill.py`, 43 cases. Thirteen run in a clean checkout with no
private archive (the PCIA sign-handling and multi-segment-summing cases against synthetic text,
the mixed-sign annual-vs-monthly minimum-bill-trigger case — built on
`group_periods_by_true_up_year()` directly with a synthetic true-up-date mapping rather than a
monkey-patched window — the synthetic package-floor-fraction arithmetic-direction case, the
gross-import sanity-check unit test, the raw-interval-CSV match-count check, the fail-closed
cases against the committed `bill_periods_electric.csv` and synthetic charge-line text, and two
cases added in a later review pass (Finding 2) that cover `_select_true_up_years()`'s two
previously-untested branches directly against synthetic true-up-year groups — zero complete
cycles found (`closest_available_approximation`, picks the group covering the most days) and no
true-up-year groups at all (`no_true_up_date_found`)); the remaining thirty — the four-bucket
arithmetic check, the netted-energy cross-check, the dual-period-statement scoping proof, the
strict-floor, package-fraction and baseline-floor consistency checks, the minimum-bill-provision
and true-up-year-grouping checks against the real corpus, the NBC-on-gross re-derivation, and the
byte-identical regeneration case — need the private bill PDF corpus and skip by name (`SkipCase`)
when it is absent, matching `test_bill_decomposition.py`'s own guard.

Two of those thirty are the regression pair the third adversarial review asked for, on the
real corpus rather than synthetic data: `case_reducing_gross_imports_reduces_non_bypassable_usd`
pins that MID's and HIGH's `non_bypassable_usd` are each strictly lower than LOW's (the direction
the artifact's own numbers already showed, now an explicit named assertion instead of something a
reader has to infer), and
`case_strictly_irreducible_usd_is_identical_across_packages_on_real_data` pins that
`strictly_irreducible_usd` is identical across all three packages despite their gross-import
totals differing — the one invariance that actually makes it a floor.

Four more are the fourth adversarial review's own additions.
`case_baseline_floor_matches_current_rate_split` and
`case_baseline_floor_differs_from_historical_split` pin Finding 1: `baseline_floor`'s split is
internally consistent, priced identically to every package's fixed term, divided into the same
current-rate `model_baseline_current_rates` total the report actually compares it against, and —
critically — numerically DIFFERENT from `twelve_month_floor`'s historical split, proving the fix
is not a no-op. `case_true_up_years_group_by_printed_true_up_date` (real-corpus,
`SkipCase`-guarded) pins Finding 2: the printed true-up dates actually separate into three
groups (2024-12-26, 2025-12-26, 2026-12-28), only the middle one is a complete cycle, and the
`SUMMARY_STATEMENTS_ELEC` rolling window neither contains nor excludes it wholly — it straddles
it, which is the observable symptom the fix targets.
`case_true_up_year_annual_sum_differs_from_naive_window_sum` pins that the true-up-year annual
sum (12,124 kWh) differs from the naive window sum (13,102 kWh) on the real corpus.
