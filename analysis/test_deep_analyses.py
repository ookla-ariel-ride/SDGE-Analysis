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
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ANALYSIS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ANALYSIS))
import suite_runner  # noqa: E402
import rates as R
import test_scripts_runnable as TSR   # the proven synthetic-household fixture
import report_tokens as RT           # the reader that enforces the wildcard key convention


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


def _house_base_kw():
    """deep_analyses.py's EV_SESSION_HOUSE_BASE_KW, exec'd out of its own
    source for the same reason _generator_constants() does: a hand-copied
    0.4 here would keep the hand computations below agreeing with a generator
    whose base had moved, for the wrong reason."""
    src = (ANALYSIS / "deep_analyses.py").read_text()
    line = src[src.index("EV_SESSION_HOUSE_BASE_KW="):]
    line = line[:line.index("\n")]
    ns = {}
    exec(line, ns)
    return float(ns["EV_SESSION_HOUSE_BASE_KW"])


_HOUSE_BASE_KW = _house_base_kw()


def _rate_sop(season):
    return _UDC5[season]["sop"] + _WFNBC + _PCIA + _CEA5[season]["sop"]


def _rate_off(season):
    return _UDC5[season]["off"] + _WFNBC + _PCIA + _CEA5[season]["off"]


def _rate_on(season):
    return _UDC5[season]["on"] + _WFNBC + _PCIA + _CEA5[season]["on"]


def _season(month):
    return "S" if month in R.SUMMER_MONTHS else "W"


# ---------------------------------------------------------------------------
# Fixture: three disjoint, hand-designed daily windows, DST-safe (26/06 US DST
# transitions only ever touch 01:00-03:00, so none of these three windows is
# ever shortened or duplicated by a spring-forward/fall-back day):
#   [3, 5)   -- phantom-load probe: flat 0.3 kWh/slot every night, every day
#   [20, 23) -- one clean recurring EV-charger block every night that CROSSES
#               the 21:00 on-peak/off-peak boundary with UNEQUAL slots:
#               2.0 kWh/slot for the 4 on-peak slots [20, 21), 3.0 kWh/slot
#               for the 8 off-peak slots [21, 23). rates.period gives 16-21
#               "on" and 21-24 "off" on every day type (weekend and holiday
#               branches only differ below 14:00), so the split is the same
#               on all 365 nights. The asymmetry is what lets the #229 case
#               tell per-interval pricing from any session-scalar pricing:
#               with equal slots in one period, kwh * r.mean() or kwh * r[0]
#               equals sum(ev_i * r_i) exactly and no assertion can see it.
#   everywhere else -- flat 0.15 kWh/slot baseline (never trips the >6.5 kW EV
#   gate, never exceeds the phantom probe's <=0.5 kWh clean threshold)
# All Generation is zero (no solar) -- keeps the un-verified wildcard section
# from depending on anything this fixture does not control.
# ---------------------------------------------------------------------------
PHANTOM_KWH = 0.3
EV_ON_KWH = 2.0      # per slot, [20, 21): 8 kW, above the 6.5 kW gate
EV_OFF_KWH = 3.0     # per slot, [21, 23): 12 kW
EV_ON_SLOTS = 4
EV_OFF_SLOTS = 8
BASE_KWH = 0.15


def _shape(h):
    if 3.0 <= h < 5.0:
        return PHANTOM_KWH
    if 20.0 <= h < 21.0:
        return EV_ON_KWH
    if 21.0 <= h < 23.0:
        return EV_OFF_KWH
    return BASE_KWH


