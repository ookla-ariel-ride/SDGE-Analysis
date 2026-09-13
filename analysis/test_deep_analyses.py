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
import ast
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

def _declaration(src, name):
    """The single source line of deep_analyses.py that declares `name`.

    Line-at-a-time, not a slice between two landmarks: the generator's rate
    tables now sit beside a PLAN_RATES map that references them all (issue
    #278), so a slice wide enough to catch one table catches statements that
    do not stand alone."""
    start = src.index(f"\n{name}=") + 1
    return src[start:src.index("\n", start)]


def _exec_declarations(names, src=None):
    """deep_analyses.py's own declarations of `names`, executed out of its
    source rather than hand-copied into this file -- the same drift risk
    test_battery_backup_sims.py's identical pattern documents: the generator
    declares its own rate table, not analysis/rates.py's canonical one, and a
    hand-copied number silently stops matching the moment the generator's own
    constant changes."""
    if src is None:
        src = (ANALYSIS / "deep_analyses.py").read_text()
    ns = {}
    for name in names:
        exec(_declaration(src, name), ns)
    return ns


def _generator_constants():
    """deep_analyses.py's own WFNBC/PCIA/UDC5/CEA5. The WFNBC line declares
    PCIA, NBC and BSC beside it, so one exec carries all four."""
    ns = _exec_declarations(("WFNBC", "UDC5", "CEA5"))
    return ns["WFNBC"], ns["PCIA"], ns["UDC5"], ns["CEA5"]


_WFNBC, _PCIA, _UDC5, _CEA5 = _generator_constants()


def _house_base_kw():
    """deep_analyses.py's EV_SESSION_HOUSE_BASE_KW, exec'd out of its own
    source for the same reason _generator_constants() does: a hand-copied
    0.4 here would keep the hand computations below agreeing with a generator
    whose base had moved, for the wrong reason."""
    ns = _exec_declarations(("EV_SESSION_HOUSE_BASE_KW",))
    return float(ns["EV_SESSION_HOUSE_BASE_KW"])


_HOUSE_BASE_KW = _house_base_kw()


