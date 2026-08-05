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
to a real provider (Tesla, etc.), this backtest's HEADLINE gross/net/kWh/miss-rate
figures use the UNION of all distinct event (date, hour) slots seen by ANY
UDC2/Residential/Stationary/2-hr aggregation as the household's hypothetical calendar (a
deliberately inclusive choice: more candidate event hours than any single real household
actually saw, so this is an upper-BOUND SCENARIO on event FREQUENCY, not a point estimate
of what this household would actually have earned). Where aggregations disagree on event
type for the same hour, the MAJORITY classification is used (tie broken toward "Test
Capacity", the more consequential/less common category) -- 60 of 68 hours (88%) have
unanimous agreement, so this affects a small minority of hours.

NOT stopping at the union: per_aggregation_sensitivity() (see its docstring) runs this
SAME backtest independently against each of the ~14 individual aggregations' OWN
calendars (build_per_aggregation_calendars()) and reports the resulting min/max net
revenue and miss rate across them -- the range a real single-aggregation household could
actually have seen. main() embeds this as the result's "per_aggregation_sensitivity"
field whenever the private raw archive is available. The union-based headline figures
are reported alongside this range, explicitly relabeled as the upper bound of it, not as
an unqualified "$X/yr" point estimate.

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

EVENT-HOUR-ONLY RESERVE FLOOR -- NOT A STANDING BACKUP-RESERVE SETTING, DECIDED (issue
#10 second adversarial review, Finding 3). The issue text asserts "§6's outage work
establishes what reserve the household would plausibly hold." This was checked against
analysis/battery_backup_sims.py and analysis/extended_findings.py and found NOT TRUE as
stated: no numeric backup-reserve fraction exists anywhere in this repository.
battery_backup_sims.py's own outage endurance sims explicitly "assume a full battery at
outage start" (see index.html's existing prose, unchanged here) -- i.e. the OPPOSITE
convention, no reserve held back in advance. BACKUP_RESERVE_FRAC below (20%) is
therefore an UNCITED, ASSUMED operating parameter chosen here, not derived from this
repo's existing analysis or from a fetched Tesla source (Tesla's backup-reserve support
pages returned HTTP 403 to this run's fetch). A 0% (no reserve) sensitivity is computed
alongside the primary 20% case so the report can see how much the assumption matters;
both are in the committed artifact.

BACKUP_RESERVE_FRAC is enforced ONLY during a declared DSGS event hour (run_batt_vpp()'s
disch_win branch and its event-forcing block both cap against it when is_event[i] is
true; see DISPATCH MODEL below) -- it does NOT reduce SOC available to ordinary, non-VPP
arbitrage in the hours before an event, so this is an EVENT-HOUR-ONLY dispatch floor, not
a model of a continuously-held Powerwall backup-reserve setting (which, on a real
Powerwall, would apply at all times, including in the hours leading up to an event, and
so would leave LESS reserve actually available by the time a declared event starts than
this event-hour-only floor implies). This is a deliberate scoping decision, not an
oversight: it matches the issue's own framing ("a backup reserve reduces dispatchable
capacity DURING events") and keeps the tested empty-event_set byte-identity guarantee to
battery_dispatch_policies.run_batt intact (a standing floor would also constrain the
household's own everyday no-VPP arbitrage, which needs its own separate justification and
is out of scope here). Every mention of this parameter in this artifact, the report, and
TECHNICAL.md is labeled "event-hour-only" for this reason -- it is NOT the same claim as
"this household holds a 20% reserve on its Powerwall at all times."

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
business-as-usual arbitrage. The reserve floor binds on the WHOLE event hour, not just
this event-forced increment: the ordinary/BAU-equivalent greedy discharge is also capped
at the reserve floor during an event hour (see run_batt_vpp()'s docstring), so combined
ordinary-plus-event discharge cannot push SOC below BACKUP_RESERVE_FRAC * capacity. An
event hour where SOC is already at or below the reserve floor cannot be served further:
this is the SOC-constrained MISS the issue asks for.

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

PARTIAL SEASON, STATED PLAINLY (not just implied by the exclusion above). The 2025 DSGS
season runs May-October; this backtest's revenue figures cover ONLY 2025-07-24 through
2025-10-31 (the household's measured window), a genuine subset of one season, not a
complete season and not a full year. The excluded 2025-05-01..07-23 portion (22 union
event hours) is unreplayable -- no measured load exists for it in ANY year -- so a
full-season or annualized figure is NOT DETERMINED, not extrapolated from this partial
figure. A complete season would very likely earn MORE than the partial figure reported
here (it strictly adds event hours, not removes them), but by how much is not computable
without measured load for May-July; this backtest does not guess at it. See
"partial_season_caveat" in the result dict and the tornado-lever framing in
extended_findings.py, which must carry the same caveat wherever this figure is quoted as
an annual input.

ENROLLMENT ELIGIBILITY. Per Olivine's DSGS Option 3 FAQ (https://dsgs.olivineinc.com/faq/,
independently re-fetched this run): equipment eligibility requires "an operational
stationary battery system... capable of discharging at least 1 kW for at least two hours
during a program event," a 2,000 kW/hour discharge cap (irrelevant at residential scale),
and utility permission to parallel the grid (e.g. a Rule 21 tariff) -- no NEM-version
exclusion was found, so this household's NEM 2.0 status is not itself a bar.

CORRECTED (issue #10 third adversarial review) -- the 2026 restriction is on
AGGREGATORS, not households. The CEC's own Program Guidelines (TN 269649, Section
II.C.1, Appendix A -- the authoritative source, checked directly, not just the FAQ
paraphrase) say: "Except for VPP aggregators of bi-directional EVSEs, participation
in the 2026 program season is limited to storage VPP aggregators that participated
in October 2025." This freezes WHICH AGGREGATORS (e.g. Tesla's own VPP program) may
receive 2026 funding at all -- it does NOT say a new household's battery is barred
from joining an aggregator that itself already qualifies. An earlier version of this
script concluded the household "could not join at all," conflating the
aggregator-level restriction with a household-level one; that conclusion is
retracted here as unsupported by the source. What the Guidelines DO additionally
establish (Appendix A): each qualifying aggregator's total 2026 compensation is
CAPPED at its own October 2025 pro-rata share of available program funds, so
enrolling new sites in 2026 does not increase what the CEC pays that aggregator --
a real economic disincentive, not a rule against it. Whether a specific aggregator
(Tesla is a listed Option 3 provider generally) both participated in October 2025
specifically and would accept a new residential site under that funding cap is NOT
DETERMINED from the public, anonymized CEC data (the anonymization means this
household's actual aggregator cannot be identified -- see the UDC-identity
ambiguity above); confirm directly with the battery manufacturer's VPP program.
The revenue figures in this artifact are a backtest of what WOULD have been earned
had the household been enrolled during 2025 -- not a claim about what a
not-yet-purchased battery could earn starting immediately, in either direction.

GRANDFATHERING INTERACTION -- SEARCHED, NOTHING FOUND. The issue asks separately whether
DSGS ENROLLMENT (as opposed to mere battery ownership) affects NEM 2.0 grandfathering in
any way. Searched the authoritative source directly: the CEC's DSGS Program Guidelines,
Fifth Edition (89 pages, efiling.energy.ca.gov/GetDocument.aspx?tn=269649) for every
occurrence of "NEM", "net energy metering", "net billing", "NBT", and "grandfath" -- the
only 4 "NEM" hits are all part of the unrelated term "VNEM" (Virtual Net Energy Metering,
a multi-tenant billing arrangement, not this household's individual tariff), and "grandfath"
has zero hits. Also checked Olivine's DSGS Option 3 FAQ (already fetched above) -- no NEM
mention there either. Consistent with this repo's own finding on the actual NEM 2.0
grandfathering forfeiture mechanism (nem3_grandfathering.py, issue #9, citing SDG&E's
tariff Schedule NEM Special Condition 7(b) / D.16-04-020): forfeiture is triggered by
ADDING GENERATING OR STORAGE EQUIPMENT beyond a nameplate threshold, not by enrolling
already-installed equipment in a demand-response/VPP program. No source found that DSGS
enrollment itself has any grandfathering effect -- a searched, sourced absence, not an
assumption.

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
BATT_KW = 11.5             # nameplate DISCHARGE power (same source)
CHARGE_KW = bp.CHARGE_KW   # nameplate CHARGE power (5 kW, issue #40) -- imported, not re-declared
ETA = bp.ETA               # sqrt(0.90), one-way efficiency -- imported, not re-declared
PWRQ = bp.PWRQ             # 11.5/4 kWh per 15-min interval -- imported, not re-declared

# Assumed, uncited operating parameter -- see the module docstring's EVENT-HOUR-ONLY
# RESERVE FLOOR note. Enforced only during a declared event hour, not a continuously-held
# standing reserve; a 0% sensitivity is computed alongside it.
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
    print(f"derived baseline ratio: {derived_baseline_ratio} "
          f"(hardcoded BASELINE_RATIO: {BASELINE_RATIO})")
    # FAIL CLOSED on drift (issue #10 third Codex review round): this used to be a
    # printed NOTICE only, so a --build-calendar run against an updated raw workbook
    # could publish a new calendar while every subsequent normal run kept using the
    # now-stale hardcoded MONTHLY_RATE_USD_PER_KW/BASELINE_RATIO constants, silently
    # combining a new event calendar with old settlement inputs. The calendar CSV
    # (event dates/hours/LMP) is independent of these Python constants and has
    # already been written above; this check exists to force a maintainer to update
    # the constants before the tool is considered done, not to protect the CSV write.
    drift = [f"month {mo}: derived {v} vs hardcoded {MONTHLY_RATE_USD_PER_KW.get(mo)}"
             for mo, v in derived_rates.items()
             if MONTHLY_RATE_USD_PER_KW.get(mo) is None
             or abs(MONTHLY_RATE_USD_PER_KW[mo] - v) > 0.01]
    if abs(derived_baseline_ratio - BASELINE_RATIO) > 0.001:
        drift.append(f"baseline ratio: derived {derived_baseline_ratio} vs "
                     f"hardcoded {BASELINE_RATIO}")
    if drift:
        raise SystemExit(
            "build_calendar: the calendar CSV was written, but these hardcoded "
            "constants have drifted from what the raw archive now derives -- "
            "update MONTHLY_RATE_USD_PER_KW/BASELINE_RATIO in this script before "
            "any normal run uses them: " + "; ".join(drift))
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


# ------------------------------------------------------- per-aggregation sensitivity
def build_per_aggregation_calendars(xlsx_path=RAW_XLSX):
    """Defect-#2 fix (issue #10 review): build EACH individual anonymized
    aggregation's OWN event calendar, not the cross-aggregation UNION
    build_calendar() uses. A real household belongs to exactly ONE aggregation's
    actual dispatch schedule (whichever VPP company it enrolls with), not the union
    of all ~14 aggregations in the UDC2/Residential/Stationary/2-hr category -- the
    union is a deliberately inclusive upper bound on event FREQUENCY (see
    build_calendar()'s docstring), not a real household's experience.

    Same category filter and 2025-no-real-events check as build_calendar(). Unlike
    build_calendar(), there is no majority-vote step: each aggregation reports its
    own event hours directly, so a (date, hour_end) pair can appear in some
    aggregations' calendars and not others', and different aggregations can (and do)
    disagree on event_type for a shared hour -- that disagreement is exactly the
    schedule-identity ambiguity this function preserves instead of resolving.

    Returns {aggregation_id: calendar_df}, one entry per distinct aggregation, each
    DataFrame in the SAME shape load_calendar() produces (date, hour_end, event_type,
    caiso_lmp_usd_per_mwh) so it can be passed straight into backtest().
    """
    if not xlsx_path.exists():
        raise SystemExit(
            f"build_per_aggregation_calendars: missing raw CEC file {xlsx_path}. "
            "This needs the private raw archive (private/1-raw-data/dsgs_events/); "
            "the normal backtest run does not need this function.")

    hourly = pd.read_excel(xlsx_path, sheet_name="Hourly Discharge Dataset", engine="openpyxl")
    types_seen = set(hourly["Option 3 Event Type"].dropna().unique())
    unexpected = types_seen - {"Test Capacity", "Test Non-Capacity"}
    if unexpected:
        raise SystemExit(
            f"build_per_aggregation_calendars: found event type(s) {sorted(unexpected)} "
            "the Data Dictionary says should not occur in 2025 -- re-verify before "
            "proceeding.")

    cat = hourly[(hourly["UDC (anonymized)"] == "UDC 2")
                 & (hourly["Customer Class"] == "Residential")
                 & (hourly["Storage Type"] == "Stationary")
                 & (hourly["Option 3 Resource duration (hours)"] == 2)]
    ev = cat[cat["Is Option 3 Event Hour?"] == True].copy()  # noqa: E712
    if ev.empty:
        raise SystemExit("build_per_aggregation_calendars: zero event-hour rows "
                         "found for the UDC2/Residential/Stationary/2-hr category.")

    calendars = {}
    for agg_id, g in ev.groupby("Aggregation Identifier (anonymized)"):
        sub = g[["Date", "Hour End (1-24)", "Option 3 Event Type",
                 "CAISO LMP ($/MWh)"]].copy()
        sub.columns = ["date", "hour_end", "event_type", "caiso_lmp_usd_per_mwh"]
        sub["date"] = pd.to_datetime(sub["date"]).dt.date
        sub["hour_end"] = sub["hour_end"].astype(int)
        sub["caiso_lmp_usd_per_mwh"] = sub["caiso_lmp_usd_per_mwh"].astype(float).round(5)
        if sub.duplicated(subset=["date", "hour_end"]).any():
            raise SystemExit(
                f"build_per_aggregation_calendars: aggregation {agg_id!r} has more "
                "than one row for the same (date, hour_end) -- the one-row-per-"
                "aggregation-per-hour assumption this function relies on does not "
                "hold; refusing to silently pick one.")
        calendars[agg_id] = sub.sort_values(["date", "hour_end"]).reset_index(drop=True)
    return calendars


def per_aggregation_sensitivity(d, xlsx_path=RAW_XLSX, reserve_frac=BACKUP_RESERVE_FRAC,
                                charge_kw=None):
    """Defect-#2 fix: run the SAME backtest() pipeline independently against each
    individual aggregation's OWN calendar (build_per_aggregation_calendars()), and
    summarize the resulting spread of outcomes across the aggregations a real
    household might actually belong to. Requires the private raw CEC archive --
    per-aggregation identity is not present in the committed, public calendar CSV,
    which stores only the union (see build_calendar()).

    Returns a dict with the full per-aggregation breakdown plus min/max net revenue
    and miss rate across all aggregations found, so the union-based headline figure
    (computed elsewhere from the committed calendar) can be reported alongside this
    range rather than as an unqualified point estimate. Also captures each
    aggregation's OWN prestaged_sensitivity (issue #53, Codex adversarial review):
    backtest(d, cal, ...) derives event_set entirely from the `cal` it is given, so
    calling it once per aggregation here already scopes run_batt_vpp(prestage=True)
    to that single aggregation's own calendar -- the union-calendar prestaged figure
    computed elsewhere (this same backtest() function, called with the committed
    union calendar) is what over-states realizable pre-staging benefit by assuming
    foreknowledge of every aggregation's combined schedule; THIS min/max range is
    the corresponding real, single-aggregation-scoped answer.
    """
    calendars = build_per_aggregation_calendars(xlsx_path)
    per_agg = {}
    for agg_id, cal in calendars.items():
        r = backtest(d, cal, reserve_frac=reserve_frac, charge_kw=charge_kw)
        rev = r["revenue"]["reserve_20pct"]
        miss = r["miss_rate"]["reserve_20pct"]
        pre = r["prestaged_sensitivity"]
        per_agg[agg_id] = {
            "n_event_hours_in_window": r["events_in_window"]["count"],
            "gross_usd": rev["gross_usd"],
            "opportunity_cost_usd": rev["opportunity_cost_usd"],
            "net_usd": rev["net_usd"],
            "total_discharge_kwh": rev["total_discharge_kwh"],
            "miss_rate": miss["rate"],
            "prestaged_net_usd": pre["net_usd"],
            "prestaged_miss_rate": pre["miss_rate"]["rate"],
            "prestaged_delta_net_usd": pre["delta_vs_reactive"]["net_usd"],
        }
    if not per_agg:  # pragma: no cover -- build_per_aggregation_calendars already
                     # fails closed on an empty result, so this can't be reached
                     # except by a future refactor; kept as a second fail-closed line.
        raise SystemExit("per_aggregation_sensitivity: zero aggregations found.")

    nets = {k: v["net_usd"] for k, v in per_agg.items()}
    miss_rates = {k: v["miss_rate"] for k, v in per_agg.items()
                  if v["miss_rate"] is not None}
    pre_nets = {k: v["prestaged_net_usd"] for k, v in per_agg.items()}
    pre_miss_rates = {k: v["prestaged_miss_rate"] for k, v in per_agg.items()
                      if v["prestaged_miss_rate"] is not None}
    min_agg = min(nets, key=nets.get)
    max_agg = max(nets, key=nets.get)
    pre_min_agg = min(pre_nets, key=pre_nets.get)
    pre_max_agg = max(pre_nets, key=pre_nets.get)
    return {
        "n_aggregations": len(per_agg),
        "net_usd_min": nets[min_agg],
        "net_usd_min_aggregation": min_agg,
        "net_usd_max": nets[max_agg],
        "net_usd_max_aggregation": max_agg,
        "miss_rate_min": min(miss_rates.values()) if miss_rates else None,
        "miss_rate_max": max(miss_rates.values()) if miss_rates else None,
        "prestaged_net_usd_min": pre_nets[pre_min_agg],
        "prestaged_net_usd_min_aggregation": pre_min_agg,
        "prestaged_net_usd_max": pre_nets[pre_max_agg],
        "prestaged_net_usd_max_aggregation": pre_max_agg,
        "prestaged_miss_rate_min": min(pre_miss_rates.values()) if pre_miss_rates else None,
        "prestaged_miss_rate_max": max(pre_miss_rates.values()) if pre_miss_rates else None,
        "note": (
            "A real household belongs to exactly ONE of these aggregations' actual "
            "dispatch schedules, not the union the rest of this artifact's headline "
            "figures use. The union-based figures are an inclusive upper bound on "
            "event FREQUENCY (see build_calendar()'s docstring), not a point "
            "estimate of what this household would actually have earned -- this "
            "range is what a real single-aggregation household could have earned "
            "instead, at the same 20% reserve. The prestaged_* fields are the same "
            "range for the event-aware SOC pre-staging sensitivity (issue #53): "
            "each aggregation's own prestaged figure uses ONLY that aggregation's "
            "own calendar for foreknowledge, never the union -- this is the "
            "realizable pre-staging range. NOTE (Codex adversarial review, issue "
            "#53, third pass): the union-calendar prestaged headline it is being "
            "compared against is an inclusive event-FREQUENCY scenario, not a "
            "proven bound on dollar economics in either direction -- do not read "
            "this range as bracketing a ceiling the union headline over-states; "
            "the union total is not proven to sit above every aggregation's own "
            "prestaged figure (see the reactive figures elsewhere in this artifact, "
            "which already demonstrate the union total can land INSIDE the "
            "per-aggregation range rather than above it)."),
        "per_aggregation": per_agg,
    }


# --------------------------------------------------------------------------- dispatch
# EVENT-AWARE SOC PRE-STAGING -- DECISION RECORD (issue #53). The reactive dispatch
# below (prestage=False, the default, and the ONLY mode used for every existing
# committed figure) only reacts to is_event[i] once the CURRENT interval is a real
# DSGS event hour. But DSGS's monthly "Test Capacity"/"Test Non-Capacity" events ARE
# scheduled in advance -- data/dsgs_event_calendar_2025.csv exists as committed data
# for exactly that reason, and issue #10 already established 2025 had ZERO real
# emergency dispatches, meaning every real 2025 event this household could have seen
# was one of these calendar-published monthly tests, not a surprise. DSGS capacity
# payments are based on demonstrated performance DURING the test, so a rational,
# revenue-motivated household has a genuine financial incentive to plan around a
# known calendar rather than treat it as a surprise. DECISION: model it (this is not
# a re-litigation of the issue's own "decide whether to model this at all" draft
# criterion -- that decision was made once, here, with this reasoning, and is not
# revisited per-call). This is a pure dispatch-logic question over data already read
# by this same script (event_set, already built from the committed calendar below);
# no new data source, no new calendar-fetching, no multi-day-ahead foresight (the
# issue's own framing -- and the calendar's own shape, see below -- is about a
# SAME-day scheduled test, not a household planning multiple days out).
#
# THE NOTICE CHAIN -- WHAT IS AND ISN'T CONFIRMED (Codex adversarial review, issue
# #53, third pass: an earlier version of this note asserted "scheduled in advance"
# without citing WHO knows how far ahead; this household's own category is DSGS
# Option 3, Storage VPP -- see the module docstring's UDC2/Residential/Stationary/
# 2-hr filter). CONFIRMED, from the CEC's own published DSGS Option 3 program FAQ
# (dsgs.olivineinc.com/faq/, checked 2026-08): "VPP aggregators must notify the CEC
# of planned test events no later than 3:00 p.m. on the day preceding the planned
# test event" -- so the AGGREGATOR itself has at minimum day-ahead knowledge of its
# own scheduled test (it is the one scheduling it and reporting that schedule to the
# CEC). NOT CONFIRMED by that source: whether, or how far ahead, an individual
# ENROLLED CUSTOMER is notified by their aggregator. This script therefore models
# same-day, at-the-start-of-day household awareness as the SIMPLEST version of a
# confirmed real information channel (the aggregator plainly has it; a customer
# actively checking their own aggregator's app/notifications same-day is a modest,
# not extravagant, assumption on top of a documented fact) -- not a household
# planning multiple days out, which the issue's own framing and this citation both
# stop short of, and not a claim that every DSGS household definitely receives or
# acts on customer-facing notice, which is NOT DETERMINED from this source.
#
# THE RULE, IMPLEMENTED. Independently re-verified against the committed calendar
# before choosing this rule (not just trusted): all 34 distinct 2025 event DATES
# have their 1-2 event HOURS entirely within hour_end 17-21 (floor_hour 16-20) --
# i.e. squarely inside this function's own `disch_win` window (16 <= h < 21), the
# SAME window the reactive/BAU-equivalent branch already treats as ordinary
# peak-arbitrage-discharge territory. That means on an event day, the reactive
# dispatch below can spend SOC on ordinary bill-arbitrage discharge in the peak
# hours strictly BEFORE that day's event hour(s) arrive, potentially leaving less
# SOC available once the event-forcing block (further down this same function,
# untouched by prestage) tries to maximize demonstrated capacity. The pre-staging
# rule: on any calendar DATE with at least one hour in event_set, suppress the
# ordinary disch_win-triggered arbitrage discharge -- and ONLY that branch, not the
# event-forcing block, which is unchanged -- for hours on that date STRICTLY BEFORE
# the date's first event hour. From the first event hour onward (including the
# event hour itself), behavior is completely unchanged from the reactive path. A
# date with no event hour in event_set is entirely unaffected. This is the
# suppress-arbitrage-before-the-known-test rule as scoped by the issue; no
# multi-day lookahead is implemented (event_set's own (date, floor_hour) shape
# gives this function no visibility into any date but the current one anyway).
#
# WHAT "SUPPRESS" MEANS, MECHANICALLY. Only the disch_win elif's own ACTIVATION is
# gated (a separate disch_win_active flag); the solar-surplus charge branch's own
# condition still reads the true, unsuppressed disch_win, so this rule changes
# nothing about how solar surplus is charged or exported -- a suppressed interval
# with imp[i] > 0 and no solar surplus simply takes NO dispatch action at all (the
# household pays ordinary retail import price that interval, same as if it had no
# battery for it), holding SOC in reserve for the day's known test rather than
# spending it on arbitrage first. disch_win_active == disch_win whenever
# prestage=False (the default) or the current interval's date has no event hour in
# event_set, which is what keeps the tested empty-event_set byte-identity guarantee
# to run_batt intact for every EXISTING call site (none of which pass prestage=True).
def run_batt_vpp(d, imp0, gen0, cap, event_set, reserve_frac, charge_kw=None, prestage=False):
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

    The reserve floor is honored for the WHOLE event hour, not just the incremental
    event-forced portion: the ordinary/BAU-equivalent greedy discharge branch (disch_win)
    is ALSO capped at reserve_frac * cap during an event hour (soc_cap = max(soc -
    reserve_kwh, 0) instead of the unconstrained soc), so ordinary dispatch cannot push
    soc below the floor before the event-forcing block below even runs. Outside an event
    hour this cap is a no-op (soc_cap == soc), which is what keeps the empty-event_set
    byte-identity guarantee to run_batt intact.

    NO CHARGING FROM SOLAR SURPLUS DURING A DECLARED EVENT HOUR (issue #10 second
    adversarial review, Finding 2). The export-charging branch is skipped whenever
    is_event[i] is true, regardless of disch_win/imp[i]: a rational VPP-optimizing
    battery would not spend round-trip efficiency charging solar surplus only to
    immediately discharge that same energy again a few lines below for the SAME
    event's credit. Without this guard, a solar-surplus interval with no concurrent
    house import (exp[i] > 0, imp[i] == 0) would take the export-charging branch
    (charging into the battery) and then the event-forcing block below would
    discharge that just-added energy back out in the SAME interval, counting the
    full amount as new "event_discharge" -- round-tripping energy that would have
    been exported directly anyway (at an ETA*ETA loss) while inflating demonstrated
    capacity/revenue as if it were genuinely new battery output. Confirmed against
    the real 2025 backtest before this fix: 12 intervals showed both a charge-from-
    solar and an event-forced discharge in the same interval, totaling 5.32 kWh
    charged and 25.52 kWh event-discharged in those intervals alone. With the guard,
    solar surplus during an event hour passes straight through to export (exp[i]
    stays at gen0[i] until the event-forcing block below adds any additional
    event-forced discharge from whatever SOC already exists), and the event-forcing
    block still maximizes discharge from existing SOC exactly as before.

    Returns (imp, exp, soc_start, event_discharge_kwh, bau_discharge_kwh):
      soc_start          -- SOC at the START of each interval (before this step's action)
      event_discharge_kwh -- the INCREMENTAL discharge forced by event participation
                             beyond what the ordinary greedy step already did
      bau_discharge_kwh  -- the ordinary greedy discharge each interval (the `dd` run_batt
                             would have produced with no VPP participation at all)

    charge_kw (issue #40) is the CHARGE-direction power cap (both the solar-
    surplus and grid-top-up branches below use it), separate from PWRQ's
    discharge-direction cap; defaults to None, which reuses PWRQ for both
    directions -- byte-for-byte the prior symmetric behavior, preserving the
    empty-event_set byte-identity guarantee to run_batt.

    prestage (issue #53, default False) turns on the EVENT-AWARE SOC PRE-STAGING
    variant -- see the module-level "DECISION RECORD" comment immediately above
    this function for the full reasoning and the exact rule. In one line: on any
    calendar date with at least one hour in event_set, the ordinary disch_win
    arbitrage-discharge branch (NOT the event-forcing block) is suppressed for
    hours on that date strictly before the date's first event hour, so a
    revenue-motivated household holds SOC in reserve ahead of a KNOWN,
    calendar-published test rather than spending it on ordinary peak arbitrage
    first. False (the default) reproduces the reactive dispatch above exactly,
    for every existing call site.
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
    pwrq_chg = PWRQ if charge_kw is None else charge_kw / 4
    soc_start = np.empty(n)
    event_discharge = np.zeros(n)
    bau_discharge = np.zeros(n)
    # Pre-staging (issue #53): the earliest floor_hour with an event on each
    # calendar date present in event_set. Built once, outside the loop, from
    # event_set itself -- no new data source, just this same event_set's own
    # (date, floor_hour) tuples regrouped by date. Empty when prestage is False
    # (never consulted below) or when event_set is empty, either of which keeps
    # this a no-op and preserves the empty-event_set byte-identity guarantee.
    first_event_hour = {}
    if prestage:
        for ev_date, ev_hour in event_set:
            prev = first_event_hour.get(ev_date)
            if prev is None or ev_hour < prev:
                first_event_hour[ev_date] = ev_hour
    for i in range(n):
        soc_start[i] = soc
        disch_win = (16 <= h[i] < 21) or (p[i] != "sop" and kw[i] < 2.5)
        disch_used = 0.0
        # Pre-staging (issue #53): suppress ONLY the ordinary disch_win branch's
        # own ACTIVATION on an event date, strictly before that date's first event
        # hour -- the solar-surplus charge branch's condition just below still
        # reads the true, unsuppressed disch_win (unchanged), and the
        # event-forcing block further down is untouched. disch_win_active ==
        # disch_win (a no-op) whenever prestage is False or i's date has no entry
        # in first_event_hour, which is what keeps the empty-event_set/
        # prestage=False byte-identity guarantee to run_batt intact.
        disch_win_active = disch_win and not (
            prestage and dates[i] in first_event_hour
            and floor_h[i] < first_event_hour[dates[i]])
        # Never charge from solar surplus during a declared event hour (Finding 2,
        # issue #10 second adversarial review) -- let the surplus pass straight
        # through to export instead of round-tripping it for the event-forcing
        # block below to discharge again in this same interval. Outside an event
        # hour (is_event[i] is False) this clause is a no-op, preserving the
        # empty-event_set byte-identity guarantee to run_batt.
        if exp[i] > 0 and not (disch_win and imp[i] > 0) and not is_event[i]:
            c = min(exp[i], (cap - soc) / ETA, pwrq_chg)
            if c > 0:
                soc += c * ETA; exp[i] -= c
        elif p[i] == "sop":
            take = min(max((cap - soc) / ETA, 0), pwrq_chg)
            if take > 0:
                soc += take * ETA; imp[i] += take
        elif disch_win_active:
            # During an event hour, the ordinary/BAU-equivalent discharge must ALSO
            # respect the reserve floor -- otherwise this branch can draw soc below
            # reserve_kwh before the event-forcing block below ever runs, and the
            # reserve is only honored in isolation, not in combination with ordinary
            # dispatch. Outside an event hour (or with reserve_kwh == 0), soc_cap ==
            # soc, so this is a no-op and matches run_batt's own greedy branch exactly
            # (needed for the empty-event_set byte-identity guarantee).
            soc_cap = max(soc - reserve_kwh, 0.0) if is_event[i] else soc
            dd = min(imp[i], soc_cap * ETA, PWRQ)
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


def backtest(d, cal, reserve_frac=BACKUP_RESERVE_FRAC, charge_kw=None):
    """The full computation: returns a dict with every figure the artifact needs.

    charge_kw (issue #40) is threaded through to every run_batt/run_batt_vpp
    call below (BAU, both reserve levels, both priced/unpriced variants);
    default None preserves the prior symmetric-power behavior exactly."""
    window_start = d.dt.min().date()
    window_end = d.dt.max().date()
    event_set, inwin, outwin = _event_set_and_hours(cal, window_start, window_end)

    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)

    # ---- which months are PARTIALLY observed (issue #10 Codex review Finding 2) ----
    # DSGS's own "Monthly DC" is an LMP-weighted average over ALL of that aggregation's
    # event hours in the month (see the CEC's own definition, module docstring). If this
    # household's measured window starts mid-month, some of that month's real event hours
    # fall BEFORE the window and cannot be replayed (no measured load exists for them) --
    # pricing the remaining, INCOMPLETE subset at the month's full published $/kW rate
    # would misrepresent a partial-month settlement as a complete one. Computed here,
    # before dispatch, so both the revenue AND the opportunity-cost calculations below can
    # consistently exclude these months (a second Codex review pass found the opportunity
    # cost was still being computed from the FULL event set including partial months, so
    # net_revenue mixed a bill effect from a month whose gross revenue was zeroed out).
    partial_months = sorted(set(r.date.month for r in outwin.itertuples())
                             & set(r.date.month for r in inwin.itertuples()))
    event_set_priced = {(dt_, h) for (dt_, h) in event_set if dt_.month not in partial_months}

    # BAU (no VPP participation) -- must match battery_dispatch_policies.py's own
    # "greedy" pw3 figures exactly (cross-checked in the test suite).
    imp_bau, exp_bau, _served_bau, _thru_bau = bp.run_batt(
        d, imp0, gen0, CAP, "greedy", charge_kw=charge_kw)
    bill_bau = bp.billed(d, imp_bau, exp_bau)

    # With VPP event dispatch -- the FULL in-window event set, including partial months.
    # AC2 requires every in-window event to be replayed through the dispatch engine, so
    # partial months' hours still appear in hour_detail/miss_rate below (that simulation
    # is valid regardless of whether the month can be priced).
    imp_vpp, exp_vpp, soc_start, event_kwh, bau_kwh = run_batt_vpp(
        d, imp0, gen0, CAP, event_set, reserve_frac, charge_kw=charge_kw)

    # A SEPARATE dispatch run, event-forcing ONLY the priced months, used exclusively for
    # the opportunity-cost feeding into net_revenue -- so a partial month's bill impact
    # (from event-forcing hours whose revenue was zeroed out) never gets netted against
    # revenue from a different set of months.
    imp_vpp_priced, exp_vpp_priced, _, _, _ = run_batt_vpp(
        d, imp0, gen0, CAP, event_set_priced, reserve_frac, charge_kw=charge_kw)
    bill_vpp_priced = bp.billed(d, imp_vpp_priced, exp_vpp_priced)
    opportunity_cost = round(bill_vpp_priced - bill_bau, 2)

    # ---- per-event-hour totals (for revenue + miss-rate) ----
    dates = d.dt.dt.date.values
    floor_h = np.floor(d.hour.values).astype(int)
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
        if mo in partial_months:
            continue  # cannot validly price a partial month -- see partial_months_note
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
        d, imp0, gen0, CAP, event_set, 0.0, charge_kw=charge_kw)
    # Same priced-months-only split as the 20%-reserve case above, for the same reason:
    # the opportunity cost feeding net_revenue0 must not include a partial month's bill
    # effect when that month's gross revenue is zeroed out.
    imp_vpp0_priced, exp_vpp0_priced, _, _, _ = run_batt_vpp(
        d, imp0, gen0, CAP, event_set_priced, 0.0, charge_kw=charge_kw)
    bill_vpp0_priced = bp.billed(d, imp_vpp0_priced, exp_vpp0_priced)
    opp_cost0 = round(bill_vpp0_priced - bill_bau, 2)
    monthly_gross0 = {}
    for mo, rows in sorted(by_month.items()):
        if mo in partial_months:
            continue  # cannot validly price a partial month -- see partial_months_note
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
    total_kwh_0pct = 0.0
    for r in hour_rows:
        key = (dt.date.fromisoformat(r["date"]), r["hour_end"] - 1)
        idxs = idx_by_hour.get(key, [])
        total0 = float(sum(event_kwh0[j] + bau_kwh0[j] for j in idxs))
        total_kwh_0pct += total0
        if total0 < 1.0:
            misses0 += 1
    miss_rate0 = round(misses0 / n_hours_in_window, 4) if n_hours_in_window else None
    total_kwh_20pct = sum(r["total_discharge_kwh"] for r in hour_rows)

    # ---- ADDITIVE sensitivity (issue #53): event-aware SOC pre-staging, same
    # reserve_frac as the primary reactive case above -- computed ALONGSIDE the
    # reactive backtest, not replacing it. Every reactive-path variable above
    # (imp_vpp/exp_vpp/hour_rows/gross_revenue/net_revenue/miss_rate/etc.) is
    # untouched by this block, which is what keeps the existing committed
    # data/dsgs_vpp_backtest.json figures byte-identical after this change. See
    # run_batt_vpp()'s module-level "DECISION RECORD" comment for the rule and
    # the reasoning behind modeling this at all. ----
    imp_pre, exp_pre, soc_start_pre, event_kwh_pre, bau_kwh_pre = run_batt_vpp(
        d, imp0, gen0, CAP, event_set, reserve_frac, charge_kw=charge_kw, prestage=True)
    # Same priced-months-only split as the reactive case, for the same reason: the
    # opportunity cost feeding net_revenue_pre must not include a partial month's
    # bill effect when that month's gross revenue is zeroed out.
    imp_pre_priced, exp_pre_priced, _, _, _ = run_batt_vpp(
        d, imp0, gen0, CAP, event_set_priced, reserve_frac, charge_kw=charge_kw, prestage=True)
    bill_pre_priced = bp.billed(d, imp_pre_priced, exp_pre_priced)
    opp_cost_pre = round(bill_pre_priced - bill_bau, 2)
    monthly_gross_pre = {}
    for mo, rows in sorted(by_month.items()):
        if mo in partial_months:
            continue  # cannot validly price a partial month -- see partial_months_note
        total_pre = {}
        for r in rows:
            key = (dt.date.fromisoformat(r["date"]), r["hour_end"] - 1)
            idxs = idx_by_hour.get(key, [])
            total_pre[r["date"], r["hour_end"]] = float(sum(event_kwh_pre[j] + bau_kwh_pre[j] for j in idxs))
        num = sum((total_pre[r["date"], r["hour_end"]] - baseline_kw) * r["caiso_lmp_usd_per_mwh"] for r in rows)
        den = sum(r["caiso_lmp_usd_per_mwh"] for r in rows)
        dc_kw = num / den if den else 0.0
        rate = MONTHLY_RATE_USD_PER_KW[mo]
        monthly_gross_pre[mo] = round(max(dc_kw, 0.0) * rate, 2)
    gross_revenue_pre = round(sum(monthly_gross_pre.values()), 2)
    net_revenue_pre = round(gross_revenue_pre - opp_cost_pre, 2)
    misses_pre = 0
    total_kwh_pre = 0.0
    for r in hour_rows:
        key = (dt.date.fromisoformat(r["date"]), r["hour_end"] - 1)
        idxs = idx_by_hour.get(key, [])
        total_p = float(sum(event_kwh_pre[j] + bau_kwh_pre[j] for j in idxs))
        total_kwh_pre += total_p
        if total_p < 1.0:
            misses_pre += 1
    miss_rate_pre = round(misses_pre / n_hours_in_window, 4) if n_hours_in_window else None

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
            "CORRECTED (issue #10 third adversarial review): the restriction is on "
            "AGGREGATORS, not individual households. The CEC's own DSGS Program "
            "Guidelines, Fifth Edition (TN 269649, Section II.C.1 and Appendix A, "
            "the authoritative source, not just Olivine's FAQ paraphrase) state: "
            "'participation in the 2026 program season is limited to storage VPP "
            "aggregators that participated in October 2025, except for VPP "
            "aggregators of bi-directional EVSEs.' This freezes WHICH AGGREGATORS "
            "(e.g. Tesla's VPP program) may participate at all in 2026 -- it does "
            "NOT say a new household's battery cannot join an aggregator that "
            "itself already qualifies. An earlier version of this artifact wrongly "
            "concluded the household 'could not join at all' by conflating the "
            "aggregator-level restriction with a household-level one -- that claim "
            "is retracted as unsupported by the source. What the source DOES "
            "additionally establish: each qualifying aggregator's total 2026 "
            "compensation is CAPPED at its own October 2025 pro-rata share of "
            "available program funds (Appendix A) -- so adding new participant "
            "sites in 2026 does not increase what the CEC pays that aggregator, "
            "creating a real economic disincentive to enroll new customers even if "
            "not a rule against it. Whether a specific aggregator (e.g. Tesla, "
            "which the CEC lists as an approved Option 3 provider generally) both "
            "(a) actually participated in October 2025 specifically and (b) would "
            "accept a new residential site in 2026 given the funding cap is NOT "
            "DETERMINED from public CEC data -- this anonymized dataset cannot "
            "identify which real aggregator this household's utility corresponds "
            "to (see udc_identity_caveat), so confirm directly with the battery "
            "manufacturer's VPP program before assuming either way."),
        "grandfathering_interaction_finding": (
            "SEARCHED, NOTHING FOUND: whether DSGS ENROLLMENT (distinct from mere "
            "battery ownership) affects NEM 2.0 grandfathering. The CEC's DSGS "
            "Program Guidelines, Fifth Edition (89 pages, TN 269649) contain zero "
            "occurrences of 'net energy metering', 'net billing', 'NBT', or "
            "'grandfath'; its 4 'NEM' hits are all the unrelated term 'VNEM' "
            "(Virtual Net Energy Metering, a multi-tenant billing arrangement). "
            "Olivine's DSGS Option 3 FAQ has no NEM mention either. Consistent with "
            "nem3_grandfathering.py's (issue #9) finding that NEM 2.0 forfeiture "
            "(SDG&E tariff Schedule NEM Special Condition 7(b), D.16-04-020) is "
            "triggered by ADDING generating/storage equipment past a nameplate "
            "threshold, not by enrolling existing equipment in a demand-response "
            "program. No source found that DSGS enrollment itself has any "
            "grandfathering effect -- a searched, sourced absence, not an "
            "assumption."),
        "backup_reserve_caveat": (
            "BACKUP_RESERVE_FRAC=0.20 is an ASSUMED, uncited operating parameter -- "
            "no reserve figure is established elsewhere in this repository (checked "
            "against battery_backup_sims.py and extended_findings.py; the existing "
            "outage-endurance sims assume a FULLY charged battery at outage start, "
            "the opposite convention). It is an EVENT-HOUR-ONLY dispatch floor: "
            "enforced only during a declared DSGS event hour (both the ordinary and "
            "the event-forced discharge are capped against it then), NOT a "
            "continuously-held standing backup-reserve setting -- ordinary, non-VPP "
            "arbitrage in the hours before an event is unaffected, so a real "
            "Powerwall's own continuously-enforced reserve setting would leave LESS "
            "reserve actually available by the time a declared event starts than "
            "this figure implies. A 0% (no reserve) sensitivity is included for "
            "comparison."),
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
        "partial_months_note": (
            f"Month(s) {partial_months} have event hours on BOTH sides of the "
            "measured-window boundary (issue #10 Codex review Finding 2): DSGS's own "
            "Monthly DC is an LMP-weighted average over ALL of a month's event hours, "
            "and this household's pre-window hours in that same month cannot be "
            "replayed (no measured load exists for them). Pricing only the in-window "
            "subset at the month's full published $/kW rate would misrepresent a "
            "partial month as a complete settlement, so these month(s) are EXCLUDED "
            "from monthly_gross_usd/net revenue entirely -- their dispatch/miss-rate "
            "outcomes for the in-window hours that DO exist are still reported in "
            "hour_detail (AC2 requires every in-window event to be replayed, and that "
            "simulation is valid; only the monthly CAPACITY PAYMENT computation is not), "
            "but they contribute $0 to gross/net revenue. The opportunity-cost figure "
            "feeding net revenue is computed from a SEPARATE dispatch run that excludes "
            "these month(s)' event-forcing entirely (a third adversarial/Codex review "
            "pass found the opportunity cost was still drawing on the full event set, "
            "netting a partial month's bill effect against zero revenue for it) -- so "
            "hour_detail's per-hour figures for an excluded month reflect the FULL "
            "event-forced dispatch (for AC2), while gross/net revenue reflect the "
            "PRICED-months-only dispatch (for consistency)."
            if partial_months else
            "No month has event hours on both sides of the measured-window boundary "
            "this run -- every priced month's Monthly DC is computed from its "
            "complete in-window event set."),
        "partial_season_caveat": (
            f"This backtest's revenue/kWh/miss-rate figures cover ONLY "
            f"{window_start.isoformat()} through "
            f"{(inwin['date'].max().isoformat() if len(inwin) else window_start.isoformat())} "
            "-- the portion of the household's measured window that overlaps the 2025 "
            "DSGS season (which runs May-October), NOT the whole season. "
            f"{len(outwin[outwin['date'] < window_start])} union event hours before "
            f"{window_start.isoformat()} are excluded (unreplayable -- no measured load "
            "exists for them in any year), so this is a PARTIAL-SEASON observation, not "
            "a complete season and not an annualized figure. A full season would very "
            "likely earn MORE (it only adds event hours relative to this partial "
            "figure), but a full-season/annual revenue figure is NOT DETERMINED here -- "
            "it is not extrapolated from the partial figure, because no measured load "
            "exists for the missing May-July hours in any year. Any downstream use of "
            "this figure as an annual input (e.g. extended_findings.py's payback "
            "tornado lever) must carry this same caveat, not present it as a complete "
            "season's revenue."),
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
                "The 20% reserve case has a HIGHER miss count than the 0% reserve "
                "sensitivity, and a lower total delivered kWh -- the expected direction: "
                "holding back an event-hour-only floor (see backup_reserve_caveat -- "
                "enforced only during a declared event hour, not continuously) leaves "
                "less headroom for BOTH the ordinary/BAU-equivalent discharge and the "
                "event-forced increment during that hour (the floor is enforced jointly "
                "across both, not just the incremental event-forced portion -- see "
                "run_batt_vpp()), so more event hours end with too little SOC above the "
                "floor to clear the 1 kWh miss threshold."),
        },
        "revenue": {
            "reserve_20pct": {
                "gross_usd": gross_revenue,
                "opportunity_cost_usd": opportunity_cost,
                "net_usd": net_revenue,
                "total_discharge_kwh": round(total_kwh_20pct, 2),
                "monthly_gross_usd": {str(k): v for k, v in monthly_gross.items()},
                "monthly_demonstrated_capacity_kw": {str(k): v for k, v in demonstrated_capacity_by_month.items()},
            },
            "reserve_0pct_sensitivity": {
                "gross_usd": gross_revenue0,
                "opportunity_cost_usd": opp_cost0,
                "net_usd": net_revenue0,
                "total_discharge_kwh": round(total_kwh_0pct, 2),
                "monthly_gross_usd": {str(k): v for k, v in monthly_gross0.items()},
            },
        },
        "prestaged_sensitivity": {
            "modeled": True,
            "description": (
                "ADDITIVE sensitivity (issue #53), NOT a replacement for the "
                "reserve_20pct reactive figures above, which are unchanged by this "
                "field's presence. Same event-hour-only 20% reserve floor and the "
                "same event calendar/measured load as reserve_20pct, but with "
                "EVENT-AWARE SOC PRE-STAGING turned on: run_batt_vpp(prestage=True) "
                "suppresses the ordinary/arbitrage disch_win discharge branch on any "
                "calendar date with a scheduled event, strictly before that date's "
                "first event hour (see run_batt_vpp()'s module-level DECISION RECORD "
                "comment for the full rule and reasoning). This models a "
                "revenue-motivated household that holds SOC in reserve for a KNOWN, "
                "same-day scheduled test -- not a household that plans multiple "
                "days ahead, which this rule does not implement. Same-day "
                "household-level awareness is a MODELED assumption, not a "
                "confirmed real notice chain all the way to the customer -- the "
                "CEC's own DSGS Option 3 program FAQ (this household's category) "
                "confirms VPP aggregators know their own test schedule at least a "
                "day ahead (aggregators must notify the CEC by 3pm the day before "
                "a planned test), but does not document whether/how far ahead an "
                "individual enrolled customer is told (see run_batt_vpp()'s "
                "module-level DECISION RECORD comment for the citation and the "
                "confirmed-vs-modeled distinction in full). CARRIES THE SAME "
                "UNION-CALENDAR CAVEAT AS THE REACTIVE HEADLINE ABOVE, PRECISELY "
                "STATED (Codex adversarial review, issue #53, second pass -- an "
                "EARLIER version of this note incorrectly called this field's own "
                "net_usd/delta an 'upper bound' on foresight benefit; that claim was "
                "NOT proven and is retracted): the union calendar (module "
                "docstring's 'inclusive upper bound on event FREQUENCY') bounds only "
                "the CANDIDATE EVENT COUNT, never the resulting dollar economics --"
                " net_usd here reflects a REAL trade-off (event-hour revenue gained "
                "vs. off-peak arbitrage forgone) that is not proven monotonic in "
                "event count, and the reactive figures elsewhere in this artifact "
                "already demonstrate the same union total can sit INSIDE the "
                "per-aggregation range rather than above every member of it. "
                "Foreknowledge of every aggregation's combined schedule is not "
                "something a real household would have, so this field's net_usd/"
                "delta is not that household's own outcome -- but it is also NOT "
                "asserted to be a ceiling on what one could earn. See "
                "per_aggregation_sensitivity()'s own prestaged range (when the "
                "private archive is available) for the actual realizable spread."),
            "reserve_frac": reserve_frac,
            "gross_usd": gross_revenue_pre,
            "opportunity_cost_usd": opp_cost_pre,
            "net_usd": net_revenue_pre,
            "total_discharge_kwh": round(total_kwh_pre, 2),
            "monthly_gross_usd": {str(k): v for k, v in monthly_gross_pre.items()},
            "miss_rate": {"misses": misses_pre, "total": n_hours_in_window, "rate": miss_rate_pre},
            "delta_vs_reactive": {
                "net_usd": round(net_revenue_pre - net_revenue, 2),
                "gross_usd": round(gross_revenue_pre - gross_revenue, 2),
                "total_discharge_kwh": round(total_kwh_pre - total_kwh_20pct, 2),
                "miss_rate": (round(miss_rate_pre - miss_rate, 4)
                              if miss_rate is not None and miss_rate_pre is not None
                              else None),
                "net_usd_pct": (round((net_revenue_pre - net_revenue) / net_revenue * 100, 1)
                                if net_revenue else None),
                "note": (
                    f"Computed, not assumed either way. Foresight is worth a REAL "
                    f"amount here, not a rounding error, but it shows up mostly as "
                    f"MORE revenue per served hour, not as fewer misses: net "
                    f"revenue rises "
                    f"${round(net_revenue_pre - net_revenue, 2):+,.2f} "
                    f"({round((net_revenue_pre - net_revenue) / net_revenue * 100, 1) if net_revenue else float('nan'):+.1f}%, "
                    f"${net_revenue:,.2f} to ${net_revenue_pre:,.2f}) and total "
                    f"delivered discharge rises "
                    f"{round(total_kwh_pre - total_kwh_20pct, 2):+,.2f} kWh "
                    f"({round((total_kwh_pre - total_kwh_20pct) / total_kwh_20pct * 100, 1) if total_kwh_20pct else float('nan'):+.1f}%), "
                    f"while the miss rate moves only "
                    f"{(round((miss_rate_pre - miss_rate) * 100, 2) if miss_rate is not None and miss_rate_pre is not None else float('nan')):+.2f} "
                    f"percentage points ({misses_pre} of {n_hours_in_window} misses "
                    f"pre-staged vs {n_misses} reactive) -- pre-staging mostly helps "
                    "event hours that were already delivering SOME capacity deliver "
                    "MORE of it (holding back arbitrage discharge earlier in the "
                    "day leaves more SOC above the reserve floor when the event "
                    "hour arrives), rather than converting a fully SOC-exhausted "
                    "miss into a hit."),
            },
        },
        "total_discharge_kwh_note": (
            "AC4's 'kWh exported' figure: total battery discharge across the "
            "in-window event hours (event-forced discharge -- which this dispatch "
            "model routes entirely to export, see run_batt_vpp -- plus any "
            "concurrent ordinary/BAU discharge that hour). This matches the CEC's "
            "own Net Discharge crediting basis (both avoided import and true "
            "export reduce net metered demand identically for DSGS purposes), not "
            "a narrower grid-export-only figure."),
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
                "The committed calendar -- and every gross/net/kWh/miss-rate figure "
                "in this artifact computed from it -- is the UNION of distinct "
                "(date, hour) event slots across ~14 aggregations in this category, "
                "since a single real household sees only ONE aggregation's own "
                "schedule and anonymization prevents identifying which. Treat these "
                "figures as an INCLUSIVE UPPER-BOUND SCENARIO, not a headline point "
                "estimate: see per_aggregation_sensitivity for the range across the "
                "individual aggregation schedules a real household might actually "
                "have belonged to."),
        },
    }
    return result