def _session_expectations():
    """Hand computation of ONE nightly session, per season: EV-only energy
    (house base off every slot), its cost at each slot's own rate, its cost if
    it had all charged super-off-peak, and the raw draw priced the way the
    unfixed generator did (issue #229). Also the two session-scalar mispricings
    the crossing fixture exists to expose."""
    base_slot = _HOUSE_BASE_KW * 0.25
    on_ev = (EV_ON_KWH - base_slot) * EV_ON_SLOTS       # 7.6 kWh
    off_ev = (EV_OFF_KWH - base_slot) * EV_OFF_SLOTS    # 23.2 kWh
    kwh = on_ev + off_ev                                # 30.8 kWh
    raw = EV_ON_KWH * EV_ON_SLOTS + EV_OFF_KWH * EV_OFF_SLOTS   # 32.0 kWh
    n = EV_ON_SLOTS + EV_OFF_SLOTS
    out = {}
    for s in ("S", "W"):
        on, off, sop = _rate_on(s), _rate_off(s), _rate_sop(s)
        out[s] = {
            "kwh": kwh, "raw_kwh": raw,
            "actual": on_ev * on + off_ev * off,            # per-interval, EV-only
            "sop": kwh * sop,
            "raw_actual": EV_ON_KWH * EV_ON_SLOTS * on + EV_OFF_KWH * EV_OFF_SLOTS * off,
            "scalar_mean": kwh * (EV_ON_SLOTS * on + EV_OFF_SLOTS * off) / n,
            "scalar_first": kwh * on,
        }
    return out


def _season_days():
    n_summer = sum(1 for i in range((END - START).days)
                   if _season((START + dt.timedelta(days=i)).month) == "S")
    return n_summer, (END - START).days - n_summer


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

FREE_FIX_EV = "a"      # the free fix an EV household's dispatch artifact records
FREE_FIX_NO_EV = "c"   # ...and the one a household with no EV records instead

_OMIT = object()
"""Sentinel for _stage(free_fix_scenario=...): leave the key OUT of the dispatch
artifact entirely, which is what one written before the field existed looks
like -- a different shape from a key holding another household's letter, and
deep_analyses.py has to refuse both."""


def _household_yaml(has_ev):
    """test_scripts_runnable.SYNTH_HOUSEHOLD as an EV or a genuinely EV-FREE
    intake. deep_analyses.py imports behavior_rebuild for the intake flag
    household.has_ev (issue #147), so every root now needs one.

    has_ev False sets household.has_ev false AND removes the charger block:
    behavior_rebuild.py refuses a declared charger beside a false flag. Every
    edit asserts it took -- a string surgery that silently matched nothing
    would leave the EV household in place and make the no-EV cases pass for the
    wrong reason."""
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


