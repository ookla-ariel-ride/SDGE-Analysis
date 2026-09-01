#!/usr/bin/env python3
"""Guard suite for battery_backup_sims.py -- run END TO END on a synthetic house.

battery_backup_sims.py sits in NEEDS_PRIVATE_ARCHIVE (test_scripts_runnable.py):
CI has no usage.csv/samA.csv/samB.csv, so before this file existed the whole
script -- both the arbitrage sim() loop and the backup endurance() loop -- ran
only on the one machine holding the private archive (issue #44). The two cases
below build a small, hand-computable synthetic house and run the real script
against it with subprocess, so an arithmetic error anywhere in sim() or
endurance() fails here, in CI, rather than only locally.

Design: the script is a monolithic top-level script (it reads its inputs and
runs both simulations at import time; only the publication call sits under
an `if __name__ == "__main__"` guard) that writes both battery_sim.json and
backup_endurance.json from ONE usage.csv + samA.csv + samB.csv every run. The
two halves need INCOMPATIBLE synthetic shapes to stay hand-computable (the
arbitrage half wants ample power at the TOU boundaries; the endurance half
wants a flat, gapless load/production floor across the whole day), so each
case builds its OWN throwaway usage.csv/samA.csv/samB.csv and runs a fresh
subprocess, checking only the one artifact its fixture was designed for. The
pair publisher write_artifacts() (issue #228) is defined above the input load,
so the publication cases below execute exactly those lines of the generator
in-process (_writer, the same source-slice technique _generator_constants
uses) and inject failures into them directly, the way test_tou_audit.py
guards its pair, without needing the archive.

Every fixture is a whole repo-SHAPED root, not a bare directory: since issue
#147 the generator imports behavior_rebuild.py for the intake flag
household.has_ev, and behavior_rebuild.py reads it through household.py, which
locates ROOT/private/household.yaml by walking up for a directory holding both
analysis/ and data/. _stage below builds exactly that, and takes the flag as
an argument, so the same fixture can be run as a household WITH an EV and as
one WITHOUT and the two answers compared.

Class SkipCase matches test_parse_bills.py's typed-exception convention
(CLAUDE.md / issue #44 AC4) -- there is no skip path in this file (both cases
are fully synthetic), but the convention is kept for consistency with the rest
of the suite as SkipCase becomes the house style.
"""
import datetime as dt
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import suite_runner  # noqa: E402
import publish  # the pair publisher the writer must go through (issue #228)
import rates as R  # canonical TOU/DST clock, so the fixture ages with the tariff
import test_scripts_runnable as TSR  # its SYNTH_HOUSEHOLD, the one synthetic intake


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

    def line_start(marker):
        return src.rfind("\n", 0, src.index(marker)) + 1
    # rate constants: exactly the 3 lines between their declaration and the
    # next line, which references the script's own dataframe `d` and would
    # NameError if executed here. The slices start at a line boundary; the run
    # body sits at module level, so dedent is a no-op kept for a future guard.
    r_start = line_start("WFNBC=0.00591")
    r_end = line_start('d["rate"]=')
    # the config list: from its own charge-rating constants (issue #40) up to
    # (not including) the comprehension that actually runs sim() -- pure
    # literals, no dataframe/pandas dependency
    c_start = line_start("CHARGE_KW_PW3=5.0")
    c_end = line_start("sim_rows=[sim(")
    ns = {}
    exec(textwrap.dedent(src[r_start:r_end]), ns)
    exec(textwrap.dedent(src[c_start:c_end]), ns)
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


