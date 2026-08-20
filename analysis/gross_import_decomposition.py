#!/usr/bin/env python3
"""Gross imports are rising (issue #16): is it the house or the array?

Two comparable early-summer bill periods show gross imports climbing and net
imports climbing FASTER:
    5/25/24 - 6/25/24 (32 days): gross 1,438 kWh, net   346 kWh
    5/29/26 - 6/26/26 (29 days): gross 1,934 kWh, net   987 kWh
Net rising faster than gross is the signature of a production problem, a
consumption problem, or both. This script decomposes the change and states,
with evidence, how much is which.

DATA REALITY THIS SCRIPT IS HONEST ABOUT (CLAUDE.md sec 0 -- no fabricated
precision):
  - This repo has NO measured plane-of-array irradiance anywhere. A true,
    weather-normalized Performance Ratio is NOT DETERMINED. What IS available:
      (a) a deterministic Haurwitz CLEAR-SKY model (reused, read-only, from
          analysis/soiling_analysis.py) -- normalizes for solar GEOMETRY
          (day length, sun angle, calendar effects), not for actual cloud
          cover;
      (b) day-matched calendar-window comparison across years, which controls
          for the typical seasonal irradiance pattern (including San Diego's
          "June gloom") without needing a raw irradiance series, because it
          uses each year's OWN measured production for that specific window;
      (c) an empirically FITTED ambient-temperature sensitivity (regression
          of clear-day performance on Open-Meteo daily mean temperature,
          season- and soiling-controlled) as a magnitude check, not a
          manufacturer datasheet coefficient asserted as fact.
  - The private Green Button archive staged in this environment covers only
    2025-06-27 .. 2026-07-26 (SDG&E's ~13-month export window). It does NOT
    reach back to May/June 2024. Daily production records
    (pvoutput_daily.csv / enphase_daily_production.csv) and the whole-home CT
    archive (SAM 8760, samA.csv/samB.csv) likewise start no earlier than
    2025. Every 2024-side figure below is therefore an ESTIMATE built from
    what DOES survive from 2024 -- the bill's own net_kwh/gross_kwh (exact)
    and the calendar-year PVOutput total (data/pvoutput_yearly_2020-2025.csv,
    exact) -- never a fabricated interval series. Session-level EV detection
    (analysis/behavior_rebuild.py's detect_sessions, reused read-only) can
    only run on the 2026 side; the 2024-side EV share of the INCREASE is
    NOT DETERMINED, and this is stated plainly rather than guessed.

METHOD SUMMARY (full derivation: TECHNICAL.md 3.26):
  1. Production, 2026 window: measured directly (pvoutput_daily.csv, cross-
     checked against enphase_daily_production.csv).
  2. Production, 2024 window: ESTIMATED as (empirical seasonal fraction of a
     year's output that falls in this calendar window, measured from the one
     year we have daily data for) x (2024's own exact calendar-year total).
  3. Gross household load (not net import) for 2026: measured directly from
     the whole-home CT archive (SAM 8760). For 2024: ESTIMATED via the
     bill's own net_kwh (exact) plus the estimated 2024 production.
  4. Hourly gross load (CT) and an hourly-resolution PRODUCTION series
     derived by combining CT load with the Green Button's import/export
     columns feed a physically-grounded counterfactual: 2024's consumption
     is PINNED by an exact accounting identity (bill-exact net_kwh plus
     estimated production), leaving production_scale as the one genuinely
     free parameter, back-solved (not assumed) so the simulated 2024 corner
     matches the 2024 bill exactly. This makes the simulated-vs-bill
     agreement EXACT BY CONSTRUCTION, not an independent validation --
     with no 2024 hourly shape available, the specific split into a
     consumption term and a production term depends on the modeling choice
     of HOW 2024 differed from 2026's diurnal shape (this script's default:
     uniform proportional scaling). A companion identifiability check
     (identifiability_robustness_check) re-solves under one materially
     different, still-defensible shape assumption (all consumption decline
     concentrated in EV-charging hours) to test whether the reported split's
     CONCLUSION -- not its exact magnitude -- survives that alternative; see
     its own docstring and the artifact's own
     decomposition_identifiability_robustness_check field for the result.
  5. EV share of the 2026 window's consumption comes from detect_sessions,
     not a guess; its share of the two-year INCREASE is bounded, not
     claimed exactly, because 2024's EV baseline is unavailable.
  6. The 6-year (2021-2025) PVOutput efficiency trend is refit here
     (OLS / CAGR / Theil-Sen, all artifact-backed) and reconciled explicitly
     against index.html's existing hand-computed "~0.5-1.0%/yr" claim, using
     the soiling module's own validated 2024-08-12 cleaning event (11.8%
     gain after ~4.4 dry months) as a bound on how much of the year-to-year
     swing weather/soiling timing alone can produce.

Run from private/verify per the standard sandbox (needs usage.csv, samA.csv,
samB.csv, plus the committed data/ files and private/household.yaml beside
it -- soiling_analysis.py and behavior_rebuild.py are imported READ-ONLY,
never modified, never re-run as generators). Writes gross_import_
decomposition.json to the CWD; promote it to data/ by hand, matching
behavior_rebuild.py's own cwd-artifact convention.
"""
import csv
import datetime as dt
import json
import math
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def _repo_root():
    """Same contract as household.py / deep_analyses.py: nearest ancestor of
    the CWD (sandbox convention) or of this file containing both analysis/
    and data/."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"

# CWD-relative sandbox inputs (overridable by tests, matching
# test_uncertainty_propagation.py's br.CSV = <absolute path> convention).
USAGE_CSV = "usage.csv"
SAM_A_CSV = "samA.csv"          # 2026 (partial year), per stage-private-data.sh
SAM_B_CSV = "samB.csv"          # 2025 (full year)

# The two bill periods this issue is about, exactly as printed in
# data/bill_periods_electric.csv (ground truth -- read, not transcribed).
PERIOD_2024 = "5/25/24 - 6/25/24"
PERIOD_2026 = "5/29/26 - 6/26/26"

# Calendar bounds of the 2026 period (inclusive both ends, matching the bill's
# own 29-day count: May 29-31 + June 1-26).
P26_START = dt.date(2026, 5, 29)
P26_END = dt.date(2026, 6, 26)

# The 2024 bill period (5/25/24-6/25/24) is 32 days -- THREE days longer than
# the 29-day 2026 period above (adversarial review pass 1, finding 1: an
# earlier draft reused the 2026 window's own PERIOD-TOTAL fraction directly
# against the 2024 bill's 32-day total, silently comparing mismatched day
# counts). See production_block() for how the day-count difference is
# corrected via a per-day rate ratio rather than a differently-dated window
# (that fix covers the WEATHER-BASED production estimate only).

# The decomposition's own hourly counterfactual (decomposition_block) needs a
# SECOND, independent fix for the identical day-count issue at the SIMULATION
# level (Codex review pass 2, finding 1): scaling the real 29-day P26_START..
# P26_END hourly vector to hit a 32-day bill total compresses 3 missing days'
# worth of energy into 29 real hours, distorting the nonlinear
# max(0, load-production) overlap the whole method depends on -- a real,
# 29-hour-count vector simply cannot represent a 32-day period without that
# distortion, no matter how it is scaled. TEMPLATE_START/END is a REAL 32-day
# 2026 window -- May 25-June 25, matching PERIOD_2024's own day count AND
# calendar span exactly (this range falls entirely inside TRAILING_START..
# TRAILING_END below, so the archive covers it) -- used as the shared base
# vector for BOTH years in decomposition_block, so no vector is ever
# stretched or compressed to a day count it wasn't measured at.
TEMPLATE_START = dt.date(2026, 5, 25)
TEMPLATE_END = dt.date(2026, 6, 25)

# The trailing-year window analysis/soiling_analysis.py itself uses for its
# daily production/weather record -- reused here for consistency, not
# reinvented, since it is the only window with both daily production and
# daily temperature data in this repo.
TRAILING_START = dt.date(2025, 7, 24)
TRAILING_END = dt.date(2026, 7, 23)

ARRAY_KW = 10.05  # nameplate, matches soiling_analysis.py's own meta and index.html


# ---------------------------------------------------------------------------
# 0. Reuse soiling_analysis.py's own machinery, read-only (CLAUDE.md: don't
#    reimplement soiling/clear-sky methodology this repo already has).
# ---------------------------------------------------------------------------
import soiling_analysis as _sa  # noqa: E402


def _clearsky_doy_table():
    """kWh/m2/day of Haurwitz clear-sky insolation by day-of-year, computed
    once over a real leap year (2024) so doy 1..366 are all covered. The
    Spencer declination soiling_analysis.py uses depends only on doy, not on
    which calendar year it falls in, so this table is exact for any year and
    avoids recomputing the (slow, pure-python) integral six times over."""
    table = {}
    d = dt.date(2024, 1, 1)
    for _ in range(366):
        table[d.timetuple().tm_yday] = _sa.clearsky_ghi_kwh_m2(d)
        d += dt.timedelta(days=1)
    return table


CLEARSKY_DOY = _clearsky_doy_table()


def clearsky_for_date(d):
    return CLEARSKY_DOY[d.timetuple().tm_yday]


def _date_range(start, end):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


# ---------------------------------------------------------------------------
# 1. Bill ground truth (data/bill_periods_electric.csv) -- exact, not modeled.
# ---------------------------------------------------------------------------
def load_bill_periods():
    rows = list(csv.DictReader(open(DATA / "bill_periods_electric.csv")))
    out = {}
    for label, period_str in (("period_2024", PERIOD_2024), ("period_2026", PERIOD_2026)):
        matches = [r for r in rows if r["period"] == period_str]
        if len(matches) != 1:
            raise SystemExit(
                f"expected exactly one bill_periods_electric.csv row for "
                f"period={period_str!r}, found {len(matches)}")
        r = matches[0]
        net = float(r["net_kwh"])
        gross = float(r["gross_kwh"])
        out[label] = {
            "statement_date": r["statement_date"],
            "period": period_str,
            "days": int(r["days"]),
            "net_kwh": net,
            "gross_kwh": gross,
            # Export = Gross import - Net ("Total Usage" = Consumption -
            # Production, TECHNICAL.md's own "Gross vs net" note): both
            # gross_kwh and net_kwh are exact bill lines, so this is exact too.
            "export_kwh": round(gross - net, 3),
        }
    return out


def yearly_pvoutput_row(year):
    rows = list(csv.DictReader(open(DATA / "pvoutput_yearly_2020-2025.csv")))
    matches = [r for r in rows if int(r["year"]) == year]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one pvoutput_yearly row for {year}")
    return matches[0]


# ---------------------------------------------------------------------------
# 2. Production: measured for 2026's window, estimated for 2024's.
# ---------------------------------------------------------------------------
def production_block():
    pv = _sa.load_pvoutput(DATA / "pvoutput_daily.csv")
    en = _sa.load_enphase(DATA / "enphase_daily_production.csv")

    period_dates = list(_date_range(P26_START, P26_END))
    missing_pv = [d for d in period_dates if d not in pv]
    if missing_pv:
        raise SystemExit(f"pvoutput_daily.csv missing days in the 2026 window: {missing_pv}")
    prod_2026_pv = sum(pv[d] for d in period_dates)
    prod_2026_en = sum(en[d] for d in period_dates if d in en)
    en_days_covered = sum(1 for d in period_dates if d in en)

    trailing_dates = list(_date_range(TRAILING_START, TRAILING_END))
    trailing_total_pv = sum(pv[d] for d in trailing_dates if d in pv)
    trailing_days_covered = sum(1 for d in trailing_dates if d in pv)

    # The fraction transferred to 2024 must represent 2024's OWN 32-day span,
    # not the 29-day 2026 comparison window (adversarial review pass 1,
    # finding 1: an earlier draft transferred a 29-day-based fraction
    # directly onto a 32-day bill total, understating the 2024 estimate by
    # roughly the missing 3 days' worth of production). Rather than picking a
    # DIFFERENT 32-day window (which would sample different, noisier days and
    # trade one error for another -- verified empirically: doing so swings the
    # production/consumption scale factors enough to push the decomposition's
    # endpoint errors to OPPOSITE signs, amplifying rather than shrinking the
    # error on the delta), this reuses the SAME measured 29 days and rescales
    # by the exact DAY-COUNT ratio: a per-day seasonal-rate index (this
    # window's average daily output over a typical day's output for the
    # year), applied to 2024's own average daily output for 32 days.
    daily_rate_2026 = prod_2026_pv / len(period_dates)
    daily_rate_trailing = trailing_total_pv / len(trailing_dates)
    seasonal_daily_rate_ratio = daily_rate_2026 / daily_rate_trailing

    # Cross-check: the same per-day ratio from pure clear-sky solar geometry
    # alone (no weather signal at all), over the SAME 29 measured days.
    period_clearsky = sum(clearsky_for_date(d) for d in period_dates)
    trailing_clearsky = sum(clearsky_for_date(d) for d in trailing_dates)
    clearsky_daily_rate_2026 = period_clearsky / len(period_dates)
    clearsky_daily_rate_trailing = trailing_clearsky / len(trailing_dates)
    seasonal_daily_rate_ratio_clearsky = clearsky_daily_rate_2026 / clearsky_daily_rate_trailing

    yearly_2024 = yearly_pvoutput_row(2024)
    yearly_2024_kwh = float(yearly_2024["kwh_generated"])
    yearly_2024_days = int(yearly_2024["days"])
    daily_rate_2024 = yearly_2024_kwh / yearly_2024_days
    period_2024_days = 32  # PERIOD_2024's own day count, bill-exact

    # Kept as the empirical basis's own label for backward-readable field
    # names below -- now a per-day RATIO, not a period-total fraction.
    seasonal_fraction = seasonal_daily_rate_ratio
    seasonal_fraction_clearsky = seasonal_daily_rate_ratio_clearsky
    prod_2024_est_empirical = seasonal_daily_rate_ratio * daily_rate_2024 * period_2024_days
    prod_2024_est_clearsky = seasonal_daily_rate_ratio_clearsky * daily_rate_2024 * period_2024_days

    return {
        "period_2026_measured_pvoutput_kwh": round(prod_2026_pv, 3),
        "period_2026_measured_enphase_kwh": round(prod_2026_en, 3),
        "period_2026_enphase_days_covered": en_days_covered,
        "period_2026_pvoutput_vs_enphase_pct_diff": round(
            (prod_2026_pv - prod_2026_en) / prod_2026_pv * 100, 2),
        "trailing_year_window": [str(TRAILING_START), str(TRAILING_END)],
        "trailing_year_total_pvoutput_kwh": round(trailing_total_pv, 3),
        "trailing_year_days_covered": trailing_days_covered,
        "period_2024_days": period_2024_days,
        "seasonal_fraction_empirical": round(seasonal_fraction, 5),
        "seasonal_fraction_clearsky_geometric": round(seasonal_fraction_clearsky, 5),
        "clearsky_geometric_note": (
            "Pure Haurwitz clear-sky solar geometry (day length/sun angle only, "
            "no cloud cover) predicts this window should carry a ~10% LARGER "
            "per-day share of the year's output than the empirical record "
            "shows -- consistent with San Diego's real, well-documented "
            "late-May/June 'June gloom' marine layer depressing actual output "
            "below what day-length alone would predict for this specific "
            "calendar window. This divergence is evidence FOR using the "
            "EMPIRICAL ratio (it captures the real regional cloud pattern) "
            "rather than evidence against it; the clear-sky figure is "
            "reported for transparency, not averaged in."),
        "period_2024_estimated_kwh_empirical_basis": round(prod_2024_est_empirical, 1),
        "period_2024_estimated_kwh_clearsky_basis": round(prod_2024_est_clearsky, 1),
        "yearly_2024_calendar_total_kwh": yearly_2024_kwh,
        "estimate_basis": (
            "ESTIMATED, not measured: no daily production record exists for "
            "2024 anywhere in this repo (pvoutput_daily.csv and "
            "enphase_daily_production.csv both start 2025-07-24). Estimated as "
            "a PER-DAY seasonal-rate index: (this 29-day 2026 window's own "
            "average daily output) / (the trailing year's own average daily "
            "output) -- a dimensionless ratio, computed from the SAME 29 "
            "measured days used for the 2026 figures above, not a different "
            "window (an earlier draft picked a differently-dated 32-day "
            "window to match 2024's day count and found that choice "
            "introduces MORE noise than it removes: shifting which specific "
            "days are sampled swings the ratio enough to flip the "
            "decomposition's two endpoint errors to opposite signs, which "
            "amplifies rather than shrinks the error on the resulting delta; "
            "adversarial review pass 1, finding 1, follow-up). That ratio is "
            "then applied to 2024's OWN average daily output (2024's exact "
            "calendar-year PVOutput total / 2024's own day count, "
            "data/pvoutput_yearly_2020-2025.csv) times PERIOD_2024's exact "
            "32-day count -- correctly scaling for day count without "
            "resampling different, noisier calendar days. This still assumes "
            "the SEASONAL RATE (including the real regional cloud pattern, "
            "not just day length -- see seasonal_fraction_clearsky_geometric, "
            "which diverges from the empirical ratio by ~10% for exactly that "
            "reason) is stable year to year, and cannot fully verify that "
            "assumption since actual cloud cover for May-June 2024 "
            "specifically is not recorded anywhere in this repo."),
        "limitation": (
            "NOT DETERMINED to better precision than this transfer estimate. "
            "What would settle it: a daily PVOutput/Enphase production export "
            "covering May-June 2024, or an irradiance dataset (e.g. NREL "
            "NSRDB) for the site coordinates spanning 2024."),
    }


# ---------------------------------------------------------------------------
# 3. Gross household load: measured (CT, 2026), estimated (bill identity, 2024).
# ---------------------------------------------------------------------------
def load_ct_hourly():
    """Whole-home CT hourly series spanning 2025-01-01..2026-12-30, exactly
    reproducing deep_analyses.py's own samA/samB concatenation (same index
    construction, same source files) -- reused pattern, not reinvented."""
    b = pd.read_csv(SAM_B_CSV).iloc[:, 0].values  # 2025 full year
    a = pd.read_csv(SAM_A_CSV).iloc[:, 0].values  # 2026 partial year
    idx25 = pd.date_range("2025-01-01", periods=8760, freq="h")
    idx26 = pd.date_range("2026-01-01", periods=8760, freq="h")
    return pd.concat([pd.Series(b, index=idx25), pd.Series(a, index=idx26)])


def load_green_button_period(start, end_exclusive):
    """15-minute Green Button rows in [start, end_exclusive). Consumption =
    gross IMPORT, Generation = gross EXPORT (never gross load -- CLAUDE.md,
    confirmed by issue #15)."""
    df = pd.read_csv(USAGE_CSV, skiprows=13)
    df.columns = [c.strip() for c in df.columns]
    df["dt"] = pd.to_datetime(df["Date"] + " " + df["Start Time"], format="%m/%d/%Y %I:%M %p")
    for c in ["Consumption", "Generation"]:
        df[c] = pd.to_numeric(df[c])
    d = df[(df.dt >= start) & (df.dt < end_exclusive)].copy().reset_index(drop=True)
    return d


def consumption_block(bill, prod):
    ct = load_ct_hourly()
    period_ct = ct[str(P26_START):f"{P26_END} 23:00"]
    cons_2026_ct = float(period_ct.sum())

    prod_2026 = prod["period_2026_measured_pvoutput_kwh"]
    identity_check = bill["period_2026"]["net_kwh"] + prod_2026
    agreement_pct = abs(cons_2026_ct - identity_check) / cons_2026_ct * 100

    prod_2024_est = prod["period_2024_estimated_kwh_empirical_basis"]
    cons_2024_est = bill["period_2024"]["net_kwh"] + prod_2024_est

    return {
        "period_2026_ct_measured_kwh": round(cons_2026_ct, 1),
        "period_2026_ct_source": "whole-home SAM 8760 CT metering (samA.csv/samB.csv)",
        "period_2026_identity_check_kwh": round(identity_check, 1),
        "period_2026_identity_check_basis": (
            "Net_kwh (exact, bill) + measured PVOutput production for the "
            "window -- an independent cross-check of the CT measurement "
            "against the bill and a separate production meter."),
        "period_2026_ct_vs_identity_agreement_pct_diff": round(agreement_pct, 2),
        "period_2024_estimated_kwh": round(cons_2024_est, 1),
        "period_2024_basis": (
            "ESTIMATED: Net_kwh_2024 (346.0, exact bill figure) + the "
            "ESTIMATED 2024 production for this window (see production "
            "block). No CT/SAM-8760 archive exists before 2025 "
            "(private/1-raw-data/enphase_sam8760_2025.csv is the earliest), "
            "so a directly MEASURED gross load figure for 2024 is NOT "
            "DETERMINED in this repo."),
        "limitation": (
            "AC3's 'trended over the same window' is only fully achievable "
            "for the 2026 side (measured). The 2024 side is bill-identity-"
            "estimated, not measured. What would settle it: a 2024 SAM 8760 "
            "whole-home export, if one exists in the monitoring account's "
            "history but was not pulled into this repo."),
    }


# ---------------------------------------------------------------------------
# 4. Hourly reconstruction + validation, then the counterfactual decomposition.
# ---------------------------------------------------------------------------
def hourly_reconstruction(bill):
    ct = load_ct_hourly()
    ct_period = ct[str(P26_START):f"{P26_END} 23:00"]

    gb = load_green_button_period(pd.Timestamp(P26_START), pd.Timestamp(P26_END) + pd.Timedelta(days=1))
    gb["hourfloor"] = gb["dt"].dt.floor("h")
    imp_h = gb.groupby("hourfloor")["Consumption"].sum().reindex(ct_period.index, fill_value=0.0)
    exp_h = gb.groupby("hourfloor")["Generation"].sum().reindex(ct_period.index, fill_value=0.0)

    import_sum = float(imp_h.sum())
    export_sum = float(exp_h.sum())
    prod_h_raw = ct_period.values - imp_h.values + exp_h.values
    prod_h = np.clip(prod_h_raw, 0.0, None)
    n_negative = int((prod_h_raw < 0).sum())
    min_negative = float(prod_h_raw.min())
    derived_prod_sum = float(prod_h.sum())

    gross_bill = bill["period_2026"]["gross_kwh"]
    export_bill = bill["period_2026"]["export_kwh"]

    return {
        "ct_index": ct_period.index,
        "ct_values": ct_period.values,
        "production_hourly_derived": prod_h,
        "validation": {
            "green_button_import_sum_kwh": round(import_sum, 1),
            "vs_bill_gross_kwh": gross_bill,
            "import_pct_diff": round((import_sum - gross_bill) / gross_bill * 100, 2),
            "green_button_export_sum_kwh": round(export_sum, 1),
            "vs_bill_export_kwh": export_bill,
            "export_pct_diff": round((export_sum - export_bill) / export_bill * 100, 2),
            "derived_production_sum_kwh": round(derived_prod_sum, 1),
            "hours_with_negative_raw_derived_production": n_negative,
            "min_raw_derived_production_kwh": round(min_negative, 3),
            "note": (
                "Production_hourly = CT_load - GreenButton_import + "
                "GreenButton_export, i.e. gross load reconciled against "
                "import/export at hourly resolution -- an independent, "
                "hourly-resolution production estimate built entirely from "
                "meter data, no PV monitoring feed involved. A handful of "
                "small negative values (meter-timing/rounding noise between "
                "the CT and Green Button meters, clipped to 0 before use) is "
                "expected and reported rather than hidden."),
        },
    }


def template_hourly_vectors():
    """Real 2026 hourly CT load and meter-derived production for
    TEMPLATE_START..TEMPLATE_END (May 25-June 25, 2026 -- 32 real days,
    matching PERIOD_2024's own day count and calendar span exactly). This is
    the shared REAL base both years' back-solves in decomposition_block
    scale from (Codex review pass 2, finding 1) -- unlike
    hourly_reconstruction()'s 29-day P26_START..P26_END window (kept
    unchanged, still used for its own independent cross-meter validation),
    no vector here is ever asked to represent a day count it wasn't actually
    measured at."""
    ct = load_ct_hourly()
    ct_period = ct[str(TEMPLATE_START):f"{TEMPLATE_END} 23:00"]

    gb = load_green_button_period(pd.Timestamp(TEMPLATE_START), pd.Timestamp(TEMPLATE_END) + pd.Timedelta(days=1))
    gb["hourfloor"] = gb["dt"].dt.floor("h")
    imp_h = gb.groupby("hourfloor")["Consumption"].sum().reindex(ct_period.index, fill_value=0.0)
    exp_h = gb.groupby("hourfloor")["Generation"].sum().reindex(ct_period.index, fill_value=0.0)
    prod_h_raw = ct_period.values - imp_h.values + exp_h.values
    prod_h = np.clip(prod_h_raw, 0.0, None)
    return ct_period.values, prod_h, ct_period.index


def _shapley_two_factor_vectors(ct_2026, prod_2026, ct_2024, prod_2024, correction=1.0):
    """g(load, prod) = correction * sum(max(0, load - prod)) over the hourly
    series, evaluated at all 4 corners of {ct_2026, ct_2024} x {prod_2026,
    prod_2024} -- FULL HOURLY VECTORS, not scalar scale factors, so this
    works identically whether the 2024 counterfactual came from a uniform
    multiplicative scale (ct_2024 = c_scale * ct_2026) or a per-hour
    ADDITIVE shape change (ct_2024 = ct_2026 - ev_hourly*frac). `correction`
    rescales for a KNOWN, disclosed, equally-applied artifact (see
    decomposition_block: the CT archive's native hourly resolution smooths
    some real sub-hourly import/export crossovers a finer-resolution meter
    would catch, understating simulated gross import by a small, consistent
    amount) -- applying it uniformly to every corner does not change WHICH
    corner explains WHAT, only removes a shared, understood bias before
    decomposing. Returns the four corner evaluations and the exact
    order-independent (Shapley/telescoping) decomposition of g(2026,2026)-
    g(2024,2024) into a consumption term and a production term that sum to
    it EXACTLY -- in GROSS-IMPORT kWh, the same units on both sides, always
    (adversarial review pass 3, finding 1: an earlier draft compared this
    scenario's RAW production-energy delta, prod_2026*(1-p), against the
    baseline's Shapley gross-import CONTRIBUTION -- different quantities
    entirely, since most of a production change is absorbed by export/
    self-consumption rather than changing gross import 1:1; every caller
    must go through this shared function so the two are always comparable)."""
    def g(load, prod):
        return correction * float(np.maximum(0.0, load - prod).sum())

    f11 = g(ct_2026, prod_2026)    # both at the 2026-actual level
    f01 = g(ct_2024, prod_2026)    # consumption at 2024 level, production at 2026 level
    f10 = g(ct_2026, prod_2024)    # consumption at 2026 level, production at 2024 level
    f00 = g(ct_2024, prod_2024)    # both at the 2024-estimated level

    consumption_term = 0.5 * ((f10 - f00) + (f11 - f01))
    production_term = 0.5 * ((f01 - f00) + (f11 - f10))
    return {
        "import_2026_actual_sim_kwh": f11,
        "import_2024_counterfactual_sim_kwh": f00,
        "import_consumption_only_shift_sim_kwh": f01,
        "import_production_only_shift_sim_kwh": f10,
        "consumption_term_kwh": consumption_term,
        "production_term_kwh": production_term,
    }


def _solve_year_scales(ct_template, prod_template, correction, net_exact, gross_exact):
    """Back-solve (consumption_scale, production_scale) for ONE year,
    relative to the shared REAL 32-day template (TEMPLATE_START..
    TEMPLATE_END -- template_hourly_vectors()), rather than assuming either
    year simply equals the template. consumption_scale is PINNED by an exact
    accounting identity once production_scale is chosen (year's gross
    consumption = year's bill-exact net_kwh + production_scale * the
    template's own total production -- net_exact is a bill fact, not an
    estimate), so production_scale is the ONE free parameter, back-solved to
    the value that makes the simulated corner match THIS year's bill
    exactly.

    Codex review pass 2, finding 1: an earlier draft treated 2026 as
    automatically "the template, scale=1" and only back-solved 2024,
    implicitly assuming the real 29-day 2026 billed window and a 32-day
    period were the SAME shape -- exactly the compression artifact this
    symmetric construction avoids. BOTH years are now solved the same way,
    relative to the SAME neutral, correctly-sized 32-day real reference, so
    no vector is ever asked to represent a day count it wasn't measured at,
    and neither year gets special-cased as "the exact one."""
    from scipy.optimize import brentq

    template_cons_total = float(ct_template.sum())
    template_prod_total = float(prod_template.sum())

    def c_of_p(p):
        return (net_exact + p * template_prod_total) / template_cons_total

    def residual(p):
        c = c_of_p(p)
        sim = correction * float(np.maximum(0.0, c * ct_template - p * prod_template).sum())
        return sim - gross_exact

    p_star = brentq(residual, 0.2, 3.0, xtol=1e-10)
    return c_of_p(p_star), p_star


def decomposition_block(bill, prod, cons, hourly):
    production_scale_estimated = (prod["period_2024_estimated_kwh_empirical_basis"]
                                  / prod["period_2026_measured_pvoutput_kwh"])

    gross_2024 = bill["period_2024"]["gross_kwh"]
    gross_2026 = bill["period_2026"]["gross_kwh"]
    observed_delta = gross_2026 - gross_2024

    # hourly (the 29-day P26 reconstruction) is kept ONLY for its own
    # independent cross-meter validation (hourly_reconstruction's own
    # docstring) and to calibrate hourly_resolution_correction below -- a
    # meter-RESOLUTION artifact, not day-count-dependent, so calibrating it
    # from the real, zero-estimation 29-day corner remains valid. The
    # decomposition itself uses the REAL 32-day TEMPLATE (Codex review pass
    # 2, finding 1), never a vector scaled to represent a day count it
    # wasn't measured at.
    ct_values = hourly["ct_values"]
    prod_values = hourly["production_hourly_derived"]
    f11_raw = float(np.maximum(0.0, 1.0 * ct_values - 1.0 * prod_values).sum())
    hourly_resolution_correction = gross_2026 / f11_raw

    ct_template, prod_template, _template_index = template_hourly_vectors()

    consumption_scale_2026, production_scale_2026 = _solve_year_scales(
        ct_template, prod_template, hourly_resolution_correction,
        bill["period_2026"]["net_kwh"], gross_2026)
    consumption_scale_2024, production_scale_2024 = _solve_year_scales(
        ct_template, prod_template, hourly_resolution_correction,
        bill["period_2024"]["net_kwh"], gross_2024)

    sh = _shapley_two_factor_vectors(
        consumption_scale_2026 * ct_template, production_scale_2026 * prod_template,
        consumption_scale_2024 * ct_template, production_scale_2024 * prod_template,
        correction=hourly_resolution_correction)

    decomposed_sum = sh["consumption_term_kwh"] + sh["production_term_kwh"]
    pct_error = abs(decomposed_sum - observed_delta) / abs(observed_delta) * 100

    # The 2024-relative-to-2026 RATIOS, for continuity with the report's
    # existing field names and the independent weather-based cross-check
    # below (also a 2024-over-2026 ratio).
    production_scale_backsolved = production_scale_2024 / production_scale_2026
    consumption_scale_backsolved = consumption_scale_2024 / consumption_scale_2026

    # Cross-check: does the back-solved production level (pinned by bill-exact
    # accounting) agree with the INDEPENDENT weather/seasonal-rate-based
    # estimate (AC1)? This is the real test of whether the normalization
    # approach is physically plausible -- not whether it happens to reproduce
    # the bill exactly, which the back-solve now guarantees by construction.
    cross_check_pct_diff = ((production_scale_backsolved - production_scale_estimated)
                            / production_scale_estimated * 100)

    validation_2026_pct_error = ((sh["import_2026_actual_sim_kwh"] - gross_2026)
                                 / gross_2026 * 100)
    validation_2024_pct_error = ((sh["import_2024_counterfactual_sim_kwh"] - gross_2024)
                                 / gross_2024 * 100)

    return {
        "method": (
            "Hourly counterfactual against a SHARED REAL 32-day template "
            "(TEMPLATE_START..TEMPLATE_END, May 25-June 25 2026 -- matching "
            "PERIOD_2024's own day count and calendar span exactly; Codex "
            "review pass 2, finding 1: an earlier draft scaled the ACTUAL "
            "29-day 2026 billed window's hourly vector to represent a 32-day "
            "total, compressing 3 missing days into 29 real hours and "
            "distorting the nonlinear max(0, load-production) overlap the "
            "whole method depends on). BOTH years -- not just 2024 -- are "
            "now back-solved relative to this SAME neutral template: for "
            "each year, consumption is PINNED by an exact accounting "
            "identity (year's gross consumption = year's bill-exact net_kwh "
            "+ production_scale * the template's own total production), so "
            "production_scale is the one free parameter per year, solved so "
            "the simulated corner matches THAT year's bill exactly. Neither "
            "year is special-cased as 'the exact one' -- the 2024-relative-"
            "to-2026 ratio is what the report calls production_scale_2024_"
            "over_2026. The whole simulation is corrected by a small factor "
            "for the CT archive's hourly (not 15-minute) resolution "
            "(hourly_resolution_correction, calibrated from the REAL, "
            "zero-estimation 29-day 2026 corner, a meter-resolution "
            "artifact independent of which specific days are used). The "
            "INDEPENDENT weather/seasonal-rate-based production estimate "
            "(AC1, production_block) is reported separately for the AC1 "
            "requirement, not as an input the decomposition's accuracy "
            "depends on -- and NOT as a numeric cross-check on the solved "
            "ratio (Codex review pass 3, finding 1: that ratio is fit "
            "against unequal-length bill targets and is not a day-count-"
            "clean rate quantity; see production_scale_backsolved_not_a_"
            "clean_rate_comparison for why). A Shapley/telescoping "
            "two-factor decomposition then splits the corrected 2026-to-2024 "
            "change into a consumption term and a production term that sum "
            "to it EXACTLY by construction. See TECHNICAL.md 3.26."),
        "hourly_resolution_correction_factor": round(hourly_resolution_correction, 5),
        "hourly_resolution_correction_basis": (
            "The whole-home CT archive (SAM 8760) resolves at hourly "
            "granularity, coarser than the Green Button meter's 15-minute "
            "resolution the bill's own gross_kwh is computed from. Averaging "
            "sub-hourly import/export crossovers before taking max(0, "
            "load-production) understates true gross import by a small, "
            "systematic amount -- confirmed at the c=p=1 corner (REAL 2026 "
            "data, zero estimation involved): the raw hourly simulation "
            "undershoots the bill's own 2026 gross-import figure by "
            f"{round((1 - f11_raw / gross_2026) * 100, 2)}%, a "
            "resolution artifact, not a modeling error. Applied identically "
            "to every corner (a shared bias, not a per-corner adjustment)."),
        "production_scale_estimated_from_weather": round(production_scale_estimated, 5),
        "production_scale_backsolved_from_bill": round(production_scale_backsolved, 5),
        "production_scale_backsolved_not_a_clean_rate_comparison": (
            "Codex review pass 3, finding 1: production_scale_2026_vs_template "
            "and production_scale_2024_vs_template are each fit so a "
            "NONLINEAR gross-import equation (sum(max(0, load-production))) "
            "matches that year's OWN bill total, using the SAME 32-day "
            "template shape -- but 2026's bill target is only 29 days while "
            "2024's is the full 32. Fitting a 32-day-shaped simulation to a "
            "shorter 29-day target mechanically pulls production_scale_2026 "
            "toward roughly (29/32 =~ 0.906) independent of any genuine "
            "production change (confirmed: production_scale_2026_vs_template "
            "= 0.917, suspiciously close to that ratio) -- 2024's own scale "
            "is NOT diluted this way, since its target already matches the "
            "template's 32-day length exactly. Their RATIO (production_"
            "scale_backsolved_from_bill, reported above for full "
            "transparency and reproducibility) therefore conflates a real "
            "day-count-fitting artifact with genuine year-over-year "
            "production change and should NOT be read as an interpretable "
            "production-rate comparison, or cross-checked numerically "
            "against production_scale_estimated_from_weather (which IS "
            "already day-count-normalized, via a different, rate-based "
            "method) -- an earlier draft did exactly that and reported a "
            "specific 'agreement' percentage this contamination makes "
            "meaningless. The Shapley DECOMPOSITION itself (consumption_"
            "term_kwh, production_term_kwh below) is NOT affected by this: "
            "it is computed directly from the four well-defined corners, "
            "each exact by construction against its own bill, and does not "
            "depend on the scale ratio being independently interpretable."),
        "production_scale_cross_check_pct_diff_DO_NOT_INTERPRET": round(cross_check_pct_diff, 2),
        "production_scale_cross_check_note": (
            "This field is diagnostic-only, not a validated cross-check "
            "(see production_scale_backsolved_not_a_clean_rate_comparison "
            "above): the raw percentage difference "
            f"({abs(cross_check_pct_diff):.1f}%) is retained in the artifact "
            "for full transparency, but neither this specific number nor "
            "its sign should be read as evidence of agreement or "
            "disagreement between the two estimation methods, because the "
            "back-solved value is not a day-count-clean rate quantity to "
            "begin with."),
        "consumption_scale_2026_vs_template": round(consumption_scale_2026, 5),
        "production_scale_2026_vs_template": round(production_scale_2026, 5),
        "consumption_scale_2024_vs_template": round(consumption_scale_2024, 5),
        "production_scale_2024_vs_template": round(production_scale_2024, 5),
        "consumption_scale_2024_over_2026": round(consumption_scale_backsolved, 5),
        "production_scale_2024_over_2026": round(production_scale_backsolved, 5),
        "import_2026_actual_sim_kwh": round(sh["import_2026_actual_sim_kwh"], 1),
        "import_2026_actual_bill_kwh": gross_2026,
        "import_2026_sim_vs_bill_pct_error": round(validation_2026_pct_error, 2),
        "import_2024_counterfactual_sim_kwh": round(sh["import_2024_counterfactual_sim_kwh"], 1),
        "import_2024_actual_bill_kwh": gross_2024,
        "import_2024_sim_vs_bill_pct_error": round(validation_2024_pct_error, 2),
        "consumption_term_kwh": round(sh["consumption_term_kwh"], 1),
        "production_term_kwh": round(sh["production_term_kwh"], 1),
        "decomposed_sum_kwh": round(decomposed_sum, 1),
        "observed_delta_gross_kwh": observed_delta,
        "decomposition_pct_error_vs_observed": round(pct_error, 2),
        "within_5_pct_tolerance": bool(pct_error < 5.0),
        "verdict": (
            "Under this scenario's modeling assumption -- 2024 shared 2026's "
            "diurnal SHAPE exactly, scaled uniformly -- the gross-import "
            "increase is overwhelmingly a CONSUMPTION story: the consumption "
            "term accounts for essentially all of the observed change. "
            "IMPORTANT: this specific split is NOT uniquely identified by "
            "the available data (adversarial review pass 2, finding 1) -- "
            "see decomposition_identifiability_robustness_check for how much "
            "the production term can move under a materially different, "
            "still-defensible shape assumption. Read this scenario's exact "
            "numbers as ONE plausible decomposition consistent with both "
            "bill totals, not as the uniquely correct physical split."
            if abs(sh["production_term_kwh"]) < abs(sh["consumption_term_kwh"]) * 0.25
            else "See consumption_term_kwh vs production_term_kwh for the split."),
    }


def _solve_year_scales_ev_shape(ct_template, prod_template, ev_template, correction,
                                net_exact, gross_exact):
    """Same well-posed, one-free-parameter construction as
    _solve_year_scales, but concentrating the required consumption
    adjustment (from the template's own total) in EV-charging hours
    specifically, rather than scaling every hour uniformly. Returns
    (reduction_frac, production_scale) for the year; reduction_frac can be
    negative (this year needed slightly MORE EV energy than the template's
    own level, not less) -- physically sensible near zero, only actually
    "not realizable" if its magnitude exceeds 100% of the template's own EV
    total (checked by the caller before trusting the root)."""
    from scipy.optimize import brentq

    template_cons_total = float(ct_template.sum())
    template_prod_total = float(prod_template.sum())
    ev_template_total = float(ev_template.sum())

    def reduction_frac(p):
        cons_y = net_exact + p * template_prod_total
        total_reduction = template_cons_total - cons_y
        return total_reduction / ev_template_total

    def residual(p):
        frac = reduction_frac(p)
        ct_shape = ct_template - ev_template * frac
        sim = correction * float(np.maximum(0.0, ct_shape - p * prod_template).sum())
        return sim - gross_exact

    p_star = brentq(residual, 0.2, 3.0, xtol=1e-10)
    return reduction_frac(p_star), p_star


def identifiability_robustness_check(bill, prod, cons, hourly, decomposition):
    """Adversarial review pass 2, finding 1: back-solving production_scale to
    hit each year's bill exactly makes the endpoint agreement TAUTOLOGICAL,
    not a validation -- with no 2024 hourly load/production shape available,
    the accounting identity (net_kwh + production = consumption) pins
    production_scale UNIQUELY only under decomposition_block's own modeling
    choice (each year's consumption shares the shared 32-day TEMPLATE's
    diurnal SHAPE, scaled uniformly). A DIFFERENT, equally defensible shape
    assumption for how consumption differed from the template could
    back-solve to a materially different production_scale, changing the
    reported split, while still satisfying the exact same bill totals.

    This function re-solves BOTH years (the same symmetric construction as
    decomposition_block's own _solve_year_scales, Codex review pass 2,
    finding 1) under ONE genuinely different, data-grounded shape
    assumption -- ALL of a year's consumption difference from the template
    concentrated in EV-charging hours specifically (detect_sessions already
    isolates which hours those are, over the SAME 32-day template window) --
    rather than decomposition_block's own uniform-proportional scaling. If
    the two assumptions' resulting production/consumption split agree
    closely, the "consumption story" conclusion is robust to this
    particular identifiability concern, not merely an artifact of one
    modeling choice; if they diverge sharply, that is reported plainly
    rather than hidden behind the headline split."""
    import behavior_rebuild as br

    ct_template, prod_template, template_index = template_hourly_vectors()
    gb = load_green_button_period(pd.Timestamp(TEMPLATE_START), pd.Timestamp(TEMPLATE_END) + pd.Timedelta(days=1))
    ev_kwh_15min, _sessions = br.detect_sessions(gb)
    ev_series = pd.Series(ev_kwh_15min, index=gb["dt"])
    ev_template = ev_series.groupby(ev_series.index.floor("h")).sum().reindex(template_index, fill_value=0.0).values
    ev_template_total = float(ev_template.sum())

    correction = decomposition["hourly_resolution_correction_factor"]
    net_2026, gross_2026 = bill["period_2026"]["net_kwh"], bill["period_2026"]["gross_kwh"]
    net_2024, gross_2024 = bill["period_2024"]["net_kwh"], bill["period_2024"]["gross_kwh"]

    # Confirm the EV-concentrated assumption is even physically realizable
    # for BOTH years (the required reduction must fit within the measured EV
    # energy itself) before trusting either root the solver might otherwise
    # find outside a sane range.
    template_cons_total = float(ct_template.sum())
    template_prod_total = float(prod_template.sum())

    def _frac_at(net_exact, p):
        cons_y = net_exact + p * template_prod_total
        return (template_cons_total - cons_y) / ev_template_total

    frac_2026_check = _frac_at(net_2026, decomposition["production_scale_2026_vs_template"])
    frac_2024_check = _frac_at(net_2024, decomposition["production_scale_2024_vs_template"])
    realizable = all(-1.0 <= f <= 1.0 for f in (frac_2026_check, frac_2024_check))

    if not realizable:
        return {
            "scenario": "EV-charging-concentrated consumption decline",
            "realizable": False,
            "note": (
                f"At the baseline scenario's own solved production scales, "
                f"the EV-concentrated assumption would require adjusting EV "
                f"charging by {frac_2026_check * 100:.0f}% (2026) / "
                f"{frac_2024_check * 100:.0f}% (2024) of the template's own "
                "EV total, outside the physically meaningful +/-100% range -- "
                "this alternative shape cannot explain the observed change "
                "on its own and is not evaluated further."),
        }

    frac_2026, p_2026_ev = _solve_year_scales_ev_shape(
        ct_template, prod_template, ev_template, correction, net_2026, gross_2026)
    frac_2024, p_2024_ev = _solve_year_scales_ev_shape(
        ct_template, prod_template, ev_template, correction, net_2024, gross_2024)

    # Run the SAME 4-corner Shapley decomposition the baseline scenario uses
    # (adversarial review pass 3, finding 1: an earlier draft compared this
    # scenario's raw production-ENERGY delta against the baseline's Shapley
    # gross-IMPORT contribution -- different quantities, since most of a
    # production change is absorbed by export/self-consumption rather than
    # changing gross import 1:1. Both scenarios now go through
    # _shapley_two_factor_vectors so their terms are directly comparable, in
    # the same units, always.)
    sh_ev = _shapley_two_factor_vectors(
        ct_template - ev_template * frac_2026, p_2026_ev * prod_template,
        ct_template - ev_template * frac_2024, p_2024_ev * prod_template,
        correction)
    consumption_term_ev = round(sh_ev["consumption_term_kwh"], 1)
    production_term_ev = round(sh_ev["production_term_kwh"], 1)
    decomposed_sum_ev = round(consumption_term_ev + production_term_ev, 1)
    production_scale_ratio_ev = p_2024_ev / p_2026_ev

    baseline_production_term = decomposition["production_term_kwh"]
    baseline_consumption_term = decomposition["consumption_term_kwh"]

    agree = abs(production_term_ev - baseline_production_term) < 0.25 * abs(baseline_consumption_term)

    return {
        "scenario": "EV-charging-concentrated consumption decline",
        "realizable": True,
        "assumption": (
            "Instead of decomposition_block's uniform-proportional scaling "
            "of every hour, each year's consumption difference from the "
            "shared 32-day template is assumed concentrated in EV-charging "
            "hours specifically (per-hour EV kWh from behavior_rebuild."
            "detect_sessions, reused read-only, over the SAME template "
            "window), with every non-EV hour held at the template's own "
            "level. Both years are solved symmetrically, the same way "
            "decomposition_block's own baseline scenario is. The SAME "
            "4-corner Shapley decomposition as the baseline scenario "
            "(_shapley_two_factor_vectors) is run against this alternative "
            "consumption shape, so both scenarios' terms are directly "
            "comparable gross-import contributions, not a production-energy "
            "delta compared against a gross-import contribution."),
        "ev_template_total_kwh": round(ev_template_total, 1),
        "implied_ev_reduction_pct_2026_vs_template": round(frac_2026 * 100, 1),
        "implied_ev_reduction_pct_2024_vs_template": round(frac_2024 * 100, 1),
        "production_scale_backsolved_this_scenario": round(production_scale_ratio_ev, 5),
        "production_scale_backsolved_baseline_scenario": decomposition["production_scale_2024_over_2026"],
        "consumption_term_kwh_this_scenario": consumption_term_ev,
        "production_term_kwh_this_scenario": production_term_ev,
        "decomposed_sum_kwh_this_scenario": decomposed_sum_ev,
        "consumption_term_kwh_baseline_scenario": baseline_consumption_term,
        "production_term_kwh_baseline_scenario": baseline_production_term,
        "conclusion_robust_to_this_alternative_shape": agree,
        "note": (
            f"The two shape assumptions' Shapley production terms agree "
            f"closely (production term {production_term_ev} kWh here vs "
            f"{baseline_production_term} kWh under uniform scaling) -- the "
            "'consumption story, not a production story' conclusion does "
            "not depend on the specific uniform-scaling assumption; it "
            "holds under this materially different, data-grounded "
            "alternative too."
            if agree else
            f"The two shape assumptions' Shapley production terms diverge "
            f"meaningfully ({production_term_ev} kWh here vs "
            f"{baseline_production_term} kWh under uniform scaling) -- the "
            "headline split is sensitive to which diurnal-shape assumption "
            "is used, and should be read as ONE plausible decomposition "
            "rather than a uniquely identified physical split. Both "
            "scenarios still attribute the dominant share of the change to "
            "consumption over production, but the exact magnitude is "
            "genuinely underdetermined by the data available."
        ),
    }


# ---------------------------------------------------------------------------
# 5. EV attribution: real detection, honestly bounded (AC5).
# ---------------------------------------------------------------------------
def ev_block(cons):
    import behavior_rebuild as br  # noqa: E402 (needs household.yaml at import time)

    gb = load_green_button_period(pd.Timestamp(P26_START), pd.Timestamp(P26_END) + pd.Timedelta(days=1))
    ev_kwh, sessions = br.detect_sessions(gb)
    ev_total = float(ev_kwh.sum())

    cons_2026 = cons["period_2026_ct_measured_kwh"]
    observed_increase_ct = cons_2026 - cons["period_2024_estimated_kwh"]
    upper_bound_pct = min(100.0, ev_total / observed_increase_ct * 100) if observed_increase_ct > 0 else None

    return {
        "period_2026_ev_kwh_detected": round(ev_total, 1),
        "n_sessions_detected": len(sessions),
        "detection_method": "analysis/behavior_rebuild.py detect_sessions(), reused read-only",
        "pct_of_period_2026_consumption": round(ev_total / cons_2026 * 100, 1),
        "upper_bound_pct_of_consumption_increase": (
            None if upper_bound_pct is None else round(upper_bound_pct, 1)),
        "upper_bound_basis": (
            "Both vehicles (Tesla Model 3 since Aug 2021, Model Y since Jun "
            "2022) were already owned in the 2024 comparison period, so EV "
            "charging existed in BOTH windows -- this is an upper bound "
            "(assumes zero EV charging in the 2024 window, which is known "
            "false), not a point estimate."),
        "true_share_of_increase": "NOT DETERMINED",
        "caveat": (
            "The private Green Button archive staged in this environment "
            "covers 2025-06-27..2026-07-26 only (SDG&E's ~13-month export "
            "window) -- it does not reach back to May/June 2024, so "
            "detect_sessions cannot run on the 2024 comparison period at "
            "all. Because measured EV charging in the 2026 window "
            f"({round(ev_total, 0)} kWh) is itself larger than the entire "
            "observed consumption increase, the true EV share of the "
            "INCREASE (as opposed to of 2026's total consumption) cannot be "
            "bounded usefully tighter than 0-100% from data in this repo. "
            "What would settle it: a Green Button export or Tesla Charge "
            "Stats history covering May-June 2024."),
    }


# ---------------------------------------------------------------------------
# 6. Degradation trend: artifact-backed, reconciled against the one claim in
#    index.html section 9 that is still hand-reasoned rather than derived from
#    this artifact -- the "best-estimate ~0.5-1.0%/yr" verdict (AC2, AC6).
# ---------------------------------------------------------------------------
def _theil_sen(x, y):
    slopes = []
    n = len(x)
    for i in range(n):
        for j in range(i + 1, n):
            slopes.append((y[j] - y[i]) / (x[j] - x[i]))
    slopes.sort()
    m = len(slopes)
    return slopes[m // 2] if m % 2 else 0.5 * (slopes[m // 2 - 1] + slopes[m // 2])


def degradation_block():
    years = list(range(2021, 2026))
    rows = {y: yearly_pvoutput_row(y) for y in years}
    eff = {y: float(rows[y]["avg_eff_kwh_per_kw_day"]) for y in years}
    x = np.array(years, dtype=float)
    y = np.array([eff[yr] for yr in years], dtype=float)
    mean_y = float(y.mean())

    slope, _ = np.polyfit(x, y, 1)
    ols_pct_per_yr = slope / mean_y * 100
    cagr_pct_per_yr = ((eff[2025] / eff[2021]) ** (1 / 4) - 1) * 100
    ts_slope = _theil_sen(list(x), list(y))
    ts_pct_per_yr = ts_slope / mean_y * 100
    total_change_pct = (eff[2025] - eff[2021]) / eff[2021] * 100

    # Clear-sky annual insolation totals, 2021-2025 -- pure solar geometry,
    # no weather. If these barely move year to year, geometry cannot explain
    # the observed efficiency swing, which is the point of computing them.
    clearsky_annual = {}
    for yr in years:
        days = 366 if (yr % 4 == 0 and (yr % 100 != 0 or yr % 400 == 0)) else 365
        clearsky_annual[yr] = round(sum(CLEARSKY_DOY[d] for d in range(1, days + 1)), 2)
    clearsky_spread_pct = ((max(clearsky_annual.values()) - min(clearsky_annual.values()))
                          / min(clearsky_annual.values()) * 100)

    soiling = json.loads((DATA / "soiling_results.json").read_text())
    sanity = soiling.get("sanity_check_2024_cleaning", {})
    single_event_swing_pct = sanity.get("known_cleaning_gain_pct")

    peak_to_trough_pct = (max(eff.values()) - min(eff.values())) / min(eff.values()) * 100

    # What the three fits establish BETWEEN THEM -- the only agreement claim
    # left here that is not circular (see the reconciliation block's comment).
    # Direction is read off the figures rather than assumed: an array that
    # gained over the window, or a set of fits that straddle zero, each gets
    # its own sentence instead of inheriting a decline word.
    naive = [ols_pct_per_yr, cagr_pct_per_yr, ts_pct_per_yr]
    naive_spread_pp = max(naive) - min(naive)
    if max(naive) < 0:
        naive_direction = ("all three fits agree the size-normalized output "
                           "DECLINED across 2021-2025")
    elif min(naive) > 0:
        naive_direction = ("all three fits agree the size-normalized output "
                           "ROSE across 2021-2025")
    else:
        naive_direction = ("the three fits do NOT agree on a direction across "
                           "2021-2025 -- their span straddles zero")

    return {
        "annual_efficiency_kwh_per_kw_day": eff,
        "ols_pct_per_yr": round(ols_pct_per_yr, 3),
        "cagr_pct_per_yr": round(cagr_pct_per_yr, 3),
        "theil_sen_pct_per_yr": round(ts_pct_per_yr, 3),
        "total_change_pct_2021_2025": round(total_change_pct, 2),
        "peak_to_trough_pct_2021_2025": round(peak_to_trough_pct, 2),
        "clearsky_annual_insolation_kwh_per_m2": clearsky_annual,
        "clearsky_annual_spread_pct": round(clearsky_spread_pct, 3),
        "clearsky_note": (
            "Clear-sky annual insolation varies only ~%.2f%% year to year "
            "(leap-day effect only) -- geometry cannot explain the observed "
            "%.1f%% peak-to-trough swing in avg_eff_kwh_per_kw_day (2022 "
            "4.839 -> 2023 4.245), so that swing must be real weather (cloud "
            "cover) and/or soiling, which this repo cannot separate for "
            "years before 2025 (no daily weather or production record "
            "exists before 2025-07-24)." % (clearsky_spread_pct, peak_to_trough_pct)),
        "single_event_soiling_swing_pct": single_event_swing_pct,
        "single_event_soiling_basis": (
            "data/soiling_results.json -> sanity_check_2024_cleaning: an "
            "11.8% production gain measured after the 2024-08-12 cleaning, "
            "following ~134 dry days (~4.4 months) with no rain -- a "
            "VALIDATED (not modeled) single-event swing, reused read-only "
            "from analysis/soiling_analysis.py, never recomputed here."),
        # WHICH OF THE REPORT'S CLAIMS IS STILL SOMETHING TO RECONCILE AGAINST.
        # This block used to pin the report's naive band as a hand-typed
        # "existing_report_naive_range_pct_per_yr": [1.3, 1.7] literal, from
        # the days when that band WAS hand arithmetic in the prose and this
        # refit was the artifact-backed check on it. It is not that any more:
        # report_tokens.DEGRADATION_NAIVE_RANGE renders the published band as
        # the SPAN of the three estimators below, rounded to a tenth. So the
        # literal was a second copy of this script's own answer, and it went
        # stale exactly the way a copy does -- still reading 1.3-1.7 after the
        # estimators' span had moved to 1.3-1.8 (issue #165). Re-deriving the
        # span here instead would fix the staleness and keep the emptiness: a
        # figure compared against itself confirms nothing, however current the
        # copy is. The field is therefore gone, not corrected. The estimators
        # are published above; the span belongs to whoever renders it, once.
        #
        # The best-estimate range below IS still an independent claim. Section
        # 9 states "best-estimate ~0.5-1.0%/yr" as hand-reasoned prose that no
        # committed artifact derives, so measuring this script's evidence
        # against it is a real reconciliation -- and it finds a real
        # disagreement, recorded in gap_on_the_best_estimate.
        "reconciliation": {
            "existing_report_best_estimate_range_pct_per_yr": [0.5, 1.0],
            "existing_report_location": (
                'index.html, section 9, "Array health: 6-year degradation '
                'trend" -- specifically its hand-reasoned "best-estimate '
                '~0.5-1.0%/yr" verdict, which no committed artifact in this '
                "repo derives. The naive band printed in the same section is "
                "NOT an independent claim to reconcile against: "
                "report_tokens.DEGRADATION_NAIVE_RANGE renders it from this "
                "artifact's own three estimators."),
            "this_scripts_naive_figures_pct_per_yr": {
                "ols": round(ols_pct_per_yr, 2),
                "cagr": round(cagr_pct_per_yr, 2),
                "theil_sen": round(ts_pct_per_yr, 2),
            },
            "agreement_among_the_three_naive_estimators": (
                f"{naive_direction}, and they land within "
                f"{naive_spread_pp:.2f} percentage points of one another "
                "despite being deliberately different instruments: OLS is "
                "pulled by every point including the 2023 outlier, "
                "first-to-last CAGR uses ONLY the two endpoints, and the "
                "Theil-Sen median-of-pairwise-slopes leans on neither. Three "
                "fits with different failure modes landing together is "
                "evidence that the naive trend is a property of the annual "
                "series rather than of one fit's sensitivity. It is NOT "
                "evidence about true panel degradation, which needs the "
                "weather normalization this repo cannot do before 2025-07-24 "
                "-- see gap_on_the_best_estimate. This is the only agreement "
                "claim available here: the report's published naive band is "
                "the span of these same three figures, so checking them "
                "against it would be checking a number against itself."),
            "gap_on_the_best_estimate": (
                "NOT DETERMINED -- and not bounded either (Codex review pass "
                "3, finding 2: an earlier draft claimed a 0%/yr-to-naive-rate "
                "'bracket', reasoning that soiling/weather timing could only "
                "make the naive trend look WORSE than true degradation. That "
                "reasoning only covers one direction: soiling/weather could "
                "just as easily mask a TRUE degradation rate WORSE than the "
                "naive trend shows, if later years happened to be sunnier or "
                "cleaner -- a single VALIDATED dry-spell/soiling event "
                f"(an {single_event_swing_pct}% swing -- more than double the "
                f"ENTIRE naive 4-year change, {total_change_pct:.1f}%) proves "
                "soiling/weather CAN swing the annual total by more than the "
                "naive trend itself, in either direction, not that it is "
                "bounded to only push one way). The existing report's tighter "
                "'0.5-1.0%/yr best estimate' is a qualitative downward "
                "adjustment (reasoning: the naive metric isn't weather-"
                "normalized, so true degradation is probably lower), not a "
                "value any committed artifact in this repo derives, and not "
                "something this script's evidence can confirm OR bound: with "
                "no daily weather or production record before 2025-07-24 to "
                "separate soiling/weather timing from true panel aging in "
                "the 2021-2025 span, and a single confounding event already "
                "shown to be large enough to explain the ENTIRE naive trend "
                "on its own, the honest statement is that true degradation "
                "is NOT DETERMINED from data in this repo -- not merely "
                "uncertain within a stated range."),
            "what_would_settle_it": (
                "A multi-year (2020-2024) daily production + local rainfall "
                "+ irradiance record, or repeated professional cleanings "
                "with a measured before/after gain each year, to build a "
                "real soiling-corrected annual trend the way "
                "soiling_analysis.py already does for the one year it has "
                "daily data."),
        },
    }


# ---------------------------------------------------------------------------
# 7. Empirical ambient-temperature sensitivity (AC1's weather-normalization
#    magnitude check) -- reuses soiling_analysis.py's regression machinery,
#    adding tmean as one more regressor alongside its existing seasonal
#    harmonics + days-since-rain terms.
# ---------------------------------------------------------------------------
def temperature_sensitivity_block():
    pv = _sa.load_pvoutput(DATA / "pvoutput_daily.csv")
    rows = _sa.flag_clear_days(_sa.build_table(pv, TRAILING_START, TRAILING_END))

    tw = pd.read_csv(DATA / "weather_daily_tmean.csv")
    tw.columns = ["date", "tmean_f"]
    tw["date"] = pd.to_datetime(tw["date"]).dt.date
    tmean_by_date = dict(zip(tw["date"], tw["tmean_f"]))

    X, y = [], []
    for r in rows:
        if not r["clear"]:
            continue
        dsr = _sa.days_since_rain(r["date"])
        if dsr is None or r["date"] not in tmean_by_date:
            continue
        doy = r["date"].timetuple().tm_yday
        w = 2 * math.pi * doy / 365.25
        X.append([1.0, math.sin(w), math.cos(w), math.sin(2 * w), math.cos(2 * w),
                 float(dsr), tmean_by_date[r["date"]]])
        y.append(math.log(r["perf"]))

    beta, se, resid, dof = _sa.ols(X, y)
    coef, se_coef = beta[6], se[6]
    t = coef / se_coef
    p = _sa.t_pvalue(abs(t), dof)
    pct_per_degf = (math.exp(coef) - 1) * 100
    pct_per_degc = (math.exp(coef * 1.8) - 1) * 100

    return {
        "n_clear_days_used": len(y),
        "window": [str(TRAILING_START), str(TRAILING_END)],
        "regressors": ["intercept", "sin(annual)", "cos(annual)", "sin(semiannual)",
                      "cos(semiannual)", "days_since_rain", "tmean_degF"],
        "coef_pct_change_per_degF": round(pct_per_degf, 4),
        "coef_pct_change_per_degC": round(pct_per_degc, 4),
        "t_stat": round(t, 3),
        "p_value": float(f"{p:.3g}"),
        "dof": dof,
        "basis": (
            "OLS on log(clear-sky-normalized daily performance) over the one "
            "trailing year with both daily production AND daily ambient "
            "temperature (Open-Meteo, data/weather_daily_tmean.csv), "
            "controlling for calendar season (harmonics) and days-since-rain "
            "(soiling) the same way analysis/soiling_analysis.py's own "
            "regression does -- reused method (ols(), t_pvalue(), "
            "days_since_rain(), build_table(), flag_clear_days() are all "
            "imported read-only from that module), one added regressor."),
        "caveat": (
            "This is AMBIENT daily-MEAN temperature, not measured cell "
            "temperature, and the magnitude is smaller than the commonly "
            "cited manufacturer cell-temperature coefficient (~-0.35%/degC "
            "above 25degC STC) for exactly that reason -- ambient tmean "
            "damps the true midday cell-temperature swing. Same DIRECTION "
            "(negative), different magnitude scale; do not compare the two "
            "numbers as if they measured the same physical quantity. This "
            "regression cannot be extended to years before 2025-07-24 (no "
            "daily temperature record exists in this repo before then), so "
            "it is a magnitude check for the ONE year available, not a "
            "correction applied to the 6-year degradation trend."),
    }


# ---------------------------------------------------------------------------
# 8. Assemble + write.
# ---------------------------------------------------------------------------
def build():
    bill = load_bill_periods()
    observed_delta_gross = bill["period_2026"]["gross_kwh"] - bill["period_2024"]["gross_kwh"]
    observed_delta_net = bill["period_2026"]["net_kwh"] - bill["period_2024"]["net_kwh"]

    prod = production_block()
    cons = consumption_block(bill, prod)
    hourly = hourly_reconstruction(bill)
    decomposition = decomposition_block(bill, prod, cons, hourly)
    robustness = identifiability_robustness_check(bill, prod, cons, hourly, decomposition)
    ev = ev_block(cons)
    degradation = degradation_block()
    temperature = temperature_sensitivity_block()

    hourly_public = {k: v for k, v in hourly.items() if k == "validation"}["validation"]

    return {
        "meta": {
            "issue": "#16 -- gross imports rising: separate load growth from production loss",
            "bill_periods_compared": {"period_2024": PERIOD_2024, "period_2026": PERIOD_2026},
            "normalization_basis": (
                "No measured plane-of-array irradiance exists anywhere in this "
                "repo -- a true weather-normalized Performance Ratio is NOT "
                "DETERMINED. Normalization actually used: (a) a deterministic "
                "Haurwitz clear-sky model for solar GEOMETRY (reused from "
                "analysis/soiling_analysis.py), (b) day-matched calendar-window "
                "comparison using each year's own measured data, which "
                "controls for the typical seasonal irradiance pattern without "
                "needing a raw irradiance series, and (c) an empirically fitted "
                "ambient-temperature sensitivity as a magnitude check (see "
                "temperature_sensitivity block). See GLOSSARY.md 'Performance "
                "ratio (PR)'."),
            "data_sources": [
                "data/bill_periods_electric.csv (bill ground truth)",
                "data/pvoutput_daily.csv, data/enphase_daily_production.csv (2025-07-24..2026-07-23 daily production)",
                "data/pvoutput_yearly_2020-2025.csv (6-year annual production)",
                "data/weather_daily_tmean.csv (Open-Meteo daily mean temperature, same trailing year)",
                "data/soiling_results.json (soiling validation target, reused read-only)",
                "private/1-raw-data/enphase_sam8760_{2025,2026}.csv via samA.csv/samB.csv (whole-home CT, gross load)",
                "private raw Green Button 15-min export (usage.csv, 2025-06-27..2026-07-26)",
                "analysis/behavior_rebuild.py detect_sessions() (EV attribution, reused read-only)",
            ],
        },
        "bill_ground_truth": {
            **bill,
            "observed_delta_gross_kwh": round(observed_delta_gross, 1),
            "observed_delta_net_kwh": round(observed_delta_net, 1),
        },
        "production": prod,
        "consumption": cons,
        "hourly_reconstruction_validation": hourly_public,
        "decomposition": decomposition,
        "decomposition_identifiability_robustness_check": robustness,
        "ev_attribution": ev,
        "degradation": degradation,
        "temperature_sensitivity": temperature,
    }


def main():
    out = build()
    path = pathlib.Path.cwd() / "gross_import_decomposition.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
        f.write("\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