def _stage(tmp, src_text=None, has_ev=True, free_fix_scenario=FREE_FIX_EV,
           where="current-run"):
    """Build one throwaway root deep_analyses.py can run in.

    `src_text` substitutes a PATCHED copy of the generator's own source (used
    below to perturb the rate tables it declares); None stages the committed
    file byte-for-byte. `has_ev` is THIS root's intake; `free_fix_scenario` is
    what the dispatch artifact says about the household IT came from; `where`
    puts that artifact in the CWD (the current-run copy, which wins), in data/
    (the committed fallback), or in both."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()          # so _repo_root() resolves tmp as root
    (tmp / "private").mkdir()
    (tmp / "private" / "household.yaml").write_text(_household_yaml(has_ev))
    for mod in ("rates.py", "household.py", "behavior_rebuild.py"):
        shutil.copy(ANALYSIS / mod, tmp / mod)
    (tmp / "deep_analyses.py").write_text(
        (ANALYSIS / "deep_analyses.py").read_text() if src_text is None
        else src_text)
    _write_meter_csv(tmp / "usage.csv")
    _write_flat_sam(tmp / "samA.csv", 0.2, 2026)
    _write_flat_sam(tmp / "samB.csv", 0.2, 2025)
    mid = {"battery_marginal": BASE_SAVE}
    post = ({"mid": mid} if free_fix_scenario is _OMIT
            else {"free_fix_scenario": free_fix_scenario, "mid": mid})
    text = json.dumps({"post_behavior": post})
    if where in ("current-run", "both"):
        (tmp / "battery_dispatch_policies.json").write_text(text)
    if where in ("committed", "both"):
        (tmp / "data" / "battery_dispatch_policies.json").write_text(text)
    return tmp


def _run(tmp):
    return subprocess.run([sys.executable, "deep_analyses.py"], cwd=tmp,
                          capture_output=True, text=True, timeout=300)


def _run_generator(src_text=None):
    """Run the REAL deep_analyses.py end to end on the synthetic house and
    return the deep_results.json it wrote."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _stage(pathlib.Path(td), src_text=src_text)
        r = _run(tmp)
        assert r.returncode == 0, f"deep_analyses.py failed: {r.stderr[-2000:]}"
        return json.loads((tmp / "deep_results.json").read_text())


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
    n_summer, n_winter = _season_days()
    assert n_summer == 153 and n_winter == 212, (n_summer, n_winter)

    got = _run_generator()

    # ---- phantom load: constant 0.3 kWh/slot in the clean night window -----
    ph = got["phantom"]
    assert abs(ph["baseload_kw"] - PHANTOM_KWH * 4) < 1e-6, ph
    exp_annual_kwh = round(PHANTOM_KWH * 4 * 8760)
    assert abs(ph["annual_kwh"] - exp_annual_kwh) <= 1, ph
    # The block states ENERGY and nothing else (issue #172). It used to publish
    # annual_cost_at_blend = annual_kwh x a hardcoded 0.20 $/kWh. The report's
    # price for the always-on load comes from data/quiet_night_floor.json,
    # through rates.py, two ways -- but that artifact prices its OWN per-NIGHT
    # estimate of the load (1-5am median, whole night dropped at a 2 kW gate,
    # 1.03 kW), not this per-INTERVAL block's baseload_kw.
    assert not [k for k in ph if _MONEY_KEY.search(k)], (
        f"the phantom block published a dollar figure again: {ph}")

    # ...and the note that explains the missing dollars has to be TRUE
    # (CLAUDE.md section 0). An earlier note said quiet_night_floor.py prices
    # "this identical load", implying this block's own kWh is what got priced.
    # It is not: the two scripts run different extraction rules and land on
    # different figures. These fail if that wording comes back.
    note = ph["note"]
    assert "quiet_night_floor.json" in note, (
        f"the phantom note must name where the load IS priced: {note}")
    assert "identical" not in note.lower(), (
        "the phantom note calls the two floor estimates identical again -- "
        "quiet_night_floor.py measures the same physical load by its own "
        f"per-night rule, not this block's figure: {note}")
    assert "separate" in note.lower(), (
        "the phantom note must say quiet_night_floor.json prices a SEPARATE "
        f"estimate of this load rather than this block's own figure: {note}")

    # ---- EV sessions: 365 identical nightly blocks, hand-computed exactly --
    ev = got["ev_sessions"]
    assert ev["count"] == 365, ev
    X = _session_expectations()
    per_session_kwh = X["S"]["kwh"]                     # 32.0 - 1.2 = 30.8
    exp_kwh_total = round(365 * per_session_kwh)
    assert abs(ev["kwh_total"] - exp_kwh_total) <= 1, ev
    assert abs(ev["avg_kwh"] - round(per_session_kwh, 1)) < 0.05, ev
    # the block sits at 20:00-22:45: its first 4 slots are "on" (16-21) and
    # the other 8 "off" (21-24) under rates.period on every day type, and it
    # is never "sop". on_kwh/off_kwh are GROSS imports by period, base
    # included, so every session touches on-peak and the two split 8.0/24.0.
    assert ev["sessions_touching_onpeak"] == 365, ev
    assert ev["onpeak_kwh_in_sessions"] == round(365 * EV_ON_KWH * EV_ON_SLOTS), ev
    assert ev["offpeak_kwh_in_sessions"] == round(365 * EV_OFF_KWH * EV_OFF_SLOTS), ev
    # "cost" prices the SAME base-adjusted EV-only kWh as the "kwh" field, each
    # interval at its own rate (issue #229): the 4 on slots at the ON entry and
    # the 8 off slots at the OFF entry of the script's own UDC5/CEA5 tables.
    exp_cost_total = n_summer * X["S"]["actual"] + n_winter * X["W"]["actual"]
    assert abs(ev["cost_total"] - round(exp_cost_total)) <= 2, (ev, exp_cost_total)
    # "if every session had charged super-off-peak": each session's own
    # baseline-adjusted kwh at ITS OWN season's sop rate, off the same UDC5 /
    # CEA5 table cost_total uses. Was a flat 0.1257 $/kWh literal (issue #172).
    exp_sop_ref = n_summer * X["S"]["sop"] + n_winter * X["W"]["sop"]
    assert abs(ev["cost_if_all_sop"] - round(exp_sop_ref)) <= 1, (ev, exp_sop_ref)
    assert abs(ev["wasted_vs_perfect"]
               - round(exp_cost_total - exp_sop_ref)) <= 2, (ev, exp_sop_ref)

    # ---- Monte Carlo: independent oracle transcription, same seed/inputs ---
    oracle = _monte_carlo_oracle(BASE_SAVE)
    mc = got["monte_carlo"]
    for k, v in oracle.items():
        assert mc[k] == v, (k, mc[k], v)

    # ---- structural checks on the two unverified sections ------------------
    assert "wildcard" in got and set(got["wildcard"]) == {
        "TOU-DR-P + PW3 (15 events dodged)", "EV-TOU-5 + PW3",
        "TOU-DR-P no battery (events hit)"}, got["wildcard"]
    # issue #202: every key the generator emits parses under the convention
    # the reader enforces, into the (plan, configuration) pairs the ranking
    # needs -- the two PW3 entries are ONE configuration, the note is not.
    parsed = {k: RT._wildcard_key(k) for k in got["wildcard"]}
    assert parsed == {
        "TOU-DR-P + PW3 (15 events dodged)": ("TOU-DR-P", "PW3"),
        "EV-TOU-5 + PW3": ("EV-TOU-5", "PW3"),
        "TOU-DR-P no battery (events hit)": ("TOU-DR-P", "no battery")}, parsed
    assert "vacation" in got and got["vacation"]["away_days_detected"] >= 0, got["vacation"]
    assert json.dumps(got), "deep_results.json content is not JSON-serializable"
    return ("deep_analyses.py runs end to end on a synthetic house; phantom "
            "load, EV-session aggregates and the Monte Carlo battery-ROI "
            "block all match hand/oracle computations")


