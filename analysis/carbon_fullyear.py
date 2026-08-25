#!/usr/bin/env python3
"""Full-year grid-carbon analysis for the solar+EV household (CAISO / SDG&E).

Upgrade of carbon_timing.py from 4 seasonal sample days to a full year of cached
CAISO days across the analysis year (2025-07-24 .. 2026-07-23): 365/365 days
fetched and cached individually in private/1-raw-data/caiso_raw/ (issue #8). A
hard fail-closed gate (COVERAGE_MIN = 350/365) refuses to run — and names the
missing calendar dates — if that cache ever falls below the issue's coverage
bar; the month-hour-mean interpolation path below still exists for any day that
cache is missing, but at full coverage it has nothing left to interpolate.

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
  * independent of that regression check, coverage below COVERAGE_MIN (350/365,
    issue #8's own bar) is a hard abort too, even on a first-ever run with no
    prior committed artifact to regress against — the missing calendar dates are
    named individually (never just a count) so a human can see exactly which
    days are affected, and it is never interpolated past silently;
  * all outputs are validated first, then BOTH artifacts (CSV + JSON) are written
    to temp files and os.replace'd — a failed run changes nothing on disk.

Coverage model:
  * covered days -> their own measured hourly intensity;
  * uncovered days -> month-hour mean of the covered days in the same calendar month
    (every month has >= 2 covered days).

Household side: SDG&E Green Button 15-min usage.csv (same file the bill-validated
models use), EV sessions re-detected with the exact algorithm from behavior_rebuild.py.

Households with no EV (household.has_ev false, the intake applicability flag):
every EV-domain figure publishes as an explicit {"not_applicable": true,
"reason": ...} stub rather than as the 0.0 the arithmetic would produce -- a
zero here would read as "moving this household's charging saves no carbon",
which is a measured claim this household's data cannot make. The GRID and METER
figures are unaffected and stay real numbers, including the annual window means
that say how much cleaner midday is than overnight on CAISO. See the
applicability block below for the exact boundary and the two-way validator.

ORDERING CONTRACT (this script runs SECOND):
  behavior_rebuild.py  ->  carbon_fullyear.py, in the SAME working directory.
  The cost_note quotes behavior scenario 'a' (or, on a household whose intake
  says household.has_ev is false, states that there is no mistimed-charging
  saving to price -- scenario 'a' is a not-applicable stub, not a broken
  artifact, and never a computed $0.00), and behavior_rebuild.py writes its
  behavior_rebuild.json into the WORKING DIRECTORY; data/behavior_rebuild.json is
  only the last PROMOTED run. So the figure is read from this run's copy when one
  is there, from the committed copy otherwise, and a disagreement between the two
  is announced loudly rather than resolved in silence (see _scenario_a_saved).
  CLAUDE.md's section 9 regeneration gate already runs the pair in this order.

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


BEHAVIOR_JSON = "behavior_rebuild.json"    # written to the CWD by behavior_rebuild.py


def _read_scenario_a(path):
    """scenarios.a out of one behavior_rebuild.json, as (saved, reason).

    Two OUTCOMES, and they are different questions:

      * (float, "")      -- the household has an EV; scenarios.a.saved is the
                            netting-correct dollar saving for fixing mistimed
                            charging, and the cost_note quotes it.
      * (None, reason)   -- behavior_rebuild.py published scenarios.a as its
                            explicit {"not_applicable": True, "reason": ...}
                            stub because household.has_ev is false. That is
                            not_applicable, NOT not_determined: the intake DID
                            determine the answer, so the stub is a VALID
                            artifact and must NOT raise. There is simply no
                            mistimed-charging saving to price, and the
                            cost_note says exactly that instead of a figure.

    Fail-closed otherwise: an unreadable or malformed copy is still an ERROR,
    never a licence to fall back to the other one. Falling back past a broken
    artifact is how a stale figure gets published under a citation that looks
    current. Only the explicit stub marker is tolerated -- a MISSING
    scenarios.a, or an a with no "saved" and no marker, still aborts.
    """
    try:
        with open(path) as fh:
            doc = json.load(fh)
        node = doc["scenarios"]["a"]
        if isinstance(node, dict) and node.get("not_applicable") is True:
            return None, str(node.get("reason", "")).strip()
        return float(node["saved"]), ""
    except (OSError, ValueError, TypeError, KeyError) as e:
        raise SystemExit(
            f"{path}: cannot read scenarios.a.saved from the behavior artifact "
            f"({type(e).__name__}: {e}). Regenerate it with behavior_rebuild.py; "
            "this script will not fall back past a broken artifact.")


def _describe_scenario_a(v):
    """One artifact's scenario-a outcome, in words, for the NOTICE lines."""
    saved, reason = v
    if saved is None:
        return ("scenario a NOT APPLICABLE (household.has_ev is false"
                + (f": {reason}" if reason else "") + ")")
    return f"scenario-a saving ${saved:,.2f}/yr"


