#!/usr/bin/env python3
"""Extended findings — Tier-1/Tier-2 review items (Jul 2026 workup).

Computes, from data already in the archive plus cited public constants:
  A. AB 205 / Base Services Charge verification + fixed-charge escalation sensitivity
     (the BSC is ALREADY in rates.py: BSC $0.79343/day = $24.15/mo, live Oct 2025 —
      CPUC D.24-05-028, Res E-5355; EV-TOU-5's $16 basic service fee was REPLACED).
  B. Electrification dividend: measured EV charging cost vs gasoline counterfactual.
  C. Away-day natural experiment: unattended baseload from low-use days.
  D. Supercharging vs home delta: value of topping up at home before trips.
  E. Weekend super-off-peak exploitation: weekend non-EV load in hours >=14 is
     physically moved into the same day's 0-14 sop window and the year re-billed
     with the canonical engine (never kWh x rate-delta; CLAUDE.md 1b).
  F. Representative-year check: SAM-8760 load, overlapping calendar years.
  G. Gas decomposition: water-heating floor vs space-heating slope (HDD regression),
     and the heat-pump electrification ladder priced at midday-surplus value.
  H. 2039 NBT transition: battery marginal value if exports credit at flat 3-8c
     (NEM 2.0 expiry = PTO + 20 yr; PTO date from private/household.yaml).
  I. Tornado sensitivity on the battery payback (which lever moves it most).

Cited constants (sources in research notes / report prose):
  gasoline $4.65/gal (EIA CA regular, Jun 2025-May 2026 trailing 12-mo published);
  fleet 23.4 mpg (FHWA VM-1 2024 on-road light-duty);
  supercharging $0.45/kWh (estimate, typical CA range $0.40-0.50);
  DSGS VPP net revenue: $187.56/yr (20% backup reserve, primary) / $270.36/yr
  (0%-reserve sensitivity) -- empirically backtested against the UNION of ~14
  aggregations' real 2025 DSGS event calendars and this household's own measured
  load (data/dsgs_vpp_backtest.json, analysis/dsgs_vpp_backtest.py, issue #10);
  read from that committed artifact at runtime below, not hardcoded here. This is
  a PARTIAL-SEASON figure (2025-07-24..2025-10-30 only, not the full May-October
  season) and an upper-bound SCENARIO (the union of aggregations, not any single
  real household's own schedule -- see the artifact's per_aggregation_sensitivity
  field for the $108-249/yr range across the 14 individual aggregation schedules
  a real household might actually belong to); NOT a fully-annualized, single-
  household measurement, and this tornado lever's own label says so. Retired: the
  earlier $150-350/season Tesla program-terms extrapolation, kept only as an
  order-of-magnitude sanity check inside dsgs_vpp_backtest.py itself.

Writes data/extended_results.json. Requires usage.csv (Green Button) beside it,
plus data/weather_daily_tmean.csv and the two SAM-8760 files in
private/1-raw-data/ — all resolved against the repo root, which is found by
walking up from the CWD (then from this file), so the documented private/verify
copy-and-run sandbox works with no path edits.

EV- and gas-domain applicability is governed by the intake flags
household.has_ev and household.has_gas (household.yaml; DATA-SOURCES-
CHEATSHEET.md). The FLAGS are the authority — data presence is only a
consistency check, never an inference:
  flag true  -> every dependent input is hard-required (EV: misc.
               supercharge_kwh_yr, misc.miles_per_year; gas: gas.
               therm_allin_usd plus private/1-raw-data/gas.csv) and a
               missing one aborts the run — an incomplete intake must not
               publish a stub in place of a material finding;
  flag false -> the dependent sections (B, D -> has_ev; G -> has_gas) are
               published as explicit not_applicable stubs naming the flag,
               and any dependent input present alongside the false flag is
               a flag/data contradiction that aborts the run.
The publication gate re-checks the same contract per section before writing.
The one EV-dependent input NOT gated here is charger.kw: behavior_rebuild.py
owns it (it is that module's detection gate) and enforces the same
both-directions contract on it at import.
"""
import json, os, pathlib
import numpy as np, pandas as pd
# behavior_rebuild is imported at module level, BEFORE the flags below are read,
# and that is only safe because it supports the no-EV household itself: with
# household.has_ev false it reads no charger.kw, detects no sessions, and returns
# an EV-free year (see its docstring). It has to be that way round — this script
# needs br.load() (the shared Green Button loader with the coverage gate) and
# br.detect_sessions() for the away-day and weekend-shift sections whatever the
# flag says, and battery_dispatch_policies imports it too, so deferring the
# import here would not have kept it out of a no-EV run.
import behavior_rebuild as br
import battery_dispatch_policies as bp
import household as hh
import rates as R

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
PRIV = ROOT / "private" / "1-raw-data"