def case_wasted_vs_perfect_prices_the_same_ev_only_energy_on_both_sides():
    """issue #229: wasted_vs_perfect is cost_total minus cost_if_all_sop, and
    both must price the SAME EV-only kWh, each interval at its own rate.

    The generator subtracted the assumed house base from each session once, as
    a scalar, to get the EV-only kwh that cost_if_all_sop prices -- but priced
    the RAW draw, house base included, for cost_total. The published
    difference then carried the house base at whatever rate it fell under,
    and was described as the cost of mistimed charging alone.

    Three wrong pricings are ruled out here, each by a KNOWN amount on this
    fixture. Every one of the 365 sessions is 4 on-peak slots of 2.0 kWh then
    8 off-peak slots of 3.0 kWh (raw 32.0 kWh, EV-only 30.8 kWh):
      * raw draw at actual rates (the defect): overstates cost_total by
        365 * 1.2 kWh of house base at the on/off rates;
      * kwh * r.mean() (a scalar session price): puts the session's energy
        on the slots evenly, 1/3 on-peak, where only 7.6/30.8 of it sits;
      * kwh * r[0] (the first slot's rate for the whole session): prices all
        of it on-peak.
    The equal-slot single-period fixture this file used before could not see
    the last two: with every slot alike, sum(ev_i * r_i) IS kwh * r.mean()
    and kwh * r[0]. Each miss is well past the $2 rounding tolerance, so this
    case fails on the unfixed generator and on either scalar variant.
    """
    n_summer, n_winter = _season_days()
    ev = _run_generator()["ev_sessions"]
    X = _session_expectations()

    def year(key):
        return n_summer * X["S"][key] + n_winter * X["W"][key]

    assert abs(ev["kwh_total"] - round(365 * X["S"]["kwh"])) <= 1, ev

    exp_actual = year("actual")            # EV-only, each slot at its own rate
    exp_sop = year("sop")                  # EV-only, all at sop
    wrong = {"raw draw, house base included": year("raw_actual"),
             "kwh * r.mean()": year("scalar_mean"),
             "kwh * r[0]": year("scalar_first")}
    for label, w in wrong.items():
        assert abs(w - exp_actual) > 20, (
            f"the fixture cannot see the '{label}' mispricing: it lands "
            f"{w - exp_actual:.2f} from the per-interval EV-only cost")

    # the two operands, and the published difference, all on EV-only energy
    assert abs(ev["cost_total"] - round(exp_actual)) <= 2, (
        f"cost_total {ev['cost_total']} is not the per-interval EV-only cost "
        f"{exp_actual:.2f}; " + "; ".join(
            f"'{k}' would give {v:.2f}" for k, v in wrong.items()))
    assert abs(ev["cost_if_all_sop"] - round(exp_sop)) <= 1, (ev, exp_sop)
    assert abs(ev["wasted_vs_perfect"] - round(exp_actual - exp_sop)) <= 2, (
        f"wasted_vs_perfect {ev['wasted_vs_perfect']} is not the EV-only "
        f"timing cost {exp_actual - exp_sop:.2f}; " + "; ".join(
            f"'{k}' would push it to {v - exp_sop:.2f}" for k, v in wrong.items()))
    # the identity the field name promises, from the published fields alone
    assert abs(ev["wasted_vs_perfect"]
               - (ev["cost_total"] - ev["cost_if_all_sop"])) <= 1, ev
    # and every wrong value is ruled out by more than the tolerance
    for label, w in wrong.items():
        assert abs(ev["wasted_vs_perfect"] - round(w - exp_sop)) > 2, (
            f"wasted_vs_perfect {ev['wasted_vs_perfect']} matches the "
            f"'{label}' pricing ({w - exp_sop:.2f})")
    gaps = ", ".join(f"{k} by ${v - exp_actual:+,.0f}" for k, v in wrong.items())
    return ("wasted_vs_perfect prices EV-only energy per interval on both "
            f"sides; on this fixture the mispricings miss it: {gaps}")


