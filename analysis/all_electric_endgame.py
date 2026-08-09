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
separate fixed/customer charge, confirmed by the bill PDFs directly and by the regression.
No correction here is needed to index.html's existing sentence.

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
`_other_fees_day_ranges`, `_segment_heat_from_days`, `load_gas_detail`, and --
post-adversarial-review -- `_gas_service_segment_tier_cost` ITSELF, called directly rather
than reimplemented) rather than reimplementing them. The dollar savings from removing any
quantity of gas usage is a WHOLE-BILL delta, `bill(T) - bill(T-X)`, which is always exactly
the cost of the TOP X therms of the ORIGINAL total under a convex (tiered, non-decreasing
marginal rate) schedule -- true regardless of which appliance the removed X therms are
conceptually attributed to. So the water heater's OWN removal (X = the floor, with heating
staying behind) is marginal/top-of-ladder to THIS removal, exactly mirroring how heating's
own removal (furnace conversion, floor staying behind) is marginal/top-of-ladder to heat_pump_
conversion.py's own computation -- the SAME function, `_gas_service_segment_tier_cost()`,
correctly prices both, called with whichever quantity is being removed in its own
`heating_therms` argument. (A first version of this module got this backwards, pricing the
floor at the tier ladder's cheap end on the reasoning that "always-on usage occupies the cheap
tier first" -- a real, confirmed bug, caught by adversarial review with a concrete
counterexample and fixed; see the fuller account at `_floor_segment_total_therms`'s own
docstring, just above `floor_savings_by_period()`.) Restricted to the same trailing-12-real-
bill window `heat_pump_conversion.py`'s own $416.25/yr already resolves to (see that module's
own gas.csv coverage window), so the two figures are on the same annual basis.

Therms do not double-count between the two conversions even when both are considered:
heating's own day-level capacity cap already reserves the floor's share of every real day
BEFORE computing heating capacity (`heat_pump_conversion._capacity_capped_days`: `capacity =
real_day_therms - floor_per_day`), so floor-day + heating-day <= real-day-therms by
construction. DOLLARS are a different matter once BOTH conversions are considered together
(`sequencing_and_paybacks.complete_transition_payback`): each conversion's own gas savings is
computed as if it ALONE were being removed from the SAME original total (the correct basis for
"marginal economics per step," which is what AC3 itself asks for), and summing two
independently-computed marginal savings OVERSTATES the TRUE joint removal savings whenever
both reach into the same segment's nonbaseline tier, because the shared top-of-ladder region
gets priced twice. `complete_transition_payback` quantifies and discloses this gap explicitly
(`tier_interaction_overstatement_usd`) rather than silently presenting the summed figure as
exact -- see that field's own note for the real-data magnitude.