def _household_yaml(has_ev):
    """test_scripts_runnable.SYNTH_HOUSEHOLD as an EV or a genuinely EV-FREE
    intake -- the same edit test_deep_analyses.py makes, for the same reason:
    behavior_rebuild.py REFUSES a declared charger beside a false
    household.has_ev, so "no EV" is both edits or neither.

    Every edit asserts it took. A str.replace that matched nothing writes the
    file back unchanged, and the no-EV case would then run the EV household
    under a no-EV name and pass for the wrong reason."""
    hh = TSR.SYNTH_HOUSEHOLD
    assert "household:\n  pto_date: 2019-12-01\n" in hh, \
        "SYNTH_HOUSEHOLD's household block no longer has the shape this edit expects"
    assert "charger:\n  kw: 11.5\n" in hh, "SYNTH_HOUSEHOLD no longer declares a charger"
    if has_ev:
        return hh
    hh = hh.replace("household:\n  pto_date: 2019-12-01\n",
                    "household:\n  pto_date: 2019-12-01\n  has_ev: false\n")
    hh = hh.replace("charger:\n  kw: 11.5\n", "")
    assert "has_ev: false" in hh and "charger:" not in hh, hh
    return hh


def _stage(tmp, has_ev=True):
    """One throwaway repo-shaped root battery_backup_sims.py can run in.

    analysis/ and data/ exist only so household.py's _repo_root() resolves
    `tmp` and finds private/household.yaml beside them; the generator and the
    three modules it imports are copied to the root itself, which is the
    sandbox convention the whole repo runs generators under (CLAUDE.md's
    private/verify pattern). The committed sources are copied byte-for-byte --
    no case here patches the generator."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    (tmp / "private").mkdir()
    (tmp / "private" / "household.yaml").write_text(_household_yaml(has_ev))
    for mod in ("battery_backup_sims.py", "rates.py", "household.py",
                "behavior_rebuild.py", "publish.py"):
        shutil.copy(ANALYSIS / mod, tmp / mod)
    return tmp


def _writer():
    """write_artifacts() as the generator defines it, without running the
    generator. Importing battery_backup_sims loads usage.csv/samA.csv/samB.csv
    (script-style, which test_charge_discharge_distinct_naming.py relies on),
    so the header of the file -- its imports and write_artifacts(), everything
    above the `import behavior_rebuild` line that starts the run -- is executed
    into a fresh module object instead. The lines are the generator's own, so
    a writer that stops staging or bypasses publish.promote_set fails here."""
    path = ANALYSIS / "battery_backup_sims.py"
    src = path.read_text()
    end = src.index("\nimport behavior_rebuild as br")
    mod = types.ModuleType("battery_backup_sims_writer")
    mod.__file__ = str(path)
    exec(compile(src[:end], str(path), "exec"), mod.__dict__)
    assert callable(mod.write_artifacts) and mod._publish is publish
    return mod


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
        tmp = _stage(pathlib.Path(td))
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
        tmp = _stage(pathlib.Path(td))
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
        tmp = _stage(pathlib.Path(td))
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


def _ev_heuristic_constants():
    """(threshold, residual) out of the generator's OWN EV-stripping line,
    read from its source rather than retyped here -- the same anti-drift rule
    _generator_constants keeps for the rate tables and the config list.

    The line is `ev=np.where(m["load"]>T,m["load"]-R,0)`: every hour whose
    stitched SAM whole-house load exceeds T kWh is treated as an hour with an
    EV on the charger, and all but R kWh of it is charged to the car."""
    src = (ANALYSIS / "battery_backup_sims.py").read_text()
    m = re.search(r'ev=np\.where\(m\["load"\]>([\d.]+),m\["load"\]-([\d.]+),0\)', src)
    assert m, ("battery_backup_sims.py no longer carries the >N kWh/h EV-stripping "
               "np.where this case is written about; re-derive the fixture below "
               "from whatever replaced it rather than editing this regex to match")
    return float(m.group(1)), float(m.group(2))


def _write_hourly_sam(path, hour_of_day_kwh, year):
    """One 8760-row Enphase SAM export whose value repeats on a 24-hour cycle.

    battery_backup_sims.py indexes these rows with
    pd.date_range("<year>-01-01", periods=8760, freq="h"), so row i is hour
    i % 24 of the local clock -- no DST in the SAM index at all, which is why
    a plain modulo is the right mapping and not an approximation of one."""
    assert len(hour_of_day_kwh) == 24, hour_of_day_kwh
    n = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
    path.write_text("kWh\n" + "".join(f"{hour_of_day_kwh[i % 24]:.6f}\n"
                                      for i in range(n)))


def _trace_zero_prod_endurance(cap, pwr, hour_of_day_kwh, start_hour=18,
                               max_steps=24 * 14):
    """Independent re-implementation of endurance()'s inner walk (NOT a call
    into the generator) for a fixture whose hourly production is ZERO at every
    hour -- so `net` is positive at every step and the walk is a pure drain --
    and whose tier load repeats on a 24-hour cycle. Same documented physics as
    endurance() (discharge net*1.05, break when soc cannot cover the next
    step), written independently, so an arithmetic bug in the generator shows
    up as a mismatch here rather than agreeing with itself."""
    soc = cap
    t = 0
    h = start_hour
    while t < max_steps:
        net = min(hour_of_day_kwh[h % 24], pwr)   # production is 0 at every hour
        if soc >= net * 1.05:
            soc -= net * 1.05
        else:
            break
        t += 1
        h += 1
    return t


# ---------------------------------------------------------------------------
# Case 4: the >7 kWh/h EV strip runs only where the intake says there is an EV
# (issue #147).
#
# Fixture: a whole-house SAM load of BASE = 1.0 kWh/h at every hour except
# 18:00, 19:00 and 20:00, which carry EVENING = 9.0 kWh/h -- above the
# generator's own EV threshold, and on a household with no EV that is an
# ordinary evening: an air conditioner, an oven and a well pump, none of which
# a stitched 8760 can tell apart from a car. usage.csv carries the SAME energy
# as import (load/4 per 15-minute slot) and zero export at every hour, so by
# the module's own identity prod = load - imp + exp = 0 EVERYWHERE: every hour
# of every walk is a discharge hour and nothing ever recharges.
#
# The two readings of t2 the flag chooses between:
#   has_ev false -> nonev = load           = 9.0 at 18/19/20, 1.0 elsewhere
#   has_ev true  -> nonev = load - (load - 1.5) where load > 7
#                                          = 1.5 at 18/19/20, 1.0 elsewhere
# Every walk starts at 18:00 on a full battery, so the no-EV reading meets
# 9.0 kWh in its FIRST hour and the EV reading meets 1.5 -- a PW3 (13.5 kWh)
# lasts 1 h against 11 h, i.e. the strip was overstating this household's
# outage endurance by a factor of eleven.
#
# t1 IS THE CONTROL INSIDE THE FIXTURE: min(nonev, 0.7) is 0.7 at every hour
# under BOTH readings (nonev never drops below 1.0 either way), so t1 must come
# back IDENTICAL. A change that leaked past the flag into the shared `nonev`
# arithmetic would move it.
# ---------------------------------------------------------------------------
def case_endurance_strips_an_ev_only_where_the_intake_declares_one():
    threshold, residual = _ev_heuristic_constants()
    BASE, EVENING = 1.0, 9.0
    PEAK_HOURS = (18, 19, 20)
    assert EVENING > threshold > BASE, (
        f"the fixture must straddle the generator's own {threshold} kWh/h EV "
        f"threshold: BASE={BASE}, EVENING={EVENING}")
    assert 0 < residual < EVENING, (
        f"the generator leaves {residual} kWh/h behind on a stripped hour; this "
        f"fixture assumes a real strip, i.e. something above zero and well below "
        f"its {EVENING} kWh/h evening")

    load = [EVENING if h in PEAK_HOURS else BASE for h in range(24)]
    stripped = [v - (v - residual) if v > threshold else v for v in load]
    assert stripped == [residual if h in PEAK_HOURS else BASE for h in range(24)], stripped
    essentials = [min(v, 0.7) for v in load]
    assert essentials == [min(v, 0.7) for v in stripped], (
        "the fixture's t1 tier is not identical under the two readings, so it "
        "cannot be this case's control")

    def shape(d, h):
        # (imp, exp) per 15-minute slot: the hour's whole load, imported.
        return load[int(h)] / 4, 0.0

    got = {}
    for has_ev in (False, True):
        with tempfile.TemporaryDirectory() as td:
            tmp = _stage(pathlib.Path(td), has_ev=has_ev)
            _write_meter_csv(tmp / "usage.csv", shape)
            _write_hourly_sam(tmp / "samA.csv", load, 2026)
            _write_hourly_sam(tmp / "samB.csv", load, 2025)
            _run(tmp)
            got[has_ev] = json.loads((tmp / "backup_endurance.json").read_text())

    key_for = {"1x Tesla Powerwall 3": "PW3", "PW3 + 1 Expansion": "PW3+Exp",
               "1x Enphase IQ 5P": "IQ 5P", "1x Enphase IQ 10C": "IQ 10C"}
    seen = {}
    for cap, pwr, name, _charge_kw in GENERATOR_CONFIGS:
        cfg = key_for.get(name)
        if cfg is None:            # a config sim() prices but endurance() does not run
            continue
        exp_house_no_ev = _trace_zero_prod_endurance(cap, pwr, load)
        exp_house_ev = _trace_zero_prod_endurance(cap, pwr, stripped)
        exp_essentials = _trace_zero_prod_endurance(cap, pwr, essentials)
        assert exp_house_ev > exp_house_no_ev, (
            f"{cfg}: the fixture does not distinguish the two readings at all")

        # THE DEFECT: t2 on a household with no EV is the WHOLE load.
        house_no_ev = got[False][f"{cfg}|t2"]
        assert house_no_ev["median_h"] == exp_house_no_ev, (
            f"{cfg}|t2 on a household whose intake says household.has_ev is false "
            f"reports {house_no_ev['median_h']} h of outage endurance where the "
            f"whole-home load ({EVENING} kWh/h at {PEAK_HOURS}, {BASE} elsewhere) "
            f"gives {exp_house_no_ev} h -- the >{threshold} kWh/h EV strip is still "
            f"running and is charging this house's evening load to a car it does "
            f"not own")
        assert house_no_ev["p10_h"] == exp_house_no_ev, (cfg, house_no_ev)

        # THE POSITIVE CONTROL: an EV household still gets the heuristic.
        house_ev = got[True][f"{cfg}|t2"]
        assert house_ev["median_h"] == exp_house_ev, (
            f"{cfg}|t2 on a household WITH an EV no longer strips the "
            f">{threshold} kWh/h hours: {house_ev} against {exp_house_ev} h")
        assert house_ev["p10_h"] == exp_house_ev, (cfg, house_ev)

        # THE CONTROL INSIDE THE FIXTURE: t1 is the same tier either way.
        assert got[False][f"{cfg}|t1"] == got[True][f"{cfg}|t1"], (
            f"{cfg}|t1 moved with the intake flag, but min(nonev, 0.7) is 0.7 at "
            f"every hour of this fixture under both readings: "
            f"{got[False][f'{cfg}|t1']} vs {got[True][f'{cfg}|t1']}")
        assert got[False][f"{cfg}|t1"]["median_h"] == exp_essentials, (
            cfg, got[False][f"{cfg}|t1"], exp_essentials)
        seen[cfg] = (exp_house_no_ev, exp_house_ev)

    assert set(got[False]) == set(got[True]), (sorted(got[False]), sorted(got[True]))
    return ("endurance()'s whole-house tier counts the WHOLE load on a household "
            "whose intake says household.has_ev is false, and still strips the "
            f">{threshold} kWh/h hours on one that has an EV -- median t2 hours "
            "(no EV vs EV): "
            + ", ".join(f"{c}: {a} vs {b}" for c, (a, b) in sorted(seen.items()))
            + "; the essentials tier is identical either way")


# ---------------------------------------------------------------------------
# Cases 5 and 6: write_artifacts() -- the pair publisher (issue #228)
#
# Before #228 the script opened each destination directly for a truncating
# json.dump, with the whole 8760-hour endurance simulation between the two, so
# a failure anywhere in that window left battery_sim.json freshly rewritten
# beside a stale backup_endurance.json, and a failure inside a dump left the
# destination itself truncated: json.load on it raised instead of returning
# last run's answer. These two cases pin the guarantee the writer now states.
# ---------------------------------------------------------------------------
SIM_ROWS = [{"config": "new", "usable_kwh": 1.0, "net_annual_savings": 2}]
ENDURANCE = {"new|t1": {"median_h": 3, "p10_h": 4}}


class _SpyOS:
    """`os`, with replace() recording who called it and which temp files were
    on disk when it ran. `who` names the namespace the spy was installed in, so
    a rename the writer performs itself is told apart from one publish.py
    performs on its behalf."""

    def __init__(self, inner, log, watch, who):
        self._inner, self._log, self._watch, self._who = inner, log, watch, who

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def replace(self, src, dst):
        self._log.append((self._who, pathlib.Path(src).name,
                          sorted(p.name for p in self._watch.iterdir()
                                 if ".tmp" in p.name)))
        return self._inner.replace(src, dst)


def case_no_destination_is_replaced_until_both_temps_exist():
    """The promotion order itself, observed at the os.replace level (issue
    #228): both temporary files must be complete before the first rename of the
    run, and every rename must be publish.promote_set's, not the writer's own.
    A writer that dumped straight into each destination would never rename at
    all; one that promoted each file as it finished would arrive at its first
    rename holding one staged temp; and one that staged both and then ran its
    own rename loop would rename from its own namespace, which is the
    hand-rolled protocol issue #227/#228 retire."""
    B = _writer()
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td)
        seen = []
        real_os, real_pub_os = B.os, B._publish.os
        B.os = _SpyOS(real_os, seen, dest, "writer")
        B._publish.os = _SpyOS(real_pub_os, seen, dest, "publish")
        try:
            B.write_artifacts(SIM_ROWS, ENDURANCE, dest=dest)
        finally:
            B.os, B._publish.os = real_os, real_pub_os
        assert seen, "nothing was renamed, so nothing was published"
        assert len(seen[0][2]) == 2, (
            f"the first rename of the run ran with {seen[0][2]} staged -- both "
            "temporary files must exist before any destination is touched")
        own = [s for s in seen if s[0] != "publish"]
        assert not own, (
            f"the writer renamed on its own instead of through "
            f"publish.promote_set: {own}")
        assert sorted(p.name for p in dest.iterdir()
                      if not p.name.startswith(".")) == [
            "backup_endurance.json", "battery_sim.json"], sorted(dest.iterdir())
        assert json.loads((dest / "battery_sim.json").read_text()) == SIM_ROWS
        assert json.loads((dest / "backup_endurance.json").read_text()) == ENDURANCE
    return "no destination is renamed until both temporary files are written"