# ---------------------------------------------------------------------------
# Issue #172 AC6: no dollar figure in this artifact may come from a flat
# $/kWh literal.
#
# Asserting that by reading the source ("no float between 0.05 and 2.0 appears
# next to a `*`") would be a lint, and a lint cannot tell 0.25 h/interval or a
# 0.90 round-trip efficiency from a price. So the property is DRIVEN instead:
# perturb the generator's OWN declared rate table and demand that every dollar
# it publishes on that tariff moves. A figure priced by a literal does not move
# -- which is exactly how the retired `annual_kwh * 0.20` and
# `kwh_total * 0.1257` would fail here.
#
# Every field the artifact publishes is classified below. An unclassified key
# fails the case, so a NEW dollar figure cannot be added without saying which
# rate table it answers to.
#   usd_ev5  -- priced off the script's UDC5/CEA5 (EV-TOU-5) table
#   usd_drp  -- priced off its UDCP/CEAP (TOU-DR-P) table
#   physical -- kW, kWh, counts and days: no rate anywhere in them
#   fixed    -- dollars, but not priced from either table (the Monte Carlo
#               reads its base saving from battery_dispatch_policies.json)
#   prose    -- a note string
# ---------------------------------------------------------------------------
FIELD_KINDS = {
    "wildcard": {"TOU-DR-P + PW3 (15 events dodged)": "usd_drp",
                 "EV-TOU-5 + PW3": "usd_ev5",
                 "TOU-DR-P no battery (events hit)": "usd_drp"},
    "phantom": {"baseload_kw": "physical", "annual_kwh": "physical",
                "note": "prose"},
    "ev_sessions": {"count": "physical", "kwh_total": "physical",
                    "cost_total": "usd_ev5", "avg_kwh": "physical",
                    "sessions_touching_onpeak": "physical",
                    "onpeak_kwh_in_sessions": "physical",
                    "offpeak_kwh_in_sessions": "physical",
                    "cost_if_all_sop": "usd_ev5",
                    "wasted_vs_perfect": "usd_ev5"},
    "vacation": {"non_ev_daily_median": "physical",
                 "away_day_threshold": "physical",
                 "away_days_detected": "physical", "note": "prose"},
    "monte_carlo": {"payback_median": "fixed", "payback_p10": "fixed",
                    "payback_p90": "fixed",
                    "prob_payback_within_warranty10yr": "fixed",
                    "npv10_at_4pct_median": "fixed"},
}

