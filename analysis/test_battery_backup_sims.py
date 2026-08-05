#!/usr/bin/env python3
"""Guard suite for battery_backup_sims.py -- run END TO END on a synthetic house.

battery_backup_sims.py sits in NEEDS_PRIVATE_ARCHIVE (test_scripts_runnable.py):
CI has no usage.csv/samA.csv/samB.csv, so before this file existed the whole
script -- both the arbitrage sim() loop and the backup endurance() loop -- ran
only on the one machine holding the private archive (issue #44). The two cases
below build a small, hand-computable synthetic house and run the real script
against it with subprocess, so an arithmetic error anywhere in sim() or
endurance() fails here, in CI, rather than only locally.

Design: the script is a monolithic top-level script (no `if __name__`, no
importable functions) that writes BOTH battery_sim.json and
backup_endurance.json from ONE usage.csv + samA.csv + samB.csv every run. The
two halves need INCOMPATIBLE synthetic shapes to stay hand-computable (the
arbitrage half wants ample power at the TOU boundaries; the endurance half
wants a flat, gapless load/production floor across the whole day), so each
case builds its OWN throwaway usage.csv/samA.csv/samB.csv and runs a fresh
subprocess, checking only the one artifact its fixture was designed for.

Class SkipCase matches test_parse_bills.py's typed-exception convention
(CLAUDE.md / issue #44 AC4) -- there is no skip path in this file (both cases
are fully synthetic), but the convention is kept for consistency with the rest
of the suite as SkipCase becomes the house style.
"""
import datetime as dt
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import rates as R  # canonical TOU/DST clock, so the fixture ages with the tariff


class SkipCase(Exception):
    pass


# The script hardcodes this window: end=2026-07-24, the 365 days before it.
END = dt.date(2026, 7, 24)
START = END - dt.timedelta(days=365)   # 2025-07-24, inclusive

def _generator_constants():
    """EXTRACT battery_backup_sims.py's own hardcoded rate constants and its
    `configs` list (cap, discharge_kw, name, charge_kw) directly out of its
    source, by executing the exact lines that declare them, rather than
    hand-copying literals into this file. The generator declares its own
    UDC/CEA/WFNBC/PCIA/NBC (not analysis/rates.py's canonical energy()/
    credit() -- see its own module docstring) and its own per-config charge
    power (CHARGE_KW_PW3/_WITH_EXPANSION, issue #40); a hand-copied constant
    silently drifts out of sync with the generator the moment either changes
    (issue #40 landed a `charge_pwr` 4th tuple element AFTER this suite's
    first draft hardcoded two-tuples -- exactly the drift this function
    exists to make impossible). Executing the generator's OWN lines means
    this test always sees whatever the generator actually computes with."""
    src = (ANALYSIS / "battery_backup_sims.py").read_text()
    # rate constants: exactly the 3 lines between their declaration and the
    # next line, which references the script's own dataframe `d` and would
    # NameError if executed here
    r_start = src.index("WFNBC=0.00591")
    r_end = src.index('\nd["rate"]=')
    # the config list: from its own charge-rating constants (issue #40) up to
    # (not including) the json.dump() call that actually runs sim() -- pure
    # literals, no dataframe/pandas dependency
    c_start = src.index("CHARGE_KW_PW3=5.0")
    c_end = src.index("\njson.dump(")
    ns = {}
    exec(src[r_start:r_end], ns)
    exec(src[c_start:c_end], ns)
    return ns["WFNBC"], ns["PCIA"], ns["NBC"], ns["UDC"], ns["CEA"], ns["configs"]


_WFNBC, _PCIA, _NBC, _UDC, _CEA, GENERATOR_CONFIGS = _generator_constants()


def _rate(s, p):
    return _UDC[s][p] + _WFNBC + _PCIA + _CEA[s][p]


def _season(month):
    return "S" if month in R.SUMMER_MONTHS else "W"


def _write_meter_csv(path, day_row_fn):
    """A Green Button 15-minute export: 13 metadata lines, a header line, then
    one row per (date, hour_frac) slot from rates.expected_day_hours -- the
    same DST-aware slot multiset every other synthetic fixture in this repo
    uses, so spring-forward/fall-back days are shaped correctly without extra
    handling in either case below."""
    head = ["Name,SYNTHETIC FIXTURE", "Address,SYNTHETIC", "Account Number,000000000",
            "Disclaimer,synthetic test fixture - no real data", "Title,CSV Export Electric Meter(s)",
            "Resource,Electric", "Meter Number,09999999", "Interval UOM,Minute(s)",
            f"Reading Start,{START.month}/{START.day}/{START.year} 00:00",
            f"Reading End,{END.month}/{END.day}/{END.year} 23:45",
            "Total Duration,365 Days", "Total Usage,0", "UOM,kWh",
            "Meter Number,Date,Start Time,Duration,Consumption,Generation,Net"]
    rows = []
    d = START
    while d < END:
        for h in R.expected_day_hours(d):
            imp, exp = day_row_fn(d, h)
            hh = int(h)
            mm = int(round((h % 1) * 60))
            ampm = "AM" if hh < 12 else "PM"
            hh12 = hh % 12 or 12
            rows.append(f'"09999999","{d.month}/{d.day}/{d.year}",'
                        f'"{hh12}:{mm:02d} {ampm}","15",'
                        f'"{imp:.6f}","{exp:.6f}","{imp - exp:.6f}"')
        d += dt.timedelta(days=1)
    path.write_text("\n".join(head + rows) + "\n")


