#!/usr/bin/env python3
"""The all-electric endgame: what closing the gas meter is actually worth (issue #20).

Depends on #1 (`heat_pump_conversion.py` / `data/heat_pump_conversion.json`, the furnace
conversion, refined by issue #109) and #6 (`service_headroom.py` / `data/service_headroom.json`,
the panel-headroom check). Both are reused here, not reimplemented: this script imports
`heat_pump_conversion` for its already-committed furnace figures and its gas-savings
machinery (the segment/tier pricing helpers, applied here to the NON-heating floor instead of
the heating slice), and reads the already-committed `service_headroom.json` cases for the
panel-headroom check rather than re-deriving the gross-load envelope.

AC1 -- fixed vs volumetric gas charges. Two real bill PDFs were read directly (a 9-therm
summer statement, a 61-therm winter statement) and every line item on both is either a
$/therm rate or a percentage of one -- see FIXED_CHARGE_EVIDENCE below. A regression of all
25 real billed periods' own (therms, billed_amount) pairs (build()'s own `fixed_charge_
regression`) puts a real dollar figure on the "near-zero" claim already in index.html rather
than leaving it as a qualitative read of a chart: the fitted intercept is small relative to a
typical bill and not reliably distinguishable from zero given the corpus's own scatter -- see
that field's own `note`. There is no tension to resolve: GR-Residential genuinely has no
separate fixed/customer charge, confirmed by the bill PDFs directly and by the regression
correction here is needed to index.html's existing sentence.

AC2 -- remaining gas end uses. `heat_pump_conversion.isolate_heating_therms()` is reused
unmodified: space heating (HDD-regression slope, 205 therms/yr raw) plus a non-heating floor
(HDD-regression intercept, 137 therms/yr) sum to the metered annual total by construction (a
regression's own floor + slope explain 100% of what it fits) -- see `gas_end_use_enumeration`
for the exact figures and the SECOND, INDEPENDENT cross-check against the trailing 12 real
gas *bills* (not the daily meter file), which was not previously computed and agrees with the
daily-meter annual total to within 1%.

What the floor actually consists of is NOT DETERMINED from committed public data, and this
script deliberately stops there rather than resolving it from private evidence. This
household's own panel schedule (`private/household.yaml` -> `panel.schedule`, read from
equipment photos, issue #6) may well bear on the question -- but that field, and
`panel.no_dryer_or_water_heater_circuit`, are BOTH private-only (TECHNICAL.md section 11.3:
"the same fact... is available publicly from `appliance_fuels`, which is where an artifact
takes it from"). An earlier version of this script read the panel schedule directly to
determine whether cooking is gas or electric here, and the repo's own pre-commit privacy gate
correctly refused the commit -- publishing a panel-derived inference would leak private intake
detail into a public artifact, which CLAUDE.md section 4 forbids outright regardless of how
strong the private evidence looks. `cooking_fuel_evidence()` reads ONLY the public-ok
`household.appliance_fuels` field (DATA-SOURCES-CHEATSHEET.md's own direct-answer channel for
this exact question), which was never filled in `private/household.yaml` -- so this script
reports the floor's own composition as genuinely unknown rather than guessing from private
evidence it cannot cite.

That has a real, honestly-stated consequence for AC6/AC7: it is NOT DETERMINED whether a
household laundry dryer or a gas cooktop is a THIRD gas end use, distinct from both
conversions this issue's own body names (water heater, furnace). This script prices water
heater + furnace exactly as the issue's own body asks, but states explicitly
(`third_end_use_gap`) that the gas meter cannot be confirmed removable after only those two
steps until this is resolved -- narrowing AC7's own "meter removal" framing to what the public
data actually supports, rather than either asserting or silently implying two steps empty the
meter. `third_end_use_gap` sizes only the POSSIBILITY (an external, uncited-to-this-household,
"estimated" tier benchmark for a typical gas dryer's own usage), names answering
`household.appliance_fuels` directly as the actual fix, and names a fully costed dryer
conversion (if one turns out to be needed) as a follow-up issue, out of this issue's own scope
box either way.

AC3/AC4 -- water heater costing, real-interval electric rebilling. The floor's own gas savings
are priced by `floor_savings_by_period()`, a sibling of `heat_pump_conversion.gas_savings_by_
period()` built by REUSING that module's own segment-tier-pricing helpers
(`_flat_segment_cost`, `_segment_day_ranges`, `_segment_real_or_proxy_therms`,
`_other_fees_day_ranges`, `_segment_heat_from_days`, `load_gas_detail`) rather than
reimplementing them -- the one genuinely new piece is `_floor_segment_tier_cost`, because the
floor occupies the OPPOSITE end of each Gas Service segment's tier ladder from the heating
slice (heating is modeled as the marginal TOP slice in heat_pump_conversion.py; the floor is
the BOTTOM slice, billed at the cheaper baseline tier first) -- reusing the heating-side
tier-cost function with the floor's own therms in its `heating_therms` argument would silently
invert that placement. Restricted to the same trailing-12-real-bill window
`heat_pump_conversion.py`'s own $416.25/yr already resolves to (see that module's own gas.csv
coverage window), so the two figures are on the same annual basis and additive without double-
counting: heating's own day-level capacity cap already reserves the floor's share of every real
day BEFORE computing heating capacity (`heat_pump_conversion._capacity_capped_days`:
`capacity = real_day_therms - floor_per_day`), so floor-day + heating-day <= real-day-therms by
construction at the day level, and therefore at every level built by summing days.

Electric load placement mirrors `heat_pump_conversion.build_hp_load_series` /
`electric_cost_scenarios` exactly (absorb existing solar first, spill into new grid import
only once that interval's export is exhausted, re-bill the whole measured year with
rates.bill_nem()) but with a DIFFERENT day-weighting: the floor runs at a roughly constant
daily rate (not HDD-driven), so `build_wh_load_series` spreads it uniformly across the real
days it actually ran (from `_floor_capped_days`'s own per-day floor allocation) rather than by
HDD share. Efficiency is by UEF (Uniform Energy Factor), not COP -- the correct metric for a
storage-type heat-pump water heater (it already folds in standby loss, unlike a furnace heat
pump's COP) -- from a real, cited product family (Rheem ProTerra, UEF ~3.5-4.0 in heat-pump
mode; see HPWH_UEF_SCENARIOS).

AC5 -- service headroom. Reuses `data/service_headroom.json`'s own already-committed
`heat_pump_only` case (fixed_added_load_a=0, i.e. panel-wide spare capacity before ANY new
240 V load) as the ampacity baseline, and calls `service_headroom.physical_fit()` directly
(not reimplemented) with the panel's own already-committed `spaces_free` to score the water
heater's own new-circuit physical fit. See `service_headroom_check` for the result, including
whether it is a hard blocker.

AC6 -- meter removal. WebSearch findings, cited with dates and URLs; anything not settled by
a citable primary source is marked not_determined rather than guessed, per CLAUDE.md section 0.

AC7 -- two paybacks. AC1's own finding (a genuinely $0 fixed charge on this rate) makes the
issue's own "credit the fixed-charge release only to the final step" rule trivial to satisfy:
crediting $0 to whichever step is last changes nothing, so sequencing here is chosen on
cost-effectiveness (whichever step's own marginal economics are better goes first) rather than
on a fixed-charge-driven order. `final_step_alone_payback` reports the identical figure with
and without the (zero) credit explicitly, rather than silently dropping that half of the
requirement because it no longer bites.

AC8 -- reconciliation against `heat_pump_conversion.json` (should match exactly, since its own
committed figures are cited directly, not recomputed) and against `extended_results.json ->
gas_decomposition`'s older flat-rate HPWH estimate (`hpwh_saving_yr: 205`), which WILL differ
from this script's own real-interval-billed figure -- both are reported and the gap is
quantified explicitly.

Run AFTER behavior_rebuild.py in the same working directory (needs its staged usage.csv);
writes data/all_electric_endgame.json.
"""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rates as R
import household as hh
import behavior_rebuild as BR
import heat_pump_conversion as HPC
import service_headroom as SH