class _BoomOnSecondDump:
    """`json`, whose second dump() of the run fails after writing part of its
    output, the way a full filesystem, a quota or an unserializable value does.
    The first dump (battery_sim.json) is the real one, so its temp is complete."""

    def __init__(self, inner):
        self._inner, self.calls = inner, 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def dump(self, obj, fh, **kw):
        self.calls += 1
        if self.calls < 2:
            return self._inner.dump(obj, fh, **kw)
        fh.write("{")                     # a partial document lands ...
        raise RuntimeError("injected: the second dump failed part-way")


def case_failure_during_the_second_dump_touches_neither_artifact():
    """A failure while backup_endurance.json is being serialized leaves both
    committed artifacts unchanged and still parseable (issue #228). Under the
    direct truncating writes this fires with battery_sim.json already
    rewritten and backup_endurance.json truncated to a partial document."""
    B = _writer()
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td)
        prior = {"battery_sim.json": '[\n {\n  "config": "prior"\n }\n]',
                 "backup_endurance.json": '{\n "prior|t1": {\n  "median_h": 1\n }\n}'}
        for name, text in prior.items():
            (dest / name).write_text(text)

        real_json = B.json
        boom = _BoomOnSecondDump(real_json)
        B.json = boom
        try:
            B.write_artifacts(SIM_ROWS, ENDURANCE, dest=dest)
        except RuntimeError as e:
            assert "injected" in str(e), e
        except SystemExit as e:                       # pragma: no cover - guard
            raise AssertionError(f"the writer converted the failure: {e}")
        else:
            raise AssertionError("the writer swallowed the injected failure")
        finally:
            B.json = real_json
        assert boom.calls == 2, f"the injection never reached the second dump ({boom.calls})"

        for name, text in prior.items():
            got = (dest / name).read_text()
            assert got == text, (
                f"{name} was changed by a run that failed before the pair was "
                f"complete: {got[:80]!r}")
            json.loads(got)               # and it still parses
        strays = sorted(p.name for p in dest.iterdir()
                        if ".tmp" in p.name or ".bak" in p.name)
        assert not strays, f"a failed run left files behind: {strays}"

        # POSITIVE CONTROL: the same writer, the same destination, no injection.
        B.write_artifacts(SIM_ROWS, ENDURANCE, dest=dest)
        assert json.loads((dest / "battery_sim.json").read_text()) == SIM_ROWS
        assert json.loads((dest / "backup_endurance.json").read_text()) == ENDURANCE
        assert sorted(p.name for p in dest.iterdir()
                      if not p.name.startswith(".")) == [
            "backup_endurance.json", "battery_sim.json"], sorted(dest.iterdir())
    return "a failure during the second dump leaves both artifacts unchanged and parseable"