def _scenario_a_saved():
    """Scenario-a outcome, as (saved, reason), from THIS run's behavior artifact.

    saved is None when behavior_rebuild.py published scenarios.a as its
    not-applicable stub (household.has_ev false); see _read_scenario_a.

    The cost_note cites behavior_rebuild.json, so the figure is read rather than
    hardcoded -- a hardcoded copy here once went stale and contradicted the
    artifact it cited. But which behavior_rebuild.json? behavior_rebuild.py
    writes into the WORKING DIRECTORY (the documented private/verify sandbox);
    data/behavior_rebuild.json changes only when the operator promotes that run.
    Reading data/ unconditionally would quote the PREVIOUS household's saving
    while claiming to cite the artifact -- the same drift, one level down.

    Resolution order:
      1. current-run copy in the CWD (the upstream generator's product) -- used
         when present;
      2. committed data/ copy -- used only when there is no current-run copy,
         with a NOTICE saying so;
      3. both present and DISAGREEING -- this run's copy wins, and the mismatch
         is announced loudly: the committed copy is stale relative to this run,
         and the section 9 regeneration gate will fail until it is promoted.
    """
    run = pathlib.Path.cwd() / BEHAVIOR_JSON
    committed = DATA / BEHAVIOR_JSON
    if not run.exists():
        if not committed.exists():
            raise SystemExit(
                f"no behavior artifact: neither a current-run {run} nor the "
                f"committed {committed} exists. Run behavior_rebuild.py in this "
                "working directory first (see the ordering contract above).")
        v = _read_scenario_a(committed)
        print(f"NOTICE: no current-run {BEHAVIOR_JSON} in {pathlib.Path.cwd()}; "
              f"{_describe_scenario_a(v)} read from the committed "
              f"{committed}. If this run's household inputs or EV detector "
              "changed, run behavior_rebuild.py here FIRST.")
        return v
    v = _read_scenario_a(run)
    if committed.exists() and run.samefile(committed):
        print(f"NOTICE: {_describe_scenario_a(v)} from {run} "
              "(the working directory IS the committed data/ directory).")
        return v
    if not committed.exists():
        print(f"NOTICE: {_describe_scenario_a(v)} from this run's {run} "
              f"(no committed {committed} to compare against).")
        return v
    c = _read_scenario_a(committed)
    if c == v:
        print(f"NOTICE: {_describe_scenario_a(v)} from this run's {run} "
              f"(agrees with the committed {committed}).")
        return v
    bar = "!" * 72
    print(bar)
    print("NOTICE -- STALE COMMITTED ARTIFACT: this run's "
          f"{BEHAVIOR_JSON} says {_describe_scenario_a(v)}, but the committed "
          f"{committed} says {_describe_scenario_a(c)}.")
    print(f"  Using THIS RUN's {_describe_scenario_a(v)}. The committed copy "
          "has not been promoted; CLAUDE.md's section 9 gate will fail until "
          "it is.")
    print(bar)
    return v
HOURLY_CSV = DATA / "caiso_hourly_intensity.csv"           # committed aggregate
OLD_RESULTS = DATA / "carbon_results.json"                 # 4-day legacy artifact
RESULTS_JSON = DATA / "carbon_fullyear_results.json"       # committed results artifact

YEAR_START, YEAR_END = "2025-07-24", "2026-07-23"
CO2_COLS = ["Biogas CO2", "Biomass CO2", "Natural Gas CO2",
            "Coal CO2", "Imports CO2", "Geothermal CO2"]
SOP_NIGHT = list(range(0, 6))     # 00:00-06:00
MIDDAY = list(range(10, 14))      # 10:00-14:00
# One threshold, not two: issue #8's own bar for calling coverage "measured" is
# >=350/365 days, and that is also the hard fail-closed floor below which the
# script refuses to run at all (see the assertion in main()). Earlier drafts of
# this script had two different constants (a soft 300-day label threshold and,
# before that, no hard floor at all) with no stated reason for the gap; now
# there is exactly one number, and it means both things at once.
COVERAGE_MIN = 350                # >=350 covered days -> "measured"; <350 -> abort