# cited PUBLIC constants (stay here, with sources — not household data):
GAS_USD_GAL = 4.65      # EIA CA regular, Jun 2025-May 2026 published 12-mo mean
FLEET_MPG = 23.4        # FHWA Highway Statistics VM-1 (2024), on-road light-duty
SC_USD_KWH = 0.45       # estimate: typical CA Tesla Supercharger $0.40-0.50
# per-HOUSE inputs from private/household.yaml (analysis/household.py; fails
# closed — run the intake interview in DATA-SOURCES-CHEATSHEET.md):
PTO_DATE = str(hh.get("household.pto_date"))        # NEM 2.0 clock starts here
# EV- and gas-domain APPLICABILITY flags (DATA-SOURCES-CHEATSHEET.md has_ev /
# has_gas; the dependent inputs are tagged required_if those flags). The flags
# are the authority — presence of data is only a consistency check:
#   flag true  -> every dependent input hard-required (incomplete intake
#                 aborts; it must not publish a stub over a material finding);
#   flag false -> dependent sections publish not_applicable stubs, and a
#                 dependent input present anyway is a contradiction (abort).

def _flag(path):
    """Read a required intake applicability flag; it must be a real boolean.

    Required here with no fallback: this script PUBLISHES the flag-governed
    sections, so an intake that never answered the question must not reach the
    artifact. (behavior_rebuild resolves household.has_ev too, and tolerates its
    absence by keeping its own hard requirement on charger.kw — the two can only
    disagree when the flag is missing, and then this call aborts the run.)"""
    v = hh.get(path)   # required=True: the intake defines the flag explicitly
    if v is not True and v is not False:
        raise SystemExit(
            f"{path} in private/household.yaml must be a YAML boolean "
            f"(true/false), got {v!r} — fix the intake before publishing")
    return v

HAS_EV = _flag("household.has_ev")
HAS_GAS = _flag("household.has_gas")

GAS_CSV = PRIV / "gas.csv"   # SDG&E gas Green Button daily export (has_gas only)

# probe every dependent input unconditionally so BOTH directions of the flag
# contract can be checked (missing-under-true AND present-under-false):
_EV_FIELDS = {
    "misc.supercharge_kwh_yr":                    # vehicle-app Charge Stats, 12 mo
        hh.get("misc.supercharge_kwh_yr", required=False),
    "misc.miles_per_year":                        # odometer-derived annual miles (report §9)
        hh.get("misc.miles_per_year", required=False),
}
_GAS_FIELDS = {
    "gas.therm_allin_usd":                        # $/therm all-in from the 12 gas bills
        hh.get("gas.therm_allin_usd", required=False),
}

