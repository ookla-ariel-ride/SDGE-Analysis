#!/usr/bin/env python3
"""Guard suite for lifetime_payback.py -- run derive_blended() END TO END on a
synthetic house.

lifetime_payback.py sits in NEEDS_PRIVATE_ARCHIVE (test_scripts_runnable.py):
CI has no usage.csv/samA.csv/samB.csv, so before this file existed
derive_blended() -- the energy-balance production derivation and the two full
NEM billing runs it depends on -- ran only on the machine holding the private
archive (issue #44). The case below runs the real script end to end (as a
subprocess, since importing the module requires a private/household.yaml at
import time) against a small, fully hand-computable synthetic house.

Design: all import is confined to the two windows where the OLD and NEW TOU
structures agree (0-6 "sop", 16-21 "on"), so blended_old and blended_new are
expected to come out equal here -- this fixture does NOT exercise the
March/April 10-14 old-structure carve-out (_per_old), only the shared
production/netting/energy-balance machinery. That narrower scope is stated
explicitly rather than implied.

SkipCase matches test_parse_bills.py's typed-exception convention (issue #44
AC4); there is no skip path in this file since the fixture is fully synthetic.
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
import rates as R


class SkipCase(Exception):
    pass


WINDOW_END = dt.date(2026, 7, 24)
WINDOW_START = WINDOW_END - dt.timedelta(days=365)

C_SOP = 1.0   # kWh per 15-min slot, hours [0, 6)  -- always "sop", both structures
C_ON = 1.0    # kWh per 15-min slot, hours [16, 21) -- always "on", both structures


def _season(month):
    return "S" if month in R.SUMMER_MONTHS else "W"


def _shape(h):
    if h < 6.0:
        return C_SOP
    if 16.0 <= h < 21.0:
        return C_ON
    return 0.0


def _write_meter_csv(path):
    head = ["Name,SYNTHETIC FIXTURE", "Address,SYNTHETIC", "Account Number,000000000",
            "Disclaimer,synthetic test fixture - no real data", "Title,CSV Export Electric Meter(s)",
            "Resource,Electric", "Meter Number,09999999", "Interval UOM,Minute(s)",
            f"Reading Start,{WINDOW_START.month}/{WINDOW_START.day}/{WINDOW_START.year} 00:00",
            f"Reading End,{WINDOW_END.month}/{WINDOW_END.day}/{WINDOW_END.year} 23:45",
            "Total Duration,365 Days", "Total Usage,0", "UOM,kWh",
            "Meter Number,Date,Start Time,Duration,Consumption,Generation,Net"]
    rows = []
    d = WINDOW_START
    while d < WINDOW_END:
        for h in R.expected_day_hours(d):
            imp = _shape(h)
            hh, mm = int(h), int(round((h % 1) * 60))
            ampm = "AM" if hh < 12 else "PM"
            hh12 = hh % 12 or 12
            rows.append(f'"09999999","{d.month}/{d.day}/{d.year}",'
                        f'"{hh12}:{mm:02d} {ampm}","15",'
                        f'"{imp:.6f}","0.000000","{imp:.6f}"')
        d += dt.timedelta(days=1)
    path.write_text("\n".join(head + rows) + "\n")


def _write_flat_sam_double(usage_hourly_by_hour, path, year):
    """One flat-shaped 8760-row SAM export: 2x the corresponding meter import
    at every hour of the fixture's daily cycle (0 outside the window's own
    year isn't needed here -- SAM covers the WHOLE calendar year, and only
    the [WINDOW_START, WINDOW_END) slice is read, so hours outside the window
    but inside this calendar year get the same repeating daily shape; it is
    never read outside the window)."""
    n = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
    base = dt.datetime(year, 1, 1)
    vals = []
    for i in range(n):
        ts = base + dt.timedelta(hours=i)
        hourly_imp = usage_hourly_by_hour(ts.hour)
        vals.append(2.0 * hourly_imp)
    path.write_text("kWh\n" + "".join(f"{v:.6f}\n" for v in vals))


def _hourly_import(hour):
    """Hourly (4-slot) import total for a given clock hour, matching _shape."""
    return sum(_shape(hour + q * 0.25) for q in range(4))


SYNTH_HOUSEHOLD = """solar:
  install_invoice_usd: 20000
  install_paid_date: "2020-01-01"
household:
  pto_date: "2020-01-01"
"""


def case_derive_blended_matches_hand_computation():
    n_summer = sum(1 for i in range((WINDOW_END - WINDOW_START).days)
                   if _season((WINDOW_START + dt.timedelta(days=i)).month) == "S")
    n_winter = (WINDOW_END - WINDOW_START).days - n_summer
    assert n_summer == 153 and n_winter == 212, (n_summer, n_winter)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "analysis").mkdir()
        (tmp / "data").mkdir()
        (tmp / "private").mkdir()
        for name in ("lifetime_payback.py", "rates.py", "household.py"):
            shutil.copy(ANALYSIS / name, tmp / name)
            shutil.copy(ANALYSIS / name, tmp / "analysis" / name)
        (tmp / "private" / "household.yaml").write_text(SYNTH_HOUSEHOLD)
        _write_meter_csv(tmp / "usage.csv")
        _write_flat_sam_double(_hourly_import, tmp / "samB.csv", 2025)
        _write_flat_sam_double(_hourly_import, tmp / "samA.csv", 2026)

        r = subprocess.run([sys.executable, "lifetime_payback.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f"lifetime_payback.py failed: {r.stderr[-2000:]}"
        assert "derived from data" in r.stdout, r.stdout[-500:]
        out = json.loads((tmp / "data" / "lifetime_payback.json").read_text())

    # ---- hand computation, using rates.py's OWN canonical energy() as the --
    # ---- single source of truth for $/kWh (not re-declared here) ----------
    imp_sop_S, imp_sop_W = n_summer * 24 * C_SOP, n_winter * 24 * C_SOP
    imp_on_S, imp_on_W = n_summer * 20 * C_ON, n_winter * 20 * C_ON
    total_imp = imp_sop_S + imp_sop_W + imp_on_S + imp_on_W
    bill_var = (imp_sop_S * R.energy("S", "sop") + imp_sop_W * R.energy("W", "sop")
               + imp_on_S * R.energy("S", "on") + imp_on_W * R.energy("W", "on")
               + total_imp * R.NBC)
    bsc_total = 365 * R.BSC
    bill_df = bsc_total + bill_var             # actual (with-solar) bill
    bill_L = bsc_total + 2 * bill_var           # no-solar counterfactual (2x import)
    exp_production = total_imp                  # load(=2x imp) - imp + 0 = imp
    exp_blended = bill_var / exp_production      # (bill_L - bill_df) / production

    assert abs(out["annual_production_kwh"] - round(exp_production)) <= 1, out
    assert abs(out["nosolar_bill_usd"] - round(bill_L)) <= 1, out
    # both structures agree everywhere this fixture puts import, by design
    assert abs(out["blended_old_tou"] - round(exp_blended, 4)) < 0.0015, out
    assert abs(out["blended_new_tou"] - round(exp_blended, 4)) < 0.0015, out
    assert out["invoice_usd"] == 20000, out
    assert json.dumps(out), "lifetime_payback.json is not JSON-serializable"
    return ("lifetime_payback.py's derive_blended() runs end to end on a "
            "synthetic house and its production/netting/blended-rate figures "
            "match the hand computation against rates.py's own energy()")


CASES = [case_derive_blended_matches_hand_computation]


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