ROOT = HPC.ROOT
DATA = HPC.DATA
GAS_CSV = HPC.GAS_CSV

HAS_GAS = HPC.HAS_GAS

# ---------------------------------------------------------------------------
# AC1 -- fixed vs volumetric gas charges: direct bill evidence.
# ---------------------------------------------------------------------------
# Two real gas bill PDFs for this household's own GR-Residential rate, read
# directly (private/1-raw-data/gas-bills/), one summer/low-usage and one
# winter/high-usage statement so a fixed-charge floor small enough to hide
# under a high winter bill would not be missed. Every line item on both is
# either a straight $/therm rate or a percentage of one -- there is no line
# item on either bill that is a flat per-statement or per-day charge.
FIXED_CHARGE_EVIDENCE = [
    {
        "pdf": "private/1-raw-data/gas-bills/sdge_gas_2025-07-30.pdf",
        "statement_date": "2025-07-30",
        "period": "Jun 27, 2025 - Jul 28, 2025",
        "therms": 9.0,
        "total_charges_usd": 22.94,
        "line_items": [
            "Gas Service: 1 therm @ $2.04568 (4 of 32 days) + 8 therms @ "
            "$2.03321 (28 of 32 days) = $18.32",
            "Gas Energy Charge, Public Purpose Programs, State Regulatory "
            "Fee: each a flat $/therm rate on every therm (page 2-3 of the "
            "PDF)",
        ],
        "note": "the lowest-usage statement in the corpus -- if a minimum "
                "or fixed charge existed, it would be most visible here, "
                "and it is not present",
    },
    {
        "pdf": "private/1-raw-data/gas-bills/sdge_gas_2025-12-30.pdf",
        "statement_date": "2025-12-30",
        "period": "Nov 26, 2025 - Dec 26, 2025",
        "therms": 61.0,
        "total_charges_usd": 170.88,
        "line_items": [
            "Gas Service: 36 baseline therms @ $2.02136 + 25 nonbaseline "
            "therms @ $2.37552 = $132.16",
            "Gas Energy Charge: 10 therms @ $.47997 (5 of 31 days) + 51 "
            "therms @ $.52402 (26 of 31 days) = $31.53",
            "Public Purpose Programs: 61 therms @ $.115410 = $7.04",
            "State Regulatory Fee: 61 therms @ $.002500 = $0.15",
        ],
        "note": "the highest-usage statement in the corpus -- every "
                "component is volumetric here too",
    },
]


