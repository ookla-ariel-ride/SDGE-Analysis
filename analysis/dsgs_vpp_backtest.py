#!/usr/bin/env python3
"""DSGS Option 3 VPP revenue backtest against the REAL 2025 events (issue #10, Phase 1).

This household has NO battery today (private/household.yaml has no `battery:` section).
Every figure here is HYPOTHETICAL: what a Powerwall 3 (13.5 kWh usable, 11.5 kW, 90%
round-trip -- battery_dispatch_policies.py's constants, imported not re-declared) would
have earned had this household owned one and enrolled it in DSGS Option 3, replayed
against the REAL 2025 DSGS event calendar and this household's REAL measured load/solar.

This REPLACES the §6 estimate (~$150-350/season, "estimated - program terms cited",
extrapolated from Tesla/Olivine program terms with no event data) with a measured
backtest. §6's own replacement prose is a later phase (see CLAUDE.md's scope note for
this issue) -- this script and its artifact are the evidence that prose will cite.

SOURCE DATA (public CEC policy data, not personal to this household -- same commit
reasoning as issue #9's rate table): private/1-raw-data/dsgs_events/dsgs_2025_performance.xlsx,
"Anonymized Data - Staff Analysis of the DSGS 2025 Program Performance", filed with the
California Energy Commission docket 22-RENEW-01 (TN 269155, 2026-03-12), commissioned by
the CEC and compiled by Olivine, Inc. Three sheets: "Data Dictionary" (read first -- it
documents every column literally, quoted below), "Monthly Aggregation Dataset" (one row
per anonymized aggregation per month, used here ONLY to derive the $/kW-month capacity
rate and the prescriptive-baseline ratio), "Hourly Discharge Dataset" (361,008 rows: one
row per aggregation per hour for the full May-Oct 2025 season -- this is the event
calendar source).

GENUINE, LOAD-BEARING FINDING #1 -- 2025 had NO real emergency dispatches for this
program option. The Data Dictionary's own text for "Option 3 Event Type" reads (quoted
verbatim):
    "Capacity (event hours triggered by LMP threshold (Note: There were no cases of
    this event type in 2025))"
    "Energy-Only (energy-only event hour triggered by a day-of EEA (Note: There were
    no cases of this event type in 2025))"
Every 2025 event hour is "Test Capacity" or "Test Non-Capacity" -- a mandatory monthly
test dispatch run in the ABSENCE of a real grid emergency that month, not an actual
emergency. Confirmed independently in this run against the raw file (see build_calendar()
and its test coverage): 0 "Capacity" rows, 0 "Energy-Only" rows, out of 361,008.

GENUINE AMBIGUITY, DISCLOSED (not silently resolved) -- WHICH UDC IS SDG&E. The 2025
file anonymizes the UDC as "UDC 1".."UDC 4"; there is no column that names SDG&E
directly. We infer UDC 2 = SDG&E by 2025 enrollment SCALE (site-months, summed from the
Monthly Aggregation Dataset): UDC3=319,814 (largest, presumably PG&E), UDC1=272,033
(second, presumably SCE), UDC2=82,776 (third, notably smaller -- consistent with SDG&E's
much smaller service territory), UDC4=2,829 (smallest, presumably LADWP, which joined the
VPP only in Oct per the 2024 analysis). CROSS-CHECK (independent, different year, same
ordering): private/1-raw-data/dsgs_events/dsgs_2024_analysis.pdf uses REAL utility names
and reports September 2024 enrolled VPP nameplate capacity of PG&E 129.0 MW > SCE 98.9 MW
> SDG&E 41.9 MW > LADWP 0.0 MW (not yet participating) -- the SAME relative ordering and
the SAME "SDG&E notably smaller than SCE, LADWP smallest/newest" shape, one year apart.
This corroborates but does NOT prove the inference (anonymization is anonymization). Every
UDC2-derived figure below is labeled with this caveat; there is no fully certain way to
confirm identity from the 2025 file alone.

GENUINE AMBIGUITY, DISCLOSED -- WHICH SPECIFIC AGGREGATION. Even restricted to UDC2 +
Residential + Stationary + 2-hour resource duration (the category matching a hypothetical
Powerwall-class residential battery), the CEC file still contains ~14 distinct anonymized
aggregations, and they do NOT all test on the same calendar date within a month (each
VPP aggregator schedules its own enrolled customers' monthly test independently) -- of 68
distinct (date, hour) event slots in this category, 16 have aggregations disagreeing on
event type for the exact same hour. A single real household belongs to exactly ONE
aggregation (whichever VPP company it enrolls with) and would see ONE aggregation's
schedule, not the union of all of them. Since the anonymized identifiers cannot be mapped
to a real provider (Tesla, etc.), this backtest uses the UNION of all distinct event
(date, hour) slots seen by ANY UDC2/Residential/Stationary/2-hr aggregation as the
household's hypothetical calendar (a deliberately inclusive choice: more candidate event
hours than any single real household actually saw, so this is an upper bound on event
FREQUENCY, not a wrong-but-precise single-aggregation schedule). Where aggregations
disagree on event type for the same hour, the MAJORITY classification is used (tie
broken toward "Test Capacity", the more consequential/less common category) -- 60 of 68
hours (88%) have unanimous agreement, so this affects a small minority of hours.

GENUINE AMBIGUITY, DISCLOSED -- PAYMENT RATE SOURCE. Two candidate sources for the $/kW
VPP payment rate:
  (1) EMPIRICAL, used here as primary: divide each UDC2/Residential/2-hr aggregation's
      "Monthly Capacity Payment ($)" by its "Demonstrated Capacity (MW)" (Monthly
      Aggregation Dataset), restricted to rows with positive demonstrated capacity (the
      Data Dictionary states negative performance is paid $0). This ratio is essentially
      CONSTANT across all aggregations within the same month (verified: <0.03% relative
      spread per month across 71 rows, May-Oct) -- i.e. DSGS Option 3 pays one published
      $/kW-month rate per month, and this recovers it directly from real settlements
      rather than guessing at the underlying CPUC/CEC rate schedule.
  (2) SANITY CHECK ONLY: the Tesla/program-terms "$150-350/season" figure already cited
      in this report's §6 (an extrapolation, not a measurement) -- computed here for a
      comparable hypothetical capacity and printed alongside the empirical figure so a
      reader can see the two agree in order of magnitude.
Source (1) is used for the headline number because it is a MEASUREMENT of what real
aggregations in this UDC/category were actually paid, not an extrapolation.

BACKUP RESERVE -- NOT ESTABLISHED ELSEWHERE IN THIS REPO, FLAGGED FOR THE PARENT. The
issue text asserts "§6's outage work establishes what reserve the household would
plausibly hold." This was checked against analysis/battery_backup_sims.py and
analysis/extended_findings.py and found NOT TRUE as stated: no numeric backup-reserve
fraction exists anywhere in this repository. battery_backup_sims.py's own outage
endurance sims explicitly "assume a full battery at outage start" (see index.html's
existing prose, unchanged here) -- i.e. the OPPOSITE convention, no reserve held back in
advance. BACKUP_RESERVE_FRAC below (20%) is therefore an UNCITED, ASSUMED operating
parameter chosen here, not derived from this repo's existing analysis or from a fetched
Tesla source (Tesla's backup-reserve support pages returned HTTP 403 to this run's
fetch). A 0% (no reserve) sensitivity is computed alongside the primary 20% case so the
report can see how much the assumption matters; both are in the committed artifact. This
is flagged for a parent-level decision, not silently resolved.

DISPATCH MODEL. battery_dispatch_policies.run_batt()'s "greedy" policy (imported, not
reimplemented) gives the business-as-usual (BAU) hypothetical-battery bill: normal
price-aware dispatch, no VPP participation. run_batt_vpp() below is a close variant that
mirrors run_batt's greedy control flow EXACTLY for non-event intervals (verified in
analysis/test_dsgs_vpp_backtest.py: with an empty event set, its imp/exp output is
byte-identical to run_batt's), and additionally, ONLY during a real 2025 DSGS event hour
that falls inside this household's measured window, forces the battery to discharge (and
export any surplus beyond what the household's own load needs at that moment) up to its
remaining headroom above BACKUP_RESERVE_FRAC * capacity and the 11.5 kW power cap -- the
behavior a revenue-maximizing VPP aggregator actually commands, not just
business-as-usual arbitrage. An event hour where SOC is already at or below the reserve
floor cannot be served further: this is the SOC-constrained MISS the issue asks for.

REVENUE, NET. GROSS = the empirical $/kW-month rate x this household's monthly
LMP-weighted demonstrated capacity (Test Capacity hours only, per the Data Dictionary's
rule that Test Non-Capacity hours do not count toward demonstrated capacity), summed
over the participation months that fall inside the measured window. Demonstrated
capacity nets a PRESCRIPTIVE BASELINE derived the same empirical way: the median ratio
of "DSGS Prescriptive Baseline (MW)" to "Total Nominal Battery Power (MW)" across the
same UDC2/Residential/Stationary/2-hr aggregations (~10.8%), applied to this household's
11.5 kW nameplate. OPPORTUNITY COST = rates.bill_nem (via battery_dispatch_policies.billed,
imported not reimplemented) re-billed for the full measured window, WITH vs WITHOUT the
VPP event dispatch modification -- the same "re-bill the modified year" technique this
repo's other battery/behavior scenarios use, not a lump-sum average-rate credit (CLAUDE.md
1b). NET = GROSS - opportunity cost.

EVENTS OUTSIDE THE WINDOW. This household's measured window (from behavior_rebuild.load(),
read at run time, not hardcoded) starts AFTER 2025-05-01, so every May-June 2025 event and
part of July is outside it; those hours are counted and reported, with NO revenue
attributed to them. The entire 2026 DSGS season (May-Oct 2026, which DOES overlap the
window's tail end) has NO published CEC event data yet (2025's data was not filed until
March 2026; 2026 season data will not be filed until roughly 2027) -- a genuine
data-availability gap, reported separately from the "outside the window" case, with NO
revenue attributed to it either.

ENROLLMENT ELIGIBILITY. Per Olivine's DSGS Option 3 FAQ (https://dsgs.olivineinc.com/faq/,
independently re-fetched this run): equipment eligibility requires "an operational
stationary battery system... capable of discharging at least 1 kW for at least two hours
during a program event," a 2,000 kW/hour discharge cap (irrelevant at residential scale),
and utility permission to parallel the grid (e.g. a Rule 21 tariff) -- no NEM-version
exclusion was found, so this household's NEM 2.0 status is not itself a bar. BUT: "Except
for VPP aggregators of bi-directional EVSEs, participation in the 2026 season is limited
to storage VPP aggregators that participated in October 2025." This household owns no
battery today and so could not have participated in October 2025 -- meaning a NEW storage
enrollment (buying a Powerwall 3 now) could NOT join DSGS Option 3 for the 2026 season at
all under this rule; the earliest a new storage enrollee could plausibly join is a LATER
season. The revenue figures in this artifact are therefore a backtest of what WOULD have
been earned had the household been enrolled during 2025 -- not a currently obtainable
revenue stream for a not-yet-purchased battery. Stated plainly, not buried.

SECOND PROGRAM YEAR (AC1). The event list above covers 2025 in full (dates, hours, and a
payment rate, all sourced). A second program year's event list is also required: CEC TN
266629 ("Staff Analysis of the DSGS Program 2024 Performance Data") documents 2024's Option
3 events -- 26 event hours over 16 days, including three statewide 2-hour events on exact,
unambiguously legible dates (2024-07-10, 2024-09-04, 2024-09-05; see
SECOND_PROGRAM_YEAR_EVENT_LIST_2024 below). No 2024 per-event payment rate was found in this
public summary (it states the rate "varies by month" without publishing the figures) --
disclosed as NOT DETERMINED for 2024 specifically, not guessed from the 2025 rate. Every
2024 event predates this household's measured window regardless, so none of this feeds the
backtest; it satisfies the event-list requirement without affecting any computed figure.

Run from private/verify (usage.csv, household.yaml, rates.py, behavior_rebuild.py,
battery_dispatch_policies.py all beside this file, per the documented sandbox):
  ../../.venv/bin/python dsgs_vpp_backtest.py --build-calendar   # only if the raw
                                                                  # xlsx is present;
                                                                  # rebuilds the
                                                                  # committed CSV
  ../../.venv/bin/python dsgs_vpp_backtest.py                    # normal run: reads
                                                                  # the committed CSV,
                                                                  # writes the results
                                                                  # JSON
"""
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import behavior_rebuild as br             # noqa: E402
import battery_dispatch_policies as bp    # noqa: E402