# --------------------------------------------------------------- applicability
# A household with no EV has no mistimed charging to move, so every EV-domain
# figure below is ABSENT, not zero. Serialized as a bare 0.0 it reads as a
# measured finding -- "moving this household's charging saves no carbon" --
# which is a different claim from "this household has no charging to move", and
# CLAUDE.md 0 forbids publishing the second as the first. So the EV-domain
# fields carry the SAME explicit marker the rest of this repo already uses:
# behavior_rebuild.py's and extended_findings.py's {"not_applicable": True,
# "reason": ...}, read by report_tokens.py's _applicability(). No new
# vocabulary, and every consumer of this artifact can use the reader it has.
#
# THE BOUNDARY (get this wrong in either direction and the artifact lies):
#   * EV-DEPENDENT -- any figure whose value is a function of the detected EV
#     kWh. On a no-EV household every one of them collapses to zero (or, for
#     the b/c scenario footprints, to a_current_imports) purely because the
#     multiplier is zero. These become stubs.
#   * GRID-MEASURED -- any figure computed from CAISO intensity and/or this
#     household's own meter, with no EV term. The whole intensity section, the
#     current-import footprint, the import/export kWh and the avoided-export
#     carbon are measured for EVERY household and stay real numbers. In
#     particular intensity_kg_per_mwh.window_means_annual still states how much
#     cleaner midday is than overnight ON THE GRID (kg CO2/MWh) -- withholding
#     that because this house has no EV would be the opposite error.
def _not_applicable(what, see=""):
    """Explicit stub for one EV-domain figure in a household the intake says has
    no EV. not_applicable, NOT not_determined: the intake DID determine the
    answer -- the domain does not exist for this household. Same contract and
    wording as behavior_rebuild.py's own _not_applicable(), which governs the
    same flag on the artifact this script reads."""
    return {
        "not_applicable": True,
        "reason": ("household.has_ev is false (intake applicability flag, "
                   "DATA-SOURCES-CHEATSHEET.md) — %s does not apply to this "
                   "household; set the flag true and complete the intake "
                   "(charger.kw) to compute it" % what)
                  + (f". {see}" if see else ""),
    }


# Where the grid-side version of the midday-vs-overnight question still lives,
# quoted into the stubs that would otherwise look like the only place it was
# ever answered.
_SEE_WINDOW_MEANS = (
    "The GRID-side comparison this figure applies EV load to is measured for "
    "every household and is published unchanged at "
    "intensity_kg_per_mwh.window_means_annual (sop_overnight_00_06 vs "
    "solar_midday_10_14, kg CO2/MWh); only the household-EV application of it "
    "is absent here")

# One inventory of both classes, so the writer in main() and the validator that
# checks its own output cannot drift apart -- and so a future edit that adds an
# EV figure has one obvious place to declare it.
EV_DEPENDENT_FIELDS = (
    ("household_inputs", "ev_kwh_detected"),
    ("household_inputs", "ev_kwh_mistimed_on_off_peak"),
    ("footprints_kg_co2_per_yr", "b_mistimed_ev_moved_to_sop_00_06"),
    ("footprints_kg_co2_per_yr", "c_mistimed_ev_moved_to_midday_10_14"),
    ("footprints_kg_co2_per_yr", "detail", "mistimed_ev_kg_at_current_hours"),
    ("footprints_kg_co2_per_yr", "detail", "mistimed_ev_kg_if_charged_00_06"),
    ("footprints_kg_co2_per_yr", "detail", "mistimed_ev_kg_if_charged_10_14"),
    ("footprints_kg_co2_per_yr", "detail", "delta_b_vs_a"),
    ("footprints_kg_co2_per_yr", "detail", "delta_c_vs_a"),
    ("footprints_kg_co2_per_yr", "detail", "midday_cleaner_than_overnight_by"),
    ("old_vs_new", "ev_shift_delta_to_sop_kg"),
    ("old_vs_new", "ev_shift_delta_to_midday_kg"),
    ("old_vs_new", "midday_cleaner_than_overnight_by_kg"),
)
GRID_MEASURED_FIELDS = (
    ("intensity_kg_per_mwh", "window_means_annual", "sop_overnight_00_06"),
    ("intensity_kg_per_mwh", "window_means_annual", "solar_midday_10_14"),
    ("intensity_kg_per_mwh", "window_means_annual", "on_peak_16_21"),
    ("household_inputs", "imports_kwh"),
    ("household_inputs", "exports_kwh"),
    ("footprints_kg_co2_per_yr", "a_current_imports"),
    ("solar_exports_avoided_kg_co2_per_yr",),
)