def _gate_domain(flag_name, flag, fields, files=()):
    """Enforce the flag contract for one domain, both directions, fail closed."""
    present = [k for k, v in fields.items() if v is not None]
    present += [str(p.relative_to(ROOT)) for p in files if p.is_file()]
    missing = [k for k, v in fields.items() if v is None]
    missing += [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    if flag and missing:
        raise SystemExit(
            f"{flag_name} is true but {', '.join(missing)} "
            "is absent — complete the intake (DATA-SOURCES-CHEATSHEET.md "
            f"required_if: {flag_name.split('.')[-1]}); an incomplete intake "
            "must not publish")
    if not flag and present:
        raise SystemExit(
            f"{flag_name} is false but {', '.join(present)} "
            "is present — the flag and the data contradict each other; "
            "reconcile the intake (set the flag true, or remove the stale "
            "input(s)) and rerun")

_gate_domain("household.has_ev", HAS_EV, _EV_FIELDS)
_gate_domain("household.has_gas", HAS_GAS, _GAS_FIELDS, files=(GAS_CSV,))

SC_KWH = int(_EV_FIELDS["misc.supercharge_kwh_yr"]) if HAS_EV else None
MILES_YR = int(_EV_FIELDS["misc.miles_per_year"]) if HAS_EV else None
THERM_ALLIN = float(_GAS_FIELDS["gas.therm_allin_usd"]) if HAS_GAS else None
KWH_PER_THERM = 29.3
HP_COP = 3.5            # heat-pump space heating seasonal COP (modeled)
HPWH_COP = 3.5          # heat-pump water heater COP (modeled)
MIDDAY_VALUE = 0.10     # $/kWh marginal midday solar value (report §8)
BSC_MONTH = 24.15       # Base Services Charge (D.24-05-028), replaced $16 BSF
BATT_COST = 14500       # PW3 installed cost, $ — the SAME figure package_results.py
                        # declares as PW3_COST (research/battery-research-notes.md:
                        # PW3 installed ~$14,500). Tornado lever "install_cost"
                        # varies it across the 12k-17k quote range.

def _load_dsgs_backtest():
    """Read-only load of dsgs_vpp_backtest.py's committed backtest (data/
    dsgs_vpp_backtest.json) — the empirically-rated 2025 DSGS VPP net revenue,
    replayed against the real 2025 event calendar and this household's own
    measured load (issue #10). NEVER recomputed here — the same read-only,
    fail-closed convention nem3_grandfathering.py's load_nbt_2039_reference()
    uses for a sibling generator's committed JSON. A stale/absent reference
    must abort the run, not silently fall back to a guessed number."""
    p = DATA / "dsgs_vpp_backtest.json"
    if not p.exists():
        raise SystemExit(
            f"_load_dsgs_backtest: {p} is missing — run dsgs_vpp_backtest.py "
            "first (or restore the committed artifact); the DSGS tornado "
            "lever needs its backtested net-revenue figures.")
    j = json.loads(p.read_text())
    try:
        net_20pct = j["revenue"]["reserve_20pct"]["net_usd"]
        net_0pct_sens = j["revenue"]["reserve_0pct_sensitivity"]["net_usd"]
    except KeyError as e:
        raise SystemExit(f"{p}: missing expected revenue key {e} — cannot "
                          "use as a DSGS tornado-lever input")
    return float(net_20pct), float(net_0pct_sens)

DSGS_NET_20PCT_USD, DSGS_NET_0PCT_SENS_USD = _load_dsgs_backtest()
                        # $187.56 / $270.36 as of the committed backtest —
                        # read at runtime, not pinned, so a future rerun of
                        # dsgs_vpp_backtest.py (e.g. a later CEC filing year)
                        # flows through here automatically. Both figures are a
                        # PARTIAL-SEASON (2025-07-24..2025-10-30, not the full
                        # May-Oct season) upper-bound-scenario observation, not
                        # a fully-annualized single-household measurement --
                        # see dsgs_vpp_backtest.json's partial_season_caveat and
                        # per_aggregation_sensitivity fields, and the
                        # "dsgs_revenue" tornado lever's own label below.

# Section -> governing intake flag. Mapping rationale (what each CONSUMES):
#   electrification_dividend (B): EV charging kWh detected in usage.csv,
#     misc.miles_per_year, misc.supercharge_kwh_yr. Its "gas" counterfactual
#     is GASOLINE priced from the cited PUBLIC constants (GAS_USD_GAL,
#     FLEET_MPG), not the household's natural-gas service -> has_ev only.
#   supercharge_delta (D): misc.supercharge_kwh_yr vs the home sop rate
#     -> has_ev.
#   gas_decomposition (G): private/1-raw-data/gas.csv + gas.therm_allin_usd
#     -> has_gas.
_SECTION_FLAG = {
    "electrification_dividend": ("household.has_ev", HAS_EV),
    "supercharge_delta": ("household.has_ev", HAS_EV),
    "gas_decomposition": ("household.has_gas", HAS_GAS),
}


def _not_applicable(flag_name):
    """Explicit stub for a section whose governing intake flag is false.
    not_applicable, NOT not_determined: the intake DID determine the answer —
    the domain does not exist for this household. (not_determined is reserved
    for values genuinely undeterminable from the data.)"""
    return {
        "not_applicable": True,
        "reason": (f"{flag_name} is false (intake applicability flag, "
                   "DATA-SOURCES-CHEATSHEET.md) — the section does not apply "
                   "to this household; set the flag true and complete the "
                   "intake to compute it"),
    }


def _pin(cond, name, detail):
    """Labeled regression pin. These checks encode relationships verified for
    THIS dataset — they guard reproduction of this house's artifact, they are
    not universal physics. Fail closed, but say both things."""
    if not cond:
        raise SystemExit(
            f"regression pin failed [{name}]: {detail}. This check is a "
            "regression pin for this dataset (it guards byte-exact "
            "reproduction of the committed artifact); if you are reproducing "
            "with DIFFERENT household data, this can be a legitimate result "
            "for your house — review the pin and adjust or remove it rather "
            "than forcing your data to match.")

out = {}
d = br.load()
imp0 = d.Consumption.values.astype(float)
gen0 = d.Generation.values.astype(float)
base_bill = bp.billed(d, imp0, gen0)

# ---- battery dispatch savings: COMPUTED, never hard-coded -------------------
ev, sessions = br.detect_sessions(d)
POL_SAVE = {}
for _pol in ("evening", "twowin", "greedy"):
    _i, _e, _, _ = bp.run_batt(d, imp0, gen0, 13.5, _pol)
    POL_SAVE[_pol] = base_bill - bp.billed(d, _i, _e)
G = POL_SAVE["greedy"]
# post-behavior marginal (EV shift first, then battery — bp's integrated pipeline)
_sop_idx, _sop_ts = br.build_sop_index(d)
_imp_sh, _ = br.shift_ev(d, ev, sessions, [True] * len(sessions), _sop_idx, _sop_ts)
_b_sh = bp.billed(d, _imp_sh, gen0)
_i3, _e3, _, _ = bp.run_batt(d, _imp_sh, gen0, 13.5, "greedy")
G_POST = _b_sh - bp.billed(d, _i3, _e3)
# consistency gate: computed values must match the committed dispatch artifact;
# a mismatch means battery_dispatch_policies.json is stale — regenerate it FIRST.
_bdp = json.load(open(DATA / "battery_dispatch_policies.json"))
for _pol in ("evening", "twowin", "greedy"):
    assert abs(POL_SAVE[_pol] - _bdp["pw3"][_pol]["save"]) <= 1.5, (
        f"{_pol} save {POL_SAVE[_pol]:.0f} != committed "
        f"{_bdp['pw3'][_pol]['save']} — regenerate battery_dispatch_policies.json first")
assert abs(G_POST - _bdp["post_behavior"]["mid"]["battery_marginal"]) <= 1.5, (
    "post-behavior marginal drifted from committed artifact — regenerate it first")

# ---------- A. AB 205 verification + fixed-charge escalation sensitivity ----
# The restructure is in effect (Oct 2025) and rates.py's BSC/day already equals it.
bsc_daily_model = R.BSC
out["ab205"] = {
    "status": "already_in_effect_and_in_model",
    "bsc_model_daily": bsc_daily_model,
    "bsc_adopted_monthly": BSC_MONTH,
    "bsc_adopted_daily": round(BSC_MONTH * 12 / 365.25, 5),
    "model_matches_adopted": abs(bsc_daily_model - BSC_MONTH * 12 / 365.25) < 0.005,
    "ev_tou5_bsf_replaced": True,
    "note": ("June 2026 rates are post-restructure; scenario run is a verification. "
             "No adopted escalation of the fixed charge exists (AB 23 failed Feb 2026)."),
    # sensitivity: every +$12/mo of future fixed charge = +$144/yr on EVERY scenario
    # equally; it does not change any ranking or marginal (behavior/battery) figure.
    "fixed_charge_sensitivity_per_12_per_mo": 144,
}

# ---------- B. Electrification dividend ------------------------------------
if not HAS_EV:
    out["electrification_dividend"] = _not_applicable("household.has_ev")
else:
    p = d.p.values
    ev_cost = 0.0
    for per in ("on", "off", "sop"):
        m = p == per
        kwh = float(ev[m].sum())
        # season-weighted all-in import price for that period
        seas = d.seas.values
        cost = sum(float(ev[m & (seas == s)].sum()) * R.allin(s, per)
                   for s in ("S", "W"))
        ev_cost += cost
    sc_cost = SC_KWH * SC_USD_KWH
    gas_cost = MILES_YR / FLEET_MPG * GAS_USD_GAL
    out["electrification_dividend"] = {
        "home_ev_kwh": round(float(ev.sum())),
        "home_ev_cost_current_rates": round(ev_cost),
        "supercharge_kwh": SC_KWH, "supercharge_cost_est": round(sc_cost),
        "total_ev_fuel_cost": round(ev_cost + sc_cost),
        "gas_counterfactual_cost": round(gas_cost),
        "gas_price": GAS_USD_GAL, "mpg": FLEET_MPG, "miles_yr": MILES_YR,
        "dividend_yr": round(gas_cost - ev_cost - sc_cost),
        # if all home charging landed super-off-peak (the implemented schedule fix):
        "home_ev_cost_if_all_sop": round(sum(
            float(ev[d.seas.values == s].sum()) * R.allin(s, "sop") for s in ("S", "W"))),
        "dividend_yr_post_fix": round(gas_cost - sc_cost - sum(
            float(ev[d.seas.values == s].sum()) * R.allin(s, "sop") for s in ("S", "W"))),
        "label": "estimated (gasoline price + mpg + SC price are cited external constants)",
    }

# ---------- C. Away-day natural experiment ----------------------------------
# Away day: whole-day import < 40% of median daily import AND no EV session kWh.
daily = d.copy()
daily["date"] = daily.dt.dt.date
daily["evk"] = ev
g = daily.groupby("date").agg(imp=("Consumption", "sum"), exp=("Generation", "sum"),
                              evk=("evk", "sum"))
med_imp = g.imp.median()
away = g[(g.evk < 0.5) & (g.imp < 0.4 * med_imp)]
occ = g[~g.index.isin(away.index)]
out["away_days"] = {
    "n_away": int(len(away)),
    "away_median_import_kwh_day": round(float(away.imp.median()), 1) if len(away) else None,
    "occupied_median_import_kwh_day": round(float(occ.imp.median()), 1),
    "note": ("away-day import is grid import only; unattended LOAD also eats solar "
             "midday, so this is a lower bound on unattended consumption"),
    "implied_unattended_kw": (round(float(away.imp.median()) / 24, 2)
                              if len(away) else None),
}

# ---------- D. Supercharging vs home delta ----------------------------------
if not HAS_EV:
    out["supercharge_delta"] = _not_applicable("household.has_ev")
else:
    home_sop_allin = R.allin("S", "sop")  # summer sop all-in (winter within 0.3c)
    out["supercharge_delta"] = {
        "sc_kwh": SC_KWH, "sc_price_est": SC_USD_KWH,
        "home_sop_allin": round(home_sop_allin, 4),
        "delta_per_kwh": round(SC_USD_KWH - home_sop_allin, 3),
        "full_shift_value_yr": round(SC_KWH * (SC_USD_KWH - home_sop_allin)),
        "note": "upper bound; road-trip supercharging away from home cannot shift",
    }

# ---------- E. Weekend super-off-peak exploitation --------------------------
# CLAUDE.md 1b: move the energy PHYSICALLY and re-bill — never kWh x rate-delta.
# Weekend house-load (non-EV) import in hours >= 14 is moved into the SAME
# day's super-off-peak window (weekends are sop 0-14), spread uniformly across
# that day's 0-14 intervals, and the modified year is re-billed with the same
# canonical engine (bp.billed -> rates.bill_nem) as the baseline.
_wkend = d.wkend.values
_hrs = d.hour.values
_house = np.clip(imp0 - ev, 0, None)          # non-EV import per interval
_src = _wkend & (_hrs >= 14)                  # weekend off/on-peak house load
_dst = _wkend & (_hrs < 14)                   # that day's sop window
kwh_mov = float(_house[_src].sum())
_daykeys = d.dt.dt.normalize().values

def _weekend_shift_bill(frac):
    take = np.where(_src, _house, 0.0) * frac
    imp = imp0 - take
    add = np.zeros(len(d))
    for day in np.unique(_daykeys[_wkend]):
        m_day = _daykeys == day
        amt = float(take[m_day].sum())
        if amt <= 1e-12:
            continue
        idx = np.where(m_day & _dst)[0]
        add[idx] += amt / len(idx)
    imp2 = imp + add
    assert abs(float(imp2.sum()) - float(imp0.sum())) < 1e-6  # energy conserved
    return bp.billed(d, imp2, gen0)

_wk_full = base_bill - _weekend_shift_bill(1.0)
_wk_half = base_bill - _weekend_shift_bill(0.5)
out["weekend_sop"] = {
    "weekend_house_kwh_outside_sop": round(kwh_mov),
    "value_if_half_shifts_yr": round(_wk_half),
    "value_if_all_shifts_yr": round(_wk_full),
    "method": ("physically moved: weekend non-EV import in hours >=14 relocated "
               "into the same day's 0-14 super-off-peak intervals (uniform "
               "spread), baseline and shifted series both billed with "
               "rates.bill_nem (monthly per-period NEM netting, NBC on gross "
               "imports); deltas, not kWh x rate-difference"),
    "note": ("weekend 0-14 window is super-off-peak year-round; realistic capture "
             "is a fraction (laundry/dish/pool timing), so half-shift is quoted. "
             "'Weekend' means every day taking weekend TOU windows, which under "
             "the canonical holiday rule includes the eight tariff holidays; "
             "they contribute part of the kWh above"),
}

# ---------- F. Representative-year check (SAM 8760 overlap) -----------------
# fail closed: any missing/malformed input aborts the run (no partial artifact)
s25 = pd.read_csv(PRIV / "enphase_sam8760_2025.csv").iloc[:, 0].astype(float)
s26 = pd.read_csv(PRIV / "enphase_sam8760_2026.csv").iloc[:, 0].astype(float)
h = 24 * 181  # compare Jan-Jun of each calendar year, both fully populated
for _n, _s in (("enphase_sam8760_2025.csv", s25), ("enphase_sam8760_2026.csv", s26)):
    if len(_s) < h:
        raise SystemExit(
            f"{_n}: only {len(_s)} hourly rows, but the Jan-Jun comparison "
            f"needs at least {h} (both SAM-8760 files must fully cover January "
            "through June) — refusing to compare a truncated year")
j25, j26 = float(s25[:h].sum()), float(s26[:h].sum())
assert j25 > 1000 and j26 > 1000, "SAM-8760 files look empty/truncated"
out["representative_year"] = {
    "load_jan_jun_2025_kwh": round(j25), "load_jan_jun_2026_kwh": round(j26),
    "delta_pct": round((j26 - j25) / j25 * 100, 1),
    "note": ("whole-home load, same-months comparison across the two SAM-8760 "
             "calendar files; production side already weather-normalized in §9"),
}

# ---------- G. Gas decomposition (HDD regression) ---------------------------
if not HAS_GAS:
    out["gas_decomposition"] = _not_applicable("household.has_gas")
else:
    # fail closed: any missing/malformed input aborts the run (no partial artifact)
    gas = pd.read_csv(GAS_CSV, skiprows=13)  # SDG&E gas Green Button:
    # 13 metadata lines then Meter Number, Date, Start Time, Duration, Consumption
    gas.columns = [c.strip().lower() for c in gas.columns]
    gas["date"] = pd.to_datetime(gas["date"]).dt.date
    gas["therms"] = pd.to_numeric(gas["consumption"], errors="coerce")
    w = pd.read_csv(DATA / "weather_daily_tmean.csv", skiprows=1,
                    names=["date", "tf"])  # index header row, then date,tmean(F)
    w["date"] = pd.to_datetime(w["date"]).dt.date
    m = gas.merge(w[["date", "tf"]], on="date").dropna()
    assert len(m) >= 300, f"gas/weather merge too small ({len(m)} days) — schema drift?"
    m["hdd"] = np.clip(65 - m.tf.astype(float), 0, None)
    slope, floor = np.polyfit(m.hdd, m.therms, 1)
    ann_floor = floor * 365
    ann_heat = float(m.therms.sum() * (365 / len(m))) - ann_floor
    _pin(ann_floor > 0 and ann_heat > 0, "gas decomposition split",
         f"annual floor {round(ann_floor)} therms / heating {round(ann_heat)} "
         "therms — this house shows both a water-heating floor and a heating "
         "slope, so both must be positive (a house with, say, no gas heating "
         "could legitimately break this)")
    hp_kwh = ann_heat * KWH_PER_THERM / HP_COP
    hpwh_kwh = ann_floor * 0.8 * KWH_PER_THERM / HPWH_COP  # ~80% of floor = DHW
    out["gas_decomposition"] = {
        "days_regressed": int(len(m)),
        "floor_therms_day": round(float(floor), 3),
        "slope_therms_per_hdd": round(float(slope), 4),
        "annual_floor_therms": round(ann_floor),
        "annual_heating_therms": round(ann_heat),
        "heating_gas_cost_yr": round(ann_heat * THERM_ALLIN),
        "hp_heating_kwh": round(hp_kwh),
        "hp_heating_cost_at_midday_value": round(hp_kwh * MIDDAY_VALUE),
        "hp_heating_saving_yr": round(ann_heat * THERM_ALLIN - hp_kwh * MIDDAY_VALUE),
        "hpwh_kwh": round(hpwh_kwh),
        "hpwh_saving_yr": round(ann_floor * 0.8 * THERM_ALLIN - hpwh_kwh * MIDDAY_VALUE),
        "label": "modeled (COP and midday value assumed; gas $/therm from bills)",
    }

# ---------- H. 2039 NBT transition ------------------------------------------
def bill_flat_export(dd, imp, exp, credit):
    """Monthly netting replaced by flat export credit; NBC on gross imports."""
    f = dd.copy(); f["I"] = imp; f["E"] = exp
    tot = 0.0
    for _, mo in f.groupby("ym"):
        ikwh = {pp: mo[mo.p == pp].I.sum() for pp in ("on", "off", "sop")}
        s = mo.seas.iloc[0]
        chg = sum(ikwh[pp] * R.allin(s, pp) for pp in ikwh)
        chg -= mo.E.sum() * credit
        chg += mo.I.sum() * 0  # NBC already inside allin
        days = mo.dt.dt.date.nunique()
        tot += chg + days * R.BSC
    return tot

nbt = {}
for credit in (0.03, 0.05, 0.08):
    b0 = bill_flat_export(d, imp0, gen0, credit)
    i2, e2, _, _ = bp.run_batt(d, imp0, gen0, 13.5, "greedy")
    b1 = bill_flat_export(d, i2, e2, credit)
    nbt[f"{int(credit*100)}c"] = {"battery_marginal_yr": round(b0 - b1)}
_MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
out["nbt_2039"] = {
    "nem2_expiry": (f"~{_MON[int(PTO_DATE[5:7]) - 1]} {int(PTO_DATE[:4]) + 20} "
                    f"(PTO {PTO_DATE} + 20 yr)"),
    "battery_marginal_under_nem2": round(G),
    "battery_marginal_under_nbt": nbt,
    "note": ("under flat-credit exports the price-aware battery is worth MORE than "
             "under NEM 2.0 (stored surplus displaces ~51-87c imports instead of "
             "earning 3-8c credits); a 2026 PW3's 10-yr warranty ends 2036, three "
             "years before expiry — sequencing risk is modest and NBT raises, not "
             "lowers, its end-of-life value. NEM 2.0 transfers with the property "
             "on sale (worth ~$1,772-2,268/yr to a buyer at current rates)."),
}

# ---------- I. Tornado sensitivity on battery payback ------------------------
# All savings figures below are the COMPUTED dispatch results from the top of
# this script (POL_SAVE / G / G_POST) — never literals.
levers = {
    "install_cost": [(12000, 12000 / G), (BATT_COST, BATT_COST / G), (17000, 17000 / G)],
    "dispatch_policy": [(round(POL_SAVE["evening"]), BATT_COST / POL_SAVE["evening"]),
                        (round(POL_SAVE["twowin"]), BATT_COST / POL_SAVE["twowin"]),
                        (round(G), BATT_COST / G)],
    "post_behavior": [(round(G_POST), BATT_COST / G_POST), (round(G), BATT_COST / G)],
    "dsgs_revenue": [(0, BATT_COST / G),
                     (round(DSGS_NET_20PCT_USD, 2),
                      BATT_COST / (G + DSGS_NET_20PCT_USD)),
                     (round(DSGS_NET_0PCT_SENS_USD, 2),
                      BATT_COST / (G + DSGS_NET_0PCT_SENS_USD))],
    "escalation_5yr_avg": [(0.0, BATT_COST / G), (0.05, BATT_COST / (G * 1.104)),
                           (0.08, BATT_COST / (G * 1.17))],  # avg uplift over payback horizon
}
tor = {}
for k, vals in levers.items():
    pays = [round(v[1], 1) for v in vals]
    tor[k] = {"payback_range_yr": [min(pays), max(pays)],
              "swing_yr": round(max(pays) - min(pays), 1)}
tor["dsgs_revenue"]["note"] = (
    "The $187.56/$270.36 DSGS figures behind this lever are a PARTIAL-SEASON "
    "observation (2025-07-24..2025-10-30 only, not the full May-October season) "
    "and an inclusive upper-bound SCENARIO (the union of ~14 aggregation "
    "schedules, not any single real household's own) -- see "
    "data/dsgs_vpp_backtest.json's partial_season_caveat and "
    "per_aggregation_sensitivity fields. Not a fully-annualized, single-household "
    "measurement.")
out["tornado_battery"] = {
    "base_payback_yr": round(BATT_COST / G, 1),
    "levers": tor,
    "ranked_by_swing": sorted(tor, key=lambda k: -tor[k]["swing_yr"]),
}

# ---- publication gate: validate everything, then write ATOMICALLY ----------
REQUIRED = ("ab205", "electrification_dividend", "away_days", "supercharge_delta",
            "weekend_sop", "representative_year", "gas_decomposition", "nbt_2039",
            "tornado_battery")
for _k in REQUIRED:
    assert _k in out and "error" not in out[_k], f"section missing/failed: {_k}"
# flag-section coherence: the intake flag is the authority. Per section:
# flag true -> the section must be COMPUTED; flag false -> the section must be
# the not_applicable stub naming that flag; any mixture -> refuse to publish.
for _k in REQUIRED:
    if "not_determined" in out[_k]:
        raise SystemExit(
            f"{_k}: carries 'not_determined' — that stub is retired here; "
            "applicability stubs are 'not_applicable', governed by the intake "
            "flags (not_determined is reserved for values genuinely "
            "undeterminable from data); refusing to publish")
    _stub = bool(out[_k].get("not_applicable", False))
    if _k in _SECTION_FLAG:
        _fname, _fval = _SECTION_FLAG[_k]
        if _fval and _stub:
            raise SystemExit(
                f"{_k}: published as not_applicable but {_fname} is true — "
                "the flag demands the fully computed section; refusing to publish")
        if not _fval and not _stub:
            raise SystemExit(
                f"{_k}: computed but {_fname} is false — the flag demands a "
                "not_applicable stub; refusing to publish")
        if _stub and _fname not in out[_k].get("reason", ""):
            raise SystemExit(
                f"{_k}: not_applicable stub does not name its governing flag "
                f"{_fname}; refusing to publish")
    elif _stub:
        raise SystemExit(f"{_k}: not a flag-governed section — it may never be "
                         "not_applicable; refusing to publish")
if HAS_EV:
    _pin(out["electrification_dividend"]["dividend_yr"] > 0,
         "electrification dividend",
         f"dividend_yr = {out['electrification_dividend']['dividend_yr']} — for "
         "this household home EV charging beats the gasoline counterfactual, so "
         "the dividend must be positive (different rates/prices/mileage can "
         "legitimately make it negative)")
assert out["ab205"]["model_matches_adopted"], "rates.py BSC != adopted fixed charge"
for _n, _v in out["nbt_2039"]["battery_marginal_under_nbt"].items():
    _pin(_v["battery_marginal_yr"]
         > out["nbt_2039"]["battery_marginal_under_nem2"] * 0.8,
         f"NBT battery marginal ({_n})",
         f"{_v['battery_marginal_yr']} <= 0.8 x NEM2 marginal "
         f"{out['nbt_2039']['battery_marginal_under_nem2']} — for this dataset "
         "the battery is worth at least as much under flat-credit exports as "
         "under NEM 2.0 (a different load/solar shape can legitimately break "
         "this ratio)")
_tmp = DATA / "extended_results.json.tmp"
with open(_tmp, "w") as f:
    json.dump(out, f, indent=1)
os.replace(_tmp, DATA / "extended_results.json")  # atomic: no partial artifact
print(json.dumps(out, indent=1))
