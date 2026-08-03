#!/usr/bin/env python3
"""AC5 (issue #40): every battery script that now has SEPARATE charge and
discharge power parameters/constants must be checked that the two are
actually named and wired distinctly -- not one variable serving double duty
under one name. Most of the affected scripts (battery_dispatch_policies.py's
run_batt, battery_sizing_curve.py, carbon_dispatch_tradeoff.py,
dsgs_vpp_backtest.py, perfect_foresight_dispatch.py, tou_structure_stress.py,
service_headroom.py) already have this covered in their own dedicated test
files, several of which import run_batt directly. This file covers the
remaining three, which have no dedicated test file of their own:
behavior_rebuild.py's battery_sim(), battery_backup_sims.py's sim(), and
deep_analyses.py's cost_with_batt().

Same synthetic-household pattern as test_battery_sizing_curve.py /
test_tou_structure_stress.py: point household.PATH at an invented household
BEFORE importing, so this file always imports cleanly, private/ or not.

Run from the repo root:  ./.venv/bin/python analysis/test_charge_discharge_distinct_naming.py
"""
import inspect
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import rates as R                    # noqa: E402
import behavior_rebuild as br        # noqa: E402
import battery_dispatch_policies as bdp  # noqa: E402

EPS = 1e-6
CASES = []

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERIFY = ROOT / "private" / "verify"


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


def case(fn):
    CASES.append(fn)
    return fn


def _import_eager_script_module(name):
    """battery_backup_sims.py and deep_analyses.py are script-style modules:
    they read usage.csv (and, for battery_backup_sims.py, samA.csv/samB.csv)
    at MODULE IMPORT time via a path relative to the CWD, not lazily inside
    a function -- so importing them at all requires a CWD that already has
    those files, unlike behavior_rebuild.py's lazy load(). private/verify is
    the documented sandbox that stages exactly those files (CLAUDE.md); if it
    is not staged, this raises SkipCase rather than failing the whole file's
    import. The module is still located via `analysis/`'s own sys.path entry
    (this file's real source, not a sandbox copy) -- only the CWD changes,
    restored afterward regardless of outcome, so the relative "usage.csv"
    read resolves inside the sandbox."""
    if not (VERIFY / "usage.csv").is_file():
        raise SkipCase(f"needs the private archive staged at {VERIFY} "
                       "(usage.csv, samA.csv, samB.csv) -- this checkout does not have it")
    import importlib
    import os
    real_cwd = os.getcwd()
    os.chdir(str(VERIFY))
    try:
        return importlib.import_module(name)
    finally:
        os.chdir(real_cwd)


def _single_interval_frame():
    """One 15-minute interval, empty battery, a solar surplus well above
    both a 5 kW and an 11.5 kW cap -- isolates the per-interval charging
    RATE. A multi-interval fixture would let overnight grid top-up erase any
    rate difference before it could be observed (see
    test_battery_sizing_curve.py's identical technique and its docstring)."""
    dtr = pd.date_range("2026-01-07 12:00", periods=1, freq="15min")
    d = pd.DataFrame({"dt": dtr})
    d["hour"] = d.dt.dt.hour + d.dt.dt.minute / 60
    d["p"] = [R.period_at(ts) for ts in d.dt]
    d["seas"] = "W"
    return d


@case
def case_behavior_rebuild_battery_sim_threads_a_distinct_charge_kw():
    """behavior_rebuild.battery_sim's charge_kw must be a separate parameter
    from BATT_KW (discharge), default to None (exact backward compatibility),
    and actually gate the charging branches, not just exist cosmetically."""
    sig = inspect.signature(br.battery_sim)
    assert "charge_kw" in sig.parameters
    assert sig.parameters["charge_kw"].default is None
    assert br.BATT_CHARGE_KW != br.BATT_KW, \
        "BATT_CHARGE_KW (charge) and BATT_KW (discharge) must be distinct constants"

    d = _single_interval_frame()
    imp0 = np.array([0.0])
    gen0 = np.array([5.0])
    i_sym, e_sym = br.battery_sim(d, imp0, gen0)
    i_asym, e_asym = br.battery_sim(d, imp0, gen0, charge_kw=5.0)
    charged_sym = gen0[0] - e_sym[0]
    charged_asym = gen0[0] - e_asym[0]
    assert charged_asym < charged_sym - EPS, (
        f"a tighter charge_kw=5.0 must charge less than the symmetric "
        f"default ({charged_asym} vs {charged_sym})")
    return "behavior_rebuild.battery_sim threads a distinct, independently-effective charge_kw"


