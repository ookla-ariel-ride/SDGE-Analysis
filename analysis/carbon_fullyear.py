#!/usr/bin/env python3
"""Full-year grid-carbon analysis for the solar+EV household (CAISO / SDG&E).

Upgrade of carbon_timing.py from 4 seasonal sample days to 28 sampled CAISO days
spread across the analysis year (2025-07-24 .. 2026-07-23), roughly 2 per calendar
month plus the original 4 mid-season days.

Carbon-intensity source (REAL DATA, no synthetic curves):
  CAISO "Today's Outlook" official history endpoints:
    https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv     (5-min CO2 by source, mT/h)
    https://www.caiso.com/outlook/history/YYYYMMDD/demand.csv  (5-min actual CAISO demand, MW)
  Hourly grid-average consumption intensity, per day:
    kg CO2 / MWh = 1000 * mean_5min(total CO2 mT/h) / mean_5min(demand MW)
  Total CO2 = Biogas+Biomass+Natural Gas+Coal+Imports+Geothermal (imports may be
  negative when CAISO is net-exporting; CAISO's own accounting).

Coverage model:
  * covered days -> their own measured hourly intensity;
  * uncovered days -> month-hour mean of the covered days in the same calendar month
    (every month has >= 2 covered days).
  The 4 original seasonal days (raw files no longer cached) are reconstructed from the
  hourly arrays preserved in data/carbon_results.json (rounded to 0.1 kg/MWh there).

Household side: SDG&E Green Button 15-min usage.csv (same file the bill-validated
models use), EV sessions re-detected with the exact algorithm from behavior_rebuild.py.

Run from private/verify with usage.csv, behavior_rebuild.py, rates.py and caiso_raw/
beside it; public artifacts are written to the repo data/ directory:
  data/caiso_hourly_intensity.csv    (date, hour, kgco2_per_mwh - aggregated ISO data)
  data/carbon_fullyear_results.json
"""
import glob
import json
import os
import re

import numpy as np
import pandas as pd

import behavior_rebuild as br  # reuse load() and detect_sessions() exactly

CAISO_DIR = "caiso_raw"
OUT_DATA = "../../data" if os.path.isdir("../../data") else "."
OLD_RESULTS = os.path.join(OUT_DATA, "carbon_results.json")

YEAR_START, YEAR_END = "2025-07-24", "2026-07-23"
CO2_COLS = ["Biogas CO2", "Biomass CO2", "Natural Gas CO2",
            "Coal CO2", "Imports CO2", "Geothermal CO2"]
SOP_NIGHT = list(range(0, 6))     # 00:00-06:00
MIDDAY = list(range(10, 14))      # 10:00-14:00
COVERED_LABEL_MIN = 300           # >=300 covered days -> "measured"


def hourly_intensity(day):
    """kg CO2/MWh for each hour of one CAISO day (identical math to carbon_timing.py)."""
    co2 = pd.read_csv(f"{CAISO_DIR}/caiso_co2_{day}.csv")
    dem = pd.read_csv(f"{CAISO_DIR}/caiso_demand_{day}.csv")
    for df in (co2, dem):
        df.drop_duplicates(subset="Time", keep="first", inplace=True)
        df["hr"] = df["Time"].str.slice(0, 2).astype(int)
    co2["total"] = co2[CO2_COLS].sum(axis=1)             # mT CO2 per hour (rate)
    m = pd.merge(co2[["Time", "hr", "total"]],
                 dem[["Time", "Current demand"]], on="Time", how="inner")
    m = m.dropna(subset=["Current demand"])
    g = m.groupby("hr").agg(co2=("total", "mean"), mw=("Current demand", "mean"))
    return (1000.0 * g.co2 / g.mw)                       # kg/MWh, index 0..23


def build_covered():
    """{pd.Timestamp date: np.array(24) kg/MWh} for every day with raw or legacy data."""
    covered = {}
    for f in sorted(glob.glob(f"{CAISO_DIR}/caiso_co2_*.csv")):
        day = re.search(r"caiso_co2_(\d{8})\.csv", f).group(1)
        if not os.path.exists(f"{CAISO_DIR}/caiso_demand_{day}.csv"):
            continue
        s = hourly_intensity(day)
        if set(s.index) != set(range(24)):
            print(f"  skipping {day}: hours present {sorted(s.index)}")
            continue
        v = s.sort_index().values
        # negative hourly values are legitimate: CAISO books negative import CO2 when
        # net-exporting, which can outweigh in-state gas on sunny spring middays
        assert np.isfinite(v).all() and (v > -200).all() and (v < 900).all(), day
        covered[pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}")] = v
    # legacy 4 seasonal days, preserved (rounded to 0.1) in the old results artifact
    with open(OLD_RESULTS) as fh:
        old = json.load(fh)
    for seas, day in old["source"]["sample_days"].items():
        dt_ = pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}")
        if dt_ not in covered:
            covered[dt_] = np.asarray(old["intensity_kg_per_mwh"]
                                         ["by_season_by_hour"][seas], dtype=float)
    return covered, old