def _repo_root():
    """Same walk-up-from-CWD-then-from-this-file convention as carbon_fullyear.py
    and household.py, so this script works both from private/verify (the
    documented sandbox) and run in place from analysis/."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:  # pragma: no cover
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"
RAW_XLSX = ROOT / "private" / "1-raw-data" / "dsgs_events" / "dsgs_2025_performance.xlsx"
CALENDAR_CSV = DATA / "dsgs_event_calendar_2025.csv"    # committed, PUBLIC (CEC policy data)
RESULTS_JSON = DATA / "dsgs_vpp_backtest.json"          # committed results artifact

CAP = 13.5                 # Powerwall 3 usable kWh -- battery_dispatch_policies.py's constant
BATT_KW = 11.5             # nameplate discharge power (same source)
ETA = bp.ETA               # sqrt(0.90), one-way efficiency -- imported, not re-declared
PWRQ = bp.PWRQ             # 11.5/4 kWh per 15-min interval -- imported, not re-declared

# Assumed, uncited operating parameter -- see the module docstring's BACKUP RESERVE note.
# Flagged for a parent-level decision; a 0% sensitivity is computed alongside it.
BACKUP_RESERVE_FRAC = 0.20

# Empirically derived from the Monthly Aggregation Dataset (UDC2, Residential, Stationary,
# 2-hour resource duration, rows with positive Demonstrated Capacity) -- see build_calendar().
# One $/kW-month value per 2025 participation month; essentially constant within each month
# across all matching aggregations (this run found <0.03% relative spread per month).
# Recomputed and asserted against the raw file whenever --build-calendar is available;
# hardcoded here (like battery_backup_sims.py's rate constants) so the normal run needs
# only the committed, public CSV, not the 30 MB private raw archive, matching this repo's
# committed-artifact-first convention (nem3_grandfathering.py's RATE_CSV/--build-rates).
MONTHLY_RATE_USD_PER_KW = {
    5: 8.775, 6: 9.074, 7: 16.380, 8: 17.550, 9: 18.720, 10: 10.244,
}
# Prescriptive-baseline fraction of nameplate power for the same aggregation category
# (median of DSGS Prescriptive Baseline (MW) / Total Nominal Battery Power (MW), 78
# UDC2/Residential/2-hr rows). See build_calendar()'s BASELINE_RATIO derivation.
BASELINE_RATIO = 0.107899

# Sanity-check-only alternative payment source already cited in this report's §6 (an
# extrapolation from Tesla/Olivine program terms, not a measurement).
TESLA_SEASON_RANGE_USD = (150, 350)

# Second program year's event list (AC1), read directly off CEC TN 266629 ("Staff Analysis
# of the DSGS Program 2024 Performance Data", filed 2025-10-16) -- see the module docstring's
# SECOND PROGRAM YEAR note for what is and is not sourced here.
SECOND_PROGRAM_YEAR_EVENT_LIST_2024 = {
    "program_year": 2024,
    "source": "CEC docket 22-RENEW-01, TN 266629, filed 2025-10-16, pages 10 and 23",
    "option_3_event_totals": (
        "26 total event hours over 16 days (statewide: 19 hours/13 days; additional "
        "PG&E-only DLAP events: 2 hours/2 days; additional SCE/SDG&E-only DLAP events: "
        "5 hours/3 days), per page 10. Day counts as stated in the source; not "
        "independently re-derived here."),
    "statewide_2hr_event_dates": ["2024-07-10", "2024-09-04", "2024-09-05"],
    "statewide_2hr_event_dates_note": (
        "The three statewide 2-hour VPP events (page 23's chart title and page 20's "
        "'three statewide 2-hour events' annotation agree), each a 2-hour block within "
        "hour-ending 19-21. These are the only 2024 dates legible unambiguously in the "
        "source's charts; the remaining PG&E-only/SCE-SDG&E-only event dates are stated "
        "in the source but not transcribed here to avoid misreading a chart axis."),
    "payment_rate_2024": (
        "NOT DETERMINED. TN 266629 states the incentive rate 'varies by month' (page 8) "
        "but does not publish the actual $/kW-month figures the way the 2025 Monthly "
        "Aggregation Dataset does; no 2024 settlement-level dataset was found. The "
        "earliest publicly available per-event payment rate is 2025's (used as this "
        "backtest's primary rate source)."),
    "replayable": False,
    "replayable_note": (
        "Entirely outside this household's measured window (starts 2025-07-24) -- not "
        "replayable against measured load regardless of rate availability. Zero revenue "
        "attributed, consistent with events_outside_window's 2025 pre-window handling."),
}


# --------------------------------------------------------------------- calendar build
def build_calendar(xlsx_path=RAW_XLSX):
    """Rebuild data/dsgs_event_calendar_2025.csv (and re-derive MONTHLY_RATE_USD_PER_KW /
    BASELINE_RATIO for comparison against the hardcoded constants above) from the raw CEC
    xlsx. NOT needed for the normal backtest run, which reads only the committed CSV --
    matches nem3_grandfathering.py's build_rate_table()/--build-rates split: the 30 MB
    raw archive is private and gitignored, so CI and any checkout without it must still
    be able to run the backtest against the committed, public CSV.

    Restricts to UDC 2 (the candidate-SDG&E inference, see module docstring),
    Residential, Stationary storage, 2-hour resource duration (the category matching a
    hypothetical Powerwall-class residential battery) and every hour where "Is Option 3
    Event Hour?" is True. Where multiple aggregations disagree on event type for the same
    (date, hour), the majority classification wins (ties toward "Test Capacity"). Fails
    closed on any of: an unexpected event type appearing (Capacity/Energy-Only, which the
    Data Dictionary says should not occur in 2025), more than one distinct CAISO LMP for
    the same (date, hour) (would mean the DLAP-per-UDC assumption doesn't hold), or an
    empty result.
    """
    if not xlsx_path.exists():
        raise SystemExit(
            f"build_calendar: missing raw CEC file {xlsx_path}. This needs the private "
            "raw archive (private/1-raw-data/dsgs_events/); the normal backtest run "
            f"does not need this function, only the committed {CALENDAR_CSV}.")
    import openpyxl  # local import: only needed for this private-archive path

    hourly = pd.read_excel(xlsx_path, sheet_name="Hourly Discharge Dataset", engine="openpyxl")
    monthly = pd.read_excel(xlsx_path, sheet_name="Monthly Aggregation Dataset", engine="openpyxl")

    # ---- genuine finding: no real Capacity or Energy-Only events in 2025 ----
    types_seen = set(hourly["Option 3 Event Type"].dropna().unique())
    unexpected = types_seen - {"Test Capacity", "Test Non-Capacity"}
    if unexpected:
        raise SystemExit(
            f"build_calendar: found event type(s) {sorted(unexpected)} the Data "
            "Dictionary says should not occur in 2025 (Capacity/Energy-Only) -- the "
            "'2025 had no real emergency dispatches' finding this script states no "
            "longer holds against the raw file; re-verify before proceeding.")

    cat = hourly[(hourly["UDC (anonymized)"] == "UDC 2")
                 & (hourly["Customer Class"] == "Residential")
                 & (hourly["Storage Type"] == "Stationary")
                 & (hourly["Option 3 Resource duration (hours)"] == 2)]
    ev = cat[cat["Is Option 3 Event Hour?"] == True].copy()  # noqa: E712
    if ev.empty:
        raise SystemExit("build_calendar: zero event-hour rows found for the "
                         "UDC2/Residential/Stationary/2-hr category -- the category "
                         "filter or the raw file's contents have changed.")

    lmp_col = "CAISO LMP ($/MWh)"
    rows = []
    for (date, hend), g in ev.groupby(["Date", "Hour End (1-24)"]):
        lmp_vals = g[lmp_col].unique()
        if len(lmp_vals) != 1:
            raise SystemExit(
                f"build_calendar: {date} HE{hend} has {len(lmp_vals)} distinct CAISO "
                "LMP values across aggregations -- the one-LMP-per-(UDC,hour) "
                "(single DLAP) assumption does not hold; refusing to silently pick one.")
        counts = g["Option 3 Event Type"].value_counts()
        winners = counts[counts == counts.max()].index.tolist()
        etype = "Test Capacity" if "Test Capacity" in winners else winners[0]
        rows.append({
            "date": pd.Timestamp(date).date().isoformat(),
            "hour_end": int(hend),
            "event_type": etype,
            "caiso_lmp_usd_per_mwh": round(float(lmp_vals[0]), 5),
        })
    cal = pd.DataFrame(rows).sort_values(["date", "hour_end"]).reset_index(drop=True)

    # ---- re-derive the payment rate and baseline ratio, for comparison ----
    m = monthly[(monthly["UDC (anonymized)"] == "UDC 2")
                & (monthly["Customer Class"] == "Residential")
                & (monthly["Option 3 Resource duration (hours)"] == 2)].copy()
    pos = m[m["Demonstrated Capacity (MW)"] > 0].copy()
    pos["rate"] = pos["Monthly Capacity Payment ($)"] / (pos["Demonstrated Capacity (MW)"] * 1000)
    derived_rates = pos.groupby("2025 Participation month")["rate"].mean().round(3).to_dict()
    derived_rates = {int(k): float(v) for k, v in derived_rates.items()}
    m["baseline_ratio"] = m["DSGS Prescriptive Baseline (MW)"] / m["Total Nominal Battery Power (MW)"]
    derived_baseline_ratio = round(float(m["baseline_ratio"].median()), 6)

    DATA.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("# UDC \"2\" is an INFERENCE (candidate SDG&E by 2025 enrollment "
                     "scale, cross-checked against the 2024 CEC analysis's real utility "
                     "names -- see analysis/dsgs_vpp_backtest.py's module docstring), "
                     "not a confirmed identity in this anonymized CEC file.\n")
            cal.to_csv(fh, index=False)
        os.replace(tmp, CALENDAR_CSV)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print(f"wrote {CALENDAR_CSV} ({len(cal)} event-hour rows, "
          f"{cal['date'].nunique()} distinct dates)")
    print(f"derived monthly rates ($/kW-month): {derived_rates}")
    print(f"hardcoded MONTHLY_RATE_USD_PER_KW:   {MONTHLY_RATE_USD_PER_KW}")
    for mo, v in derived_rates.items():
        hard = MONTHLY_RATE_USD_PER_KW.get(mo)
        if hard is None or abs(hard - v) > 0.01:
            print(f"  NOTICE: month {mo} derived rate {v} disagrees with hardcoded "
                  f"{hard} by more than 1 cent/kW -- consider updating the constant.")
    print(f"derived baseline ratio: {derived_baseline_ratio} "
          f"(hardcoded BASELINE_RATIO: {BASELINE_RATIO})")
    return cal, derived_rates, derived_baseline_ratio


def load_calendar():
    """Load the committed, public event calendar (skips the leading '#' caveat line)."""
    if not CALENDAR_CSV.exists():
        raise SystemExit(
            f"{CALENDAR_CSV} is missing. Run this script with --build-calendar first "
            "(needs the private raw CEC archive) to create it.")
    cal = pd.read_csv(CALENDAR_CSV, comment="#")
    cal["date"] = pd.to_datetime(cal["date"]).dt.date
    return cal


# --------------------------------------------------------------------------- dispatch
def run_batt_vpp(d, imp0, gen0, cap, event_set, reserve_frac):
    """A close variant of battery_dispatch_policies.run_batt's "greedy" policy.

    For any interval NOT in event_set, the control flow is IDENTICAL to run_batt's
    greedy branch (verified byte-for-byte in test_dsgs_vpp_backtest.py with an empty
    event_set). For an interval inside event_set (a real 2025 DSGS event hour that
    falls inside the household's measured window), the battery ADDITIONALLY discharges
    (exporting any surplus beyond what the household's own load needs right then) up to
    its remaining headroom this interval (PWRQ minus whatever the ordinary greedy step
    already discharged) and its remaining energy above reserve_frac * cap -- the
    behavior a revenue-maximizing VPP aggregator commands, not just business-as-usual
    arbitrage. event_set is a set of (date, floor_hour) tuples, floor_hour = Hour End - 1
    (Hour End 1-24 is CAISO hour-ending; the interval from HH:00 to HH:59 has floor_hour
    HH and belongs to Hour End HH+1).

    Returns (imp, exp, soc_start, event_discharge_kwh, bau_discharge_kwh):
      soc_start          -- SOC at the START of each interval (before this step's action)
      event_discharge_kwh -- the INCREMENTAL discharge forced by event participation
                             beyond what the ordinary greedy step already did
      bau_discharge_kwh  -- the ordinary greedy discharge each interval (the `dd` run_batt
                             would have produced with no VPP participation at all)
    """
    imp = imp0.copy(); exp = gen0.copy()
    soc = cap / 2.0
    p = d.p.values; h = d.hour.values; kw = imp0 * 4
    dates = d.dt.dt.date.values
    floor_h = np.floor(h).astype(int)
    n = len(d)
    is_event = np.fromiter(
        ((dates[i], int(floor_h[i])) in event_set for i in range(n)), dtype=bool, count=n)
    reserve_kwh = reserve_frac * cap
    soc_start = np.empty(n)
    event_discharge = np.zeros(n)
    bau_discharge = np.zeros(n)
    for i in range(n):
        soc_start[i] = soc
        disch_win = (16 <= h[i] < 21) or (p[i] != "sop" and kw[i] < 2.5)
        disch_used = 0.0
        if exp[i] > 0 and not (disch_win and imp[i] > 0):
            c = min(exp[i], (cap - soc) / ETA, PWRQ)
            if c > 0:
                soc += c * ETA; exp[i] -= c
        elif p[i] == "sop":
            take = min(max((cap - soc) / ETA, 0), PWRQ)
            if take > 0:
                soc += take * ETA; imp[i] += take
        elif disch_win:
            dd = min(imp[i], soc * ETA, PWRQ)
            if dd > 0:
                soc -= dd / ETA; imp[i] -= dd; disch_used = dd
        bau_discharge[i] = disch_used
        if is_event[i]:
            headroom_power = max(PWRQ - disch_used, 0.0)
            # Output-capacity from the SOC above reserve, mirroring run_batt's own
            # disch_win cap (dd = min(imp[i], soc * ETA, PWRQ)): the *ETA converts an
            # SOC quantity into the OUTPUT obtainable from it, so soc never drops
            # below reserve_kwh. Comparing extra (an output) against the raw SOC
            # headroom without this factor would let a partial last interval draw
            # soc slightly BELOW the reserve floor.
            headroom_energy = max(soc - reserve_kwh, 0.0) * ETA
            extra = min(headroom_power, headroom_energy)
            if extra > 1e-9:
                soc -= extra / ETA
                exp[i] += extra
                event_discharge[i] = extra
    return imp, exp, soc_start, event_discharge, bau_discharge


def _event_set_and_hours(cal, window_start, window_end):
    """(in-window set of (date, floor_hour), in-window rows, out-of-window rows)."""
    cal = cal.copy()
    cal["in_window"] = cal["date"].apply(lambda d0: window_start <= d0 <= window_end)
    inwin = cal[cal["in_window"]]
    outwin = cal[~cal["in_window"]]
    event_set = {(row.date, row.hour_end - 1) for row in inwin.itertuples()}
    return event_set, inwin.reset_index(drop=True), outwin.reset_index(drop=True)


def backtest(d, cal, reserve_frac=BACKUP_RESERVE_FRAC):
    """The full computation: returns a dict with every figure the artifact needs."""
    window_start = d.dt.min().date()
    window_end = d.dt.max().date()
    event_set, inwin, outwin = _event_set_and_hours(cal, window_start, window_end)

    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)

    # BAU (no VPP participation) -- must match battery_dispatch_policies.py's own
    # "greedy" pw3 figures exactly (cross-checked in the test suite).
    imp_bau, exp_bau, _served_bau, _thru_bau = bp.run_batt(d, imp0, gen0, CAP, "greedy")
    bill_bau = bp.billed(d, imp_bau, exp_bau)

    # With VPP event dispatch.
    imp_vpp, exp_vpp, soc_start, event_kwh, bau_kwh = run_batt_vpp(
        d, imp0, gen0, CAP, event_set, reserve_frac)
    bill_vpp = bp.billed(d, imp_vpp, exp_vpp)
    opportunity_cost = round(bill_vpp - bill_bau, 2)

    # ---- per-event-hour totals (for revenue + miss-rate) ----
    dates = d.dt.dt.date.values
    floor_h = np.floor(d.hour.values).astype(int)
    months = d.dt.dt.month.values
    idx_by_hour = {}
    for i in range(len(d)):
        idx_by_hour.setdefault((dates[i], floor_h[i]), []).append(i)

    baseline_kw = BATT_KW * BASELINE_RATIO
    hour_rows = []
    for row in inwin.itertuples():
        key = (row.date, row.hour_end - 1)
        idxs = idx_by_hour.get(key, [])
        total_kwh = float(sum(event_kwh[j] + bau_kwh[j] for j in idxs))
        soc_at_start = float(soc_start[idxs[0]]) if idxs else None
        nd_kw = total_kwh - baseline_kw    # Demonstrated Net Discharge, this hour
        served_frac = min(total_kwh / BATT_KW, 1.0) if BATT_KW else 0.0
        is_miss = total_kwh < 1.0          # essentially nothing delivered: SOC-exhausted
        hour_rows.append({
            "date": row.date.isoformat(), "hour_end": row.hour_end,
            "event_type": row.event_type, "caiso_lmp_usd_per_mwh": row.caiso_lmp_usd_per_mwh,
            "month": row.date.month, "total_discharge_kwh": round(total_kwh, 4),
            "soc_at_hour_start_kwh": round(soc_at_start, 4) if soc_at_start is not None else None,
            "demonstrated_net_discharge_kw": round(nd_kw, 4),
            "served_fraction_of_nameplate": round(served_frac, 4),
            "miss": is_miss,
        })

    n_hours_in_window = len(hour_rows)
    n_misses = sum(1 for r in hour_rows if r["miss"])
    miss_rate = round(n_misses / n_hours_in_window, 4) if n_hours_in_window else None

    # ---- monthly LMP-weighted demonstrated capacity -> gross revenue (Test Capacity only) ----
    monthly_gross = {}
    demonstrated_capacity_by_month = {}
    cap_rows = [r for r in hour_rows if r["event_type"] == "Test Capacity"]
    by_month = {}
    for r in cap_rows:
        by_month.setdefault(r["month"], []).append(r)
    for mo, rows in sorted(by_month.items()):
        num = sum(r["demonstrated_net_discharge_kw"] * r["caiso_lmp_usd_per_mwh"] for r in rows)
        den = sum(r["caiso_lmp_usd_per_mwh"] for r in rows)
        dc_kw = num / den if den else 0.0
        demonstrated_capacity_by_month[mo] = round(dc_kw, 4)
        rate = MONTHLY_RATE_USD_PER_KW.get(mo)
        if rate is None:
            raise SystemExit(f"backtest: no MONTHLY_RATE_USD_PER_KW entry for month {mo} "
                             "-- the event calendar now spans a month this constant table "
                             "does not cover; extend it (see build_calendar()).")
        monthly_gross[mo] = round(max(dc_kw, 0.0) * rate, 2)
    gross_revenue = round(sum(monthly_gross.values()), 2)
    net_revenue = round(gross_revenue - opportunity_cost, 2)

    # ---- sensitivity: 0% reserve (no reserve held) ----
    imp_vpp0, exp_vpp0, soc_start0, event_kwh0, bau_kwh0 = run_batt_vpp(
        d, imp0, gen0, CAP, event_set, 0.0)
    bill_vpp0 = bp.billed(d, imp_vpp0, exp_vpp0)
    opp_cost0 = round(bill_vpp0 - bill_bau, 2)
    monthly_gross0 = {}
    for mo, rows in sorted(by_month.items()):
        total0 = {}
        for r in rows:
            key = (dt.date.fromisoformat(r["date"]), r["hour_end"] - 1)
            idxs = idx_by_hour.get(key, [])
            total0[r["date"], r["hour_end"]] = float(sum(event_kwh0[j] + bau_kwh0[j] for j in idxs))
        num = sum((total0[r["date"], r["hour_end"]] - baseline_kw) * r["caiso_lmp_usd_per_mwh"] for r in rows)
        den = sum(r["caiso_lmp_usd_per_mwh"] for r in rows)
        dc_kw = num / den if den else 0.0
        rate = MONTHLY_RATE_USD_PER_KW[mo]
        monthly_gross0[mo] = round(max(dc_kw, 0.0) * rate, 2)
    gross_revenue0 = round(sum(monthly_gross0.values()), 2)
    net_revenue0 = round(gross_revenue0 - opp_cost0, 2)
    misses0 = 0
    for r in hour_rows:
        key = (dt.date.fromisoformat(r["date"]), r["hour_end"] - 1)
        idxs = idx_by_hour.get(key, [])
        total0 = float(sum(event_kwh0[j] + bau_kwh0[j] for j in idxs))
        if total0 < 1.0:
            misses0 += 1
    miss_rate0 = round(misses0 / n_hours_in_window, 4) if n_hours_in_window else None

    # ---- Tesla program-terms sanity check ----
    tesla_note = (
        f"§6's existing program-terms estimate cites ${TESLA_SEASON_RANGE_USD[0]}-"
        f"${TESLA_SEASON_RANGE_USD[1]}/season; this backtest's empirically-rated gross "
        f"revenue for the {len(by_month)} in-window participation month(s) is "
        f"${gross_revenue:,.2f} -- same order of magnitude as a partial (not full "
        "6-month) season at the program-terms rate.")

    result = {
        "hypothetical": True,
        "household_has_battery_today": False,
        "battery_config": {"usable_kwh": CAP, "power_kw": BATT_KW, "round_trip_eta": 0.90},
        "measured_window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "udc_identity_caveat": (
            "UDC 2 in the CEC anonymized file is an INFERENCE (candidate SDG&E) by "
            "2025 enrollment scale, cross-checked against the 2024 CEC analysis's real "
            "utility names showing the same relative ordering one year earlier -- not "
            "a confirmed identity. See this script's module docstring for the full "
            "site-month and nameplate-MW figures."),
        "finding_2025_no_real_emergency_events": (
            "0 'Capacity' (LMP-triggered) and 0 'Energy-Only' event hours occurred in "
            "2025 (confirmed against the raw Data Dictionary's own text and against "
            "all 361,008 Hourly Discharge Dataset rows). Every 2025 event hour is "
            "'Test Capacity' or 'Test Non-Capacity' -- a mandatory monthly test "
            "dispatch, not a real grid emergency."),
        "finding_2026_enrollment_eligibility": (
            "Per Olivine's DSGS Option 3 FAQ (https://dsgs.olivineinc.com/faq/): "
            "'Except for VPP aggregators of bi-directional EVSEs, participation in "
            "the 2026 season is limited to storage VPP aggregators that participated "
            "in October 2025.' This household owns no battery today and could not "
            "have participated in October 2025, so a NEW storage enrollment (buying "
            "a Powerwall 3 now) could not join DSGS Option 3 for the 2026 season at "
            "all -- only a later season. These backtested figures are retrospective "
            "(what 2025 participation would have earned), not a currently obtainable "
            "revenue stream for a not-yet-purchased battery."),
        "backup_reserve_caveat": (
            "BACKUP_RESERVE_FRAC=0.20 is an ASSUMED, uncited operating parameter -- "
            "no reserve figure is established elsewhere in this repository (checked "
            "against battery_backup_sims.py and extended_findings.py; the existing "
            "outage-endurance sims assume a FULLY charged battery at outage start, "
            "the opposite convention). A 0% (no reserve) sensitivity is included "
            "for comparison."),
        "payment_rate_source": (
            "EMPIRICAL: Monthly Capacity Payment ($) / Demonstrated Capacity (MW) "
            "from the Monthly Aggregation Dataset, UDC2/Residential/2-hr rows with "
            "positive demonstrated capacity (essentially constant within each month). "
            "Tesla's $150-350/season program-terms figure (already cited in §6) is "
            "used only as an order-of-magnitude sanity check, not as the payment source."),
        "events_outside_window": {
            "pre_window_count": int(len(outwin[outwin["date"] < window_start])),
            "pre_window_dates": sorted(outwin[outwin["date"] < window_start]["date"]
                                        .astype(str).unique().tolist()),
            "season_2026_note": (
                "The 2026 DSGS season (May-Oct 2026) overlaps the tail of the "
                "measured window (through 2026-07-23), but the CEC has not yet "
                "published 2026 performance data (2025's data was not filed until "
                "March 2026) -- a data-availability gap, not an extrapolatable one. "
                "0 events counted, 0 revenue attributed."),
        },
        "events_in_window": {
            "count": n_hours_in_window,
            "dates": sorted(inwin["date"].astype(str).unique().tolist()),
            "test_capacity_hours": sum(1 for r in hour_rows if r["event_type"] == "Test Capacity"),
            "test_non_capacity_hours": sum(1 for r in hour_rows if r["event_type"] == "Test Non-Capacity"),
        },
        "miss_rate": {
            "reserve_20pct": {"misses": n_misses, "total": n_hours_in_window, "rate": miss_rate},
            "reserve_0pct_sensitivity": {"misses": misses0, "total": n_hours_in_window, "rate": miss_rate0},
            "note": (
                "Counterintuitive but genuine, checked hour-by-hour: the 0% reserve "
                "sensitivity has a HIGHER miss count than the 20% reserve case despite "
                "higher total gross kWh. On dates with two consecutive event hours, 0% "
                "reserve lets the first hour draw the battery down further, sometimes "
                "leaving nothing for the second (a complete miss) where 20% reserve's "
                "more conservative first-hour draw leaves just enough for a small "
                "second-hour delivery that clears the miss threshold. More aggressive "
                "extraction trades a few complete misses for a higher total -- a real "
                "tradeoff, not an inversion bug (see e.g. 2025-08-22 HE19/HE20 in "
                "hour_detail)."),
        },
        "revenue": {
            "reserve_20pct": {
                "gross_usd": gross_revenue,
                "opportunity_cost_usd": opportunity_cost,
                "net_usd": net_revenue,
                "monthly_gross_usd": {str(k): v for k, v in monthly_gross.items()},
                "monthly_demonstrated_capacity_kw": {str(k): v for k, v in demonstrated_capacity_by_month.items()},
            },
            "reserve_0pct_sensitivity": {
                "gross_usd": gross_revenue0,
                "opportunity_cost_usd": opp_cost0,
                "net_usd": net_revenue0,
                "monthly_gross_usd": {str(k): v for k, v in monthly_gross0.items()},
            },
        },
        "second_program_year_event_list_2024": SECOND_PROGRAM_YEAR_EVENT_LIST_2024,
        "tesla_sanity_check_note": tesla_note,
        "opportunity_cost_note": (
            "Computed here, not assumed, and it came out small and slightly NEGATIVE "
            "(the re-billed VPP-dispatch year costs marginally LESS than the "
            "no-VPP/BAU year, before counting the DSGS payment at all). Traced to the "
            "monthly per-TOU-period NEM netting engine (rates.bill_nem): DSGS event "
            "hours fall inside the 4-9pm on-peak window, already this household's "
            "highest-value discharge time under ordinary price-aware dispatch, so "
            "forcing extra export there and drawing the battery down a bit further "
            "mostly reduces future SOC availability during CHEAPER off-peak/"
            "super-off-peak hours (which the overnight SOP grid top-up refills "
            "anyway) rather than costing expensive on-peak service later. This is a "
            "real, computed NEM-netting effect specific to this household's TOU "
            "schedule and usage pattern, not an assumption."),
        "baseline_kw": round(baseline_kw, 4),
        "monthly_rate_usd_per_kw": {str(k): v for k, v in MONTHLY_RATE_USD_PER_KW.items()},
        "hour_detail": hour_rows,
        "bau_battery_bill_usd": round(bill_bau, 2),
        "notes": {
            "engine": "battery_dispatch_policies.run_batt (BAU) / run_batt_vpp (event "
                      "dispatch, this script) + rates.bill_nem via "
                      "battery_dispatch_policies.billed",
            "category_filter": "UDC2 (candidate SDG&E) x Residential x Stationary x "
                               "2-hour resource duration (matches a Powerwall-class "
                               "residential battery)",
            "event_hour_union_caveat": (
                "The committed calendar is the UNION of distinct (date, hour) event "
                "slots across ~14 aggregations in this category, since a single real "
                "household sees only ONE aggregation's own schedule and anonymization "
                "prevents identifying which. This is an upper bound on event "
                "FREQUENCY, disclosed, not a wrong-but-precise single-aggregation "
                "schedule."),
        },
    }
    return result


def main():
    cal = load_calendar()
    d = br.load()
    result = backtest(d, cal)

    DATA.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(result, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, RESULTS_JSON)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    print(f"wrote {RESULTS_JSON}")
    print(f"events in window: {result['events_in_window']['count']} hours, "
          f"{len(result['events_in_window']['dates'])} dates")
    print(f"events pre-window (unreplayable): "
          f"{result['events_outside_window']['pre_window_count']} hours")
    print(f"miss rate (20% reserve): {result['miss_rate']['reserve_20pct']}")
    print(f"gross/opportunity-cost/net (20% reserve): "
          f"${result['revenue']['reserve_20pct']['gross_usd']:,.2f} / "
          f"${result['revenue']['reserve_20pct']['opportunity_cost_usd']:,.2f} / "
          f"${result['revenue']['reserve_20pct']['net_usd']:,.2f}")
    print(f"gross/opportunity-cost/net (0% reserve sensitivity): "
          f"${result['revenue']['reserve_0pct_sensitivity']['gross_usd']:,.2f} / "
          f"${result['revenue']['reserve_0pct_sensitivity']['opportunity_cost_usd']:,.2f} / "
          f"${result['revenue']['reserve_0pct_sensitivity']['net_usd']:,.2f}")


if __name__ == "__main__":
    if "--build-calendar" in sys.argv:
        build_calendar()
    else:
        main()