_MONEY_KEY = re.compile(r"cost|usd|price|dollar|blend|\$", re.I)

# (label, the source slice whose tables get scaled, the kind that must move)
_TABLES = (("EV-TOU-5", ('UDC5={"S":', "\ndef rates("), ("UDC5", "CEA5"), "usd_ev5"),
           ("TOU-DR-P", ('UDCP={"S":', 'UDC5={"S":'), ("UDCP", "CEAP"), "usd_drp"))


def _source_with_scaled_table(start, end, names, factor):
    """deep_analyses.py's source with one of its declared rate tables scaled.

    The tables are re-emitted from the values the generator itself declares
    (exec'd out of its own source, never hand-copied), so this keeps working
    when a rate changes."""
    src = (ANALYSIS / "deep_analyses.py").read_text()
    block = src[src.index(start):src.index(end)]
    ns = {}
    exec(block, ns)
    scaled = "\n".join(
        "{}={!r}".format(name, {s: {p: v * factor for p, v in cells.items()}
                                for s, cells in ns[name].items()})
        for name in names) + "\n"
    assert src.count(block) == 1, "rate-table slice is not unique in the source"
    return src.replace(block, scaled)


def case_no_published_dollar_figure_survives_a_change_to_its_rate_table():
    base = _run_generator()

    for block, fields in FIELD_KINDS.items():
        assert set(base[block]) == set(fields), (
            f"deep_results.json:{block} does not carry the fields this case "
            f"classifies -- unclassified: {sorted(set(base[block]) - set(fields))}, "
            f"missing: {sorted(set(fields) - set(base[block]))}. Classify every new "
            "field (issue #172 AC6) before it can be published.")
    assert set(base) == set(FIELD_KINDS), sorted(set(base) ^ set(FIELD_KINDS))

    # The phantom load states energy and no dollars at all. The always-on load
    # is priced in data/quiet_night_floor.json, which takes every rate from
    # rates.py -- applied to that script's own separate, closely matching
    # estimate of the load, not to this block's figure.
    assert not [k for k in FIELD_KINDS["phantom"] if _MONEY_KEY.search(k)], (
        "the phantom block declares a dollar field again (issue #172): "
        f"{sorted(FIELD_KINDS['phantom'])}")

    moved = 0
    for label, (start, end), names, kind in _TABLES:
        got = _run_generator(_source_with_scaled_table(start, end, names, 2.0))
        for block, fields in FIELD_KINDS.items():
            for field, this_kind in fields.items():
                a, b = base[block][field], got[block][field]
                if this_kind == kind:
                    assert b > a, (
                        f"deep_results.json:{block}.{field} is ${a:,} with the {label} "
                        f"table doubled as well as before it -- it is not priced from "
                        f"that table at all. A dollar figure that ignores the rate "
                        f"table is a flat-rate multiply (issue #172).")
                    moved += 1
                else:
                    assert a == b, (
                        f"doubling the {label} table moved "
                        f"deep_results.json:{block}.{field} ({a} -> {b}), which this "
                        f"case classifies as {this_kind}: the perturbation is not "
                        "isolated, so nothing it proves about the other fields holds.")
    return (f"every dollar figure deep_results.json publishes ({moved} across two "
            "tariffs) moves when its own rate table moves, and the phantom block "
            "publishes no dollar figure at all")