def case_a_leftover_recovery_copy_makes_the_writer_refuse_and_leave_no_temps():
    """publish.promote_set refuses to start over a leftover .bak (the only good
    copy after a failed rollback; test_publish.py). Two things are pinned: the
    refusal reaches this writer at all, which only a writer that promotes
    through promote_set can show, and a refused run leaves no complete
    temporaries beside the untouched pair, because the promotion sits inside
    the writer's cleanup try (issue #228)."""
    B = _writer()
    with tempfile.TemporaryDirectory() as td:
        dest = pathlib.Path(td)
        prior = {"battery_sim.json": '[\n {\n  "config": "prior"\n }\n]',
                 "backup_endurance.json": '{\n "prior|t1": {\n  "median_h": 1\n }\n}'}
        for name, text in prior.items():
            (dest / name).write_text(text)
        bak = dest / "battery_sim.json.bak99999"
        bak.write_text("the only good copy\n")
        try:
            B.write_artifacts(SIM_ROWS, ENDURANCE, dest=dest)
            raise AssertionError("the writer published over a leftover recovery copy")
        except SystemExit as e:
            assert "Recover it by hand" in str(e), e
        for name, text in prior.items():
            assert (dest / name).read_text() == text, f"{name} was touched by a refused run"
        assert bak.read_text() == "the only good copy\n", "the recovery copy was consumed"
        strays = sorted(p.name for p in dest.iterdir() if ".tmp" in p.name)
        assert not strays, f"a refused run left complete temporaries behind: {strays}"
    return "a leftover recovery copy makes the writer refuse, touching nothing and leaving no temps"


CASES = [
    case_arbitrage_sim_matches_hand_computation,
    case_backup_endurance_matches_hand_computation,
    case_endurance_solar_recharge_respects_the_charge_cap,
    case_endurance_strips_an_ev_only_where_the_intake_declares_one,
    case_no_destination_is_replaced_until_both_temps_exist,
    case_failure_during_the_second_dump_touches_neither_artifact,
    case_a_leftover_recovery_copy_makes_the_writer_refuse_and_leave_no_temps,
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
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(case, e)
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