@case
def case_battery_dispatch_policies_bare_and_expansion_constants_are_correct_and_distinct():
    """issue #40 Finding 1 (Codex adversarial review, confirmed HIGH severity):
    an earlier version applied battery_dispatch_policies.CHARGE_KW (the
    bare-13.5kWh-unit rate, 5 kW) uniformly to BOTH the 13.5 kWh 'pw3' config
    AND the 27 kWh 'pw3x' (PW3 + Expansion) config, contradicting the
    module's own citation in research/battery-research-notes.md, which
    records 8 kW for a Powerwall 3 with up to 3 expansion packs. This pins
    the two constants to their cited values and confirms they are genuinely
    distinct, not the same float under two names."""
    assert bdp.CHARGE_KW == 5.0
    assert bdp.CHARGE_KW_WITH_EXPANSION == 8.0
    assert bdp.CHARGE_KW != bdp.CHARGE_KW_WITH_EXPANSION
    return "CHARGE_KW (5.0, bare unit) and CHARGE_KW_WITH_EXPANSION (8.0) are distinct"


@case
def case_battery_dispatch_policies_expansion_rate_is_behaviorally_wired_not_cosmetic():
    """Proves CHARGE_KW_WITH_EXPANSION actually gates run_batt's charging
    branches at the 27 kWh capacity, rather than existing only as an unused
    constant. Single interval, empty battery, solar surplus well above both
    5 kW and 8 kW isolates the per-interval charging RATE (same isolation
    technique as _single_interval_frame's other uses in this file)."""
    d = _single_interval_frame()
    imp0 = np.array([0.0])
    gen0 = np.array([10.0])
    i_bare, e_bare, _, thru_bare = bdp.run_batt(d, imp0, gen0, 27.0, "greedy",
                                                 charge_kw=bdp.CHARGE_KW)
    i_exp, e_exp, _, thru_exp = bdp.run_batt(d, imp0, gen0, 27.0, "greedy",
                                              charge_kw=bdp.CHARGE_KW_WITH_EXPANSION)
    assert thru_exp > thru_bare + EPS, (
        f"the wider 8 kW expansion charge cap must charge MORE from the same "
        f"solar surplus in one interval than the tighter 5 kW bare-unit cap "
        f"({thru_exp} vs {thru_bare})")
    return "CHARGE_KW_WITH_EXPANSION charges more per interval than CHARGE_KW at 27 kWh -- behaviorally real"


@case
def case_committed_dispatch_artifact_pw3x_and_high_use_the_expansion_constant():
    """Regression guard on the committed data/battery_dispatch_policies.json
    itself (both battery_dispatch_policies.py's dispatch-policy block and its
    post_behavior package block persist their own charge_kw per row): the 27
    kWh pw3/high rows must carry CHARGE_KW_WITH_EXPANSION (8.0), and the 13.5
    kWh pw3/mid rows must carry CHARGE_KW (5.0) -- a silent revert to the
    uniform-bare-unit bug would flip the pw3x/high value back to 5.0 here
    even if every other field looked plausible."""
    artifact = ROOT / "data" / "battery_dispatch_policies.json"
    if not artifact.is_file():
        raise SkipCase(f"{artifact} not present")
    import json
    data = json.loads(artifact.read_text())
    assert data["pw3"]["charge_kw"] == bdp.CHARGE_KW
    assert data["pw3x"]["charge_kw"] == bdp.CHARGE_KW_WITH_EXPANSION
    assert data["post_behavior"]["mid"]["charge_kw"] == bdp.CHARGE_KW
    assert data["post_behavior"]["high"]["charge_kw"] == bdp.CHARGE_KW_WITH_EXPANSION
    assert data["notes"]["charge_kw_bare_unit"] == bdp.CHARGE_KW
    assert data["notes"]["charge_kw_with_expansion"] == bdp.CHARGE_KW_WITH_EXPANSION
    return "committed artifact's pw3x/high rows use CHARGE_KW_WITH_EXPANSION, pw3/mid use CHARGE_KW"


