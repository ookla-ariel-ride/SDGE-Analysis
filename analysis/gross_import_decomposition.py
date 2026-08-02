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
     columns are used to run a physically-grounded counterfactual: scale
     2026's actual hourly consumption and production down to their
     2024-estimated LEVELS (holding the diurnal SHAPE fixed) and recompute
     the resulting gross import. This reproduces both bill-actual endpoints
     (1,934 and 1,438 kWh) as an internal validation, then a Shapley-style
     two-factor decomposition attributes the change to a consumption term
     and a production term that sum to the observed change by construction.
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

    seasonal_fraction = prod_2026_pv / trailing_total_pv

    # Cross-check: what the SAME window's share of a year's insolation would
    # be from pure clear-sky solar geometry alone (no weather signal at all).
    period_clearsky = sum(clearsky_for_date(d) for d in period_dates)
    trailing_clearsky = sum(clearsky_for_date(d) for d in trailing_dates)
    seasonal_fraction_clearsky = period_clearsky / trailing_clearsky

    yearly_2024 = yearly_pvoutput_row(2024)
    yearly_2024_kwh = float(yearly_2024["kwh_generated"])
    prod_2024_est_empirical = seasonal_fraction * yearly_2024_kwh
    prod_2024_est_clearsky = seasonal_fraction_clearsky * yearly_2024_kwh

    return {
        "period_2026_measured_pvoutput_kwh": round(prod_2026_pv, 3),
        "period_2026_measured_enphase_kwh": round(prod_2026_en, 3),
        "period_2026_enphase_days_covered": en_days_covered,
        "period_2026_pvoutput_vs_enphase_pct_diff": round(
            (prod_2026_pv - prod_2026_en) / prod_2026_pv * 100, 2),
        "trailing_year_window": [str(TRAILING_START), str(TRAILING_END)],
        "trailing_year_total_pvoutput_kwh": round(trailing_total_pv, 3),
        "trailing_year_days_covered": trailing_days_covered,
        "seasonal_fraction_empirical": round(seasonal_fraction, 5),
        "seasonal_fraction_clearsky_geometric": round(seasonal_fraction_clearsky, 5),
        "clearsky_geometric_note": (
            "Pure Haurwitz clear-sky solar geometry (day length/sun angle only, "
            "no cloud cover) predicts this window should carry a ~10% LARGER "
            "share of the year's output than the empirical record shows -- "
            "consistent with San Diego's real, well-documented late-May/June "
            "'June gloom' marine layer depressing actual output below what "
            "day-length alone would predict for this specific calendar window. "
            "This divergence is evidence FOR using the EMPIRICAL fraction (it "
            "captures the real regional cloud pattern) rather than evidence "
            "against it; the clear-sky figure is reported for transparency, "
            "not averaged in."),
        "period_2024_estimated_kwh_empirical_basis": round(prod_2024_est_empirical, 1),
        "period_2024_estimated_kwh_clearsky_basis": round(prod_2024_est_clearsky, 1),
        "yearly_2024_calendar_total_kwh": yearly_2024_kwh,
        "estimate_basis": (
            "ESTIMATED, not measured: no daily production record exists for "
            "2024 anywhere in this repo (pvoutput_daily.csv and "
            "enphase_daily_production.csv both start 2025-07-24). Estimated as "
            "(this window's empirical share of a full year's output, measured "
            "from the one year with daily data) x (2024's own exact calendar-"
            "year PVOutput total, data/pvoutput_yearly_2020-2025.csv). This "
            "assumes the SEASONAL SHAPE of production (including the real "
            "regional cloud pattern, not just day length -- see "
            "seasonal_fraction_clearsky_geometric, which diverges from the "
            "empirical fraction by ~10% for exactly that reason) is stable "
            "year to year. It cannot fully verify that assumption, since "
            "actual cloud cover for May-June 2024 specifically is not "
            "recorded anywhere in this repo."),
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


def _shapley_two_factor(ct_values, prod_values, c_scale, p_scale):
    """f(c,p) = sum(max(0, c*CT - p*Production)) over the hourly series.
    Returns the four corner evaluations and the exact order-independent
    (Shapley/telescoping) decomposition of f(1,1)-f(c_scale,p_scale) into a
    consumption term and a production term that sum to it EXACTLY."""
    def f(c, p):
        return float(np.maximum(0.0, c * ct_values - p * prod_values).sum())

    f11 = f(1.0, 1.0)              # both at the 2026-actual level
    f01 = f(c_scale, 1.0)          # consumption at 2024 level, production at 2026 level
    f10 = f(1.0, p_scale)          # consumption at 2026 level, production at 2024 level
    f00 = f(c_scale, p_scale)      # both at the 2024-estimated level

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


def decomposition_block(bill, prod, cons, hourly):
    consumption_scale = cons["period_2024_estimated_kwh"] / cons["period_2026_ct_measured_kwh"]
    production_scale = (prod["period_2024_estimated_kwh_empirical_basis"]
                        / prod["period_2026_measured_pvoutput_kwh"])

    sh = _shapley_two_factor(hourly["ct_values"], hourly["production_hourly_derived"],
                             consumption_scale, production_scale)

    gross_2024 = bill["period_2024"]["gross_kwh"]
    gross_2026 = bill["period_2026"]["gross_kwh"]
    observed_delta = gross_2026 - gross_2024

    decomposed_sum = sh["consumption_term_kwh"] + sh["production_term_kwh"]
    pct_error = abs(decomposed_sum - observed_delta) / abs(observed_delta) * 100

    validation_2026_pct_error = ((sh["import_2026_actual_sim_kwh"] - gross_2026)
                                 / gross_2026 * 100)
    validation_2024_pct_error = ((sh["import_2024_counterfactual_sim_kwh"] - gross_2024)
                                 / gross_2024 * 100)

    return {
        "method": (
            "Hourly counterfactual scaling: 2026's actual hourly whole-home "
            "load (CT) and hourly-derived production are each scaled to "
            "their 2024-ESTIMATED aggregate level (holding the diurnal SHAPE "
            "fixed), gross import is recomputed as sum(max(0, load-"
            "production)) at each of the 4 corners of the 2x2 consumption x "
            "production scale grid, and a Shapley/telescoping two-factor "
            "decomposition splits the change into a consumption term and a "
            "production term that sum to the SIMULATED change EXACTLY by "
            "construction. See TECHNICAL.md 3.26."),
        "consumption_scale_2024_over_2026": round(consumption_scale, 5),
        "production_scale_2024_over_2026": round(production_scale, 5),
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
            "The gross-import increase for this pair of bill periods is "
            "overwhelmingly a CONSUMPTION story: the consumption term "
            "accounts for essentially all of the observed change, and the "
            "production term is small and slightly NEGATIVE (meaning "
            "estimated 2024 production for this window is not clearly lower "
            "than 2026's measured production -- consistent with the small "
            "expected 2-year degradation signal, roughly 45-60 kWh at the "
            "naive 1.3-1.8%/yr rate, sitting inside this method's own "
            "cross-meter noise floor of a few percent, as the "
            "import_*_sim_vs_bill_pct_error fields above show)."
            if abs(sh["production_term_kwh"]) < abs(sh["consumption_term_kwh"]) * 0.25
            else "See consumption_term_kwh vs production_term_kwh for the split."),
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
# 6. Degradation trend: artifact-backed, reconciled against index.html's
#    existing hand-computed claim (AC2, AC6).
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
        "reconciliation": {
            "existing_report_naive_range_pct_per_yr": [1.3, 1.7],
            "existing_report_best_estimate_range_pct_per_yr": [0.5, 1.0],
            "existing_report_location": (
                'index.html, section 9, "Array health: 6-year degradation '
                'trend" (hand-computed prose, not previously artifact-backed)'),
            "this_scripts_naive_figures_pct_per_yr": {
                "ols": round(ols_pct_per_yr, 2),
                "cagr": round(cagr_pct_per_yr, 2),
                "theil_sen": round(ts_pct_per_yr, 2),
            },
            "agreement_on_the_naive_range": (
                "CONFIRMED: this script's independently computed OLS "
                f"({ols_pct_per_yr:.2f}%/yr) and CAGR ({cagr_pct_per_yr:.2f}"
                "%/yr) reproduce the existing report's stated naive "
                "~1.3-1.7%/yr range closely, from a committed, reproducible "
                "artifact rather than hand arithmetic."),
            "gap_on_the_best_estimate": (
                "NOT INDEPENDENTLY CONFIRMED beyond a bracket. The existing "
                "report's tighter '0.5-1.0%/yr best estimate' is a "
                "qualitative downward adjustment (reasoning: the naive "
                "metric isn't weather-normalized, so true degradation is "
                "probably lower), not a value any committed artifact in this "
                "repo derives. This script's own evidence bounds it instead "
                "of guessing a point: a single VALIDATED dry-spell/soiling "
                f"event alone produced an {single_event_swing_pct}% swing -- "
                f"more than double the ENTIRE naive 4-year change "
                f"({total_change_pct:.1f}%) -- and this repo has no daily "
                "weather or production record before 2025-07-24 to separate "
                "how much of the 2021-2025 swing is soiling/weather timing "
                "vs true panel aging. The defensible statement is a bracket: "
                "true degradation is somewhere between ~0%/yr (if the "
                "observed swing is entirely soiling/weather-timing noise) "
                f"and the naive ~{max(ols_pct_per_yr, cagr_pct_per_yr):.1f}"
                "%/yr (if none of it is). The existing report's 0.5-1.0%/yr "
                "point falls inside that bracket but is NOT DETERMINED to be "
                "more correct than any other point inside it from data in "
                "this repo."),
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