def per_aggregation_sensitivity_or_preserved(d):
    """The per_aggregation_sensitivity field's value for the artifact: freshly
    computed if the private raw CEC archive is present, otherwise preserved from
    whatever is already committed at RESULTS_JSON (issue #10 third Codex review
    round). This script is CI_RUNNABLE (the normal run needs no archive), so a
    CI/archive-less run MUST NOT overwrite the committed evidence this field
    carries with a placeholder -- an earlier version of this code did exactly
    that on every archive-less run, silently destroying the real 14-aggregation
    breakdown backing the report's own published $97-$213 range, every time the
    archive-less path executed (which is the NORMAL case for CI and any fresh
    clone). Only falls back to a placeholder string if there is truly nothing
    committed yet (e.g. a from-scratch bootstrap with no archive ever available,
    which should not happen in practice but must not crash)."""
    if RAW_XLSX.exists():
        return per_aggregation_sensitivity(d, charge_kw=CHARGE_KW)
    if RESULTS_JSON.exists():
        existing = json.loads(RESULTS_JSON.read_text())
        preserved = existing.get(
            "per_aggregation_sensitivity",
            "NOT COMPUTED: needs the private raw CEC archive, which this checkout "
            "does not have, and the previously committed artifact never computed "
            "this field either.")
        # issue #53, Codex adversarial review: per_aggregation_sensitivity() now
        # also computes a prestaged_* range, but a PRESERVED dict (this archive-
        # less path) can only carry whatever was last computed WITH the archive --
        # if that was before this field existed, silently shipping the union
        # calendar's prestaged_sensitivity headline with no corrective per-
        # aggregation range is the exact "do not ship" problem the review found.
        # Flag it explicitly rather than presenting an incomplete preserved dict
        # as if it were current.
        if isinstance(preserved, dict) and "prestaged_net_usd_min" not in preserved:
            preserved = dict(preserved)
            preserved["prestaged_range_pending_archive_regeneration"] = (
                "The preserved per-aggregation range above predates issue #53's "
                "prestaged_sensitivity field. It does NOT yet include a "
                "prestaged_net_usd_min/max range, so the union calendar's "
                "prestaged_sensitivity headline elsewhere in this artifact has "
                "no realizable single-aggregation counterpart computed yet -- "
                "that headline reflects an inclusive UNION calendar (more "
                "candidate event hours than any single real household saw), "
                "which bounds event FREQUENCY only, not the resulting dollar "
                "economics (a real household's own smaller, more selectively-"
                "timed calendar is not proven to earn less). Do not treat "
                "net_usd/delta_vs_reactive as a ceiling on realizable benefit "
                "until a future run with the private raw CEC archive present "
                "recomputes this field.")
        return preserved
    return (
        "NOT COMPUTED: needs the private raw CEC archive "
        f"({RAW_XLSX}), which this checkout does not have, and there is no "
        "previously committed artifact to preserve this field from. See "
        "per_aggregation_sensitivity() and the event_hour_union_caveat note "
        "above -- the union-calendar figures elsewhere in this artifact are an "
        "inclusive upper bound, not this household's actual single-aggregation "
        "revenue.")


def main():
    cal = load_calendar()
    d = br.load()
    result = backtest(d, cal, charge_kw=CHARGE_KW)
    result["per_aggregation_sensitivity"] = per_aggregation_sensitivity_or_preserved(d)

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