def case_dispatch_artifact_from_the_other_household_is_refused():
    """issue #147: the dispatch artifact deep_analyses.py seeds its Monte Carlo
    from must belong to THIS household.

    _base_save() falls back to the committed data/battery_dispatch_policies.json
    when no current-run copy exists, and nothing checked that the resolved copy
    came from a household with the same EV applicability. A household with no EV
    then built its entire battery payback and NPV distribution out of the
    committed EV household's post_behavior.mid.battery_marginal -- a figure
    measured on top of a free fix this household never ran -- and exited 0.

    post_behavior.mid.battery_marginal is the ONLY figure this script takes from
    that artifact, so there is no tolerance, no tie-out and no other assertion
    anywhere in the file that could have caught it: the number simply flowed
    into the Monte Carlo as the base case.

    Both directions, and both resolution paths -- guarding only the committed
    fallback would leave the identical defect for a stale current-run copy
    another household's run left in this working directory, and that copy WINS
    the resolution.
    """
    variants = [("no-EV household handed the EV household's dispatch artifact",
                 False, FREE_FIX_EV),
                ("EV household handed the no-EV household's dispatch artifact",
                 True, FREE_FIX_NO_EV)]
    for label, has_ev, theirs in variants:
        for where in ("committed", "current-run"):
            with tempfile.TemporaryDirectory() as td:
                tmp = _stage(pathlib.Path(td), has_ev=has_ev,
                             free_fix_scenario=theirs, where=where)
                r = _run(tmp)
                ctx = f"{where}/{label}"
                assert r.returncode != 0, (
                    f"{ctx}: deep_analyses.py seeded this household's Monte "
                    f"Carlo from another household's dispatch artifact:\n"
                    f"{r.stdout[-2000:]}")
                assert "EV APPLICABILITY MISMATCH" in r.stderr, (ctx, r.stderr)
                # the message must name the intake FLAG, the artifact, what it
                # says, the harm and the remedy
                assert "household.has_ev" in r.stderr, (ctx, r.stderr)
                assert "battery_dispatch_policies.json" in r.stderr, (ctx, r.stderr)
                assert f"free_fix_scenario {theirs!r}" in r.stderr, (ctx, r.stderr)
                assert "Monte Carlo" in r.stderr, (ctx, r.stderr)
                assert "battery_dispatch_policies.py" in r.stderr, (ctx, r.stderr)
                assert not (tmp / "deep_results.json").exists(), (
                    f"{ctx}: deep_results.json was written despite the "
                    "applicability abort")

    # An artifact that names NO free fix cannot be checked against this
    # household at all, so it is refused too rather than trusted.
    with tempfile.TemporaryDirectory() as td:
        tmp = _stage(pathlib.Path(td), free_fix_scenario=_OMIT)
        r = _run(tmp)
        assert r.returncode != 0, (
            "deep_analyses.py accepted a dispatch artifact that names no free "
            f"fix at all:\n{r.stdout[-2000:]}")
        assert "no usable post_behavior.free_fix_scenario" in r.stderr, r.stderr
        assert "household.has_ev" in r.stderr, r.stderr
        assert not (tmp / "deep_results.json").exists(), (
            "deep_results.json was written despite the abort")

    # POSITIVE CONTROL: a MATCHING household still runs, and its Monte Carlo is
    # still seeded from the artifact's own battery_marginal. Without this a
    # generator that refused everything would pass every assertion above.
    oracle = _monte_carlo_oracle(BASE_SAVE)
    for has_ev, mine in ((True, FREE_FIX_EV), (False, FREE_FIX_NO_EV)):
        for where in ("committed", "current-run"):
            with tempfile.TemporaryDirectory() as td:
                tmp = _stage(pathlib.Path(td), has_ev=has_ev,
                             free_fix_scenario=mine, where=where)
                r = _run(tmp)
                ctx = f"{where}/has_ev={has_ev}"
                assert r.returncode == 0, (
                    f"{ctx}: deep_analyses.py refused a dispatch artifact from "
                    f"its OWN household: {r.stderr[-2000:]}")
                mc = json.loads((tmp / "deep_results.json").read_text())["monte_carlo"]
                for k, v in oracle.items():
                    assert mc[k] == v, (ctx, k, mc[k], v)
    return ("deep_analyses.py refuses a dispatch artifact whose "
            "post_behavior.free_fix_scenario disagrees with this run's "
            "household.has_ev -- both directions, on the current-run copy as "
            "well as the committed fallback -- refuses one that names no free "
            "fix at all, writes nothing in either case, and still seeds the "
            "Monte Carlo from its own household's artifact")


CASES = [case_deep_analyses_end_to_end_matches_hand_and_oracle_computations,
         case_wasted_vs_perfect_prices_the_same_ev_only_energy_on_both_sides,
         case_no_published_dollar_figure_survives_a_change_to_its_rate_table,
         case_dispatch_artifact_from_the_other_household_is_refused]


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