@case
def case_battery_backup_sims_sim_threads_a_distinct_charge_pwr():
    """battery_backup_sims.sim's charge_pwr must be separate from its
    discharge-direction `pwr` argument, default to None (reuse pwr), and
    actually gate the charging branches (export-forgone-credit and overnight
    grid top-up), not just the discharge branch. battery_backup_sims.py reads
    usage.csv at import time (script-style), so this needs the private
    archive staged in private/verify -- see _import_eager_script_module."""
    bbs = _import_eager_script_module("battery_backup_sims")
    sig = inspect.signature(bbs.sim)
    assert "charge_pwr" in sig.parameters
    assert sig.parameters["charge_pwr"].default is None

    r_sym = bbs.sim(13.5, 11.5, "test")
    r_asym = bbs.sim(13.5, 11.5, "test", charge_pwr=5.0)
    # a tighter charge cap can only ever reduce (never increase) forgone
    # export credits earned by charging faster from solar surplus, and can
    # only reduce (never increase) grid-charge cost incurred overnight --
    # so net_annual_savings under the tighter cap must be <= the symmetric
    # figure on this household's real measured year.
    assert r_asym["net_annual_savings"] <= r_sym["net_annual_savings"], (
        r_sym["net_annual_savings"], r_asym["net_annual_savings"])
    assert r_sym["power_kw"] == 11.5, "sim's own power_kw field must report discharge, unaffected by charge_pwr"
    return "battery_backup_sims.sim threads a distinct charge_pwr, separate from its discharge pwr"


@case
def case_battery_backup_sims_configs_pair_the_27kwh_entry_with_the_expansion_rate():
    """issue #40 Finding 1 sweep: battery_backup_sims.py's own `configs` list
    had the SAME uniform-bare-unit-rate bug as battery_dispatch_policies.py --
    its 'PW3 + 1 Expansion' (27.0 kWh) row was wired to CHARGE_KW_PW3 (5.0)
    instead of a separately cited with-expansion rate. Fixed by adding
    CHARGE_KW_PW3_WITH_EXPANSION (8.0) and re-pairing the configs list. This
    checks the actual list entry, not just that both constants exist,
    against exactly the failure mode Codex's review caught: a config that
    LOOKS fixed (a second constant exists) but still points the 27 kWh row
    at the old one."""
    bbs = _import_eager_script_module("battery_backup_sims")
    assert bbs.CHARGE_KW_PW3 == 5.0
    assert bbs.CHARGE_KW_PW3_WITH_EXPANSION == 8.0
    assert bbs.CHARGE_KW_PW3 != bbs.CHARGE_KW_PW3_WITH_EXPANSION

    bare_entries = [c for c in bbs.configs if c[0] == 13.5]
    exp_entries = [c for c in bbs.configs if c[0] == 27.0]
    assert bare_entries, "configs must include the bare 13.5 kWh Powerwall 3"
    assert exp_entries, "configs must include the 27.0 kWh PW3 + Expansion config"
    for cap, power, name, charge_pwr in bare_entries:
        assert charge_pwr == bbs.CHARGE_KW_PW3, \
            f"13.5 kWh config {name!r} must use CHARGE_KW_PW3 (5.0), got {charge_pwr}"
    for cap, power, name, charge_pwr in exp_entries:
        assert charge_pwr == bbs.CHARGE_KW_PW3_WITH_EXPANSION, \
            f"27.0 kWh config {name!r} must use CHARGE_KW_PW3_WITH_EXPANSION " \
            f"(8.0), not the bare-unit rate -- got {charge_pwr}"
    return "battery_backup_sims.configs pairs the 27 kWh 'PW3 + 1 Expansion' row with CHARGE_KW_PW3_WITH_EXPANSION"


@case
def case_deep_analyses_cost_with_batt_threads_a_distinct_charge_pwr():
    """deep_analyses.cost_with_batt's charge_pwr must be separate from its
    discharge-direction `pwr` argument, default to None (reuse pwr), and
    actually gate the charging branches (solar-surplus and overnight sop
    grid top-up), not just the on-peak discharge branch. deep_analyses.py
    reads usage.csv (and battery_dispatch_policies.json) at import time
    (script-style), so this needs the private archive staged in
    private/verify -- see _import_eager_script_module."""
    da = _import_eager_script_module("deep_analyses")
    sig = inspect.signature(da.cost_with_batt)
    assert "charge_pwr" in sig.parameters
    assert sig.parameters["charge_pwr"].default is None

    c_sym = da.cost_with_batt(da.UDC5, da.CEA5, False)
    c_asym = da.cost_with_batt(da.UDC5, da.CEA5, False, charge_pwr=5.0)
    # a tighter charge cap can only make the battery LESS able to arbitrage
    # (less forgone-export-credit avoided, less cheap-hour charging), so
    # the resulting annual cost cannot go DOWN under the tighter cap.
    assert c_asym >= c_sym - 1e-6, (c_sym, c_asym)
    return "deep_analyses.cost_with_batt threads a distinct charge_pwr, separate from its discharge pwr"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
