#!/usr/bin/env python3
"""Grid-carbon-timing analysis for the solar+EV household (CAISO / SDG&E).

Carbon-intensity source (REAL DATA, no synthetic curves):
  CAISO "Today's Outlook" official history endpoints, fetched 2026-07-24:
    https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv     (5-min CO2 by source, metric tonnes/hour)
    https://www.caiso.com/outlook/history/YYYYMMDD/demand.csv  (5-min actual CAISO demand, MW)
  Four seasonal sample days inside the household's analysis year (2025-07-24 .. 2026-07-23):
    2025-10-15 (SON), 2026-01-15 (DJF), 2026-04-15 (MAM), 2026-07-15 (JJA)
  Hourly grid-average consumption intensity:
    kg CO2 / MWh = 1000 * mean_5min(total CO2 mT/h) / mean_5min(demand MW)
  Total CO2 = Biogas+Biomass+Natural Gas+Coal+Imports+Geothermal (imports may be negative
  when CAISO is net-exporting; CAISO's own accounting).

Household side: SDG&E Green Button 15-min usage.csv (same file the bill-validated models use),
EV sessions re-detected with the exact algorithm from behavior_rebuild.py.
"""
import json
import numpy as np
import pandas as pd

import behavior_rebuild as br  # reuse load() and detect_sessions() exactly

DATA = "caiso_data"
# sample day -> months it represents (meteorological season)
SEASON_DAYS = {
    "20260115": ("DJF", [12, 1, 2]),
    "20260415": ("MAM", [3, 4, 5]),
    "20260715": ("JJA", [6, 7, 8]),
    "20251015": ("SON", [9, 10, 11]),
}
CO2_COLS = ["Biogas CO2", "Biomass CO2", "Natural Gas CO2",
            "Coal CO2", "Imports CO2", "Geothermal CO2"]

SOP_NIGHT = list(range(0, 6))     # 00:00-06:00
MIDDAY = list(range(10, 14))      # 10:00-14:00


def hourly_intensity(day):
    """kg CO2/MWh for each hour of one CAISO day (energy-weighted within hour)."""
    co2 = pd.read_csv(f"{DATA}/caiso_co2_{day}.csv")
    dem = pd.read_csv(f"{DATA}/caiso_demand_{day}.csv")
    for df in (co2, dem):
        df.drop_duplicates(subset="Time", keep="first", inplace=True)
        df["hr"] = df["Time"].str.slice(0, 2).astype(int)
    co2["total"] = co2[CO2_COLS].sum(axis=1)             # mT CO2 per hour (rate)
    m = pd.merge(co2[["Time", "hr", "total"]],
                 dem[["Time", "Current demand"]], on="Time", how="inner")
    m = m.dropna(subset=["Current demand"])
    g = m.groupby("hr").agg(co2=("total", "mean"), mw=("Current demand", "mean"))
    return (1000.0 * g.co2 / g.mw)                       # kg/MWh, index 0..23