The SAME non-additivity problem exists on the ELECTRIC side, independently of the gas-side
fix above, and was missed by the first version of this correction (Codex adversarial review,
issue #20 round 2, Finding 1): `wh_electric_cost_scenarios()` and `heat_pump_conversion.
electric_cost_scenarios()` each net their OWN added load against the household's FULL solar
Generation before billing, so two independently-computed electric-cost increases both let
their own conversion claim first dibs on the SAME exported kWh in the SAME interval, which
cannot really happen once both loads are real -- `rates.bill_nem()`'s own per-(month, TOU
bucket) netting is not additive across two separately-billed scenarios either. Fixed by
`joint_electric_cost_scenario()`: the furnace's and the water heater's own added-load series
(each built by the SAME functions their independent scenarios already use, `heat_pump_
conversion.build_hp_load_series` / `build_wh_load_series`, not reimplemented) are SUMMED into
one combined series, netted against solar ONCE, and re-billed ONCE -- the correct joint
electric-cost delta for the complete-transition scenario. `electric_interaction_
overstatement()` quantifies the gap between that joint figure and the naive sum of the two
independent figures, exactly mirroring `tier_interaction_overstatement()`'s own role on the
gas side, and `complete_transition_payback` nets BOTH corrections out of its own headline
combined savings, not just the gas one.

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
requirement because it no longer bites. `complete_transition_payback`'s own combined savings
correct TWO interactions (Codex adversarial review, issue #20 rounds 1 and 2, both a direct
consequence of the Finding-1 pricing fix): summing each step's own INDEPENDENTLY-computed
marginal GAS savings overstates the true joint savings whenever both reach into the same
period's nonbaseline tier, since that shared marginal region gets priced twice (`tier_
interaction_overstatement()`); summing each step's own INDEPENDENTLY-rebilled ELECTRIC cost
increase similarly understates the true joint electric cost, since both independent rebills
let their own added load claim the SAME exported solar kWh (`electric_interaction_
overstatement()`, backed by one joint rebill of the combined added-load series, `joint_
electric_cost_scenario()`). Both corrections are quantified and netted out of the headline
combined savings, never left silently in it (CLAUDE.md section 9). Every water-heater-derived
payback in this section (this one and `final_step_alone_payback` when the water heater is
last) is the PURE 100%-floor-is-water-heater basis and carries a `not_verified_caveat`
pointing at `water_heater_share_sensitivity` (Finding 2, above) rather than presenting itself
as settled.

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
    # A present-but-non-null answer is NOT the same as a resolved-favorably
    # answer: appliance_fuels is unstructured free text (DATA-SOURCES-
    # CHEATSHEET.md's own format hint is "cooking ____", not a fixed enum),
    # and an answer of e.g. "cooking: gas" would mean a third gas end use
    # DOES remain -- the opposite of resolved. This script deliberately does
    # NOT parse free text into a gas/electric verdict (auto-parsing nuance
    # like "gas cooktop but electric oven" risks silently misreading it,
    # which is itself a CLAUDE.md section 0 "never guess" violation), so
    # "answered_but_not_interpreted" is a real third state distinct from
    # both "not determined" and any claim of resolution -- see
    # third_end_use_gap below, which must not collapse this state into
    # "resolved" either (Codex adversarial review, issue #20).
    return {
        "appliance_fuels_field_present": appliance_fuels is not None,
        "verdict": ("answered_but_not_interpreted" if appliance_fuels is not None
                   else "not determined"),
        "note": (
            "household.appliance_fuels was answered directly at intake, but "
            "this script does not parse free text into a gas/electric "
            "verdict -- read the field's own recorded value directly to "
            "determine this household's actual fuel mix; presence of an "
            "answer alone settles nothing about the third-end-use gap."
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

# A second national, uncited-to-this-household benchmark, same "estimated"
# tier and same evidentiary standard as DRYER_THERMS_PER_MONTH_RANGE above
# (Finding 2, Codex adversarial review, issue #20 round 2): typical gas
# range/cooktop annual usage, used ONLY to size water_heater_share_
# sensitivity()'s own benchmark_incompatibility_check below, never to
# assert this household
# cooks with gas. Two real figures found: a specific annual figure for a
# typical 4-burner gas range (40-60 therms/yr -- chefsresource.com "How
# Many Therms Does a Gas Stove Use?" and prodhut.com, both 2026) and a
# broader monthly-usage-pattern estimate (3-10 therms/month = 36-120
# therms/yr -- learnmetrics.com, 2026) that corroborates the same order of
# magnitude from an independent estimation method. The narrower, more
# directly-annual figure (40-60) is used, since it is the more specific,
# directly-cited claim rather than one requiring an extra "how many hours a
# day" assumption on top.
COOKING_THERMS_YR_RANGE = (40.0, 60.0)
COOKING_THERMS_YR_BASIS = (
    "typical 4-burner gas range/cooktop annual usage -- chefsresource.com "
    "('How Many Therms Does a Gas Stove Use?') and prodhut.com, both 2026, "
    "corroborated in order of magnitude by a separate monthly-usage-pattern "
    "estimate (learnmetrics.com, 3-10 therms/month = 36-120 therms/yr, "
    "2026); consumer/contractor-guide tier evidence, the SAME evidentiary "
    "standard DRYER_THERMS_PER_MONTH_RANGE above already uses, not a "
    "primary utility or DOE study -- not measured for this household and "
    "not evidence that cooking here is gas")


def third_end_use_gap(floor_therms_yr, cooking_fuel):
    """AC2/AC6: whether a gas end use besides the water heater (cooking
    and/or a clothes dryer) might remain unconverted after this issue's own
    two named steps -- reported CONDITIONALLY, since household.
    appliance_fuels (the one public-ok source for this) is unanswered and
    this script does not infer a fuel mix from private panel data (see
    cooking_fuel_evidence's own docstring)."""
    lo, hi = DRYER_THERMS_PER_MONTH_RANGE
    lo_yr, hi_yr = round(lo * 12), round(hi * 12)
    lo_c, hi_c = COOKING_THERMS_YR_RANGE
    lo_c_yr, hi_c_yr = round(lo_c), round(hi_c)
    # NOTE: field_answered is presence-only, not resolution. appliance_fuels
    # is unstructured free text -- an answer of "cooking: gas" would mean a
    # third gas end use DOES remain, the opposite of resolved-in-the-
    # favorable-direction. Mere presence must never be reported as
    # "resolved"; only a human reading the field's own recorded text can
    # settle this (Codex adversarial review, issue #20 -- a prior version of
    # this branch said "resolved" from presence alone, which would have
    # silently declared the gap closed on a household whose own answer said
    # cooking runs on gas).
    field_answered = cooking_fuel["appliance_fuels_field_present"]
    return {
        "gap": ("NOT DETERMINED whether a third gas end use (cooking, and/or "
                "a clothes dryer) remains unconverted after the water-heater "
                "and furnace steps this issue's own body names -- "
                "household.appliance_fuels, the one public-ok source for "
                "this household's own fuel mix, was never answered. If "
                "either is gas, the meter cannot actually be removed after "
                "just those two conversions." if not field_answered else
                "household.appliance_fuels has been answered -- read its "
                "own recorded text directly to determine whether cooking or "
                "a dryer here run on gas; this script does not parse free "
                "text into a verdict, so it cannot itself declare the gap "
                "closed."),
        "possible_dryer_rough_magnitude_therms_yr": [lo_yr, hi_yr],
        "possible_dryer_pct_of_floor_range": [round(100 * lo_yr / floor_therms_yr, 1),
                                              round(100 * hi_yr / floor_therms_yr, 1)],
        "possible_cooking_rough_magnitude_therms_yr": [lo_c_yr, hi_c_yr],
        "possible_cooking_pct_of_floor_range": [round(100 * lo_c_yr / floor_therms_yr, 1),
                                                round(100 * hi_c_yr / floor_therms_yr, 1)],
        "possible_cooking_basis": COOKING_THERMS_YR_BASIS,
        "basis": ("external benchmark (HomeGuide/SlashPlan-class contractor "
                  "and DOE-adjacent usage guides, 2026), not measured for "
                  "this household and not evidence a dryer exists here -- "
                  "sizes the possibility only, for the case where it does. "
                  f"Not separable from other shares of the {floor_therms_yr:g}"
                  "-therm/yr floor with the single, unsplit gas meter this "
                  "household has. The cooking benchmark above sizes a "
                  "SECOND, independent possibility the same way -- both can "
                  "in principle draw on the SAME floor at once, see "
                  "water_heater_share_sensitivity's own "
                  "benchmark_incompatibility_check."),
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
    days read BELOW floor_per_day (0.376 therms/day), all 143 of them at
    exactly 0.00 (the dataset is bimodal at this resolution -- every real
    day reads either 0.00 or >= 1.014 therms, with no partial values in
    between) -- ordinary meter-read-date batching noise (heat_pump_
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


# --- Corrected per Codex adversarial review (round 1 of issue #20's own
# review loop): floor removal must be priced at the TOP of each Gas Service
# segment's tier ladder, not the bottom. ---
#
# The dollar SAVINGS from removing any quantity X of usage from a bill is
# bill(T) - bill(T-X): a whole-bill delta, not an itemized "this appliance's
# own conceptual slice of the ladder" attribution. Because SDG&E's tiered
# rate is convex (the marginal $/therm is non-decreasing in total usage),
# bill(T) - bill(T-X) is ALWAYS exactly the cost of the TOP X therms of the
# ORIGINAL total T -- this is a general mathematical fact about savings from
# a reduction under an increasing-marginal-cost schedule, true regardless of
# which appliance the removed X therms are conceptually attributed to. It
# does not matter that the floor is "always-on, baseline-tier-shaped"
# usage; what matters is that the floor is the quantity being REMOVED here
# (the water heater converts, the rest of the bill's usage -- heating, and
# anything else -- stays), so it is the floor that is marginal to this
# removal, exactly mirroring heat_pump_conversion.py's own furnace-removal
# case (there, heating is what's removed, so heating is marginal and floor
# stays behind at the bottom).
#
# A first version of this module got this backwards -- see its own removed
# _floor_segment_tier_cost(), which priced the floor at the CHEAP end,
# reasoning (wrongly) that "always-on usage occupies the cheap tier first."
# Caught by adversarial review with a concrete counterexample: a 60-therm
# period, 20-therm baseline allowance, $2.00/$2.38 rates, removing 11 floor
# therms. True whole-bill savings (bill(60) - bill(49)): 11 x $2.38 =
# $26.18, since the period never drops below the baseline allowance and so
# every removed therm was marginal (nonbaseline). The bottom-of-ladder
# method gave 11 x $2.00 = $22.00 -- a 16% understatement, reproduced
# exactly by test_all_electric_endgame.py's own
# case_priced_at_top_of_ladder_matches_reviewers_hand_worked_example.
#
# Fixed by reusing heat_pump_conversion._gas_service_segment_tier_cost()
# directly, passing floor_therms as ITS `heating_therms` argument (the
# marginal/top-of-ladder role) -- not reimplementing a parallel function,
# since the correct arithmetic already exists and this is exactly the
# reuse heat_pump_conversion.py's own docstring warned against for the
# WRONG reason (it assumed the floor's role was fixed as "bottom," when in
# fact which end-use is marginal depends on which one is being removed).


def _floor_segment_total_therms(seg_start, seg_end, period_therms, period_days, floor_s, context):
    """Segment total therms (t_s), the `total_therms` argument _gas_service_
    segment_tier_cost() needs to place the floor's own removal on the
    correct rung of the ladder. Always day-proportion (gas_daily=None):
    heat_pump_conversion._segment_real_or_proxy_therms()'s own real-daily-
    data path fails closed whenever the quantity being priced (its `heat_s`
    argument) is nonzero and gas.csv doesn't cover every day of the segment
    -- true for heating only outside gas.csv's own coverage window (HDD is
    exactly zero there by construction), but the floor is NEVER exactly
    zero on any real day, so that same fail-closed path would fire on the
    one trailing period whose early days precede gas.csv's coverage start
    (2025-07-30's own period begins 2025-06-27, 28 days before gas.csv
    starts). Day-proportion is the same estimate heat_pump_conversion.py's
    own JUDGMENT CALL paragraph already uses for baseline_allowance (no
    better alternative exists for a tariff ENTITLEMENT either), reused here
    for the segment total rather than inventing a second convention."""
    return HPC._segment_real_or_proxy_therms(
        seg_start, seg_end, period_therms, period_days, gas_daily=None,
        heat_s=floor_s, context=context)


# heat_pump_conversion._gas_service_segment_tier_cost()'s own 1e-6 fail-
# closed tolerance is appropriate for HEATING, whose own total_therms/
# heating_therms inputs are capacity-capped against REAL per-day meter
# data (_capacity_capped_days, gas_daily) -- genuinely tight. The floor's
# own total_therms (_floor_segment_total_therms, above) and baseline_
# allowance are BOTH day-proportion ESTIMATES with no real-daily
# counterpart (see that function's own docstring), so noise at this scale
# is expected, not a sign of a real problem, and 1e-6 fires on it
# constantly. Checked on this household's real corpus, restricted to the
# trailing-12-statement window this tolerance actually gets exercised
# against (floor_savings_by_period()'s own default n_trailing=12, the SAME
# window every caller of _priced_at_top_of_ladder() uses): the largest real
# overflow is 0.46875 therms, on the 2025-10-29 period's Gas Service
# segment 0 (day-proportion segment total ~2.19 therms against a
# ~1.72-therm allowance share) -- 93.75% of this 0.5-therm tolerance, a
# genuinely thin margin (0.03 therms of headroom), not a comfortable one.
# Widening the tolerance further to buy back margin would weaken the
# fail-closed check's own ability to catch a real methodology problem, and
# this repo's own evidentiary standard (CLAUDE.md section 0) does not
# support inventing a new number with no real-corpus grounding either --
# 0.5 is kept, one occurrence at 93.75% of it is treated as a real but
# isolated data point worth flagging honestly, not evidence the tolerance
# itself is wrong. If a FUTURE billing-period update pushes a real overflow
# past 0.5, this check is designed to fire, and that data point (not this
# comment) is the thing to re-examine first.
FLOOR_ESTIMATION_TOLERANCE_THERMS = 0.5


def _priced_at_top_of_ladder(total_therms, marginal_therms, baseline_allowance,
                             baseline_rate, nonbaseline_rate, context):
    """Thin tolerance wrapper around heat_pump_conversion._gas_service_
    segment_tier_cost(), REUSED not reimplemented (Codex adversarial
    review, issue #20 round 1): prices `marginal_therms` (the floor, being
    removed by this conversion) at the TOP of the segment's tier ladder,
    exactly like heating's own removal in heat_pump_conversion.py.

    The only added logic: when the segment's own real bill never printed a
    nonbaseline_rate at all (it never crossed into that tier), and the
    day-proportion estimates above would place a SMALL amount of
    marginal_therms there anyway (within FLOOR_ESTIMATION_TOLERANCE_THERMS
    -- estimation noise, not a real crossing the bill itself contradicts),
    the bill's own evidence wins and the full marginal_therms prices at the
    baseline rate. A LARGE apparent overflow still reaches HPC's own
    function unchanged, which fails closed on it exactly as it does for
    heating."""
    if nonbaseline_rate is None or pd.isna(nonbaseline_rate):
        non_marginal = max(0.0, total_therms - marginal_therms)
        baseline_ceiling = min(baseline_allowance, total_therms)
        overlap_baseline = min(max(0.0, baseline_ceiling - non_marginal), marginal_therms)
        overflow = marginal_therms - overlap_baseline
        if overflow <= FLOOR_ESTIMATION_TOLERANCE_THERMS:
            return marginal_therms * baseline_rate
    return HPC._gas_service_segment_tier_cost(
        total_therms=total_therms, heating_therms=marginal_therms,
        baseline_allowance=baseline_allowance, baseline_rate=baseline_rate,
        nonbaseline_rate=nonbaseline_rate, context=context)


def floor_savings_by_period(iso, n_trailing=12, water_heater_share=1.0):
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
    calendar-year basis.

    `water_heater_share` (Codex adversarial review, issue #20 round 1,
    Finding 2): the floor's own composition beyond "at least the water
    heater" is NOT DETERMINED (gas_end_use_enumeration, cooking_fuel_
    evidence) -- water_heater_share < 1.0 re-runs this SAME tier-pricing
    machinery against a SCALED floor_per_day, so the water_heater_share_
    sensitivity section in build()'s own output is a real re-pricing, not a
    linear dollar-scaling of the 100%-floor figure (tiered rates are not
    exactly linear in therms, so a dollar-scaling would itself be a small
    additional approximation on top of an already-uncertain share
    assumption -- avoided here since the real re-price costs little)."""
    periods = pd.read_csv(HPC.GAS_PERIODS_CSV)
    periods["statement_date"] = pd.to_datetime(periods["statement_date"]).dt.date
    periods[["period_start", "period_end"]] = periods["period"].str.split(
        " - ", expand=True).apply(lambda c: pd.to_datetime(c, format="%b %d, %Y").dt.date)
    periods = periods.sort_values("period_start").reset_index(drop=True)
    trailing = periods.tail(n_trailing).copy()

    gas_detail = HPC.load_gas_detail()
    floor_per_day = iso["floor_therms_per_day"] * water_heater_share

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
            seg_context = f"{context} gas_service segment {seg['segment']}"
            t_s = _floor_segment_total_therms(
                seg_start, seg_end, row["therms"], period_days, floor_s, seg_context)
            # floor_s is the quantity being REMOVED (the water heater
            # converts), so it takes the marginal/top-of-ladder role --
            # exactly as heat_pump_conversion.py uses its own
            # `heating_therms` argument for heating's own removal. See this
            # module's own top-of-file note and _priced_at_top_of_ladder's
            # own docstring.
            gs_cost += _priced_at_top_of_ladder(
                total_therms=t_s, marginal_therms=floor_s, baseline_allowance=a_s,
                baseline_rate=seg["baseline_rate"], nonbaseline_rate=seg["nonbaseline_rate"],
                context=seg_context)

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

      uniform         -- spread evenly across every interval of each day.
      super_off_peak  -- concentrated in each day's own super-off-peak
                 (rates.period()'s "sop" code) intervals -- the
                 discretionary-timer placement index.html describes as
                 physically realistic for a water heater (it can run on a
                 schedule, unlike a thermostat-driven heating load). This
                 is NOT a "midday" placement: on this household's EV-TOU-5
                 tariff sop is 00:00-06:00 plus 10:00-14:00 on weekdays and
                 00:00-14:00 on weekends (rates.period()) -- mostly
                 overnight and early-morning hours, only the weekday
                 10:00-14:00 slice genuinely coincides with peak solar. It
                 is named for the RATE PERIOD it targets (the genuinely
                 cheapest tariff period on this schedule, the same
                 convention heat_pump_conversion.build_hp_load_series uses
                 for its own sop-targeting `off_peak` scenario), not for a
                 daytime/solar-timer story -- a real, corrected mismatch
                 between this scenario's own code and index.html's earlier
                 "midday" prose (Codex review pass, issue #20 round 3).
      on_peak -- concentrated in each day's own on-peak intervals -- the
                 illustrative high-cost lean, the water-heater-side bracket
                 partner to `super_off_peak`.

    Neither `super_off_peak` nor `on_peak` is a computed cost extremum, the
    same caveat heat_pump_conversion.build_hp_load_series states for its own
    on_peak/off_peak bracket."""
    dates = d["dt"].dt.date
    unique_dates = dates.unique()
    n_days = len(unique_dates)
    if n_days == 0:
        raise SystemExit("all_electric_endgame.py: d has no dates to place "
                         "the water heater's load into")
    kwh_per_day = ann_wh_kwh / n_days
    out = {"uniform": pd.Series(0.0, index=d.index),
           "super_off_peak": pd.Series(0.0, index=d.index),
           "on_peak": pd.Series(0.0, index=d.index)}
    fallback_days = {"super_off_peak": 0, "on_peak": 0}
    for day in unique_dates:
        mask = dates == day
        n = int(mask.sum())
        out["uniform"].loc[mask] += kwh_per_day / n

        sop_mask = mask & (d["p"] == "sop")
        n_sop = int(sop_mask.sum())
        if n_sop > 0:
            out["super_off_peak"].loc[sop_mask] += kwh_per_day / n_sop
        else:
            out["super_off_peak"].loc[mask] += kwh_per_day / n
            fallback_days["super_off_peak"] += 1

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
        if ann_wh_kwh <= 0:
            # Fails closed rather than a bare ZeroDivisionError below. No
            # current caller reaches this (round 4 retired the only
            # water_heater_share=0.0 call site -- see the module-level
            # comment above water_heater_share_sensitivity()), but the
            # named shares here are COMPUTED from external benchmarks
            # (dryer_pct_of_floor_range etc.), not constants, so a future
            # benchmark update reaching 100% of the floor could reintroduce
            # a zero share without this function's own signature changing.
            raise SystemExit(
                f"all_electric_endgame.py: wh_electric_cost_scenarios got "
                f"floor_therms_yr={floor_therms_yr:g}, giving a "
                f"non-positive ann_wh_kwh for {uef_key} -- refusing to "
                "price a zero-or-negative water-heating load")
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


def joint_electric_cost_scenario(d, furnace_iso, furnace_cop, ann_wh_kwh):
    """The COMBINED furnace + water-heater added-load series, netted against
    solar and re-billed ONCE -- the correct joint electric-cost delta for
    `sequencing_and_paybacks`'s own complete-transition scenario (Finding 1,
    Codex adversarial review, issue #20 round 2).

    `wh_electric_cost_scenarios()` and `heat_pump_conversion.electric_cost_
    scenarios()` each independently net their OWN added load against the
    household's FULL solar Generation (CLAUDE.md section 1b) before
    billing. Summing their two independently-computed electric-cost
    increases silently lets BOTH conversions claim the SAME exported solar
    kWh in the SAME interval -- physically impossible once both loads are
    real, and `rates.bill_nem()`'s own per-(month, TOU bucket) NEM netting
    is not additive across two separately-billed scenarios either. This
    function builds each conversion's own uniform-distribution added-load
    series with the SAME functions their independent scenarios already use
    (`heat_pump_conversion.build_hp_load_series` for the furnace, `build_wh_
    load_series` for the water heater -- not reimplemented), SUMS them into
    ONE combined series, then runs the SAME solar-absorb-first-then-grid-
    import netting and `rates.bill_nem()` re-bill ONCE against that combined
    series.

    Neither `build_hp_load_series()` nor `build_wh_load_series()` enforces
    a per-interval kW/circuit-amperage cap -- each only allocates a day's
    (or heating-day's) total kWh across some subset of that day's own real
    intervals, with no check on how many watts that implies in any single
    interval. `rates.bill_nem()` itself has no demand-charge/kW-rate
    component either (rates.py: pure TOU-and-NEM netting on kWh, see its
    own module docstring) -- so summing the two series here changes
    electric BILLING correctly without interacting with, weakening, or
    needing any interval-level power cap. The real physical question --
    can the panel actually supply both loads at once -- is a PANEL-level
    check, already made CUMULATIVELY for the furnace heat pump and the
    water heater by `service_headroom_check()` (AC5, whose own docstring
    opens "cumulative headroom for furnace heat pump + water heater"); this
    function does not duplicate, weaken, or bypass that check."""
    furnace_added, ann_heat_kwh, _ = HPC.build_hp_load_series(d, furnace_iso, furnace_cop)
    wh_added, _ = build_wh_load_series(d, ann_wh_kwh)
    combined = furnace_added["uniform"] + wh_added["uniform"]
    total_added = float(combined.sum())
    expected = ann_heat_kwh + ann_wh_kwh
    if expected > 0 and abs(total_added - expected) / expected > 0.001:
        raise SystemExit(
            f"all_electric_endgame.py: joint_electric_cost_scenario added "
            f"{total_added:.1f} kWh, not the {expected:.1f} kWh the "
            "combined furnace + water-heater load requires -- energy is "
            "not conserved")
    base_bill = R.bill_nem(d, imp="Consumption", exp="Generation")
    absorbed = pd.concat([d["Generation"], combined], axis=1).min(axis=1)
    remainder = combined - absorbed
    f = d.copy()
    f["Generation"] = d["Generation"] - absorbed
    f["Consumption"] = d["Consumption"] + remainder
    assert abs(float(absorbed.sum() + remainder.sum()) - total_added) < 0.01, (
        "joint_electric_cost_scenario: solar-netting step lost or duplicated energy")
    new_bill = R.bill_nem(f, imp="Consumption", exp="Generation")
    return {
        "furnace_added_kwh": round(ann_heat_kwh),
        "water_heater_added_kwh": round(ann_wh_kwh),
        "combined_added_kwh": round(total_added),
        "solar_absorbed_kwh": round(float(absorbed.sum())),
        "electric_cost_increase_usd": round(new_bill - base_bill, 2),
        "basis": ("uniform distribution for both loads, furnace at central "
                  "COP 3.5, water heater at the headline UEF -- matching "
                  "each conversion's own headline scenario basis"),
    }


def electric_interaction_overstatement(wh_electric_increase_usd, furnace_electric_increase_usd,
                                       joint_electric_increase_usd):
    """How much `complete_transition_payback`'s naive combined savings
    OVERSTATES the true combined savings by summing two INDEPENDENTLY
    rebilled electric-cost increases rather than rebilling the combined
    added-load series once (Finding 1, Codex adversarial review, issue #20
    round 2 -- the ELECTRIC-side counterpart to `tier_interaction_
    overstatement()`'s gas-side correction; same CLAUDE.md section 9
    requirement, "one pipeline per package figure").

    `rates.bill_nem()`'s own NEM netting absorbs added load against that
    SAME interval's solar Generation first (CLAUDE.md section 1b); when the
    water heater's and furnace's added loads are rebilled independently
    (`wh_electric_cost_scenarios`, `heat_pump_conversion.electric_cost_
    scenarios`), each one separately gets first claim on the household's
    FULL solar export in every interval -- both cannot actually do that at
    once. `joint_electric_cost_scenario()`'s own combined rebill is
    therefore always >= the sum of the two independent electric_cost_
    increase_usd figures: less solar is really available to each load once
    both draw on the same panel, so the true combined bill rises by at
    least as much as the two independent estimates suggest. This is a
    structural, not a coincidental, result: for any nonnegative solar-
    export quantity g and any two nonnegative added loads a, b,
    min(g, a) + min(g, b) >= min(g, a + b) (the "how much of a fixed
    export can a load absorb" function is concave and therefore
    subadditive), and rates.bill_nem() prices additional grid import at a
    non-decreasing marginal rate, so more combined import never bills for
    less."""
    independent_sum = round(wh_electric_increase_usd + furnace_electric_increase_usd, 2)
    overstatement = round(joint_electric_increase_usd - independent_sum, 2)
    return {
        "wh_independent_electric_increase_usd": wh_electric_increase_usd,
        "furnace_independent_electric_increase_usd": furnace_electric_increase_usd,
        "independent_sum_electric_increase_usd": independent_sum,
        "joint_electric_increase_usd": joint_electric_increase_usd,
        "overstatement_usd": overstatement,
        "note": ("Positive means summing each conversion's own "
                 "independently-rebilled electric-cost increase "
                 "UNDERSTATES the true combined electric cost (equivalently "
                 "OVERSTATES combined net savings), because each "
                 "independent rebill lets that conversion's own added load "
                 "claim first dibs on the household's FULL solar export in "
                 "every interval -- both cannot do that simultaneously. "
                 "Structurally >= 0 whenever the two loads' own placements "
                 "ever compete for the same interval's export; see this "
                 "function's own docstring for why."),
    }


# Codex adversarial review, issue #20 round 1, Finding 2 (and round 2, which
# found the round-1 fix still overclaimed): the pure computation above
# prices the WHOLE non-heating floor as if it were 100% water heater, even
# though gas_end_use_enumeration's own cooking_fuel_evidence says that
# composition is NOT DETERMINED. CLAUDE.md section 0 forbids treating an
# unverified assumption as a headline point estimate -- this function
# propagates that uncertainty through as explicit scenarios instead of
# hiding it behind one number. 1.0 is the pure computation, kept because it
# is the mechanically correct answer to "if the whole floor is water
# heater"; the next two apply third_end_use_gap's OWN possible-dryer-
# share-of-floor benchmark (27.7-78.8%), inverted (a household with no
# dryer needs no such adjustment at all, hence 1.0 remains a real, live
# possibility, not a straw-man ceiling).
#
# Round 2 (Codex adversarial review, Finding 2): index.html's own cooking_
# fuel_evidence section already says gas COOKING might ALSO share this same
# floor, unresolved by the same missing household.appliance_fuels answer --
# if cooking is gas, the true water-heater share could sit below the
# round-1 "low" scenario (21.2%), so calling those three a bound overstated
# the evidence. COOKING_THERMS_YR_RANGE (same evidentiary tier as the dryer
# benchmark) sizes cooking's own possible claim on the floor the same way
# the dryer benchmark already sizes the dryer's.
#
# Round 4 (Codex `review` pass, issue #20, Finding 1): round 2 published the
# dryer-and-cooking-high-ends-combined RESIDUAL as a fourth live "scenario"
# (residual_if_dryer_and_cooking_both_present_at_benchmark_high), complete
# with its own $0.00/yr floor_savings/net_savings and a "no payback" table
# row. That is internally impossible, not merely unverified: this household
# is KNOWN to have a gas water heater today (this issue's own body,
# "Water heating is gas today"; gas_end_use_enumeration's own
# non_heating_floor.floor_composition already states the floor is "at
# minimum the water heater"), so a scenario asserting the water heater's
# OWN share is exactly ZERO asserts something already known false about
# this household, not an open question. The mechanical arithmetic (on this
# household's real data, 108 dryer-therms/yr + 60 cooking-therms/yr = 168,
# against a 137-therm/yr floor) only shows that DRYER_THERMS_PER_MONTH_
# RANGE's own high end and COOKING_THERMS_YR_RANGE's own high end -- two
# independent, uncited-to-this-household EXTERNAL benchmarks -- cannot BOTH
# be true simultaneously for this household; it does not show the water
# heater uses zero gas. Publishing that arithmetic as a fourth payback
# scenario overstated certainty in the opposite direction CLAUDE.md section
# 0 already warns about (asserting a known-false input as a live outcome,
# not merely an under-evidenced one). Fixed by keeping the mechanical
# number -- it is a real, checkable computation, and worth showing exactly
# how far the two high ends overshoot the floor by -- but reporting it as
# `benchmark_incompatibility_check`, a diagnostic, NOT a member of
# `scenarios`: no floor_savings/electric_cost/net_savings/payback is
# computed or published for it, since a known-false input has no real
# payback to report. This repo considered, and rejected, inventing a
# small-but-nonzero floor share instead (CLAUDE.md section 0 forbids a
# floor value with no evidentiary basis just as much as it forbids an
# impossible zero -- no source pins what the true minimum water-heater
# share actually is, so any such number would itself be a guess).
def water_heater_share_sensitivity(iso, d, dryer_pct_of_floor_range,
                                   cooking_pct_of_floor_range, headline_uef):
    """AC3/AC4, Finding 2: floor_savings_annual_usd, electric cost, net
    savings and payback at explicit water-heater-share assumptions, each a
    REAL re-price (floor_savings_by_period + wh_electric_cost_scenarios
    re-run at the scaled floor_per_day/therms, not a linear dollar-scale of
    the 100% figure -- see floor_savings_by_period's own water_heater_share
    docstring paragraph). Headline-UEF, uniform-distribution basis only
    (matching this script's own primary reference elsewhere), to keep the
    output to the scenarios that matter rather than a full UEF x
    distribution x share cross product no reader asked for.

    Reports exactly THREE live scenarios (100%, 72.3%, 21.2%), plus a
    separate, non-scenario `benchmark_incompatibility_check` -- see the
    module-level comment immediately above this function (Finding 1, Codex
    `review` pass, issue #20 round 4) for why a fourth "0%-share" scenario
    is not published as a live payback outcome."""
    lo_dryer_pct, hi_dryer_pct = dryer_pct_of_floor_range
    lo_cooking_pct, hi_cooking_pct = cooking_pct_of_floor_range
    shares = {
        "100pct_full_floor": 1.0,
        "72pct_if_dryer_present_at_benchmark_low": round(1 - lo_dryer_pct / 100, 3),
        "21pct_if_dryer_present_at_benchmark_high": round(1 - hi_dryer_pct / 100, 3),
    }
    scenarios = {}
    for key, share in shares.items():
        rows, savings_usd, therms_annual = floor_savings_by_period(iso, water_heater_share=share)
        electric, _ = wh_electric_cost_scenarios(d, therms_annual)
        electric_increase = electric[headline_uef]["uniform"]["electric_cost_increase_usd"]
        net_savings = round(savings_usd - electric_increase, 2)
        scenarios[key] = {
            "water_heater_share": share,
            "floor_therms_annual": therms_annual,
            "floor_savings_annual_usd": savings_usd,
            "electric_cost_increase_usd": electric_increase,
            "annual_net_savings_usd": net_savings,
            "payback": {
                "low_install": HPC.payback_and_npv(
                    net_savings, WH_INSTALL_COST_LOW_USD, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS),
                "central_install": HPC.payback_and_npv(
                    net_savings, WH_INSTALL_COST_CENTRAL_USD, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS),
                "high_install": HPC.payback_and_npv(
                    net_savings, WH_INSTALL_COST_HIGH_USD, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS),
            },
        }

    combined_hi_pct = round(hi_dryer_pct + hi_cooking_pct, 1)
    residual_share = round(max(0.0, 1 - combined_hi_pct / 100), 3)
    incompatible = residual_share <= 0.0
    benchmark_incompatibility_check = {
        "dryer_benchmark_high_pct_of_floor": hi_dryer_pct,
        "cooking_benchmark_high_pct_of_floor": hi_cooking_pct,
        "combined_pct_of_floor": combined_hi_pct,
        "mechanical_residual_water_heater_share": residual_share,
        "verdict": "implausible_for_this_household" if incompatible else "not_triggered",
        "not_a_scenario": True,
        "note": ((
            f"the dryer benchmark's own high end ({hi_dryer_pct}% of the "
            f"floor) plus the cooking benchmark's own high end "
            f"({hi_cooking_pct}% of the floor) together claim "
            f"{combined_hi_pct}% of the floor -- "
            f"{round(combined_hi_pct - 100, 1)} percentage points over "
            "100%, i.e. more gas than the floor actually contains. This "
            "household is KNOWN to have a gas water heater today (this "
            "issue's own body; gas_end_use_enumeration's own "
            "non_heating_floor.floor_composition: 'at minimum the water "
            "heater'), so a scenario claiming the water heater's own share "
            "is exactly zero would assert something already known false, "
            "not merely unverified. The correct reading is the reverse: "
            "these two SPECIFIC external, uncited-to-this-household "
            "high-end benchmarks cannot BOTH be true simultaneously for "
            "this household -- at most one applies at its own high end "
            "here, or both apply at less than their own high ends -- not "
            "that the water heater consumes zero gas. This is NOT a "
            "payback scenario: no floor_savings/electric_cost/net_savings/"
            "payback is computed or published for it."
        ) if incompatible else (
            f"the dryer benchmark's own high end ({hi_dryer_pct}% of the "
            f"floor) plus the cooking benchmark's own high end "
            f"({hi_cooking_pct}% of the floor) together claim "
            f"{combined_hi_pct}% of the floor -- under 100%, so the two "
            "high-end benchmarks are not mutually incompatible on this "
            "household's own numbers and no incompatibility is flagged."
        )),
    }

    return {
        "basis": ("household.appliance_fuels was never answered at intake, so "
                  "the floor's own composition beyond 'at least the water "
                  "heater' is NOT DETERMINED (see gas_end_use_enumeration). "
                  "These are ILLUSTRATIVE scenarios built from external, "
                  "uncited-to-this-household usage benchmarks (a typical gas "
                  "dryer and a typical gas range/cooktop), NOT a proven "
                  "mathematical bound on the water heater's own true share: "
                  "the 100pct scenario is the mechanically pure computation "
                  "(and remains live if this household has neither a gas "
                  "dryer nor gas cooking); the other two apply only the "
                  "dryer benchmark, at its low and high end. A THIRD "
                  "external benchmark (typical gas cooking, "
                  "COOKING_THERMS_YR_RANGE) is not folded into a fourth "
                  "scenario here -- see benchmark_incompatibility_check "
                  "below, which shows the dryer's and cooking's own high "
                  "ends together would claim more of the floor than exists, "
                  "which is evidence those two specific high-end "
                  "assumptions can't both hold at once for a household "
                  "known to run a gas water heater, not evidence the water "
                  "heater itself uses no gas. The true water-heater share "
                  "-- and the true payback -- could still be lower than "
                  "every scenario shown here (an even heavier dryer or "
                  "cooking load, or another unlisted gas end use, cannot be "
                  "ruled out from these benchmarks), or could be the full "
                  "100pct if this household has neither. Report the range "
                  "as illustrative, not as a settled bracket, per CLAUDE.md "
                  "section 0."),
        "headline_uef": headline_uef,
        "scenarios": scenarios,
        "benchmark_incompatibility_check": benchmark_incompatibility_check,
    }


def _wh_net_savings_at_share(iso, d, share, headline_uef, base_bill=None, unit_uniform_series=None):
    """The water heater's real-repriced annual net savings ($) at an
    ARBITRARY water_heater_share, restricted to the (headline_uef, uniform)
    basis this script's own headline figures already use -- a leaner
    single-scenario rebill than wh_electric_cost_scenarios()'s own full
    3-UEF x 3-distribution grid, which sequencing_share_robustness()'s own
    bisection search below does not need (it calls this dozens of times).
    Reuses floor_savings_by_period() and build_wh_load_series() directly
    (not reimplemented) -- the SAME functions wh_electric_cost_scenarios()
    itself calls -- and rates.bill_nem() for the rebill, mirroring wh_
    electric_cost_scenarios()'s own solar-absorb-first netting pattern
    (CLAUDE.md section 1b). `base_bill` does not depend on `share`, so a
    caller running many trial shares (a bisection search) may compute it
    once and pass it in rather than re-billing the SAME unmodified year on
    every trial.

    `unit_uniform_series` is an optional PRECOMPUTED build_wh_load_series()
    "uniform" series for ann_wh_kwh=1.0. build_wh_load_series()'s own
    uniform allocation has no share-dependent branching at all (every
    interval gets exactly `(1.0 / n_days) / n`, a pure per-day constant)
    -- so it is EXACTLY linear in ann_wh_kwh, and `unit_series *
    ann_wh_kwh` reproduces build_wh_load_series(d, ann_wh_kwh)["uniform"]
    exactly, not an approximation. build_wh_load_series() itself walks
    every one of ~365 real days with a fresh boolean mask over the whole
    year's own interval series (profiled at ~0.9s/call on this
    household's real archive) -- expensive to repeat on every one of a
    bisection search's dozens of trial shares for no reason, since only
    the SCALE changes between trials, never the shape. A caller running
    many trial shares should compute this once (`build_wh_load_series(d,
    1.0)[0]["uniform"]`) and pass it in."""
    _, savings_usd, therms_annual = floor_savings_by_period(iso, water_heater_share=share)
    if base_bill is None:
        base_bill = R.bill_nem(d, imp="Consumption", exp="Generation")
    ann_wh_kwh = therms_annual * HPC.KWH_PER_THERM * GAS_WH_UEF / HPWH_UEF_SCENARIOS[headline_uef]
    if unit_uniform_series is None:
        added, _ = build_wh_load_series(d, 1.0)
        unit_uniform_series = added["uniform"]
    series = unit_uniform_series * ann_wh_kwh
    absorbed = pd.concat([d["Generation"], series], axis=1).min(axis=1)
    remainder = series - absorbed
    f = d.copy()
    f["Generation"] = d["Generation"] - absorbed
    f["Consumption"] = d["Consumption"] + remainder
    new_bill = R.bill_nem(f, imp="Consumption", exp="Generation")
    assert abs(float(absorbed.sum() + remainder.sum()) - float(series.sum())) < 0.01, (
        "_wh_net_savings_at_share: solar-netting step lost or duplicated energy")
    return round(savings_usd - (new_bill - base_bill), 2)


def _crossover_water_heater_share(iso, d, headline_uef, target_payback_years, base_bill=None,
                                  install_usd=None, unit_uniform_series=None):
    """Bisects water_heater_share in (roughly 0, 1] for the share at which
    the water heater's own central-install payback first reaches
    `target_payback_years` (the furnace's own standalone payback) -- the
    numeric crossover sequencing_share_robustness() below reports (Finding
    2, Codex `review` pass, issue #20 round 4). Reuses HPC.payback_and_
    npv() and _wh_net_savings_at_share() above at each trial share, not a
    second re-pricing implementation.

    Net savings is empirically monotonically INCREASING in water_heater_
    share on this household's real data (checked at the three named
    scenarios water_heater_share_sensitivity() reports: 21.2%/72.3%/100%
    give $32.32/$101.80/$136.27), so payback years is monotonically
    DECREASING in share and a bisection search is valid here; that
    monotonicity is a property of this real run, not proven algebraically
    for an arbitrary gas tariff, so the search brackets itself (checking
    payback(lo) is worse than target and payback(hi) is at least as good)
    rather than assuming the bracket blindly."""
    install_usd = WH_INSTALL_COST_CENTRAL_USD if install_usd is None else install_usd
    if base_bill is None:
        base_bill = R.bill_nem(d, imp="Consumption", exp="Generation")
    if unit_uniform_series is None:
        added, _ = build_wh_load_series(d, 1.0)
        unit_uniform_series = added["uniform"]

    def payback_at(share):
        net = _wh_net_savings_at_share(iso, d, share, headline_uef, base_bill=base_bill,
                                       unit_uniform_series=unit_uniform_series)
        return HPC.payback_and_npv(
            net, install_usd, HPC.DISCOUNT_RATES, HPC.NPV_HORIZON_YEARS)["payback_years"]

    lo, hi = 1e-4, 1.0
    pb_lo, pb_hi = payback_at(lo), payback_at(hi)
    # pb_hi is None means the water heater NEVER pays back at all at 100%
    # share (HPC.payback_and_npv() returns payback_years=None when net
    # savings are <= 0, not a large finite number) -- that is just as much
    # "never beats the target" as a finite pb_hi >= target_payback_years, so
    # both must return None here. A version that checked only `pb_hi is not
    # None and pb_hi >= target_payback_years` silently skipped the None case
    # (a False `and` short-circuit), fell through to the bisection below
    # with pb_mid also None at every trial (mid never reaches hi=1.0 in the
    # 40 bisection iterations), which drove `lo` UP toward the pinned
    # hi=1.0 every iteration -- falsely reporting the MAXIMUM possible
    # crossover share (round(hi, 4) == 1.0) as if the water heater "wins"
    # at 100% share, when it never wins at all.
    if pb_hi is None or pb_hi >= target_payback_years:
        return None  # the water heater never beats the target even at 100% share
    if pb_lo is not None and pb_lo < target_payback_years:
        return round(lo, 4)  # crossover at or below the search floor
    for _ in range(40):
        mid = (lo + hi) / 2
        pb_mid = payback_at(mid)
        if pb_mid is None or pb_mid > target_payback_years:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-4:
            break
    return round(hi, 4)


def _share_robustness_on_basis(iso, d, headline_uef, wh_share_scenarios, furnace_install_usd,
                               furnace_annual_net_savings_usd, target_furnace_payback_years,
                               tier_interaction, electric_interaction, base_bill,
                               unit_uniform_series, basis_label):
    """The named-scenario order check plus the crossover bisection, run
    against ONE furnace payback figure (`target_furnace_payback_years`) --
    factored out of sequencing_share_robustness() so the SAME check can run
    twice, once per install-cost basis (Finding 4, code-reviewer, issue #20
    round 6). See sequencing_share_robustness()'s own docstring for why
    reusing sequencing_and_paybacks() itself for the order probe is
    correct and why complete_transition_payback is deliberately not
    surfaced from it."""
    named = {}
    all_robust = True
    for key, share in wh_share_scenarios.items():
        net = _wh_net_savings_at_share(iso, d, share, headline_uef, base_bill=base_bill,
                                       unit_uniform_series=unit_uniform_series)
        pb = HPC.payback_and_npv(net, WH_INSTALL_COST_CENTRAL_USD, HPC.DISCOUNT_RATES,
                                 HPC.NPV_HORIZON_YEARS)["payback_years"]
        probe = sequencing_and_paybacks(
            fixed_charge_verdict_is_zero=True, wh_install_usd=WH_INSTALL_COST_CENTRAL_USD,
            wh_annual_net_savings_usd=net, wh_payback_years=pb,
            furnace_install_usd=furnace_install_usd,
            furnace_annual_net_savings_usd=furnace_annual_net_savings_usd,
            furnace_payback_years=target_furnace_payback_years,
            tier_interaction=tier_interaction, electric_interaction=electric_interaction)
        named[key] = {
            "water_heater_share": share,
            "water_heater_annual_net_savings_usd": net,
            "water_heater_payback_years": pb,
            "order": probe["order"],
            "last_step": probe["last_step"],
        }
        if probe["order"] != ["water_heater", "furnace"]:
            all_robust = False

    crossover_share = _crossover_water_heater_share(
        iso, d, headline_uef, target_furnace_payback_years, base_bill=base_bill,
        unit_uniform_series=unit_uniform_series)

    return {
        "furnace_payback_years_basis": basis_label,
        "furnace_payback_years": target_furnace_payback_years,
        "named_scenarios": named,
        "robust_across_named_scenarios": all_robust,
        "crossover_water_heater_share": crossover_share,
        "crossover_note": ((
            "the water_heater_share below which the water heater's own "
            "central-install payback would exceed the furnace's own "
            f"{target_furnace_payback_years:g}-year {basis_label} payback, "
            "reversing the published order, found by bisection on the SAME "
            "real-interval gas/electric re-pricing this section's own "
            "named scenarios use (not a linear extrapolation)"
        ) if crossover_share is not None else (
            "no crossover found in (0, 1] -- on this household's real "
            "data the water heater sequences first at every possible "
            "share, however low, against this basis's own furnace payback"
        )),
    }


def sequencing_share_robustness(iso, d, headline_uef, wh_share_scenarios, furnace_install_usd,
                                furnace_annual_net_savings_usd, furnace_payback_years,
                                tier_interaction, electric_interaction,
                                furnace_payback_years_marginal=None):
    """Finding 2 (Codex `review` pass, issue #20 round 4): whether the
    published sequencing order (water heater first, furnace last --
    sequencing_and_paybacks()'s own `order`/`last_step`) is ROBUST across
    the water-heater-share uncertainty water_heater_share_sensitivity()
    already establishes, or would REVERSE at a lower, still-plausible
    share. sequencing_and_paybacks()'s own headline order is computed from
    ONLY the 100%-water-heater-share scenario, but that share is NOT
    VERIFIED (its own not_verified_caveat) and the true share could sit
    below every illustrative scenario shown.

    `furnace_payback_years` is the furnace's own STANDALONE-install-cost
    basis (matching `combined_install`'s own basis in sequencing_and_
    paybacks() -- CLAUDE.md section 2's "one basis per projection"
    principle: complete_transition_payback's own combined_install actually
    IS wh_install_usd + furnace_install_usd, the standalone figure, so this
    is the basis-consistent robustness check for that published figure).
    Reuses sequencing_and_paybacks() ITSELF for the order check at each
    named share scenario, rather than a second sort implementation: its
    `order`/`last_step` fields are computed from install_usd/
    payback_years alone, BEFORE either interaction correction is applied
    (see its own source -- the sort happens first, the tier/electric
    corrections are applied only afterward to complete_transition_
    payback's own combined savings) -- so calling it with a DIFFERENT
    share's own marginal wh_payback_years, holding the SAME (100%-share-
    basis) tier_interaction/electric_interaction dicts, still yields a
    correct order/last_step. This function deliberately does NOT surface
    that probe call's own complete_transition_payback: pairing a
    non-headline share's own marginal savings with an interaction
    correction computed on the headline share would be a real, mismatched
    composite figure, not a second one this function publishes. The
    marginal-basis probe below (`furnace_payback_years_marginal`) carries
    a SECOND, related mismatch for the same reason: it always passes the
    STANDALONE `furnace_install_usd`, never a marginal install figure, so
    that probe's own internal (unsurfaced) combined-install/combined-
    payback fields would be doubly incoherent -- mismatched share AND
    mismatched install basis -- if anyone ever extended this function to
    publish them. `order`/`last_step` are unaffected (they depend on
    `payback_years` alone, never on `furnace_install_usd`), so today's
    published `marginal_basis` output is correct; this is a hazard for a
    future extension, not a live defect.

    `furnace_payback_years_marginal` (Finding 4, code-reviewer, issue #20
    round 6): sequencing_and_paybacks()'s combined_install always uses the
    furnace's own STANDALONE install cost, so the robustness check above is
    ONLY verified on that basis. `data/heat_pump_conversion.json` and this
    report's own furnace section ALSO publish a MARGINAL-over-AC-
    replacement basis (a homeowner already due for an AC replacement pays
    only the marginal cost of upgrading that replacement to a heat pump),
    which this report elsewhere calls the more realistic basis for that
    situation -- and on this household's real data the published order does
    NOT survive on that basis at the lowest illustrative share (the water
    heater's own 130.0-year payback at 21.2% share loses to a 48.6-year
    marginal-basis furnace, reversing the order at a scenario this report
    explicitly illustrates). When provided, this runs the SAME check a
    second time against that basis and returns it separately as
    `marginal_basis`, rather than silently leaving the published "robust
    across every illustrative share" claim unqualified about which
    install-cost basis it was actually checked against."""
    base_bill = R.bill_nem(d, imp="Consumption", exp="Generation")
    # Computed ONCE and reused for every trial share below (named scenarios
    # plus the crossover bisection's own dozens of trials, on BOTH bases
    # when a marginal basis is supplied) -- see _wh_net_savings_at_share()'s
    # own docstring for why this is exact, not an approximation, and why it
    # matters for runtime.
    unit_uniform_series = build_wh_load_series(d, 1.0)[0]["uniform"]

    standalone = _share_robustness_on_basis(
        iso, d, headline_uef, wh_share_scenarios, furnace_install_usd,
        furnace_annual_net_savings_usd, furnace_payback_years, tier_interaction,
        electric_interaction, base_bill, unit_uniform_series, "standalone")

    marginal = None
    if furnace_payback_years_marginal is not None:
        marginal = _share_robustness_on_basis(
            iso, d, headline_uef, wh_share_scenarios, furnace_install_usd,
            furnace_annual_net_savings_usd, furnace_payback_years_marginal, tier_interaction,
            electric_interaction, base_bill, unit_uniform_series, "marginal_over_ac_replacement")

    out = {
        "basis": ("checks whether the headline sequencing order (water "
                  "heater first, furnace last, computed on the "
                  "100%-water-heater-share basis) survives the SAME share "
                  "uncertainty water_heater_share_sensitivity() already "
                  "establishes -- the true share is not verified and could "
                  "sit below every scenario shown there. Checked against "
                  "the furnace's own STANDALONE install-cost basis (matching "
                  "combined_install's own basis above, CLAUDE.md section 2) "
                  "by default" + (
                      "; a SECOND check against the furnace's own "
                      "marginal-over-AC-replacement install-cost basis is "
                      "also reported below (marginal_basis) -- that basis "
                      "is NOT the one combined_install actually uses, so "
                      "marginal_basis is a diagnostic on a different "
                      "hypothetical, not a second combined-savings figure"
                      if marginal is not None else
                      " -- no marginal-over-AC-replacement check was run "
                      "for this build")),
        "named_scenarios": standalone["named_scenarios"],
        "robust_across_named_scenarios": standalone["robust_across_named_scenarios"],
        "crossover_water_heater_share": standalone["crossover_water_heater_share"],
        "crossover_note": standalone["crossover_note"],
        "marginal_basis": marginal,
    }
    return out


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

    # after_wh_* is a CEILING on the furnace heat pump's own MCA (the
    # largest unit that would still fit alongside a full-code water
    # heater) -- it is NOT a verified combined fit, because the furnace
    # heat pump's own equipment ampacity is never subtracted anywhere in
    # this computation. heat_pump_only's remaining_headroom_a is itself a
    # SOLVED-FOR term (its own service_headroom.json note: "No heat pump
    # has been selected, so the term is solved for rather than assumed:
    # this is the largest minimum circuit ampacity that fits") -- it
    # already equals the panel's total spare capacity with a heat pump's
    # own draw at zero, not a headroom figure with a real unit's demand
    # already debited. heat_pump_replaces_ac's own remaining_headroom_a is
    # the SAME shape (also solved-for, per its own "remaining_is": "the
    # largest heat-pump MCA that fits...") -- crediting the outgoing A/C's
    # removed load changes the ceiling's SIZE, not the fact that it is
    # still a ceiling, not a fixed remaining number for an assumed unit.
    # Nowhere in this issue's own furnace analysis is one specific
    # heat-pump model selected: heat_pump_conversion.py prices a COP
    # BRACKET (COP_SCENARIOS: 2.8/3.5/4.2), not one nameplate MCA. So a
    # 'pass' verdict here would assert something not knowable from this
    # artifact. A 'fail' verdict does NOT have that problem: if the
    # ceiling is already negative on BOTH bases, the water heater's own
    # fixed code load alone -- with ZERO heat pump amps added -- already
    # exceeds spare capacity, which holds regardless of which heat pump,
    # if any, is eventually chosen.
    ampacity_verdict = ("fail" if after_wh_conservative < 0 and after_wh_measured < 0
                        else "not_determined")

    return {
        "basis": ("furnace heat pump reuses the existing A/C circuit "
                  "(service_headroom.json's own heat_pump_replaces_ac "
                  "case, verdict "
                  f"{hp_replaces_ac['ampacity_verdict']!r}) -- that verdict "
                  "credits the outgoing A/C's own removed demand against "
                  "the historical summer coincident PEAK the panel's spare "
                  "capacity is measured from; it says nothing about the "
                  "incoming heat pump's own equipment ampacity (MCA), "
                  "which this check never subtracts because no specific "
                  "heat-pump model is selected anywhere in this issue's "
                  "own furnace analysis (heat_pump_conversion.py prices a "
                  "COP bracket, not one nameplate unit). The water "
                  "heater's own new 30 A/240 V circuit, whose code load IS "
                  "fixed and known, is checked against the SAME panel-wide "
                  "spare capacity heat_pump_only's own case already "
                  "establishes (fixed_added_load_a=0, i.e. before any new "
                  "240 V load); what is left after that subtraction is a "
                  "CEILING on the furnace heat pump's own MCA, not a "
                  "verified combined installation -- see known_gap"),
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
        "known_gap": ("TWO gaps, not one. (1) service_headroom.json's own "
                      "cases are built from a SUMMER coincident-peak "
                      "measurement window (issue #6's own gross-load "
                      "reconstruction); a water heater and a space-heating "
                      "heat pump both draw the most in WINTER, a season "
                      "the underlying measurement window does not cover -- "
                      "the same already-documented gap heat_pump_"
                      "conversion.py's own module docstring names for the "
                      "furnace's added load. This check inherits that gap "
                      "rather than resolving it. (2) No specific furnace "
                      "heat-pump model has been selected anywhere in this "
                      "issue's own analysis -- heat_pump_conversion.py "
                      "prices a COP bracket (COP_SCENARIOS: 2.8/3.5/4.2), "
                      "not one nameplate unit -- so spare_after_water_"
                      "heater_a is a CEILING on the largest heat-pump MCA "
                      "that would still fit, not a verified combined "
                      "installation: this check cannot certify that any "
                      "real unit fits (ampacity_verdict is 'not_"
                      "determined' whenever that ceiling is non-negative). "
                      "The one exception is a genuine 'fail': if "
                      "spare_after_water_heater_a is already negative on "
                      "BOTH bases, even a zero-amp heat pump would not "
                      "fit, which holds regardless of gap (2). What would "
                      "settle gap (2): a specific heat-pump model's "
                      "nameplate MCA, checked against "
                      "spare_after_water_heater_a."),
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


def tier_interaction_overstatement(iso, n_trailing=12):
    """How much `complete_transition_payback` overstates the TRUE combined
    gas savings by summing two INDEPENDENTLY-computed marginal-removal
    figures, rather than computing one JOINT removal (CLAUDE.md section 9:
    "one pipeline per package figure... never by adding numbers from
    different models"). A direct, quantified consequence of the Finding-1
    fix (Codex adversarial review, issue #20 round 1): under a convex
    (tiered) rate, `[bill(T)-bill(T-F)] + [bill(T)-bill(T-H)]` (this
    script's own floor savings plus heat_pump_conversion.json's own
    heating savings, EACH computed as if it alone were removed from the
    period's original total T) is >= `bill(T) - bill(T-F-H)` (the true
    joint savings from removing both), because the shared top-of-ladder
    (nonbaseline) region gets priced twice whenever both F and H reach
    into it. Equality holds only when the period never reaches the
    nonbaseline tier at all, or when one of F/H is zero.

    SEGMENT-level basis (Codex `review` pass, issue #20 round 4, Finding 3
    -- a real methodology bug, not a style choice). A first version of this
    function computed at the PERIOD level (bill_periods_gas.csv's own
    period-blended baseline_rate/nonbaseline_rate/baseline_allowance_
    therms columns), even though BOTH figures it corrects --
    floor_savings_by_period() in this file and heat_pump_conversion.
    gas_savings_by_period() -- price at SEGMENT level (issue #109: each
    Gas Service segment's own rate, split at every mid-cycle rate change
    within a billing period). This household's real gas corpus has NINE
    periods with a real mid-cycle Gas Service rate change (bill_gas_
    detail.csv, e.g. 2025-01-29: 5 days at $1.56901/$1.87417, 27 days at
    $1.61980/$1.91783 -- two genuinely different, real billed rates inside
    ONE statement), so a single period-blended rate is not the same basis
    the two figures being corrected actually used whenever a period like
    that falls in the trailing window -- subtracting a coarse, wrong-basis
    correction from two segment-level figures does not produce a
    rigorously true joint figure. Fixed by computing the SAME F-vs-H
    interaction this function has always computed, but per Gas Service
    SEGMENT (reusing floor_savings_by_period()'s own `_floor_capped_days()`
    and heat_pump_conversion.gas_savings_by_period()'s own
    `_capacity_capped_days()` for the day-level floor/heating allocation,
    and `_segment_day_ranges()` / `_segment_heat_from_days()` /
    `_segment_real_or_proxy_therms()` -- ALL reused directly from
    heat_pump_conversion.py, not reimplemented, exactly the same helpers
    floor_savings_by_period() and gas_savings_by_period() themselves call)
    -- then summed across segments, instead of computing once per period
    against a blended rate.

    Restricted to the trailing `n_trailing` real statements, the SAME
    window floor_savings_by_period()'s own default resolves to, so this
    diagnostic covers exactly the periods the headline combined-savings
    figure actually uses. Gas Energy Charge and other_fees are flat
    (linear) rates with no tier interaction regardless of granularity, so
    this covers the Gas Service component only, which is where the whole
    effect lives (unchanged from the period-level version)."""
    periods = pd.read_csv(HPC.GAS_PERIODS_CSV)
    periods["statement_date"] = pd.to_datetime(periods["statement_date"]).dt.date
    periods[["period_start", "period_end"]] = periods["period"].str.split(
        " - ", expand=True).apply(lambda c: pd.to_datetime(c, format="%b %d, %Y").dt.date)
    periods = periods.sort_values("period_start").reset_index(drop=True)
    trailing = periods.tail(n_trailing).copy()

    gas_detail = HPC.load_gas_detail()
    hdd_by_day = iso["hdd_by_day"]
    total_hdd = iso["total_hdd"]
    ann_heat = iso["annual_heating_therms"]
    floor_per_day = iso["floor_therms_per_day"]
    gas_daily = iso.get("gas_daily")

    total_independent_sum, total_joint, rows = 0.0, 0.0, []
    for _, row in trailing.iterrows():
        start, end = row["period_start"], row["period_end"]
        context = f"{row['statement_date']} [{row['period']}]"
        therms = row["therms"]
        period_days = (end - start).days + 1

        detail = gas_detail.get(str(row["statement_date"]))
        if not detail or "gas_service" not in detail:
            raise SystemExit(
                f"all_electric_endgame.py: tier_interaction_overstatement "
                f"needs gas_service segment detail for {context} in "
                "bill_gas_detail.csv -- run parse_bills.py first")
        gs_segs = detail["gas_service"]
        gs_ranges = HPC._segment_day_ranges(
            start, end, [s["segment_days"] for s in gs_segs], f"{context} gas_service")

        # The SAME two day-level allocations floor_savings_by_period() and
        # heat_pump_conversion.gas_savings_by_period() each already compute
        # for their own headline figures -- not reimplemented, just run
        # side by side here so both can be projected onto the SAME Gas
        # Service segment boundaries below.
        floor_capped_days = _floor_capped_days(start, end, floor_per_day, therms)
        heating_capable_per_day = max(0.0, therms / period_days - floor_per_day)
        heat_capped_days = HPC._capacity_capped_days(
            start, end, hdd_by_day, total_hdd, ann_heat, heating_capable_per_day,
            floor_per_day, gas_daily, context)

        gs_floor_shares = HPC._segment_heat_from_days(gs_ranges, floor_capped_days)
        gs_heat_shares = HPC._segment_heat_from_days(gs_ranges, heat_capped_days)

        for (seg_start, seg_end), F_s, H_s, seg in zip(
                gs_ranges, gs_floor_shares, gs_heat_shares, gs_segs):
            if F_s <= 0.0 and H_s <= 0.0:
                continue  # nothing removed in this segment -- no interaction possible
            seg_days = (seg_end - seg_start).days + 1
            a_s = row["baseline_allowance_therms"] * seg_days / period_days
            seg_context = f"{context} gas_service segment {seg['segment']}"
            # t_s: gas_daily=None (day-proportion), NOT the real iso
            # gas_daily, even though heat_capped_days above used it. This
            # mirrors _floor_segment_total_therms()'s own established
            # convention (see that function's docstring): heat_pump_
            # conversion.gas_savings_by_period() can safely pass real
            # gas_daily for t_s because H_s > 0 there only on days gas.csv
            # is KNOWN to cover (_capacity_capped_days() already fails
            # closed otherwise) -- but F_s is NEVER exactly zero on any
            # real day, so F_s + H_s > 0 gives no such guarantee here, and
            # the real-daily path would fail closed on this household's own
            # trailing period whose early days (2025-06-27 on) precede
            # gas.csv's coverage start (2025-07-25). This is the SAME
            # supplementary-diagnostic precision tradeoff the retired
            # period-level version already made (it used bill_periods_
            # gas.csv's own values with no gas_daily at all); only the
            # SEGMENT granularity is new here, not full real-daily t_s.
            t_s = HPC._segment_real_or_proxy_therms(
                seg_start, seg_end, therms, period_days, None, F_s + H_s,
                context=seg_context)
            br = seg["baseline_rate"]
            nbr = seg["nonbaseline_rate"]
            # Priced by _priced_at_top_of_ladder() ITSELF (Codex silent-
            # failure-hunter, issue #20 round 6), not a second, locally
            # reimplemented bill(t) closure: a prior version of this
            # function built its own tiny bill(t) helper that fell back to
            # baseline_rate whenever nonbaseline_rate was missing, with NO
            # tolerance check and no fail-closed guard at all -- unlike
            # every other place in this module (and heat_pump_conversion.
            # _gas_service_segment_tier_cost() itself) that prices a missing
            # nonbaseline_rate. _priced_at_top_of_ladder() already IS
            # `bill(T) - bill(T-X)` for the top-of-ladder removal of X
            # therms (see its own docstring and the reviewer's hand-worked
            # test), so reusing it here directly gives this function the
            # SAME tolerance-then-fail-closed treatment for free, rather
            # than duplicating that logic a second time. marginal_therms is
            # clamped to `t_s` (matching the retired bill()'s own
            # max(0.0, t_s - X) clamp at the zero-remaining-therms end) so a
            # rare estimation-noise case where F_s, H_s, or F_s+H_s slightly
            # exceeds this segment's own day-proportion t_s still prices as
            # "remove everything", not an out-of-range marginal quantity.
            savings_f = _priced_at_top_of_ladder(
                total_therms=t_s, marginal_therms=min(F_s, t_s), baseline_allowance=a_s,
                baseline_rate=br, nonbaseline_rate=nbr, context=f"{seg_context} (F)")
            savings_h = _priced_at_top_of_ladder(
                total_therms=t_s, marginal_therms=min(H_s, t_s), baseline_allowance=a_s,
                baseline_rate=br, nonbaseline_rate=nbr, context=f"{seg_context} (H)")
            savings_joint = _priced_at_top_of_ladder(
                total_therms=t_s, marginal_therms=min(F_s + H_s, t_s), baseline_allowance=a_s,
                baseline_rate=br, nonbaseline_rate=nbr, context=f"{seg_context} (F+H)")
            independent_sum = savings_f + savings_h
            total_independent_sum += independent_sum
            total_joint += savings_joint
            gap = round(independent_sum - savings_joint, 2)
            if abs(gap) > 0.005:
                rows.append({"statement_date": str(row["statement_date"]),
                            "segment": int(seg["segment"]), "overstatement_usd": gap})
    overstatement = round(total_independent_sum - total_joint, 2)
    return {
        "gas_service_independent_sum_usd": round(total_independent_sum, 2),
        "gas_service_joint_removal_usd": round(total_joint, 2),
        "overstatement_usd": overstatement,
        "by_segment": rows,
        "note": ("Gas Service (tiered) component only, SEGMENT-level basis "
                "(Finding 3, Codex review pass, issue #20 round 4) -- see "
                "this function's own docstring for why period-blended rates "
                "were not the correct basis. Positive means the naive sum "
                "overstates the true joint savings; this is structurally "
                "guaranteed non-negative under a convex rate, per segment."),
    }


# ---------------------------------------------------------------------------
# AC3/AC7 -- sequencing and the two paybacks.
# ---------------------------------------------------------------------------
def sequencing_and_paybacks(fixed_charge_verdict_is_zero, wh_install_usd,
                            wh_annual_net_savings_usd, wh_payback_years,
                            furnace_install_usd, furnace_annual_net_savings_usd,
                            furnace_payback_years, tier_interaction,
                            electric_interaction):
    """AC3 (sequencing) + AC7 (two paybacks), reusing heat_pump_conversion.
    payback_and_npv() directly for both the combined-transition and the
    final-step-alone figures rather than reimplementing payback math.

    `tier_interaction` (tier_interaction_overstatement()'s own return
    value) corrects `complete_transition_payback`'s own combined savings
    for the tier-ladder interaction CLAUDE.md section 9 warns composite
    figures must not carry silently: summing the water heater's and the
    furnace's own INDEPENDENTLY-computed marginal GAS savings (each
    correct on its own -- see this module's top-of-file note -- for "this
    step alone") overstates the TRUE savings from doing both, since both
    reach into the same periods' nonbaseline tier.

    `electric_interaction` (electric_interaction_overstatement()'s own
    return value, Finding 1, Codex adversarial review, issue #20 round 2)
    corrects the SAME combined savings for the mirror-image problem on the
    ELECTRIC side: summing the water heater's and the furnace's own
    INDEPENDENTLY-rebilled electric-cost increases UNDERSTATES the true
    combined electric cost, since both independent rebills let their own
    added load claim the SAME exported solar kWh -- which cannot really
    happen once both loads are real. Both corrections are applied here,
    never left as a footnote next to an uncorrected headline number."""
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
    naive_combined_annual_net_savings = (wh_annual_net_savings_usd
                                         + furnace_annual_net_savings_usd
                                         + fixed_charge_release_usd)
    combined_annual_net_savings = round(
        naive_combined_annual_net_savings
        - tier_interaction["overstatement_usd"]
        - electric_interaction["overstatement_usd"], 2)
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
                  "per-meter charge would produce. This order is derived "
                  "from the 100%-water-heater-share basis alone -- see "
                  "this section's own share_robustness (added separately, "
                  "in build()) for whether it survives the water-heater-"
                  "share uncertainty water_heater_share_sensitivity "
                  "establishes."),
        "order": order_names,
        "last_step": last_step["name"],
        "fixed_charge_release_usd": fixed_charge_release_usd,
        "complete_transition_payback": {
            "combined_install_usd": combined_install,
            "combined_annual_net_savings_usd": combined_annual_net_savings,
            "naive_summed_annual_net_savings_usd": round(naive_combined_annual_net_savings, 2),
            "tier_interaction_overstatement_usd": tier_interaction["overstatement_usd"],
            "tier_interaction_note": (
                "combined_annual_net_savings_usd is the naive sum of each "
                "step's own independently-computed marginal GAS savings, "
                "minus BOTH the tier_interaction_overstatement_usd "
                "correction (this field) AND the electric_interaction_"
                "overstatement_usd correction (below) -- see tier_"
                "interaction_overstatement() and electric_interaction_"
                "overstatement() (CLAUDE.md section 9: composite figures "
                "must not silently add numbers from two separate models). "
                "naive_summed_annual_net_savings_usd is kept alongside for "
                "transparency, not used in the payback below."),
            "electric_interaction_overstatement_usd": electric_interaction["overstatement_usd"],
            "electric_interaction_note": (
                "combined_annual_net_savings_usd also subtracts this "
                "correction -- see electric_interaction_overstatement() "
                "(Finding 1, Codex adversarial review, issue #20 round 2): "
                "summing each step's own independently-rebilled electric-"
                "cost increase understates the true combined electric "
                "cost, since both conversions' independent rebills let "
                "their own added load claim the SAME exported solar kWh, "
                "which cannot really happen once both loads are real."),
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
        "not_verified_caveat": ("the water-heater half of both paybacks "
                                "above (complete_transition_payback and, "
                                "when the water heater is the last step, "
                                "final_step_alone_payback) is built on the "
                                "PURE 100%-water-heater assumption, NOT "
                                "VERIFIED against this household's own "
                                "actual fuel mix -- see water_heater_"
                                "conversion.water_heater_share_sensitivity "
                                "for the same figures at explicit, "
                                "illustrative water-heater-share scenarios "
                                "(not a proven bound -- see that section's "
                                "own basis)"),
    }


# ---------------------------------------------------------------------------
# build() / main()
# ---------------------------------------------------------------------------
def reconcile_unattributed_usd(trailing12_billed_usd, floor_savings_usd,
                               heating_savings_usd, tier_overstatement_usd):
    """The reconciliation.unattributed_heating_signal.unattributed_usd
    figure, factored out of build() into its own pure, directly-testable
    function (test-analyzer finding, issue #20 round 6 -- unlike its
    sibling corrections tier_interaction_overstatement() and electric_
    interaction_overstatement(), this arithmetic used to be inlined
    directly in build(), whose only guard was one archive-gated,
    end-to-end test that silently skips in CI when the private archive is
    absent).

    Naively summing floor_savings_usd and heating_savings_usd (each an
    INDEPENDENTLY-computed marginal gas saving, exactly as tier_
    interaction_overstatement()'s own docstring describes) and subtracting
    that sum from the trailing-12 billed total double-subtracts the same
    tier-interaction gap complete_transition_payback already corrects for:
    both marginal figures reach into the SAME nonbaseline-tier dollars
    whenever both apply. Adding tier_overstatement_usd back before
    subtracting from the billed total turns the naive (double-subtracted)
    residual into the correct one: billed total minus the TRUE joint gas
    savings from removing both the floor and the heating slice together,
    not minus the sum of two independently-priced marginal removals."""
    return round(
        trailing12_billed_usd - floor_savings_usd - heating_savings_usd
        + tier_overstatement_usd, 2)


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

    share_sensitivity = water_heater_share_sensitivity(
        iso, d, enumeration["third_end_use_gap"]["possible_dryer_pct_of_floor_range"],
        enumeration["third_end_use_gap"]["possible_cooking_pct_of_floor_range"],
        headline_uef)

    hpc_path = os.path.join(DATA, "heat_pump_conversion.json")
    if not os.path.exists(hpc_path):
        raise SystemExit(f"all_electric_endgame.py: {hpc_path} not found -- "
                         "run heat_pump_conversion.py first (issue #1/#109)")
    hpc_data = json.load(open(hpc_path))
    furnace_headline = hpc_data["payback"]["central_3.5"]
    furnace_install_usd = hpc_data["install_cost"]["standalone_usd"]
    furnace_payback_years = furnace_headline["standalone"]["payback_years"]
    # The furnace's OWN marginal-over-AC-replacement payback (a homeowner
    # already due for an AC replacement pays only the marginal cost of
    # upgrading it to a heat pump) -- used ONLY for sequencing_share_
    # robustness()'s own second, marginal-basis check below (Finding 4,
    # code-reviewer, issue #20 round 6), never for combined_install itself,
    # which stays on the standalone basis throughout (CLAUDE.md section 2).
    furnace_payback_years_marginal = furnace_headline["marginal_over_ac_replacement"]["payback_years"]

    interaction = tier_interaction_overstatement(iso)

    # Finding 1 (Codex adversarial review, issue #20 round 2): the electric
    # side is non-additive too, exactly mirroring the gas side above. Build
    # the SAME furnace added-load basis heat_pump_conversion.json's own
    # central_3.5 headline used (its own reconciled_heating_therms_yr, not
    # recomputed here) and the SAME water-heater added-load basis this
    # script's own headline UEF used, sum them, and rebill ONCE.
    #
    # Issue #127: furnace_iso must ALSO carry the SAME capacity-capped daily
    # heating shape heat_pump_conversion.json's own payback/electric_cost_
    # by_scenario figures were built from (issue #119) -- otherwise
    # joint_electric_cost_scenario()'s own furnace-added-load series places
    # the SAME physical furnace load on the older, uncapped day_hdd/total_hdd
    # shape while furnace_headline (below) reports the capped-shape figure,
    # mixing two bases for one quantity inside a single joint rebill.
    # Recompute the shape via the SAME HPC.gas_savings_by_period(iso) call
    # heat_pump_conversion.py's own build() makes (not reimplemented, reused
    # directly like every other HPC.* helper this file already calls), using
    # the SAME `iso` already loaded above. Both calls are deterministic
    # functions of the same real gas/weather data, so the recomputed total
    # must reproduce heat_pump_conversion.json's own committed reconciled
    # total exactly -- verified below, not assumed, so a private-data
    # refresh that regenerates one artifact but not the other fails closed
    # instead of silently mixing bases again.
    day_gas_rows, _, _, day_heat_therms = HPC.gas_savings_by_period(iso)
    day_reconciled_check = round(
        sum(r["heating_therms_attributed"] for r in day_gas_rows), 2)
    day_sum_tolerance = 0.005 * len(day_gas_rows) + 1e-6
    assert abs(day_reconciled_check - hpc_data["reconciled_heating_therms_yr"]) < day_sum_tolerance, (
        "all_electric_endgame.py: recomputed reconciled heating "
        f"({day_reconciled_check} therms) disagrees with heat_pump_"
        f"conversion.json's own committed reconciled_heating_therms_yr "
        f"({hpc_data['reconciled_heating_therms_yr']}, tolerance "
        f"{day_sum_tolerance:.4f}) by more than rounding-order noise can "
        "explain -- run heat_pump_conversion.py to regenerate that "
        "artifact from the same private data before this script")
    furnace_iso = {**iso, "annual_heating_therms": hpc_data["reconciled_heating_therms_yr"],
                   "capped_heat_by_day": day_heat_therms}
    furnace_cop = HPC.COP_SCENARIOS["central_3.5"]
    ann_wh_kwh_headline = (floor_therms_annual * HPC.KWH_PER_THERM * GAS_WH_UEF
                           / HPWH_UEF_SCENARIOS[headline_uef])
    joint_electric = joint_electric_cost_scenario(d, furnace_iso, furnace_cop, ann_wh_kwh_headline)
    electric_interaction = electric_interaction_overstatement(
        wh_electric_increase_usd=wh_headline["annual_electric_cost_increase_usd"],
        furnace_electric_increase_usd=furnace_headline["annual_electric_cost_increase_usd"],
        joint_electric_increase_usd=joint_electric["electric_cost_increase_usd"])

    sequencing = sequencing_and_paybacks(
        fixed_charge_verdict_is_zero=True,
        wh_install_usd=WH_INSTALL_COST_CENTRAL_USD,
        wh_annual_net_savings_usd=wh_headline["annual_net_savings_usd"],
        wh_payback_years=wh_headline["central_install"]["payback_years"],
        furnace_install_usd=furnace_install_usd,
        furnace_annual_net_savings_usd=furnace_headline["annual_net_savings_usd"],
        furnace_payback_years=furnace_payback_years,
        tier_interaction=interaction,
        electric_interaction=electric_interaction,
    )

    # Finding 2 (Codex `review` pass, issue #20 round 4): the sequencing
    # order above is derived from the 100%-water-heater-share basis alone
    # -- check whether it survives the SAME share scenarios water_heater_
    # share_sensitivity() already reports, and find the numeric crossover
    # share below which it would reverse.
    share_scenario_shares = {k: v["water_heater_share"]
                             for k, v in share_sensitivity["scenarios"].items()}
    sequencing["share_robustness"] = sequencing_share_robustness(
        iso, d, headline_uef, share_scenario_shares,
        furnace_install_usd=furnace_install_usd,
        furnace_annual_net_savings_usd=furnace_headline["annual_net_savings_usd"],
        furnace_payback_years=furnace_payback_years,
        tier_interaction=interaction,
        electric_interaction=electric_interaction,
        furnace_payback_years_marginal=furnace_payback_years_marginal,
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
    unattributed_usd = reconcile_unattributed_usd(
        trailing12_billed_total, floor_savings_annual, hpc_data["gas_savings_annual_usd"],
        interaction["overstatement_usd"])

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
                     "total against the TRUE joint gas savings from removing "
                     "both the floor and the heating slice together, not the "
                     "naive sum of the two steps' own independently-computed "
                     "marginal savings"),
            "unattributed_therms_yr": unattributed_therms,
            "trailing_12_billed_total_usd": trailing12_billed_total,
            "floor_savings_usd": floor_savings_annual,
            "heating_savings_usd": hpc_data["gas_savings_annual_usd"],
            "tier_interaction_overstatement_usd": interaction["overstatement_usd"],
            "tier_interaction_correction_note": (
                "unattributed_usd is trailing_12_billed_total_usd minus "
                "floor_savings_usd minus heating_savings_usd PLUS this "
                "tier_interaction_overstatement_usd (Finding 1, Codex review "
                "pass, issue #20 round 3): floor_savings_usd and "
                "heating_savings_usd are each computed as if it alone were "
                "removed from the original bill (tier_interaction_"
                "overstatement()'s own docstring), so naively subtracting "
                "both from the billed total double-subtracts the shared "
                "nonbaseline-tier dollars they both reach into -- the same "
                "overstatement complete_transition_payback already corrects "
                "for (sequencing_and_paybacks). Adding it back here turns "
                "the naive, double-subtracted residual into the TRUE "
                "unattributed gap: billed total minus the joint (not summed) "
                "gas savings from removing both."),
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
            "not_verified_caveat": (
                "payback below (and every figure derived from it, including "
                "sequencing_and_paybacks) is the PURE 100%-water-heater "
                "computation and is NOT VERIFIED against this household's "
                "own actual appliance fuel mix, which is not determined "
                "(see cooking_fuel_evidence). See water_heater_share_"
                "sensitivity for the same figures propagated across "
                "explicit, illustrative water-heater-share scenarios (NOT "
                "a proven bound -- the true share could be lower still) "
                "rather than asserting this one as the true answer -- "
                "CLAUDE.md section 0 (no false precision on an unverified "
                "input)."),
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
            "water_heater_share_sensitivity": share_sensitivity,
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
        "joint_electric_cost_scenario": joint_electric,
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