def _plan_rates_binding():
    """{plan: [UDC name, CEA name]} out of deep_analyses.py's PLAN_RATES.

    ast for the mapping, since its values are the NAMES of the tables declared
    above it rather than literals. Reading the binding rather than repeating it
    here is what keeps this file from hand-copying which table belongs to which
    plan (issue #278)."""
    src = (ANALYSIS / "deep_analyses.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "PLAN_RATES"):
            return {ast.literal_eval(k): [e.id for e in v.elts]
                    for k, v in zip(node.value.keys, node.value.values)}
    raise AssertionError("deep_analyses.py no longer declares PLAN_RATES at module "
                         "level, so this file cannot read its plan table")


def _plan_tables(plan):
    """The (UDC, CEA) pair PLAN_RATES binds to `plan`, as values: the binding,
    then the declarations the two names point at."""
    bound = _plan_rates_binding()
    assert plan in bound, (plan, sorted(bound))
    ns = _exec_declarations(bound[plan])
    return tuple(ns[name] for name in bound[plan])


def _rate_sop(season, udc=None, cea=None):
    udc, cea = (_UDC5 if udc is None else udc), (_CEA5 if cea is None else cea)
    return udc[season]["sop"] + _WFNBC + _PCIA + cea[season]["sop"]


def _rate_off(season, udc=None, cea=None):
    udc, cea = (_UDC5 if udc is None else udc), (_CEA5 if cea is None else cea)
    return udc[season]["off"] + _WFNBC + _PCIA + cea[season]["off"]


def _rate_on(season, udc=None, cea=None):
    udc, cea = (_UDC5 if udc is None else udc), (_CEA5 if cea is None else cea)
    return udc[season]["on"] + _WFNBC + _PCIA + cea[season]["on"]


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


def _session_expectations(udc=None, cea=None):
    """Hand computation of ONE nightly session, per season: EV-only energy
    (house base off every slot), its cost at each slot's own rate, its cost if
    it had all charged super-off-peak, and the raw draw priced the way the
    unfixed generator did (issue #229). Also the two session-scalar mispricings
    the crossing fixture exists to expose.

    `udc`/`cea` price the session on a plan other than the fixture's default
    (issue #278: the block prices household.plan, not a hardcoded EV-TOU-5)."""
    base_slot = _HOUSE_BASE_KW * 0.25
    on_ev = (EV_ON_KWH - base_slot) * EV_ON_SLOTS       # 7.6 kWh
    off_ev = (EV_OFF_KWH - base_slot) * EV_OFF_SLOTS    # 23.2 kWh
    kwh = on_ev + off_ev                                # 30.8 kWh
    raw = EV_ON_KWH * EV_ON_SLOTS + EV_OFF_KWH * EV_OFF_SLOTS   # 32.0 kWh
    n = EV_ON_SLOTS + EV_OFF_SLOTS
    out = {}
    for s in ("S", "W"):
        on = _rate_on(s, udc, cea)
        off = _rate_off(s, udc, cea)
        sop = _rate_sop(s, udc, cea)
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


SYNTH_PLAN = "EV-TOU-5"
"""The rate plan the fixture's intake declares unless a case names another.

deep_analyses.py's wildcard block reads household.plan (issue #278), so every
root needs one; EV-TOU-5 keeps the default fixture on the plan the rest of this
file's hand computations price (_UDC5/_CEA5)."""


def _household_yaml(has_ev, plan=SYNTH_PLAN):
    """test_scripts_runnable.SYNTH_HOUSEHOLD as an EV or a genuinely EV-FREE
    intake, on rate plan `plan`. deep_analyses.py imports behavior_rebuild for
    the intake flag household.has_ev (issue #147) and reads household.plan for
    the wildcard block (issue #278), so every root now needs both.

    has_ev False sets household.has_ev false AND removes the charger block:
    behavior_rebuild.py refuses a declared charger beside a false flag. Every
    edit asserts it took -- a string surgery that silently matched nothing
    would leave the EV household in place and make the no-EV cases pass for the
    wrong reason."""
    hh = TSR.SYNTH_HOUSEHOLD
    assert "household:\n  pto_date: 2019-12-01\n" in hh, \
        "SYNTH_HOUSEHOLD's household block no longer has the shape this edit expects"
    assert "charger:\n  kw: 11.5\n" in hh, "SYNTH_HOUSEHOLD no longer declares a charger"
    assert "plan:" not in hh, \
        "SYNTH_HOUSEHOLD now declares a plan of its own; this edit would add a second"
    if not has_ev:
        hh = hh.replace("household:\n  pto_date: 2019-12-01\n",
                        "household:\n  pto_date: 2019-12-01\n  has_ev: false\n")
        hh = hh.replace("charger:\n  kw: 11.5\n", "")
        assert "has_ev: false" in hh and "charger:" not in hh, hh
    hh = hh.replace("household:\n", f'household:\n  plan: "{plan}"\n')
    assert f'plan: "{plan}"' in hh, hh
    return hh


def _stage(tmp, src_text=None, has_ev=True, free_fix_scenario=FREE_FIX_EV,
           where="current-run", plan=SYNTH_PLAN):
    """Build one throwaway root deep_analyses.py can run in.

    `src_text` substitutes a PATCHED copy of the generator's own source (used
    below to perturb the rate tables it declares); None stages the committed
    file byte-for-byte. `has_ev` is THIS root's intake, `plan` its declared
    rate plan; `free_fix_scenario` is what the dispatch artifact says about the
    household IT came from; `where` puts that artifact in the CWD (the
    current-run copy, which wins), in data/ (the committed fallback), or in
    both."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()          # so _repo_root() resolves tmp as root
    (tmp / "private").mkdir()
    (tmp / "private" / "household.yaml").write_text(_household_yaml(has_ev, plan))
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


def _run_generator(src_text=None, plan=SYNTH_PLAN):
    """Run the REAL deep_analyses.py end to end on the synthetic house and
    return the deep_results.json it wrote."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _stage(pathlib.Path(td), src_text=src_text, plan=plan)
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
    # needs -- the two PW3 entries are ONE configuration (the one the ranking
    # is on), the note is not, and both plans carry that entry.
    parsed = {k: RT._wildcard_key(k) for k in got["wildcard"]}
    assert parsed == {
        "TOU-DR-P + PW3 (15 events dodged)": ("TOU-DR-P", "PW3", "15 events dodged"),
        "EV-TOU-5 + PW3": ("EV-TOU-5", "PW3", None),
        "TOU-DR-P no battery (events hit)": ("TOU-DR-P", "no battery", "events hit")}, parsed
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
#   usd_ev5  -- priced off the script's UDC5/CEA5 table, the PLAN_RATES row for
#               this fixture's own household.plan (SYNTH_PLAN, EV-TOU-5)
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

# (label, the tables that get scaled, the kind that must move)
_TABLES = (("EV-TOU-5", ("UDC5", "CEA5"), "usd_ev5"),
           ("TOU-DR-P", ("UDCP", "CEAP"), "usd_drp"))


def _source_with_scaled_table(names, factor):
    """deep_analyses.py's source with the named declared rate tables scaled.

    Each table's own declaring LINE is rewritten in place, so the PLAN_RATES
    map that binds the tables to plan names (issue #278) survives untouched
    and keeps pointing at the scaled objects. The tables are re-emitted from
    the values the generator itself declares (exec'd out of its own source,
    never hand-copied), so this keeps working when a rate changes."""
    src = (ANALYSIS / "deep_analyses.py").read_text()
    for name in names:
        line = _declaration(src, name)
        ns = {}
        exec(line, ns)
        scaled = "{}={!r}".format(
            name, {s: {p: v * factor for p, v in cells.items()}
                   for s, cells in ns[name].items()})
        assert src.count(line) == 1, f"{name}'s declaration is not unique in the source"
        src = src.replace(line, scaled)
    return src


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
    for label, names, kind in _TABLES:
        got = _run_generator(_source_with_scaled_table(names, 2.0))
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


# ---------------------------------------------------------------------------
# Issue #278: the wildcard block prices THIS household's plan.
#
# The block used to build itself from two hardcoded rate tables and emit keys
# for EV-TOU-5 and TOU-DR-P only, whatever household.plan said. On this
# household it happened to name the right plan; on any other one section 0's
# card and section 9's heading were left with a block that never priced the
# plan, and report_tokens refuses by name rather than publish a comparison the
# artifact does not support -- so the household could not generate a report at
# all.
#
# The published key text is a CONTRACT between this generator and
# report_tokens._wildcard_totals, so it is asserted here as literal strings
# (the same way the structural check above does) rather than rebuilt from the
# generator's own constants: a test that reassembles the convention from the
# source it is checking cannot see the convention change.
# ---------------------------------------------------------------------------
WILDCARD_BATTERY = "PW3"
WILDCARD_EVENT_PLAN = "TOU-DR-P"
WILDCARD_EVENT_KEY = f"{WILDCARD_EVENT_PLAN} + {WILDCARD_BATTERY} (15 events dodged)"
WILDCARD_NO_BATTERY_KEY = f"{WILDCARD_EVENT_PLAN} no battery (events hit)"
# every plan the block's own table prices, so the cases below drive the whole
# table rather than the one alternative that happens to be handy
WILDCARD_PRICED_PLANS = ("EV-TOU-5", "EV-TOU-2", "TOU-ELEC", WILDCARD_EVENT_PLAN)
WILDCARD_UNPRICED_PLAN = "TOU-DR2"   # a two-period plan: no super-off-peak rate to read


def case_the_wildcard_block_prices_this_households_own_plan():
    """issue #278: household.plan is the plan the wildcard block prices.

    Three properties, none of which a renamed key satisfies:
      * the household's own key names household.plan, and its TOTAL moves with
        that plan -- a block that relabelled one EV-TOU-5 workup would give the
        same dollars under three different plan names;
      * the RIVAL side does not move with it: the event plan is priced from its
        own table, so both TOU-DR-P totals are identical across households;
      * every key parses under report_tokens' convention, which is the reader
        that refuses the block when it does not.

    A household already ON the event plan gets the question the other way
    round: the block prices its plan against every other plan its table
    carries, so section 9's heading still has a rival to name.
    """
    own_totals = {}
    for plan in WILDCARD_PRICED_PLANS:
        wild = _run_generator(plan=plan)["wildcard"]
        rivals = [p for p in WILDCARD_PRICED_PLANS if p != plan] \
            if plan == WILDCARD_EVENT_PLAN else [WILDCARD_EVENT_PLAN]
        expected = {WILDCARD_EVENT_KEY, WILDCARD_NO_BATTERY_KEY} | {
            f"{p} + {WILDCARD_BATTERY}" for p in rivals + [plan]
            if p != WILDCARD_EVENT_PLAN}
        assert set(wild) == expected, (
            f"a household on {plan} got a wildcard block keyed {sorted(wild)}; "
            f"expected {sorted(expected)}")

        parsed = {k: RT._wildcard_key(k) for k in wild}
        assert all(parsed.values()), (plan, parsed)
        priced = {}
        for key, (name, configuration, _note) in parsed.items():
            assert configuration not in priced.setdefault(name, {}), (plan, parsed)
            priced[name][configuration] = wild[key]
        assert plan in priced and WILDCARD_BATTERY in priced[plan], (
            f"a household on {plan} got a wildcard block that never prices "
            f"{plan} with the battery: {sorted(wild)}")
        assert set(priced) == set(rivals) | {plan}, (plan, sorted(priced))
        for name in priced:
            assert WILDCARD_BATTERY in priced[name], (
                f"{name} carries no battery entry, which report_tokens refuses: "
                f"{sorted(wild)}")
        own_totals[plan] = priced[plan][WILDCARD_BATTERY]

        # the rival side is priced from the event plan's own table, so it is the
        # same two figures whatever plan the household is on
        assert wild[WILDCARD_NO_BATTERY_KEY] == _run_generator()["wildcard"][
            WILDCARD_NO_BATTERY_KEY], (
            f"the event plan's no-battery total moved when the household moved to "
            f"{plan}; the rival is priced from its own table")

    distinct = sorted(set(own_totals.values()))
    assert len(distinct) == len(own_totals), (
        "two plans were priced to the same total, so the block is relabelling one "
        f"workup rather than pricing each plan: {own_totals}")

    # a plan the block's table cannot price is refused BY NAME, not priced as
    # something else and not written half-way
    with tempfile.TemporaryDirectory() as td:
        tmp = _stage(pathlib.Path(td), plan=WILDCARD_UNPRICED_PLAN)
        r = _run(tmp)
        assert r.returncode != 0, (
            f"deep_analyses.py priced a household on {WILDCARD_UNPRICED_PLAN}, a plan "
            f"its rate table does not carry:\n{r.stdout[-2000:]}")
        assert WILDCARD_UNPRICED_PLAN in r.stderr and "household.plan" in r.stderr, r.stderr
        assert not (tmp / "deep_results.json").exists(), (
            "deep_results.json was written despite the unpriceable-plan abort")

    return ("deep_analyses.py's wildcard block prices household.plan: "
            + ", ".join(f"{p} ${t:,}" for p, t in own_totals.items())
            + f", each against the {WILDCARD_EVENT_PLAN} workup, every key parsing "
            "under report_tokens' convention, and an unpriceable plan refused by name")


def _analyze_tables():
    """analyze.py's three published rate tables (UDC, EECC, CEA) as literals.

    READ, NEVER IMPORTED: analyze.py loads usage.csv at import, so importing it
    would tie this case to the private archive and skip in CI, which is where
    the two files most need to be compared. ast.literal_eval reaches the values
    without running a line of it.
    """
    src = (ANALYSIS / "analyze.py").read_text()
    out = {}
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("UDC", "EECC", "CEA")):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    missing = {"UDC", "EECC", "CEA"} - set(out)
    assert not missing, (
        f"analyze.py no longer declares {sorted(missing)} as a literal at module "
        "level, so this pin cannot read the rows deep_analyses.py copies")
    return out