def main():
    # ---------- CAISO intensity profiles ----------
    season_int = {}         # season -> np.array(24) kg/MWh
    month_season = {}
    for day, (seas, months) in SEASON_DAYS.items():
        s = hourly_intensity(day)
        assert set(s.index) == set(range(24)), f"missing hours in {day}"
        season_int[seas] = s.sort_index().values
        for mo in months:
            month_season[mo] = seas
    annual = np.mean([season_int[s] for s in ["DJF", "MAM", "JJA", "SON"]], axis=0)

    # ---------- household 15-min data + EV detection (identical to behavior_rebuild) ----------
    d = br.load()
    ev, sessions = br.detect_sessions(d)
    d = d.assign(ev=ev,
                 hr=d.dt.dt.hour,
                 cseas=d.dt.dt.month.map(month_season))
    inten = np.empty(len(d))
    for seas, arr in season_int.items():
        mask = (d.cseas == seas).values
        inten[mask] = arr[d.hr.values[mask]]
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

    # per-season mistimed totals (energy stays in its own season when moved)
    mis_by_seas = d.assign(mis=mistimed).groupby("cseas").mis.sum()

    def window_mean(seas, hours):
        return float(np.mean(season_int[seas][hours]))

    def moved_kg(hours):
        return sum(mis_by_seas[s] * window_mean(s, hours) for s in mis_by_seas.index) * KG

    kg_to_sop = moved_kg(SOP_NIGHT)      # recharge 00-06 uniformly
    kg_to_mid = moved_kg(MIDDAY)         # recharge 10-14 uniformly

    foot_sop = base_kg - mistimed_kg_now + kg_to_sop
    foot_mid = base_kg - mistimed_kg_now + kg_to_mid

    # ---------- cost side (EV-TOU-5 + CEA rates already validated in behavior_rebuild) ----------
    # simple repricing of the mistimed kWh (ignores NEM monthly netting; scenario 'a' in
    # behavior_rebuild.json is the netting-correct number: $1,179.93/yr)
    mis_cost_now = 0.0
    for (s, p), grp in d.assign(mis=mistimed).groupby(["seas", "p"]):
        mis_cost_now += grp.mis.sum() * br.retail(s, p)
    mis_cost_sop = sum(grp.mis.sum() * br.retail(s, "sop")
                       for (s, p), grp in d.assign(mis=mistimed).groupby(["seas", "p"]))
    # On EV-TOU-5 (post-May-2026 windows) weekday 10:00-14:00 is ALSO super-off-peak,
    # so the midday window prices identically to overnight SOP on weekdays.

    results = {
        "source": {
            "name": "CAISO Today's Outlook (official ISO data)",
            "endpoints": [
                "https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv",
                "https://www.caiso.com/outlook/history/YYYYMMDD/demand.csv"],
            "fetched": "2026-07-24",
            "sample_days": {s: day for day, (s, _m) in SEASON_DAYS.items()},
            "method": ("hourly kg CO2/MWh = 1000 * mean(total CO2 mT/h, all sources incl. "
                       "imports) / mean(CAISO demand MW); one representative mid-month day "
                       "per meteorological season, applied to the household's 15-min data "
                       "by season and hour of day"),
            "raw_files_dir": "caiso_data/"},
        "intensity_kg_per_mwh": {
            "annual_avg_by_hour": [round(x, 1) for x in annual],
            "by_season_by_hour": {s: [round(x, 1) for x in v]
                                  for s, v in season_int.items()},
            "window_means_annual": {
                "sop_overnight_00_06": round(float(np.mean(annual[SOP_NIGHT])), 1),
                "solar_midday_10_14": round(float(np.mean(annual[MIDDAY])), 1),
                "on_peak_16_21": round(float(np.mean(annual[16:21])), 1)}},
        "household_inputs": {
            "window": "2025-07-24 .. 2026-07-23 (365 days)",
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
        "cost_vs_carbon": {
            "note": ("On EV-TOU-5 with post-May-2026 TOU windows, weekday 10:00-14:00 and "
                     "00:00-06:00 are BOTH super-off-peak at the same price, so the cleaner "
                     "midday window costs the same as overnight (weekdays)."),
            "simple_reprice_mistimed_now_usd": round(mis_cost_now, 2),
            "simple_reprice_mistimed_sop_usd": round(mis_cost_sop, 2),
            "simple_reprice_saving_usd": round(mis_cost_now - mis_cost_sop, 2),
            "netting_correct_saving_usd_scenario_a": 1179.93},
        "caveats": [
            "Intensity built from 4 real CAISO days (one per season), not all 365; day-to-day weather/hydro/outage variation is not captured.",
            "Grid-AVERAGE intensity, not marginal; marginal overnight emissions (usually gas on the margin) would widen the overnight-vs-midday gap.",
            "Export credit uses the same grid-average intensity at export hours (standard displacement assumption).",
            "CAISO CO2 includes estimated import emissions (can be negative when exporting); 2026-07-15 was a hot, gas-heavy day (demand peaked ~43.3 GW) - summer overnight intensity reflects that.",
            "Moved EV energy assumed spread uniformly across the destination window within its season."]}

    with open("carbon_results.json", "w") as fh:
        json.dump(results, fh, indent=1)

    print("annual avg intensity by hour (kg/MWh):")
    print("  " + " ".join(f"{h:02d}:{annual[h]:.0f}" for h in range(24)))
    for s in ("DJF", "MAM", "JJA", "SON"):
        v = season_int[s]
        print(f"  {s}: 00-06 {np.mean(v[SOP_NIGHT]):.0f} | 10-14 {np.mean(v[MIDDAY]):.0f} | 16-21 {np.mean(v[16:21]):.0f}")
    f = results["footprints_kg_co2_per_yr"]
    print(f"footprint now: {f['a_current_imports']:.0f} kg | to SOP: {f['b_mistimed_ev_moved_to_sop_00_06']:.0f} | to midday: {f['c_mistimed_ev_moved_to_midday_10_14']:.0f}")
    print(f"midday cleaner than overnight by {f['detail']['midday_cleaner_than_overnight_by']:.0f} kg/yr")
    print(f"solar exports avoided: {results['solar_exports_avoided_kg_co2_per_yr']:.0f} kg/yr")
    print(f"mistimed EV kWh: {mistimed_kwh:.0f}")


if __name__ == "__main__":
    main()