def main():
    covered, old = build_covered()

    # ---------- public aggregated artifact: measured hourly intensity ----------
    rows = [(dt_.strftime("%Y-%m-%d"), h, round(v[h], 1))
            for dt_, v in sorted(covered.items()) for h in range(24)]
    tab = pd.DataFrame(rows, columns=["date", "hour", "kgco2_per_mwh"])
    tab.to_csv(os.path.join(OUT_DATA, "caiso_hourly_intensity.csv"), index=False)

    # ---------- full-year intensity: covered day -> itself, else month-hour mean ----------
    cov = pd.DataFrame({"date": [d for d in covered for _ in range(24)],
                        "hour": list(range(24)) * len(covered),
                        "kg": np.concatenate([covered[d] for d in covered])})
    cov["month"] = cov.date.dt.month
    mh_mean = cov.groupby(["month", "hour"]).kg.mean()    # (month, hour) -> kg/MWh

    days = pd.date_range(YEAR_START, YEAR_END, freq="D")
    inten_map = {}                                        # date -> np.array(24)
    for dt_ in days:
        inten_map[dt_] = covered[dt_] if dt_ in covered else \
            mh_mean.loc[dt_.month].sort_index().values
    n_cov = sum(1 for dt_ in days if dt_ in covered)

    # ---------- household 15-min data + EV detection (identical to behavior_rebuild) ----------
    d = br.load()
    ev, _sessions = br.detect_sessions(d)
    d = d.assign(ev=ev, hr=d.dt.dt.hour, day=d.dt.dt.normalize())
    inten = np.array([inten_map[day_][hr] for day_, hr in zip(d.day, d.hr)])
    d["inten"] = inten                                    # kg/MWh at each 15-min interval

    KG = 1e-3  # kWh * kg/MWh -> kg
    imp = d.Consumption.values
    exp = d.Generation.values

    base_kg = float((imp * inten).sum() * KG)
    export_avoided_kg = float((exp * inten).sum() * KG)

    # mistimed EV energy = detected EV kWh in on-peak or off-peak TOU periods
    mistimed = np.where(np.isin(d.p.values, ["on", "off"]), ev, 0.0)
    mistimed_kg_now = float((mistimed * inten).sum() * KG)
    mistimed_kwh = float(mistimed.sum())

    # moved EV energy: each day's mistimed kWh spread uniformly over the destination
    # window of the SAME day (finer than carbon_timing.py's per-season treatment)
    mis_by_day = d.assign(mis=mistimed).groupby("day").mis.sum()

    def moved_kg(hours):
        return float(sum(kwh * np.mean(inten_map[day_][hours])
                         for day_, kwh in mis_by_day.items()) * KG)

    kg_to_sop = moved_kg(SOP_NIGHT)      # recharge 00-06 uniformly
    kg_to_mid = moved_kg(MIDDAY)         # recharge 10-14 uniformly

    foot_sop = base_kg - mistimed_kg_now + kg_to_sop
    foot_mid = base_kg - mistimed_kg_now + kg_to_mid

    annual = np.mean([inten_map[dt_] for dt_ in days], axis=0)
    of = old["footprints_kg_co2_per_yr"]

    label = ("measured" if n_cov >= COVERED_LABEL_MIN
             else f"estimated ({n_cov} days sampled)")
    results = {
        "source": {
            "name": "CAISO Today's Outlook (official ISO data)",
            "endpoints": [
                "https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv",
                "https://www.caiso.com/outlook/history/YYYYMMDD/demand.csv"],
            "fetched": "2026-07-25",
            "method": ("per covered day: hourly kg CO2/MWh = 1000 * mean(total CO2 mT/h, "
                       "all sources incl. imports) / mean(CAISO demand MW); uncovered days "
                       "use the month-hour mean of covered days in the same calendar month; "
                       "applied to the household's 15-min data by date and hour"),
            "legacy_seasonal_days": ("4 original days reused from carbon_results.json "
                                     "hourly arrays (raw 5-min files no longer cached)"),
            "public_intensity_table": "data/caiso_hourly_intensity.csv"},
        "coverage": {
            "analysis_year": f"{YEAR_START} .. {YEAR_END} (365 days)",
            "days_covered": n_cov,
            "pct_of_year": round(100.0 * n_cov / 365, 1),
            "covered_dates": [dt_.strftime("%Y-%m-%d") for dt_ in sorted(covered)
                              if days[0] <= dt_ <= days[-1]],
            "days_interpolated_month_hour_mean": 365 - n_cov,
            "why_not_365": ("CAISO endpoints unreachable from the sandboxed analysis "
                            "environment (proxy allowlist); days were fetched individually "
                            "through the permitted fetch channel, so coverage was capped at "
                            "~2 days per calendar month plus the 4 original seasonal days")},
        "label": label,
        "intensity_kg_per_mwh": {
            "annual_avg_by_hour": [round(x, 1) for x in annual],
            "window_means_annual": {
                "sop_overnight_00_06": round(float(np.mean(annual[SOP_NIGHT])), 1),
                "solar_midday_10_14": round(float(np.mean(annual[MIDDAY])), 1),
                "on_peak_16_21": round(float(np.mean(annual[16:21])), 1)}},
        "household_inputs": {
            "window": f"{YEAR_START} .. {YEAR_END} (365 days)",
            "imports_kwh": round(float(imp.sum()), 1),
            "exports_kwh": round(float(exp.sum()), 1),
            "ev_kwh_detected": round(float(ev.sum()), 1),
            "ev_kwh_mistimed_on_off_peak": round(mistimed_kwh, 1)},
        "footprints_kg_co2_per_yr": {
            "a_current_imports": round(base_kg, 1),
            "b_mistimed_ev_moved_to_sop_00_06": round(foot_sop, 1),
            "c_mistimed_ev_moved_to_midday_10_14": round(foot_mid, 1),
            "detail": {
                "mistimed_ev_kg_at_current_hours": round(mistimed_kg_now, 1),
                "mistimed_ev_kg_if_charged_00_06": round(kg_to_sop, 1),
                "mistimed_ev_kg_if_charged_10_14": round(kg_to_mid, 1),
                "delta_b_vs_a": round(foot_sop - base_kg, 1),
                "delta_c_vs_a": round(foot_mid - base_kg, 1),
                "midday_cleaner_than_overnight_by": round(foot_sop - foot_mid, 1)}},
        "solar_exports_avoided_kg_co2_per_yr": round(export_avoided_kg, 1),
        "old_vs_new": {
            "old_basis": "4 seasonal sample days (carbon_results.json)",
            "new_basis": f"{n_cov} sampled days + month-hour-mean interpolation",
            "annual_import_footprint_kg": {
                "old": of["a_current_imports"],
                "new": round(base_kg, 1),
                "delta": round(base_kg - of["a_current_imports"], 1)},
            "exports_avoided_kg": {
                "old": old["solar_exports_avoided_kg_co2_per_yr"],
                "new": round(export_avoided_kg, 1),
                "delta": round(export_avoided_kg
                               - old["solar_exports_avoided_kg_co2_per_yr"], 1)},
            "ev_shift_delta_to_sop_kg": {
                "old": of["detail"]["delta_b_vs_a"],
                "new": round(foot_sop - base_kg, 1)},
            "ev_shift_delta_to_midday_kg": {
                "old": of["detail"]["delta_c_vs_a"],
                "new": round(foot_mid - base_kg, 1)},
            "midday_cleaner_than_overnight_by_kg": {
                "old": of["detail"]["midday_cleaner_than_overnight_by"],
                "new": round(foot_sop - foot_mid, 1)}},
        "cost_note": ("On EV-TOU-5 with post-May-2026 TOU windows, weekday 10:00-14:00 and "
                      "00:00-06:00 are BOTH super-off-peak at the same price; the netting-"
                      "correct dollar saving for fixing mistimed charging is scenario 'a' in "
                      "behavior_rebuild.json ($1,179.93/yr), unchanged by this carbon rerun."),
        "caveats": [
            f"Intensity measured on {n_cov} real CAISO days; the other {365 - n_cov} days "
            "use month-hour means of covered days (day-to-day weather/hydro/outage "
            "variation only partially captured).",
            "Grid-AVERAGE intensity, not marginal; marginal overnight emissions (usually "
            "gas on the margin) would widen the overnight-vs-midday gap.",
            "Export credit uses the same grid-average intensity at export hours (standard "
            "displacement assumption).",
            "CAISO CO2 includes estimated import emissions (can be negative when "
            "exporting); on sunny spring days midday hourly intensity goes slightly "
            "negative under this accounting, which the 4-day version never sampled.",
            "Moved EV energy assumed spread uniformly across the destination window on its "
            "own day."]}

    with open(os.path.join(OUT_DATA, "carbon_fullyear_results.json"), "w") as fh:
        json.dump(results, fh, indent=1)

    print(f"coverage: {n_cov}/365 days ({100 * n_cov / 365:.1f}%) -> label: {label}")
    print("annual avg intensity by hour (kg/MWh):")
    print("  " + " ".join(f"{h:02d}:{annual[h]:.0f}" for h in range(24)))
    print(f"footprint now: {base_kg:.0f} kg | to SOP: {foot_sop:.0f} | to midday: {foot_mid:.0f}")
    print(f"midday cleaner than overnight by {foot_sop - foot_mid:.0f} kg/yr")
    print(f"solar exports avoided: {export_avoided_kg:.0f} kg/yr")
    print(f"mistimed EV kWh: {mistimed_kwh:.0f}")


if __name__ == "__main__":
    main()