# Which analyze.py table each half of a PLAN_RATES pair is a copy of, in the
# order the pair declares them. The first half is the utility's delivery total
# and the second is the CCA's generation, so the pin below compares each against
# the table it came from -- and never against EECC, SDG&E's own BUNDLED
# generation, which analyze.py carries for a different comparison and whose
# numbers differ (EV-TOU-5 summer on-peak 0.47019 there against the CCA's
# 0.51684).
_PLAN_RATE_SOURCES = ("UDC", "CEA")


def case_the_wildcard_rate_table_is_analyze_pys_published_rows():
    """The comment beside PLAN_RATES claims every row is analyze.py's own. Pin
    it, because nothing else can: two files each declaring the same published
    tariff by hand is exactly the drift CLAUDE.md section 9's one-rates-module
    rule exists to prevent, and the labelled cross-plan exception that lets
    both exist does not make them agree.

    The EECC cross-check is a POSITIVE CONTROL for the comparison itself: it
    proves this case can tell the CCA generation row from SDG&E's bundled one,
    so a pass means the rows match the table the comment names rather than any
    table with the right shape.
    """
    tables = _analyze_tables()
    plans = sorted(_plan_rates_binding())
    for plan in plans:
        ours = _plan_tables(plan)
        assert len(ours) == len(_PLAN_RATE_SOURCES), (plan, ours)
        for source, row in zip(_PLAN_RATE_SOURCES, ours):
            theirs = tables[source].get(plan)
            assert theirs is not None, (
                f"analyze.py's {source} table does not carry {plan}, which "
                f"deep_analyses.py's PLAN_RATES prices; the comment beside PLAN_RATES "
                "claims every row is analyze.py's")
            assert row == theirs, (
                f"deep_analyses.py's {source} row for {plan} is not analyze.py's "
                f"{source}[{plan!r}]: {row} against {theirs}. The two files declare the "
                "same published tariff by hand, so a row that moves in one and not the "
                "other publishes two prices for one plan.")
    ev5_cca, ev5_bundled = tables["CEA"]["EV-TOU-5"], tables["EECC"]["EV-TOU-5"]
    assert ev5_cca != ev5_bundled, (
        "analyze.py's CEA and EECC rows for EV-TOU-5 are identical, so this case cannot "
        "tell the CCA generation table from SDG&E's bundled one and its match above "
        "proves nothing about which table deep_analyses.py copied")
    assert _plan_tables("EV-TOU-5")[1] == ev5_cca, (
        "deep_analyses.py's CEA5 is not the CCA generation row")
    return (f"every UDC and CEA row in deep_analyses.py's PLAN_RATES is analyze.py's own "
            f"({len(plans)} plans: {', '.join(plans)}), and the pin distinguishes the "
            f"CCA generation row from SDG&E's bundled EECC row "
            f"({ev5_cca['S']['on']} against {ev5_bundled['S']['on']} on EV-TOU-5 summer "
            "on-peak)")


