#!/usr/bin/env python3
"""Heat-pump conversion: replace the gas furnace + AC, priced per interval (issue #1).

Two things this script does NOT do, and why, stated up front so the design is
checkable against CLAUDE.md's own rules:

  - It does NOT distribute the heat pump's electric load using an INVENTED
    hourly shape. This household's gas meter reports DAILY totals only
    (`private/1-raw-data/gas.csv`, Interval UOM: Day) and there is no hourly
    outdoor-temperature series anywhere in this repo (only
    `data/weather_daily_tmean.csv`, also daily) -- so there is no measured
    basis for a specific hour-of-day heating curve. Rather than assume one
    (a "typical morning/evening heating shape" is a real HVAC convention --
    ASHRAE Handbook--Fundamentals Ch.17 documents an overnight-setback
    "pickup load" recovery peak -- but this house's own OWN diurnal shape
    was never measured), this script illustrates the sensitivity with an
    on-peak/off-peak BRACKET (each side spreads the day's kWh evenly across
    every interval of its own TOU window -- a plausible concentrated shape,
    not a computed cost extremum; a real schedule concentrated into one
    specific interval within that window could in principle land outside
    this bracket, Codex review, issue #1, pass 3) plus a UNIFORM-within-day
    illustrative midpoint
    (each heating day's own kWh spread evenly across that day's own real
    intervals) for a single reference number to anchor the payback tables
    against. Uniform spreading is itself a real, specific operational
    assumption -- not an absence of one, and not more likely to be true
    than either bracket end (Codex adversarial review, issue #1, pass 2) --
    so it is never called a "central estimate" below, and the bracket, not
    the midpoint, is the honest disclosure of what this script actually
    knows.
  - It does NOT credit any purchase incentive. As of this run (2026-08),
    every federal/state/utility incentive that could apply to this
    conversion is confirmed closed: the federal 25C credit terminated for
    property placed in service after 2025-12-31 under the One Big Beautiful
    Bill Act (P.L. 119-21) -- irs.gov/credits-deductions/energy-efficient-
    home-improvement-credit, and its OBBB FAQ; TECH Clean California's
    single-family heat-pump-HVAC incentive reservations closed statewide
    2025-11-14 -- techcleanca.com/incentives/single-family-incentives/;
    HEEHRA (the federal IRA rebate TECH administers in CA) reports
    single-family rebates "fully reserved for projects statewide" as of
    2026-02-24 -- techcleanca.com/incentives/heehrarebates/; SGIP (CPUC)
    covers batteries and heat-pump WATER heaters, not space-heating HVAC,
    and its ratepayer budgets closed 2025-12-31 regardless; SDG&E's own
    electrification page names no dedicated HVAC heat-pump rebate, only a
    referral to a third-party readiness program. INCENTIVE_USD is 0 for
    exactly this reason -- not an oversight, a verified absence, re-checked
    at whatever date this script is next run (the constant is dated below).

Furnace therms are isolated TWO independent ways and cross-checked (issue's
own AC): a summer-month floor average (rates.SUMMER_MONTHS, the same months
this repo already treats as non-heating for every other purpose) and an HDD
regression against data/weather_daily_tmean.csv (the same method, same
weather file, as extended_findings.py's existing gas_decomposition -- this
script's own regression is expected to closely reproduce it, and asserts so;
a material drift between them would mean the weather file or the gas export
changed and needs investigating, not silently accepting two different
"true" floors).

Electric cost is computed by ADDING the heat pump's modeled kWh into real
15-minute Consumption intervals and re-billing the WHOLE measured year with
rates.bill_nem() -- the same canonical NEM engine every other script in this
repo bills through, never a year-end lump-sum rate multiply (CLAUDE.md
section 1b). This is what makes the NEM interaction (added winter import
first offsetting whatever export credit that period already had, only then
spending on volumetric energy charges) come out of the SAME arithmetic the
household's real bills are validated against, not a separate assumption.

Gas savings are priced at each REAL billing period's own TRUE MARGINAL-TIER
rate (issue #98): the heating slice (modeled as the marginal, top slice of
that period's usage) is priced at whichever Gas Service tier(s) it actually
occupies (data/bill_periods_gas.csv's baseline_rate/nonbaseline_rate, split
at that period's own printed baseline_allowance_therms) plus the separate,
flat, untiered gas_energy_charge_rate every therm pays -- see
gas_savings_by_period()'s and _heating_slice_cost()'s own docstrings for
the full mechanism. This replaced an earlier blended-average approximation
(total_gas_service / therms) once parse_bills.py started extracting the
baseline allowance and energy-charge rate needed for the marginal-tier
computation. A real gas bill PDF for this household's own GR-Residential
rate (sdge_gas_2025-07-30.pdf) was read directly to confirm there is NO
separate fixed/customer charge on this rate schedule -- every line item is
per-therm or a percentage of the per-therm charge -- so eliminating heating
gas usage removes that share of the bill in full, with no minimum floor
left behind.

Run AFTER behavior_rebuild.py in the same working directory (needs its
staged usage.csv); writes data/heat_pump_conversion.json.
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rates as R
import household as hh
import behavior_rebuild as BR


def repo_root():
    for base in (os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        p = base
        for _ in range(8):
            if os.path.exists(os.path.join(p, "data", "plan_results.csv")):
                return p
            p = os.path.dirname(p)
    raise SystemExit("repo root not found (data/plan_results.csv)")


ROOT = repo_root()
DATA = os.path.join(ROOT, "data")
GAS_CSV = os.path.join(ROOT, "private", "1-raw-data", "gas.csv")
WEATHER_CSV = os.path.join(DATA, "weather_daily_tmean.csv")
GAS_PERIODS_CSV = os.path.join(DATA, "bill_periods_gas.csv")

KWH_PER_THERM = 29.3   # same conversion extended_findings.py uses (EIA/DOE standard)

# Metered gas THERMS are furnace FUEL INPUT, not delivered heat -- a furnace
# below 100% AFUE (Annual Fuel Utilization Efficiency) burns more fuel than
# the heat it actually delivers, and it is the DELIVERED heat a heat pump
# has to replace, not the raw therms (Codex adversarial review, issue #1,
# pass 1: treating therms as 100% delivered heat overstates the heat pump's
# required kWh, and so overstates its electric cost, by 1/AFUE). This
# household's own furnace nameplate AFUE was never recorded (not in
# private/household.yaml, not in any committed intake field) -- 0.78 is
# used instead: the SAME existing-furnace assumption the cited CZ7
# cost-effectiveness study uses for its own baseline (Table 3, "Heating
# Efficiency 78 AFUE"), and also the historical federal minimum efficiency
# for residential gas furnaces sold in the US for decades before the 2028
# 95-AFUE standard takes effect (cited in that same study, section 2.4).
# A furnace built to a higher voluntary efficiency tier delivers MORE
# useful heat per therm burned, meaning the heat pump would have to
# replace MORE heat for the same metered gas usage, raising its required
# kWh and cost -- so 0.78, the low end of the plausible existing-furnace
# range, is the assumption more FAVORABLE to the heat pump, not a
# worst-case pick made to flatter the gas side.
FURNACE_AFUE = 0.78

# Three coefficients of performance spanning this issue's own requested range;
# coastal-mild climate (this household's own climate_zone: "Coastal",
# private/household.yaml, bill-confirmed) favors the high end, so all three
# are carried through rather than picking one.
COP_SCENARIOS = {"low_2.8": 2.8, "central_3.5": 3.5, "high_4.2": 4.2}

# The two discount rates already established in this repo for a payback/NPV
# figure (battery_dispatch_policies.escalation()'s 5%, deep_analyses.py's/
# test_deep_analyses.py's 4%) -- reused rather than a third, novel rate.
DISCOUNT_RATES = (0.04, 0.05)

NPV_HORIZON_YEARS = 15   # this study's own HPSH EUL (Table 8, cited in INSTALL_COST_NOTE)

# Install-cost scenarios, sourced from the one CA-specific, CZ7-relevant primary
# source found: "2025 Cost-Effectiveness Study: Single Family AC to Heat Pump
# Replacement" (Frontier Energy / Misti Bruceri & Associates for the CA
# Statewide Codes & Standards Program, funded by PG&E/SCE/SDG&E under CPUC
# auspices, rev 2025/06/09) -- localenergycodes.com/content/resources, PDF
# hosted at pinole.gov/wp-content/uploads/2025/07/2025-Single-Family-AC-to-HP-
# Cost-eff-Study.pdf. Table 4 (p.15) sizes this household's own climate zone
# (CZ7, SDG&E territory, EV-TOU-5 electrification tariff -- this household's
# OWN plan, Table 5 p.16) at 3.0 tons for BOTH the AC-only and heat-pump
# paths. Table 8 (p.19) gives the year-2026 first-cost comparison for the
# FULL HPSH replacement (no gas furnace kept, matching this issue's own
# "replace gas furnace + AC" scope, not the dual-fuel DFHP alternative that
# keeps a gas connection) for a 4-ton EXAMPLE system -- the study tabulates
# 4-ton as its worked example regardless of CZ7's own 3.0-ton sizing, so
# these dollar figures are cited as a 4-ton reference point, not rescaled to
# 3 tons (no cited $/ton scaling factor exists to rescale them correctly;
# guessing one would violate CLAUDE.md section 0). A smaller, 3-ton system
# is expected to cost somewhat less than the figures below, directionally,
# not quantified further.
INSTALL_COST_NOTE = (
    "2025 CA Statewide Codes & Standards Cost-Effectiveness Study (PG&E/SCE/"
    "SDG&E, CPUC), Tables 7-8 -- CZ7/SDG&E/EV-TOU-5, 4-ton example system "
    "(this household's own CZ7 sizing, Table 4, is 3.0 tons; a smaller "
    "system would likely cost somewhat less, not quantified)")
INSTALL_COST_STANDALONE_USD = 14529    # Table 8: "AC fails, install new HP & AHU", 2026
# The TRUE "AC-only" comparator (Codex adversarial review, issue #1, pass 2):
# Table 8's $13,808 "AC fails, install new AC & furnace" line is a
# SIMULTANEOUS AC-and-furnace replacement, not the AC-only counterfactual
# this issue's own AC asks for ("marginal cost over an AC replacement that
# would happen eventually anyway", i.e. the furnace keeps running as-is).
# Table 7 (the DFHP dual-fuel scenario, which keeps the existing furnace as
# backup) tabulates exactly that: "AC fails, install new AC, keep existing
# furnace" = $10,431 in 2026 -- the real cost of replacing ONLY the AC,
# leaving the furnace alone. That is the correct baseline to net the full
# HPSH replacement against for a genuine "eventually anyway" framing.
INSTALL_COST_AC_ONLY_REPLACEMENT_USD = 10431   # Table 7: "AC fails, install new AC, keep existing furnace", 2026
INSTALL_COST_MARGINAL_USD = INSTALL_COST_STANDALONE_USD - INSTALL_COST_AC_ONLY_REPLACEMENT_USD

# A wider bracket for the sensitivity table, from web-sourced contractor
# pricing guides (rough estimates, not California-specific, kept ONLY as a
# sensitivity bound around the cited CEC-study point estimate above, never
# as the primary figure): San Diego-specific $14,000-$24,000 before
# incentives (climateprosd.com); general CA $9,000-$16,000-$18,000
# (hvacprojectcost.com, eamechanical.com).
INSTALL_COST_SENSITIVITY_USD = (10000, 14529, 20000)

# Every incentive checked and confirmed closed/expired as of this date --
# see the module docstring for the source list. Re-verify before reusing
# this constant on a later run.
INCENTIVE_USD = 0
INCENTIVE_VERIFIED_DATE = "2026-08-06"

# The observed per-therm range across this household's own real 25 billed
# gas statements (data/bill_periods_gas.csv, baseline_rate column, low to
# high) -- used as the gas-price sensitivity bound, not an invented range.


def _flag(path):
    v = hh.get(path, required=False)
    if v is None:
        raise SystemExit(f"private/household.yaml is missing {path}")
    if not isinstance(v, bool):
        raise SystemExit(f"{path} in private/household.yaml must be a YAML boolean")
    return v


HAS_GAS = _flag("household.has_gas")


def load_gas_daily():
    """{date: therms} for every day in the gas Green Button export."""
    gas = pd.read_csv(GAS_CSV, skiprows=13)
    gas.columns = [c.strip().lower() for c in gas.columns]
    gas["date"] = pd.to_datetime(gas["date"]).dt.date
    gas["therms"] = pd.to_numeric(gas["consumption"], errors="coerce")
    if gas["therms"].isna().any():
        raise SystemExit("heat_pump_conversion.py: unparseable therms value in gas.csv")
    return gas.set_index("date")["therms"]


def load_weather_daily():
    """{date: mean temp F} for every day the weather file carries."""
    w = pd.read_csv(WEATHER_CSV, skiprows=1, names=["date", "tf"])
    w["date"] = pd.to_datetime(w["date"]).dt.date
    return w.set_index("date")["tf"]


def summer_baseline_floor(gas_daily):
    """Annual non-heating floor (therms/yr), method 1: the average daily
    rate over rates.SUMMER_MONTHS -- the same months this repo already
    treats as having no space heating for every other purpose -- times 365.
    No weather data needed; this is the simplest, most defensible baseline
    and does not depend on the HDD regression agreeing with it."""
    dates = pd.Series(gas_daily.index)
    in_summer = dates.map(lambda d: d.month in R.SUMMER_MONTHS)
    summer_days = gas_daily[in_summer.values]
    if len(summer_days) < 60:
        raise SystemExit(
            f"heat_pump_conversion.py: only {len(summer_days)} summer days in "
            "gas.csv -- too few to estimate a non-heating floor")
    daily_floor = float(summer_days.mean())
    return daily_floor, daily_floor * 365


def hdd_regression(gas_daily, weather_daily):
    """(floor_therms_day, slope_therms_per_hdd, per-day HDD Series), method 2:
    linear regression of daily therms against heating-degree-days (base
    65F), reproducing extended_findings.py's own gas_decomposition method
    exactly (same weather file, same regression) so the two can be
    cross-checked rather than silently diverging."""
    merged = pd.DataFrame({"therms": gas_daily}).join(
        pd.DataFrame({"tf": weather_daily}), how="inner")
    if len(merged) < 300:
        raise SystemExit(
            f"heat_pump_conversion.py: gas/weather merge too small "
            f"({len(merged)} days) -- schema drift?")
    hdd = np.clip(65 - merged["tf"].astype(float), 0, None)
    slope, floor = np.polyfit(hdd, merged["therms"], 1)
    return float(floor), float(slope), pd.Series(hdd.values, index=merged.index)


def isolate_heating_therms():
    """Both isolation methods, cross-checked, plus the per-day HDD weights
    method 2 needs for allocating heating kWh to specific calendar days."""
    gas_daily = load_gas_daily()
    weather_daily = load_weather_daily()

    floor_day_summer, ann_floor_summer = summer_baseline_floor(gas_daily)
    ann_total = float(gas_daily.sum()) * 365 / len(gas_daily)
    ann_heat_summer = ann_total - ann_floor_summer

    floor_day_hdd, slope_hdd, hdd_by_day = hdd_regression(gas_daily, weather_daily)
    ann_floor_hdd = floor_day_hdd * 365
    ann_heat_hdd = ann_total - ann_floor_hdd

    if not (ann_floor_summer > 0 and ann_heat_summer > 0
            and ann_floor_hdd > 0 and ann_heat_hdd > 0):
        raise SystemExit(
            "heat_pump_conversion.py: both isolation methods must find a "
            "positive floor AND a positive heating slope -- this house has "
            "both a water-heating floor and space heating; a method finding "
            "otherwise signals a real data problem, not a fact about this house")

    floor_disagreement_pct = abs(ann_floor_summer - ann_floor_hdd) / ann_floor_hdd * 100
    heating_disagreement_pct = abs(ann_heat_summer - ann_heat_hdd) / ann_heat_hdd * 100
    return {
        "annual_total_therms": round(ann_total),
        "summer_baseline": {
            "method": "average daily therms over rates.SUMMER_MONTHS x 365",
            "floor_therms_yr": round(ann_floor_summer),
            "heating_therms_yr": round(ann_heat_summer),
        },
        "hdd_regression": {
            "method": "linear regression, daily therms vs HDD base 65F "
                      "(same method and weather file as extended_findings.py's "
                      "gas_decomposition)",
            "floor_therms_yr": round(ann_floor_hdd),
            "heating_therms_yr": round(ann_heat_hdd),
            "slope_therms_per_hdd": round(slope_hdd, 4),
        },
        # Codex review, issue #1: report BOTH disagreements distinctly, never
        # let one stand in for the other in prose -- floor and heating are
        # different quantities with different (and here, different-looking)
        # agreement percentages.
        "cross_check": {
            "floor_disagreement_pct": round(floor_disagreement_pct, 1),
            "heating_disagreement_pct": round(heating_disagreement_pct, 1),
            "note": ("the two independent methods' non-heating floors "
                     f"({round(ann_floor_summer)} vs {round(ann_floor_hdd)} "
                     f"therms/yr, {round(floor_disagreement_pct, 1)}% apart) "
                     "and heating estimates "
                     f"({round(ann_heat_summer)} vs {round(ann_heat_hdd)} "
                     f"therms/yr, {round(heating_disagreement_pct, 1)}% apart) "
                     "are compared as cross-checks, not averaged; the HDD "
                     "regression drives every downstream figure below since "
                     "it alone can attribute a SPECIFIC day's therms to that "
                     "day's own heating demand, which the electric "
                     "re-billing needs"),
        },
        "annual_heating_therms": round(ann_heat_hdd),
        "floor_therms_per_day": floor_day_hdd,
        "hdd_by_day": hdd_by_day,
        "total_hdd": float(hdd_by_day.sum()),
    }


def _heating_slice_cost(total_therms, heating_therms, baseline_allowance,
                         baseline_rate, nonbaseline_rate, energy_rate, context):
    """True marginal-tier price of the TOP `heating_therms` of a period's
    `total_therms` (issue #98) -- i.e. what those specific therms cost given
    where they sit in the tariff's two-tier ladder, not the period's blended
    average $/therm.

    Heating is modeled as the MARGINAL (highest) slice of usage: the
    non-heating "floor" (water heating, cooking -- floor_per_day in
    gas_savings_by_period) runs every day regardless, so the heating-specific
    therms are the ones a heat pump would remove FROM THE TOP of what was
    actually billed, not an arbitrary subset spread across both tiers. SDG&E
    bills the baseline allowance first (cheapest) and only the therms beyond
    it at the nonbaseline rate, so the heating slice occupies the tariff's
    MORE expensive tier first, working down, until the slice is exhausted:
        - the therms strictly above `max(baseline_allowance, non_heat_therms)`
          are nonbaseline-tier (priced at nonbaseline_rate + energy_rate);
        - whatever of the slice remains below that line is baseline-tier
          (priced at baseline_rate + energy_rate).
    The flat Gas Energy Charge (energy_rate) applies to EVERY therm
    regardless of tier, so it is added on both slices rather than tiered.

    A period whose blended nonbaseline_rate came out blank (bill_periods_gas.
    csv: every segment's own Gas Service was single-tier, i.e. this period
    never crossed its baseline allowance) must therefore have its ENTIRE
    heating slice fall in the baseline tier -- if it doesn't, something
    upstream (baseline_allowance_therms or the heating attribution itself)
    is inconsistent with the bill, and this fails closed rather than
    treating a missing rate as zero.
    """
    non_heat_therms = max(0.0, total_therms - heating_therms)
    baseline_ceiling = min(baseline_allowance, total_therms)
    overlap_baseline = max(0.0, baseline_ceiling - non_heat_therms)
    overlap_baseline = min(overlap_baseline, heating_therms)
    overlap_nonbaseline = heating_therms - overlap_baseline
    if overlap_nonbaseline > 1e-6 and (nonbaseline_rate is None or pd.isna(nonbaseline_rate)):
        raise SystemExit(
            f"heat_pump_conversion.py: {context} needs "
            f"{overlap_nonbaseline:.2f} nonbaseline-tier heating therms "
            f"(total {total_therms:g} therms, baseline allowance "
            f"{baseline_allowance:g}) but bill_periods_gas.csv has no "
            f"nonbaseline_rate for this period -- baseline_allowance_therms, "
            f"the heating attribution, or the rate extraction disagree with "
            f"the bill.")
    nonbaseline_rate = 0.0 if pd.isna(nonbaseline_rate) else nonbaseline_rate
    tier_cost = overlap_baseline * baseline_rate + overlap_nonbaseline * nonbaseline_rate
    energy_cost = heating_therms * energy_rate
    return tier_cost + energy_cost


def gas_savings_by_period(iso):
    """Real per-statement gas savings: each billed period's own share of
    annual heating therms (from that period's OWN days' HDD, never a flat
    annual average) priced at that period's own TRUE MARGINAL rate (issue
    #98) -- the tier(s) the heating slice actually sits in, plus the flat
    Gas Energy Charge every therm pays -- rather than the period's blended
    average $/therm.

    HISTORY (why this replaced a blended-average approximation, Codex
    adversarial review, issue #1, two passes). Two real gas bill PDFs for
    this household's own GR-Residential rate were read directly
    (sdge_gas_2025-07-30.pdf, a low-usage summer statement; sdge_gas_2025-
    12-30.pdf, a 61-therm winter statement) to see what a gas bill actually
    contains: THREE separate charges. "Gas Service" is the two-tier
    baseline/nonbaseline rate (baseline_rate/nonbaseline_rate); a SEPARATE
    "Gas Energy Charge" line prices EVERY therm (baseline and nonbaseline
    alike) at a third, flat, untiered rate (gas_energy_charge_rate, ~
    $0.50/therm on the December statement); Public Purpose Programs and a
    State Regulatory Fee add a further ~$0.12/therm (folded into
    total_gas_service, not separately modeled here). Pricing heating therms
    at nonbaseline_rate ALONE would OMIT the Gas Energy Charge and the tax
    surcharge entirely. parse_bills.py (issue #98) now extracts
    baseline_allowance_therms and a day-weighted blended
    gas_energy_charge_rate into data/bill_periods_gas.csv (plus full
    per-segment detail into data/bill_gas_detail.csv), which is what makes
    the true marginal-tier pricing below possible -- the predecessor of this
    function used total_gas_service / therms (an all-in blended average)
    specifically because neither figure was available yet.

    JUDGMENT CALL (stated per CLAUDE.md section 8): baseline_rate/
    nonbaseline_rate/gas_energy_charge_rate are read from bill_periods_gas.
    csv's PERIOD-LEVEL day-weighted blend, not from bill_gas_detail.csv's
    per-segment detail, even though Gas Service can itself split into two
    segments with different tier rates within one period (a mid-cycle rate
    change, independent of the heating attribution's own day-level HDD
    weighting). This issue's own acceptance criteria ask for energy-charge-
    level marginal correctness on top of tier correctness, not segment-level
    Gas Service precision within a period -- the tier boundary
    (baseline vs. nonbaseline) is what determines which $/therm a heating
    therm pays, and getting that boundary right matters far more than which
    of a period's one or two Gas Service rate sub-segments it nets out to.
    Splitting heating therms across Gas Service's own sub-period segments
    would require deciding which CALENDAR DAYS within the period the
    heating slice occupies, a level of precision this function's HDD
    allocation does not carry (it allocates by period, not by sub-period
    segment) -- left as a further refinement if ever needed, not attempted
    here with a day-assignment this script has no basis for.
    """
    periods = pd.read_csv(GAS_PERIODS_CSV)
    periods["statement_date"] = pd.to_datetime(periods["statement_date"]).dt.date
    hdd_by_day = iso["hdd_by_day"]
    total_hdd = iso["total_hdd"]
    ann_heat = iso["annual_heating_therms"]
    floor_per_day = iso["floor_therms_per_day"]

    # Each period's own REAL service dates come straight from the CSV's own
    # "period" column ("Mon DD, YYYY - Mon DD, YYYY", the exact range SDG&E
    # itself prints on the statement) -- NOT reconstructed from adjacent
    # statement_dates. Codex adversarial review, issue #1, pass 3: an
    # earlier version of this function assumed "period" was unparseable
    # prose and rebuilt each period as (previous statement_date, this one],
    # which can be off by several real days from the printed range (the
    # 2025-11-28 statement's own printed period is Oct 28-Nov 25; the
    # reconstruction gave approximately Oct 30-Nov 28) -- shifting which
    # calendar days' HDD gets attributed to which period, and to which
    # period's own realized $/therm rate.
    periods[["period_start", "period_end"]] = periods["period"].str.split(
        " - ", expand=True).apply(lambda c: pd.to_datetime(c, format="%b %d, %Y").dt.date)
    periods = periods.sort_values("period_start").reset_index(drop=True)
    for i in range(1, len(periods)):
        prev_end = periods.loc[i - 1, "period_end"]
        this_start = periods.loc[i, "period_start"]
        gap = (this_start - prev_end).days
        if gap != 1:
            raise SystemExit(
                f"heat_pump_conversion.py: gas billing periods are not "
                f"contiguous -- {periods.loc[i - 1, 'statement_date']}'s period "
                f"ends {prev_end}, but {periods.loc[i, 'statement_date']}'s "
                f"period starts {this_start} ({gap - 1} day gap, or an "
                "overlap if negative) -- a real coverage gap changes which "
                "days' HDD gets attributed to which period's own rate")
    rows = []
    total_savings = 0.0
    total_allocated_heat = 0.0
    for i, row in periods.iterrows():
        start, end = row["period_start"], row["period_end"]
        period_hdd = hdd_by_day[(hdd_by_day.index >= start) & (hdd_by_day.index <= end)].sum()
        heat_share = ann_heat * (period_hdd / total_hdd) if total_hdd > 0 else 0.0
        therms = row["therms"]
        # Codex review, issue #1, pass 2: capping at total period therms
        # alone still lets a shoulder-season period attribute nearly ALL its
        # usage to heating, when this same model's own non-heating floor
        # (water heating/cooking, floor_per_day) had to run every one of
        # those days too. Reserve that floor's share of the period before
        # capping, so heating attribution can never exceed what's left after
        # the model's own non-heating usage is accounted for.
        period_days = (end - start).days + 1
        heating_capable_therms = max(0.0, therms - floor_per_day * period_days)
        heating_therms_attributed = min(heat_share, heating_capable_therms)
        savings = _heating_slice_cost(
            total_therms=therms, heating_therms=heating_therms_attributed,
            baseline_allowance=row["baseline_allowance_therms"],
            baseline_rate=row["baseline_rate"],
            nonbaseline_rate=row["nonbaseline_rate"],
            energy_rate=row["gas_energy_charge_rate"],
            context=f"{row['statement_date']} [{row['period']}]")
        all_in_rate = (savings / heating_therms_attributed
                       if heating_therms_attributed > 0 else 0.0)
        total_savings += savings
        total_allocated_heat += heat_share
        rows.append({
            "statement_date": str(row["statement_date"]),
            "therms": float(therms),
            "period_hdd": round(float(period_hdd), 1),
            "heating_therms_attributed": round(float(heating_therms_attributed), 2),
            "realized_rate_usd_per_therm": round(float(all_in_rate), 4),
            "gas_savings_usd": round(float(savings), 2),
        })
    return rows, round(total_savings, 2), round(total_allocated_heat)


def build_hp_load_series(d, iso, cop):
    """Three added-Consumption Series (index-aligned to d), one per
    distribution scenario, each summing to the SAME total heating kWh
    (energy conservation, CLAUDE.md section 1b) but placed in different
    intervals:

      uniform    -- ILLUSTRATIVE MIDPOINT, not a privileged central estimate.
                    Each heating day's own kWh (from that day's own HDD
                    share) spreads evenly across that day's own real
                    intervals -- a real, specific operational assumption
                    (uniform-in-time), not the absence of one.
      on_peak    -- ILLUSTRATIVE high-cost lean: every kWh spread evenly
                    across that day's on-peak (4-9pm) intervals only.
      off_peak   -- ILLUSTRATIVE low-cost lean: every kWh spread evenly
                    across that day's SUPER-OFF-PEAK intervals only
                    (rates.period()'s "sop" code) -- the genuinely cheapest
                    tariff period, not "not on-peak" (Codex adversarial
                    review, issue #1, pass 3: EV-TOU-5 has THREE tiers, not
                    two; a load spread across off-peak AND super-off-peak
                    together is not the cheap lean, since super-off-peak
                    alone prices lower than off-peak). Physically plausible
                    too: weekday super-off-peak runs before 6am and
                    10am-2pm -- exactly the overnight/pre-dawn window
                    ASHRAE's own pickup-load convention (cited in this
                    module's own docstring) says heating demand
                    concentrates in.

    Neither on_peak nor off_peak is a computed cost extremum: each spreads
    evenly across every interval of its own TOU window rather than solving
    for the single cheapest or costliest interval placement, so a real
    schedule concentrated into one specific interval within that window
    could in principle land outside this bracket in either direction
    (Codex review, issue #1, pass 3). These are illustrative
    TOU-concentrated distributions, not proven upper/lower bounds.

    A day with zero intervals of the required kind (on_peak/super-off-peak)
    falls back to uniform for that day alone, logged, never silently dropped
    -- weekend days can plausibly need this (rates.period() gives a weekend
    day sop only before 2pm, off afterward, so a mask CAN come up empty).
    """
    hdd_by_day = iso["hdd_by_day"]
    total_hdd = iso["total_hdd"]
    ann_heat_kwh = iso["annual_heating_therms"] * KWH_PER_THERM * FURNACE_AFUE / cop

    dates = d["dt"].dt.date
    out = {"uniform": pd.Series(0.0, index=d.index),
           "on_peak": pd.Series(0.0, index=d.index),
           "off_peak": pd.Series(0.0, index=d.index)}
    fallback_days = {"on_peak": 0, "off_peak": 0}
    for day, day_hdd in hdd_by_day.items():
        if day_hdd <= 0 or total_hdd <= 0:
            continue
        day_kwh = ann_heat_kwh * (day_hdd / total_hdd)
        mask = dates == day
        n = int(mask.sum())
        if n == 0:
            continue   # day not covered by the electric window at all
        out["uniform"].loc[mask] += day_kwh / n

        on_mask = mask & (d["p"] == "on")
        n_on = int(on_mask.sum())
        if n_on > 0:
            out["on_peak"].loc[on_mask] += day_kwh / n_on
        else:
            out["on_peak"].loc[mask] += day_kwh / n
            fallback_days["on_peak"] += 1

        sop_mask = mask & (d["p"] == "sop")
        n_sop = int(sop_mask.sum())
        if n_sop > 0:
            out["off_peak"].loc[sop_mask] += day_kwh / n_sop
        else:
            out["off_peak"].loc[mask] += day_kwh / n
            fallback_days["off_peak"] += 1

    return out, ann_heat_kwh, fallback_days


def electric_cost_scenarios(d, iso):
    """{cop_key: {dist_key: annual electric cost increase usd}}, every kWh
    total verified to conserve against ann_heat_kwh before being trusted.

    Added load is netted against that SAME interval's own solar Generation
    before it ever becomes new Consumption (Codex adversarial review, issue
    #1, pass 2, matching the established solar_absorbed_i convention
    perfect_foresight_dispatch.py already uses): a real house's added
    electric load is served first by whatever solar is being generated in
    that instant, and only spills into new grid import once that interval's
    export is exhausted. Adding the load straight into Consumption while
    leaving Generation untouched would manufacture simultaneous gross
    import AND export the household never actually has, and rates.py's own
    non-bypassable charge (NBC) is billed on GROSS imports under NEM
    (CLAUDE.md's own documented lesson) -- so an unnetted interval would
    overstate NBC on import that never happened.
    """
    base_bill = R.bill_nem(d, imp="Consumption", exp="Generation")
    out = {}
    for cop_key, cop in COP_SCENARIOS.items():
        added, ann_heat_kwh, fallback_days = build_hp_load_series(d, iso, cop)
        scen = {}
        for dist_key, series in added.items():
            total_added = float(series.sum())
            if ann_heat_kwh > 0 and abs(total_added - ann_heat_kwh) / ann_heat_kwh > 0.001:
                raise SystemExit(
                    f"heat_pump_conversion.py: {cop_key}/{dist_key} added "
                    f"{total_added:.1f} kWh, not the {ann_heat_kwh:.1f} kWh "
                    "the heating load requires -- energy is not conserved")
            absorbed = pd.concat([d["Generation"], series], axis=1).min(axis=1)
            remainder = series - absorbed
            f = d.copy()
            f["Generation"] = d["Generation"] - absorbed
            f["Consumption"] = d["Consumption"] + remainder
            # the netting step must not change total delivered energy: every
            # added kWh is now EITHER absorbed solar (Generation reduced) OR
            # new import (Consumption increased), never both, never lost
            assert abs(float(absorbed.sum() + remainder.sum()) - total_added) < 0.01, (
                cop_key, dist_key, "solar-netting step lost or duplicated energy")
            new_bill = R.bill_nem(f, imp="Consumption", exp="Generation")
            scen[dist_key] = {
                "added_kwh": round(total_added),
                "solar_absorbed_kwh": round(float(absorbed.sum())),
                "electric_cost_increase_usd": round(new_bill - base_bill, 2),
            }
        out[cop_key] = scen
        out[cop_key]["_fallback_days"] = fallback_days
    return out, round(base_bill, 2)


def payback_and_npv(annual_net_savings, install_cost, discount_rates, years):
    if annual_net_savings <= 0:
        return {"payback_years": None,
                "note": "no positive annual savings on this basis -- no payback"}
    payback = install_cost / annual_net_savings
    npv = {}
    for r in discount_rates:
        pv = sum(annual_net_savings / ((1 + r) ** y) for y in range(1, years + 1))
        npv[f"{int(r * 100)}pct"] = round(pv - install_cost)
    return {"payback_years": round(payback, 1), "npv": npv}


def sensitivity_table(iso, electric, gas_realized_rate_avg, gas_rows):
    """COP x install-cost x gas-price grid, each row a real payback/NPV, all
    on the PRIMARY (uniform) electric-distribution basis -- the on/off-peak
    bracket sensitivity is reported separately, alongside, not multiplied
    into this table (a 3x3x3x3 grid would bury the reader in scenarios no
    one asked to compare at once).

    low/central/high gas prices all share the SAME basis -- the TRUE
    MARGINAL-TIER realized rate gas_savings_by_period() actually bills a
    heating period's slice at (gas_savings_usd / heating_therms_attributed,
    the same per-period `realized_rate_usd_per_therm` figure in
    data/heat_pump_conversion.json's own gas_savings_by_period array),
    never a mix of that true-marginal rate and the period's blended
    average (issue #98, Codex adversarial review round 1: an earlier
    version of this function paired a true-marginal `central` against
    low/high still drawn from the retired total_gas_service/therms blended
    figure -- two different quantities, silently no longer sharing a basis
    once gas_savings_by_period() stopped using the blended average itself.
    Before that: Codex adversarial review, issue #1, pass 1, caught the
    same class of bug in the PRIOR design -- an earlier version compared a
    single-tier rate's low/high against an all-in blended-rate central,
    which could and did invert the ordering -- and a still-earlier attempt
    at using the nonbaseline tier rate alone turned out to OMIT two real
    charge components entirely, see gas_savings_by_period()'s own
    docstring). low/high come from the observed range of that SAME
    per-period true-marginal rate across this household's own real heating
    periods (periods with zero heating_therms_attributed have no realized
    rate to observe and are excluded, not treated as a zero price)."""
    per_period_rate = pd.Series([r["realized_rate_usd_per_therm"] for r in gas_rows
                                 if r["heating_therms_attributed"] > 0])
    gas_price_lo, gas_price_hi = float(per_period_rate.min()), float(per_period_rate.max())
    assert gas_price_lo <= gas_realized_rate_avg <= gas_price_hi, (
        "sensitivity_table: low/central/high gas prices are not monotonically "
        f"ordered ({gas_price_lo}, {gas_realized_rate_avg}, {gas_price_hi}) -- "
        "they no longer share the same rate basis")
    gas_prices = {"low": gas_price_lo, "central": gas_realized_rate_avg, "high": gas_price_hi}

    rows = []
    for cop_key in COP_SCENARIOS:
        electric_increase = electric[cop_key]["uniform"]["electric_cost_increase_usd"]
        ann_heat_kwh = electric[cop_key]["uniform"]["added_kwh"]
        for cost_key, cost in zip(("low", "central", "high"), INSTALL_COST_SENSITIVITY_USD):
            for price_key, price in gas_prices.items():
                gas_savings = iso["annual_heating_therms"] * price
                net_savings = gas_savings - electric_increase
                pb = payback_and_npv(net_savings, cost, DISCOUNT_RATES, NPV_HORIZON_YEARS)
                rows.append({
                    "cop": cop_key, "install_cost": cost_key,
                    "gas_price": price_key,
                    "install_cost_usd": cost,
                    "gas_price_usd_per_therm": round(price, 4),
                    "annual_heating_kwh": ann_heat_kwh,
                    "gas_savings_usd": round(gas_savings, 2),
                    "electric_cost_increase_usd": electric_increase,
                    "annual_net_savings_usd": round(net_savings, 2),
                    "payback_years": pb["payback_years"],
                })
    return rows


def build():
    if not HAS_GAS:
        return {"applicable": False, "reason": "household.has_gas is false"}

    d = BR.load()
    iso = isolate_heating_therms()
    gas_rows, gas_savings_annual, allocated_heat_therms = gas_savings_by_period(iso)
    if abs(allocated_heat_therms - iso["annual_heating_therms"]) > 2:
        raise SystemExit(
            "heat_pump_conversion.py: gas savings allocated across billing "
            f"periods ({allocated_heat_therms} therms) disagrees with the "
            f"annual heating estimate ({iso['annual_heating_therms']} therms) "
            "by more than 2 therms -- the period date-range reconstruction "
            "or the HDD allocation has a bug")
    gas_realized_rate_avg = sum(r["gas_savings_usd"] for r in gas_rows) / \
        sum(r["heating_therms_attributed"] for r in gas_rows if r["heating_therms_attributed"] > 0)

    # Codex review, issue #1, pass 3 (P1): the raw HDD-regression estimate
    # (iso["annual_heating_therms"]) is an unconstrained annual extrapolation;
    # once each period's own non-heating floor is reserved (above), not all
    # of it can actually be attributed to a specific billed period without
    # exceeding what that period billed. Sizing the heat pump's electric
    # load on the raw (larger) figure while crediting gas savings on the
    # capped (smaller) figure silently modeled and paid for two different
    # amounts of heat. Reconcile: the heat pump only has to replace, and
    # only earns credit for, the therms this analysis can actually attribute
    # to a real billed period -- so both sides use the SAME reconciled total.
    reconciled_heat_therms = round(
        sum(r["heating_therms_attributed"] for r in gas_rows), 2)
    iso_reconciled = {**iso, "annual_heating_therms": reconciled_heat_therms}

    electric, baseline_bill_usd = electric_cost_scenarios(d, iso_reconciled)

    paybacks = {}
    for cop_key in COP_SCENARIOS:
        electric_increase = electric[cop_key]["uniform"]["electric_cost_increase_usd"]
        net_savings = gas_savings_annual - electric_increase
        paybacks[cop_key] = {
            "annual_gas_savings_usd": gas_savings_annual,
            "annual_electric_cost_increase_usd": electric_increase,
            "annual_net_savings_usd": round(net_savings, 2),
            "standalone": payback_and_npv(
                net_savings, INSTALL_COST_STANDALONE_USD, DISCOUNT_RATES, NPV_HORIZON_YEARS),
            "marginal_over_ac_replacement": payback_and_npv(
                net_savings, INSTALL_COST_MARGINAL_USD, DISCOUNT_RATES, NPV_HORIZON_YEARS),
        }

    out = {
        "applicable": True,
        "basis": ("furnace therms isolated two ways and cross-checked; heat "
                  "pump kWh added into real 15-minute intervals and the "
                  "whole measured year re-billed with rates.bill_nem() "
                  "(canonical NEM engine, never a lump-sum multiply); gas "
                  "savings priced at each real billing period's own "
                  "realized $/therm"),
        "isolation": {k: v for k, v in iso.items() if k not in ("hdd_by_day",)},
        "reconciled_heating_therms_yr": reconciled_heat_therms,
        "gas_savings_by_period": gas_rows,
        "gas_savings_annual_usd": gas_savings_annual,
        "gas_realized_rate_avg_usd_per_therm": round(gas_realized_rate_avg, 4),
        "baseline_electric_bill_usd": baseline_bill_usd,
        "electric_cost_by_scenario": electric,
        "cooling_side": {
            "treatment": "roughly a wash vs a like-for-like AC replacement, "
                        "not separately re-billed",
            "seer2_note": ("2026 federal minimum residential ducted "
                           "efficiency is 14.3 SEER2/11.7 EER2 (the exact "
                           "baseline the CZ7 cost-effectiveness study above "
                           "uses for its new-AC comparison, Table 8); "
                           "mid-tier units run 16-18 SEER2, high-efficiency "
                           "19-21+ SEER2. Coastal San Diego's mild summers "
                           "mean the cooling-side efficiency delta between a "
                           "heat pump's cooling mode and a same-tier "
                           "standalone AC is small and not separately "
                           "quantified here -- a secondary term, per this "
                           "issue's own scope."),
        },
        "install_cost": {
            "note": INSTALL_COST_NOTE,
            "standalone_usd": INSTALL_COST_STANDALONE_USD,
            "ac_only_replacement_usd": INSTALL_COST_AC_ONLY_REPLACEMENT_USD,
            "marginal_over_ac_replacement_usd": INSTALL_COST_MARGINAL_USD,
            "sensitivity_range_usd": list(INSTALL_COST_SENSITIVITY_USD),
        },
        "incentives": {
            "usd": INCENTIVE_USD,
            "verified_date": INCENTIVE_VERIFIED_DATE,
            "sources": [
                "IRS 25C credit: terminated for property placed in service "
                "after 2025-12-31 under P.L. 119-21 (One Big Beautiful Bill "
                "Act) -- irs.gov/credits-deductions/energy-efficient-home-"
                "improvement-credit",
                "TECH Clean California single-family heat-pump-HVAC "
                "reservations: closed statewide 2025-11-14 -- "
                "techcleanca.com/incentives/single-family-incentives/",
                "HEEHRA (federal IRA rebate, CA-administered via TECH): "
                "single-family rebates fully reserved statewide as of "
                "2026-02-24 -- techcleanca.com/incentives/heehrarebates/",
                "SGIP (CPUC): covers batteries and heat-pump water heaters, "
                "not space-heating HVAC; ratepayer budgets closed "
                "2025-12-31 regardless",
                "SDG&E residential electrification page: no dedicated HVAC "
                "heat-pump rebate named, only a referral to a third-party "
                "readiness program (CHERP) -- sdge.com/residential/savings-"
                "center/tips/home-electrification",
            ],
        },
        "payback": paybacks,
        "sensitivity_table": sensitivity_table(iso_reconciled, electric, gas_realized_rate_avg, gas_rows),
        "npv_horizon_years": NPV_HORIZON_YEARS,
        "discount_rates": list(DISCOUNT_RATES),
    }
    return out


def main():
    out = build()
    tmp = os.path.join(DATA, "heat_pump_conversion.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(DATA, "heat_pump_conversion.json"))
    print("wrote data/heat_pump_conversion.json")
    if out["applicable"]:
        c = out["payback"]["central_3.5"]
        print(f"central COP (3.5): net savings ${c['annual_net_savings_usd']}/yr, "
              f"standalone payback {c['standalone']['payback_years']} yr, "
              f"marginal-over-AC payback {c['marginal_over_ac_replacement']['payback_years']} yr")


if __name__ == "__main__":
    main()
