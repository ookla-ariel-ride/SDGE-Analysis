#!/usr/bin/env python3
"""Guard suite for deep_analyses.py -- run END TO END on a synthetic house.

deep_analyses.py sits in NEEDS_PRIVATE_ARCHIVE (test_scripts_runnable.py): CI
has no usage.csv/samA.csv/samB.csv, so before this file existed its five
sections (TOU-DR-P wildcard, phantom load, EV sessions, vacation detection,
Monte Carlo battery ROI) ran only on the machine holding the private archive
(issue #44). The case below runs the real script end to end against a small,
mostly hand-computable synthetic house, so an arithmetic error anywhere in its
main path fails here rather than only locally.

Hand-verified exactly: the phantom-load floor, the EV-session aggregates
(count/kwh/cost -- built from one clean recurring nightly charging block), and
the Monte Carlo battery-ROI block (fully decoupled from usage.csv -- it reads
only this run's battery_dispatch_policies.json and a fixed RNG seed, so an
independent transcription of the same published formula serves as an exact
oracle). The wildcard and vacation sections are only checked structurally
(present, JSON-serializable, internally consistent) -- their day-selection
logic ties on a flat fixture by construction, so they are not meant to be
exact here.

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

import numpy as np

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import rates as R


class SkipCase(Exception):
    pass


END = dt.date(2026, 7, 24)
START = END - dt.timedelta(days=365)

def _generator_constants():
    """EXTRACT deep_analyses.py's own hardcoded WFNBC/PCIA/NBC/BSC/UDC5/CEA5
    directly out of its source (executing the exact declaring line, not
    hand-copying literals into this file) -- the same drift risk
    test_battery_backup_sims.py's identical pattern documents: the generator
    declares its own rate table, not analysis/rates.py's canonical one, and a
    hand-copied number silently stops matching the moment the generator's own
    constant changes."""
    src = (ANALYSIS / "deep_analyses.py").read_text()
    line = src[src.index("WFNBC=0.00591"):src.index("\nout={}")]
    ns = {}
    exec(line, ns)
    udc5_cea5 = src[src.index('UDC5={"S":'):src.index("\ndef rates(")]
    exec(udc5_cea5, ns)
    return ns["WFNBC"], ns["PCIA"], ns["UDC5"], ns["CEA5"]


_WFNBC, _PCIA, _UDC5, _CEA5 = _generator_constants()


def _rate_sop(season):
    return _UDC5[season]["sop"] + _WFNBC + _PCIA + _CEA5[season]["sop"]


def _rate_off(season):
    return _UDC5[season]["off"] + _WFNBC + _PCIA + _CEA5[season]["off"]


def _season(month):
    return "S" if month in R.SUMMER_MONTHS else "W"


# ---------------------------------------------------------------------------
# Fixture: three disjoint, hand-designed daily windows, DST-safe (26/06 US DST
# transitions only ever touch 01:00-03:00, so none of these three windows is
# ever shortened or duplicated by a spring-forward/fall-back day):
#   [3, 5)   -- phantom-load probe: flat 0.3 kWh/slot every night, every day
#   [22, 24) -- one clean recurring EV-charger block every night, 2.0 kWh/slot
#   everywhere else -- flat 0.15 kWh/slot baseline (never trips the >6.5 kW EV
#   gate, never exceeds the phantom probe's <=0.5 kWh clean threshold)
# All Generation is zero (no solar) -- keeps the un-verified wildcard section
# from depending on anything this fixture does not control.
# ---------------------------------------------------------------------------
PHANTOM_KWH = 0.3
EV_KWH = 2.0
BASE_KWH = 0.15


def _shape(h):
    if 3.0 <= h < 5.0:
        return PHANTOM_KWH
    if 22.0 <= h < 24.0:
        return EV_KWH
    return BASE_KWH


def _write_meter_csv(path):
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
            imp = _shape(h)
            hh, mm = int(h), int(round((h % 1) * 60))
            ampm = "AM" if hh < 12 else "PM"
            hh12 = hh % 12 or 12
            rows.append(f'"09999999","{d.month}/{d.day}/{d.year}",'
                        f'"{hh12}:{mm:02d} {ampm}","15",'
                        f'"{imp:.6f}","0.000000","{imp:.6f}"')
        d += dt.timedelta(days=1)
    path.write_text("\n".join(head + rows) + "\n")


def _write_flat_sam(path, value, year):
    n = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
    path.write_text("kWh\n" + "".join(f"{value:.6f}\n" for _ in range(n)))


BASE_SAVE = 1500.0    # this run's battery_dispatch_policies.json marginal


def _monte_carlo_oracle(base_save):
    """Exact transcription of deep_analyses.py's Monte Carlo block (same
    formula, same fixed seed) -- an independent oracle because it encodes the
    PUBLISHED algorithm, not a copy blessed by running the generator."""
    rng = np.random.default_rng(42)
    N = 5000
    esc = rng.uniform(0.00, 0.10, N)
    fade = rng.uniform(0.005, 0.025, N)
    price = rng.uniform(12500, 17000, N)
    payback = np.full(N, np.nan)
    npv10 = np.zeros(N)
    for i in range(N):
        cum = 0
        for yr in range(1, 26):
            s_yr = base_save * ((1 + esc[i]) ** (yr - 1)) * ((1 - fade[i]) ** (yr - 1))
            cum += s_yr
            if np.isnan(payback[i]) and cum >= price[i]:
                payback[i] = yr - 1 + (price[i] - (cum - s_yr)) / s_yr
            if yr <= 10:
                npv10[i] += s_yr / (1.04 ** yr)
    return {
        "payback_median": round(float(np.nanmedian(payback)), 1),
        "payback_p10": round(float(np.nanpercentile(payback, 10)), 1),
        "payback_p90": round(float(np.nanpercentile(payback, 90)), 1),
        "prob_payback_within_warranty10yr": round(float(np.mean(payback <= 10)), 3),
        "npv10_at_4pct_median": round(float(np.median(npv10) - np.median(price))),
    }


def case_deep_analyses_end_to_end_matches_hand_and_oracle_computations():
    n_summer = sum(1 for i in range((END - START).days)
                   if _season((START + dt.timedelta(days=i)).month) == "S")
    n_winter = (END - START).days - n_summer
    assert n_summer == 153 and n_winter == 212, (n_summer, n_winter)

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "analysis").mkdir()
        (tmp / "data").mkdir()          # so _repo_root() resolves tmp as root
        for name in ("deep_analyses.py", "rates.py"):
            shutil.copy(ANALYSIS / name, tmp / name)
        _write_meter_csv(tmp / "usage.csv")
        _write_flat_sam(tmp / "samA.csv", 0.2, 2026)
        _write_flat_sam(tmp / "samB.csv", 0.2, 2025)
        (tmp / "battery_dispatch_policies.json").write_text(
            json.dumps({"post_behavior": {"mid": {"battery_marginal": BASE_SAVE}}}))

        r = subprocess.run([sys.executable, "deep_analyses.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f"deep_analyses.py failed: {r.stderr[-2000:]}"
        got = json.loads((tmp / "deep_results.json").read_text())

    # ---- phantom load: constant 0.3 kWh/slot in the clean night window -----
    ph = got["phantom"]
    assert abs(ph["baseload_kw"] - PHANTOM_KWH * 4) < 1e-6, ph
    exp_annual_kwh = round(PHANTOM_KWH * 4 * 8760)
    assert abs(ph["annual_kwh"] - exp_annual_kwh) <= 1, ph
    assert abs(ph["annual_cost_at_blend"] - round(PHANTOM_KWH * 4 * 8760 * 0.20)) <= 1, ph

    # ---- EV sessions: 365 identical nightly blocks, hand-computed exactly --
    ev = got["ev_sessions"]
    assert ev["count"] == 365, ev
    per_session_kwh = EV_KWH * 8 - 0.4 * 8 * 0.25   # 16.0 - 0.8 = 15.2
    exp_kwh_total = round(365 * per_session_kwh)
    assert abs(ev["kwh_total"] - exp_kwh_total) <= 1, ev
    assert abs(ev["avg_kwh"] - round(per_session_kwh, 1)) < 0.05, ev
    # the block sits at 22:00-23:45, never inside 16:00-21:00 ("on") or
    # labelled "off" (hour<6-style sop only applies elsewhere, but 22-24 is
    # "off" under rates.period -- NOT on-peak, and it is never "sop" either,
    # so on_kwh must be exactly zero and off_kwh must equal the whole session)
    assert ev["onpeak_kwh_in_sessions"] == 0, ev
    assert ev["sessions_touching_onpeak"] == 0, ev
    # "cost" sums RAW Consumption * rate (NOT the baseline-adjusted "kwh"
    # field) -- 22:00-23:45 is "off" under rates.period (never "on", never
    # "sop": h>=14 rules both the weekend and holiday branches out too), so
    # this uses the OFF entries of the script's own UDC5/CEA5 tables.
    raw_session_kwh = EV_KWH * 8   # 16.0, unadjusted
    exp_cost_total = (n_summer * raw_session_kwh * _rate_off("S")
                      + n_winter * raw_session_kwh * _rate_off("W"))
    assert abs(ev["cost_total"] - round(exp_cost_total)) <= 2, (ev, exp_cost_total)
    exp_cost_if_all_sop = round(exp_kwh_total * 0.1257)
    assert abs(ev["cost_if_all_sop"] - exp_cost_if_all_sop) <= 1, ev
    assert abs(ev["wasted_vs_perfect"]
               - round(exp_cost_total - exp_kwh_total * 0.1257)) <= 2, ev

    # ---- Monte Carlo: independent oracle transcription, same seed/inputs ---
    oracle = _monte_carlo_oracle(BASE_SAVE)
    mc = got["monte_carlo"]
    for k, v in oracle.items():
        assert mc[k] == v, (k, mc[k], v)

    # ---- structural checks on the two unverified sections ------------------
    assert "wildcard" in got and set(got["wildcard"]) == {
        "TOU-DR-P + PW3 (15 events dodged)", "EV-TOU-5 + PW3",
        "TOU-DR-P no battery (events hit)"}, got["wildcard"]
    assert "vacation" in got and got["vacation"]["away_days_detected"] >= 0, got["vacation"]
    assert json.dumps(got), "deep_results.json content is not JSON-serializable"
    return ("deep_analyses.py runs end to end on a synthetic house; phantom "
            "load, EV-session aggregates and the Monte Carlo battery-ROI "
            "block all match hand/oracle computations")


CASES = [case_deep_analyses_end_to_end_matches_hand_and_oracle_computations]


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
