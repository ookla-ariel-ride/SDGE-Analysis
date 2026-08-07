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

**Part 1 — arbitrage `sim(cap, pwr, name, eff=0.90, charge_pwr=None)`.** `pwr` is the
DISCHARGE cap; `charge_pwr` (issue #40, default `None` reuses `pwr`) is the CHARGE cap.
The two Tesla configs pass DIFFERENT charge rates, both from the same datasheet (see
research/battery-research-notes.md): the bare 1× PW3 passes `charge_pwr=5.0`, PW3 + 1
Expansion passes `charge_pwr=8.0` (an earlier version incorrectly applied the bare-unit
5 kW figure to both configs, contradicting the datasheet's own with-expansion rating —
Codex adversarial review caught this; empirically inert here, since 8 kW never binds
tighter than the symmetric 11.5 kW baseline for this household's data at 27 kWh, so the
corrected `net_annual_savings` reproduces the pre-issue-40 figure exactly). The
11.5 kW discharge rating is the same for both configs — only charge differs. The
Enphase configs have no cited charge rating in this project and keep the symmetric
default. Dispatch is described in §4. Simulated
configurations `(usable kWh, power kW)`: 1× Enphase IQ 5P (5, 3.84); 1× IQ 10C (10, 7.08);
1× Tesla Powerwall 3 (13.5, 11.5); 3× IQ 5P (15, 7.68); 2× IQ 10C (20, 7.08); PW3 + 1 Expansion
(27, 11.5). Output `battery_sim.json`: per config, `onpeak_offset_value`,
`forgone_export_credits`, `grid_charge_cost`, `net_annual_savings` (= offset − forgone − grid),
`equiv_full_cycles` (Σ discharge ÷ capacity). Example: 1× PW3 → offset $2,262, forgone $382,
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
   the §3.13 price-aware basis (~6.2–6.5 yr simple payback at $2,238–2,328/yr).

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
- **`threeway_production_validation.csv`** — regenerated by `analysis/threeway_production_
  validation.py` (§9 of this file; issue #37); originally an in-session computation with no
  committed generator. Date-indexed daily `pvoutput` and `enphase_meter` (each instrument's own
  daily production total, passed through from `pvoutput_daily.csv`/`enphase_daily_production.csv`
  unchanged) plus a third, DERIVED column, `meter_derived`: `sum(max(sam_hour − import_hour +
  export_hour, 0))` over each day's 24 hours, from the Enphase SAM 8760 whole-home load CT and the
  Green Button meter's own 15-minute import/export — the same identity `analysis/
  service_headroom.py`'s `derive_pv()` uses. `meter_derived` is null on the two DST transition
  dates inside the window (2025-11-02, 2026-03-08): the SAM export's flat 24-hours-a-day grid and
  the Green Button meter's real wall-clock day (25 hours fall-back, 23 hours spring-forward) do not
  align on those two dates, so no value is computed for them rather than a wrong one; all 365
  calendar rows stay in the file, and `pvoutput`/`enphase_meter` (independent instruments,
  unaffected by the SAM/wall-clock mismatch) are populated normally on both dates. Over the other
  363 days, `meter_derived` tracks `enphase_meter` at r=0.99996, MAE 0.160 kWh/day, ratio 1.0032,
  and `pvoutput` at r=0.99986, MAE 0.789 kWh/day, ratio 0.9831 — inside the ≈2.05% (ratio 1.0205)
  spread the two REFERENCE instruments already carry between themselves (`analysis/
  test_threeway_production_validation.py` proves the DST dates are excluded from these figures,
  not just visually null). Annual totals: `pvoutput` 16,839.4 kWh and `enphase_meter` 16,501.9 kWh
  over all 365 days; `meter_derived` 16,459.2 kWh over its 363 measured days (not directly
  comparable to the other two 365-day totals). These are this generator's own reproduced figures,
  not a re-derivation of the ~16,660 kWh meter-derived estimate `index.html` currently publishes
  from the retired in-session computation — whether the published figure should move to this
  script's own number is outside issue #37's scope.
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
($2,328/yr baseline marginal; $2,238/yr post-behavior marginal) — no overlap subtraction
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
15-minute dataset and bill-validated rates, except `escalation` — issue #34 found it
had no committed generator at all and was drifting toward looking like an error
rather than a documented historical comparison; `analysis/extra_results.py` now
reproduces it from the same dated constant described below, byte-identical to what
was already committed, and copies the other six keys through unchanged):

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
(13.5 kWh Powerwall 3 and 27 kWh PW3+Expansion, both 11.5 kW discharge but DIFFERENT
continuous charge caps -- 5 kW bare-unit / 8 kW with-expansion, Tesla's own datasheet,
issue #40, see research/battery-research-notes.md — 90% RTE): **evening-only**
(discharge 4–9pm; overnight top-up to 60%), **two-window** (+6–9am house load), and
**price-aware** (discharge against every non-super-off-peak import; top-up toward full in
any super-off-peak gap). Rationale: stored energy costs ~8.4¢/kWh (surplus) to ~13.9¢
(grid top-up) while all non-super-off-peak imports price at 51–87¢, so every such import
is worth serving. Ordering matters: solar surplus charges first (10am–2pm is both
super-off-peak and peak solar). EV-spillover intervals (≥2.5 kW outside on-peak) are
excluded from service. Results (`data/battery_dispatch_policies.json`): 1×PW3
$1,720 / $1,954 / $2,328 per year; 27 kWh $2,067 / $2,298 / $2,795; price-aware runs
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
cross-artifact assertion target), the committed `data/dsgs_vpp_backtest.json` (read-only —
its two union-scenario net-revenue points feed `tornado_battery.dsgs_excluded_note`'s
additive-dollar report, §3.19; NOT a tornado lever input, see §3.14/§3.19 below), and the
imported modules `behavior_rebuild.py`, `battery_dispatch_policies.py`, and `rates.py` —
plus **cited external constants** recorded with sources in
`research/extended-research-notes.md`: EIA California gasoline 12-month mean $4.65/gal,
FHWA Highway Statistics VM-1 on-road fleet economy 23.4 mpg, supercharger price estimate
$0.45/kWh (labeled estimate), SDG&E 2024 reliability report SAIDI figures, CPUC
D.24-05-028 / Resolution E-5355 (BSC $24.15/mo = $0.79343/day, matching `rates.py`
exactly). The DSGS/Tesla VPP program-terms figure ($150–350/season) that used to seed an
earlier `dsgs_revenue` lever directly is retired from this script (issue #10): that
lever's revenue points came from `dsgs_vpp_backtest.py`'s committed backtest instead
(`_load_dsgs_backtest()`, fail-closed if the artifact or its expected keys are missing —
the same read-only convention `nem3_grandfathering.py`'s `load_nbt_2039_reference()` uses
for its own sibling artifact), and the lever itself has since been removed in favor of an
additive-dollar note (issue #10 second adversarial review, Finding 1 — see §3.14 below).
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
therms/yr), `nbt_2039` (price-aware battery marginal $2,507–2,542/yr under 3–8¢ flat
exports vs $2,328 under NEM 2.0), and `tornado_battery` (payback swings: dispatch 2.2 yr >
install quote 2.1 > escalation 0.9 > EV-fix interaction 0.3 around the 6.2-yr base).
**DSGS is deliberately NOT one of these levers** (issue #10 second adversarial review,
Finding 1): every other lever varies a genuinely annual input, but the DSGS backtest is a
PARTIAL-SEASON observation (2025-07-24..2025-10-30 only), so `BATT_COST / (G +
dsgs_dollars)` would misrepresent four months of VPP revenue as a full year's recurring
figure — the exact payback-arithmetic problem CLAUDE.md §2 exists to catch, not something
a caveat fixes after the fact. An earlier version of this script computed a `dsgs_revenue`
lever this way (payback envelope 5.6–6.2 yr); it has been removed rather than re-labeled,
per CLAUDE.md's own guidance to lean toward removing a shaky calculation over defending
it. `tornado_battery.dsgs_excluded_note` reports the backtested dollars ($139.95 at 20%
reserve, primary; $199.14 at 0%-reserve sensitivity, both over the observed
2025-07-24..2025-10-30 window, not an annual figure — both read from
`data/dsgs_vpp_backtest.json` at runtime, never hardcoded) as an ADDITIVE amount on top of
the arbitrage payback above, once a full season is measured, and points to
`per_aggregation_sensitivity` for the range across individual aggregation schedules — never
as its own payback-year lever (see §3.19). Figures derived purely from external estimates (outage-hour exposure) carry
**estimated** pills in the report; artifact-derived ones, including DSGS VPP revenue since
issue #10, carry **modeled**/**measured** per source. The report's "What to do Monday"
appendix is **content-only** — it cites §5/§6/§9/§13 figures and introduces no new
artifacts.

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
`canonical_crosscheck_ev_tou_5` block ($4,904 no-battery / $2,328 battery value from
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
4,968 kWh/yr against Run B's 2,394), so this does not isolate a pure objective effect at
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
  Unlike Run A, Run B also declines to store solar surplus during a dirty hour rather than
  exporting it (an adversarial review finding): storing it instead of exporting forgoes that
  hour's own (large, dirty-hour) export credit for an uncertain future discharge credit, and
  after round-trip losses that trade is not guaranteed to pay off even though the later
  discharge is also, separately, classified dirty — measured directly on the real committed
  year, 14.6% of solar-surplus intervals are themselves above the charge threshold, not a rare
  edge case. Run A has no analogue to this risk (price has no equivalent "forgone credit"
  failure mode). Run B also uses a SEPARATE, higher threshold for discharge than for charge
  (a second adversarial review finding, the same underlying issue in a different guise):
  charging c kWh delivers only c·ETA² (~0.9, the round-trip efficiency) on later discharge, so
  a single shared threshold lets a near-threshold pair straddle the break-even line — e.g.
  charging at 184 kg/MWh and discharging at 186 with a 185 threshold nets MORE emissions
  (186 < 184/0.9 = 204.4), even though each side individually passed its own dirty/clean test.
  The discharge threshold (charge threshold ÷ ETA² = 185.0 ÷ 0.9 = 205.6 kg/MWh here)
  guarantees every allowed charge/discharge combination is net non-negative for carbon,
  regardless of which specific charged kWh a pooled (non-FIFO) state of charge later delivers
  at which specific discharge — proved directly from the arithmetic in
  `test_carbon_dispatch_tradeoff.py`, not merely asserted.
- **Run C (union/efficient, new).** Discharges whenever either Run A's or Run B's condition
  holds; grid-charges only when both cheap AND clean hold. This isolates the genuinely
  conflicting hours (cheap-but-dirty, clean-but-expensive) from the hours both objectives
  would serve anyway.

**Run B's charge threshold is derived, not invented.** Its charge window is sized to the same
fraction of the year as Run A's non-super-off-peak discharge window, measured directly from
this household's own TOU assignment (`rates.period_at` via `behavior_rebuild.load()`) rather
than hardcoded — so the comparison isolates *which* hours get served, not how many. Charge
threshold = the intensity value at that fraction's quantile of the year's per-interval
intensity array: 185.0 kg/MWh. Target clean/dirty split 46.74%/53.26%; achieved 46.75%/53.25%
(ties at the underlying data's 0.1 kg/MWh resolution keep the achieved split close to, not
exactly on, the target). The discharge threshold (185.0 ÷ 0.9 = 205.6 kg/MWh) is derived from
that charge threshold, not independently fit, so it carries no separate free parameter.

**Net, not gross, CO₂.** The three policies consume different amounts of exportable solar via
battery charging (Run A's own solar-charging displaces 827.1 kg/yr of exports, Run C's only
601.4), so ranking policies on gross import CO₂ alone silently drops that difference and can
invert which policy is actually cleaner for the atmosphere — an adversarial review caught
exactly this: the gross-import figures published in an earlier draft ranked Run C as cleaner
than Run B (4,826.4 vs 4,917.9 kg), but net accounting (import minus that policy's own
export-avoided) reverses it (Run B is actually cleaner). Every comparison below is therefore
NET; gross import and gross export-avoided are still reported per policy in the artifact as a
breakdown, never discarded.

**Results** (all figures against the no-battery baseline: $4,904.13/yr, 4,487.2 kg net
CO₂/yr): Run A saves $2,328.31/yr but *raises* net CO₂ by 442.0 kg/yr above the baseline —
grid-charging during super-off-peak means charging during the year's dirtiest hours (270.1
kg/MWh overnight vs. 158.4 on-peak, §3.15's own window means). Run B avoids 244.1 kg/yr net
but keeps only $145.19 of the saving. Run C recovers $1,818.69/yr (78% of Run A's saving)
while still avoiding 181.1 kg/yr net — 63.0 kg/yr less than Run B, a real but small carbon
cost for capturing roughly 12.5 times more of the dollar saving. Run C is still judged a
genuinely distinct third outcome (not merely a blend reducible to A or B) by requiring BOTH
its $ and its CO₂ to be within 2% of a policy's own figures to count as "not meaningfully
different": its bill sits 35.2% from Run B's, so the test fails against B even though its net
CO₂ now sits within 1.5% of Run B's own.

**Tradeoff figures.** Cost penalty of the clean policy (Run B's bill minus Run A's bill):
$2,183.12/yr. CO₂ penalty of the cheap policy (Run A's net CO₂ minus Run B's net CO₂): 686.1
kg/yr. Both are also expressed per kWh cycled (59.3¢/kWh and 0.186 kg/kWh respectively),
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

### 3.18 `analysis/nem3_grandfathering.py` — NEM 2.0 grandfathering value + battery-marginal reconciliation (`data/nem3_grandfathering.json`)

**Purpose.** Two related questions, both re-billed on the same measured 15-minute year:
what is this household's NEM 2.0 grandfathering actually worth against SDG&E's real Net
Billing Tariff (NBT, "Solar Billing Plan") export pricing (rather than the flat 3–8¢/kWh
assumption used elsewhere), and — added in a later phase — how does the price-aware
battery's own marginal value under that same real hourly schedule compare with
`extended_findings.py`'s existing flat-credit `nbt_2039` figures.

**Rate table (`data/nbt_export_rates_2026.csv`, public).** Built from SDG&E's own published
MIDAS export-pricing files (`--build-rates`, needs the private raw archive at
`private/1-raw-data/sdge_nbt_export_rates/`; the normal run needs only the committed CSV).
Two vintages are priced for TARIFF_YEAR = 2026 only — NBT26 (a 9-year *escalating* rate
schedule, of which only year 1/2026 is priced here) and NBT00 (no-lock, current-year-only) —
found to be byte-identical in this one year (see the script's own "GENUINE FINDING" docstring
note). A Codex review finding, corrected: an earlier version of this note said NBT26 "locks
this table" flat for 9 years; the raw NBT26 archive actually shows genuinely escalating rates
year over year (this household's own "Jan Weekend HS0" Generation rate: $0.087115/kWh in 2026,
$0.090474/kWh in 2027), so NBT26 locks a 9-year *schedule*, not a repeated snapshot — this
script prices only that schedule's first year, as an apples-to-apples comparison against
NBT00's own single-year guarantee, not a claim about the full 9-year path. Because both
vintages' YEAR 1 happens to coincide, the VINTAGE component of the grandfathering-value band
has zero width for now (`vintage_band_usd_per_yr` in the artifact) — the authoritative
published band is not zero-width, though; see the generation-component sensitivity below for
where its actual width comes from (a separate Codex review finding: an earlier version of this
script scoped the published band to vintage only, silently excluding that real uncertainty).

**Export credit = SDG&E Delivery + Generation + a flat $0.01/kWh CEA "Solar Impact" bonus,
with the Generation component disclosed as a genuine, unresolved ambiguity (an adversarial
review finding), not silently assumed.** This household is on a CCA (Clean Energy Alliance),
and SDG&E's own export-pricing methodology page states its Generation component is
"applicable only to bundled customers" — not CCA customers. Against that, CEA's own "Solar
Impact" program page states, unhedged, that its export credit equals "the same export credit
pricing paid by SDG&E" plus the $0.01/kWh adder, with no component breakdown; no CEA document
spelling out a separate NBT-specific generation-credit rate was found. The script uses CEA's
direct statement as the primary assumption (the most specific evidence for what this
household's account actually receives) but publishes a Delivery-only alternative alongside
it (`generation_component_sensitivity` in the artifact) so the reader sees the range this
ambiguity creates: $2,103.58/yr (primary) vs $2,455.64/yr (Delivery-only) — removing the
Generation credit raises the NBT bill and widens the grandfathering value, so the primary
figure is the more conservative (lower) of the two. Import side stays gross-billed at
`rates.allin()` (no monthly netting) — the same assumption `extended_findings.py`'s own
`bill_flat_export()` already uses for its flat-credit NBT proxy.

**Grandfathering result.** NEM 2.0 modeled bill $4,904.13/yr vs NBT counterfactual
$7,007.70/yr (both vintages) → **$2,103.58/yr** grandfathering value (computed from the
full-precision bill figures before rounding either total to display precision — subtracting
the two rounded totals shown here gives $2,103.57, one cent off; the artifact's own stored
value, rounded once at the end, is the correct one), kWh-weighted realized
export credit ~4.7¢/kWh (this household's exports concentrate at midday, exactly where the
real hourly schedule is cheapest). Same order of magnitude as the retired flat-cent bracket
in `data/extra_results.json → nbt.gf_value` ($1,772–2,268/yr, issue #34, not touched by this
script), now a single computed, traceable figure instead of an assumed sensitivity range.

**Battery-marginal reconciliation vs `extended_findings.py`'s `nbt_2039` (issue #9 AC6).**
`extended_findings.py`'s `nbt_2039` block prices the price-aware battery's marginal bill
savings (no-battery bill minus with-battery bill, `bp.run_batt(d, imp0, gen0, 13.5,
"greedy", charge_kw=bp.CHARGE_KW)`) under three FLAT export-credit assumptions: $2,542/yr at 3¢, $2,528/yr at 5¢,
$2,507/yr at 8¢, against $2,328/yr under NEM 2.0 today. This script adds the same marginal
priced against the REAL hourly NBT schedule for TARIFF_YEAR = 2026 only (a snapshot, not a
projection to 2039 or any other future year — a Codex review finding, corrected: an earlier
version of this section, and of `index.html`, presented this 2026-rate figure under a
"planning for 2039" framing, which overclaimed precision this script does not have; NBT26's
own true 9-year schedule escalates, and 2039 is 13 years beyond even that lock period), reusing
the identical `bp.run_batt` dispatch (so only the export-pricing assumption differs, never the
physical battery behavior) and billing both the no-battery and with-battery series through
its own `bill_nbt()`. Result: **$2,522.61/yr** for both vintages (NBT26/NBT00 year-1 figures
again coincide) — **+$194.61** above the NEM 2.0 figure (confirming the existing nbt_2039
finding that the battery is worth more once exports price at NBT rather than near-retail), and
**inside** the existing flat 3–8¢ bracket ($2,507–2,542/yr), landing within $5.39 of the 5¢
figure specifically (vs −$19.39 from the 3¢ figure and +$15.61 from the 8¢ figure). The
disagreement is stated explicitly for each reference point in the artifact
(`battery_marginal_reconciliation_vs_nbt_2039 → disagreement_vs_reference`), not averaged or
hidden: for this household's measured export shape, the real hourly schedule happens to land
close to the middle of the flat-cent bracket that was always meant as a placeholder for it, so
the two methods corroborate rather than contradict each other at today's rates. `extended_
findings.py` and its `nbt_2039` figures are read-only from this script's perspective
(`json.load`, never recomputed) — issue #34 owns any future change to that generator.

**Fail-closed design.** `load_rate_table()` and `bill_nbt()` abort (`SystemExit`) on any
(month, day-type, hour) bucket the household's data touches that the committed rate table
does not cover, never interpolating or zero-filling. `load_nbt_2039_reference()` similarly
aborts if `data/extended_results.json` or its `nbt_2039` section (or one of its 3c/5c/8c
buckets) is missing — a stale or absent reference must not be silently treated as "nothing to
reconcile against." The CSV and JSON outputs are both written to temp files and `os.replace`d,
so a partial/failed run changes neither.

**Inputs and provenance.** `usage.csv` (Green Button) via `behavior_rebuild.load()`, the
committed `data/nbt_export_rates_2026.csv`, `data/electric_bill_summary.csv` (actual-billed
anchor, context only), `data/extended_results.json` (the `nbt_2039` reference, read-only),
and the imported `battery_dispatch_policies` module (`bp.run_batt`, not re-implemented). Run
from `private/verify` with `usage.csv`, `behavior_rebuild.py`, `battery_dispatch_policies.py`
and `rates.py` beside it; writes `data/nem3_grandfathering.json`.

### 3.19 `analysis/dsgs_vpp_backtest.py` — DSGS VPP revenue backtest against the real 2025 event calendar (`data/dsgs_vpp_backtest.json`, `data/dsgs_event_calendar_2025.csv`)

**Purpose (issue #10).** §6's DSGS revenue figure used to be a program-terms extrapolation
("estimated · program terms cited", ~$150–350/season from Tesla's own marketing). This
script replaces it with a backtest: the REAL 2025 DSGS Option 3 event calendar, replayed
against this household's own measured 15-minute load and solar, for a HYPOTHETICAL
Powerwall 3 (this household owns no battery today). Every figure in the artifact is
labeled hypothetical for that reason.

**Source data.** California Energy Commission docket 22-RENEW-01: TN 269155 (filed
2026-03-12, "Anonymized Data - Staff Analysis of the DSGS 2025 Program Performance",
`private/1-raw-data/dsgs_events/dsgs_2025_performance.xlsx` — Data Dictionary, Monthly
Aggregation Dataset, and a 361,008-row Hourly Discharge Dataset covering the full May–Oct
2025 season) and TN 266629 (filed 2025-10-16, "Staff Analysis of the DSGS Program 2024
Performance Data", pages 10/20/23, for the second program year's event list). Public CEC
policy data, not personal to this household — same commit reasoning as §3.18's rate table.

**Genuine finding: no real 2025 emergency dispatches.** The Data Dictionary states outright
that the "Capacity" (LMP-triggered) and "Energy-Only" (day-of EEA) event types had zero
occurrences in 2025; every event hour that season is "Test Capacity" or "Test
Non-Capacity" — a mandatory monthly test, not an actual grid emergency. Confirmed
independently against the raw 361,008-row dataset, not just quoted from the dictionary
text.

**Disclosed ambiguities (checked, not silently resolved):**
- **UDC identity.** The file anonymizes utilities as "UDC 1"–"UDC 4"; UDC2 is inferred as
  SDG&E by 2025 enrollment scale (82,776 site-months — third largest, plausible for SDG&E's
  smaller territory) and cross-checked against TN 266629's 2024 analysis, which uses real
  utility names and shows the same relative ordering (PG&E > SCE > SDG&E > LADWP) one year
  earlier. Corroborating, not proof — anonymization is anonymization.
- **Aggregation union, an upper-bound SCENARIO, not the headline.** Restricted to
  UDC2 × Residential × Stationary × 2-hour resource duration (the category matching a
  residential Powerwall-class battery), ~14 distinct anonymized aggregations each run their
  own monthly test on their own schedule; a real household sees only ONE aggregation's
  hours, not all of them. The committed calendar uses the UNION of all distinct (date,
  hour) event slots across that category (68 slots; 16 with aggregations disagreeing on
  event type, resolved by majority vote, ties toward "Test Capacity") — deliberately
  inclusive, so it overstates event frequency rather than guessing at one arbitrary
  aggregation's schedule. Because a real household belongs to exactly one aggregation,
  `per_aggregation_sensitivity()` independently re-runs the SAME backtest against each of
  the 14 individual aggregations' own calendars and reports the resulting range (see
  "Revenue, net" below) — the union-based gross/net/kWh/miss-rate figures are relabeled as
  that range's inclusive upper-bound scenario, not an unqualified point estimate.
- **Payment-rate source.** Primary: EMPIRICAL — each month's "Monthly Capacity Payment ($)"
  ÷ "Demonstrated Capacity (MW)" across UDC2/Residential/2-hr rows with positive
  demonstrated capacity, essentially constant within a month (<0.03% relative spread).
  Tesla's $150–350/season program-terms figure is kept only as an order-of-magnitude sanity
  check (same commit as §6's retired framing), not the rate source.
- **Event-hour-only reserve floor, decided (issue #10 second adversarial review, Finding
  3).** The issue text asserted that "§6's outage work establishes what reserve the
  household would plausibly hold" — checked against `battery_backup_sims.py` and
  `extended_findings.py` and found NOT TRUE: no numeric reserve fraction exists anywhere in
  this repo, and the outage-endurance sims assume the OPPOSITE convention (a full battery
  at outage start, no reserve withheld). `BACKUP_RESERVE_FRAC = 0.20` here is therefore an
  assumed, uncited operating parameter, with a 0%-reserve sensitivity computed alongside it
  so the report can see how much the assumption matters. It is enforced ONLY during a
  declared DSGS event hour (both the ordinary and event-forced discharge are capped against
  it then; see Dispatch model below) — NOT a continuously-held standing backup-reserve
  setting: ordinary, non-VPP arbitrage in the hours before an event is unaffected, so a real
  Powerwall's own always-on reserve setting would leave LESS charge actually available by
  the time a declared event starts than this event-hour-only figure implies. Decided in
  favor of this event-only scoping rather than a standing floor: it matches the issue's own
  framing ("a backup reserve reduces dispatchable capacity DURING events"), keeps the tested
  empty-event-set byte-identity guarantee to `battery_dispatch_policies.run_batt` intact
  (which a standing floor would break), and avoids introducing an unjustified new constraint
  on the household's own everyday no-VPP arbitrage. Every mention of this parameter — in
  the artifact's `backup_reserve_caveat`/`miss_rate.note` fields, this document, and
  `index.html` — is labeled "event-hour-only" for this reason.
- **2026-season enrollment eligibility, corrected (issue #10 third adversarial review).**
  An earlier version of this finding read Olivine's FAQ paraphrase — "participation in the
  2026 season is limited to storage VPP aggregators that participated in October 2025" — as
  a HOUSEHOLD-level bar, concluding a new storage enrollment "could not join at all." Checked
  against the authoritative source directly (CEC DSGS Program Guidelines, Fifth Edition, TN
  269649, Section II.C.1 + Appendix A) and found that reading UNSUPPORTED: the restriction
  freezes which AGGREGATORS may receive 2026 funding at all, not whether a new household's
  battery can join an aggregator that already qualifies. What the Guidelines DO additionally
  establish (Appendix A): each qualifying aggregator's total 2026 compensation is CAPPED at
  its own October-2025 pro-rata share of program funds, so enrolling new sites doesn't
  increase what the aggregator gets paid — a real economic disincentive, not a rule against
  it. Whether a specific aggregator (Tesla is a listed Option 3 provider generally) both
  participated in October 2025 and would accept a new residential site under that funding
  cap is NOT DETERMINED from the public, anonymized CEC data — this dataset cannot identify
  which real aggregator this household's utility corresponds to. The earlier "could not join"
  claim is retracted, not softened, in the artifact, §6, and GLOSSARY.md.
- **Grandfathering interaction, searched and nothing found.** The issue asks separately
  whether DSGS ENROLLMENT (distinct from mere battery ownership) affects NEM 2.0
  grandfathering. Searched the authoritative source directly: the CEC's DSGS Program
  Guidelines, Fifth Edition (89 pages, TN 269649) — zero occurrences of "net energy
  metering", "net billing", "NBT", or "grandfath"; its 4 "NEM" hits are all the unrelated
  term "VNEM" (Virtual Net Energy Metering, a multi-tenant billing arrangement). Olivine's
  FAQ has no NEM mention either. Consistent with `nem3_grandfathering.py`'s (issue #9,
  §3.18) own finding that NEM 2.0 forfeiture (SDG&E tariff Schedule NEM Special Condition
  7(b), D.16-04-020) is triggered by ADDING generating/storage equipment past a nameplate
  threshold, not by enrolling existing equipment in a demand-response program — a searched,
  sourced absence, not an assumption.

**Dispatch model.** `run_batt_vpp()` mirrors `battery_dispatch_policies.run_batt()`'s
"greedy" policy exactly for non-event intervals (asserted byte-identical against an empty
event set in `test_dsgs_vpp_backtest.py`) and, only during a real 2025 DSGS event hour
inside this household's measured window, forces discharge up to the greater of the
household's own load and its remaining headroom above the reserve floor and the 11.5 kW
power cap — the behavior a revenue-maximizing VPP aggregator actually commands. An event
hour where SOC is already at or below the reserve floor is a MISS (SOC-constrained,
counted, not hidden). The reserve floor binds on the WHOLE event hour, not just the
event-forced increment: the ordinary/BAU-equivalent greedy discharge is ALSO capped at the
reserve floor during a declared event hour (an earlier version capped only the
event-forced portion, letting the ordinary branch draw straight through the floor —
confirmed against the real dataset to breach the floor in 38 of 46 in-window event hours,
worst case draining to 0 kWh; fixed and covered by a regression test with real,
non-zero house load, since the zero-load fixtures used elsewhere never exercised the
ordinary branch during an event hour at all).

**No charging from solar surplus during a declared event hour (issue #10 second
adversarial review, Finding 2).** The export-charging branch is now skipped whenever
`is_event[i]` is true, regardless of `disch_win`/`imp[i]`. Without this guard, an earlier
version could charge the battery from solar surplus (the branch fires whenever
`exp[i] > 0` and `imp[i] == 0`, which the reserve-floor fix above didn't touch) and then
IMMEDIATELY discharge that same energy again via the event-forcing block a few lines
below, in the SAME interval — round-tripping solar that would have exported directly
anyway (at an `ETA` round-trip loss) while crediting the full amount as new
"event_discharge" as if it were genuinely new battery output. Confirmed against the real
2025 backtest before the fix: 12 intervals showed both a charge-from-solar and an
event-forced discharge in the same interval, totaling 5.32 kWh charged and 25.52 kWh
event-discharged in those intervals alone (union-scenario total event discharge fell from
144.67 kWh to 139.88 kWh once the round-trip was removed, before the reserve-scenario
totals below also shifted from the resulting change in dispatch order). Fixed by adding
`and not is_event[i]` to the export-charging branch's condition; solar surplus during an
event hour now passes straight through to export, and the event-forcing block still
maximizes discharge from whatever SOC already exists. Covered by a regression test
(`case_solar_surplus_during_an_event_hour_does_not_round_trip_through_the_battery`) built
on the same three-prior-hours-of-real-load fixture as the reserve-floor regression above,
with a solar surplus and zero concurrent consumption declared during the event hour —
exactly the condition that triggered the bug.

**Revenue, net — union-calendar scenario (upper bound, not the headline).** GROSS = the
empirical $/kW-month rate × this household's monthly LMP-weighted demonstrated capacity
(Test Capacity hours only, per the Data Dictionary's own rule; demonstrated capacity nets
a prescriptive baseline derived the same empirical way, ~10.8% of nameplate), summed over
the participation months inside the measured window that are NOT excluded as partial
(see below), over the observed 2025-07-24..2025-10-30 window (not an annual figure):
**$128.47**. OPPORTUNITY COST is COMPUTED, not assumed — `rates.bill_nem` re-billed for
the full measured window with vs without the VPP dispatch modification (the same
"re-bill the modified year" technique the rest of this repo's battery/behavior work uses,
CLAUDE.md §1b), computed from a dispatch run that excludes any partial month's
event-forcing (see "Partial calendar months" below, and its own note on why) — and came
out small and slightly NEGATIVE (**−$11.48**): DSGS event hours fall inside 4–9pm on-peak,
already this household's highest-value discharge window under ordinary price-aware
dispatch, so the extra forced export there mostly draws down SOC that would otherwise have
been used in cheaper off-peak/super-off-peak hours (refilled overnight anyway) rather than
costing expensive on-peak service later — a real, computed NEM-netting effect specific to
this household's usage pattern, not an assumption. NET = **$139.95** at the 20% reserve
(primary); a 0%-reserve sensitivity gives $187.78 gross, −$11.36 opportunity cost,
**$199.14** net. AC4's kWh figure: **182.19 kWh** delivered across the 46 in-window event
hours at 20% reserve (241.11 kWh at 0% reserve) — `revenue.<scenario>.total_discharge_kwh`
in the artifact (from the FULL event set, including any partial month, since AC2 requires
every in-window event replayed), event-forced discharge (which `run_batt_vpp` routes
entirely to export) plus any concurrent ordinary/BAU discharge that hour, matching the
CEC's own Net Discharge crediting basis rather than a narrower grid-export-only figure (see
the artifact's `total_discharge_kwh_note`). These figures reflect the Finding-2 fix above
(no charging from solar surplus during a declared event hour) on top of the reserve-floor
fix: total discharge at 20% reserve fell from 186.98 kWh to 182.19 kWh once the
round-tripped solar was removed, gross/net revenue fell further (to $128.47 gross) once
July -- a month with event hours on both sides of the measured-window boundary -- was
excluded from monthly_gross_usd entirely (Codex review Finding 2, see "Partial calendar
months" below), and opportunity cost changed again (from -$14.16 to -$11.48 at 20%
reserve) once a THIRD review round found it was still drawing on July's dispatch effect
even after July's revenue was zeroed (fixed by computing it from a priced-months-only
dispatch run instead). Miss rate: 24 of 46
in-window event hours (52.2%) at 20% reserve vs 17 of 46 (37.0%) at 0% reserve — the
expected direction (the tighter reserve leaves less headroom for both ordinary and
event-forced discharge, so more hours fall short of the 1 kWh miss threshold; see the
artifact's `miss_rate.note`). Miss rate is unaffected by the partial-month exclusion:
it measures whether the battery could serve an event hour that actually exists, a
question the July in-window hours can still answer even though their revenue cannot be
validly priced.

**Partial calendar months, priced as $0 rather than from an incomplete subset (Codex
review Finding 2).** DSGS's own "Monthly DC" (Demonstrated Capacity) is defined as an
LMP-weighted average over ALL of an aggregation's event hours in a calendar month. July
2025 has event hours on BOTH sides of this household's measured-window boundary — one
pre-window test (2025-07-22) and three in-window tests (2025-07-29..31) — so the
household's own measured load exists for only 6 of July's 8 real event hours. Pricing
that incomplete 6-hour subset at July's full published $/kW-month rate would misrepresent
a partial month as a complete settlement (confirmed directly: July's in-window-only
demonstrated capacity, priced at the full rate, would have contributed $36.87 to gross
revenue — about 22% of the previous $165.34 total). Fixed by excluding any month with
event hours on both sides of the window boundary from `monthly_gross_usd`/net revenue
entirely, in both reserve scenarios and in `per_aggregation_sensitivity` (each
aggregation's own calendar is checked independently, since a different aggregation's July
schedule could in principle be entirely in- or out-of-window). July's in-window hours
still appear in `hour_detail` and count toward the miss rate — that dispatch simulation is
unaffected, only the monthly capacity PAYMENT is invalid for the incomplete month. Which
month(s) were excluded, and why, is disclosed in the artifact's `partial_months_note`.

A follow-up review round found this fix was incomplete: `run_batt_vpp()`'s event-forced
dispatch for July still ran against the FULL event set, so July's own bill effect was
still baked into the single `opportunity_cost` figure netted against gross revenue — even
though July's gross revenue was now $0, its bill impact was NOT, silently overstating net
revenue (confirmed directly: -$14.16 full-event-set opportunity cost vs -$11.48 once
July's dispatch effect is excluded, at 20% reserve). Fixed with a SECOND dispatch run
(`event_set_priced`, excluding any partial month) used exclusively for the opportunity
cost feeding `net_revenue`/`net_revenue0`, while the FULL event set (including July) is
still used for `hour_detail`/`miss_rate`/`total_discharge_kwh` — AC2 requires every
in-window event to be replayed, and that requirement and the revenue-consistency
requirement pull in different directions unless the two are computed separately, as here.

**Revenue, net — per-individual-aggregation range (the real range a household could see).**
`per_aggregation_sensitivity()` isolates each of the 14 individual aggregations in the
UDC2/Residential/Stationary/2-hour category (grouping the same Hourly Discharge Dataset
rows by "Aggregation Identifier (anonymized)" instead of unioning them) and re-runs the
identical `backtest()` pipeline against each one's own calendar, at the same 20% reserve.
Across all 14, over the same observed 2025-07-24..2025-10-30 window: **net revenue
$96.99–$213.19**, **miss rate 50.0%–60.0%** — this is
the range a real single-aggregation household could actually have earned, not the union
figure above, which sits inside this range rather than bounding it from above (a household
on a smaller, better-timed aggregation calendar can net MORE than the union: fewer event
hours can mean less opportunity cost without proportionally less demonstrated capacity).
Committed in the artifact's `per_aggregation_sensitivity` field (`net_usd_min/max`,
`miss_rate_min/max`, and the full 14-row `per_aggregation` breakdown), computed fresh from
the private raw archive on every regeneration — not scaled from the union figure by a
fraction.

**Coverage gaps, disclosed rather than filled — this is a PARTIAL SEASON, not a complete
one.** This household's measured window starts 2025-07-24, so 22 May–July 2025 event
hours are outside it and get zero revenue attributed — a data-availability gap, not
extrapolated. Every gross/net/kWh/miss-rate figure above therefore covers only
2025-07-24 through 2025-10-30, roughly the back two-thirds of the May–October 2025 season,
NOT a complete season and NOT an annualized figure — stated explicitly in the artifact's
`partial_season_caveat` field. A full season would very likely earn MORE (it only adds
event hours relative to the partial figure here), but a full-season or annual revenue
figure is **NOT DETERMINED** — it is not extrapolated from the partial figure, because no
measured load exists for the missing May–July hours in any year this household has been
metered. Per CLAUDE.md §2's payback-honesty standard, this figure is therefore never
combined with a full year of arbitrage savings to produce a payback-year claim —
`extended_findings.py`'s `tornado_battery.dsgs_excluded_note` (§3.14) reports it only as
an additive dollar amount on top of the arbitrage payback, once a full season is measured.
The 2026 DSGS season's tail overlaps the window,
but the CEC had not published 2026 performance data as of this run (2025's data itself was
not filed until March 2026) — also zero revenue attributed, for the same reason.
The 2024 event list from TN 266629 (three statewide 2-hour events on 2024-07-10,
2024-09-04, 2024-09-05, plus an aggregate 26 event-hours/16 days) satisfies the
second-program-year requirement but contributes no revenue either way: it predates this
household's measured window regardless of whether a rate could be found for it, and in
fact no 2024 per-event payment rate is published (TN 266629 states the rate "varies by
month" without printing the figures) — disclosed as **not determined** for 2024
specifically, never guessed from 2025's empirical rate.

**Reconciliation with §6.** The retired estimate's $150–350/season range and this
backtest's $128.47 gross for the ~3 in-window, fully-priced participation months (August,
September, October — July is excluded as a partial month, above) are the same order of
magnitude (a partial season at the program-terms rate) — the sanity check the module's
own docstring sets out to perform. `extended_findings.py`'s `tornado_battery` no longer
turns this into a payback-year figure at all (issue #10 Finding 1, §3.14):
`dsgs_excluded_note` reads this artifact's two union-scenario net-revenue figures
read-only ($139.95 primary, $199.14 0%-reserve sensitivity, both over the observed
2025-07-24..2025-10-30 window) and reports them as an
ADDITIVE dollar amount on top of the arbitrage-only `base_payback_yr` (6.2 yr), never
combined into a blended payback year — an earlier version computed `BATT_COST / (G +
dsgs_dollars)`-style payback points from this same partial-season input (envelope 5.6–6.2
yr), which silently treated four months of measured VPP revenue as if it recurred all
year; removed rather than re-labeled, per CLAUDE.md's own guidance to lean toward removing
a shaky calculation over defending it. Across the 14 individual aggregation schedules
(`per_aggregation_sensitivity`), the additive DSGS dollars run $96.99–$213.19 over that
same window — the
range a real single-aggregation household could see on top of its own arbitrage payback,
not a payback-year figure in its own right.

**Event-aware SOC pre-staging, an ADDITIVE sensitivity (issue #53) — decided: model it.**
`run_batt_vpp()` above dispatches REACTIVELY: it only changes behavior once `is_event[i]`
is true for the CURRENT interval, with no advance knowledge of an upcoming scheduled event.
But DSGS's monthly "Test Capacity"/"Test Non-Capacity" tests ARE scheduled in advance —
`data/dsgs_event_calendar_2025.csv` exists as committed data for exactly that reason, and
this section's own earlier finding (2025 had zero real emergency dispatches) means every
real 2025 event this household could have seen was one of these calendar-published monthly
tests, not a surprise. Since DSGS capacity payments are based on demonstrated performance
DURING the test, a rational, revenue-motivated household has a genuine financial incentive
to plan around a known calendar rather than dispatch reactively and hope enough charge is
left. Decided in favor of modeling it: this is a pure dispatch-logic question over data
this script already reads (`event_set`, built from the same committed calendar above), not
a new data source, and quantifying how much foresight actually matters is itself a useful,
evidence-based finding for the report regardless of which way the delta cuts.

**What "known in advance" is actually confirmed, not assumed (Codex adversarial review,
issue #53, third pass).** This household's own DSGS category is Option 3, Storage VPP (the
module docstring's UDC2/Residential/Stationary/2-hr filter). The CEC's own published DSGS
Option 3 program FAQ (`dsgs.olivineinc.com/faq/`, checked 2026-08) confirms: "VPP
aggregators must notify the CEC of planned test events no later than 3:00 p.m. on the day
preceding the planned test event" — so the aggregator itself has AT LEAST day-ahead
knowledge of its own scheduled test (it is the one scheduling it). What that same source
does NOT document is whether, or how far ahead, an individual enrolled customer is notified
by their aggregator. Same-day household-level awareness — the assumption this sensitivity
actually models — is therefore a MODELED assumption resting on a confirmed fact one link up
the chain (the aggregator plainly knows), not a confirmed real notice chain reaching the
household. This is a materially weaker claim than "the household is scheduled in advance"
read as a household-level fact, and this section's language has been corrected to say so
explicitly rather than let the aggregator-level citation stand in for a customer-level one.

**The rule, independently re-verified before choosing it.** All 34 distinct 2025 event
DATES have their 1–2 event HOURS entirely within `hour_end` 17–21 (`floor_hour` 16–20) —
squarely inside `run_batt_vpp()`'s own `disch_win` window (`16 <= h < 21`), the SAME window
the reactive path already treats as ordinary peak-arbitrage-discharge territory. So on an
event day, the reactive dispatch can spend SOC on ordinary bill-arbitrage discharge in the
peak hours strictly BEFORE that day's event hour(s) arrive, potentially leaving less SOC
available once the event-forcing block tries to maximize demonstrated capacity. The rule:
on any calendar date with at least one hour in `event_set`, suppress the ordinary
`disch_win`-triggered arbitrage-discharge branch — and ONLY that branch, never the
event-forcing block — for hours on that date strictly BEFORE the date's first event hour.
From the first event hour onward (including the event hour itself), dispatch is completely
unchanged from the reactive path; a date with no event in `event_set` is entirely
unaffected. No multi-day lookahead is implemented or needed — the issue's own framing, and
the calendar's own same-day shape confirmed here, are both about a known SAME-day test, not
a household planning multiple days ahead.

Mechanically, a new `run_batt_vpp(..., prestage=False)` keyword (default `False`,
preserving every existing call site and the tested empty-event-set byte-identity guarantee
to `battery_dispatch_policies.run_batt` exactly) gates only the `disch_win` branch's own
ACTIVATION via a separate `disch_win_active` flag; the solar-surplus charge branch's
condition still reads the true, unsuppressed `disch_win`, so a suppressed interval with
house import and no solar surplus simply takes no dispatch action at all that interval
(the household pays ordinary retail import price, same as if it had no battery for it).
`backtest()` calls `run_batt_vpp(..., prestage=True)` a SECOND time, alongside (not instead
of) the existing reactive calls, at the same primary 20% reserve and the same
priced-months-only opportunity-cost split already used for the reactive figures, and embeds
the result in a new, additive `prestaged_sensitivity` field — every existing `revenue`/
`miss_rate`/`hour_detail` key is untouched (verified: a fresh `backtest()` run reproduces
`data/dsgs_vpp_backtest.json`'s pre-existing `reserve_20pct`/`reserve_0pct_sensitivity`/
`hour_detail` sections byte-for-byte, `case_prestaging_leaves_committed_reactive_figures_
untouched`).

**The delta vs. reactive: foresight is worth a real amount here, not a rounding error, but
it shows up as more revenue per served hour, not as fewer misses.** Over this household's
real 2025 event calendar and measured load, pre-staging raises net revenue from $139.95 to
**$176.82** (+$36.87, +26.3%) and gross revenue from $128.47 to $176.96 (+$48.49), with
opportunity cost falling from −$11.48 to **$0.14** (pre-staging trades away some
off-peak-hour arbitrage savings the reactive path was capturing, which very nearly
cancels out against the bill impact of the extra event-hour export). Total delivered
discharge across the 46 in-window event hours rises from 182.19 kWh to 235.65 kWh
(+53.46 kWh, +29.3%). The miss rate, by contrast, moves only marginally: 23 of 46 misses
(50.0%) vs the reactive path's 24 of 46 (52.2%) — a single additional event hour served,
not a materially lower miss rate. The mechanism: pre-staging can only ever leave SOC
entering an event hour greater than or equal to the reactive path's SOC at that same
moment (proved by induction over the shared, monotone-nondecreasing charge/discharge
update rule both paths use — confirmed empirically too: the pre-staged miss count never
exceeds the reactive miss count on this dataset, `case_prestaged_sensitivity_is_additive_
and_internally_consistent`), so it mostly helps event hours that were already delivering
SOME demonstrated capacity deliver MORE of it, rather than converting a fully
SOC-exhausted miss into a hit. Computed, not assumed either way — see the artifact's own
`prestaged_sensitivity.delta_vs_reactive.note` for the same figures in narrative form.
These figures are **MODELED**, not measured: a real household's actual pre-staging
behavior (if any) was never observed, since this household owns no battery today: it's an
alternative dispatch POLICY replayed against the same real load/calendar the reactive
backtest already uses, exactly as reactive figures are modeled against the same inputs.

**These are UNION-CALENDAR figures, precisely caveated (Codex adversarial review, issue
#53, second pass) — a real household would not have this foreknowledge, but that does NOT
make $176.82/+26.3% a proven ceiling on realizable benefit.** A single real household
belongs to exactly ONE aggregation and knows only that aggregation's own test calendar,
never the union of all ~14 aggregations' combined schedules this figure assumes
foreknowledge of — but the union calendar is a deliberately inclusive upper bound on event
**FREQUENCY** only (more candidate event hours than any real household saw), not on the
resulting dollar economics. Pre-staging trades event-hour revenue against forgone off-peak
arbitrage, a real trade-off not proven monotonic in event count, and the reactive figures
elsewhere in this same section already demonstrate the union total can land INSIDE the
per-aggregation range rather than above every member of it — a smaller, more selectively-
timed real calendar is not guaranteed to earn less. `per_aggregation_sensitivity()` now
also computes a `prestaged_net_usd_min`/`_max` range per aggregation, scoped to that
aggregation's own calendar only, mirroring exactly how the reactive headline's own
realizable range already works — but that range needs the private raw CEC archive to
(re)compute, which was not available when this sensitivity was added, so the committed
artifact's `per_aggregation_sensitivity` section still predates it (flagged explicitly
there via `prestaged_range_pending_archive_regeneration`). Until a future run with the
archive present recomputes it, the $176.82/+26.3% figure should be read as one scenario
computed from an inclusive event-frequency assumption, not as a bound in either direction
on what a real single-aggregation household would have earned.

**Fail-closed design.** Every ambiguity above is checked and asserted in
`test_dsgs_vpp_backtest.py`, not just narrated. The event calendar and results JSON are
each written to a temp file and `os.replace`d, so a partial/failed run changes neither.
`--build-calendar` (rebuilding `data/dsgs_event_calendar_2025.csv` from the raw archive)
is a separate, explicit flag from the normal run (which reads the committed CSV) — the
raw `.xlsx` is not required for a normal regeneration.

**Inputs and provenance.** `usage.csv` (Green Button) via `behavior_rebuild.load()`, the
committed `data/dsgs_event_calendar_2025.csv`, and the imported `battery_dispatch_policies`
module (`bp.run_batt`/`bp.billed`, not reimplemented). Run from `private/verify` with
`usage.csv`, `behavior_rebuild.py`, `battery_dispatch_policies.py` and `rates.py` beside
it; writes `data/dsgs_vpp_backtest.json` (and, with `--build-calendar` and the raw archive
present, `data/dsgs_event_calendar_2025.csv`).

---

### 3.20 `analysis/cca_rate_extraction.py` — CEA's own charged per-TOU generation rates, extracted from every CCA-era bill (`data/cca_generation_rates.csv`)

**Purpose (issue #11, Phase 1).** `rates_history.py`'s `RateSet.cca_generation()` fails
closed by design on every CCA-era date, because CEA's own per-(season, TOU) rates are
printed only on the CCA bill pages, which `parse_bills.py` does not extract — only the
period total (`bill_periods_electric.cca_generation`) is committed. Until now the only
place those per-TOU CCA rates had ever been parsed was `bill_decomposition.py`'s private
`cca_block()`, used for exactly one hardcoded statement (2026-07-02, its CURRENT
comparison date, §10 below). This script generalizes that same parsing approach — the same
section anchors, the same per-line regex, the same kWh-rounding tolerance, the same
"named lines must sum to the printed total" gate — across all 18 CCA-era statement PDFs,
so later phases (§3.21) have a real per-TOU CCA rate to reprice against instead of a
period total.

**The 18 statements, the 19 periods.** `data/bill_periods_electric.csv` carries 19 rows
with `generation_provider == "CCA"`, spanning 18 distinct statement dates — one statement
(2025-10-31) bills two periods (a stub cycle straddling SDG&E's move from the flat Monthly
Service Fee to the per-day Base Services Charge, issue #7), printing two separate CCA
generation-charge sections, each with its own "Billing Period" line. The script does not
assume one section per PDF: it finds every section a statement's PDF actually contains,
matches each section to the right row of `bill_periods_electric.csv` by its own printed
billing-period line, and fails closed if the section count or the billing periods found do
not exactly match what that CSV says the statement should contain.

**Genuine finding: CEA's own charged rate did not move once in 18 months.** Extracted
across all 18 statements (Jan 2025 – Jul 2026), CEA's per-(season, TOU) generation rate is
identical on every statement that prints that cell (winter 0.2443 / 0.15782 / 0.05187
on-peak/off-peak/super-off-peak; summer 0.51684 / 0.15975 / 0.04961), and its Clean Impact
Plus product-adder rate is 0.001/kWh on every one of the 18 statements. Checked, not
assumed going in — `test_cca_rate_extraction.py`'s
`case_cca_rate_is_flat_across_the_whole_corpus` asserts it against the extracted rows. This
contrasts sharply with `data/rate_vintages.csv`'s `generation_printed_comparison` rows,
which show SDG&E's printed bundled-generation (EECC) comparison table moving repeatedly
over the same 18 months (e.g. summer on-peak 0.38826 → 0.40592 → 0.47019): the bundled
comparison table moved with the tariff vintage while the CCA's own charged rate this
household actually paid did not move once. This is reported as what the 18 statements on
file show, not asserted as a property of the CCA tariff going forward — a later statement
could break the pattern, and nothing here assumes it won't.

**Format variation found, and why it needed no special-casing.** 2025-10-01's statement
interleaves a right-hand "Breakdown of Current Charges" column into the same extracted
text lines as the CCA section (the same interleaving `bill_decomposition.py`'s
`printed_tou_blocks()` documents for the 2026 SDG&E delivery/generation tables). It does
not break this parser: every per-line regex anchors on a complete printed phrase that
stays contiguous on one extracted line even when other columns are interleaved elsewhere,
and the section boundary is an exact string search that the interleaving does not touch.

**Disclosed ambiguity: the Clean Impact Plus line's own kWh base.** CEA prints, e.g.,
"Clean Impact Plus 945 kWh X $0.001 .95" alongside a period whose `net_kwh` (per
`bill_periods_electric.csv`) is 946.0 — close but not identical, and the same one-or-two-kWh
gap recurs on other statements. The statements do not say what quantity Clean Impact Plus
is levied on; it is not simply `net_kwh`. The script records exactly what is printed (kwh,
rate, usd) and does not assert what the kWh base represents — disclosed in the
`clean_impact_plus` rows' own `note` field, not silently reconciled.

**Fail-closed design.** Each statement's section(s) must sum to the printed "Total CCA
Electric Generation Charges" line within the corpus's standard rounding tolerance, and the
set of (statement, period) rows the PDFs actually produce must exactly match
`bill_periods_electric.csv`'s expected 19-row CCA universe — a missing PDF, an extra
section, or a mismatched billing-period line stops the run rather than silently
under- or over-counting. `data/cca_generation_rates.csv` is written atomically; run twice →
byte-identical.

**Output** `data/cca_generation_rates.csv`. Registered in `test_scripts_runnable.py` under
`NEEDS_PRIVATE_ARCHIVE` (the per-TOU CCA charges are printed nowhere else), so the §9
byte-for-byte gate covers it locally.

**Tests** `analysis/test_cca_rate_extraction.py`, 16 cases, split the same way as this
repo's other bill-PDF-dependent suites: cases that need no private archive (parsing logic
against synthetic statement text, the corpus-shape assertions) run unconditionally; the
cases needing the real PDFs gate on `_require_archive()` and SKIP with the reason named
when this checkout lacks `private/`.

---

### 3.21 `analysis/cca_bundled_counterfactual.py` — was switching to the CCA a win? (`data/cca_bundled_counterfactual.json`)

**Purpose (issue #11, Phase 2/3).** §3.20 established what CEA itself charged, per
(period, season, TOU) cell, on every one of the 19 CCA-era billing periods, and found the
charged rate never moved. This script pairs that with what SDG&E's own bundled generation
would have cost on the SAME dates and the SAME usage, in BOTH directions, so the answer is
not anchored to one hand-picked pair of statements the way
`bill_decomposition.py`'s `provider_effect_whole_period()` is (that function answers
exactly this trade-off for exactly one CCA statement, 2026-07-02, against exactly one
bundled statement, 2024-06-27 — read here as a methodology reference and a reconciliation
target, never reimplemented). It re-derives nothing already committed: bundled-side rates
come from `rates_history.RateSet.generation()` / `generation_comparison_table()`
(imported, read-only), and CEA's per-TOU rates come from the committed
`data/cca_generation_rates.csv` (§3.20). Its only new extraction is two direct bill-line
citations — the bundled statement's own PCIA sentence and each era's franchise-fee line —
read off the same two anchor statements `bill_decomposition.py` already uses, because no
committed artifact carries either fact.

**Direction A — the 19 CCA periods repriced at bundled rates (MODELED · same-date bill
rates).** For each CCA period's actual per-(season, TOU) kWh, this asks what SDG&E's own
bundled-generation comparison table printed for that exact date — the same same-statement
diagnostic `bill_decomposition.py`'s module docstring documents at length: on a CCA date this
is SDG&E's bundled-generation (EECC) comparison, printed for reference rather than charged,
and it is the only place a same-date bundled rate exists for a CCA-billed date.

**Relabeled from an earlier MEASURED (second Codex review, issue #11, confirmed).** Both
multiplicands here are real, bill-printed figures — the household's own measured kWh, and
SDG&E's own real, same-date printed bundled-generation comparison rate — but the dollar TOTAL
this calculation produces was never actually billed to anyone: it is this script's own
reconstruction of a bundled arrangement the household never had on this date, for a period it
was actually billed by CEA. CLAUDE.md §9's confidence tiers distinguish an observed fact (a
meter reading, an actual bill line — MEASURED) from a validated computation on real,
current-for-that-date inputs (MODELED); this is the latter. The qualifier "same-date bill
rates" travels with the label everywhere it is reported (this script's own
`confidence_detail` field, `index.html`'s pills, this section) because it is a materially
stronger MODELED figure than one built from a rate table with no date-specific verification
(contrast `index.html`'s whole-year, one-current-rate reading, labeled plain "modeled ·
current rates, whole year") — every input here is the real bill-printed number for the
actual date being priced. The dollar figures themselves were unchanged by this labeling
correction; only the label and the surrounding prose describing what kind of evidence they
represent changed (a later, separate fix DID change the dollar figures — see MIXED-SIGN
CELLS below). Bundled comparison dollars are priced at SEGMENT level
(`data/bill_tou_detail.csv`'s own segment/segment_days columns),
never at one representative date's rate applied to the whole period's kWh: a period whose
printed comparison table itself changed mid-cycle (a rate revision landed inside a billing
cycle — e.g. 1/28/25–2/26/25, four days at the old winter rate and 26 at the new one) prints
two segments at two different $/kWh, and both are priced at their own rate. An earlier
version of this script priced such periods at one representative date's rate for the whole
period, overstating the bundled comparison by $1.63 across the four affected periods (Codex
review, issue #11); segment-level pricing is numerically identical to the old approach for
every period whose comparison table did not change mid-cycle (41 of the 49 priced rows).
Each (period, season, TOU) cell is classified MIXED-SIGN first (see below), then, for the
remaining cells, PRICED (billed as a net import, comparison present — 49 of 66 cells, the
only ones the energy-only total sums), ABSENT (billed as a net export, no comparison
printed — 11 cells; SDG&E prints nothing because a net-export bucket settles at the annual
true-up under NEM 2.0, not because its bundled rate is zero), or CARRIED-EXPORT (billed as a
net export in that specific period, yet a same-date bundled rate is still knowable because it
was carried across the gap from flanking direct observations elsewhere in the corpus —
`rates_history`'s "carried" tier — 3 cells, excluded from the priced total for the same
NEM-2.0-defers-the-dollar reason as ABSENT, reported separately rather than folded in).
**Result: CEA charged $2,450.78 against $2,375.75 of same-date bundled comparison over the
547 days on file — $75.03 more, $50.10/yr.**

**MIXED-SIGN CELLS (second Codex review, issue #11, confirmed broader than the single cell
first flagged).** A (period, season, TOU) cell's kWh in `data/cca_generation_rates.csv` is
a PERIOD-TOTAL net figure, but the underlying printed generation-comparison segments in
`data/bill_tou_detail.csv` can carry BOTH signs within that same period — part net-exported
(SDG&E prints its $0.00000/deferred-to-the-true-up sentinel), part net-imported (a real
per-kWh comparison rate) — even though the two sub-stretches net down to one signed total.
An earlier version of the segment-level pricing above summed a cell's segments' kWh and
dollars together whenever the PERIOD-TOTAL happened to be positive, without checking whether
the segments themselves agreed in sign. Where they did not, the resulting per-cell
"effective rate" (`bundled_comparison_usd` / summed kWh) blended a real, same-date printed
rate together with a $0 sentinel for energy no bundled comparison exists for at all — a rate
no tariff SDG&E ever printed. Checked directly against every generation-section row in
`data/bill_tou_detail.csv` (not special-cased to the one cell a review first flagged):
exactly **3** (period, season, TOU) cells in the whole 66-cell CCA corpus carry mixed-sign
segments — only one of which (the smallest kWh swing) had actually been reaching the old
priced branch; the other two were already excluded, but mislabeled ("genuine data absence")
when a real comparison rate in fact exists for part of their period. All 3 are now caught up
front, before any disposition decision, regardless of which way the period's own net kWh
falls (the corpus already has one of each: 2 net-export aggregates and 1 net-import
aggregate). **Resolution chosen: exclude the whole mixed-sign cell**, never split or prorate
its real CCA-side dollar between the two segments. The alternative — price only the
net-import segment(s) and treat the export segment(s) like a whole-cell export — was
considered and rejected: CEA's own bill only prices the WHOLE cell's net kWh (no bill line
prices one sub-segment on its own), so recovering a segment-level CCA dollar would require
assuming the flat charged rate applies exactly per sub-segment kWh, which does not reconcile
to the cent the way every other figure in this script does (CEA's own per-period dollar
already carries a documented half-kWh unrounding tolerance that a synthetic split would
inherit and compound). Excluding the whole cell keeps every priced dollar exactly
bill-reconciled on both sides, at the cost of some information (each excluded row still
discloses its own segments/import_segment_kwh/export_segment_kwh for transparency, just not
summed into any headline). **Result: 3 cells excluded, CEA's own real whole-cell charge for
them totals −$36.28** (`direction_a.excluded_mixed_sign_cca_usd` /
`.excluded_mixed_sign_note`, `.excluded_mixed_sign_detail`) — a separate disposition from the
net-export bucket below, since a mixed-sign cell is not necessarily billed as a net export
overall (one of the 3 nets to a net IMPORT for its period). Direction B
(`bundled_generation_cells()`) is checked for the same theoretical risk and found
structurally immune today: it never derives an effective rate by dividing a summed dollar by
a summed kWh, so a mixed-sign bundled cell would not reproduce this defect — but it still
fails closed if one is ever found (0 of the 7 bundled-era periods' cells mix signs today),
rather than assuming that reasoning holds forever.

**This figure is narrower than "was switching to the CCA a win" — it prices only the
net-import, single-sign cells.** The 14 excluded (11 ABSENT + 3 CARRIED-EXPORT) cells carry
a real CEA credit of −$338.87 on the household's own bills — over 4.5× the size of the
$75.03 priced-cell delta — but there is no same-date SDG&E bundled counterfactual to compare
it against: NEM 2.0 defers a net-export bucket's dollar value to the annual true-up, so
SDG&E's bill prints no bundled comparison for these cells at all (a genuine absence, not a
bundled rate of zero — the same Settlement-is-not-a-price distinction
`bill_decomposition.py`'s `Settlement` type enforces). A further 3 MIXED-SIGN cells (−$36.28)
are excluded for the different reason described above. What the bundled side would have
credited for either excluded category is **not determined** here: reconstructing the
net-export side would mean rebuilding the whole NEM annual true-up (which nets exports
against imports across the compensation year at rates this repo has not extracted for the
export side), and the mixed-sign side is not knowable from any single bill line at all — both
are a materially larger undertaking, properly scoped as their own follow-up rather than
estimated. `data/cca_bundled_counterfactual.json` carries both live —
`direction_a.excluded_net_export_cca_credit_usd` / `.excluded_net_export_note` and
`.excluded_mixed_sign_cca_usd` / `.excluded_mixed_sign_note`, computed from the same
committed CSV as every other Direction-A figure, never hardcoded. Direction B carries the
analogous, MODELED disclosure for its own excluded net-export cells (10 cells, a hypothetical
−$472.80 CEA-side credit, smaller in evidentiary weight since CEA never served this household
in 2024) in `direction_b.excluded_net_export_hypothetical_cca_credit_usd`. The practical
consequence: the whole-household answer to whether switching to the CCA was a win is **not
fully determined** by this analysis — the $50.10/yr figure is the real, small answer for the
net-import, single-sign cells, and `recommendation.text` states this scope explicitly rather
than letting the priced-cell figure stand in for a conclusion it does not, on its own,
support.

**Direction B — the 7 bundled periods repriced at CCA rates (MODELED).** For each
bundled-era period's actual generation kWh (summed from `data/bill_tou_detail.csv`'s
`section='generation'` rows), this asks what CEA's own flat charged rate (§3.20's finding:
one value per season × TOU cell across the whole observed CCA era) would have billed. This
is modeled, never measured — CEA did not serve this household in 2024, so applying its
2025–2026 rate to 2024 usage is an explicit, labeled assumption. The same net-import/
net-export split applies (10 of 17 cells excluded as net exports, for the same NEM 2.0
reason). Direction B publishes an energy-only figure only: a "whole period arrangement"
figure matching `provider_effect_whole_period()`'s second scope would need CEA's 2024
product adders and CCA-only unbundling riders (PCIA-equivalent, ICA, EDP credit) that no
committed artifact carries and that CEA never actually charged in 2024 — inventing one
would be exactly the guess CLAUDE.md §0 forbids, so it is reported as not determined
instead. **Result: SDG&E actually charged $934.19 against $1,014.44 CEA's flat rate would
have billed over the 216 days on file — $80.25 more, $135.70/yr.**

**Provider effect vs vintage effect, generalizing `bill_decomposition.py`'s two-statement
identity (`g1 − g0 = (s1 − g0) + (g1 − s1)`) across every Direction-A-priced cell.** `g0` is
the last bundled rate SDG&E actually charged before the 12/27/24 CCA switch (directly
evidenced, late in the bundled era per season); `s1` is Direction A's same-date bundled
comparison; `g1` is CEA's own charged rate (§3.20's flat finding). `vintage_usd = kWh ×
(s1 − g0)`: how much SDG&E's own bundled rate moved from the pre-switch baseline to this
later date, holding usage fixed — attributable to tariff vintage, not the provider switch.
`provider_usd = kWh × (g1 − s1)`: CEA vs SDG&E's bundled rate on the SAME later date —
attributable to the switch, not vintage. Over the subset of Direction-A-priced cells with a
vintage baseline, `provider_usd + vintage_usd` sums to the total change to the cent
(`identity_check.residual_usd == 0.0`, asserted in `test_cca_bundled_counterfactual.py`):
**provider effect $74.05, vintage effect $177.09**. The CCA side of this split is exactly
zero by construction — §3.20 found CEA's charged rate never moved, so 100% of the drift in
"what CEA would charge now vs then" is a bundled-side vintage phenomenon. Two cells
(summer/off_peak, winter/off_peak) have no bundled-charged observation anywhere in the
bundled era (this household's off-peak buckets were net-export whenever billed bundled),
so `g0` does not exist for them; their 5 (of 49) Direction-A rows still count fully toward
Direction A's headline (which needs no `g0`) and are simply excluded from, and disclosed
as excluded from, the provider/vintage split. (One of those two cells, winter/off_peak, is
also where all 3 of the MIXED-SIGN cells above live — the mixed-sign one that used to reach
the priced branch was ALREADY one of these no-`g0` rows before this fix, so removing it from
`priced_detail` shrinks this count from 6 (of 50) to 5 (of 49) without changing
`vintage_effect_usd` at all.)

**The two directions disagree by 170.9%; rate vintage is a real, large, likely major
contributor, but the gap is not proven to be fully attributable to vintage alone (Codex
review, issue #11, defect 1 — an earlier version of this text overclaimed the causal
attribution as a flat, unqualified fact).** Direction A ($50.10/yr) and Direction B
($135.70/yr) are not two readings of the same question. Direction A prices CEA against the
same-date bundled rate, so the vintage drift above nets out of it by construction. Direction
B has no 2024 CCA rate to anchor to, so it necessarily compares 2024's cheaper bundled rate
against CEA's rate as observed in 2025–2026 — exactly the "one rate vintage per projection"
trap CLAUDE.md §9 names elsewhere in this repo. **What IS known, verified**: the
provider/vintage split above measures the vintage effect at $177.09 over the Direction-A
sample, more than double the $74.05–75.03 provider effect over the same sample, and CEA's
own charged rate never moved once across the whole corpus while SDG&E's bundled comparison
rate rose substantially — real, bill-printed facts, and the most plausible dominant driver
of the 170.9% gap. **What is NOT known**: the $177.09 figure is computed entirely from
Direction A's own 547-day sample, its own weights, and its own set of priced cells — there
is no mathematical identity tying it to Direction B's independently-computed $135.70 result.
Direction A and Direction B also differ in period count (19 vs 7), seasonal composition (two
summers and two winters vs one summer and a partial winter missing Jan–Apr), usage weights,
and a partly different set of included cells — none of this has been separately decomposed
from the vintage effect, so it is not ruled out as an additional contributor to the 170.9%
gap. A rigorous common-weight, common-cell decomposition across Direction A and Direction B
has not been performed; this reconciliation names the vintage effect as verified and large,
not as a complete accounting of the gap. This does not weaken the recommendation: **Direction
A is the recommendation basis** (modeled · same-date bill rates, 547 days spanning two
summers and two winters, vs Direction B's 216 days of one summer and a partial winter) on
its own terms — its same-date comparison avoids vintage drift by construction, while
Direction B has no 2024 CCA rate to anchor to and necessarily mixes multiple effects — and
that reasoning holds whether or not the full 170.9% gap is ever decomposed. Direction B's
larger figure is not averaged with Direction A's.

**Reconciliation with the existing single-statement figures (`bill_decomposition.py`,
§10).** That script's `provider_effect_whole_period()` runs exactly this same comparison
for exactly one CCA statement (2026-07-02) against exactly one bundled statement
(2024-06-27), publishing two scopes: energy-only over the five cells the bundled table
prices (CEA $170.59 vs $180.46 bundled, **−$9.87**, CEA cheaper) and the whole-period
arrangement including riders and adders (CEA's side $197.97 vs the same $180.46,
**+$17.51**, CEA costlier). The 2026-07-02 statement is itself one of Direction A's 19
sampled periods: summing its own five priced cells in `direction_a.priced_detail` gives
exactly −$9.87, matching `bill_decomposition.py`'s independently computed figure for the
same statement to the cent — the two scripts agree completely on this one data point; it
is not a second, disagreeing estimate, which is why it's used here as a cross-check
reference. It is NOT the one period in the 19-period sample that reads CCA-cheaper, nor
even the most extreme one: grouping `direction_a.priced_detail` by period, 7 of the 19
priced periods have a net-negative provider delta (CEA cheaper), ranging from −$1.36 to
−$14.31 (2026-03-04's statement, period 1/28/26-2/26/26 — more extreme than the
2026-07-02 statement's −$9.87). All 7 fall among the later, more-recent statements in the
corpus, consistent with the vintage-effect finding above (the bundled comparison rate has
risen over the span while CEA's rate held flat) but not confined to a single outlier date.
Averaged across all 19 periods, including these 7, CEA cost more than bundled would have —
the full 547-day average is the $50.10/yr priced-cell headline above.
The two single-statement figures in `bill_decomposition.py`/§10 remain correct for that one
statement; they are not superseded, only placed in the context of the fuller sample.

**PCIA, read directly off the bundled statement.** The 2024-06-27 (bundled) statement's own
generation-charge line states "$1.97 of your Electricity Generation Charge is your bundled
PCIA charge" — confirming, by direct re-citation of the raw PDF rather than by assumption,
that bundled service carries a PCIA-equivalent component baked into its generation charge
even though bundled customers see no separate PCIA line item (unlike CCA customers, who see
"PCIA 2023 1,001 kWh x $.02828 28.31" as its own line). This does NOT resolve `rates.py`'s
documented ambiguity about how PCIA behaves on net kWh in a net-negative period: no bundled
statement in this household's own bundled-era corpus (2024-06-27 – 2024-12-30) ever went
net-negative for the whole period, and the bundled generation charge is not itemized
per-kWh anywhere on the bill (a single dollar figure, not a rate applied to a kWh
quantity) — the bundled side is genuinely more opaque here, not merely unobserved.

**Franchise fee, a genuine finding not previously in this repo.** The franchise-fee line
item survives the provider switch under both names — "Franchise Fees on Electric Energy
Supplied by" (bundled, 2024-06-27: $8.07 × 1.10% = $0.09) and "Franchise Fee Equivalent
Surcharge" (CCA, 2026-07-02: $189.93 × 1.10% = $2.09) — but its BASE changes: on the
bundled statement it is 1.10% of the Wildfire Fund Charge alone (a small non-bypassable
per-kWh charge on gross imports); on the CCA statement it is 1.10% of a much larger figure
under the renamed line, consistent with California CCAs collecting a franchise-fee-
equivalent surcharge on generation revenue specifically because that revenue no longer
flows through the utility once a customer enrolls in a CCA (cities would otherwise lose the
franchise fee on it). The dollar difference is small on these two anchor statements (+$2.00)
but the base is not proportional to the same quantity in both eras, so it is real and
provider-attributable, and not captured by either direction's per-TOU-cell energy-only
repricing. What the CCA-era statement's $189.93 base actually IS could not be reconciled to
any single printed subtotal on the statement — reported as **not determined**, not guessed.
No committed artifact extracts this line for all 26 periods, so its systematic dollar
contribution across the whole corpus is likewise not determined here.

**Fail-closed design.** `data/cca_bundled_counterfactual.json` is written atomically; the
two directions' NUMERIC results depend only on committed CSV/engine data and regenerate
identically with or without the private PDF archive present — only the PCIA and
franchise-fee bill-line citations need the two anchor PDFs directly. A net-import cell
lacking a same-date bundled comparison raises rather than silently pricing at zero (it has
never happened, but the guard is unconditional); the provider/vintage identity check
(`residual_usd == 0.0`) is asserted, not merely computed and trusted.

**Output** `data/cca_bundled_counterfactual.json`. Registered in `test_scripts_runnable.py`
under `NEEDS_PRIVATE_ARCHIVE` (the same dependency shape as `irreducible_bill.py`: it needs
`data/cca_generation_rates.csv` and `data/bill_tou_detail.csv`, which themselves derive
from the bill PDF corpus), so the §9 byte-for-byte gate covers it locally.

**Tests** `analysis/test_cca_bundled_counterfactual.py`, 39 cases, split the same way:
cases exercising the numeric core (the classification rules, the mixed-sign-cell
regressions, the provider/vintage identity, the reconciliation arithmetic) run
unconditionally against committed CSV/engine data; the cases needing the two real anchor
PDFs (the PCIA and franchise-fee citations) gate on `_require_archive()` and SKIP with the
reason named when this checkout lacks `private/`.

### 3.22 `analysis/battery_sizing_curve.py` — a sizing curve, not a two-product comparison (`data/battery_sizing_curve.json`)

**Purpose (issue #12).** §3.13 (`battery_dispatch_policies.py`) only ever compared two
shipping configs — 13.5 kWh Powerwall 3 and 27 kWh PW3+Expansion, both at 11.5 kW — which
answers "which of these two" but never "how much storage this house actually wants." This
script re-runs the same price-aware ("greedy") dispatch across an **energy grid** (5–40
kWh, holding power at 11.5 kW — the rate both shipping Tesla configs share) and a **power
grid** (5–15 kW, holding energy at 13.5 kWh — the base Powerwall 3), on **both** current
behavior and post-behavior (EV-shifted) load, on the measured year via the same canonical
engine (`rates.bill_nem`) and the identical EV-spillover exclusion rule (≥2.5 kW outside
on-peak is never battery-served) at every grid point. Both shipping configs' own capacity
and power (13.5 kWh, 27 kWh, 11.5 kW) are added to the grids as exact points, not
interpolated from neighbors, so they are genuinely ON the swept curve.

**`run_batt` generalized, not duplicated.** Rather than write a second dispatch loop,
`battery_dispatch_policies.run_batt()` gained optional `power_kw=11.5` and `soc0=None`
parameters (both default to the exact prior behavior — verified: `battery_dispatch_
policies.json` and `battery_plan_matrix.json` regenerate identically after the change,
and every one of the ~30 other call sites across the analysis package, which never pass
either new parameter, is unaffected) and now returns after asserting its own internal
energy-conservation identity (`soc == soc0 + thru - served/ETA`, i.e. every joule leaving
the pack matches one that entered it net of the round-trip loss) — `SystemExit` if it does
not hold, CLAUDE.md §1b. `battery_sizing_curve.py` adds a second, independent conservation
check per grid point (`_check_conservation`): the import series' total discharge relief
must equal `served` exactly, and the export series' total solar-routed-to-charging plus
grid top-up must equal `thru` net of the round-trip loss — defense in depth, not a
formality (`analysis/test_battery_sizing_curve.py` fails both checks closed against a
synthetically corrupted `served`/`thru` value). The return tuple's arity was deliberately
left unchanged (still `imp, exp, served, thru`) rather than adding a fifth "final SOC"
value: with ~30 call sites elsewhere in the package unpacking exactly four values, that
would have forced changes far outside this issue's scope. `battery_sizing_curve.py`
instead derives the final SOC itself from the same public identity `run_batt` already
asserts (`soc0 + thru - served / ETA`) — see steady-state fix below.

**Steady-state dispatch, not a one-time year-1 boundary condition (Codex adversarial
review, third pass).** `run_batt()` always started at `soc0 = cap/2` and ran the measured
year exactly once. On the real data, this house's greedy dispatch always saturates against
a hard boundary (empty or full) within the year, so the STARTING soc0 has no lasting
effect once it does — but until it converges, larger capacities carry a larger absolute
amount of un-costed "free" starting charge (current-behavior, which drains toward empty:
observed year-1 ending SOC ranged from 0 kWh at 5-27 kWh capacity to 5.0 kWh at 40 kWh) or
un-recovered "stranded" ending charge (post-behavior, which can end the year over 90%
full: observed year-1 ending SOC ranged from 4.3 kWh at 5 kWh capacity to 39.3 kWh at
40 kWh) — an arbitrary boundary effect that grows with capacity and could in principle
contaminate the reported annual savings, marginals, and knee. Fixed by `_steady_state_run()`:
iterate `run_batt`, feeding each pass's ending SOC forward as the next pass's starting SOC,
until the two converge to within 0.01 kWh (`STEADY_STATE_TOL_KWH`) — a genuine steady
annual charge/discharge cycle rather than a transient. On this house's data, every grid
point converges in exactly one extra pass (the greedy policy's aggressive daily cycling
erases any memory of the starting condition almost immediately), and the corrected annual
savings differ from the uncorrected, boundary-contaminated figures by single-digit dollars
per grid point (e.g. 13.5 kWh current-behavior: $2,328.31 → $2,327.42) — the underlying
methodological defect was real and worth fixing (CLAUDE.md §0/§1: a result should not
depend on an arbitrary boundary condition), even though its numeric impact on this
particular dataset turned out to be small; the knee's location (20 kWh, both scenarios)
and the sensitivity conclusion (energy binds) are unchanged by the correction. One
consequence worth stating plainly: an EARLIER version of this section described post-EV-fix
savings past 30 kWh as very slightly DECLINING (a boundary-condition artifact — the
uncorrected run's growing "stranded" ending charge at higher capacities was itself the
cause of that apparent decline); the steady-state-corrected data shows a clean flat
plateau instead (kWh served AND saving both exactly unchanged from 30 through 40 kWh),
which is also the more physically sensible result. If the SOC iteration ever failed to
converge within 8 passes, `_steady_state_run()` raises `SystemExit` naming the capacity
and power at fault rather than silently returning an unconverged result.

**Cost model — fit to same-power quotes only, not blended (Codex adversarial review, second
pass).** The energy sweep holds power fixed at 11.5 kW, so its cost model is fit to ONLY
the two real quoted configs that also run at 11.5 kW — PW3 $14,500/13.5 kWh and
PW3+Expansion $20,400/27 kWh — giving slope $437.04/kWh, intercept $8,600.00. `index.html`
§6's other two quoted configs (IQ 5P $8,500/5 kWh at 3.8 kW, IQ 10C $13,000/10 kWh at
7.1 kW) are deliberately EXCLUDED from this fit and kept only as documented context
(`cost_model.excluded_from_fit`): their own kW differs from the 11.5 kW reference, so
blending them in would price some of their own lower power capability into a per-kWh rate
applied to a fixed-power sweep. An earlier version of this script fit all four configs
together (slope $512.80/kWh) — materially higher, because the smaller units' own lower
power capability was silently priced into the per-kWh rate; that version is retired.
Because both shipping configs are themselves the fit's only two anchors, the fitted cost
at 13.5 kWh and 27 kWh now equals their own real quoted cost exactly (no residual). The
power sweep still reports the physical curve (dollars saved, kWh served) only; no real
anchor isolates power-only cost scaling, so its cost and payback stay `null` (**not
determined**, CLAUDE.md §0), never guessed from the energy-fit slope.

**Knee — pre-declared, not picked by eye.** Because the cost fit is linear, the marginal
cost of one more kWh is the same constant (the fit's own slope, $437.04/kWh) at every grid
point. The knee is the smallest energy-grid point whose own marginal kWh (versus the
previous grid point) fails to pay back that constant marginal cost within **10 years** —
the Powerwall 3 warranty term §6 already cites — stated in the script before the sweep
runs, not chosen after seeing the curve. Result on the real data: **20 kWh** in both
scenarios (marginal saving/kWh drops to $42.10 current-behavior, $22.46 post-behavior —
marginal payback 10.4 yr / 19.5 yr, both past the $437.04/10 ≈ $43.70/kWh/yr line a
10-year payback requires; the knee's location is unchanged by either the cost-model or
the steady-state correction below — only its stated marginal-payback years moved).

**Sensitivity — local elasticity, not a raw span (Codex adversarial review, second pass).**
An earlier version compared the energy sweep's top-to-bottom dollar span (5–40 kWh) against
the power sweep's (5–15 kW) directly to decide which "binds" — not a valid comparison,
since the two spans cover different units and arbitrarily different relative ranges (8× vs
3×), so the classification could flip under a different choice of grid endpoints without
any change in the house's actual sensitivity. Replaced with a **local elasticity**: an
unequal-spacing 3-point (Lagrange) derivative AT the shared reference point (13.5 kWh,
11.5 kW), using the reference's own row and the grid points immediately flanking it in
each sweep, normalized to percent-change-in-saving per percent-change-in-the-dimension —
a local derivative property, immune to how far either grid extends beyond that
neighborhood. A version after that used a plain secant between the reference's two
neighbors (equivalent to the ordinary centered-difference formula) — Codex adversarial
review, third pass: that estimates the derivative at the NEIGHBORS' midpoint, not at the
reference, whenever the grid is not symmetric around it, which is true for both grids here
(energy neighbors sit 3.5 kWh below / 1.5 kWh above 13.5 kWh; power neighbors sit 1.5 kW
below / 1.0 kW above 11.5 kW). The corrected formula (`h0 = ref-lo`, `h1 = hi-ref`) reduces
to the ordinary centered difference when `h0 == h1`, and is verified EXACT (not merely a
closer approximation) against a known quadratic `f(x) = x²` on a deliberately asymmetric
grid in `analysis/test_battery_sizing_curve.py`. Result: energy elasticity 0.47
current-behavior / 0.38 post-behavior vs power elasticity 0.0025 / ~0 — energy is
far more sensitive at this reference point in both scenarios (moved from the
retired secant-based 0.56/0.48 vs 0.0025/−0.0001, but the qualitative conclusion —
capacity, not discharge rate, is what this house's load shape responds to near the
reference configuration — is unchanged), and both sweeps' `save_usd` at the shared
reference point are asserted equal to the float epsilon, since they describe the identical
13.5 kWh/11.5 kW configuration.

**Power sweep's own charge-power confound (Codex adversarial review, fourth pass).**
The power sweep is supposed to isolate discharge power as the SOLE varying dimension.
An earlier version held `charge_kw` at the real, cited 5 kW ONLY at its one real point
(the REF_POWER_KW=11.5 kW anchor) and let every other power-sweep point fall back to
`charge_kw=None` (symmetric — charge power tracking whatever hypothetical discharge
power that point swept to). That let charge power drift uncontrolled alongside the
named dimension between the reference and its immediate neighbors, contaminating the
power-elasticity derivative with a second moving variable. Fixed: `charge_kw` is now
held FIXED at 5 kW at EVERY power-sweep point — more principled than the retired
symmetric fallback, since this household's actual battery never changes its own
capacity or charge circuitry in this sweep (only the hypothetical discharge rating
varies), so 5 kW genuinely isolates discharge power rather than inventing a rating
for a hypothetical unit. Effect on the real data: the non-anchor power-sweep points'
`save_usd` shift down a few cents each (less charging capacity than the old symmetric
fallback gave them); power elasticity moves from 0.003 → 0.0025 current-behavior and
from 0.0004 → ~1e-15 (a true, not merely rounded, zero — `save_usd` is now identical
to the penny at kw = 10, 11.5, 12.5, 15 once the confound is removed) post-behavior.
Both changes make the qualitative conclusion (energy binds, power does not) STRONGER,
not weaker: the energy/power elasticity ratio moves from ~158× to 191.7× current-
behavior; post-behavior's ratio is no longer a finite number to report (there is no
measurable local power sensitivity left to form a ratio against — reported as `null`
in `sensitivity.energy_elasticity_ratio_to_power_real`, not as a fabricated-looking
large number from dividing by floating-point noise). Neither the knee (still 20 kWh,
both scenarios) nor either shipping product's `save_usd`/payback moved — this fix is
confined to the power sweep's own five non-13.5-kWh points and the derived
power-elasticity number.

**Energy elasticity's own smaller confound, and why the published number is
unchanged.** The energy sweep's per-point charge rate (5 kW at/below 13.5 kWh, 8 kW
above — §3.13/this section, correct and unchanged) means the energy elasticity's
own flanking point above the reference (15 kWh) uses a genuinely different charge
rate (8 kW) than the reference itself (5 kW) — a small second variable moving
alongside the intended one (capacity) in that ONE derivative. This is correct and
must stay for the full curve (knee, payback, shipping products all price real
per-product hardware), so it is not "fixed" there. Instead,
`sensitivity.energy_elasticity_charge_held_fixed_diagnostic` recomputes the SAME
local derivative a second way, counterfactually holding the 15 kWh flanking point's
charge rate at 5 kW too (diagnostic only — does not touch the published
`energy_sweep_at_11.5kw` rows or `energy_elasticity`). Result: 0.4721
(charge-held-fixed) vs 0.473 (published, real-hardware) current-behavior — a 0.2%
difference; 0.3829 vs 0.3837 post-behavior — a 0.2% difference. The
>150×-energy-vs-power conclusion is unaffected either way (191.4× vs 191.7×
current-behavior; post-behavior has no finite power elasticity to compare against
under either variant). The published `energy_elasticity` stays the real-hardware
number (it is more representative — every point really does run its own product's
real charge rate); the diagnostic exists to show the confound's size is negligible
next to the ~190× gap driving the conclusion, not to replace the published figure.

**Shipping products located on the curve.** At 13.5 kWh / 11.5 kW: $2,327.42/yr saved
current-behavior (payback 6.23 yr — now exactly the real $14,500 quote's own payback,
since 13.5 kWh is one of the fit's only two anchors, matching §6's cited ~6.2–6.5 yr
range), $2,238.89/yr post-behavior (payback 6.48 yr, exactly the §6 figure). At 27 kWh /
11.5 kW: $2,792.85/yr current-behavior, $2,455.82/yr post-behavior (payback 8.31 yr) —
both cross-checked against `battery_dispatch_policies.json`'s own `pw3`/`pw3x` `greedy.save`
and `post_behavior.mid`/`high.battery_marginal` figures, within $2.15 (that canonical
artifact is deliberately NOT steady-state — correcting its own single-pass boundary
condition is a separate concern outside this issue's scope — so a small, expected gap
between the two remains; $2.15 against $1,000+ savings figures is itself evidence the
steady-state correction is a minor refinement here, not a large swing). The **gap between
the economic optimum and what can actually be bought**: the knee (20 kWh) marks the first
capacity whose own marginal kWh no longer clears the 10-year payback bar — 15 kWh is the
largest capacity that still does — and no shipping product sits at either 15 or 20 kWh.
The choice in practice is 13.5 kWh (leaves some economic value below 15 kWh on the table)
or 27 kWh (well past the knee), stated plainly rather than implying a 15 or 20 kWh product
exists.

**Output** `data/battery_sizing_curve.json`. Registered in `test_scripts_runnable.py`
under `CI_RUNNABLE` (needs only `usage.csv` via `behavior_rebuild.load()` and
`household.yaml`, the same dependency shape as `battery_dispatch_policies.py` — no
tie-out assertion against archive-derived data inside the generator itself, unlike
`battery_plan_matrix.py`, so synthetic CI inputs run it cleanly rather than tripping a
divergence check).

**Tests** `analysis/test_battery_sizing_curve.py`, 33 cases: synthetic-frame unit tests of
the cost fit (including that only the two same-power configs are anchors, that the two
different-power configs are excluded with a named reason, and that the fitted cost passes
through both shipping configs exactly — Codex adversarial review, second pass), the
conservation checks (including two fail-closed corruption cases), the marginal-difference
helper, the knee-detection rule, `_locate_products()`'s two fail-closed guards (a shipping
product absent from the swept kWh grid, and — second adversarial review — one whose own
kW does not match the energy sweep's reference power), and the local-elasticity sensitivity
calculation (that it is unaffected by a grid point far outside the reference neighborhood,
that it fails closed on a reference point with no flanking neighbor, and — third
adversarial review — that it reproduces the exact analytic derivative of a known quadratic
on a deliberately asymmetric grid, the case that distinguishes the corrected unequal-spacing
formula from the retired plain-secant one) need no private archive at all; cases
cross-checking the 13.5 kWh point and the steady-state shipping saves' bounded gap against
`battery_dispatch_policies.json`, the sensitivity conclusion, the knee's presence, and
byte-identical regeneration gate on `_require_archive()` and SKIP with the reason named
when this checkout lacks `private/`. Issue #40 Finding 1's sweep added cases pinning that
the energy sweep routes `CHARGE_KW_WITH_EXPANSION` above 13.5 kWh (not the bare-unit rate)
and that the committed artifact's 27 kWh point matches it, not the retired uniform-5kW
figure. Fourth-pass adversarial review (the power-sweep and elasticity confounds above)
added: a spy-based regression proving every power-sweep point now shares the SAME fixed
`charge_kw` regardless of its own varying discharge power (needs no private archive — a
synthetic fixture with `power_kw` genuinely varying across the grid); a live case (needs
the real archive) asserting the charge-held-fixed diagnostic elasticity stays within 2% of
the published one and that energy still dominates power by >10x under either variant; and
a committed-artifact regression pin on the corrected `power_elasticity` values themselves
(0.0025 current-behavior, ~0 — not the old symmetric 0.003/0.0004 — post-behavior) and on
the ratio fields' null-handling when there is no finite power sensitivity to divide by.

### 3.23 `analysis/perfect_foresight_dispatch.py` — how much is a smarter controller worth? (`data/perfect_foresight_dispatch.json`)

**Purpose (issue #13).** The "greedy" price-aware policy (§3.13) is a threshold heuristic:
serve every import priced above the battery's stored-energy cost. Nobody had checked how
close that gets to the best ANY controller could do on this house's own measured year, at
identical hardware (13.5 kWh, 11.5 kW discharge / 5 kW charge — Tesla's own datasheet,
issue #40 — 90% round-trip) and the identical EV-spillover
exclusion (≥2.5 kW outside on-peak never battery-served). This script computes the TRUE
annual-bill-minimizing dispatch directly, as a linear program — not a heuristic and not
naive price arbitrage. The distinction is real: `rates.bill_nem_monthly` nets import
against export per (month, season, TOU-period) bucket, charging the bucket's net position
at the higher "energy" rate if positive or crediting it at the lower "credit" rate if
negative, and separately charges non-bypassable costs (NBC) on GROSS monthly imports
regardless of netting. An optimizer that just chases the biggest per-interval price spread
can genuinely bill WORSE, because it ignores which side of zero a bucket's net position
lands on, or that NBC keeps accruing on gross imports even when energy nets to zero.

**LP formulation.** Per 15-minute interval (n = 35,040/year): `grid_topup_i` ≥ 0 (extra
grid import to charge), `solar_absorbed_i` ∈ [0, gen0_i] (solar surplus retained to charge
instead of exported), `discharge_i` ∈ [0, imp0_i] (serves import, capped at the interval's
OWN gross import so the battery can reduce it to zero but never manufacture NEW export
beyond gen0_i), `soc_i` ∈ [0, cap]. `imp_i` and `exp_i` are **derived, not free variables**:
`imp_i := imp0_i - discharge_i + grid_topup_i`, `exp_i := gen0_i - solar_absorbed_i`. This
is deliberate and matches `battery_dispatch_policies.run_batt`'s own convention exactly
(Codex adversarial review, third pass — see "Do not ship" finding below): an interval with
no battery action reproduces `imp0_i`/`gen0_i` exactly, gross flows included. Constraints:
combined charge-power cap (`grid_topup_i + solar_absorbed_i ≤ power_kw/4`), SOC continuity
(`soc_i = soc_{i-1} + (grid_topup_i+solar_absorbed_i)·ETA - discharge_i/ETA`), a **cyclic
SOC boundary** (`soc_init = soc_{n-1}` — a steady annual cycle, not a one-time year-1
starting-charge windfall or ending stranded-charge deficit; issue #12's Codex review caught
exactly this class of bug in the heuristic dispatch script, built in here from the start
rather than fixed after the fact), and EV exclusion (`discharge_i` forced to 0 wherever
kW ≥ 2.5 outside on-peak). Objective (minimize; BSC excluded as dispatch-invariant):
`NBC·sum(imp0_i)` (a constant, added back after solving) `+ sum_i[NBC·grid_topup_i -
NBC·discharge_i]` + for each of the ~36-39 (month, season, period) buckets with
`net_b = x_b - y_b` (the standard convex-piecewise-linear-as-LP split, `x_b, y_b ≥ 0`),
`sum_b[energy_rate_b·x_b - credit_rate_b·y_b]` — the EXACT structure of
`rates.bill_nem_monthly`, not an approximation. Solved via `scipy.optimize.linprog` (HiGHS)
— a new dependency (`requirements.txt`), solving in ~1-3 seconds for the full year.

**Do not ship: gross-flow preservation (Codex adversarial review, third pass — the most
consequential finding across all three review passes).** An earlier version used FREE
`imp_i`/`exp_i` variables tied only to the signed net `imp0_i - gen0_i`, with `discharge_i`
bounded only by power/SOC (no cap tied to `imp0_i`). Codex correctly flagged this as "do not
ship" for two compounding reasons: (1) it silently discarded the ~6.3% of real intervals
that carry BOTH gross import and gross export simultaneously (per
`battery_dispatch_policies.py`'s own count) — collapsing them to a signed net erases that
gross import for free, understating the NBC genuinely owed on it, with NO battery action
required to "earn" that saving; (2) it let the optimizer manufacture brand-new export
beyond what the house ever generated, a capability the shipping greedy policy's own
discharge cap (`min(imp[i], soc·ETA, pwrq)`) never uses, breaking the "same hardware, same
envelope" comparison this whole bound depends on. Both are now fixed by construction:
`discharge_i ≤ imp0_i` and `solar_absorbed_i ≤ gen0_i` mean the battery can never move
`imp_i` or `exp_i` outside `[0, imp0_i]` / `[0, gen0_i]`. The SAME bug existed a second time
in `rolling_day_ahead`'s execution loop (re-collapsing to a signed net when applying the
plan to real data) and was fixed identically there, with discharge additionally capped at
the REAL `imp0_i` (never the forecast's) so execution can never manufacture export beyond
what the house actually generated even if the plan implied it could.

**Impact: this was not a rounding-level bug.** Before this fix, the (incorrect) headline
was a 64.4% optimality gap ($3,829.61/yr theoretical maximum vs $2,329/yr greedy, the
then-current greedy figure). After the fix, perfect foresight saved $2,546.24/yr — a
$217.24/yr gap, 9.3% of greedy's own saving, at that same $2,329/yr greedy figure. (Issue
#40 later cited the Powerwall 3's real 5 kW charge / 11.5 kW discharge split and moved
both sides of this comparison again — see that section below for the current
$2,545.39/yr / $217.39/yr / 9.3% figures against today's $2,328/yr greedy.) Over
$1,283/yr of the originally-reported "optimum" was non-physical: free NBC
relief from collapsed simultaneous flows, and export the battery never actually had
anything to back. This is the single largest correction across all of issue #13's review
rounds, and the reason the whole headline finding inverted from "the shipping policy
leaves most of the value on the table" to "the shipping policy is already close to the
ceiling for this hardware."

**Verification (the AC's own explicit requirement).** The LP's objective, plus the BSC it
deliberately excludes, is re-billed through `rates.bill_nem` directly and must agree to
within $1 — enforced with a `SystemExit` inside the generator itself (not only a separate
test), since the whole point of "verified" is a property the artifact-producing script
checks every run. Observed agreement: **$0.0017**. An early version of this check compared
the LP objective directly against `rates.bill_nem`'s output (which includes BSC) and found
a spurious $289.60 discrepancy — exactly `365 × $0.79343` (BSC), not a modeling bug at all;
fixed by adding BSC back to the LP objective before comparing.

**Energy conservation.** Checked via exact algebraic identities derived directly from the
net-flow relationship (`sum(imp-exp) - sum(house_net) = charge_sum - discharge_sum`), plus
two invariants the gross-flow-preserving fix specifically guarantees: `exp` never exceeds
`gen0` (no manufactured export) and `discharge` never exceeds `imp0` (never flips an
interval to export). Simultaneous gross import AND export in the SAME interval is EXPECTED
and CORRECT whenever the real data has it (~6.3% of intervals) — an earlier, stricter check
would have wrongly rejected exactly the intervals this fix exists to preserve, so it was
replaced with the two invariants above. Charging AND discharging in the SAME interval is
only flagged as wasteful when that interval has just ONE real flow to work with; at a
genuine simultaneous-flow interval, discharging to serve the real import while separately
storing the real solar surplus are two independent, individually rational actions (verified
directly: on the real data, all 311 such simultaneous-action intervals are simultaneous-flow
intervals, none are single-flow — confirmed empirically before writing this check, not
assumed).

**Do not ship: combined bidirectional power cap (Codex adversarial review, fourth pass).**
The gross-flow-preserving fix above capped `discharge_i` at `min(imp0_i, power_kw/4)` and
capped `grid_topup_i + solar_absorbed_i` (combined charging) at `power_kw/4`, but nothing
tied those two caps together: an interval could charge at the full power rating AND
discharge at the full power rating simultaneously, demanding 2x the battery's rated
throughput through what is physically ONE bidirectional inverter — no single-inverter
battery can do that. Fixed by adding `grid_topup_i + solar_absorbed_i + discharge_i ≤
power_kw/4` as a genuine combined inequality (not two independent ones) to `_solve_lp`'s
constraint set, the identical combined-cap logic to `rolling_day_ahead`'s execution-time
re-clip (inherited automatically there, since execution only clips DOWN from a plan that
now itself respects the combined cap), and a matching hard check to `_check_conservation`
and the day-ahead's own inline conservation check. On this house's real data, the annual LP
never actually wanted combined throughput above the cap even before this fix existed —
simultaneous full charge and full discharge always wastes round-trip efficiency for no bill
benefit, so the cost-minimizing solution already avoided it on its own — so the annual
headline figures ($2,546.24/yr perfect-foresight save, $217.24/yr gap) are UNCHANGED. The
day-ahead case DID move slightly: a single day's local LP, optimizing over a much shorter
horizon with a fixed starting SOC, found the combined cap genuinely binding on some days,
moving day-ahead's save from $1,711.13/yr to **$1,711.28/yr** (a $0.15/yr correction) and the
purchasing-statement's "day-ahead worse than greedy" gap from $617.87 to $617.72. Confirmed
by direct instrumentation before writing the fix that the combined throughput on the real
data topped out at exactly the power cap (2.875 kWh/interval = 11.5 kW ÷ 4), never above it,
in both the annual and day-ahead traces — the constraint was a genuine modeling gap, not one
that was silently inflating the published figures by any material amount. (Issue #40 later
generalized this combined cap to the real, asymmetric 5 kW charge / 11.5 kW discharge rates
and moved the day-ahead figure again, by a similar small amount and for a related reason —
see the note immediately below for the current numbers and the correction's own history,
which includes a first, wrong generalization attempt that briefly undid this fourth-pass
fix before being caught and corrected.)

**Issue #40: generalizing the combined cap to asymmetric charge/discharge rates.** Tesla's
own official 2025 Powerwall 3 Datasheet gives DIFFERENT continuous ratings for a single
unit's charge and discharge directions — 5 kW charge vs. 11.5 kW discharge (see
research/battery-research-notes.md) — where the fourth-pass fix above assumed one shared
`power_kw` for both. The combined constraint was generalized to a NORMALIZED form,
`discharge_i/power_kw + (grid_topup_i+solar_absorbed_i)/charge_kw ≤ 1/4`, which reduces
algebraically to the untouched fourth-pass constraint whenever `charge_kw == power_kw`
(verified: byte-identical artifact regeneration at the symmetric default). A first attempt
at this generalization split it into two INDEPENDENT rows (`discharge_i ≤ power_kw/4` and
`grid_topup_i+solar_absorbed_i ≤ charge_kw/4` as separate inequalities) instead of one
combined row — this silently reintroduced the exact bug the fourth-pass fix above exists to
prevent (simultaneous full-rate charge AND full-rate discharge through what is physically
one bidirectional inverter), caught because it failed to reproduce the committed artifact
byte-identically even at symmetric power (the day-ahead figure reverted to the pre-fourth-
pass $1,711.13/yr). Corrected to the single normalized row; the actual regression guards
are `test_perfect_foresight_dispatch.py`'s
`case_solve_lp_combined_power_cap_row_is_normalized_not_two_independent_rows` and
`case_solve_lp_symmetric_normalized_row_is_bit_identical_to_the_fourth_pass_constraint`
(both inspect the real `A_ub` matrix handed to the solver directly and correctly fail when
the two-independent-rows bug is reinstated), plus
`case_check_conservation_rejects_simultaneous_moderate_charge_and_discharge_that_only_
combined_check_catches`, so this specific class of regression can't recur silently.
(An earlier version of this passage also credited
`case_solve_lp_does_not_choose_simultaneous_full_rate_charge_and_discharge_at_symmetric_
power` — formerly named as though it WERE the regression test — with this role; an
independent code-reviewer agent (PR #69) found that test still passes when the bug is
reinstated, because its specific scenario's own economic optimum avoids simultaneous
charge/discharge regardless of whether the combined cap is enforced (fixed SOC boundary,
free/unpriced ending SOC — see the test's own docstring for the full mechanism). It is kept
as a smoke check on `_solve_lp`'s basic behavior, not relied on as a regression guard.)

At the real 5 kW charge / 11.5 kW discharge rates (now this script's own production
default), the effect is small — **~$4/yr, ~0.23%** on the day-ahead persistence figure,
similar in kind to (though larger than) the fourth-pass fix's own $0.15/yr effect above.
Annual and day-ahead-perfect-horizon figures move by under $1 (from a $1-smaller committed
`greedy_save_usd` cross-check, not from the charge cap itself — see below). The day-ahead
persistence case is the one genuinely affected because its LP re-solves independently for
each of 365 days, each starting from a FIXED real SOC rather than the annual solve's
free-to-choose cyclic boundary: on a day whose starting SOC sits low enough that the
planning LP wants to recharge briskly, the real 5 kW charge cap (vs. the prior symmetric
11.5 kW assumption) genuinely binds on some individual days, the same mechanism — a
shorter, more power-constrained per-day optimization — the fourth-pass fix's own $0.15/yr
effect came from. Current figures (this script's own production default,
`charge_kw=5.0`): annual perfect-foresight save $2,545.39/yr (gap $217.39/yr, 9.3% of
greedy's own $2,328/yr saving, 91.5% of theoretical maximum captured); day-ahead
persistence save $1,715.29/yr (up from $1,711.28/yr, +$4.01); day-ahead perfect-horizon
save $2,537.18/yr; myopic-horizon effect $8.21/yr; forecast-error effect $821.89/yr;
leak-sensitivity bound $28.20/yr. `battery_dispatch_policies.json`'s own `pw3.greedy.save`
also moved to $2,328/yr (from $2,329/yr) as part of issue #40's broader propagation (§3.13),
which is why `greedy_save_usd` above differs from $2,329 even apart from the day-ahead
effect.

**The true optimum barely cycles more than greedy.** With the gross-flow fix, the LP's own
cycling (1.06 cycles/day, 4,966.35 kWh discharged) is now close to greedy's own 1.01
cycles/day, 4,720 kWh — a modest, not dramatic, difference. The earlier (incorrect) figure
of 1.69 cycles/day, 7,922 kWh discharged was itself a symptom of the same bug: much of that
"extra" cycling was manufactured export and free-netted import that never had to physically
move through the battery at all.

**Day-ahead forecast (the realistic middle case) — a genuine, disclosed underperformance,
not a bug.** This case commits to a full day's charge/discharge SCHEDULE in advance, planned
against only a persistence forecast: yesterday's actual house-net profile, time-of-day
aligned, stands in for tomorrow's forecast, including a forecast of which future intervals
will see EV-spillover (also persisted from yesterday's real mask — an earlier version handed
the planning LP the REAL future EV mask directly, giving it perfect advance knowledge and
undercutting the "forecast quality, not perfect knowledge" premise; roughly half of this
house's true EV-spillover intervals cannot be reliably anticipated this way). The REAL
EV-exclusion rule and REAL SOC/power feasibility are both still enforced at EXECUTION
regardless of what the plan assumed. Forecast error: MAE 0.6682 kWh, RMSE 1.1766 kWh per
15-minute interval (a real, sizable per-interval error). **Result: $1,715.29/yr saved —
WORSE than the shipping greedy policy's $2,328/yr, not better.**

**Isolating forecast error from the myopic planning horizon (Codex adversarial review, fourth
pass — the second "do not ship" finding of this issue).** Each day's local LP fixes SOC at
the real start-of-day level but leaves it FREE at day's end — unlike the annual solve's
cyclic boundary, no value is placed on SOC held past midnight. Codex correctly flagged that
an earlier version attributed day-ahead's ENTIRE shortfall versus the true optimum to
forecast pre-commitment without first ruling out this second, independent cause: a
day-ahead controller could underperform even with a PERFECT forecast, simply because its
planning horizon never sees past midnight. This is checked directly, not argued away:
`rolling_day_ahead(..., perfect=True)` runs the IDENTICAL day-by-day architecture (same SOC
handling, same real bucket-offset carry-forward) but plans against the REAL same-day data
instead of a persistence forecast. Result: **$2,537.18/yr saved — within $8.21 of the true
annual optimum ($2,545.39/yr)**. That $8.21/yr is the myopic-horizon effect on its own,
holding forecast quality at perfect; the remaining $821.89/yr (of the persistence run's
total $830.11/yr shortfall) is attributable to imperfect forecasting, holding the horizon
fixed at one day. The horizon confound was real to check and worth ruling out, but turned
out small in practice on this house's data — the day-ahead case's underperformance is
almost entirely a forecasting problem, not a horizon problem. Mechanism for the forecast
cost: pre-committing to a schedule the day before hard-caps how much the battery can
discharge at each interval to whatever the forecast anticipated (`discharge_i ≤
forecast_imp0_i` during planning); when reality diverges — which it does, substantially,
every day — that cap forecloses value the shipping policy's simpler real-time reactivity
(react to whatever import is ACTUALLY happening right now, no schedule to be wrong about)
does not give up. The day-ahead run also ends the year holding 5.49 kWh of un-cashed-out
SOC (vs starting near 0), a real, if secondary, symptom of the same limitation.

**Disclosed leakage: day 0 and DST-mismatched days.** 4 of 365 days have no same-length day
to persist from (DST transitions) and fall back to their own actual data for that day only.
Day 0 has the same problem for a different reason: its "prior day" (day 364 of the SAME
year, via the wraparound convention) is, chronologically, 364 days in the future relative to
day 0's own true position — a single year of data has no real prior year to draw a genuine
persistence source from. An earlier version counted day 0 as ordinary persistence anyway;
it is now tracked separately (`n_days_with_leaked_future_information` = 5) and excluded from
BOTH the forecast-error statistics AND disclosed with a quantified bound
(`leak_sensitivity_usd`): re-billing with those 5 days' dispatch replaced by no battery
action at all changes the reported day-ahead save by **$28.20/yr** — small next to the
$830.11/yr gap it sits inside, bounding rather than merely asserting that the leak does not
materially affect the headline figure.

**Bucket netting carries the REAL (not forecast) month-to-date position forward (Codex
adversarial review, second pass).** Each day's bucket-net constraint is offset by the
ALREADY-REALIZED (never forecast — it is the past) net position accrued earlier the same
month in that bucket, via `_solve_lp`'s optional `bucket_offsets` parameter (default zero,
exactly correct for the annual solve, where every bucket genuinely starts empty). A
day-ahead controller genuinely has this information: it is not a forecast quantity. An
earlier version reset every bucket to zero at the start of every day's local LP, discarding
it and conflating genuine forecast error with an avoidable loss of already-known
information — confirmed to reach the LP objective directly (a synthetic +100 kWh offset
shifts a test LP's objective by the expected ~$50, `analysis/test_perfect_foresight_
dispatch.py`). This mechanism did not change the eventual headline day-ahead figure's
correction from the gross-flow fix above, but remains a real, independently tested
correctness property of the day-ahead planning LP.

**Purchasing statement.** The shipping policy already captures **91.5% of the theoretical
maximum** at this hardware — a $217.39/yr optimality gap is small next to the $2,328/yr it
already saves, so there is not much room for ANY controller, however smart, to add. A naive
day-ahead pre-committed schedule based on simple persistence forecasting is NOT a reliable
way to capture more of that gap and can genuinely do WORSE than the shipping policy
($612.71/yr worse, in this case) — and that shortfall is almost entirely a forecasting
problem ($821.89/yr), not a planning-horizon problem ($8.21/yr, checked directly rather than
assumed), so a longer planning horizon alone would not fix it either. No shipping product
changes the controller, only the hardware, so closing the small remaining gap is a
firmware/software question, but this data does not show that "add a day-ahead forecast" is
the answer; the shipping policy's simpler real-time reactivity is, on this evidence, the
safer choice.

**Output** `data/perfect_foresight_dispatch.json`. Registered in `test_scripts_runnable.py`
under `CI_RUNNABLE` (needs only `usage.csv` via `behavior_rebuild.load()` and
`household.yaml`; its optional cross-check against `battery_dispatch_policies.json` is
read-only and skipped gracefully if absent, not a hard tie-out assertion, so synthetic CI
inputs run it cleanly).

**Tests** `analysis/test_perfect_foresight_dispatch.py`, 28 cases: synthetic-frame unit
tests of the bucket assignment (matches `bill_nem_monthly`'s own grouping), the core LP
solver (a same-bucket case where netting alone correctly leaves the battery idle, a
TWO-bucket case that forces genuine physical battery use since netting cannot substitute
for it across buckets — added after a second adversarial review found the original
same-bucket case's docstring wrongly claimed the battery was exercised there, and that
NO case in the file asserted nonzero charge/discharge at all, so a regression that
disabled the battery entirely would have passed all 18 prior cases — confirmed by
monkeypatching the bounds to (0,0) and rerunning: 17 of 18 still passed — EV-exclusion
enforcement, both SOC boundary modes, the bucket-offset mechanism reaching the objective
by the expected amount, and — fourth adversarial review — that the combined-throughput
power-cap row in `A_ub` binds `grid_topup`, `solar_absorbed`, AND `discharge` together, a
structural check on the constraint matrix itself since the real data's own economics
never push combined use above the cap, so a purely behavioral assertion would pass even
with the fix reverted), the gross-flow-preserving conservation checks (simultaneous
real import/export now correctly ALLOWED, manufactured export and over-discharge both
caught, wasteful single-flow simultaneous charge/discharge still caught, and the same
combined-power-cap violation caught by `_check_conservation` directly), and the
day-ahead forecast machinery (cyclic persistence for day 0, energy conservation, SOC
bounds under heavy load, a nonzero real prior-day contribution to a bucket the next day
also touches, `perfect=True` reporting exactly zero forecast error and reacting to a
same-day spike a persistence forecast would have missed, and — third adversarial
review — that the REAL EV-exclusion rule is enforced at execution even when a
forecast-based plan, blind to a real future spillover spike, would otherwise have
discharged into it) need no private archive at all; cases requiring the $1 agreement
with `rates.bill_nem`, the real annual solve's conservation and cyclic closure, the
day-ahead-never-beats-perfect-foresight bound (day-ahead vs greedy is deliberately NOT
asserted either way — a real, disclosed finding, not a test assumption), the
perfect-horizon variant never beating the true optimum, the leak-sensitivity bound
staying small on the real measured year, and byte-identical regeneration gate on
`_require_archive()` and SKIP with the reason named when this checkout lacks `private/`.

### 3.24 `analysis/tou_structure_stress.py` — stress-testing the tariff STRUCTURE, not the rate level (`data/tou_structure_stress.json`)

**Why this is a different sensitivity than §13's escalation ladder.** `battery_dispatch_
policies.escalation()` (§13, index.html's "Battery payback vs rate escalation" table) holds
the TOU window SHAPES fixed and varies how fast $/kWh prices rise. Issue #14's premise: for
a battery with a 10-15 year horizon, a redrawn window boundary — on-peak starting an hour
earlier, the midday super-off-peak window narrowing — changes the arithmetic more than any
plausible escalation rate, and it was completely unmodeled. This script holds today's prices
fixed and varies the window SHAPES instead: on-peak start/end, the weekday midday
super-off-peak window, and the summer-season month set.

**Corpus check first (the issue's own first acceptance criterion).** Before inventing
hypothetical scenarios, the household's own bill corpus was checked for a structural change
that already happened. It has one: `tou_audit.py`'s `refit_changeover()` independently fits
the exact changeover day for the weekday 10am-2pm window from statement residuals (35.4 kWh
unexplained assuming no changeover, 0.5 kWh assuming one) and pins it to `tou_audit.
MIDDAY_SOP_START` (2026-03-01, ambiguous only within the enclosing 2026-02-28..03-02
weekend). Before that date those hours were off-peak; after, super-off-peak. That is exactly
the shape of the "midday super-off-peak narrowed" scenario, so that scenario reverts to the
pre-2026-03-01 structure directly — measured, in-corpus precedent, not an invented one.
`lifetime_payback.py` separately hardcodes a THIRD, older window shape (`_per_old`: midday
SOP only in March/April, pre-2026) for its multi-year valuation, but unlike the 2026-03-01
change, no `tou_audit.py` function scores it against a bill, and the bill corpus here only
starts 2024-05-25 — noted as a lead for a future audit, not relied on as measured precedent.

**The other three scenarios have no in-corpus precedent**, so each cites external, checked
grounding instead of inventing a number, per the issue's own instruction to label an
ungrounded scenario hypothetical:

- **On-peak widened** (16-21 → 14-21, 5h → 7h) and **on-peak shifted later** (16-21 → 17-22)
  both draw on the same real, checked history: SDG&E's own on-peak window was **11am-6pm (7
  hours)** for roughly 30 years before the CPUC's **March 2019** mandated default-TOU
  transition moved it to today's 4-9pm (5 hours), explicitly to track the evening "duck
  curve" net-demand peak as rooftop solar grew (KPBS "SDG&E's New Time-Of-Use Plan
  Explained", Jul 2019; Utility Dive "California utilities prep nation's biggest
  time-of-use rate roll-out"). Widening tests a partial reversion toward that historical
  WIDTH (7h, though not the historical clock hours, 11-18); shifting later tests a further,
  smaller move in the SAME direction the 2019 transition already moved (16-21 → 17-22, not
  the transition's own endpoint) — a real, precedented DIRECTION, though the specific
  magnitude modeled in each case is a bounding choice, not a re-enactment. Labeled
  **historically motivated**, not **measured** (Codex adversarial review, second pass: an
  earlier version labeled both "measured," which overstated how directly this exact
  scenario traces to the cited history — the direction and mechanism are real precedent,
  but neither scenario's own clock hours were ever themselves observed).
- **Summer season extended** one month (Jun-Oct → Jun-Nov) has **no precedent**: SDG&E's
  summer is already longer than PG&E's or SCE's (5 months vs 4 each), and no CPUC proceeding
  defining a longer season turned up in a direct check. Labeled **hypothetical** in both the
  artifact (`precedent: "hypothetical"`) and the report prose, motivated only by real (if not
  yet regulatorily acted-on) evidence that California's fire/heat season is measurably
  lengthening into the traditionally cooler months (NOAA/Yale Climate Connections coverage
  of the Jan 2025 LA fires; Scripps Institution of Oceanography Santa Ana wind-timing
  research) — a directional motivation, not a settled precedent.

**Mechanics: no changes to `rates.py`, `behavior_rebuild.py`, or `battery_dispatch_
policies.py`.** `rates.py`'s canonical `period()`/`period_at()` remain the single source of
truth for the CURRENT tariff. `period_variant()` in the new script is a parametrized
generalization (`on_start`, `on_end`, `weekday_sop_windows`, `weekend_sop_end`) — verified
directly (`test_tou_structure_stress.py`) to reproduce `rates.period` exactly at today's own
parameters, across a fine hour grid, both day types. `assign_structure()` builds a scenario's
own `p`/`seas` columns on a COPY of the real measured year; Consumption/Generation (physical
reality) never change, only which TOU bucket each interval is billed under. Because
`behavior_rebuild.build_sop_index/shift_ev/shift_house` and `battery_dispatch_policies.
run_batt` already read `p`/`hour`/`seas` off the frame they are GIVEN rather than importing
`rates.py` directly, re-running them against a scenario frame naturally re-derives the EV
shift's destination window and the battery's discharge windows under that scenario's own
structure — zero code changes needed in either module. (This module's own hardcoded "on"/
"off"/"sop" string constants, by design a parametrized alternative to the canonical rule,
correctly pass `test_scripts_runnable.py`'s `case_tou_assignment_comes_from_the_canonical_
module` check via its `imports_loader` branch, since the script does import
`behavior_rebuild`; the dedicated reproduction test above is the actual correctness
guarantee, not incidental exemption from that check.)

**The three reported numbers, per scenario, and the combined verdict.** Each scenario
reports `baseline_delta_usd` (change to the no-behavior, no-battery bill), `behavior_save_
delta_usd` (change to the EV-shift-only saving, 100% compliance), and `battery_marginal_
delta_usd` (change to the price-aware/"greedy" battery's marginal saving on top of the
shifted load) — all three recomputed FRESH for the CURRENT structure inside this same script
(not read from a sibling artifact), so every comparison is apples-to-apples on identical
code and identical physical data. A combined `total_package_impact_usd = baseline_delta -
behavior_delta - battery_delta` answers "which structural change hurts most, and by how
much": how much MORE (or less) a fully-optimized household (EV shift + price-aware battery)
would pay per year under that scenario, holding physical usage fixed.

**Independent cross-check.** The CURRENT-structure figures this script recomputes from
scratch ($4,904.13 baseline, $1,220.85 behavior save) agree with `behavior_rebuild.json`'s
scenario (a) to the cent — an independent proof the reused pipeline (`shift_ev`) is wired
correctly, not merely internally self-consistent (`test_tou_structure_stress.py`). The
battery marginal ($2,238.89) is DELIBERATELY $0.80 off `battery_dispatch_policies.json`'s
post-behavior MID figure ($2,238.09) rather than matching it to the cent -- see the
steady-state boundary fix immediately below for why.

**Do not ship (Codex adversarial review, third pass): steady-state battery boundary.**
`run_batt` always starts at `soc0=cap/2` and runs the year once -- a one-time year-1
boundary condition, not a steady annual cycle, the identical issue Codex's adversarial
review already found and fixed for issue #12's `battery_sizing_curve.py` capacity sweep
(`_steady_state_run`, §3.22). Left uncorrected here, a scenario whose altered window shape
happens to leave the battery meaningfully fuller or emptier at year's end than another
scenario would fold un-costed "free" starting charge or un-recovered "stranded" ending
charge into the very DELTA this script exists to report -- exactly the class of defect
that mattered for issue #12's capacity sweep. Checked empirically before fixing: the
boundary drift (ending SOC minus starting SOC) measured 6.054-6.056 kWh across the current
structure and all four scenarios -- nearly IDENTICAL regardless of scenario, unlike issue
#12's sweep across capacities, where the drift's magnitude itself varied with capacity.
Fixed anyway, matching this project's own established rule that "a fix that barely moves
the numbers can still be the correct fix": added `_steady_state_battery()`, a local
reimplementation of `battery_sizing_curve._steady_state_run`'s convergence loop (iterating
`run_batt`, feeding each pass's ending SOC forward as the next pass's starting SOC, until
they converge to within 0.01 kWh) rather than importing that module's underscore-prefixed
internal helper across a script boundary. **Result: every scenario's own `battery_marginal_
delta_usd` and `total_package_impact_usd` are UNCHANGED to the cent** (the near-identical
boundary drift across scenarios cancels almost entirely in the differencing); only the
CURRENT structure's own absolute battery marginal moved, from $2,238.09 (the one-shot
figure) to $2,238.89 (the steady-state figure) -- an $0.80 correction, confirming the
boundary artifact was real but immaterial to every published dollar figure in this section.

**Result: a genuinely counterintuitive finding, verified by direct inspection of the
physical data before publishing it.** On-peak shifting later hurts most (**+$132.81/yr**
even after the EV shift and battery) — the newly-captured evening hour (9-10pm) is pure
grid import with no solar to offset it (confirmed directly: 731.8 kWh imported, 0 kWh
exported in that slot across the measured year, every day of the week — the on-peak window
applies daily, not just on weekdays). Widening on-peak and narrowing the midday
super-off-peak window each LOWER this household's bill (−$515.25/yr and −$1,437.37/yr)
rather than raise it — both reclassify hours when this household is a heavy net EXPORTER
(10am-4pm weekday, confirmed directly: 6,508 kWh exported vs 459 kWh imported across the
10am-4pm weekday window in the measured year) into periods with a materially higher export
credit rate, a windfall a grid-dependent household would not see. This inverts the naive
"wider/narrower window = worse" intuition for a net-exporting solar household specifically,
and was verified against the household's own import/export profile (not merely accepted
because the arithmetic ran without error) before being written into the report. The summer
extension is roughly neutral (−$1.04/yr).

**Tests** `analysis/test_tou_structure_stress.py`, 15 cases: `period_variant` reproduces
`rates.period` exactly at CURRENT's parameters; `assign_structure` preserves physical
load; each scenario's window reclassification checked directly (midday-narrowed reverts
weekday 10-14 to off-peak while leaving 0-6 and on-peak alone; widened reclassifies 14-16;
shifted-later drops 16-17 and picks up 21-22; summer-extended reclassifies only November);
`run_batt`'s discharge window genuinely tracks a scenario's own on-peak reassignment
rather than the hardcoded clock hours an earlier version tested (adversarial review,
first pass — a synthetic fixture places a single >=2.5 kW spike at a slot that changes
on/off status between structures, isolating the discriminating case so an unfixed bug
would fail rather than pass vacuously); every scenario's precedent label is one of
measured-in-corpus/historically-motivated/hypothetical (Codex adversarial review, second
pass added the "historically motivated" tier, distinct from "measured," for a scenario
whose direction is real precedent but whose exact magnitude was never itself observed),
the summer-extension scenario specifically must be hypothetical, and the midday-narrowed
scenario specifically must be measured, in-corpus; the midday-narrowed precedent note cites
`tou_audit.MIDDAY_SOP_START`
live rather than a hand-copied date; `total_package_impact_usd` is the exact hand-derived
combination of the three deltas; every scenario's EV shift conserves energy; the
steady-state battery boundary genuinely converges (soc0 approx soc_final within
STEADY_STATE_TOL_KWH) for the current structure and all four scenarios on the real
measured year (Codex adversarial review, third pass); the current-structure recomputation
matches the committed sibling artifacts; the committed `worst_scenario` is the true argmax
on the real measured year; and byte-identical artifact
regeneration.

**Output** `data/tou_structure_stress.json`. Registered in `test_scripts_runnable.py` under
`CI_RUNNABLE` (needs only `usage.csv` via `behavior_rebuild.load()` and `household.yaml`;
unlike `perfect_foresight_dispatch.py` it reads no sibling artifact at all, only recomputing
its own CURRENT-structure baseline fresh, so there is no optional cross-check to skip).

### 3.25 `analysis/uncertainty_propagation.py` — putting an error bar on the recommendation: a 7-input Monte Carlo that reproduces the old 3-input one as a verified special case (`data/uncertainty_results.json`)

**Why the old Monte Carlo was not enough (issue #15).** `deep_analyses.py`'s `monte_carlo`
block draws three inputs (rate escalation, battery capacity fade, install cost) around one
point base case (`post_behavior.mid.battery_marginal`). That is real uncertainty
propagation, but it is not the uncertainty this project has actually measured elsewhere:
issue #4 found the escalation TREND itself "not determined" (`data/tou_spread.json`'s
`per_period.summer/winter` verdicts — a structural break, not a survives-scrutiny trend);
the three-way production validation (`data/threeway_production_validation.csv`) shows two
independent monitoring sources disagreeing on the same physical production by a few
percent; the soiling analysis (`data/soiling_results.json`) has two genuinely different
rate estimates depending on which evidence window is trusted ("split evidence" in its own
words); round-trip efficiency is a nameplate spec, never independently measured here; and
the battery-marginal base case is itself conditional on an EV-charging behavior shift
persisting, which is a real, previously unquantified risk. This script draws all seven of
those inputs and reports the payback/NPV question as a probability distribution rather than
a point estimate, without editing `deep_analyses.py` or its artifact (both stay
byte-identical — this script never imports `deep_analyses.py`).

**The seven input distributions and their evidential basis:**

| Input | Distribution | Evidential basis |
|---|---|---|
| Escalation | Uniform(0%, 12%) | **Estimated** — `data/tou_spread.json`'s `battery.uniform_ladder` bounding range (3/5/8/12%); the escalation TREND itself is "not determined" in that artifact, so this is a bounding scenario range, not a measured rate. Floor kept at the old model's 0%; ceiling asserted at build time to equal the ladder's own top scenario, so a future `tou_spread.json` regeneration cannot silently drift out of sync. |
| Degradation (battery capacity fade) | Uniform(0.5%, 2.5%)/yr | Manufacturer (Powerwall 3) warranty degradation curve — unchanged from the old Monte Carlo. Not solar panel degradation (a separate, already-published ~0.5-1.0%/yr figure, index.html §9), which answers a different question. |
| Install cost | Uniform($12,500, $17,000) | Quoted installer cost bound — unchanged from the old Monte Carlo. |
| EV-behavior persistence | Beta(2,1) compliance fraction *c*, mean 0.667 | **Estimated** — blends `battery_dispatch_policies.json`'s pre-behavior marginal (`pw3.greedy.save`, *c*=0) and post-behavior marginal (`post_behavior.mid.battery_marginal`, *c*=1), the only two compliance points the pipeline computes. This is a MODELED, not-yet-implemented change (§7 recommends it as a pending action, "do it this week," not something this household has sustained) — an earlier draft wrongly called it an already-observed, completed behavior to justify a more confident Beta(4,1) prior (Codex review pass 3 finding). The milder skew toward *c*=1 reflects only the indirect evidence that ~80% of this household's EV charging already lands in favorable windows unshifted (2,618 of ~13,100 kWh/yr currently mis-timed, per `behavior_rebuild.py`'s own session detection). |
| Soiling / production loss | Triangular(0, 0, lossB) | `data/soiling_results.json`'s two named, genuinely different scenarios, reframed relative to the OBSERVED baseline (Codex review pass 2): the Green Button `Generation` column is this year's actual, already-soiled production, and scenario A **is** "this year's evidence" — so the observed data already embeds roughly scenario A's own loss, and scaling it down by scenario A's raw loss fraction would double-subtract that loss. `lossB` = the INCREMENTAL further loss (as a fraction of measured annual generation) to reach `scenario_B_2024_cleaning_evidence`'s worse, dirtier rate, relative to that same observed baseline — not scenario B's raw loss applied on top of an already-reduced series. Converted into a battery-saving derate via a REAL, calibrated sensitivity (below), not an assumed proportionality. |
| Round-trip efficiency (RTE) | Uniform(85%, 95%) | **Engineering estimate** around the Powerwall 3 nameplate 90% round-trip spec (`battery_dispatch_policies.py`'s `ETA = sqrt(0.90)`); no independent RTE measurement exists in this repo for this household. |
| Production measurement spread | Normal(mean 1.0, sd ≈2.05%) | **Empirical** — `data/threeway_production_validation.csv`'s 365-day PVOutput-vs-Enphase-meter comparison. The sd used is the ANNUAL relative gap between the two full-year totals (≈2.05%), not the larger day-to-day relative std (≈2.8%): the annual gap tracks the MEAN daily gap rather than shrinking by 1/√365, which is evidence the two meters disagree systematically (a persistent accounting/calibration gap) rather than each day being an independent noisy draw that would average out. Routed through the SAME calibrated generation-sensitivity as soiling (`soil_slope_loss`/`soil_slope_surplus`, below) rather than applied as a direct 1:1 multiplier on the dollar saving — a production-measurement discrepancy and a soiling-driven generation change are uncertainty about the identical physical quantity, so an equal-fraction change from either source must move the saving identically PROVIDED it's on the same side (a shortfall from either source routes through `soil_slope_loss`; a surplus from either source routes through `soil_slope_surplus`, issue #89 — the two are no longer one shared number, since `scale_production()`'s loss/surplus reallocation is genuinely asymmetric by physical design) (adversarial review pass 2, finding 2: an earlier draft assumed a 1:1 response, overstating this lever's swing roughly 1/`soil_slope`-fold). |

**Calibrating the RTE and soiling saving-sensitivity from the REAL engine, not an assumed
proportionality.** Rather than guess how much a change in round-trip efficiency or a
soiling-driven generation loss moves the battery's marginal saving, the script reruns
`battery_dispatch_policies.run_batt`/`.billed` (imported read-only, never edited) at RTE ∈
{0.85, 0.90, 0.95} and generation scaled by {1, 1-lossA, 1-lossB}, on both the pre- and
post-behavior load — six to eight real dispatch reruns. `ETA` is a module-level constant
`run_batt` reads by name at call time rather than a function parameter, so a temporary
`battery_dispatch_policies.ETA = ...` override for one calibration call (restored
immediately after) changes its behavior without editing the file. Each lever's no-battery
baseline bill is recomputed AT THE SAME generation scale as its battery run (adversarial
review pass 1, finding 1: an earlier draft billed the scaled-generation battery run against
an unscaled-generation baseline, which folded the direct cost of lost solar into what was
supposed to be an isolated battery effect and pulled the soiling slope sharply, spuriously
negative). A linear factor(x) = 1 + slope·(x - nominal) is fit by least squares to each
lever's points. The pre- and post-behavior calibration runs land on nearly identical
fractional slopes (RTE: +0.552 vs +0.592 per unit RTE; soiling: +0.0565 vs +0.0568 per unit
loss fraction, both small and positive once correctly isolated — soiling's realistic
1.3-6.6% loss range moves the battery marginal by well under 1%) — evidence that averaging
the two into a single slope, applied to whichever pre/post-behavior blend a given Monte
Carlo draw lands on, is a reasonable simplification rather than a fabricated shortcut.
Every calibration point runs `run_batt` to a converged, steady-annual-cycle SOC boundary
(iterating with each pass's ending SOC fed forward as the next pass's starting SOC until
they agree within 0.01 kWh) rather than the single one-time pass from a fixed `cap/2` start
— the identical boundary-condition fix `tou_structure_stress.py`'s own `_steady_state_
battery` applied for issue #14, reimplemented locally here (Codex review pass 1, finding 2).
This nominal recomputation (`pre_nominal` $2,327.42, `mid_nominal` $2,238.89) legitimately
differs from `battery_dispatch_policies.json`'s own committed figures (`pw3.greedy.save`
$2,328, `post_behavior.mid.battery_marginal` $2,238) by ~$1-2 — the known, expected size of
the steady-state-vs-single-pass difference, not a stale artifact — so the build-time
cross-check instead recomputes a SEPARATE single-pass figure (`pre_nominal_single_pass`/
`mid_nominal_single_pass`, using `battery_dispatch_policies.py`'s own uncorrected method
exactly, which that module is out of this issue's scope to change) and compares THAT against
the committed artifact within $1, raising `SystemExit` on disagreement — the same fail-loud
convention `deep_analyses.py`'s own `_base_save()` uses for a stale sibling artifact. Both
figures and the reasoning are recorded in the artifact's own `calibration.steady_state_vs_
single_pass_note` field.

**Gross production reconstructed from SAM 8760, not net export (issue #60, resolved).**
Previously (Codex review pass 1 finding 1 on issue #15) the Green Button `Generation` column
— net grid EXPORT, not gross PV production, since self-consumed solar never crosses the
meter — was scaled directly by a loss/noise fraction, which likely understated both the
soiling and production-measurement-spread slopes.

`reconstruct_gross_production()`/`scale_production()` fix this — and went through four
further review-caught corrections of their own before shipping, each finding something the
algebraic energy-conservation check (verifying the reallocation's own arithmetic is
self-consistent) could not see by construction, since that check never verifies `P` means
the right physical thing or that an allocation is otherwise physically sound.

**First (Codex adversarial review, issue #60, first pass): SAM data misidentified.** An
earlier draft assigned the SAM 8760 files' raw hourly value straight to `P` (production) —
but the two staged Enphase SAM 8760 exports (`samA.csv`/`samB.csv`) are whole-home GROSS-LOAD
kWh, the SAME CT-metered consumption series `threeway_production_validation.py`'s own
`load_sam_hourly()` reads, NOT production directly. That draft would have claimed ~29,866
kWh/yr of PV production against this repo's own validated `meter_derived` total of 16,459.2
kWh/yr — an ~82% overstatement. **Fixed**: gross production is DERIVED per hour via the SAME
identity `threeway_production_validation.py`'s own `derive_daily()` uses — `pv_hour =
max(sam_load_hour - import_hour + export_hour, 0.0)` — computed from this script's own
already-loaded 15-minute data grouped into hours (reimplemented locally per this repo's
established convention for this exact situation, rather than importing across the module
boundary).

**Second (Codex adversarial review, issue #60, second pass): flat intra-hour split.** A flat
`pv_hour / N` split across each hour's intervals ignored real intra-hour shape, producing a
physically-impossible NEGATIVE implied household load on 407 of 35,040 intervals. **Fixed**:
each net-EXPORTING interval within an hour now gets its own net export as a floor first, and
only the hour's REMAINING production is spread evenly across all its intervals — still sums
to `pv_hour` exactly, but guarantees the diagnostic implied-load figure stays non-negative
everywhere it's mathematically possible to (verified: zero deficit hours exist anywhere in
this household's real measured year).

**Third (Codex adversarial review, issue #60, third pass): DST clock misalignment.** The SAM
8760 export is a FLAT 24-hours-a-day grid, never adjusted for DST (this repo's own documented
fact — `service_headroom.py`'s "DST" section, `threeway_production_validation.py`'s own
`dst_dates_in()` exclusion) — while Green Button `d` is true wall clock (23 real hours on the
spring-forward day, 25 on fall-back). Joining the two by bare `(date, hour)` on either
transition date silently pairs SAM's flat-clock hour against the wrong real wall-clock hour
for ~48 of 35,040 intervals a year. **Fixed** the same way this repo's own precedent handles
it: the two DST transition dates are EXCLUDED from the SAM join entirely, taking a
conservative, explicitly-labeled fallback instead (`P = max(net, 0)`, i.e. no self-consumption
modeled for those ~48 intervals) rather than trusting a misaligned join for 2 days out of 365.
This reconstruction lands at 16,521.4 kWh/yr — 0.4% from the validated 16,459.2 kWh figure,
the gap fully explained by covering 363 non-DST days (this script's own measured window minus
the two DST-fallback dates) against that validation's own stricter, differently-scoped
363-day window, not a discrepancy to chase further.

**Fourth (Codex `review`, final pass): import must never DECREASE under a production loss.**
An intermediate version of `scale_production()` (between the second and this final pass)
reallocated via `D`/an "overlap" component (2,206 of 35,040 intervals with simultaneous
import AND export) scaled proportionally with `gen_scale`. That correctly let export shrink
on a net-importing overlap interval, but ALSO let IMPORT shrink on a net-EXPORTING overlap
interval whenever export alone had enough margin to absorb the whole loss — physically
backwards: less production available can only require the same or MORE grid draw to meet a
fixed load, never less. Codex's own worked example: `P=5, imp0=1, gen0=4, gen_scale=0.8`
should reduce export from 4 to 3 while leaving import at 1 exactly (a 1 kWh loss, smaller
than the 4 kWh export margin, fully absorbed by export alone) — that intermediate version
instead produced export 2.8, import 0.8, importing LESS after a production loss. **Fixed**
with a simpler, correct model that also eliminates the need for `D`/"overlap" in the core
math entirely: a shortfall (`loss = P * (1 - gen_scale)`) reduces the REAL measured export
(`gen0`) directly — export is tied to production, so it absorbs a shortfall first, however
much of it exists, including any simultaneous-flow portion, since `gen0` already IS the real
gross export for that interval — floored at 0, with only the EXCESS spilling into import,
which is otherwise untouched (monotonic in `gen_scale` by construction, never negative for a
loss). `gen0` is used directly rather than a scenario-specific reconstruction, matching the
existing convention that export is independent of which `imp_base` scenario (real vs.
EV-shifted) is under test — so this also resolves, by construction rather than special-
casing, a second `review` finding about the post-behavior calibration needing the SAME
treatment: since the reallocation never reads `imp_base` at all, it can't drift out of sync
between the two scenarios.

**The correction, quantified and energy-conservation-checked, not just directionally
asserted.** At this household's own `lossB` (5.28%), the lost 872.2 kWh/yr splits into 790.7
kWh less export and 81.6 kWh more import (the two sum to the total lost, exactly, to a
fraction of a kWh — `calibration.production_reconstruction.energy_conservation_check`, not
just trusted from the generator's own arithmetic). ~9% of the lost energy was being
SELF-CONSUMED, invisible to the old export-only scaling entirely, and increases IMPORT
(billed near the full retail rate) rather than only reducing export (billed at the lower NEM
credit rate) — confirming the issue's own "likely understates" hypothesis with a specific,
quantified mechanism, not just a directional hunch. `soil_slope_mid` rose from 0.0561 to
0.2176 (`old_vs_new_soil_slope` in the artifact) — an ~3.9x larger PER-UNIT slope — with the
REALIZED swing at this household's actual loss fraction staying modest (≈1.1%,
`soil_slope_mid * lossB`). Downstream, the corrected calibration is a genuine but modest
correction to the published Monte Carlo (soiling/production-measurement-spread remain
low-swing tornado levers, dominated by install cost, escalation and degradation): median
payback unchanged at 5.8 yr, p10 unchanged at 5.1 yr, p90 narrows slightly from 6.9 to 6.8
yr, 10-yr NPV median rises from $7,474 to $7,510 at 4% discount ($4,318 to $4,359 at 7%) —
`index.html`'s own §6 mention of these specific figures updated to match, the only report
location citing this artifact's own headline numbers (grepped to confirm no other instance
was missed).

**Two-sided soiling/production-measurement slope (issue #89, resolved).** The calibration
above fit ONE `soil_slope` from a single loss-side dispatch rerun (`gen_scale = 1-lossB`)
and applied it linearly to both directions in `save1_of()` — a `loss` (soiling, always ≥0)
and a `prod_noise`-implied `(1 - prod_noise)` (production-measurement, either sign). This
was exact before issue #60 (the old `gen0*gen_scale` scaling was linear in `gen_scale`, so
extrapolating its own fitted slope cost nothing); issue #60's `scale_production()` is
deliberately ASYMMETRIC (a loss reduces export first; a surplus reduces the scenario's own
import first, self-consumption absorbing it, before spilling into more export), so
extrapolating the loss-fit slope to the surplus side is no longer exact. **Fixed**:
`dispatch_calibration()` now reruns a real THIRD dispatch point at the mirrored surplus
scenario (`gen_scale = 1+lossB`) every regeneration and fits `soil_slope_surplus` from it
directly — two separate 2-point fits (`{nominal, loss}` and `{surplus, nominal}`), not one
3-point line through all three, since the physical relationship is genuinely piecewise, not
one straight line. `save1_of()` now selects `soil_slope_loss` or `soil_slope_surplus` by
the sign of its input (`np.where`, vectorized-safe for both the Monte Carlo's array draws
and tornado()/escalation_downside_sensitivity()'s scalar calls). At this household's own
`lossB` (5.28%), the real surplus-side slope (`soil_slope_surplus_mid` +0.3404) came out
steeper than the loss-side slope (`soil_slope_loss_mid` +0.2176, ratio ≈1.56 — steeper than
this issue's own filing ballpark of ~1.06, which was a pre-fix estimate rather than a real
dispatch rerun of the self-consumption-first surplus model this fix implements).
Extrapolating the old loss-fit slope to the real surplus point would have predicted
$2,213.17 against the real measured $2,198.66 — a $14.51 (0.65%) ONE-SIDED-EXTRAPOLATION gap
now eliminated by construction (`calibration.production_reconstruction.surplus_slope_fix` in
the artifact) — specifically that gap, not every discrepancy `save1_of()` has. A separate,
smaller residual that USED TO remain (Codex review, issue #89, pass 2-3: ~$3.5, ~0.16% at
this household's real surplus point) from averaging `soil_slope_loss`/`surplus` across
mid/pre before applying them to the `c`-blended `base_marginal` — the same convention
`rte_slope` used to follow — is now resolved; see "Mid/pre slope averaging (issue #107,
resolved)" below.
Downstream this is a small correction: `production_measurement_spread`'s own tornado swing
widens from 0.0680 yr to 0.0761 yr at full precision (both round to the same 0.1 yr in the
published, rounded artifact field), and the full Monte Carlo's NPV percentiles shift by a
few dollars (10-yr NPV median at 4% discount $7,510 → $7,496, at 7% discount $4,359 →
$4,357) — soiling and production-measurement-spread remain low-swing tornado levers either
way, dominated by install cost, escalation and degradation.

**Combining loss and prod_noise before selecting a slope side (Codex review, issue #89,
pass 1).** `loss` and `prod_noise` both perturb the SAME physical quantity (true generation
relative to nominal). A first draft of the piecewise design applied them as two INDEPENDENT
multiplicative factors, each separately selecting its own slope side by its own sign — exact
while both sides shared one slope (pre-#89), but with genuinely different loss/surplus
slopes that introduces a first-order spurious bias whenever the two draws partially offset
(e.g. a soiling-loss draw coinciding with an equal-and-opposite production-measurement-
surplus draw). Quantified at this household's real `prod_sigma`/`lossB` via a 2M-draw
simulation: a +0.28% mean bias across the Monte Carlo's own draw distribution — real, not
Codex's constructed edge case alone. Fixed by combining the two into ONE exact shortfall
variable before any slope is selected: `true_relative_generation = (1 - loss) * prod_noise`,
so `combined_x = loss + x - loss*x` where `x = 1 - prod_noise` — one slope side, one factor.
The figures above (swing 0.0761 yr, NPV $7,496/$4,357) are this corrected version's own
output, not the intermediate biased one.

**Mid/pre slope averaging (issue #107, resolved).** Every slope-based lever (`rte_slope`,
and — after issue #89 — `soil_slope_loss`/`soil_slope_surplus`) used to be averaged across
the mid- and pre-behavior calibration runs into ONE value, then applied to the ALREADY
`c`-blended `base_marginal = c*mid + (1-c)*pre`. At `c=1` (pure post-behavior) this used a
slope that was half pre-behavior; the real mid-only calibration point was never exactly
reproduced, and symmetrically at `c=0`. Quantified at this household's real soil-surplus
point: the old averaged-slope prediction was $2,202.19 against the real measured $2,198.66
— the $3.53 (0.16%) gap issue #89's own review pass first surfaced. **Fixed**: `save1_of()`
now applies each side's OWN slope to that side's OWN nominal value FIRST
(`mid*factor_mid`, `pre*factor_pre`), THEN blends the two resulting dollar figures by `c`
(`c*mid_adjusted + (1-c)*pre_adjusted`) instead of blending the two marginals before
applying one averaged factor. Because `soil_slope_loss`/`soil_slope_surplus` are each fit
from an EXACT 2-point line (`{nominal, loss}` or `{surplus, nominal}`), this makes
`save1_of()` reproduce the real soil calibration points EXACTLY at `c=1`/`c=0` — confirmed
to a residual of 0.0 (to float precision) at this household's own calibration
(`calibration.mid_pre_slope_unaveraging_fix.soil_surplus_point_mid` in the artifact). The
RTE lever is NOT exactly reproduced even by this fix: `rte_slope_mid`/`rte_slope_pre` are
each fit by LEAST SQUARES across THREE points (`RTE_LO`/`RTE_NOM`/`RTE_HI`, not two), which
leaves an inherent best-fit residual (well under 0.2% either way at this household's real
calibration, `calibration.mid_pre_slope_unaveraging_fix.rte_points_mid` in the artifact)
independent of mid/pre averaging — a single straight line generally cannot pass through
three real (non-collinear) points exactly. This fix removes the mid/pre-averaging
CONTRIBUTION to that gap (the specific thing this issue targets) but does not and cannot
remove the 3-point-fit residual itself. That residual does NOT shrink uniformly at both
RTE points (adversarial review, issue #107, round 1): it gets WORSE at `RTE_LO` (the old
averaged-slope error happened to partially cancel the independent 3-point-fit residual
there, by coincidence, not by design) and smaller at `RTE_HI` -- both directions disclosed
live in the artifact's `rte_points_mid`/`rte_points_pre`, not summarized as uniformly
small. Downstream this is a
very small correction, smaller than issue #89's own: 10-yr NPV median at 4% discount $7,496
→ $7,497, at 7% discount $4,357 → $4,360; payback median/p10/p90 unchanged at the published
1dp rounding (5.8/5.1/6.8 yr).

**Correlation structure: assumed independent, stated bias direction.** All seven draws are
independent random variables. No correlation between them is measured anywhere in this
repo, so none is modeled numerically — but two plausible real correlations both point the
SAME direction: (a) escalation and soiling could positively correlate (a warmer/drier
climate pattern raising both wildfire-driven rate escalation and soiling accumulation); (b)
round-trip efficiency and capacity fade both trend with cell aging and heat, so a
hard-cycled or hot-climate pack would tend to show both together. Modeling all seven as
independent therefore likely UNDERSTATES the true probability of the worst-of-both-worlds
tail (e.g. high escalation together with high soiling, or low RTE together with high fade)
relative to reality, because independent sampling under-represents scenarios that share a
common root cause. No numeric correction is applied — no data in this repo quantifies
either correlation (CLAUDE.md §0: state what would settle it, not a guessed value).

**Reproducing the old 3-input Monte Carlo as a verified special case (issue #15's AC5).**
`legacy_reproduction()` draws exactly the old three inputs, in the old order, from the same
seeded `numpy.random.default_rng(42)`, fed the COMMITTED (already-rounded) `post_behavior.
mid.battery_marginal` as its base case — matching `deep_analyses.py`'s own `_base_save()`,
which reads that same rounded committed figure rather than an unrounded recomputation (an
earlier draft of this script fed its own higher-precision recomputation instead and the
resulting NPV differed from the committed artifact by $3 after 5,000 draws — traced to that
rounding mismatch, not RNG drift, and fixed by reading the committed figure directly). The
other four levers are not drawn at all in this mode — not merely fixed, never touched by the
RNG stream — so the (escalation, fade, price) arrays are bit-identical to `deep_analyses.
py`'s own, and `test_uncertainty_propagation.py`'s
`case_legacy_reproduction_matches_committed_deep_results_exactly` asserts every field of
`data/deep_results.json`'s `monte_carlo` block matches to < 1e-9 — exact equality, not
"close", because a fixed-seed RNG has no sampling variance left to be close about.

**Tornado reconciliation against `data/extended_results.json`'s `tornado_battery` (issue
#15's AC6).** `extended_findings.py`'s tornado sweeps four different things: `install_cost`,
`dispatch_policy` (a discrete DESIGN CHOICE among evening/twowin/greedy, not an uncertain
physical input), `post_behavior` (a 2-point sensitivity: G vs G_POST), and
`escalation_5yr_avg` (an average-uplift approximation over a narrower 0-8% band). This
script's own ranking, most-to-least swing on the real measured year: **install_cost** and
**escalation** (1.6 yr each, over $12.5-17k and 0-12% respectively), **degradation** and
**round_trip_efficiency** (0.3 yr each), **ev_persistence** (0.2 yr), **soiling** and
**production_measurement_spread** (0.0 yr each — both route through the same calibrated
generation-sensitivity, see below, which shrinks their realistic-range effect to well under
a tenth of a year). Reconciliation: `install_cost` (the
one directly shared lever, same band) matches closely (old 2.1 yr vs new 1.6 yr — both root
in the same `post_behavior.mid.battery_marginal`-derived base case); `escalation`'s larger
swing here (1.6 yr vs the old model's 0.9 yr) is an expected reordering, not a disagreement
— the old sweep used a narrower 0-8% band with an average-uplift approximation, this one
sweeps the full 0-12% ladder-bound range directly; `ev_persistence` generalizes the old
`post_behavior` 2-point lever into a continuous Beta(2,1) blend across the SAME two
endpoints and lands on a swing of the same order (0.2 yr vs 0.3 yr); `dispatch_policy` (the
OLD model's largest lever, 2.2 yr) has no counterpart here because it is a design choice the
household makes, not an uncertain input to propagate — this Monte Carlo holds it fixed at
greedy throughout, matching the old model's own base case; `soiling`, `round_trip_efficiency`
and `production_measurement_spread` are new levers this issue adds, never quantified
anywhere else in the repo. The full numeric comparison is regenerated into the artifact's
own `tornado.reconciliation_vs_extended_results_tornado_battery` field, not hand-copied here.

**The comprehensive result (`battery_marginal_only_full_model`, N=5,000, seed 43, on the
real measured year).** Payback median 5.8 yr (p10-p90 5.1-6.9 yr); under this model's stated
assumptions, 5,000 of 5,000 draws repay within the 10-yr warranty and within 15 years; 0 of
5,000 draws never repaid within the 25-year horizon this script treats as the outer bound of
"never" (the same horizon `deep_analyses.py`'s own loop already uses, `range(1, 26)`). A
finite 5,000-draw sample observing zero failures does not itself prove a true probability of
exactly 1.0 (adversarial review pass 1, finding 2), so the artifact reports the raw counts
and a one-sided 95% Clopper-Pearson bound: repayment-within-warranty is bounded below at
99.94%, and never-repaying is bounded above at 0.06%. That bound is itself only a
finite-sample statement CONDITIONAL on this exact model (adversarial review pass 2, finding
1) — it quantifies sampling error within the seven assumed input distributions, not
uncertainty in whether those distributions or their independence assumption are themselves
correct, several of which are labeled "estimated" rather than "measured" above; the artifact
records this distinction explicitly in a dedicated `epistemic_caveat` field, separate from
the sampling-only `finite_sample_caveat`, and the report states the result as conditional on
the model rather than as an unconditional real-world guarantee. NPV: 10-yr median $7,474 at
a 4% discount rate ($4,318 at 7%); 15-yr median $18,875 at 4% ($12,215 at 7%) — reported per
draw as the standard `-price + PV(savings)`, unlike the old artifact's own
`npv10_at_4pct_median` (a `median(npv) - median(price)` convention, reproduced exactly but
only inside `legacy_reproduction()` for special-case matching, not used for this
comprehensive figure).

**Run** from `private/verify` per the standard sandbox (needs `usage.csv`/`samA.csv`/
`samB.csv`/`rates.py`/`behavior_rebuild.py`/`battery_dispatch_policies.py` beside it, plus
`private/household.yaml`; recomputes its own calibration fresh every run — no cached
intermediate state — so two runs on the same inputs are byte-identical, verified with
`cmp`). Tests: `analysis/test_uncertainty_propagation.py`, following `test_battery_sizing_
curve.py`'s convention (`household.PATH` stubbed with a synthetic YAML before import so the
file imports cleanly with no private data; archive-dependent cases gate on the private
Green Button archive's presence and SKIP rather than fail when it is absent). Registered in
`test_scripts_runnable.py`'s `MANIFEST` (generator) and `NEEDS_PRIVATE_ARCHIVE` rather than
`CI_RUNNABLE`, matching `battery_plan_matrix.py`'s and `carbon_dispatch_tradeoff.py`'s
classification: its hard tie-out against the committed `battery_dispatch_policies.json` and
`data/deep_results.json` (built from the real year) must diverge and trip on synthetic
inputs by design, so it runs against the real archive only.

**Two scope questions from issue #15's review, resolved without a model change (issue
#59).** Both checked against real evidence and documented in the artifact itself
(`dispatch_policy_adherence_note`, `escalation_two_sided_evidence_note`) and in this
script's own module docstring, not left as a silent gap.

*Dispatch-policy adherence risk* — whether the Powerwall's own automation reliably
EXECUTES the chosen `greedy` policy (distinct from WHICH policy to choose, already
addressed by `reconcile_tornado()`'s existing note) — was checked (WebSearch, 2026-08)
for a citable adherence/no-show rate against: Tesla's own published specs; Wood Mackenzie
and EnergySage industry reports; a Solar Insure Powerwall reliability study
(solarinsure.com/tesla-powerwall-reliability-study); CPUC's ELRP demand-response
load-impact evaluation (calmac.org/publications/PY2024_SCE_DR_Program_Report_ELRP_
FINAL_PUBLIC.pdf); and a PG&E-sponsored residential-battery VPP pilot study
(dret-ca.com). None of these quantifies dispatch-schedule adherence: the Solar Insure
figure is a warranty-claims hardware failure rate (unit died / needed replacement), not a
measure of whether a working unit follows its configured schedule; CPUC's evaluation
aggregates across technologies without isolating residential battery storage; no
compliance metric could be confirmed in the PG&E pilot study. Left "not determined" per
CLAUDE.md §0, bounded to the sources actually checked, rather than modeled from an
invented number.

*Two-sided escalation distribution* — whether the `Uniform(0.00, 0.12)` escalation input
should allow for rates falling, not just rising. `esc` scales `save1` (the battery's
marginal SAVING, proportional to the on-peak/off-peak/super-off-peak SPREAD it arbitrages)
by one uniform factor in `payback_of()`/`npv_of()` — so the decision-relevant question is
the SPREAD trend, not any single period's own absolute level, and `tou_spread.json`'s own
dedicated, structural-break-tested spread analysis already answers that: "not determined"
in BOTH seasons (`battery.per_period`) — genuinely unknown, not evidence for either a
positive or a zero-floored range.

An earlier draft of this section argued per-TOU-cell absolute-level trends (on-peak
delivery rates rising, individually, with 95% CIs excluding zero: summer +7.63%/yr,
winter +11.3%/yr) PROVED the 0% floor correct. **Retracted** (Codex adversarial review,
issue #59, first pass): that conflates "the price level in each period has risen" with
"the arbitrage margin has risen" — two different quantities. Winter on-peak and winter
off-peak, for example, moved on nearly identical trajectories over the same window (same
rate_first $0.26687 and rate_last $0.31174 for both; 11.3%/yr vs 12.06%/yr) — exactly the
case where both periods rising together leaves the SPREAD roughly flat, not evidence it
widened. What remains true, stated more carefully: no cell or spread-level figure in this
repo, at any rigor, shows a statistically significant NEGATIVE trend for this household's
own tariff (super-off-peak's negative point estimate, ~-21%/yr both seasons, has a 95% CI
crossing zero in both — [-61.54, 60.62] summer, [-49.31, 20.86] winter). Externally
(WebSearch, 2026-08), California IOU electric rates HAVE fallen in a real, documented
multi-year episode: PG&E's residential rates dropped in several separate 2024-2025 rate
actions, ~$12/mo lower by October 2025 for a typical customer (pge.com/en/newsroom/
currents/energy-savings, nasdaq.com/press-release/pge-lower-electric-prices-jan-1-fourth-
decrease-two-years-2025-12-30), driven by an identified mechanism — AB 1054 wildfire-safety
capital costs rolling off the rate base (docs.cpuc.ca.gov/PublishedDocs/Efile/G000/M523/
K181/523181110.PDF) — a real, citable magnitude for a rate decline. But that mechanism is
not shown to apply to SDG&E specifically: the same research found SDG&E's own electric
delivery rate rose over a comparable recent window even as its gas transportation rate
fell. PG&E's magnitude is not validly transferable into an SDG&E-specific negative bound
without fabricating one.

**Decision: the 0% floor is kept, honestly re-labeled.** It is an INHERITED assumption
from `deep_analyses.py`'s original design, not one this repo's evidence proves correct —
a real, stated LIMITATION of the current model, not a resolved question. Replacing it with
a different unevidenced negative number (self-invented or borrowed from PG&E) would trade
one unproven assumption for another, not improve on it. `data/uncertainty_results.json`'s
`escalation_two_sided_evidence_note` carries this reasoning, including the retraction, and
what would actually settle the underlying question — a per-TOU-period battery-savings
model built from real dispatch reruns at each period's own separately-measured rate path,
rather than one blended scalar — was filed as issue #87 (a model-design change, out of
issue #59's own scope box).

**Issue #87 resolved as "document the gap," not "build the model."** `tou_spread.json`'s
`delivery_cell_escalation` resolves ON-PEAK to a tight, zero-excluding, POSITIVE trend in
both seasons (winter 11.37%/yr, CI [7.75, 15.11], r² 0.973; summer 7.66%/yr, CI
[1.73, 13.94]). SUPER-OFF-PEAK — the charging leg, the other side of the arbitrage spread —
has a large negative point estimate in both seasons (~-21%/yr) but a CI wide enough to
cross zero by a wide margin ([-61.89, 62.02] summer, [-49.24, 20.68] winter): a noisy
estimate, not a resolved one (`summer_off_peak` is separately unresolved: 1 vintage).
Combining a confidently-known on-peak trend with an unresolved super-off-peak point
estimate to build a per-period spread trend would manufacture a specific-looking widening
number that is really just whichever central estimate the noisy leg happened to land on —
the same per-cell-as-clean-input error the earlier retraction above already caught once,
applied to a trend instead of a level. The spread-level structural-break test — which
differences the two legs directly, so the wide super-off-peak uncertainty carries straight
through instead of hiding inside a confident-looking on-peak number — is the honest
version of the same question, and it already can't resolve a trend from the same
underlying data (3 independent post-break price levels in summer, 4 in winter). A
per-period model built from these per-cell trends would not be mathematically equivalent
to `esc`'s existing blended scalar — the two legs' point estimates plainly differ — but it
would inherit at least as much uncertainty as the direct spread test already found
inadequate, since super-off-peak's own wide-crossing-zero CI is the actual bottleneck
either way — the documented-gap branch of #87's own acceptance criteria is the only
evidence-based choice today. A longer bill corpus is needed first (`tou_spread.py`'s own
estimate: reaching into 2028 would roughly double the independent units).
`test_uncertainty_propagation.py`'s
`case_spread_trend_is_still_not_determined_so_esc_stays_a_blended_scalar` guards this: it
reads `tou_spread.json`'s own verdict and `post_break.adequate` fields and fails once the
corpus grows enough to resolve them, which is the signal to revisit #87 for real.

**Showing the downside's consequence, not just documenting its existence (Codex
adversarial review, issue #59, third pass).** Labeling the 0% floor as unproven while the
Monte Carlo still structurally cannot sample a negative escalation draw left a real gap: a
reader could not judge what that excluded downside would actually cost. `escalation_
downside_sensitivity()` closes it WITHOUT fabricating a probability distribution — a plain
what-if grid (0%, -3%, -6%, -9%, -12%) run through the same `payback_of()` at the EXACT
nominal save1/fade/price `tornado()`'s own escalation lever sweeps (an earlier draft used
the raw post-behavior `mid` alone instead of that Beta(2,1)-blended nominal save1, a
self-inconsistency caught in review before this PR shipped — that draft's own +0% point
silently disagreed with the figure it claimed to match), explicitly labeled as carrying no
evidence-backed weight for any point (`not_a_probability_distribution` in the artifact).
This grid's +0% point is now identical, by construction, to `tornado()`'s own escalation
lever's ESC_LO payback endpoint (6.8 yr) — not to `tornado()`'s overall
`nominal_payback_yr` (5.8 yr), which uses 6%, not 0%, escalation, a genuinely different
scenario. The result: payback stays within the 10-yr warranty down to -6%/yr (8.5 yr), but
misses it at -9%/yr (10.2 yr) and worse at -12%/yr (14.0 yr) — a concrete, computed answer
to "how much downside would it take to matter," reported as a labeled sensitivity, the same
pattern `dsgs_vpp_backtest.py`'s own additive sensitivities already established in this
repo, never folded into the Monte Carlo's own percentile claims.

---

### 3.26 `analysis/gross_import_decomposition.py` — gross imports are rising: is it the house or the array? (`data/gross_import_decomposition.json`)

**The question (issue #16).** Two comparable early-summer bill periods (`data/bill_periods_
electric.csv`) show gross imports climbing and net imports climbing FASTER: 5/25/24-6/25/24
(32 days) billed 1,438 kWh gross / 346 kWh net; 5/29/26-6/26/26 (29 days) billed 1,934 kWh
gross / 987 kWh net. Net rising faster than gross is the textbook signature of a production
problem, a consumption problem, or both — this script decomposes the 496 kWh gross-import
change with evidence rather than asserting a cause.

**What this repo actually has, stated before anything is computed (CLAUDE.md sec 0).** No
measured plane-of-array irradiance exists anywhere in this repo — no on-site pyranometer, no
purchased satellite/NSRDB feed. A true weather-normalized Performance Ratio is therefore NOT
DETERMINED (GLOSSARY.md's new "Performance ratio (PR)" entry states this explicitly). The
private Green Button archive staged for this analysis covers only 2025-06-27..2026-07-26
(SDG&E's ~13-month export window) and does not reach back to May/June 2024; the daily
production records (`data/pvoutput_daily.csv`, `data/enphase_daily_production.csv`) and the
whole-home CT archive (SAM 8760, `private/1-raw-data/enphase_sam8760_{2025,2026}.csv` via
`samA.csv`/`samB.csv`) likewise start no earlier than 2025. Every 2024-side figure below is
an ESTIMATE built from what survives from 2024 — the bill's own `net_kwh`/`gross_kwh` (exact)
and 2024's exact calendar-year PVOutput total (`data/pvoutput_yearly_2020-2025.csv`) — never a
fabricated interval series.

**Normalization basis actually used (AC1).** Three things, each labeled for what it is, none
of them a substitute for real irradiance:
1. A deterministic Haurwitz clear-sky model, REUSED read-only from `analysis/soiling_
   analysis.py`'s `clearsky_ghi_kwh_m2()` (never reimplemented) — normalizes for solar
   GEOMETRY (day length, sun angle, calendar effects), not for actual cloud cover. Precomputed
   once over a day-of-year lookup table (`_clearsky_doy_table()`, 366 entries from a real leap
   year) rather than recomputed per calendar year, since the Spencer declination the function
   uses depends only on day-of-year. Annual totals for 2021-2025 vary only ~0.15% year to year
   (leap-day effect only) — proof that geometry alone cannot explain the observed 14.0%
   peak-to-trough swing in PVOutput's `avg_eff_kwh_per_kw_day` (2022 4.839 -> 2023 4.245), so
   that swing must be real weather and/or soiling.
2. Day-matched calendar-window comparison: the 2024 production estimate is transferred via
   this year's OWN empirically measured seasonal fraction (this window's share of a trailing
   year's output), not via clear-sky geometry — the two disagree by ~10% (clear-sky geometry
   predicts a LARGER share than what actually happened), which is itself evidence for using
   the empirical fraction: it is consistent with San Diego's real, well-documented late-May/
   June "June gloom" marine layer, which the clear-sky model cannot see. The geometric fraction
   is still reported (`seasonal_fraction_clearsky_geometric`) for transparency, not blended in.
3. An empirically FITTED ambient-temperature sensitivity, not a manufacturer datasheet
   coefficient asserted as fact: `temperature_sensitivity_block()` reuses `soiling_analysis.
   py`'s own regression machinery (`ols()`, `t_pvalue()`, `days_since_rain()`, `build_table()`,
   `flag_clear_days()`, all imported read-only) and adds Open-Meteo daily mean temperature
   (`data/weather_daily_tmean.csv`) as one more regressor alongside its existing seasonal
   harmonics and days-since-rain terms, on the same 2025-07-24..2026-07-23 clear-day sample
   (176-177 days depending on which household latitude flows through the clear-day filter).
   Result: -0.117%/degF (-0.211%/degC), t=-2.02, p=0.045, n=176 — correctly signed (hotter
   ambient days show lower clear-sky-normalized output) and smaller in magnitude than the
   commonly cited manufacturer cell-temperature coefficient (~-0.35%/degC above 25degC STC)
   because ambient daily-MEAN temperature damps the true midday cell-temperature swing; the
   two numbers measure different physical quantities and are not directly comparable. This
   regression cannot be extended before 2025-07-24 (no daily temperature record exists earlier
   in this repo), so it is a magnitude check for the one year available, not a correction
   applied to the 6-year trend.

**Degradation isolated from soiling and weather (AC2, AC6) — reconciled against index.html's
existing claim, not just asserted to agree.** `degradation_block()` refits the 2021-2025
`avg_eff_kwh_per_kw_day` series (excluding 2020's partial 357-day year, matching index.html's
own "full years 2021-2025" framing) three ways: OLS (-1.765%/yr), CAGR first-to-last
(-1.332%/yr), and Theil-Sen median-of-pairwise-slopes (-1.484%/yr, a robustness check against
the 2023 outlier dominating a 5-point OLS). All three independently reproduce index.html's
already-published "naive fit reads ~1.3-1.7%/yr" claim from a committed, reproducible
artifact rather than hand arithmetic — CONFIRMED, not merely asserted. The tighter "best-
estimate ~0.5-1.0%/yr" verdict in that same paragraph is a different matter: it is a
qualitative downward adjustment (reasoning: the naive metric isn't weather-normalized, so true
degradation is probably lower), and no committed artifact in this repo independently derives
that narrower range. `soiling_results.json`'s validated 2024-08-12 cleaning event
(`sanity_check_2024_cleaning`, reused read-only, never recomputed) shows an 11.8% production
gain after ~134 dry days (~4.4 months) with no rain — a SINGLE validated event whose swing
exceeds the ENTIRE naive 4-year change (-5.22%, 2021 to 2025) by more than double.

**Codex review pass 3, finding 2 — a claimed "bracket" overstated what this evidence
establishes.** An earlier draft used the cleaning event to argue a bound: true degradation
sits between ~0%/yr (if the observed swing is entirely soiling/weather timing) and the naive
rate (if none of it is). Codex correctly identified that this reasoning only rules out ONE
direction of confounding — it assumes soiling/weather can only make the naive trend look
WORSE than true degradation, when it could just as easily MASK a true degradation rate WORSE
than the naive trend shows, if later years in the 2021-2025 span happened to be sunnier or
had less soiling accumulation. A single confounding event large enough to explain the ENTIRE
naive 4-year change on its own is evidence that soiling/weather can move the annual total by
more than the naive trend itself, in EITHER direction — not evidence that the true rate is
bounded to only push one way. With no daily weather or production record before 2025-07-24 to
separate soiling/weather timing from true panel aging across 2021-2025, the honest statement
is that true degradation is NOT DETERMINED from data in this repo, not merely uncertain within
a stated bracket. index.html's degradation subsection (sec9) now cites this script's
naive-range confirmation and this honest non-determination, rather than a bound the evidence
doesn't actually support.

**Gross household load, not net import (AC3).** The Green Button `Consumption`/`Generation`
columns are net IMPORT/EXPORT, never gross load (self-consumed solar never crosses that
meter — confirmed by issue #15's work and reconfirmed here: summing `Consumption` over the
2026 window gives 1,949.9 kWh, 0.82% off the bill's 1,934 kWh gross-import figure, and
`Generation` sums to 963.1 kWh, 1.7% off the bill-derived 947 kWh export — both are import/
export cross-checks, not load). Gross load for 2026 is MEASURED directly from the whole-home
CT archive (SAM 8760, reusing `deep_analyses.py`'s own `samA`/`samB` concatenation pattern
exactly — same index construction, same source files, never reinvented): 2,643.3 kWh for the
comparison window. An independent identity cross-check (`Net_kwh` (bill, exact) + measured
PVOutput production for the window = 2,681.4 kWh) agrees within 1.44% — cross-meter noise
consistent with this repo's already-documented ~2% PVOutput-vs-Enphase-meter gap
(`data/threeway_production_validation.csv`, `uncertainty_propagation.py`'s own calibrated
`prod_sigma`). No CT/SAM-8760 archive exists before 2025 (`enphase_sam8760_2025.csv` is the
earliest), so the 2024 side is NOT MEASURED — it is ESTIMATED as `Net_kwh_2024` (346.0, exact)
plus the estimated 2024 production, and the artifact says so explicitly rather than implying
a measurement it doesn't have.

**The decomposition (AC4) — a physically grounded counterfactual, not a total-only identity.**
An early draft of this analysis tried to decompose gross import using only period-TOTAL
consumption and production (`Consumption = Net_kwh + Production`), which is an exact identity
for NET import but NOT for GROSS import: gross import depends on the INTERVAL-LEVEL overlap
between load and production, not on period totals alone (a physical fact, not a coding bug —
`Gross_import = Consumption - Production + Export`, and `Export` is itself a THIRD
bill-exact quantity, `gross_kwh - net_kwh`, that a two-total identity silently drops).
Verified concretely: that naive approach decomposes the OBSERVED NET change (641 kWh) rather
than the OBSERVED GROSS change (496 kWh) issue #16 actually asks about — a 145 kWh, ~29% miss,
far outside AC4's 5% tolerance. `decomposition_block()` instead builds an hourly-resolution
counterfactual: 2026's actual hourly CT load and an hourly-derived production series
(`CT_load - GreenButton_import + GreenButton_export`, reconstructing production entirely from
meter data at hourly resolution — the same SAM-8760-based gross-production reconstruction
`uncertainty_propagation.py`'s own `reconstruct_gross_production()` implements (issue #60),
built independently here for a different purpose before that issue was resolved)
feed `sum(max(0, load - production))`.

**Four bugs, found by three separate review passes, on the SAME calculation.** This
decomposition went through more review-driven correction than any other artifact this
generator produces, worth tracing in full because each fix changed the METHOD, not just a
number.

1. *Adversarial pass 1, finding 1 — day-count mismatch in the production estimate.* An
   earlier draft scaled the 2026 hourly series to 2024-ESTIMATED aggregate levels using scale
   factors derived from two INDEPENDENTLY estimated numbers (a weather/seasonal-rate
   production estimate and a bill-identity consumption estimate) and happened to land within
   0.86% of the observed 496 kWh change. Codex's adversarial review caught that this
   "agreement" was not a real validation: the 29-day 2026 window's production estimate was
   being transferred directly onto the 2024 bill's 32-day total, a silent day-count
   mismatch. Correcting it (a per-day rate ratio, transferred using the SAME 29 measured
   days rather than a different, differently-dated window — sampling different specific
   calendar days turned out to introduce MORE noise than the day-count fix removed, verified
   empirically before settling on the rate-ratio approach) pushed the two independent
   estimates far enough apart that the decomposition's error jumped to 23.8% — the original
   close agreement had been accidental, propped up by the bug's own error cancelling other
   noise, not evidence the method was sound.
2. *The resulting redesign — stop relying on two independent estimates to coincidentally
   agree with a bill-exact fact.* Once production_scale is chosen, consumption_scale is not
   a second free estimate at all — it is PINNED by an exact accounting identity: gross
   consumption = bill-exact net_kwh (a fact, not an estimate) + estimated production.
   Production_scale is therefore the ONE genuinely free parameter, back-solved (via
   `scipy.optimize.brentq`) to the value that makes a simulated corner match its bill total
   precisely, rather than hoping an independently-derived estimate happens to land there. A
   small, separately-verified `hourly_resolution_correction` factor corrects for the SAM 8760
   archive's native hourly resolution understating true gross import relative to the bill's
   15-minute-resolved figure (confirmed at a real, zero-estimation corner) — a shared,
   disclosed bias removed before decomposing, not a per-corner fudge.
3. *Adversarial pass 2, finding 1 — the "0% error" this redesign produced was tautological,
   not a validation.* With no 2024 hourly shape available, the accounting identity pins
   production_scale UNIQUELY only under a CHOSEN shape assumption (2024 shares 2026's diurnal
   shape, scaled uniformly) — a different, equally defensible assumption could back-solve to
   a materially different split while satisfying the identical bill totals.
   `identifiability_robustness_check()` tests this directly: it re-solves under ONE
   materially different, still data-grounded shape assumption (consumption decline
   concentrated in EV-charging hours, per `detect_sessions()`, rather than spread uniformly)
   and compares the resulting split.
4. *Adversarial pass 3, finding 1 — comparing incomparable units in that comparison.* An
   earlier draft compared the EV-concentrated scenario's raw production-ENERGY delta against
   the default scenario's Shapley gross-import CONTRIBUTION — different physical quantities,
   since most of a production change is absorbed by export/self-consumption rather than
   changing gross import 1:1. Fixed by generalizing `_shapley_two_factor` into
   `_shapley_two_factor_vectors` (accepts full alternative hourly vectors, not just scalar
   scale factors) so both scenarios go through the identical decomposition.
5. *Codex review pass 1, finding 1 — the back-solve's own identity used the wrong production
   total.* The accounting identity is only internally consistent if the production figure it
   uses is the SAME series the hourly simulation actually scales (the meter-derived
   `CT_load - GreenButton_import + GreenButton_export` series) — an earlier draft used the
   SEPARATELY-measured PVOutput total instead, which differs by ~1.7% (a real,
   already-documented cross-meter gap). Mixing the two meant the "exact" identity matched
   gross import while implying a net import different from the bill-exact fact it was
   supposed to be pinned to. Fixed by deriving the identity from the meter-derived series'
   own sum; the PVOutput figure remains the separate, clearly-labeled weather/seasonal-rate
   cross-check only. The identical bug existed in `identifiability_robustness_check()`'s own
   accounting and was fixed the same way.
6. *Codex review pass 2, finding 1 — the compression artifact was never actually removed, only
   moved.* Fix 1 above corrected the WEATHER-BASED production ESTIMATE's day-count, but the
   DECOMPOSITION's own hourly simulation still scaled the real 29-day 2026 vector to
   represent a 32-day 2024 total — compressing 3 missing days' worth of energy into 29 real
   hours, distorting the nonlinear `max(0, load - production)` overlap the whole method
   depends on. No amount of scaling a 29-hour-count vector can correctly represent a 32-day
   period; the vector is simply the wrong length. Fixed with a genuinely different
   construction: `template_hourly_vectors()` builds a REAL 32-day 2026 window
   (`TEMPLATE_START`..`TEMPLATE_END`, May 25-June 25 — matching `PERIOD_2024`'s own day count
   and calendar span exactly, and falling entirely inside the archive's coverage), and
   `_solve_year_scales()` now back-solves BOTH years symmetrically against this ONE shared,
   correctly-sized template — 2026 is no longer special-cased as "the exact corner, scale
   fixed at 1"; it gets its own back-solved production_scale relative to the template, just
   like 2024 does. Every vector in every corner of the Shapley decomposition is now the SAME
   768-hour (32-day) length, so no vector is ever asked to represent a day count it wasn't
   measured at. `identifiability_robustness_check()`'s own EV-concentrated scenario was
   rebuilt the same way (`_solve_year_scales_ev_shape()`, symmetric, template-based).
7. *Codex review pass 2, finding 2 — a sign error in the degradation bracket.* `degradation_
   block()`'s reconciliation bracket used `max(ols_pct_per_yr, cagr_pct_per_yr)` on two
   NEGATIVE decline rates, silently reporting a "bracket" that ran from 0%/yr to a MORE
   negative number and mathematically excluded the positive 0.5-1.0%/yr figure it claimed to
   bound. Fixed by taking `max(abs(...), abs(...))` — the intended magnitude.

**The answer, on genuinely comparable, dimensionally consistent grounds.** Under the DEFAULT
scenario (both years share the 32-day template's diurnal shape, each scaled uniformly to its
own bill), `consumption_term_kwh` = +491.0 kWh; `production_term_kwh` = +5.0 kWh — both
endpoints matching their bills exactly by construction, decomposed sum equal to the observed
496 kWh change to the same precision (0.0% error), and the AC4 load-bearing test in
`test_gross_import_decomposition.py` asserts this precisely rather than merely under a
tolerance.

**Codex review pass 3, finding 1 — the back-solved production RATIO is not a valid rate
comparison, even though the DECOMPOSITION it feeds is fine.** An earlier draft reported
`production_scale_2024_over_2026` (1.024) as "2024 produced ~2.4% more than 2026" and compared
it numerically against the independent weather/seasonal-rate estimate
(`production_scale_estimated_from_weather` = 1.088, a 5.9% gap, framed as rough agreement).
Codex correctly identified that this comparison conflates a real day-count-fitting artifact
with genuine signal: `production_scale_2026` is fit so a NONLINEAR gross-import equation
matches 2026's 29-day bill using the 32-day template's SHAPE, while `production_scale_2024` is
fit against 2024's own 32-day bill using the SAME template — since 2024's target already
matches the template's length exactly and 2026's doesn't, fitting a 32-day-shaped simulation
to a 29-day target mechanically pulls `production_scale_2026` toward roughly 29/32 ≈ 0.906
independent of any genuine production change (confirmed: `production_scale_2026_vs_template`
= 0.917, suspiciously close to that ratio). Applying Codex's own suggested day-count
correction (`1.024 × 29/32 ≈ 0.928`) does not merely shrink the disagreement — it FLIPS the
direction relative to the weather-based estimate (0.928 vs 1.088), and since that correction
is itself only an approximation for a nonlinear fit, presenting its specific value as
authoritative would trade one questionable precision claim for another. The fix taken:
`production_scale_2024_over_2026`/`production_scale_backsolved_from_bill` remain in the
artifact for full transparency and reproducibility, but are now explicitly labeled as NOT a
day-count-clean rate comparison (`production_scale_backsolved_not_a_clean_rate_comparison`),
and the numeric "cross-check" against the weather-based estimate has been removed from both
the artifact's interpretive framing and the report prose. **Critically, the Shapley
DECOMPOSITION itself — `consumption_term_kwh`, `production_term_kwh`, and the observed-change
match — is unaffected by this finding**: it is computed directly from the four well-defined
corners, each exact by construction against its own bill, and never depended on the scale
ratio being independently interpretable as a rate.

Under the EV-concentrated alternative shape, the split is +469.7 kWh consumption against
+26.3 kWh production — its own terms summing to the observed 496 kWh change exactly, just like
the default scenario's (a property `test_gross_import_decomposition.py`'s own test asserts
explicitly, per Codex's own recommendation, rather than only asserting the default scenario's
sum). With both scenarios computed in the same units and against the same correctly-sized
template, the finding is reassuring: +5.0 kWh (uniform scaling) and +26.3 kWh (EV-concentrated)
are both small relative to the ~470-490 kWh consumption term either scenario reports, and the
"consumption story, not a production story" conclusion is genuinely robust to this
identifiability concern, not merely an artifact of one modeling choice.
`case_identifiability_robustness_check_reports_an_alternative_shape_honestly` pins this exact
agreement (`conclusion_robust_to_this_alternative_shape` = `True`) so a future regeneration
that silently reintroduces either the units-mismatch bug or the compression artifact — both of
which produced dramatic, attention-grabbing "divergences" rather than this quieter, correct
agreement — would be caught rather than mistaken for a more interesting finding.

This does not contradict the 6-year degradation trend above — both scenarios' production terms
sit comfortably inside the ~45-60 kWh expected from the naive 1.3-1.8%/yr degradation rate
applied to this window, so a small, real per-year decline remains entirely consistent with
what this short, two-years-apart, no-2024-daily-data comparison can resolve.

**EV attribution (AC5) — real detection, honestly bounded, not assumed.** `ev_block()` runs
`analysis/behavior_rebuild.py`'s `detect_sessions()` (imported read-only, never modified) on
the 2026 comparison window's own 15-minute Green Button slice: 50 sessions, 1,253.0 kWh, 47.4%
of that window's CT-measured gross consumption. Both vehicles (Tesla Model 3 since Aug 2021,
Model Y since Jun 2022) were already owned during the 2024 comparison period, so EV charging
existed in BOTH windows — the private Green Button archive staged for this analysis does not
reach back to May/June 2024, so `detect_sessions()` cannot run on the earlier period at all.
Because 2026's measured EV energy alone (1,253 kWh) exceeds the entire observed consumption
increase, the true EV share of the INCREASE (as opposed to of 2026's own total consumption)
cannot be bounded usefully tighter than 0-100% from data in this repo — the artifact records
`true_share_of_increase: "NOT DETERMINED"` rather than publishing a guess, and names what
would settle it (a Green Button export or Tesla Charge Stats history covering May-June 2024).

**Registration and reproduction.** Registered in `test_scripts_runnable.py`'s `MANIFEST`
(generator, `OWNS` = `[("cwd", "gross_import_decomposition.json")]`, matching `behavior_
rebuild.py`'s cwd-artifact convention) and `NEEDS_PRIVATE_ARCHIVE` (both SAM 8760 exports plus
the raw Green Button export have no synthetic stand-in, same shape as `service_headroom.py`)
rather than `CI_RUNNABLE` — its hourly reconstruction and decomposition need a real two-EV
household's real two-year-apart billing periods, which no invented fixture carries. Also added
to `analysis/check_coverage.sh`'s suite and generator lists so its lines count toward the
package coverage floor. **Run** from `private/verify` per the standard sandbox (needs
`usage.csv`/`samA.csv`/`samB.csv` beside it, plus the committed `data/` files and `private/
household.yaml`; writes `gross_import_decomposition.json` to the CWD, promoted to `data/` by
hand). Tests: `analysis/test_gross_import_decomposition.py`, following `test_uncertainty_
propagation.py`'s convention (`household.PATH` stubbed with a synthetic YAML before import;
archive-dependent cases gate on the real archive's presence and SKIP rather than fail when
absent — this checkout has the archive staged, so those cases run for real). The load-bearing
tests are `case_decomposition_sums_within_5_pct_of_observed_change` (AC4) and `case_
degradation_reproduces_the_soiling_modules_own_validation_target` (AC2, pinned to `soiling_
results.json`'s own committed `known_cleaning_gain_pct` so a future regeneration cannot
silently drift out of sync with this script's documented reconciliation).

---

### 3.27 `analysis/reprice_by_vintage.py` — reprice the billed year at its own tariff vintages (`data/reprice_by_vintage.json`)

**The question (issue #30).** `billing_model_nem.py`'s native 365-day rolling window
(2025-07-24 minus 365 days .. 2026-07-23) prices the whole year at CURRENT 6/1/2026 rates and
prints **$4,904/yr**. The 13 real billing periods (`data/bill_periods_electric.csv`, cross-checked
against `data/electric_bill_summary.csv`) span a DIFFERENT 365-day window, 2025-06-27..2026-06-26,
and accrued **$3,282.22** of real `current_charges`. §6.3 used to attribute the whole ~$1,622 gap
to "rate vintage" — the bills were mostly rendered on cheaper 2025 tariffs. Issue #3 measured that
claim directly on two matched periods and found it doesn't hold: delivery price LEVEL barely moved
while its SHAPE rotated hard. This script measures the vintage effect over the *whole* billed year
using `analysis/rates_history.py` (the UDC delivery tariff actually in force on each historical
date, sourced from the bill PDFs' own printed rate lines), and separates it from a second,
previously unexamined confound: the $4,904 and $3,282.22 figures were never computed over the same
365-day window in the first place.

**The decomposition.** Eight terms, each "the previous total plus one correction," landing exactly
on the actual bills — a pure algebraic identity by construction, asserted to the cent. (Three
earlier versions of this decomposition had bugs adversarial review caught. A 4-term version
conflated `window_effect` with a large, mislabeled generation/fixed-charge vintage effect, because
its two sides did not share a methodology for generation and the fixed charge — fixed by inserting
`bill_window_all_current_vintage_modeled_total`, the bill-aligned window priced with
`billing_model_nem.bill()`'s own unmodified methodology, as a bridge value. A 5-term version then
counted nearly all of that vintage-looking gap as real "generation vintage" — but a Codex
adversarial review flagged this as unsupported, and it was: `analysis/cca_rate_extraction.py`'s own
committed `data/cca_generation_rates.csv` proves CEA's charged per-TOU generation rate never moved
once across the whole CCA era and is IDENTICAL to `rates.CEA`'s current table, re-verified here
against these specific 13 periods rather than assumed from that module's docstring — the true cause
was almost entirely the SAME TOU-window-shape confound this script already named as a
`residual_total` candidate, now quantified for generation dollars specifically. A 6-term version
then still silently carried two small, real, unmodeled generation-side line items — CEA's "Clean
Impact Plus" (CIP) per-kWh product adder and a per-period state surcharge tax — inside
`generation_tou_window_effect`, mislabeling real money with no `rates.py` counterpart at all as part
of the TOU-window-shape effect; a second Codex review pass caught this too. Both are now separated
into their own terms. This is the corrected, 8-term version.)

```
$4,904.13   native_window_total              billing_model_nem's own window, current vintage,
                                             modeled
  + $-54.12    window_effect                   bill-aligned window vs. native window, BOTH SIDES
                                             modeled the same way
  + $-273.12   generation_tou_window_effect    NOT a vintage effect (CEA's rate is proven flat and
                                             equal to current) -- the TOU-window-shape confound,
                                             quantified for generation dollars (see below)
  + $+13.09    cip_adder_usd                   real, unmodeled CEA product adder -- no rates.py
                                             counterpart, so neither vintage nor window-shape
  + $+3.94     state_surcharge_tax_usd         real, unmodeled per-period state tax -- same
                                             reasoning as the CIP adder
  + $-25.50    fixed_charge_vintage_effect     genuinely a vintage/regime effect: flat Monthly
                                             Service Fee vs. today's per-day Base Services Charge
  + $-47.39    delivery_vintage_effect         UDC delivery at its own historical vintage vs.
                                             current (SOURCED portion only -- see caveat below)
  + $-1,238.80 residual_total                  whatever is left -- the real, previously
                                             undecomposed gap
  = $3,282.22  actual_total_sum                the bills' own accrued current_charges

  total_vintage_effect (delivery + fixed-charge; generation/CIP/surcharge EXCLUDED) = $-72.89
```

**Generation rate vintage is zero, by evidence, not assumption.**
`_verify_cca_generation_rate_flat()` reads `data/cca_generation_rates.csv`, filters to
`authority == "charged_tariff"` rows for the three real TOU cells on exactly these 13 periods, and
confirms every (season, TOU) cell's charged rate is both flat across every period that bills it and
identical to `rates.CEA`'s current value to 5 decimal places — true for all six cells, zero
exceptions, all 13 periods covered. `build()` runs this check and fails closed if a future
regeneration of the corpus ever shows the rate moving. There is therefore no "generation vintage"
term in this decomposition — CEA charged exactly today's rate throughout the analysis year.

**The sourced-vs-total caveat (a Codex review finding: report-wording precision, not a code bug —
these numbers were always correct).** `total_vintage_effect` is **-$72.89, about 4.5% of the
$1,621.91 gap** — but this is the SOURCED, MEASURABLE portion of rate vintage, not a ceiling on the
true total. `delivery_vintage_effect` is a clean comparison only over the kWh `rates_history.py` can
actually source a historical delivery rate for; **1,168.3 kWh across 247 days** (the off-peak
delivery bucket this household net-exported through for most of the analysis year) has no sourced
historical rate at all, and that slice's own vintage effect — genuinely unknown, not zero by
evidence the way generation's is — is folded into `residual_total` instead, indistinguishable there
from a genuine model-vs-bill mechanics gap (already the second candidate below, both before and
after this wording fix). If that unpriced slice's real historical rate differed materially from
today's, the true total vintage effect could be somewhat larger than 4.5%. `delivery_vintage_effect`
alone is **-$47.39 (about 2.9%)**, still confirming issue #3's finding (delivery price level barely
moved) at the scale of the whole year, for the portion it can measure. `fixed_charge_vintage_effect`
is **-$25.50 (about 1.6%)**, a genuine, undisputed structural change (Monthly Service Fee → Base
Services Charge, October 2025, issue #7) with no sourcing gap at all (it's a real, fully known
dollar figure on both sides).

**`generation_tou_window_effect` (-$273.12, about 16.8% of the gap) is the term a fresh Codex
review corrected TWICE.** It differences `bill_window_current_vintage_total` (delivery + PCIA
computed by `_delivery_and_pcia_kwh()`, called separately PER BILL PERIOD and summed) against
`bill_window_all_current_vintage_modeled_total` (`billing_model_nem.bill()` called ONCE on the whole
bill-aligned window as a continuous frame), MINUS `fixed_charge_vintage_effect`, `cip_adder_usd`, and
`state_surcharge_tax_usd`. It splits into two named, quantified pieces:
- `generation_clean_tou_effect` (**-$290.53**): real billed generation dollars, with the CIP adder
  and the state surcharge tax subtracted out FIRST (see below), minus a continuous-window,
  current-vintage CEA model of the remainder. `billing_model_nem.load()` assigns every historical
  interval's TOU period via `rates.period_at()`'s CURRENT window shape, applied uniformly to every
  historical date; the real statements billed generation against kWh bucketed by whichever window
  shape was actually in force at the time. Since CEA's on/off/sop rates differ by roughly
  $0.11-0.47/kWh, reclassified kWh produces a real dollar gap. `_verify_and_compute_generation_side_
  fees()` independently confirms, for every period, that the three real TOU-cell dollars plus CIP
  plus the surcharge tax reconstruct the real `cca_generation` figure to the cent — nothing else is
  hiding in this figure beyond the TOU-window-shape confound and ordinary rounding.
- `delivery_pcia_restart_artifact_usd` (**+$17.41**, split $15.96 delivery / $1.45 PCIA): a
  per-bill-period-restart artifact. Bill periods don't align with calendar-month boundaries, so a
  calendar month that nets positive OVERALL can split at a bill-period boundary into a
  negative-looking fragment that `_delivery_and_pcia_kwh()`'s own zero-clamp drops — verified
  directly, that function run on the bill-aligned window as ONE continuous frame finds ZERO
  net-negative buckets, so `bmn.bill()` never actually credits any of them; every occurrence this
  script finds is an artifact of its own per-bill-period restart. An earlier diagnostic
  (`negative_bucket_mechanics_gap_usd`, retired) tried to compute this per negative bucket using a
  UDC+CEA "credit-rate" placeholder and overstated it by about $6.66 by folding in part of
  generation's own effect; this figure is instead the direct, actual difference between the
  per-period-summed and continuous-window delivery/PCIA totals. `delivery_vintage_effect` is
  unaffected: both its sides come from the SAME per-bill-period-restarted calls, so this artifact
  cancels out of their difference.

**`cip_adder_usd` (+$13.09) and `state_surcharge_tax_usd` (+$3.94) are real, fully known, and not a
vintage or window-shape effect at all.** Both are directly billed line items in
`data/cca_generation_rates.csv` (the `clean_impact_plus` and `state_surcharge_tax` rows) with NO
counterpart anywhere in `rates.py`'s current-vintage table — there is nothing to compare either
against for a vintage claim, and neither depends on how interval kWh gets bucketed into TOU periods,
so there is no window-shape claim either. They are simply real money `billing_model_nem.bill()`'s
model structurally never counts, in every year, regardless of vintage or window.
`_verify_and_compute_generation_side_fees()` verifies CIP's own rate is flat at $0.001/kWh across
all 13 periods (the evidence for calling its own vintage effect essentially zero, the same rigor
applied to core CEA generation) — the state surcharge tax has no rate at all (a flat per-period
dollar fee), so no flatness question even applies to it.

**The residual is the dominant term, and it is NOT determined by this script — with one strong,
unproven lead.** `residual_total` is **-$1,238.80, about 76.4% of the gap** — unchanged by the
generation restructuring above (it never depended on how the generation/fixed-charge gap gets
further decomposed). Three explicitly named, non-exclusive candidates, none ruled in or out:
1. **PCIA / NBC vintage** — `rates_history.py` cannot source either historically (no committed
   artifact carries them); both are held at the current `rates.py` vintage in every total this
   script computes, so they cancel out of every vintage term and cannot explain any of them. Unlike
   generation, there is no independent evidence either stayed flat. Any real historical difference
   lands in `residual_total` instead, indistinguishable there from a genuine mechanics gap. Out of
   scope to resolve further here (issue #27).
2. **Unsourceable off-peak delivery** — `delivery/summer/off_peak` and `delivery/winter/off_peak`
   are flagged "absent" in `data/rate_vintages.csv` across most of the analysis year: this household
   net-EXPORTED during those specific hours (weekday 6-10am/2-4pm, weekend 2-4pm) on nearly every
   statement, in both seasons, until a recent shift, so no bill ever printed a positive off-peak
   delivery kWh line to source a historical rate from. Calling `rates_history.bill_nem_monthly()`
   directly on a whole period (the design's original approach) raises the instant it meets one such
   day, discarding an entire period's worth of otherwise-good evidence — sometimes over a 3-4 day
   gap at a period's edge. The script instead prices day-by-day via `RateSet.cells()` (a
   non-raising per-cell lookup), substituting the current-vintage rate only for the specific
   unsourceable (day, TOU-bucket) slice — 1,168.3 kWh across 247 of the 365 days — so that slice
   contributes exactly zero to `delivery_vintage_effect` (it is priced identically on both sides) and
   any real historical difference for it lands in `residual_total` instead. **This is a deviation
   from issue #30's original design** (which called for a single whole-period
   `bill_nem_monthly(..., delivery_only=True)` call): run against the real corpus, that call fails
   on 11 of the 13 periods. See the script's own docstring and `_delivery_and_pcia_kwh()` for the
   full reasoning.
3. **A documented, pre-existing TOU-window vintage limitation, inherited unmodified from
   `billing_model_nem.py`.** `rates.py`'s own docstring records that the weekday 10am-2pm
   super-off-peak window took effect 2026-03-01 — before that, those hours were off-peak — and
   warns that applying the current window to earlier dates "misallocates 250-360 kWh per period
   between off-peak and super-off-peak." `billing_model_nem.load()` (reused here read-only, per
   CLAUDE.md) assigns every historical interval's TOU period via the CURRENT window rule
   uniformly, so this limitation is baked into both `native_window_total` and every per-period
   figure this script computes (including `generation_tou_window_effect`'s own construction above,
   which is exactly this same confound quantified for generation specifically). The data shows a
   striking, OBSERVED-NOT-CONFIRMED pattern: 98.9% of `residual_total` (-$1,225.32 of -$1,238.80)
   falls in the 9 periods ending before 2026-03-01, and the 4 periods ending on or after it net to
   only -$13.48 combined (`notes.residual_concentration` in the artifact). The alignment with the
   documented window change date is circumstantial, not a proven causal decomposition — rebuilding
   TOU assignment from the historical window shapes is out of scope for this script.

The kWh reconciliation (interval-derived gross/net kWh per period vs. the bill's own gross_kwh/
net_kwh) agrees to within **0.93%** in the worst period (120.9 kWh total absolute gross diff across
all 13 periods) — small enough that metering or window-slicing is NOT a material contributor to
the residual, which favors the three candidates above over a data-quality explanation.

**Registration and reproduction.** Registered in `test_scripts_runnable.py`'s `MANIFEST`
(generator, `OWNS` = `[("data", "reprice_by_vintage.json")]` — writes directly into `ROOT/data`
via its own `_repo_root()` walk-up, matching `rates_history.py`'s convention rather than the
cwd-then-promote pattern some other generators use) and `NEEDS_PRIVATE_ARCHIVE` (needs `usage.csv`
via `billing_model_nem.load()` for the interval reconciliation). Also added to
`analysis/check_coverage.sh`'s suite and generator lists. **Run** from `private/verify` per the
standard sandbox; writes `data/reprice_by_vintage.json` directly (no manual promotion step).
Tests: `analysis/test_reprice_by_vintage.py` (28 cases) — fabricated-CSV cases for every Step 1
fail-closed assertion (CCA-provider boundary, period-set cross-check, current_charges arithmetic,
365-day sum, period contiguity), a fabricated-frame case for the date-level interval-coverage gap
plus a separate pair of cases for `_check_slot_coverage()`'s stricter 15-minute SLOT-level check
(one fabricating a truncated day that passes the date-only `_check_coverage()` but is correctly
rejected by the slot-level check, one confirming full slot coverage passes), a fabricated-frame
unit test pinning `_delivery_and_pcia_kwh()`'s calendar-month netting restart exactly, a case
confirming `_continuous_current_vintage_components()`'s five components sum exactly to
`bmn.bill()`'s own total, a case confirming the restart artifact matches a direct, independent
per-period-vs-continuous difference (not a per-negative-bucket formula), four cases for
`_verify_cca_generation_rate_flat()`'s fail-closed checks (including one that runs against the REAL
committed `data/cca_generation_rates.csv`, no private archive needed), five cases for
`_verify_and_compute_generation_side_fees()` (real-corpus totals for `cip_adder_usd` and
`state_surcharge_tax_usd` pinned to the cent, a non-flat CIP rate rejected, a missing CIP row
rejected, a missing surcharge row rejected, a per-period reconciliation mismatch rejected), a
pure-arithmetic case exercising `_aggregate()`'s own 8-term telescoping identity on two unrelated
fabricated scenarios (one zero-fee, one with nonzero CIP/surcharge values), two cases for
`_aggregate()`'s internal consistency cross-checks (NBC cancellation, the generation/fixed-charge
reconstruction), and end-to-end cases against the real staged archive asserting the identity holds,
the artifact write round-trips, both new fee fields land in a plausible range, and both
`window_effect < total_vintage_effect` and `total_vintage_effect < generation_tou_window_effect`
(regression guards against the two inverted relationships adversarial review found across earlier
versions of this decomposition).

---

### 3.28 `analysis/quiet_night_floor.py` — pricing the always-on load (`data/quiet_night_floor.json`)

**Purpose (issue #17).** §3.11's `phantom` key reports a ~1 kW overnight import floor and
DE-PRIORITIZES it: the owner identifies the cause as home-lab compute (report prose,
index.html §13 — not independently recorded in any structured data file or in this
document), so the floor is never costed. This script re-measures the floor directly from
interval data (not by reading `phantom`'s hand-recorded numbers), prices it two independent
ways, and reconciles them.

**Measurement (issue AC1).** `night_floor_series()` computes a NEW, independently-designed
per-night rule — for each calendar night, the median 1-5am import power, EXCLUDING any
night whose max 1-5am power reaches `HIGH_DEMAND_GATE_KW` (2 kW) entirely (not zeroed, not
averaged in). This is deliberately NOT the existing per-interval rule in §3.5 item 2
(`deep_analyses.py`: 3-5am window, `Consumption <= 0.5` kWh per interval, 25th percentile) —
a per-interval filter can admit a night with a brief high-demand spike as "quiet" so long as
its other intervals stay low, letting spillover contaminate the aggregate; a per-night gate
excludes the whole night once any interval in it crosses the threshold, the more appropriate
shape for isolating an ALL-NIGHT continuous floor. The field is named `excluded_high_demand`,
not `ev_night`: a dryer, a heat-pump defrost cycle, or a well pump trips the identical gate,
and on this household's real data the gate excludes ~88% of nights (322 of 365) — reported
explicitly as `night_floor.selection_caveat` rather than left implicit. The kept measurement
(43 quiet nights, median 1.03 kW) agrees closely with `phantom`'s own hand-computed figures
(44 nights, 1.025 kW), even though this script is the first committed generator for either
rule. `hour_of_day_profile()` is a SEPARATE, independently-instrumented 24-hour distribution
(p10/median/p90 of Enphase SAM 8760 whole-home consumption by hour) — the only signal that
can see the floor persisting through daylight, where an import-only measurement reads near
zero because solar is covering it.

**Physical floor-removal model (`_split_floor`).** A constant `floor_kw` is subtracted from
every 15-minute interval; energy that cannot reduce measured import (because solar was
already covering it) flows to increased export ONLY in an interval that actually has metered
generation there. Where `Consumption < floor` and `Generation == 0` in the SAME interval,
crediting the shortfall as freed export would invent energy that was never metered, so it is
DROPPED (clamped to zero), not credited — `floor_assumption_violations()` quantifies the
residual (on the real measured year: 4,221 intervals, 219.5 kWh, 2,234 of them before 6am or
after 7pm where solar is physically impossible, ~$85.52/yr dropped at the export rate),
making every pricing figure below conservative by roughly that amount rather than inflated by
it. (Correction: an earlier version of this module described the split as an unconditional
identity; an independent review, PR #77, found this false and the fix above followed.)

**Two pricing methods, reconciled (issue AC2/AC3).** Both price the identical `_split_floor`
allocation: (a) `price_method_a` multiplies each interval's `reduce_from_import`/`leftover`
by `rates.allin()`/`rates.credit()` for that interval's own season/period, with no monthly
aggregation; (b) `price_method_b` re-bills the counterfactual year with `rates.bill_nem`
(the canonical monthly-netting engine) and differences three billed series (baseline, import
reduced only, import reduced AND export increased) to isolate the avoided-import and
displaced-export channels exactly as the engine would price them. Both report
`avoided_import_usd` and `displaced_export_usd` separately, never pre-summed (CLAUDE.md
§1b) — on the real measured year, ~73% of the floor's cost is avoided grid import and ~27%
is displaced solar export credit, priced at very different marginal rates.

**Reconciliation (`gap_decomposition`).** The two methods disagree by a small, fully
explained amount (-1.23% on the real measured year). The PRIMARY mechanism is PCIA
(`rates.PCIA`, $0.02828/kWh) being priced differently inside a (year-month, season, period)
bucket whose net sign does NOT change: monthly netting values an extra exported kWh sitting
inside an already net-positive bucket at the full retail energy rate (credit + PCIA, since it
is really just offsetting more of the same net-positive import), while method (a)'s flat
price_map always prices a "leftover" kWh at the plain export credit rate — undervaluing it by
PCIA/kWh. The symmetric case (an avoided-import kWh inside an already net-negative bucket)
overvalues by PCIA/kWh in the opposite direction. `sign_flip_buckets` — buckets whose
aggregate sign genuinely flips between the billed year and the counterfactual — is a smaller,
SECONDARY contributor, reported as `sign_flip_residual_usd` (gap minus the PCIA effect).
**Correction:** an earlier version of this module and its artifact named sign flips as the
SOLE cause of the gap. An independent review (PR #77) built a counterexample (metered solar,
zero sign flips anywhere, a real nonzero gap) that the sign-flip-only story predicts as ~$0
and the PCIA mechanism predicts correctly — proving the original explanation false. The fix
is verified by `test_quiet_night_floor.case_reconciliation_gap_is_explained_by_pcia_not_
sign_flips`, which asserts BOTH that the old theory's prediction misses (falsifying it) and
that the corrected theory's prediction matches closely (confirming it) on the same fixture.
`pricing.reconciliation.scope_of_agreement` states plainly what this reconciliation does and
does not prove: it validates the NETTING/AGGREGATION treatment only (both methods share the
identical `_split_floor` allocation and the identical `rates.py` constants), not the rate
constants themselves and not the physical floor-allocation model, where the larger,
separately-quantified `floor_assumption_violations` limitation actually lives.

**Sensitivity (issue AC4, `sensitivity_per_100w`).** Re-bills (method b) at 100 W steps from
100 to 1,200 W (`MAX_REDUCTION_W`) and reports both the marginal $/100W at the sensitivity
step nearest the currently-measured floor and the general linear-fit slope across the whole
range, stating explicitly which is which and how far the removal is from being perfectly
linear (a bucket sign flip inside the tested range would show up as measurable nonlinearity).

**Battery interaction (issue AC5, `battery_interaction`).** Re-runs
`battery_dispatch_policies.run_batt` (same greedy policy, same Powerwall 3 config, the same
steady-state SOC convergence §3.24 established) on the baseline and floor-removed series,
isolating the floor's own effect (no EV-shift behavior model stacked on top). On the real
measured year, removing the ~1 kW floor cuts the battery's own marginal saving by roughly
45% — the floor persists into the 4-9pm on-peak window the battery discharges into, so a
smaller floor leaves less expensive import for the battery to displace. Reported with a
noted, uncorrected confound: `run_batt`'s greedy EV-spillover gate is evaluated on each
series' own import values, so a small amount of energy becomes servable only in the
counterfactual purely because the floor's removal pushed it under the gate threshold, not
because of any real behavioral change (~$26/yr in the conservative direction on the real
year).

**Confidence labels (issue AC6).** `confidence_labels()` keeps the measured LOAD (two
independent instruments: 15-minute import data and the SAM whole-home meter) separate from
the attested CAUSE (owner's word only, report prose — not independently verified with a
plug-meter study), and separately labels the pricing and battery-interaction sections as
modeled (both assume the measured floor magnitude holds constant across all 8,760 hours of
the year).

**Reproduction.** `price_map_from_rates()` computes the price map straight from `rates.py`
(the canonical module) and cross-checks it against the committed `data/extra_results.json ->
price_map` the issue cites, to the cent — never reading that artifact as the operative rate
source. `quiet_night_floor.json` is written directly to `data/` (repo-root discovery, atomic
tmp-then-replace, the same convention §3.24/§3.27 use); `test_quiet_night_floor.py` covers
the night-floor extraction, both pricing methods individually, the reconciliation (including
the falsifying test above), the sensitivity calculation, the confidence-label distinction,
and byte-identical artifact regeneration.

---

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
The 16,660 kWh figure above is from that original, unarchived workpaper computation, over the
full 365-day year. Issue #37 subsequently gave the file a committed generator,
`analysis/threeway_production_validation.py`, whose own `meter_derived` column reproduces the
SAME identity but is NOT directly comparable to the 365-day figure above: two DST transition
dates are excluded (the flat-grid SAM export and the wall-clock Green Button meter do not
share a common hour boundary on those two calendar days — see the script's own docstring), so
the generator's total is 16,459.2 kWh over 363 measured days. Over those days it tracks
enphase_meter at r=0.99996 (MAE 0.160 kWh/day) and pvoutput at r=0.99986 (MAE 0.789 kWh/day) —
both inside the ±2% spread the two reference instruments already carry between themselves
(r=0.99989 on the same window). Whether the headline 16,660 kWh figure above should be
replaced with this now-reproducible number is a report-prose question issue #37 explicitly
left open; this note exists so this section's own claim about what the committed artifact
contains stays accurate regardless of how that question is eventually settled.

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
~0.5%** and is not the source of the gap. The model prices the *entire* year at current
**6/1/2026 rates**, and this section used to attribute the whole ~$1,622 gap to that vintage
difference — the bills were mostly rendered on cheaper 2025 tariffs. That attribution was
never actually measured across the full year; issue #3 measured it on two matched periods and
found the claim doesn't hold (delivery price level barely moved while its shape rotated hard),
and §3.27 (`analysis/reprice_by_vintage.py`, issue #30) now measures it properly, over all 13
billing periods, against the actual UDC delivery tariff in force each period and the real fixed
charge, cleanly separated from CEA generation. The result: rate vintage (delivery + fixed charge)
explains **-$73 of the SOURCED gap (about 4.5%)** — genuinely small, confirming issue #3's finding
at the scale of the whole year, for the portion `rates_history.py` can actually price. This is a
measured floor, not a ceiling: 1,168.3 kWh across 247 days of off-peak delivery has no sourced
historical rate at all and folds into the residual below, so the true total vintage effect could be
somewhat larger than 4.5% (§3.27's sourced-vs-total caveat). A second, previously unexamined
confound — the native model's rolling window (2025-07-24 minus 365 days) and the bill-aligned window
(2025-06-27..2026-06-26) are not the same 365 days — explains only **-$54 (about 3.3%)**, once
compared on a like-for-like modeled basis. The largest correction is neither of those:
**generation_tou_window_effect, -$273 (about 16.8%)**, is NOT a vintage effect —
`data/cca_generation_rates.csv` proves CEA's charged generation rate never moved and equals today's
rate exactly, on every one of these 13 periods — it is the same TOU-window-shape confound the model
already carries for delivery, now quantified for generation dollars specifically instead of left
undetermined, with two small real-but-unmodeled generation-side fees (CEA's "Clean Impact Plus"
product adder, **+$13 or 0.8%**, and a per-period state surcharge tax, **+$4 or 0.2%**) separated
out into their own terms rather than folded silently into the window-shape figure. (Two earlier
versions of this section made progressively narrower versions of the same mistake: one attributed
nearly all of the gap to "rate vintage" at ~20%, double-counting the TOU-window effect as vintage;
a later one corrected that but still let the CIP adder and surcharge tax ride inside
`generation_tou_window_effect` as if they were part of the window-shape story. Both have been
corrected.) The remaining **-$1,239 (about 76.4%)** is the real, still-undecomposed model-vs-bill
gap; §3.27 names three non-exclusive candidates (unsourceable historical PCIA/NBC vintage, an
off-peak delivery bucket this household net-exported through for most of the analysis year and so
never has a historical rate for, and the same TOU-window-shape limitation acting on
delivery/PCIA/NBC) without ruling any in or out. Consequence
adopted throughout, unchanged: absolute dollars are anchored to actual bills; the model is trusted
for **rankings and deltas** (savings), which are driven by on-peak arbitrage priced identically in
every variant.

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

**6.7 What CI actually verifies, and what stays local-only (issue #44).** A green CI badge on
a pull request is a claim about a specific, checkable set of properties — recorded here so the
badge stops implying more coverage than it has. `analysis/test_scripts_runnable.py` classifies
every generator into exactly one of three tiers, and the case-by-case tier assignment
(`CI_RUNNABLE` / `NEEDS_PRIVATE_ARCHIVE`, plus `VERIFIED_ELSEWHERE_IN_CI` for the second tier)
is itself guarded (`case_every_generator_is_covered_by_one_of_the_two_tiers`,
`case_verified_elsewhere_mapping_is_real_and_wired_into_ci`), so this can't silently drift from
the code:

- **`CI_RUNNABLE` (15 generators)** — `behavior_rebuild.py`, `battery_dispatch_policies.py`,
  `package_results.py`, `report_data.py`, `analyze.py`, `analyze_norelief.py`,
  `carbon_fullyear.py`, `tou_audit.py`, `rates_history.py`, `tou_spread.py`,
  `nem3_grandfathering.py`, `dsgs_vpp_backtest.py`, `battery_sizing_curve.py`,
  `perfect_foresight_dispatch.py`, `tou_structure_stress.py` — run end to end in CI against a
  synthetic Green Button export shaped by `test_scripts_runnable.py`'s own fixture (a real
  household file only has to look like this, not be it: 96-slot/DST-adjusted days, an EV
  session signature, a solar shape). **CI verifies their main computational path directly**,
  with no skip path at all (`case_the_ci_tier_cannot_skip`).
- **`NEEDS_PRIVATE_ARCHIVE` (16 generators)** need an input that shared fixture cannot supply
  (a bill-PDF corpus, a SAM-8760 pair, a monitoring production history, or a fail-closed
  tie-out against a real-year-derived committed artifact that a synthetic run must legitimately
  fail). Before issue #44, CI's only exercise of these was `test_scripts_runnable.py`'s own
  real-archive case, which skips entirely on a runner (no `private/`) — so a defect in, say,
  `service_headroom.py`'s `build()` could pass every CI check (demonstrated: a 10% error
  injected into `build()` on a fresh archive-less checkout still reported 78/81 passed, 3
  skipped, exit 0). An earlier draft of this section claimed 15 of the 16 were covered this
  way; a clean-room review (a `git archive` checkout with no `private/` at all) found 9 of
  those 15 false — the named test file existed and had a CI step, but every case in it that
  actually invokes the real generator SKIPS without the archive, and only leaf/unit checks on
  synthetic text ran. That defect is now caught mechanically:
  `case_verified_elsewhere_mapping_is_real_and_wired_into_ci` builds a fresh archive-free root
  (the real `analysis/` + the real committed `data/`, no `private/` anywhere) and actually RUNS
  every claimed case there, failing if any of them skips instead of trusting the dict. The
  honest count as of this commit:
  - **8 of 16 are verified end to end in CI**, each by its own dedicated test file whose
    claimed case was confirmed, by that mechanical check, to pass (not skip) with no private
    archive present:
    - `service_headroom.py` (`test_service_headroom.py`, pre-existing).
    - `battery_backup_sims.py`, `deep_analyses.py`, `lifetime_payback.py`, `soiling_analysis.py`,
      `extended_findings.py` (added for issue #44's first pass). Each was validated by
      deliberate fault injection — a small arithmetic defect planted in a scratch copy of the
      generator's main path was confirmed to turn its test red, then reverted and confirmed
      green again.
    - `battery_plan_matrix.py` (added on review): its NEEDS_PRIVATE_ARCHIVE entry originally
      claimed its fail-closed tie-out against `battery_dispatch_policies.json` "must diverge"
      on synthetic inputs; that claim was **false** and was disproven with a working
      demonstration (an independently-computed reference bill, transcribing the generator's own
      published-rate-table formula, promoted into a throwaway `data/` as the tie-out target —
      satisfied for real, not neutered). `test_battery_plan_matrix.py` formalizes this and was
      fault-injection tested the same way as the first five.
    - `carbon_dispatch_tradeoff.py` (added on review): `compute()` now runs end to end on the
      synthetic Green Button fixture against the REAL, PUBLIC, committed
      `data/caiso_hourly_intensity.csv` (aggregate CAISO grid data — not household-specific, so
      no synthesis needed), with a promoted `battery_dispatch_policies.json` tie-out and an
      independent hand-computed baseline-CO2 check. Fault-injection tested.
  - **8 of 16 are NOT verified end to end anywhere in CI today.** Per generator, what was
    actually checked (not guessed) before concluding this:
    - `uncertainty_propagation.py` — **assessed, harder than it first looked.** Its own code
      comments describe "the same tie-out shape as `battery_plan_matrix.py`", but `build()`
      actually cross-checks FOUR committed artifacts at once —
      `battery_dispatch_policies.json`, `tou_spread.json` (its `ESC_HI` must match
      `tou_spread.json`'s own escalation-ladder ceiling exactly), `deep_results.json`
      (reproduced to float-equality via a specific rounding chain through
      `battery_dispatch_policies.json`'s `post_behavior.mid.battery_marginal`), and
      `extended_results.json`'s `tornado_battery` block — all of which would need to derive
      from the SAME synthetic fixture with sub-dollar tolerances between them. That is a
      four-generator consistent-artifact chain, not a single promoted JSON; not attempted given
      the effort already spent on this issue.
    - `gross_import_decomposition.py` — **assessed, partially promising.** Its bill-ground-truth
      read (`load_bill_periods()`) is against `data/bill_periods_electric.csv`, which is already
      PUBLIC and committed (not private) — so no bill-PDF corpus is needed for that part. But it
      requires that file to contain rows for two SPECIFIC hardcoded historical statement periods
      (`PERIOD_2024`, `PERIOD_2026`) and reconciles a real two-calendar-year-apart pair of SAM
      8760 exports against them; a synthetic fixture would need to land inside those exact real
      date windows rather than inventing new ones. Plausibly tractable with more time; not
      attempted.
    - `reprice_by_vintage.py` — **not independently assessed in depth**; grouped with
      `gross_import_decomposition.py` on the strength of its own NEEDS_PRIVATE_ARCHIVE entry
      (reconciles interval data against the real 13-period bill corpus), not verified by reading
      its source. Not attempted.
    - `parse_bills.py`, `bill_decomposition.py`, `irreducible_bill.py`,
      `cca_rate_extraction.py`, `cca_bundled_counterfactual.py` — need a genuine bill-PDF-shaped
      text corpus (these parse PDF statement text directly, not derived CSVs), which is a
      larger, qualitatively different fixture-building effort than a synthetic Green Button
      export or a promoted JSON artifact. **Believed disproportionate effort relative to this
      issue's scope, not attempted.**
- **The real-archive byte-tie-out itself** (`case_generators_run_on_the_real_archive`: every
  `NEEDS_PRIVATE_ARCHIVE` and `CI_RUNNABLE` generator reproduces its committed artifact
  byte-for-byte against the actual private data) is **local-only** — CI runners never have
  `private/`, so this case always skips there. It is the strongest single check in the suite
  (it is literally the CLAUDE.md §9 regeneration gate folded in) and must be run manually
  (`./.venv/bin/python analysis/test_scripts_runnable.py` from the `private/verify` sandbox,
  or from a checkout with `private/` staged) before trusting a change to any generator.

In short: CI now either runs a generator's real logic against real or fixture-shaped data, or
says explicitly which generator it didn't and why — never a guard that reports success without
checking anything, and never a passing summary that silently skips its strongest case. The
"which generators are covered" claim is itself mechanically checked against a live run, not
just against a name appearing in a dict, specifically because the previous version of this
paragraph was wrong and nothing caught it before it shipped.

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

### 8.1 `analysis/generate_report.py` — filling the report without an agent harness (issue #39)

The bullets above describe the tooling used to build THIS repo's own `index.html`. This
subsection describes a different, later capability: a reader who has cloned the repo, run
Phases A–C, and regenerated `data/*.json` for their own household can turn those artifacts
into a finished report with a paid LLM API key and no agentic coding tool at all.

- **Provider abstraction (`analysis/llm_providers.py`).** One chokepoint,
  `_post_json(url, headers, body)`, built on `urllib.request` alone — zero new dependencies,
  verified against `requirements.txt` by `test_llm_providers.py`'s own AST walk (no other
  function in the module may construct a `Request` or call `urlopen`). Three ~40-line
  adapters (`_call_anthropic`, `_call_openai`, `_call_google`) each build that vendor's own
  native request shape (Anthropic's Messages API, OpenAI's Chat Completions API, Google's
  `generateContent`) and normalize the response to `{text, finish_reason, usage}` — never an
  OpenAI-compatibility shim, since Anthropic's and Google's compat endpoints are second-class
  and lag on parameters. No dated snapshot model id is hardcoded anywhere:
  `PROVIDERS[*]["default_model"]` is the literal sentinel `DEFAULT_MODEL_SENTINEL`, and
  `call()`/`generate_report.py` both fail closed if a real id was never configured;
  `--list-models` calls each vendor's own model-list endpoint so the id comes from the
  vendor, not a guess typed into this repo.
- **Credentials.** A ten-line `KEY=VALUE` parser (`load_env()`) reads a repo-root `.env`
  (`.env.example` documents `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`) — no
  `python-dotenv` dependency, no key ever accepted as a CLI argument. Every loaded key value
  is registered with `_register_secret()`; `_redact()`/`_redact_loaded()` scrub it from every
  exception message `llm_providers.py` raises, proven by an induced-error test that crafts an
  HTTP error body echoing a fabricated key and asserts the raised `ProviderError` never
  contains it.
- **Egress allowlist (`llm_providers.preflight()`).** The privacy gate for this new network
  path (`CLAUDE.md` §4): every item contributing to a request body must be one of
  `data_file` (a git-tracked path under `data/`), `template_file` (`report-template.html`
  itself), `claude_excerpt` / `todo_text` (caller-asserted literal strings — a TODO block's
  own instruction text, or a resolved token's rendered value labeled `NAME = value`), or
  `household_token` (a `(name, value)` pair `preflight()` RE-RESOLVES through
  `report_tokens.resolve_token()` itself before trusting it, so a stale or tampered value
  can never reach a request body under that label). `private/household.yaml` is never an
  eligible `data_file` — its `public-ok` values enter a request only as already-rendered
  token strings. The assembled request body is then scanned with this repo's own gitleaks
  rule chain (`.gitleaks.toml`, plus `private/pii-rules.toml` when present) before anything
  may be sent; a missing or non-functional gitleaks binary fails SAFE (refuses) rather than
  skipping the scan. `--dry-run` writes every would-be request body under
  `private/llm_dry_run/` and calls `preflight()` with `dry_run=True`, which returns before
  any adapter could plausibly be called — so a payload can be inspected, and its cost
  estimated, before a single API call is spent.
- **The classification map (`analysis/report_blocks.py`).** Parses every actionable
  `<!-- TODO ... -->` block out of `report-template.html` (105 of them; the top-of-file
  authoring-instructions comment is excluded, and `test_report_blocks.py` re-parses the
  template fresh on every run to prove the map still covers it exactly) and classifies each
  one `prose` (an LLM writes it, tokens only), `data` (filled mechanically — one table row
  per plan/policy/year/season, or a vestigial comment already covered by an adjacent
  `{{TOKEN}}`), or `human` (a fact this pipeline has never measured: a hardware price quote,
  incentive-program status, or a new statistic — a degradation rate, a regression
  coefficient — with no `report_tokens.TOKENS` entry at all). `generate_report.py` fails
  closed on any block the map doesn't cover.
- **The numeral guard.** `generate_report.py` hands the model ONE `TODO` block's own text
  plus that block's scoped token values (every token live in its `<h2>` section, union any
  token its own instruction names) and nothing else — never the surrounding HTML, CSS, or
  JS. `find_fragment_violations()` then rejects any returned fragment containing a digit that
  is not inside a `{{TOKEN}}` reference naming a real `report_tokens.TOKENS` entry, or a
  `§N` section reference; a committed literal allowlist for anything else starts empty and
  stays that way absent a reviewed, cited exception. A violation — or an abnormal
  `finish_reason`, or a `prose_lint.py` violation (the mechanical gate for CLAUDE.md's banned
  constructions: rule-of-three padding, negative parallelism, filler transitions,
  promotional adjectives, and §9's literal process-narrative ban strings) — earns exactly one
  corrective retry, then hard-fails that block by name. A failed block is reported, never
  spliced in partially, and never silently dropped.
- **Caching and determinism.** Every prose block's accepted fragment is cached under
  `private/report_cache/`, keyed by a hash of the block id, a prompt-version constant, its
  exact scoped token VALUES, its own TODO text, the provider, and the model id. Re-running
  with nothing changed reuses every cache entry (zero new API calls, byte-identical output);
  changing one artifact value changes the resolved token(s) it feeds and invalidates only the
  block(s) whose scope named it — both are asserted by call-count in
  `test_generate_report.py`, not by inspection.
- **Provenance overrides.** `report_tokens.py`'s `GENERATION_TOOL` / `REVIEW_TOOL_1` /
  `REVIEW_TOOL_2` are hardcoded to the values `CLAUDE.md` §11 requires for THIS repo's own
  hand-curated `index.html` ("Claude Cowork (Fable 5)", "Claude Code (Fable 5)", "Codex
  (GPT-5.6 Sol)"). Using them verbatim in a fork's generated report would be false on two
  counts — it would name a tool that run never used, and assert an independent and
  adversarial review that never happened — so `generate_report.py` overrides all three for
  its own output only: `GENERATION_TOOL` becomes `"{provider} ({model})"`, the actual
  provider/model that run used; `REVIEW_TOOL_1`/`REVIEW_TOOL_2` become a fixed, digit-free
  disclaimer that no review is recorded for the run, which is NEVER overridable — not by
  `--human-answers`, not under any circumstance — because a script cannot verify a review
  happened and must never assert one it did not perform. `test_generate_report.py` asserts
  a sneaked-in override attempt for the review clause is ignored and that neither of
  `report_tokens.py`'s original review-tool strings ever survives into the generated file.
- **Human-in-the-loop completion and final write.** `human`-classified blocks, and the three
  `KNOWN_GAPS` tokens (`report_tokens.py`) that appear in LIVE template markup rather than
  inside a `TODO` comment, are filled only from a caller-supplied `--human-answers` JSON file
  of literal, operator-researched text — never invented by the script. If any block or gap
  token is left unresolved, NOTHING is written and the run reports exactly what is missing
  and exits non-zero. On success, the fully spliced document (including the `<script>`
  block's `const D` chart-data placeholders, filled mechanically from the same artifacts
  `test_report_consistency.py` already checks `index.html`'s hand-written arrays against) is
  staged to a temp file and handed to `analysis/publish.py`'s `promote_set()` as
  `index.generated.html` — the same crash-consistent, single-writer promotion
  `battery_plan_matrix.py` and its siblings use for `data/*.json`. `index.html` is never
  written to; `test_generate_report.py` proves this by running a full generation into a
  directory holding a copy of the real `index.html` and asserting it comes out
  byte-unchanged.

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
| `data/bill_periods_gas.csv` | one row per gas period: `statement_date, period, period_end_month, therms, total_gas_service, billed_amount, baseline_rate, nonbaseline_rate, baseline_allowance_therms, gas_energy_charge_rate, other_fees_rate` — `baseline_rate`/`nonbaseline_rate`/`gas_energy_charge_rate` are each a day-weighted blend across however many rate segments the period has (issue #98; see `bill_gas_detail.csv` below for the segment-level detail this collapses); `other_fees_rate` (Public Purpose Programs + State Regulatory Fee combined — Codex review, issue #98, pass 1) is a THERM-weighted blend instead, since that charge type splits by therm count on a mid-period rate change, not days |
| `data/bill_tou_detail.csv` | long format: `statement_date, period, section (delivery/generation), season, tou_period, kwh, rate_per_kwh` — the rates as printed on each bill |
| `data/bill_gas_detail.csv` | long format (issue #98): `statement_date, period, charge_type (gas_service/gas_energy/other_fees), segment, segment_days, segment_therms, baseline_rate, nonbaseline_rate, energy_rate, other_fees_rate` — one row per rate segment for EACH gas charge type. "Gas Service" (the tiered baseline/non-baseline rate) and "Gas Energy Charge" (a flat, untiered $/therm charge on every therm) split by DAY on a mid-period rate change; "other_fees" (Public Purpose Programs + State Regulatory Fee combined — Codex review, issue #98, pass 1) is also flat and untiered but splits by THERM COUNT instead. All three charge types have INDEPENDENT segment counts within one period, so `charge_type` is the discriminator and only the columns relevant to it are populated. This is what a true marginal-tier gas rebilling needs and `bill_periods_gas.csv`'s blended columns cannot provide — see `heat_pump_conversion.py`'s `gas_savings_by_period()` for the reference consumer |
| `data/electric_bill_summary.csv`, `data/gas_bill_summary.csv` | regenerated in their original schemas |

**Reproduction gate.** The script rewrites the two legacy summaries from the same parse.
`gas_bill_summary.csv` regenerates byte-identically to the version committed before the
script existed, and `electric_bill_summary.csv` reproduces 12 of its 13 rows exactly. The
regeneration command in `CLAUDE.md` runs the parser and diffs all six artifacts.

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
- *Transactional publication.* The six artifacts are one evidence set. Each is staged to a
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

### 11.3a The condenser nameplate field (issue #45)

`panel.existing_ac_nameplate_rla_a` — the existing air conditioner's or heat pump condenser's
own nameplate RATED-LOAD AMPS (RLA) specifically, not MCA, added for the fifth
`heat_pump_replaces_ac` case (a heat pump REPLACING the existing A/C on its own circuit, rather
than every other case's ADD). RLA rather than MCA because MCA (NEC 440.32/440.33) already
carries a 125% margin on the largest motor, and the case applies its own, independently
justified 125% (the NEC 220.87(2) factor) on top of whatever this field holds — recording MCA
would compound two different margins and overstate the credit. It is `public-ok`, on the same
principle 11.3 states rather than as an exception to it: it is a bare equipment rating (the same
class of fact as `solar.kw_ac` and `charger.kw`, both already `public-ok`), it is load-bearing
(the case's own noncoincident-credit arithmetic is 125% of it, and AC1/AC2 of the issue require
that arithmetic shown), and a `private-only` tier here would make the credit unpublishable — the
case would have nothing to show. This is a DIFFERENT fact from `existing_ac_ocpd_a` (11.3's
derived exception): that is the branch breaker's rating, read off the schedule; this is the
equipment's own draw, and NEC 440.22(A) — not 240.6(A), which is only the standard-ampere-rating
list — permits a breaker to be sized up to 175-225% of the equipment's RLA, so the two numbers
routinely differ. The case also caps the heat pump MCA it solves for at `existing_ac_ocpd_a`
(the existing breaker's own rating): a solved MCA above it would need larger branch conductors
too, which is no longer "reusing the circuit" for free, and the cap itself is only an upper
bound on the true conductor ampacity, never a measurement of it (`conductor_ampacity_caveat`
names this when it binds). The case fails closed without the RLA reading — `not_determined`
rather than an assumed credit, and never a guessed `fail` either, since an unbounded real
credit could still rescue a case that fails only at zero credit — per CLAUDE.md §0; see
`analysis/service_headroom.py`'s `heat_pump_replacement_case()`.

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

**Two parsing gaps found while building this.** `bill_decomposition._LINE_PATTERNS` anchored its
per-kWh-rate lines on a literal `x $`, with no allowance for a minus sign printed *before* the
dollar sign — SDG&E prints PCIA that way routinely (`PCIA 2023 802 kWh x -$.03161 -25.35`,
2025-03-04 statement), and the unmodified pattern simply failed to match, silently undercounting
PCIA. `irreducible_bill.py` carried its own patterns (`_OWN_PATTERNS`) that allowed the leading
sign, discovered here because this script's independent cross-check came out $25.35 high on that
exact statement until the sign was allowed for; issue #46 has since fixed
`bill_decomposition._LINE_PATTERNS` itself to accept the sign in either position (neither of the
two statements that module's own year-over-year comparison uses happened to trigger the gap, so
its committed artifact is unchanged by the fix). Separately, those same per-kWh-rate lines
(Wildfire Fund Charge, PCIA, the Incremental Procurement Cost Adjustment) can reprint more than
once *within a single period* when a mid-cycle rate change splits it into segments (confirmed:
wildfire on 2025-03-04 and 2026-02-02; PCIA on 2025-03-04 and 2026-05-04) — each reprint is a
portion of the same charge and must be summed, not conflated with a genuine conflict.
`bill_decomposition.py`'s own conflict guard has no segment-summing concept and correctly refuses
any same-period duplicate with differing values, genuine or segmented alike, which is why
`irreducible_bill.py` still carries its own patterns and sums same-name segments
(`_SUM_ACROSS_SEGMENTS`) rather than calling `bill_decomposition.charge_lines()` for this purpose.

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
13.3%** ($490.91), **MID 33.6%** ($485.44), **HIGH 39.6%** ($486.90) — MID and HIGH both lower
in dollars than LOW despite the larger percentage, because their projected bills are smaller and
their gross imports fall relative to the baseline. Combined (**LOW 21.2%, MID 53.6%, HIGH
63.2%**), the figure is a larger fraction of a *smaller* projected bill by arithmetic necessity,
not a claim about which package is better — and, per the correction above, not itself a floor.
(MID/HIGH's non-bypassable dollars reflect `battery_dispatch_policies.run_batt()`'s per-configuration
charge rate — issue #40 Finding 2: `compute_package_gross_imports()` previously called `run_batt()`
with no `charge_kw` at all for either package, silently defaulting to the old symmetric 11.5 kW
charge behavior; it now passes `charge_kw=CHARGE_KW` for MID's 13.5 kWh bare unit and
`charge_kw=CHARGE_KW_WITH_EXPANSION` for HIGH's 27 kWh with-expansion unit, the same split
`battery_dispatch_policies.json` itself uses. The effect here is small — MID's non-bypassable
figure moves $485.78 → $485.44, HIGH's $487.02 → $486.90 — because a tighter charge cap only
marginally changes how much of this household's solar surplus and grid top-up get stored before
NBC-priced import happens.)

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
