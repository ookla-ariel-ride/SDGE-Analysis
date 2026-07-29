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

Intensity-source resolution order (documented in TECHNICAL.md 3.15):
  1. RAW day-cache private/1-raw-data/caiso_raw/ (gitignored, local archive of the
     per-day CAISO CSVs) — used when present; the 4 original seasonal days are
     reconstructed from data/carbon_results.json as before.
  2. COMMITTED aggregate data/caiso_hourly_intensity.csv (all covered days x 24 h,
     0.1 kg/MWh) — a clean checkout rebuilds the results artifact exactly from it.
  Covered-day arrays are canonicalized to 0.1 kg/MWh (the committed CSV's
  resolution) in BOTH modes, so the two paths produce byte-identical artifacts.

Fail-closed + atomic (CLAUDE.md 9):
  * if the available coverage (raw cache or committed CSV) has FEWER covered days
    than the committed carbon_fullyear_results.json, the script ABORTS — it never
    silently rebuilds a degraded artifact;
  * all outputs are validated first, then BOTH artifacts (CSV + JSON) are written
    to temp files and os.replace'd — a failed run changes nothing on disk.

Coverage model:
  * covered days -> their own measured hourly intensity;
  * uncovered days -> month-hour mean of the covered days in the same calendar month
    (every month has >= 2 covered days).

Household side: SDG&E Green Button 15-min usage.csv (same file the bill-validated
models use), EV sessions re-detected with the exact algorithm from behavior_rebuild.py.

Run from private/verify with usage.csv, behavior_rebuild.py and rates.py beside it
(repo paths resolve automatically); public artifacts are written to the repo data/:
  data/caiso_hourly_intensity.csv    (date, hour, kgco2_per_mwh - aggregated ISO data)
  data/carbon_fullyear_results.json
