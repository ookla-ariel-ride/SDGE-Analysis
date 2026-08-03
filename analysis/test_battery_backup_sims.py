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

# battery_backup_sims.py's OWN hardcoded rate constants (local to the script,
# not analysis/rates.py -- it declares its own UDC/CEA/WFNBC/PCIA/NBC). Copied
# here as the basis for hand-computing the expected arbitrage totals; if the
# script's own constants ever change this copy must be updated to match, same
# as any other hand-derived fixture in this suite.
_WFNBC = 0.00591
_PCIA = 0.02828
_NBC = 0.01515 - 0.00007 + _WFNBC
_UDC = {"S": {"on": 0.31711, "off": 0.31711, "sop": 0.04114},
        "W": {"on": 0.31711, "off": 0.31711, "sop": 0.04114}}
_CEA = {"S": {"on": 0.51684, "off": 0.15975, "sop": 0.04961},
        "W": {"on": 0.24430, "off": 0.15782, "sop": 0.05187}}


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
            f"Reading End,{END.month}/{END.day}/{END.year - 1 if False else END.year} 23:45",
            f"Total Duration,365 Days", "Total Usage,0", "UOM,kWh",
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

    configs = [(5.0, 3.84), (10.0, 7.08), (13.5, 11.5),
               (15.0, 7.68), (20.0, 7.08), (27.0, 11.5)]
    for cap, pwr in configs:
        # both windows give every config enough slot-capacity to fully cycle:
        assert pwr * 0.25 * 24 >= cap, "charge window too short for this fixture"
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
    names = ["1x Enphase IQ 5P", "1x Enphase IQ 10C", "1x Tesla Powerwall 3",
             "3x Enphase IQ 5P", "2x Enphase IQ 10C", "PW3 + 1 Expansion"]
    assert set(by_name) == set(names), by_name.keys()
    for name, (cap, pwr) in zip(names, configs):
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


CASES = [
    case_arbitrage_sim_matches_hand_computation,
    case_backup_endurance_matches_hand_computation,
]


def main():
    ran = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