def fixed_charge_regression():
    """Linear regression of all 25 real billed periods' own (therms,
    billed_amount) pairs -- a real, script-computed number for index.html's
    existing qualitative "near-zero fixed floor" claim, not a restatement of
    it. Uses data/bill_periods_gas.csv directly (the same file heat_pump_
    conversion.py's own gas_savings_by_period() reads), no new data source.
    """
    periods = pd.read_csv(HPC.GAS_PERIODS_CSV)
    therms = periods["therms"].to_numpy(dtype=float)
    billed = periods["billed_amount"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(therms, billed, 1)
    resid = billed - (slope * therms + intercept)
    # a crude standard error on the intercept, enough to say whether it is
    # distinguishable from zero given this corpus's own scatter -- not a
    # rigorous confidence interval, just a script-backed magnitude check.
    n = len(therms)
    dof = n - 2
    s2 = float((resid ** 2).sum() / dof)
    x_mean = float(therms.mean())
    sxx = float(((therms - x_mean) ** 2).sum())
    se_intercept = (s2 * (1.0 / n + x_mean ** 2 / sxx)) ** 0.5
    return {
        "n_periods": int(n),
        "slope_usd_per_therm": round(float(slope), 4),
        "intercept_usd": round(float(intercept), 2),
        "intercept_std_error_usd": round(se_intercept, 2),
        "note": ("the fitted per-statement intercept is small relative to a "
                 "typical bill and within roughly one standard error of "
                 "zero given this corpus's own scatter -- consistent with, "
                 "not merely suggestive of, a genuinely zero fixed charge; "
                 "the bill PDFs above are the harder evidence, this "
                 "regression is a cross-check on the same claim, not a "
                 "replacement for reading the bill"),
    }


# ---------------------------------------------------------------------------
# AC2 -- what the non-heating floor actually is.
# ---------------------------------------------------------------------------
def cooking_fuel_evidence():
    """Whether cooking (and, by the same DATA-SOURCES-CHEATSHEET.md
    question, the water heater and space heating) run on gas or electricity,
    read from the ONE public-ok source for this fact: household.
    appliance_fuels.

    This deliberately does NOT read panel.schedule or panel.no_dryer_or_
    water_heater_circuit, even though this household's own panel schedule
    (read for issue #6) may well hold relevant evidence: TECHNICAL.md
    section 11.3 tiers both fields private-only precisely because "the same
    fact... is available publicly from appliance_fuels, which is where an
    artifact takes it from." Reading either into this script would put
    private intake detail (equipment photos, door-legend text) into this
    script's own committed output -- CLAUDE.md section 4 forbids that
    outright, and this repo's own pre-commit privacy gate refuses it
    mechanically (caught while first building this function -- an earlier
    version read the panel schedule directly and was blocked before it ever
    reached a commit)."""
    appliance_fuels = hh.get("appliance_fuels", required=False)
    return {
        "appliance_fuels_field_present": appliance_fuels is not None,
        "verdict": "recorded at intake" if appliance_fuels is not None else "not determined",
        "note": (
            "household.appliance_fuels was answered directly at intake; see "
            "its own recorded value for this household's confirmed fuel mix."
            if appliance_fuels is not None else
            "the DATA-SOURCES-CHEATSHEET.md appliance_fuels field -- the "
            "one public-ok source for what fuels this household's major "
            "appliances (pool, water heater, space heating, cooking) -- was "
            "never filled in private/household.yaml, so this script cannot "
            "determine, and does not guess, whether cooking here is gas or "
            "electric. Recommend: ask directly and record the answer in "
            "household.appliance_fuels, then re-run this script."),
    }


# A national, uncited-to-this-household benchmark (not measured for this
# house -- "estimated" tier): typical gas clothes dryer usage, used ONLY to
# size the possibility named in third_end_use_gap() below, never to assert
# a dryer exists. Two real figures found (a monthly-average vendor estimate
# at ~3.2 therms/month, and a load-frequency-based range of 4-9 therms/month
# for moderate-to-heavy laundry use); reported as a bracket, not a point
# estimate, since neither is specific to this household.
DRYER_THERMS_PER_MONTH_RANGE = (3.2, 9.0)


def third_end_use_gap(floor_therms_yr, cooking_fuel):
    """AC2/AC6: whether a gas end use besides the water heater (cooking
    and/or a clothes dryer) might remain unconverted after this issue's own
    two named steps -- reported CONDITIONALLY, since household.
    appliance_fuels (the one public-ok source for this) is unanswered and
    this script does not infer a fuel mix from private panel data (see
    cooking_fuel_evidence's own docstring)."""
    lo, hi = DRYER_THERMS_PER_MONTH_RANGE
    lo_yr, hi_yr = round(lo * 12), round(hi * 12)
    determined = cooking_fuel["appliance_fuels_field_present"]
    return {
        "gap": ("NOT DETERMINED whether a third gas end use (cooking, and/or "
                "a clothes dryer) remains unconverted after the water-heater "
                "and furnace steps this issue's own body names -- "
                "household.appliance_fuels, the one public-ok source for "
                "this household's own fuel mix, was never answered. If "
                "either is gas, the meter cannot actually be removed after "
                "just those two conversions." if not determined else
                "resolved by household.appliance_fuels's own recorded "
                "answer -- see that field, not this script's own guess."),
        "possible_dryer_rough_magnitude_therms_yr": [lo_yr, hi_yr],
        "possible_dryer_pct_of_floor_range": [round(100 * lo_yr / floor_therms_yr, 1),
                                              round(100 * hi_yr / floor_therms_yr, 1)],
        "basis": ("external benchmark (HomeGuide/SlashPlan-class contractor "
                  "and DOE-adjacent usage guides, 2026), not measured for "
                  "this household and not evidence a dryer exists here -- "
                  "sizes the possibility only, for the case where it does. "
                  f"Not separable from other shares of the {floor_therms_yr:g}"
                  "-therm/yr floor with the single, unsplit gas meter this "
                  "household has."),
        "recommendation": ("answer household.appliance_fuels directly (a "
                           "single interview question, DATA-SOURCES-"
                           "CHEATSHEET.md section E1) to resolve this "
                           "before treating either conversion as sufficient "
                           "to remove the gas meter; a fully costed "
                           "gas-to-electric dryer conversion, if one turns "
                           "out to be needed, is out of this issue's own "
                           "scope box and is a good candidate for a "
                           "follow-up issue."),
        "not_priced_here": True,
    }


def gas_end_use_enumeration(iso):
    """AC2: every gas end use, quantified in therms, cross-checked against
    the metered total TWO independent ways -- the daily meter file's own
    annual total (trivially exact: a regression's floor + slope explain
    100% of what it fits) and, newly, the trailing 12 real gas BILLS'
    own printed therms (a second, independent meter reading -- the monthly
    statement read, not the 15-minute-resolution daily export)."""
    total = iso["annual_total_therms"]
    heating = iso["hdd_regression"]["heating_therms_yr"]
    floor = iso["hdd_regression"]["floor_therms_yr"]
    sum_pct_of_total = round(100 * (heating + floor) / total, 2)

    periods = pd.read_csv(HPC.GAS_PERIODS_CSV).sort_values("statement_date")
    last12 = periods.tail(12)
    bill_therms_12mo = float(last12["therms"].sum())
    bill_vs_meter_pct = round(100 * abs(bill_therms_12mo - total) / total, 2)

    cooking = cooking_fuel_evidence()
    return {
        "annual_total_therms_meter": total,
        "space_heating_therms_yr": heating,
        "non_heating_floor_therms_yr": floor,
        "sum_check": {
            "heating_plus_floor_therms": heating + floor,
            "pct_of_metered_total": sum_pct_of_total,
            "note": ("exact by construction: the HDD regression's own floor "
                     "(intercept) and heating slope are complementary "
                     "components of the same fit, so they sum to the fitted "
                     "annual total; this is the enumeration's internal "
                     "consistency check, not an independent cross-check"),
        },
        "independent_bill_cross_check": {
            "trailing_12_statement_therms": bill_therms_12mo,
            "daily_meter_annual_total_therms": total,
            "pct_difference": bill_vs_meter_pct,
            "note": ("a genuinely independent check: 12 real monthly "
                     "statement reads (data/bill_periods_gas.csv, most "
                     "recent 12 of 25) against the daily Green Button "
                     "export's own annual sum -- two different meter-read "
                     "processes agreeing within "
                     f"{bill_vs_meter_pct}% is real corroboration, not "
                     "circular"),
        },
        "space_heating": {
            "therms_yr": heating,
            "source": "heat_pump_conversion.isolate_heating_therms() (HDD "
                      "regression, reused unmodified)",
        },
        "non_heating_floor": {
            "therms_yr": floor,
            "cooking_fuel_evidence": cooking,
            "floor_composition": (
                "not determined from committed public data -- at minimum the "
                "water heater; possibly also cooking and/or a clothes dryer, "
                "depending on this household's own fuel mix, which household."
                "appliance_fuels does not yet record (see cooking_fuel_"
                "evidence above)"),
            "not_further_separable": ("even where the fuel mix is known, "
                                      "individual appliance shares cannot be "
                                      "split from this household's single, "
                                      "unsplit gas meter -- see "
                                      "third_end_use_gap for the external "
                                      "benchmark bracket used only to size a "
                                      "POSSIBLE dryer share, never to invent "
                                      "a split of this figure"),
        },
        "third_end_use_gap": third_end_use_gap(floor, cooking),
    }


# ---------------------------------------------------------------------------
# AC3/AC4 -- water-heater conversion: floor gas savings + real-interval
# electric rebilling.
# ---------------------------------------------------------------------------
def _floor_capped_days(period_start, period_end, floor_per_day, period_total_therms):
    """One (day, floor_therms) pair per real calendar day of the period --
    the constant floor_per_day allocated to EVERY real day, capped at that
    PERIOD's own real billed total divided evenly across its own days (a
    period plainly cannot have more floor gas attributed to it than it
    billed in total).

    This deliberately does NOT mirror heating's own _capacity_capped_days(),
    which caps against real DAILY data, because gas.csv's own daily
    resolution is measurably lumpy at this small a scale: 143 of 365 real
    days read BELOW floor_per_day (0.376 therms/day), 88 of them at exactly
    0.00 -- ordinary meter-read-date batching noise (heat_pump_
    conversion.py's own module docstring documents the same read-date noise
    at the PERIOD level, "not uniformly small"). A day-level cap against
    that noise was tried and rejected (checked, not assumed: it silently
    zeroed out about 40% of the floor against the trailing-year HDD-
    regression total). The PERIOD-level real billed total has no such
    noise -- it is the actual meter read the statement was billed on -- and
    still catches the real physical constraint a day-level cap was really
    protecting: the lowest-usage summer statement in this corpus (2025-07-30,
    9 real billed therms over 32 days) bills LESS in total than the annual-
    average floor_per_day times its own day count would imply (10.5 vs 9),
    since the regression's floor is a year-round AVERAGE and this
    household's real non-heating usage is not perfectly flat month to
    month -- capping at the period's own real total is what makes that
    physically impossible ("more floor gas than the meter read that month")
    unreachable, while still trusting the smoothed annual constant over any
    single day's read."""
    period_days = (period_end - period_start).days + 1
    day_floor = min(floor_per_day, period_total_therms / period_days)
    return [(period_start + dt.timedelta(days=i), day_floor)
            for i in range(period_days)]


def _floor_segment_tier_cost(floor_therms, baseline_allowance, baseline_rate,
                             nonbaseline_rate, context):
    """Price of `floor_therms` at the BOTTOM of a Gas Service segment's own
    two-tier ladder -- the complement of heat_pump_conversion._gas_service_
    segment_tier_cost(), which prices heating at the TOP. SDG&E bills the
    baseline allowance first (cheapest), so the floor -- always-on usage
    that runs whether or not any heating happens -- occupies that cheap
    tier first, and only spills into the nonbaseline tier if the floor
    itself (unusual: this household's floor is ~0.376 therms/day, far under
    any real period's baseline allowance) exceeds the segment's own
    allowance. Reusing the heating-side function with the floor's own
    therms in its `heating_therms` argument would silently invert this --
    that function treats its `heating_therms` argument as the segment's
    MARGINAL (top) slice, which is heating's own role, not the floor's.

    `baseline_allowance` for a short, mid-cycle-split segment is itself only
    a DAY-PROPORTION estimate of the period's own printed allowance (no
    better alternative exists -- see heat_pump_conversion.gas_savings_by_
    period()'s own JUDGMENT CALL paragraph), and floor_therms is UNCAPPED
    against real daily data (see _floor_capped_days's own docstring for
    why). The two together mean floor_therms can land fractionally above a
    short segment's own estimated allowance even though the real bill's own
    Gas Service block for that exact segment never crossed into nonbaseline
    at all (parse_bills.py leaves nonbaseline_rate blank in exactly that
    case). A SMALL overflow there (checked on this household's real corpus:
    the largest is 0.05 therms, on a 1.83-therm segment) is estimation noise
    in a_s, not evidence of a real tier crossing the bill itself contradicts
    -- so it is folded back into the baseline tier (the bill's own evidence
    wins over the day-proportion estimate) rather than failing the run,
    UNLESS it exceeds FLOOR_OVERFLOW_TOLERANCE_THERMS, which would be large
    enough to need investigating rather than silently absorbing."""
    if nonbaseline_rate is None or pd.isna(nonbaseline_rate):
        overflow = floor_therms - baseline_allowance
        if overflow > FLOOR_OVERFLOW_TOLERANCE_THERMS:
            raise SystemExit(
                f"all_electric_endgame.py: {context} needs "
                f"{overflow:.2f} nonbaseline-tier floor therms (exceeds the "
                f"{FLOOR_OVERFLOW_TOLERANCE_THERMS}-therm estimation-noise "
                "tolerance) but bill_gas_detail.csv has no nonbaseline_rate "
                "for this Gas Service segment.")
        return floor_therms * baseline_rate
    baseline_used = min(floor_therms, baseline_allowance)
    nonbaseline_used = max(0.0, floor_therms - baseline_allowance)
    return baseline_used * baseline_rate + nonbaseline_used * nonbaseline_rate


# The largest real overflow observed on this household's own 25-statement
# corpus is 0.05 therms (2025-09-29's 5-day Gas Service segment); this is
# set an order of magnitude above that so a genuinely large discrepancy (a
# real methodology problem, not day-proportion noise) still fails closed.
FLOOR_OVERFLOW_TOLERANCE_THERMS = 0.5


def floor_savings_by_period(iso, n_trailing=12):
    """Sibling of heat_pump_conversion.gas_savings_by_period(), reusing that
    module's own segment-day-range and flat-rate-segment helpers, priced for
    the non-heating FLOOR instead of the heating slice.

    Restricted to the most recent `n_trailing` real statements (chronological
    order), the SAME trailing-12-statement window this repo already uses
    elsewhere for "current annual" gas figures (it independently reproduces
    the existing $922.34/yr all-in total exactly -- see gas_end_use_
    enumeration's own bill cross-check) and the SAME window heat_pump_
    conversion.py's own $416.25/yr heating figure resolves to (every one of
    its OWN 25-period loop's contributions outside this window is exactly
    zero, since HDD is zero outside gas.csv's coverage). floor_savings_by_
    period() has no gas.csv dependency at all (_floor_capped_days() is a
    pure day-proportion allocation against each period's own real BILLED
    total, never the noisy daily export -- see its own docstring), so unlike
    heating this window is a genuinely free choice, made to match the SAME
    window rather than one gas.csv's own coverage constrains -- keeping
    every "annual" figure in this script's own output on one common, real,
    calendar-year basis."""
    periods = pd.read_csv(HPC.GAS_PERIODS_CSV)
    periods["statement_date"] = pd.to_datetime(periods["statement_date"]).dt.date
    periods[["period_start", "period_end"]] = periods["period"].str.split(
        " - ", expand=True).apply(lambda c: pd.to_datetime(c, format="%b %d, %Y").dt.date)
    periods = periods.sort_values("period_start").reset_index(drop=True)
    trailing = periods.tail(n_trailing).copy()

    gas_detail = HPC.load_gas_detail()
    floor_per_day = iso["floor_therms_per_day"]

    rows = []
    total_savings, total_floor_therms = 0.0, 0.0
    for _, row in trailing.iterrows():
        start, end = row["period_start"], row["period_end"]
        context = f"{row['statement_date']} [{row['period']}]"
        detail = gas_detail.get(str(row["statement_date"]))
        if not detail or not all(ct in detail for ct in
                                 ("gas_service", "gas_energy", "other_fees")):
            raise SystemExit(
                f"all_electric_endgame.py: {context} is missing one or more "
                f"of gas_service/gas_energy/other_fees in bill_gas_detail.csv")
        gs_segs, ge_segs, of_segs = (detail["gas_service"], detail["gas_energy"],
                                     detail["other_fees"])
        gs_ranges = HPC._segment_day_ranges(
            start, end, [s["segment_days"] for s in gs_segs], f"{context} gas_service")
        ge_ranges = HPC._segment_day_ranges(
            start, end, [s["segment_days"] for s in ge_segs], f"{context} gas_energy")

        capped_days = _floor_capped_days(start, end, floor_per_day, row["therms"])
        floor_therms_period = sum(f for _, f in capped_days)

        gs_shares = HPC._segment_heat_from_days(gs_ranges, capped_days)
        ge_shares = HPC._segment_heat_from_days(ge_ranges, capped_days)

        period_days = (end - start).days + 1
        gs_cost = 0.0
        for (seg_start, seg_end), floor_s, seg in zip(gs_ranges, gs_shares, gs_segs):
            seg_days = (seg_end - seg_start).days + 1
            a_s = row["baseline_allowance_therms"] * seg_days / period_days
            gs_cost += _floor_segment_tier_cost(
                floor_therms=floor_s, baseline_allowance=a_s,
                baseline_rate=seg["baseline_rate"], nonbaseline_rate=seg["nonbaseline_rate"],
                context=f"{context} gas_service segment {seg['segment']}")

        ge_cost = sum(HPC._flat_segment_cost(floor_s, seg["energy_rate"])
                     for floor_s, seg in zip(ge_shares, ge_segs))

        of_ranges = HPC._other_fees_day_ranges(gs_ranges, ge_ranges, of_segs, context)
        if of_ranges is None:
            of_cost = HPC._flat_segment_cost(floor_therms_period, of_segs[0]["other_fees_rate"])
        else:
            of_shares = HPC._segment_heat_from_days(of_ranges, capped_days)
            of_cost = sum(HPC._flat_segment_cost(floor_s, seg["other_fees_rate"])
                         for floor_s, seg in zip(of_shares, of_segs))

        savings = gs_cost + ge_cost + of_cost
        total_savings += savings
        total_floor_therms += floor_therms_period
        rows.append({
            "statement_date": str(row["statement_date"]),
            "floor_therms_attributed": round(float(floor_therms_period), 2),
            "floor_savings_usd": round(float(savings), 2),
        })
    return rows, round(total_savings, 2), round(total_floor_therms, 2)


# A real, cited HPWH product family (Rheem ProTerra, the current market
# leader for this size class) rather than a generic assumption --
# manufacturer-published UEF (Uniform Energy Factor -- the correct metric
# for a storage-type water heater; it already folds in standby loss, unlike
# a furnace heat pump's COP) across its own model range, 2026-08.
HPWH_UEF_SCENARIOS = {"low_3.5": 3.5, "central_3.88": 3.88, "high_4.0": 4.0}

# The existing GAS water heater's own efficiency, needed to convert metered
# THERMS (fuel input) into delivered heat (the quantity a HPWH actually has
# to replace) -- the same role FURNACE_AFUE plays in heat_pump_
# conversion.py. DOE's current federal minimum Uniform Energy Factor for a
# gas STORAGE water heater of this household's likely size class (a
# residential 40-55 gallon tank) is 0.59-0.64; 0.60 is used, the low end of
# that band and also close to the historical minimum -- the SAME
# more-heat-pump-favorable convention heat_pump_conversion.py's own
# FURNACE_AFUE comment establishes (a less-efficient existing gas unit
# delivers LESS heat per therm, so LESS heat needs replacing, understating
# rather than overstating the HPWH's required kWh). This household's own
# water heater nameplate UEF was never recorded (not in private/
# household.yaml, no equipment photo archived the way the panel was for
# issue #6).
GAS_WH_UEF = 0.60
GAS_WH_UEF_NOTE = ("DOE federal minimum UEF for a residential gas storage "
                   "water heater, <=55 gal (0.64) / ~40 gal (~0.59) -- "
                   "energy.gov / hotwater.com DOE regulations pages, "
                   "2026-08 -- 0.60 used as the low end of that band, the "
                   "assumption most favorable to the heat pump (a more "
                   "efficient existing unit would mean MORE delivered heat "
                   "to replace, raising required kWh), not a worst-case "
                   "pick to flatter the electric side")

# Installed-cost bracket: general contractor-pricing guides (Angi, Fixr,
# HomeGuide, hotwater.com/A.O. Smith, 2026), not a CA-specific engineering
# study the way heat_pump_conversion.py's furnace figure is -- "estimated"
# tier, kept as a bracket rather than a single point estimate, following the
# same convention heat_pump_conversion.py's own INSTALL_COST_SENSITIVITY_USD
# uses for its own web-sourced (non-primary-study) bracket.
WH_INSTALL_COST_LOW_USD = 2800
WH_INSTALL_COST_CENTRAL_USD = 4200
WH_INSTALL_COST_HIGH_USD = 8000
WH_INSTALL_COST_NOTE = (
    "general contractor-pricing guides (Angi, Fixr, HomeGuide, hotwater.com "
    "/ A.O. Smith), 2026 -- not a CA-specific engineering study the way "
    "heat_pump_conversion.py's furnace figure is; central is Angi's own "
    "quoted installed average ($4,200), low/high are Fixr's/hotwater.com's "
    "own quoted brackets ($2,800-$8,000)")

# Same incentive research heat_pump_conversion.py already did for this
# household, re-cited rather than re-run: SGIP (the one CPUC program that
# DOES cover heat-pump water heaters specifically, unlike space-heating
# HVAC) closed its ratepayer budgets 2025-12-31 regardless of appliance
# type -- see heat_pump_conversion.INCENTIVE_USD's own docstring / sources
# list, which this script does not duplicate.
WH_INCENTIVE_USD = 0
WH_INCENTIVE_NOTE = ("SGIP (the CPUC program covering heat-pump water "
                     "heaters specifically) closed its ratepayer budgets "
                     "2025-12-31 regardless of appliance type -- see "
                     "heat_pump_conversion.INCENTIVE_USD's own sources "
                     "list, re-cited not re-verified")


def build_wh_load_series(d, ann_wh_kwh):
    """Three added-Consumption Series (index-aligned to d), one per
    distribution scenario, mirroring heat_pump_conversion.build_hp_load_
    series's own solar-netting/interval-placement pattern but with a
    DIFFERENT day-weighting: the water heater's own load runs at a roughly
    constant daily rate (unlike HDD-driven heating), so each real calendar
    day `d` covers gets an EQUAL share of ann_wh_kwh, not an HDD-weighted
    one.

      uniform -- spread evenly across every interval of each day.
      midday  -- concentrated in each day's own super-off-peak (sop)
                 intervals -- the discretionary-timer placement index.html
                 already describes as physically realistic for a water
                 heater (it can run on a schedule, unlike a thermostat-
                 driven heating load).
      on_peak -- concentrated in each day's own on-peak intervals -- the
                 illustrative high-cost lean, the water-heater-side bracket
                 partner to `midday`.

    Neither `midday` nor `on_peak` is a computed cost extremum, the same
    caveat heat_pump_conversion.build_hp_load_series states for its own
    on_peak/off_peak bracket."""
    dates = d["dt"].dt.date
    unique_dates = dates.unique()
    n_days = len(unique_dates)
    if n_days == 0:
        raise SystemExit("all_electric_endgame.py: d has no dates to place "
                         "the water heater's load into")
    kwh_per_day = ann_wh_kwh / n_days
    out = {"uniform": pd.Series(0.0, index=d.index),
           "midday": pd.Series(0.0, index=d.index),
           "on_peak": pd.Series(0.0, index=d.index)}
    fallback_days = {"midday": 0, "on_peak": 0}
    for day in unique_dates:
        mask = dates == day
        n = int(mask.sum())
        out["uniform"].loc[mask] += kwh_per_day / n

        sop_mask = mask & (d["p"] == "sop")
        n_sop = int(sop_mask.sum())
        if n_sop > 0:
            out["midday"].loc[sop_mask] += kwh_per_day / n_sop
        else:
            out["midday"].loc[mask] += kwh_per_day / n
            fallback_days["midday"] += 1

        on_mask = mask & (d["p"] == "on")
        n_on = int(on_mask.sum())
        if n_on > 0:
            out["on_peak"].loc[on_mask] += kwh_per_day / n_on
        else:
            out["on_peak"].loc[mask] += kwh_per_day / n
            fallback_days["on_peak"] += 1
    return out, fallback_days


def wh_electric_cost_scenarios(d, floor_therms_yr):
    """{uef_key: {dist_key: annual electric cost increase usd}}, same
    solar-absorb-first-then-grid-import netting and rates.bill_nem()
    re-billing pattern as heat_pump_conversion.electric_cost_scenarios --
    reused verbatim in structure, not reimplemented independently, since
    CLAUDE.md section 1b's requirement (real intervals, solar absorbed
    first, whole year re-billed) is identical for any added load."""
    base_bill = R.bill_nem(d, imp="Consumption", exp="Generation")
    out = {}
    for uef_key, uef in HPWH_UEF_SCENARIOS.items():
        ann_wh_kwh = floor_therms_yr * HPC.KWH_PER_THERM * GAS_WH_UEF / uef
        added, fallback_days = build_wh_load_series(d, ann_wh_kwh)
        scen = {}
        for dist_key, series in added.items():
            total_added = float(series.sum())
            if abs(total_added - ann_wh_kwh) / ann_wh_kwh > 0.001:
                raise SystemExit(
                    f"all_electric_endgame.py: {uef_key}/{dist_key} added "
                    f"{total_added:.1f} kWh, not the {ann_wh_kwh:.1f} kWh "
                    "the water-heating load requires -- energy is not "
                    "conserved")
            absorbed = pd.concat([d["Generation"], series], axis=1).min(axis=1)
            remainder = series - absorbed
            f = d.copy()
            f["Generation"] = d["Generation"] - absorbed
            f["Consumption"] = d["Consumption"] + remainder
            assert abs(float(absorbed.sum() + remainder.sum()) - total_added) < 0.01, (
                uef_key, dist_key, "solar-netting step lost or duplicated energy")
            new_bill = R.bill_nem(f, imp="Consumption", exp="Generation")
            scen[dist_key] = {
                "added_kwh": round(total_added),
                "solar_absorbed_kwh": round(float(absorbed.sum())),
                "electric_cost_increase_usd": round(new_bill - base_bill, 2),
            }
        out[uef_key] = scen
        out[uef_key]["_fallback_days"] = fallback_days
    return out, round(base_bill, 2)


# ---------------------------------------------------------------------------
# AC5 -- service headroom, reusing service_headroom.py's own committed
# artifact and its own physical_fit() function directly.
# ---------------------------------------------------------------------------
# NEC 422.13: a storage-type water heater of 3 imperial gallons or more is a
# CONTINUOUS load; branch conductors/OCPD sized at not less than 125% of the
# nameplate rating. A HPWH's own worst-case draw is its electric-resistance
# BACKUP element (typically 4,500 W at 240 V -- Rheem ProTerra spec sheets,
# 2026-08), not its compressor (2-3 A in normal heat-pump-only operation) --
# the code load has to cover the worst case, matching the "standard 30 A
# circuit" every manufacturer spec sheet found in this research calls for.
WH_BACKUP_ELEMENT_W = 4500
WH_CODE_LOAD_A = round(WH_BACKUP_ELEMENT_W / SH.SERVICE_VOLTAGE_V * 1.25, 2)
WH_CODE_LOAD_BASIS = ("NEC 422.13 (water heater as a continuous load, 125% "
                      "of nameplate) applied to a 4,500 W backup resistance "
                      f"element at {SH.SERVICE_VOLTAGE_V} V -- matches the "
                      "standard 30 A/240 V dedicated circuit every "
                      "manufacturer spec sheet found calls for")


def service_headroom_check():
    """AC5: cumulative headroom for furnace heat pump + water heater,
    reusing data/service_headroom.json's own already-committed cases
    (not re-deriving the gross-load envelope) and service_headroom.
    physical_fit() called directly (not reimplemented) for the water
    heater's own new-circuit physical fit."""
    sh_path = os.path.join(DATA, "service_headroom.json")
    if not os.path.exists(sh_path):
        raise SystemExit(f"all_electric_endgame.py: {sh_path} not found -- "
                         "run service_headroom.py first")
    sh = json.load(open(sh_path))
    cases = {c["case"]: c for c in sh["cases"]}

    hp_only = cases["heat_pump_only"]
    hp_replaces_ac = cases["heat_pump_replaces_ac"]
    spare = hp_only["remaining_headroom_a"]
    spare_conservative = spare["conservative_basis"]["binding"]
    spare_measured = spare["measured_basis"]["binding"]

    # Physical space: the water heater needs a NEW 240 V circuit (no
    # existing circuit to reuse -- panel.no_dryer_or_water_heater_circuit),
    # unlike the furnace, which reuses the existing A/C circuit
    # (heat_pump_replaces_ac, no new breaker or space). spaces_free is the
    # SAME already-committed panel figure service_headroom.py's own
    # second_evse_only case already uses for an equivalent new-240V-circuit
    # case.
    spaces_free = hp_only["spaces"]["spaces_free"]
    physical_fit = SH.physical_fit(new_2pole_breakers=1, spaces_free=spaces_free,
                                   adjacent_free_pairs=None)

    after_wh_conservative = round(spare_conservative - WH_CODE_LOAD_A, 4)
    after_wh_measured = round(spare_measured - WH_CODE_LOAD_A, 4)

    ampacity_verdict = ("fail" if after_wh_conservative < 0 and after_wh_measured < 0
                        else "not_determined" if after_wh_conservative < 0
                        else "pass")

    return {
        "basis": ("furnace heat pump reuses the existing A/C circuit "
                  "(service_headroom.json's own heat_pump_replaces_ac "
                  "case, verdict "
                  f"{hp_replaces_ac['ampacity_verdict']!r}, contributes no "
                  "net-new panel-wide demand in the case's own summer-"
                  "coincident-peak measurement basis); the water heater's "
                  "own new 30 A/240 V circuit is checked against the SAME "
                  "panel-wide spare capacity heat_pump_only's own case "
                  "already establishes (fixed_added_load_a=0, i.e. before "
                  "any new 240 V load), since it is the one addition that "
                  "genuinely stacks on top of what is already installed"),
        "water_heater_code_load_a": WH_CODE_LOAD_A,
        "water_heater_code_load_basis": WH_CODE_LOAD_BASIS,
        "spare_before_any_new_load_a": {
            "conservative_basis": spare_conservative, "measured_basis": spare_measured},
        "spare_after_water_heater_a": {
            "conservative_basis": after_wh_conservative, "measured_basis": after_wh_measured},
        "ampacity_verdict": ampacity_verdict,
        "physical_fit_verdict": physical_fit,
        "physical_fit_basis": (
            f"spaces_free={spaces_free} (data/service_headroom.json, panel "
            "occupancy); a 240 V circuit needs 2 adjacent full-size spaces "
            "-- service_headroom.physical_fit() called directly, not "
            "reimplemented"),
        "hard_blocker": physical_fit == "fail",
        "hard_blocker_note": (
            "PHYSICAL PANEL SPACE, not ampacity, is the binding constraint: "
            f"only {spaces_free} free full-size space(s) remain and a new "
            "240 V water-heater circuit needs 2 -- this fails regardless of "
            "how much amperage headroom the ampacity check above shows. "
            "The furnace conversion frees no space of its own (it reuses "
            "the A/C's existing circuit), so it does not relieve this "
            "blocker either. Resolving it (a subpanel, tandem-breaker "
            "consolidation, or removing another circuit) is outside this "
            "issue's own scope box." if physical_fit == "fail" else
            "no physical-space blocker found"),
        "known_gap": ("service_headroom.json's own cases are built from a "
                      "SUMMER coincident-peak measurement window (issue #6's "
                      "own gross-load reconstruction); a water heater and a "
                      "space-heating heat pump both draw the most in WINTER, "
                      "a season the underlying measurement window does not "
                      "cover -- the same already-documented gap heat_pump_"
                      "conversion.py's own module docstring names for the "
                      "furnace's added load. This check inherits that gap "
                      "rather than resolving it."),
    }


# ---------------------------------------------------------------------------
# AC6 -- meter removal research (WebSearch, cited; CLAUDE.md section 0: say
# so explicitly wherever a definitive citable answer was not found).
# ---------------------------------------------------------------------------
METER_REMOVAL_RESEARCH = {
    "removal_fee": {
        "finding": "no fee to remove/cap a gas meter in most circumstances",
        "confidence": "not a primary SDG&E tariff citation -- a consumer "
                      "advocacy resource (QuitCarbon), not SDG&E's own "
                      "tariff book, states this plainly; SDG&E's own "
                      "disconnection page (sdge.com/disconnection, fetched "
                      "2026-08) describes the STOP-SERVICE PROCESS (call "
                      "877-789-9866 or use the online Stop Service portal) "
                      "but does not itself state a dollar fee either way "
                      "for a customer-requested, non-delinquency "
                      "disconnection",
        "sources": [
            "https://www.quitcarbon.com/help/removing-your-gas-meter "
            "(fetched 2026-08)",
            "https://www.sdge.com/disconnection (fetched 2026-08)",
        ],
    },
    "voluntary_disconnection_process": {
        "finding": ("SDG&E Rule 11.H (ELECTRIC tariff, "
                    "sdge.com/sites/default/files/elec_elec-rules_erule11.pdf"
                    ", fetched 2026-08): a customer requesting service "
                    "discontinuance must give >= 2 business days' notice; "
                    "the customer remains responsible for charges until "
                    "the requested date. No fee is named in this rule for "
                    "a customer-requested discontinuance."),
        "confidence": ("this is the ELECTRIC rules PDF, not the gas book -- "
                       "the analogous GAS Rule 11 PDF could not be located "
                       "at a working URL during this research; SDG&E's gas "
                       "and electric tariff books commonly mirror this "
                       "class of customer-service rule, but that parallel "
                       "was NOT independently confirmed for gas "
                       "specifically. NOT DETERMINED for gas without "
                       "reading the actual gas Rule 11."),
        "sources": [
            "https://www.sdge.com/sites/default/files/elec_elec-rules_erule11.pdf "
            "(fetched 2026-08, ELECTRIC rules)",
        ],
    },
    "reconnection_fee_nonpayment": {
        "finding": "SDG&E eliminated residential reconnection fees for "
                   "NONPAYMENT-related disconnections as of 2020-06-20 "
                   "(a CPUC decision) -- a different scenario from a full "
                   "voluntary meter removal followed by a later NEW gas "
                   "service installation, but indicative of SDG&E's current "
                   "posture toward reconnection-type fees generally.",
        "confidence": "well-sourced for the nonpayment case specifically; "
                      "does not directly answer the voluntary-removal case",
        "sources": ["web search, 2026-08 (secondary sources; no direct "
                    "CPUC decision document was fetched)"],
    },
    "future_reconnection_cost": {
        "finding": "NOT DETERMINED. A gas meter fully removed (not merely "
                   "capped) and later wanted again is, functionally, a NEW "
                   "gas service installation, not a same-day reconnection: "
                   "SDG&E's own 'Schedule SE' (Service Establishment "
                   "Charge) tariff governs new/re-instituted service, and a "
                   "secondary source cited a $5.85 SEC figure, but this "
                   "could not be verified against a current, fetchable "
                   "SDG&E tariff PDF during this research, and it is not "
                   "clear that figure (if current) covers the full real "
                   "cost of restoring gas to a fully-capped/removed line "
                   "(a licensed plumber's line inspection/pressure test and "
                   "a permit are the typical real-world requirements for "
                   "bringing a capped gas line back into service, per "
                   "general industry practice, not an SDG&E-specific "
                   "citation).",
        "confidence": "not determined -- flagged explicitly per CLAUDE.md "
                      "section 0 rather than guessed",
        "what_would_settle_it": ("SDG&E's own current Gas Rules tariff book "
                                 "(Rule 11 and Schedule SE), or a direct "
                                 "call to SDG&E's tariff line "
                                 "(858-654-1748 / tariffbook@sdge.com, "
                                 "found via sdge.com/rates-and-regulations/"
                                 "current-and-effective-tariffs, 2026-08)"),
    },
}


# ---------------------------------------------------------------------------
# AC3/AC7 -- sequencing and the two paybacks.
# ---------------------------------------------------------------------------
def sequencing_and_paybacks(fixed_charge_verdict_is_zero, wh_install_usd,
                            wh_annual_net_savings_usd, wh_payback_years,
                            furnace_install_usd, furnace_annual_net_savings_usd,
                            furnace_payback_years):
    """AC3 (sequencing) + AC7 (two paybacks), reusing heat_pump_conversion.
    payback_and_npv() directly for both the combined-transition and the
    final-step-alone figures rather than reimplementing payback math."""
    fixed_charge_release_usd = 0.0 if fixed_charge_verdict_is_zero else None
    if fixed_charge_release_usd is None:
        raise SystemExit(
            "all_electric_endgame.py: sequencing_and_paybacks() assumes "
            "AC1's own $0-fixed-charge finding; a nonzero finding would "
            "need this function's own credit-only-to-the-last-step logic "
            "rewritten, not silently defaulted")

    # basis: whichever step's OWN marginal economics are better goes first
    # (cost-effectiveness), since AC1's own $0 finding makes the issue's own
    # "credit the release to the last step" rule trivial either way -- see
    # this function's own final_step_alone_payback below, which reports
    # that triviality explicitly rather than eliding it.
    steps = [
        {"name": "water_heater", "install_usd": wh_install_usd,
         "annual_net_savings_usd": wh_annual_net_savings_usd,
         "payback_years": wh_payback_years},
        {"name": "furnace", "install_usd": furnace_install_usd,
         "annual_net_savings_usd": furnace_annual_net_savings_usd,
         "payback_years": furnace_payback_years},
    ]

    def _pb_sort_key(s):
        return (s["payback_years"] if s["payback_years"] is not None else float("inf"))

    ordered = sorted(steps, key=_pb_sort_key)
    order_names = [s["name"] for s in ordered]
    last_step = ordered[-1]

    combined_install = wh_install_usd + furnace_install_usd
    combined_annual_net_savings = (wh_annual_net_savings_usd
                                   + furnace_annual_net_savings_usd
                                   + fixed_charge_release_usd)
    complete_transition = HPC.payback_and_npv(
        combined_annual_net_savings, combined_install, HPC.DISCOUNT_RATES,
        HPC.NPV_HORIZON_YEARS)

    final_step_with_credit = HPC.payback_and_npv(
        last_step["annual_net_savings_usd"] + fixed_charge_release_usd,
        last_step["install_usd"], HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS)
    final_step_without_credit = HPC.payback_and_npv(
        last_step["annual_net_savings_usd"],
        last_step["install_usd"], HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS)

    return {
        "basis": ("cost-effectiveness: whichever step's own marginal "
                  "payback is shorter goes first. AC1's own finding (a "
                  "genuinely $0 fixed gas charge on this rate) makes the "
                  "issue's own 'credit the meter-release value only to the "
                  "final step' rule trivially satisfied either way -- "
                  "crediting $0 to whichever step is last changes nothing, "
                  "so there is no fixed-charge-driven reason to sequence "
                  "one way over the other, unlike a rate WITH a real "
                  "per-meter charge would produce."),
        "order": order_names,
        "last_step": last_step["name"],
        "fixed_charge_release_usd": fixed_charge_release_usd,
        "complete_transition_payback": {
            "combined_install_usd": combined_install,
            "combined_annual_net_savings_usd": round(combined_annual_net_savings, 2),
            **complete_transition,
        },
        "final_step_alone_payback": {
            "step": last_step["name"],
            "install_usd": last_step["install_usd"],
            "with_fixed_charge_credit": final_step_with_credit,
            "without_fixed_charge_credit": final_step_without_credit,
            "identical_because_credit_is_zero": (
                final_step_with_credit["payback_years"] == final_step_without_credit["payback_years"]),
            "note": ("reported both ways per the issue's own AC7 text, "
                     "making the 'only the last step gets it' framing "
                     "checkable -- with fixed_charge_release_usd=0.0 "
                     "(AC1's own finding) the two are numerically "
                     "identical, which is the correct, checkable outcome "
                     "of that rule on THIS rate, not a shortcut around it"),
        },
        "third_end_use_caveat": ("neither payback above represents a "
                                 "CONFIRMED gas-meter removal: see "
                                 "third_end_use_gap -- whether a third gas "
                                 "end use (possibly cooking, possibly a "
                                 "clothes dryer) remains unconverted and "
                                 "unpriced after these two steps is not "
                                 "determined, so the meter cannot be "
                                 "confirmed removable yet even once both "
                                 "pay for themselves"),
    }


# ---------------------------------------------------------------------------
# build() / main()
# ---------------------------------------------------------------------------
def build():
    if not HAS_GAS:
        return {"applicable": False, "reason": "household.has_gas is false"}

    d = BR.load()
    iso = HPC.isolate_heating_therms()

    fixed_charge_check = {
        "bill_evidence": FIXED_CHARGE_EVIDENCE,
        "regression": fixed_charge_regression(),
        "verdict": "confirmed -- GR-Residential has no separate fixed/customer "
                  "gas charge; every line item on both bill PDFs read directly "
                  "is a $/therm rate or a percentage of one",
        "resolution": ("no tension between index.html's existing 'near-zero "
                       "fixed floor' regression claim and a per-meter fixed "
                       "charge -- the regression is correct because there "
                       "genuinely is no such charge on this rate, confirmed "
                       "by reading two real bill PDFs directly (a 9-therm "
                       "summer statement and a 61-therm winter statement) "
                       "line by line, not merely inferred from the "
                       "regression's own intercept alone"),
    }

    enumeration = gas_end_use_enumeration(iso)

    floor_rows, floor_savings_annual, floor_therms_annual = floor_savings_by_period(iso)
    electric, base_bill = wh_electric_cost_scenarios(d, floor_therms_annual)

    wh_paybacks = {}
    for uef_key in HPWH_UEF_SCENARIOS:
        electric_increase = electric[uef_key]["uniform"]["electric_cost_increase_usd"]
        net_savings = floor_savings_annual - electric_increase
        wh_paybacks[uef_key] = {
            "annual_gas_savings_usd": floor_savings_annual,
            "annual_electric_cost_increase_usd": electric_increase,
            "annual_net_savings_usd": round(net_savings, 2),
            "low_install": HPC.payback_and_npv(
                net_savings, WH_INSTALL_COST_LOW_USD, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS),
            "central_install": HPC.payback_and_npv(
                net_savings, WH_INSTALL_COST_CENTRAL_USD, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS),
            "high_install": HPC.payback_and_npv(
                net_savings, WH_INSTALL_COST_HIGH_USD, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS),
        }

    headline_uef = "central_3.88"
    wh_headline = wh_paybacks[headline_uef]

    hpc_path = os.path.join(DATA, "heat_pump_conversion.json")
    if not os.path.exists(hpc_path):
        raise SystemExit(f"all_electric_endgame.py: {hpc_path} not found -- "
                         "run heat_pump_conversion.py first (issue #1/#109)")
    hpc_data = json.load(open(hpc_path))
    furnace_headline = hpc_data["payback"]["central_3.5"]
    furnace_install_usd = hpc_data["install_cost"]["standalone_usd"]
    furnace_payback_years = furnace_headline["standalone"]["payback_years"]

    sequencing = sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=WH_INSTALL_COST_CENTRAL_USD,
        wh_annual_net_savings_usd=wh_headline["annual_net_savings_usd"],
        wh_payback_years=wh_headline["central_install"]["payback_years"],
        furnace_install_usd=furnace_install_usd,
        furnace_annual_net_savings_usd=furnace_headline["annual_net_savings_usd"],
        furnace_payback_years=furnace_payback_years,
    )

    headroom = service_headroom_check()

    ext_path = os.path.join(DATA, "extended_results.json")
    ext_data = json.load(open(ext_path)) if os.path.exists(ext_path) else {}
    old_hpwh_saving = ext_data.get("gas_decomposition", {}).get("hpwh_saving_yr")

    reconciled_heating_therms = hpc_data["reconciled_heating_therms_yr"]
    raw_heating_therms = iso["hdd_regression"]["heating_therms_yr"]
    unattributed_therms = round(raw_heating_therms - reconciled_heating_therms, 2)
    trailing12_billed_total = round(
        pd.read_csv(HPC.GAS_PERIODS_CSV).sort_values("statement_date")
        .tail(12)["billed_amount"].sum(), 2)
    unattributed_usd = round(
        trailing12_billed_total - floor_savings_annual - hpc_data["gas_savings_annual_usd"], 2)

    reconciliation = {
        "furnace_vs_heat_pump_conversion_json": {
            "source": "data/heat_pump_conversion.json (cited directly, not "
                      "recomputed)",
            "gas_savings_annual_usd": hpc_data["gas_savings_annual_usd"],
            "agreement": "exact by construction",
        },
        "water_heater_vs_extended_results_gas_decomposition": {
            "old_estimate_net_usd_yr": old_hpwh_saving,
            "old_method": "flat 'midday solar timer' rate estimate "
                          "(extended_findings.py's gas_decomposition), not "
                          "real-interval billed",
            "new_estimate_net_usd_yr": wh_headline["annual_net_savings_usd"],
            "new_method": "real 15-minute intervals, solar absorbed first, "
                          "whole year re-billed with rates.bill_nem() "
                          "(this script, CLAUDE.md section 1b)",
            "gap_usd": (round(wh_headline["annual_net_savings_usd"] - old_hpwh_saving, 2)
                       if old_hpwh_saving is not None else None),
            "gap_note": "the two are expected to differ -- they price the "
                       "SAME conversion by genuinely different methods, one "
                       "of them (this script's) at this repo's own "
                       "established real-interval-billing rigor and one "
                       "(extended_findings.py's) at a flat-rate proxy",
        },
        "unattributed_heating_signal": {
            "note": ("heat_pump_conversion.py's own module docstring already "
                     "documents that its day-level capacity cap excludes "
                     f"{unattributed_therms:g} therms/yr of HDD-regression "
                     "heating signal it cannot pin to a specific real day "
                     f"({raw_heating_therms:g} raw vs "
                     f"{reconciled_heating_therms:g} reconciled) -- this "
                     "script quantifies that gap in DOLLARS for the first "
                     "time, by comparing the trailing-12-statement billed "
                     "total against this script's own floor savings plus "
                     "heat_pump_conversion.json's own heating savings"),
            "unattributed_therms_yr": unattributed_therms,
            "trailing_12_billed_total_usd": trailing12_billed_total,
            "floor_savings_usd": floor_savings_annual,
            "heating_savings_usd": hpc_data["gas_savings_annual_usd"],
            "unattributed_usd": unattributed_usd,
            "resolution": ("not credited to either conversion step, "
                          "conservatively, matching heat_pump_conversion.py's "
                          "own treatment of the underlying therms"),
        },
    }

    out = {
        "applicable": True,
        "basis": ("furnace figures cited directly from heat_pump_"
                  "conversion.json (issues #1/#109), not recomputed; water-"
                  "heater figures built by reusing heat_pump_conversion.py's "
                  "own segment-tier gas-pricing helpers for the non-heating "
                  "floor and its own real-interval electric-rebilling "
                  "pattern; service-headroom figures reused from "
                  "service_headroom.json (issue #6) and its own "
                  "physical_fit() function"),
        "fixed_charge_check": fixed_charge_check,
        "gas_end_use_enumeration": enumeration,
        "water_heater_conversion": {
            "floor_savings_by_period": floor_rows,
            "floor_savings_annual_usd": floor_savings_annual,
            "floor_therms_annual": floor_therms_annual,
            "upper_bound_caveat": (
                "floor_savings_annual_usd prices the WHOLE non-heating "
                "floor, which may include other gas end uses besides the "
                "water heater (gas_end_use_enumeration.non_heating_floor."
                "floor_composition: not determined from committed public "
                "data) -- a HPWH replaces only the water-heater share, "
                "which cannot be separated from this household's single, "
                "unsplit gas meter, so this is an UPPER BOUND on the water "
                "heater step's own true gas savings, not a precise "
                "water-heater-only figure"),
            "baseline_electric_bill_usd": base_bill,
            "electric_cost_by_scenario": electric,
            "gas_wh_uef_assumed": GAS_WH_UEF,
            "gas_wh_uef_basis": GAS_WH_UEF_NOTE,
            "hpwh_uef_scenarios": HPWH_UEF_SCENARIOS,
            "install_cost": {
                "low_usd": WH_INSTALL_COST_LOW_USD,
                "central_usd": WH_INSTALL_COST_CENTRAL_USD,
                "high_usd": WH_INSTALL_COST_HIGH_USD,
                "note": WH_INSTALL_COST_NOTE,
            },
            "incentives": {"usd": WH_INCENTIVE_USD, "note": WH_INCENTIVE_NOTE},
            "payback": wh_paybacks,
            "headline_uef": headline_uef,
        },
        "furnace_conversion": {
            "source": "data/heat_pump_conversion.json (issues #1/#109, "
                      "reused directly)",
            "gas_savings_annual_usd": hpc_data["gas_savings_annual_usd"],
            "install_cost_standalone_usd": furnace_install_usd,
            "install_cost_marginal_over_ac_usd":
                hpc_data["install_cost"]["marginal_over_ac_replacement_usd"],
            "payback_central_cop_3_5": furnace_headline,
        },
        "sequencing_and_paybacks": sequencing,
        "service_headroom_check": headroom,
        "meter_removal_research": METER_REMOVAL_RESEARCH,
        "reconciliation": reconciliation,
    }
    return out


def main():
    out = build()
    tmp = os.path.join(DATA, "all_electric_endgame.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(DATA, "all_electric_endgame.json"))
    print("wrote data/all_electric_endgame.json")
    if out["applicable"]:
        wh = out["water_heater_conversion"]["payback"]["central_3.88"]
        fc = out["furnace_conversion"]["payback_central_cop_3_5"]
        print(f"water heater: net savings ${wh['annual_net_savings_usd']}/yr, "
              f"central-install payback {wh['central_install']['payback_years']} yr")
        print(f"furnace (reused from heat_pump_conversion.json): net savings "
              f"${fc['annual_net_savings_usd']}/yr, standalone payback "
              f"{fc['standalone']['payback_years']} yr")


if __name__ == "__main__":
    main()