"""
import glob
import json
import os
import pathlib
import re
import shutil

import numpy as np
import pandas as pd

import behavior_rebuild as br  # reuse load() and detect_sessions() exactly


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"
CAISO_DIR = ROOT / "private" / "1-raw-data" / "caiso_raw"  # raw day-cache (gitignored)


def _scenario_a_saved():
    """Scenario-a dollar saving from the committed behavior artifact.

    The cost_note cites behavior_rebuild.json; reading the figure from that
    artifact keeps the two from drifting -- a hardcoded copy here once went
    stale and contradicted the artifact it cited.
    """
    with open(DATA / "behavior_rebuild.json") as fh:
        return json.load(fh)["scenarios"]["a"]["saved"]
HOURLY_CSV = DATA / "caiso_hourly_intensity.csv"           # committed aggregate
OLD_RESULTS = DATA / "carbon_results.json"                 # 4-day legacy artifact
RESULTS_JSON = DATA / "carbon_fullyear_results.json"       # committed results artifact

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


def _check(day, v):
    # negative hourly values are legitimate: CAISO books negative import CO2 when
    # net-exporting, which can outweigh in-state gas on sunny spring middays
    assert np.isfinite(v).all() and (v > -200).all() and (v < 900).all(), day
    return v


def build_covered_from_raw():
    """{pd.Timestamp date: np.array(24) kg/MWh} from the raw per-day cache, plus
    the 4 legacy seasonal days preserved in carbon_results.json."""
    covered = {}
    for f in sorted(glob.glob(f"{CAISO_DIR}/caiso_co2_*.csv")):
        day = re.search(r"caiso_co2_(\d{8})\.csv", f).group(1)
        if not os.path.exists(f"{CAISO_DIR}/caiso_demand_{day}.csv"):
            continue
        s = hourly_intensity(day)
        if set(s.index) != set(range(24)):
            print(f"  skipping {day}: hours present {sorted(s.index)}")
            continue
        v = _check(day, s.sort_index().values)
        covered[pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}")] = v
    # legacy 4 seasonal days, preserved (rounded to 0.1) in the old results artifact
    with open(OLD_RESULTS) as fh:
        old = json.load(fh)
    for seas, day in old["source"]["sample_days"].items():
        dt_ = pd.Timestamp(f"{day[:4]}-{day[4:6]}-{day[6:]}")
        if dt_ not in covered:
            covered[dt_] = np.asarray(old["intensity_kg_per_mwh"]
                                         ["by_season_by_hour"][seas], dtype=float)
    return covered


def build_covered_from_committed_csv():
    """{pd.Timestamp date: np.array(24) kg/MWh} rebuilt from the committed
    aggregate data/caiso_hourly_intensity.csv (all covered days x 24 h)."""
    tab = pd.read_csv(HOURLY_CSV)
    if list(tab.columns) != ["date", "hour", "kgco2_per_mwh"]:
        raise SystemExit(f"{HOURLY_CSV}: unexpected schema {list(tab.columns)}")
    covered = {}
    for day, g in tab.groupby("date"):
        g = g.sort_values("hour")
        if list(g.hour) != list(range(24)):
            raise SystemExit(f"{HOURLY_CSV}: {day} does not have hours 0..23 — "
                             "truncated/corrupt aggregate; refusing to rebuild")
        covered[pd.Timestamp(day)] = _check(
            day, g.kgco2_per_mwh.astype(float).values)
    return covered


def main():
    # ---------- intensity source resolution (raw cache, else committed CSV) ----
    if CAISO_DIR.is_dir() and glob.glob(f"{CAISO_DIR}/caiso_co2_*.csv"):
        covered, mode = build_covered_from_raw(), f"raw cache ({CAISO_DIR})"
    elif HOURLY_CSV.exists():
        covered, mode = build_covered_from_committed_csv(), f"committed CSV ({HOURLY_CSV})"
    else:
        raise SystemExit("no intensity source available: neither the raw cache "
                         f"{CAISO_DIR} nor the committed {HOURLY_CSV} exists")
    # canonicalize to the committed CSV's 0.1 kg/MWh so both source modes are
    # bit-identical (the legacy 4 days were already stored at 0.1)
    covered = {k: np.round(v, 1) for k, v in covered.items()}
    with open(OLD_RESULTS) as fh:
        old = json.load(fh)

    days = pd.date_range(YEAR_START, YEAR_END, freq="D")
    n_cov = sum(1 for dt_ in days if dt_ in covered)

    # ---------- FAIL CLOSED: never rebuild with less coverage than committed ----
    if RESULTS_JSON.exists():
        prev_cov = json.load(open(RESULTS_JSON)).get("coverage", {}) \
                                                .get("days_covered", 0)
        if n_cov < prev_cov:
            raise SystemExit(
                f"FAIL-CLOSED: available coverage is {n_cov} day(s) but the "
                f"committed {RESULTS_JSON.name} was built from {prev_cov}; "
                "refusing to silently rebuild a degraded artifact. Restore "
                f"{CAISO_DIR} or the committed {HOURLY_CSV.name} first.")

    # ---------- full-year intensity: covered day -> itself, else month-hour mean ----
    cov = pd.DataFrame({"date": [d for d in covered for _ in range(24)],
                        "hour": list(range(24)) * len(covered),
                        "kg": np.concatenate([covered[d] for d in covered])})
    cov["month"] = cov.date.dt.month
    mh_mean = cov.groupby(["month", "hour"]).kg.mean()    # (month, hour) -> kg/MWh

    inten_map = {}                                        # date -> np.array(24)
    for dt_ in days:
        inten_map[dt_] = covered[dt_] if dt_ in covered else \
            mh_mean.loc[dt_.month].sort_index().values

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
                      f"behavior_rebuild.json (${_scenario_a_saved():,.2f}/yr), unchanged "
                      "by this carbon rerun."),
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

    # ---------- validate, then write BOTH artifacts atomically ----------
    rows = [(dt_.strftime("%Y-%m-%d"), h, round(v[h], 1))
            for dt_, v in sorted(covered.items()) for h in range(24)]
    tab = pd.DataFrame(rows, columns=["date", "hour", "kgco2_per_mwh"])
    assert len(tab) == 24 * len(covered) and np.isfinite(tab.kgco2_per_mwh).all()
    assert np.isfinite(annual).all() and base_kg > 0 and export_avoided_kg > 0
    for k in ("source", "coverage", "label", "intensity_kg_per_mwh",
              "household_inputs", "footprints_kg_co2_per_yr",
              "solar_exports_avoided_kg_co2_per_yr", "old_vs_new"):
        assert k in results, f"results section missing: {k}"

    tmp_csv, tmp_json = f"{HOURLY_CSV}.tmp", f"{RESULTS_JSON}.tmp"
    tab.to_csv(tmp_csv, index=False)
    with open(tmp_json, "w") as fh:
        json.dump(results, fh, indent=1)
    # Two os.replace calls are not one atomic action: a failure between them
    # would leave a new CSV beside the old JSON. Back up the CSV first and
    # restore it if the JSON replacement fails, so the pair advances or
    # reverts together (each os.replace is itself atomic; a hard kill between
    # the replaces leaves the .bak on disk as the recovery copy — and the §9
    # git-diff regeneration gate catches any mixed state before commit).
    bak_csv = f"{HOURLY_CSV}.bak"
    had_old_csv = os.path.exists(HOURLY_CSV)
    if had_old_csv:
        shutil.copy2(HOURLY_CSV, bak_csv)
    os.replace(tmp_csv, HOURLY_CSV)
    try:
        os.replace(tmp_json, RESULTS_JSON)
    except BaseException:
        if had_old_csv:
            os.replace(bak_csv, HOURLY_CSV)               # revert to the old pair
        raise
    if had_old_csv:
        os.remove(bak_csv)

    print(f"intensity source: {mode}")
    print(f"coverage: {n_cov}/365 days ({100 * n_cov / 365:.1f}%) -> label: {label}")
    print("annual avg intensity by hour (kg/MWh):")
    print("  " + " ".join(f"{h:02d}:{annual[h]:.0f}" for h in range(24)))
    print(f"footprint now: {base_kg:.0f} kg | to SOP: {foot_sop:.0f} | to midday: {foot_mid:.0f}")
    print(f"midday cleaner than overnight by {foot_sop - foot_mid:.0f} kg/yr")
    print(f"solar exports avoided: {export_avoided_kg:.0f} kg/yr")
    print(f"mistimed EV kWh: {mistimed_kwh:.0f}")


if __name__ == "__main__":
    main()