def _ev_field(value, ev_applies, what, see=""):
    """One EV-domain figure: the computed value on a household the intake says
    has an EV, the explicit not-applicable stub on one it says has none. The
    caller always computes `value` -- the arithmetic is harmless and the
    branch stays a single expression -- but on a no-EV household that value is
    the zero this stub exists to keep out of the artifact."""
    return value if ev_applies else _not_applicable(what, see)


def _dig(doc, path):
    for k in path:
        doc = doc[k]
    return doc


def _validate_applicability(results, ev_applies):
    """Fail closed if the artifact's own applicability does not match the flag.

    Two directions, both real failure modes: an EV field published as a number
    on a no-EV household is the false measurement this guard exists to stop,
    and a GRID field published as a stub withholds a figure that is measured
    for every household. A partial edit that converts some EV fields and not
    others fails here rather than shipping a half-marked artifact."""
    for path in EV_DEPENDENT_FIELDS:
        v = _dig(results, path)
        stub = isinstance(v, dict) and v.get("not_applicable") is True
        if ev_applies and stub:
            raise SystemExit(
                f"{'.'.join(path)}: published as not_applicable but "
                "household.has_ev is not false — refusing to publish")
        if not ev_applies and not stub:
            raise SystemExit(
                f"{'.'.join(path)}: household.has_ev is false but the field "
                f"carries the value {v!r} — an absent EV figure must publish "
                "the explicit not_applicable marker, never a computed zero")
        if stub and not str(v.get("reason", "")).strip():
            raise SystemExit(f"{'.'.join(path)}: not_applicable stub with no "
                             "reason — refusing to publish")
    for path in GRID_MEASURED_FIELDS:
        v = _dig(results, path)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise SystemExit(
                f"{'.'.join(path)}: is grid/meter-measured for every household "
                f"but published as {v!r} — refusing to withhold a measured "
                "figure over a question about the EV domain")


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
    # ---------- intensity source resolution: build BOTH available sources and
    # merge, raw cache winning per-day where it has that day, rather than an
    # either/or choice that lets a partial raw cache shadow a complete
    # committed CSV. A raw cache with even one file used to be selected
    # outright and never fall back to (or merge with) the committed CSV, so a
    # stray/partial cache directory could fail the coverage gate below even
    # while a fully-covered committed CSV sat right beside it unused.
    has_raw = CAISO_DIR.is_dir() and bool(glob.glob(f"{CAISO_DIR}/caiso_co2_*.csv"))
    has_committed = HOURLY_CSV.exists()
    if not has_raw and not has_committed:
        raise SystemExit("no intensity source available: neither the raw cache "
                         f"{CAISO_DIR} nor the committed {HOURLY_CSV} exists")
    covered_raw = build_covered_from_raw() if has_raw else {}
    covered_committed = build_covered_from_committed_csv() if has_committed else {}
    covered = {**covered_committed, **covered_raw}  # raw wins per-day where present
    if has_raw and has_committed:
        mode = (f"raw cache ({CAISO_DIR}, {len(covered_raw)} day(s)) merged over "
                f"committed CSV ({HOURLY_CSV}, {len(covered_committed)} day(s)) "
                f"-> {len(covered)} day(s) total")
    elif has_raw:
        mode = f"raw cache ({CAISO_DIR})"
    else:
        mode = f"committed CSV ({HOURLY_CSV})"
    # resolve the upstream behavior figure once, up front, so its NOTICE lands
    # before the run's own output rather than in the middle of it
    scenario_a, scenario_a_reason = _scenario_a_saved()
    # canonicalize to the committed CSV's 0.1 kg/MWh so both source modes are
    # bit-identical (the legacy 4 days were already stored at 0.1)
    covered = {k: np.round(v, 1) for k, v in covered.items()}
    with open(OLD_RESULTS) as fh:
        old = json.load(fh)

    days = pd.date_range(YEAR_START, YEAR_END, freq="D")
    n_cov = sum(1 for dt_ in days if dt_ in covered)
    missing_dates = [dt_.strftime("%Y-%m-%d") for dt_ in days if dt_ not in covered]

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

    # ---------- FAIL CLOSED: issue #8's own bar is >=350/365 covered days ----
    # "never interpolated silently" means a human must be able to see exactly
    # which calendar dates are affected, not just a count -- so name them.
    if n_cov < COVERAGE_MIN:
        raise SystemExit(
            f"FAIL-CLOSED: only {n_cov}/365 days covered (need >={COVERAGE_MIN}); "
            f"missing {len(missing_dates)} date(s): {', '.join(missing_dates)}. "
            f"Restore the raw day-cache at {CAISO_DIR} for these dates (or the "
            f"committed {HOURLY_CSV.name}) before regenerating.")

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
    # ONE predicate for the whole EV domain, read from behavior_rebuild at call
    # time rather than re-derived here: the intake FLAG is the authority, and
    # "ev.sum() == 0" would be an inference from data, which CLAUDE.md 0 bans.
    # br.detect_sessions() keys off the same global, so the detector and the
    # artifact's applicability can never disagree.
    ev_applies = br.EV_ANALYSIS
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

    # by the time we reach this line the hard fail-closed gate above has already
    # guaranteed n_cov >= COVERAGE_MIN, so the else branch is unreachable in
    # practice; it is kept as a documented, harmless defense rather than deleted,
    # in case a future edit ever reorders these two blocks.
    label = ("measured" if n_cov >= COVERAGE_MIN
             else f"estimated ({n_cov} days sampled)")

    def ev_field(value, what, see=""):
        """_ev_field bound to THIS run's applicability, so the thirteen call
        sites below cannot each re-decide it."""
        return _ev_field(value, ev_applies, what, see)

    results = {
        "source": {
            "name": "CAISO Today's Outlook (official ISO data)",
            "endpoints": [
                "https://www.caiso.com/outlook/history/YYYYMMDD/co2.csv",
                "https://www.caiso.com/outlook/history/YYYYMMDD/demand.csv"],
            "fetched": "2026-08-01",
            "method": ("per covered day: hourly kg CO2/MWh = 1000 * mean(total CO2 mT/h, "
                       "all sources incl. imports) / mean(CAISO demand MW); uncovered days "
                       "use the month-hour mean of covered days in the same calendar month; "
                       "applied to the household's 15-min data by date and hour"),
            "legacy_seasonal_days": ("4 original days (one per season) that seeded the very "
                                     "first carbon_timing.py study; kept as a fallback in "
                                     "carbon_results.json for build_covered_from_raw() to "
                                     "reuse ONLY if the raw cache is ever missing one of "
                                     "these 4 exact dates -- with a full 365-day raw cache "
                                     "in place this fallback does not trigger, since every "
                                     "one of those 4 dates now has its own raw CAISO file"),
            "public_intensity_table": "data/caiso_hourly_intensity.csv"},
        "coverage": {
            "analysis_year": f"{YEAR_START} .. {YEAR_END} (365 days)",
            "days_covered": n_cov,
            "pct_of_year": round(100.0 * n_cov / 365, 1),
            "covered_dates": [dt_.strftime("%Y-%m-%d") for dt_ in sorted(covered)
                              if days[0] <= dt_ <= days[-1]],
            "days_interpolated_month_hour_mean": 365 - n_cov,
            "missing_dates": missing_dates,
            "coverage_achieved": (
                f"{n_cov}/365 days fetched directly from CAISO's public per-day "
                "history endpoints (private/1-raw-data/caiso_raw/, gitignored local "
                "archive); no proxy/allowlist barrier was encountered fetching this "
                "full year. Fall-back day 2025-11-02 IS covered, with a documented "
                "approximation (see caveats). Spring-forward day 2026-03-08 has a "
                "genuinely BLANK (not merely unused) 02:00 hour in CAISO's own raw "
                "files for both co2 and demand -- every 5-minute row that hour is "
                "empty -- so the existing all-24-hours validity check drops the "
                "whole day rather than just the one hour that is actually bad, and "
                "it falls back to that March's month-hour mean like any other "
                "uncovered day; the other 23 real hours of that date go unused as "
                "a result. This is a real finding from this run's own raw files, "
                "not merely the two-day approximation anticipated going in.")},
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
            "ev_kwh_detected": ev_field(
                round(float(ev.sum()), 1), "detected EV charging energy"),
            "ev_kwh_mistimed_on_off_peak": ev_field(
                round(mistimed_kwh, 1),
                "EV charging energy mistimed into on/off-peak hours")},
        "footprints_kg_co2_per_yr": {
            "a_current_imports": round(base_kg, 1),
            # b and c are the SAME number as a on a no-EV household -- two
            # distinct scenario footprints that coincide only because there is
            # nothing to move. Publishing them would read as "moving charging
            # changes nothing", so they are stubs instead.
            "b_mistimed_ev_moved_to_sop_00_06": ev_field(
                round(foot_sop, 1),
                "the footprint with mistimed EV charging moved to 00:00-06:00"),
            "c_mistimed_ev_moved_to_midday_10_14": ev_field(
                round(foot_mid, 1),
                "the footprint with mistimed EV charging moved to 10:00-14:00"),
            "detail": {
                "mistimed_ev_kg_at_current_hours": ev_field(
                    round(mistimed_kg_now, 1),
                    "the carbon carried by mistimed EV charging at its current hours"),
                "mistimed_ev_kg_if_charged_00_06": ev_field(
                    round(kg_to_sop, 1),
                    "the carbon of that same EV energy charged 00:00-06:00"),
                "mistimed_ev_kg_if_charged_10_14": ev_field(
                    round(kg_to_mid, 1),
                    "the carbon of that same EV energy charged 10:00-14:00"),
                "delta_b_vs_a": ev_field(
                    round(foot_sop - base_kg, 1),
                    "the footprint change from moving EV charging to 00:00-06:00"),
                "delta_c_vs_a": ev_field(
                    round(foot_mid - base_kg, 1),
                    "the footprint change from moving EV charging to 10:00-14:00"),
                # EV-DEPENDENT despite the name: this is the two destination
                # windows priced on THIS HOUSEHOLD'S mistimed EV kWh, so it
                # scales to zero with the EV, not with the grid.
                "midday_cleaner_than_overnight_by": ev_field(
                    round(foot_sop - foot_mid, 1),
                    "the midday-vs-overnight carbon gap on this household's "
                    "mistimed EV charging", _SEE_WINDOW_MEANS)}},
        "solar_exports_avoided_kg_co2_per_yr": round(export_avoided_kg, 1),
        "old_vs_new": {
            "old_basis": "4 seasonal sample days (carbon_results.json)",
            "new_basis": (f"{n_cov}/365 real CAISO days" if n_cov == 365 else
                          f"{n_cov} real CAISO days + month-hour-mean interpolation "
                          f"for the other {365 - n_cov}"),
            "annual_import_footprint_kg": {
                "old": of["a_current_imports"],
                "new": round(base_kg, 1),
                "delta": round(base_kg - of["a_current_imports"], 1)},
            "exports_avoided_kg": {
                "old": old["solar_exports_avoided_kg_co2_per_yr"],
                "new": round(export_avoided_kg, 1),
                "delta": round(export_avoided_kg
                               - old["solar_exports_avoided_kg_co2_per_yr"], 1)},
            # The whole old-vs-new COMPARISON goes, not just its "new" side: an
            # "old" EV-shift delta left standing beside an absent "new" one
            # would publish an EV figure for a household with no EV, one level
            # down.
            "ev_shift_delta_to_sop_kg": ev_field({
                "old": of["detail"]["delta_b_vs_a"],
                "new": round(foot_sop - base_kg, 1)},
                "the old-vs-new comparison of the EV-shift delta to 00:00-06:00"),
            "ev_shift_delta_to_midday_kg": ev_field({
                "old": of["detail"]["delta_c_vs_a"],
                "new": round(foot_mid - base_kg, 1)},
                "the old-vs-new comparison of the EV-shift delta to 10:00-14:00"),
            "midday_cleaner_than_overnight_by_kg": ev_field({
                "old": of["detail"]["midday_cleaner_than_overnight_by"],
                "new": round(foot_sop - foot_mid, 1)},
                "the old-vs-new comparison of the midday-vs-overnight gap on "
                "this household's mistimed EV charging", _SEE_WINDOW_MEANS)},
        "cost_note": (
            ("On EV-TOU-5 with post-May-2026 TOU windows, weekday 10:00-14:00 and "
             "00:00-06:00 are BOTH super-off-peak at the same price; the netting-"
             "correct dollar saving for fixing mistimed charging is scenario 'a' in "
             f"behavior_rebuild.json (${scenario_a:,.2f}/yr), unchanged "
             "by this carbon rerun.")
            if scenario_a is not None else
            # No EV: there is no charge timing to fix, so there is no dollar
            # saving to quote. Say so, name the flag, and never let the absent
            # figure read as a computed $0.00.
            ("This household has no EV (household.has_ev is false), so there is "
             "no charge timing to fix and NO mistimed-charging dollar saving to "
             "price: behavior_rebuild.json publishes scenario 'a' as an explicit "
             "not-applicable stub rather than a figure. The carbon figures above "
             "are unaffected -- they are measured on this household's own imports "
             "and exports."
             + (f" Artifact reason: {scenario_a_reason}"
                if scenario_a_reason else ""))),
        "caveats": ([
            f"Intensity measured on {n_cov} real CAISO days; the other "
            f"{365 - n_cov} day{'s' if 365 - n_cov != 1 else ''} "
            f"{'use' if 365 - n_cov != 1 else 'uses'} month-hour means of covered days "
            "(day-to-day weather/hydro/outage variation only partially captured)."]
           if n_cov < 365 else []) + [
            "Grid-AVERAGE intensity, not marginal; marginal overnight emissions (usually "
            "gas on the margin) would widen the overnight-vs-midday gap.",
            "Export credit uses the same grid-average intensity at export hours (standard "
            "displacement assumption).",
            "CAISO CO2 includes estimated import emissions (can be negative when "
            "exporting); on sunny spring days midday hourly intensity goes slightly "
            "negative under this accounting, which the 4-day version never sampled."]
           # a method caveat for a computation this household never ran
           + (["Moved EV energy assumed spread uniformly across the destination "
               "window on its own day."] if ev_applies else []) + [
            "DST transition days are a documented, narrow approximation, not a full "
            "redesign, on both 2025-11-02 (fall-back) and 2026-03-08 (spring-forward) -- "
            "unlike the household meter, which genuinely has two distinct 01:00 "
            "quarter-hour blocks on fall-back (100 intervals that day) and none labeled "
            "02:00 on spring-forward (92 intervals that day). On fall-back, CAISO's own "
            "file carries a flat 24-hour grid with no duplicate hour, so both of the "
            "household's real 01:00 blocks are matched against CAISO's single reported "
            "01:00 value -- an unavoidable approximation, since CAISO's own data does not "
            "distinguish the two. On spring-forward, CAISO's file is NOT simply flat: its "
            "02:00 hour is present as a row label but every 5-minute value that hour is "
            "blank in both the co2 and demand files (confirmed by inspecting the raw "
            "files directly, not assumed) -- so this script's existing all-24-hours "
            "validity check drops the WHOLE day rather than the one bad hour, and "
            "2026-03-08 is interpolated from March's month-hour mean like any other "
            "uncovered day, even though its other 23 real hours are perfectly good. No "
            "household interval that day has hour=2 either way, so this only costs one "
            "day's worth of otherwise-usable measured hours, not correctness."]}

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
    # Applicability is validated BEFORE either temp file is written, so a
    # mis-marked artifact never reaches disk (the atomic-write contract above).
    _validate_applicability(results, ev_applies)

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
    if ev_applies:
        print(f"footprint now: {base_kg:.0f} kg | to SOP: {foot_sop:.0f} | to midday: {foot_mid:.0f}")
        print(f"midday cleaner than overnight by {foot_sop - foot_mid:.0f} kg/yr")
    else:
        # the same rule as the artifact: no EV -> no EV figure, not a zero
        print(f"footprint now: {base_kg:.0f} kg | the moved-EV scenarios do not "
              "apply (household.has_ev is false)")
        wm = results["intensity_kg_per_mwh"]["window_means_annual"]
        print(f"grid intensity (measured, EV-independent): overnight 00-06 "
              f"{wm['sop_overnight_00_06']:.0f} vs midday 10-14 "
              f"{wm['solar_midday_10_14']:.0f} kg/MWh")
    print(f"solar exports avoided: {export_avoided_kg:.0f} kg/yr")
    if ev_applies:
        print(f"mistimed EV kWh: {mistimed_kwh:.0f}")


if __name__ == "__main__":
    main()