def _write_flat_sam(path, value, year):
    """One flat 8760-row Enphase SAM export (the header name is irrelevant --
    the script reads iloc[:, 0])."""
    n = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
    path.write_text("kWh\n" + "".join(f"{value:.6f}\n" for _ in range(n)))


def _run(tmp):
    r = subprocess.run([sys.executable, "battery_backup_sims.py"], cwd=tmp,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"battery_backup_sims.py failed: {r.stderr[-2000:]}"
    return r


# ---------------------------------------------------------------------------
# Case 1: sim() -- the arbitrage half
#
# Fixture: Generation = 1000 kWh/slot (ample) for every slot with hour < 6
# (always "sop" regardless of weekday/weekend/holiday -- rates.period() puts
# hour<6 in "sop" unconditionally); Consumption = 1000 kWh/slot (ample) for
# every slot with 16 <= hour < 21 (always "on", also unconditional). Zero
# everywhere else. Every one of the six configs (max cap 27 kWh, min power
# 3.84 kW) fully charges from empty within the 6-hour window and fully
# discharges within the 5-hour on-peak window every single day (checked
# below), so per config:
#   forgone/day = cap * (rate_sop(season) - NBC)      [sum telescopes to cap]
#   offset/day  = cap * eff * rate_on(season)          [eff = 0.90]
#   grid/day    = 0   (soc never drops below 60% of cap before sop ends)
# summed over the fixture's 153 summer + 212 winter days in the window.
#
# DELIBERATE SCOPE LIMIT (stated explicitly, not left implicit): this fixture
# saturates every config by construction -- Generation/Consumption are always
# "ample" relative to power and capacity, so sim()'s charging/discharging is
# always POWER-limited, never capacity- or supply-limited. Two branches of
# sim() are therefore never exercised by this case: the elif overnight
# grid-top-up-to-60%-of-cap branch (asserted not to fire via
# grid_charge_cost == 0 below, rather than actually driving soc through it),
# and any interval where min(Generation, pwr*step, cap-soc) or
# min(Consumption, pwr*step, soc*eff) is clamped by the FIRST or THIRD
# argument rather than the power term. A defect specifically inside the
# grid-top-up branch or the non-power clamps would not be caught here.
# ---------------------------------------------------------------------------
def case_arbitrage_sim_matches_hand_computation():
    def shape(d, h):
        if h < 6:
            return 0.0, 1000.0        # (imp, exp): pure export/generation
        if 16 <= h < 21:
            return 1000.0, 0.0        # pure import/consumption
        return 0.0, 0.0

    n_summer = sum(1 for i in range((END - START).days)
                   if _season((START + dt.timedelta(days=i)).month) == "S")
    n_winter = (END - START).days - n_summer
    assert n_summer == 153 and n_winter == 212, (n_summer, n_winter)  # pins the window

    forgone_per_cap = (n_summer * (_rate("S", "sop") - _NBC)
                       + n_winter * (_rate("W", "sop") - _NBC))
    offset_per_cap = 0.90 * (n_summer * _rate("S", "on") + n_winter * _rate("W", "on"))

    # (cap, discharge_kw, name, charge_kw) -- read from the generator's own
    # source (see _generator_constants), not hand-copied: issue #40 gave the
    # PW3 configs a charge rate DIFFERENT from their discharge rate, and a
    # hardcoded two-tuple list here would silently check the wrong power for
    # the charge-window assertion below.
    for cap, pwr, _name, charge_kw in GENERATOR_CONFIGS:
        cpwr = pwr if charge_kw is None else charge_kw
        # both windows give every config enough slot-capacity to fully cycle:
        assert cpwr * 0.25 * 24 >= cap, "charge window too short for this fixture"
        assert pwr * 0.25 * 20 >= cap, "discharge window too short for this fixture"

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        shutil.copy(ANALYSIS / "battery_backup_sims.py", tmp / "battery_backup_sims.py")
        shutil.copy(ANALYSIS / "rates.py", tmp / "rates.py")
        _write_meter_csv(tmp / "usage.csv", shape)
        # sim() runs before the SAM/endurance half needs samA/samB, but the
        # script is monolithic -- it always tries to read both files.
        _write_flat_sam(tmp / "samA.csv", 1.0, 2026)
        _write_flat_sam(tmp / "samB.csv", 1.0, 2025)
        _run(tmp)
        got = json.loads((tmp / "battery_sim.json").read_text())

    by_name = {c["config"]: c for c in got}
    names = [name for _cap, _pwr, name, _ckw in GENERATOR_CONFIGS]
    assert set(by_name) == set(names), by_name.keys()
    for cap, pwr, name, _charge_kw in GENERATOR_CONFIGS:
        c = by_name[name]
        exp_forgone = cap * forgone_per_cap
        exp_offset = cap * offset_per_cap
        exp_net = exp_offset - exp_forgone
        # tolerance $2: far tighter than the 10%-of-value defect this suite
        # exists to catch (10% of the smallest total here is >$19).
        assert abs(c["forgone_export_credits"] - exp_forgone) <= 2, (name, c, exp_forgone)
        assert abs(c["onpeak_offset_value"] - exp_offset) <= 2, (name, c, exp_offset)
        assert abs(c["net_annual_savings"] - exp_net) <= 3, (name, c, exp_net)
        assert c["grid_charge_cost"] == 0, (name, c)          # soc never starves
        # cycles: exactly eff (0.90) full cycles/day by construction
        assert abs(c["equiv_full_cycles"] - round(365 * 0.90)) <= 2, (name, c)
    return ("sim() end-to-end on synthetic ample-power days reproduces every "
            "config's forgone/offset/net/cycle figures within $3 of the "
            "hand-computed telescoping sums")


# ---------------------------------------------------------------------------
# Case 2: endurance() -- the backup half
#
# Fixture: usage.csv carries a flat 0.5 kWh/hour import (0.125 kWh/15-min slot)
# and zero export at EVERY hour of every day. samA.csv/samB.csv carry a flat
# 1.0 kWh/hour whole-house load for the full 8760(+leap) hours of both years.
# By the module's own energy-balance identity, prod = load - imp + exp =
# 1.0 - 0.5 + 0 = 0.5 kWh/h, constant, every hour -- and since load (1.0) never
# exceeds the >7 kWh/h EV-detection threshold, nonev = load = 1.0 for every
# hour, so:
#   t1 (essentials, capped 0.7 kW) draws net = min(0.7, pwr) - 0.5 = 0.2 kWh/h
#   t2 (whole house)               draws net = min(1.0, pwr) - 0.5 = 0.5 kWh/h
# both constant, so every one of the 365 calendar starts (at 18:00) drains
# identically: soc depletes by net*1.05 every hour until it would go negative,
# giving an EXACT integer hour count -- median and p10 across 365 identical
# starts both equal that same integer.
# ---------------------------------------------------------------------------
def case_backup_endurance_matches_hand_computation():
    def flat(d, h):
        return 0.125, 0.0   # (imp, exp) per 15-min slot -> 0.5 kWh/h, 0 kWh/h

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        shutil.copy(ANALYSIS / "battery_backup_sims.py", tmp / "battery_backup_sims.py")
        shutil.copy(ANALYSIS / "rates.py", tmp / "rates.py")
        _write_meter_csv(tmp / "usage.csv", flat)
        _write_flat_sam(tmp / "samA.csv", 1.0, 2026)
        _write_flat_sam(tmp / "samB.csv", 1.0, 2025)
        _run(tmp)
        got = json.loads((tmp / "backup_endurance.json").read_text())

    expected = {
        ("IQ 5P", "t1"): 23, ("IQ 5P", "t2"): 9,
        ("IQ 10C", "t1"): 47, ("IQ 10C", "t2"): 19,
        ("PW3", "t1"): 64, ("PW3", "t2"): 25,
        ("PW3+Exp", "t1"): 128, ("PW3+Exp", "t2"): 51,
    }
    assert len(got) == len(expected), got.keys()
    for (cfg, tier), exp_hours in expected.items():
        key = f"{cfg}|{tier}"
        assert key in got, (key, got.keys())
        # every one of the 365 daily starts sees an IDENTICAL flat fixture, so
        # median and p10 across them must both equal the single hand-computed
        # depletion time exactly (no distribution to speak of).
        assert got[key]["median_h"] == exp_hours, (key, got[key], exp_hours)
        assert got[key]["p10_h"] == exp_hours, (key, got[key], exp_hours)
    return ("endurance() end-to-end on a flat synthetic load/production floor "
            "reproduces the exact hand-computed depletion hour for all 4 "
            "configs x 2 tiers")


def _trace_capped_endurance(cap, pwr, cpwr, load, max_steps=24 * 14):
    """Independent re-implementation of endurance()'s inner walk (NOT a call
    into the generator) for a fixture whose hourly whole-house load is a
    constant `load` and whose hourly production alternates 0 (even hour) /
    a huge surplus (odd hour), starting at an even hour (18:00, soc=cap) --
    mirrors case_endurance_solar_recharge_respects_the_charge_cap's fixture
    below. Same documented physics as endurance() (discharge net*1.05,
    capped recharge*0.9, capacity ceiling, break when soc can't cover the
    next discharge) written independently, so an arithmetic bug in the
    generator shows up as a mismatch here rather than agreeing with itself."""
    soc = cap
    t = 0
    even = True  # hour 18 is even -> the daily start is a discharge hour
    while t < max_steps:
        if even:
            net = load  # prod=0 on even hours; load <= pwr by construction, so min(load,pwr)=load
            if soc >= net * 1.05:
                soc -= net * 1.05
            else:
                break
        else:
            soc = min(cap, soc + cpwr * 0.9)  # odd hour: huge surplus, recharge capped at cpwr
        t += 1
        even = not even
    return t


# ---------------------------------------------------------------------------
# Case 3: endurance()'s solar-recharge branch respects the per-config charge
# cap (issue #70). The flat fixture in Case 2 never exercises this branch at
# all (production there is always LESS than load, so net is always positive
# -- pure discharge). This fixture alternates: even hours carry pure load
# (imp = SL, exp = 0, so prod = SL - imp + exp = 0, a discharge hour), odd
# hours carry a huge export (imp = 0, exp = BIG, so prod = SL + BIG, a
# recharge hour whose surplus vastly exceeds any config's charge cap).
# SAM load is held at SL = 7.0 (<= the >7 kWh/h EV-detection threshold, so
# nonev = load = SL exactly, and <= every tested config's discharge power,
# so discharge is never power-limited). Only PW3 and PW3+Exp are asserted --
# the two configs issue #70 actually changed (a real, cited charge rating
# different from their discharge rating); the Enphase configs keep
# charge_pwr=None (symmetric with discharge, unchanged by this fix) and
# aren't part of what this case exists to prove.
# ---------------------------------------------------------------------------
def case_endurance_solar_recharge_respects_the_charge_cap():
    SL = 7.0
    BIG = 1000.0

    def shape(d, h):
        if int(h) % 2 == 0:
            return SL / 4, 0.0
        return 0.0, BIG / 4

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        shutil.copy(ANALYSIS / "battery_backup_sims.py", tmp / "battery_backup_sims.py")
        shutil.copy(ANALYSIS / "rates.py", tmp / "rates.py")
        _write_meter_csv(tmp / "usage.csv", shape)
        _write_flat_sam(tmp / "samA.csv", SL, 2026)
        _write_flat_sam(tmp / "samB.csv", SL, 2025)
        _run(tmp)
        got = json.loads((tmp / "backup_endurance.json").read_text())

    # (cap, discharge_pwr, name, charge_kw) from the generator's own source --
    # same rationale as case_arbitrage_sim_matches_hand_computation above.
    by_name = {name: (cap, pwr, charge_kw) for cap, pwr, name, charge_kw in GENERATOR_CONFIGS}
    for cfg_name in ("1x Tesla Powerwall 3", "PW3 + 1 Expansion"):
        cap, pwr, charge_kw = by_name[cfg_name]
        assert charge_kw is not None, (cfg_name, "expected a distinct charge rating")
        assert SL <= pwr, "fixture assumes discharge is never power-limited"
        exp_hours = _trace_capped_endurance(cap, pwr, charge_kw, SL)
        # sanity: an UNCAPPED recharge (the pre-#70 bug) would snap straight
        # to `cap` every odd hour regardless of `charge_kw` -- since SL*1.05
        # can never exceed a capacity this small in one discharge step, that
        # never breaks, so it would report the full max_steps window instead.
        assert exp_hours < 24 * 14, "fixture failed to distinguish capped from uncapped recharge"
        key = {"1x Tesla Powerwall 3": "PW3", "PW3 + 1 Expansion": "PW3+Exp"}[cfg_name] + "|t2"
        assert key in got, (key, got.keys())
        assert got[key]["median_h"] == exp_hours, (key, got[key], exp_hours)
        assert got[key]["p10_h"] == exp_hours, (key, got[key], exp_hours)
    return ("endurance()'s solar-recharge branch caps hourly charge at the "
            "config's own charge rating instead of absorbing an unbounded "
            "surplus, for both configs with a distinct charge rating (PW3, "
            "PW3+Exp)")


CASES = [
    case_arbitrage_sim_matches_hand_computation,
    case_backup_endurance_matches_hand_computation,
    case_endurance_solar_recharge_respects_the_charge_cap,
]


def main():
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