def case_the_ev_session_workpaper_prices_this_households_plan():
    """issue #278, same defect one block along: the EV-session workpaper priced
    every household's sessions off the hardcoded EV-TOU-5 table.

    cost_total, cost_if_all_sop and wasted_vs_perfect are all EV-only energy at
    a rate, and the rate came from UDC5/CEA5 whatever household.plan said. On a
    household on EV-TOU-2 or TOU-ELEC every one of the three was a figure from
    another plan's tariff, and nothing in the artifact said so.

    Driven against a hand computation on each plan's OWN PLAN_RATES row, read
    out of the generator's source. The three plans differ in their UDC row
    (0.31711/0.30372/0.25317 on- and off-peak, 0.04114/0.16275/0.25317
    super-off-peak), so a block still pricing one hardcoded table lands on one
    set of figures for all three and fails both the oracle and the last
    assertion.
    """
    n_summer, n_winter = _season_days()
    got = {}
    for plan in ("EV-TOU-5", "EV-TOU-2", "TOU-ELEC"):
        udc, cea = _plan_tables(plan)
        X = _session_expectations(udc, cea)
        ev = _run_generator(plan=plan)["ev_sessions"]
        exp_actual = n_summer * X["S"]["actual"] + n_winter * X["W"]["actual"]
        exp_sop = n_summer * X["S"]["sop"] + n_winter * X["W"]["sop"]
        assert abs(ev["cost_total"] - round(exp_actual)) <= 2, (
            f"a household on {plan} got cost_total {ev['cost_total']}, not the "
            f"{exp_actual:.2f} its own plan row prices")
        assert abs(ev["cost_if_all_sop"] - round(exp_sop)) <= 1, (plan, ev, exp_sop)
        assert abs(ev["wasted_vs_perfect"] - round(exp_actual - exp_sop)) <= 2, (
            plan, ev, exp_actual - exp_sop)
        got[plan] = (ev["cost_total"], ev["cost_if_all_sop"], ev["wasted_vs_perfect"])
    assert len(set(got.values())) == len(got), (
        "the three plans were priced to one set of figures, so the block is still "
        f"reading one hardcoded rate table: {got}")
    return ("the EV-session workpaper prices household.plan's own rate row: "
            + "; ".join(f"{plan} cost_total ${t[0]:,} wasted ${t[2]:,}"
                        for plan, t in got.items()))


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
         case_the_wildcard_block_prices_this_households_own_plan,
         case_the_wildcard_rate_table_is_analyze_pys_published_rows,
         case_the_ev_session_workpaper_prices_this_households_plan,
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
