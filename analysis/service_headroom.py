#!/usr/bin/env python3
"""Electrical service headroom from measured demand (NEC 220.87).

The question this answers: with a 175 A service already carrying a house, an EV
charger and a 10 kW PV array, is there room for a heat pump, a second EV charger,
or both -- and what happens if a battery inverter is added on top.

NEC 220.87 (2020) lets an existing dwelling's calculated load be taken as the
ACTUAL maximum demand times 125%, in place of a nameplate 220.82/220.83
calculation, where its three conditions hold. Condition (1) is that the maximum
demand data is available for a 1-YEAR period; this window is 395 days, so the
household qualifies under (1) itself. The 30-day continuously-recorded route
often quoted as "the code minimum" is not condition (1) -- it is the EXCEPTION
to it, the fallback where a year is not available, and it closes with "This
exception shall not apply if the feeder or service has a renewable energy
system (i.e., solar photovoltaic or wind electric) or employs any form of peak
load shaving." This service has ~9.45 kW of PV on it, so that route was never
open to it. Qualifying under (1) rather than under an Exception the service
cannot use is a strengthening of the evidence, and the artifact says so.
Condition (2) is the arithmetic this module computes; condition (3) -- feeder
overcurrent protection and service overload protection -- is NOT verified here
and is published as not determined.

Every NEC section number this module publishes is declared once in NEC_RULES
with the rule it stands for, and every citation is built from that table.

SCOPING ESTIMATE ONLY -- THE PERMIT CALCULATION BELONGS TO A LICENSED
ELECTRICIAN. Approved means acceptable to the authority having jurisdiction
(Article 100), so whether measured-demand data is accepted at all is the AHJ's
call; the AHJ may instead require a full 220.82 or 220.83 nameplate
calculation, and only a licensed electrician working from the actual equipment
nameplates, conductor sizes and the panel's own labelling can produce a number
that will be permitted. Nothing here is a design, and no equipment should be
ordered against it.

The measurement problem, and how it is handled
----------------------------------------------
The revenue meter records NET flows, not load. With PV on the roof the metered
import understates true household demand whenever the sun is up, so a maximum
taken straight off the meter would be dangerously optimistic. Gross load is

    gross = import + pv_consumed_on_site = import - export + pv

and `pv` is not metered at 15-minute resolution here. Three instruments are
combined instead:

  * the SDG&E revenue meter, 15-minute import and export (the only 15-minute
    series);
  * the Enphase consumption CT (`enphase_sam8760_*.csv`), an INDEPENDENT
    whole-home GROSS load reading, hourly. Its alignment is evidence, not
    assumption: dark-hour (00-04) correlation against metered net import is
    0.995 at zero offset and collapses at +/-1 h, so row 0 is 00:00 local wall
    clock of the file's year, and no timezone conversion is applied;
  * the Enphase production CT via `data/threeway_production_validation.csv`,
    used only to check the reconstruction.

Hourly PV is then derived, `pv_hour = max(sam_hour - import_hour + export_hour, 0)`,
and each 15-minute interval is reported as a BOUND rather than a point:

    lower = import                      (all PV exported, none self-consumed)
    upper = import - export + pv_up

`pv_up` is `min(pv_hour, kw_ac * 0.25)` on the hours the Enphase file covers
and `kw_ac * 0.25` on the hours it does not. The asymmetry is deliberate: a
covered hour has a MEASUREMENT of itself and may be narrowed by it, while an
uncovered hour has only the physical cap. The largest production previously
observed at the same hour of day is not a ceiling on an uncovered hour -- that
hour can legitimately beat every one seen before it -- so it narrows nothing,
and the uncovered intervals carry the full nameplate cap. Which basis each
interval took is counted in the artifact rather than implied.

`kw_ac` is the array's INVERTER AC NAMEPLATE, read from intake
(`solar.kw_ac`). It is a physical ceiling: the array cannot put more onto the
service than its inverters are rated to deliver, so a quarter-hour can carry at
most a quarter of it. The largest observed hourly production is NOT used for
this -- a single quarter-hour can legitimately exceed a quarter of the best full
hour, through intra-hour ramps and cloud-edge lensing, so an empirical maximum
would produce an "upper bound" capable of sitting below the true gross demand.
The empirical maxima (largest derived hourly PV, largest 15-minute meter export,
largest 5-minute inverter output) are kept as CORROBORATION that the nameplate
is not contradicted, and any one of them exceeding the nameplate stops the run
as a data-integrity failure. With `solar.kw_ac` absent there is no physical
ceiling and the run stops: an envelope built on the empirical maximum is not a
bound, and every ampacity verdict here rests on it being one.

The bound collapses to a point -- gross is EXACTLY the metered import -- wherever
the containing hour produced no PV and the interval exported nothing. Whether
the annual maximum is one of those is a fact about the peak interval, so it is
read off that interval and published (`maximum_demand.basis`,
`peak_coincident.point_determined`) rather than asserted about the method or
about a top-N of the distribution. On this window it is: the peak interval
exported nothing and its hour produced nothing. Elsewhere the reconstruction is
genuinely a bound and is reported as one; the width is stated rather than
papered over.

The headline maximum demand is the measured one and stays that way. The upper
bound is loose by construction, for either of two reasons: inside a producing
hour it credits that whole hour's measured production to one quarter-hour, and
on an hour the Enphase file does not cover it carries the bare nameplate cap.
A PASS/FAIL verdict taken off the measured basis alone would assert more than
the data supports wherever the answer flips inside the disclosed width. Every
case verdict is therefore THREE-VALUED, computed on both bases: `pass` where the
case fits even on the conservative upper-bound reconstruction, `fail` where it
does not fit even on the point-determined measured maximum, and `not_determined`
where it fits on one and not the other.

WHICH of the two loosenesses sets the conservative basis is computed, not
assumed -- `binding_upper_interval()` reports the interval that produces
max_upper_bound_kw and the ceiling it took -- because what would settle a
`not_determined` case depends on it. Where the binding interval sits inside a
covered hour, 15-minute production data (never metered here) would settle it.
Where it sits in an hour the production export does not cover, 15-minute data
would settle nothing: there is no measurement of that hour at any resolution,
and what is needed is a consumption-CT export pulled through the end of the
meter window. The artifact names the right remedy rather than the usual one,
and it names the binding interval beside it.

The gap between the end of the production record and the end of the meter
window is therefore a gated quantity, not a footnote: `coverage_lag()` publishes
it in hours and intervals and stops the run past
ENPHASE_COVERAGE_MAX_LAG_HOURS, because past that the conservative basis every
verdict rests on is being set by a lengthening stretch of unmeasured hours. The
window is never shortened to make the gate pass -- that would delete real
metered demand from a maximum-demand study.

Two joint-computation hazards are handled explicitly.

  DST. The Enphase file is a flat 8760-row grid; the meter is true wall clock
  (100 intervals on the fall-back Sunday, 92 on spring-forward, per
  rates.expected_day_hours). The two disagree only on those two days -- the
  Enphase file literally repeats one value at 01:00 and 02:00 of the fall-back
  day -- so those dates are excluded from every meter x Enphase computation and
  their intervals take the nameplate PV cap. They are NOT excluded from the
  maximum-demand search, which uses the meter alone.

  Zero padding. The current-year Enphase export is a full 8760 rows with the
  future zero-filled. The zero tail is truncated at the last nonzero row and
  never treated as measurement; treating it as data would invent hours of zero
  household load. Those intervals take the nameplate PV cap too.

Hourly aggregation divides by the interval count actually present, never by a
hard-coded four. A naive `groupby(date, hour).sum()` reports a phantom 21.4 kW
on the fall-back Sunday, because that hour carries eight intervals covering two
real hours.

What is computed
----------------
  1. the 15-minute gross-load envelope and its energy-conservation residual
     against the two independent production references, gated: the residual,
     the day-by-day error, the correlation and the overlap length are each
     checked against a stated threshold, and a breach stops the run instead of
     publishing a reassuring sentence the numbers did not produce. The two
     instrument-consistency claims about the peak -- the Enphase hourly maximum
     against the top of the envelope (physics) and against the headline
     15-minute maximum (a publication precondition, since every headroom figure
     rests on it) -- are enforced the same way;
  2. maximum demand for the whole window and per calendar month, with the
     timestamp and coincident conditions of the annual peak;
  3. the 220.87 arithmetic step by step, against both the 175 A main breaker and
     the tighter 170 A continuous rating of the meter socket;
  4. headroom for four cases -- heat pump only, second EVSE only, both, and both
     plus a battery inverter -- with the heat pump left SOLVED-FOR (the largest
     MCA that fits) because no unit has been selected and inventing a nameplate
     would violate the evidence rule;
  5. the NEC 705.12(B)(3) busbar checks, which are what actually decide the
     battery case, and the panel's remaining physical spaces, which can veto a
     result that passes every ampacity test but can never confirm one, because
     adjacency is not in the schedule;
  6. load-management mitigations with their demand reduction computed.

705.12(B)(3)(2) is TWO conjunctive conditions, not one. The 120% arithmetic is
the first; the second is that the backfeed breaker sits at the opposite end of
the busbar from the main supply. Both are evaluated, and the position condition
fails closed: with no recorded position for the backfeed breaker or for the
main, it reports `not_determined`, and the arithmetic alone never reads as a
compliant verdict.

The position condition is asked about ONE breaker at a time, and the battery's
breaker is not the PV's. The existing PV backfeed breaker's end
(`panel.pv_breaker_position`) is a fact about the device already installed; it
says nothing about where a NEW 2-pole source breaker could physically go. The
proposed battery breaker therefore carries its own intake field
(`panel.battery_breaker_position`), and with no surveyed position for it the
battery's position leg is `not_determined` on that ground alone. The existing
PV breaker's own condition is still reported, labelled as being about the
installed source.

Whether the question is asked at all
------------------------------------
`household.has_new_load_interest` is the intake APPLICABILITY flag for this
analysis, and the whole `panel:` block is tagged `required_if:
has_new_load_interest`. It is read FIRST, before any input is opened, and it
follows the same contract `household.has_ev` and `household.has_gas` carry:

  * explicitly false -- the household is not asking whether its service has
    room for a new load. The artifact is a not_applicable stub naming the flag
    and what to set to enable the analysis; no panel field, no interval export
    and no Enphase file is read;
  * true or ABSENT -- the analysis runs exactly as it always has, including the
    fail-closed stop on a missing `panel.service_rating_a`. Absence is not read
    as false: the flag postdates some intake files, and a household that asks
    for this answer without supplying the panel must stop rather than be
    quietly excused.

A `panel:` block present under a false flag is NOT treated as a contradiction,
which is where this differs from behavior_rebuild.py's charger.kw check. A
charger is an EV; a panel survey is a description of the house that anyone may
have on file, and wanting no new-load answer is compatible with having one.
Checking for it would also mean reading the panel intake the false flag says
not to read.

`household.has_ev` is a SECOND applicability flag, and it gates a subset of this
analysis rather than the whole of it. Wanting a new-load answer and having no EV
is an ordinary combination -- somebody scoping a heat pump -- so the two flags
are independent, and `has_ev: false` with `has_new_load_interest: true` runs.
It carries the same contract as the flag above: only an explicit false disables,
absence is not false. With an explicit false:

  * `charger.kw` is NOT read. There is no home charger to record, and the
    intake contract lets a household with no EV omit the `charger:` block
    entirely, so reading it would fail closed on a key the contract says may be
    missing;
  * every scenario and mitigation about a SECOND charger is reported not
    applicable, naming the flag, instead of being computed from a figure the
    intake does not carry or from a default nobody supplied;
  * the heat-pump and battery scenarios still run. They do not depend on the
    EVSE term, and the 120% busbar analysis never did.

What is skipped, and why, is published in the artifact's
`scenarios_not_applicable` list rather than left to be inferred from a missing
key. The list is present either way and is empty where nothing was skipped.

Panel facts (service rating, busbar rating, spaces, max circuits, the device
schedule) are private-tier intake fields read through household.get(), which
fails closed. There are no defaults: a missing service rating stops the run
rather than producing a plausible-looking answer about a panel nobody measured.
Two fields are OPTIONAL because the intake contract documents them as nullable,
and each has a defined meaning when absent:

  * `panel.pv_backfeed_a` -- an explicit null means the panel was surveyed and
    nothing backfeeds it, so the existing backfeed is a KNOWN 0 A. That is an
    answer, and it is kept distinct from the key being ABSENT, which is the
    question never having been asked. Both put 0 A into the arithmetic; only
    the absent one leaves the allowance an upper bound and the ampacity leg
    undecided. The artifact names which of the three it is;
  * `panel.meter_socket_continuous_a` -- an explicit null means the socket was
    read and carries no printed continuous rating (a separate meter enclosure),
    so the constraint does not apply and the service rating is the only ampacity
    constraint. The socket is then omitted from the 220.87 steps and from every
    headroom, never carried as a null in arithmetic. An ABSENT key is not that:
    nobody looked, and since the socket is the TIGHTER of the two constraints
    wherever it exists, dropping it would delete the binding constraint and
    inflate every headroom. Step 4 is then emitted as `not_determined` and every
    binding figure is labelled an upper limit.

Both nullable panel fields therefore have three states, not two, and the
artifact names which one each is in. The rule is one rule: an unanswered
question never gets to look like an answer, and it especially never gets to
look like the answer that makes the result more permissive.

`panel.pv_breaker_position`, `panel.battery_breaker_position` and
`panel.main_breaker_position` are optional too; their absence is what makes the
position condition `not_determined`.

`solar.kw_ac` is REQUIRED and has no default, for the reason given above.

Every intake value that survives to the arithmetic is then checked against its
physical domain (validate_panel, panel_occupancy). Being present is not the same
as being possible, and the busbar formula turns an impossible value into a
plausible answer rather than an obviously wrong one: a NEGATIVE
`panel.pv_backfeed_a` enlarges the remaining allowance and can turn a failing
panel into a passing one. A main larger than the busbar it feeds, a non-positive
rating or space count, fewer pole positions than spaces, or a schedule that
fills more of the enclosure than the enclosure has -- each stops the run naming
the field and the value.

Nothing here publishes a verdict the data does not settle
-------------------------------------------------------
The same defect kept reappearing at different exits: a pass/fail asserted from
one basis, from an assumed zero, or from a count that cannot see what the rule
asks about. Every judgement in this artifact is therefore either three-valued
with an explicit `not_determined` and what would settle it, or a direct
restatement of something measured. The three-valued ones:

  * `cases[].ampacity_verdict` -- computed on the measured AND the conservative
    basis, `not_determined` where the answer sits inside the reconstruction's
    disclosed width;
  * `cases[].spaces.physical_fit` -- a 240 V circuit needs two ADJACENT
    full-size spaces, and the schedule records devices, not slot positions. Too
    few free spaces is a fail on the count alone; enough free spaces is
    `not_determined`, never a fit;
  * the battery's `ampacity_leg` -- where the existing backfeed was never
    recorded the 120% allowance is computed at 0 A, which is the largest it
    could be, so a shortfall is real and a fit is `not_determined`. A recorded
    rating, or an explicit null meaning the panel was surveyed and nothing
    backfeeds it, both make the allowance complete and the leg decidable;
  * the battery's `position_leg` -- fails closed on an unsurveyed breaker end;
  * the battery's `sum_rule` -- the recorded schedule can only understate the
    true sum of overcurrent devices, so a sum already over the busbar rating
    fails and a sum under it is `not_determined`.

The battery's demand-side figure is the one assumption left standing, and it is
labelled rather than hidden: research/battery-research-notes.md records a single
11.5 kW continuous power rating for the Powerwall 3 with no split between charge
and discharge, so applying it to grid charging is a conservative stand-in for a
charge-input specification this project does not have. A unit whose AC charge
input is below its discharge output would draw less. What would settle it is the
selected unit's own nameplate; no figure is invented here in either direction.

Run from anywhere in the checkout:  ./.venv/bin/python analysis/service_headroom.py
Writes data/service_headroom.json atomically.
"""
import collections
import csv
import datetime as dt
import json
import os
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import household as HH
import rates as R

# ---------------------------------------------------------------------------
# NEC citations, declared once.
#
# Every section number this module publishes is a key here, with the rule it
# stands for, and every published citation is BUILT from this table by nec() or
# nec_rule(). Nothing is typed inline. A review found three misnumbered
# citations scattered across prose -- the sum-of-all-OCPDs rule cited as
# (B)(3)(1), the MCA marking cited as 440.6, and the 30-day recording route
# cited as the body of 220.87 rather than as the Exception this service may not
# use -- and each had to be hunted for at several sites. With one table a
# misnumbering is a one-line fix and a test failure.
#
# The EDITION is named because the numbering and the wording both moved: in the
# 2020 edition the sum-of-all-overcurrent-devices rule is 705.12(B)(3)(3), and
# the 120% rule's own text now reads on 125 percent of source output current
# rather than on the source breaker's rating.
# ---------------------------------------------------------------------------
NEC_EDITION = "2020"

NEC_RULES = {
    "100": (
        "Article 100, definition of Approved: acceptable to the authority "
        "having jurisdiction. It is a general principle of the Code, not a "
        "sentence of any one article"),
    "220.60": (
        "Noncoincident loads. Where it is unlikely that two or more "
        "noncoincident loads will be in use simultaneously, it is permissible "
        "to use only the largest load(s) that will be used at one time in "
        "calculating the total load of a feeder or service. If a motor or "
        "air-conditioning load is part of the noncoincident load and is not "
        "the largest of the noncoincident loads, 125 percent of either the "
        "motor load or the air-conditioning load, whichever is larger, shall "
        "be used in the calculation"),
    "220.82": "Optional calculation for a dwelling unit",
    "220.83": (
        "Optional calculation for an existing dwelling unit adding loads"),
    "220.87": (
        "Determining existing loads. The calculated load for an existing "
        "service or feeder is permitted to be taken from measured maximum "
        "demand where conditions (1), (2) and (3) are all met"),
    "220.87(1)": "The maximum demand data is available for a 1-year period",
    "220.87(1) Exception": (
        "Where 1-year maximum demand data is not available, the calculated "
        "load may instead rest on maximum demand continuously recorded over a "
        "minimum 30-day period with a recording ammeter or power meter, taken "
        "while the building is occupied and including the larger of the "
        "heating or cooling load. The exception closes: 'This exception shall "
        "not apply if the feeder or service has a renewable energy system "
        "(i.e., solar photovoltaic or wind electric) or employs any form of "
        "peak load shaving'"),
    "220.87(2)": (
        "The maximum demand at 125 percent plus the new load does not exceed "
        "the ampacity of the feeder or rating of the service"),
    "220.87(3)": (
        "The feeder has overcurrent protection in accordance with 240.4, and "
        "the service has overload protection in accordance with 230.90"),
    "230.90": "Overload protection for service conductors",
    "240.4": "Protection of conductors",
    "240.6(A)": (
        "Standard ampere ratings for fuses and inverse time circuit breakers"),
    "440.4(B)": (
        "Nameplate marking for air-conditioning and refrigerating equipment: "
        "the equipment is marked with its minimum circuit ampacity (MCA), "
        "which already embeds the 125 percent on the largest motor"),
    "440.35": (
        "Conductors for room air conditioners are sized to the marked minimum "
        "circuit ampacity"),
    "625.42": (
        "Electric vehicle supply equipment rating. EV charging loads are "
        "continuous loads, so the code value is 125 percent of rated output; "
        "where an EVSE load management system is used, the load on the service "
        "or feeder is the maximum the system permits rather than the sum of "
        "the connectors"),
    "705.11": (
        "Supply-side (line-side) source connection ahead of the service "
        "disconnecting means"),
    "705.12(B)(3)": (
        "Busbars. The numbered items under it are alternative compliance paths "
        "for connecting a power source to a panelboard busbar; each is a "
        "different test, and citing one for another's arithmetic names the "
        "wrong rule"),
    "705.12(B)(3)(2)": (
        "Where the sources are at the opposite end of the busbar from the "
        "primary supply, 'the sum of 125 percent of the power-source(s) output "
        "circuit current and the rating of the overcurrent device protecting "
        "the busbar shall not exceed 120 percent of the ampacity of the "
        "busbar'. The opposite-end position is part of the rule, not a "
        "commentary on it"),
    "705.12(B)(3)(3)": (
        "The sum of the ampere ratings of all overcurrent devices on the "
        "panelboard, excluding the device protecting the busbar, shall not "
        "exceed the ampacity of the busbar"),
    "705.13": (
        "Power control systems: a listed PCS limits the current on the "
        "conductors and busbars it controls, and the interconnection is "
        "evaluated against the PCS setting"),
}


def nec(section):
    """'NEC 705.12(B)(3)(3)' -- a citation, from the table.

    A section this module has not declared cannot be published: the whole point
    of the table is that the number and the rule it stands for travel together
    and are written down once.
    """
    if section not in NEC_RULES:
        raise SystemExit(f"service_headroom.py: {section!r} is not a declared "
                         f"NEC citation; add it to NEC_RULES with the rule it "
                         f"stands for. Declared: {sorted(NEC_RULES)}")
    return f"NEC {section}"


def nec_rule(section):
    """'NEC 220.87(1) -- <the rule it stands for>', built from the table."""
    return f"{nec(section)} -- {NEC_RULES[section]}"


# main()'s summary line names the method, and its local `nec` is the artifact's
# own section rather than this module's citation helper -- so the citation is
# taken once, here.
NEC_220_87_LABEL = nec("220.87")


# ---------------------------------------------------------------------------
# Code and equipment constants. Each carries its source; none is a guess.
# ---------------------------------------------------------------------------

# 120/240 V single-phase 3-wire residential service, the standard US residential
# configuration. Amps at the service disconnect are taken across the 240 V legs:
# A = kW * 1000 / 240.
#
# The meter's own class is NOT quoted here or in the artifact. It is a
# private-only intake field (DATA-SOURCES-CHEATSHEET.md, panel_meter_class):
# read off one installed meter alongside its form, model and AMI type, and
# CLAUDE.md §4 keeps a private-only answer out of every committed artifact and
# script. It settles nothing here either -- a meter class is a socket rating,
# and the socket rating this analysis binds on is panel.meter_socket_continuous_a.
SERVICE_VOLTAGE_V = 240.0

# NEC 220.87(2): the calculated load is the measured maximum demand x 125%.
NEC_220_87_FACTOR = 1.25

# NEC 220.87(1): the maximum demand data must be available for a 1-YEAR period.
# That is the condition this household qualifies under, and 365 days is the
# figure every margin below is measured against.
#
# The 30-day continuously-recorded route is NOT this condition. It is the
# Exception to (1) -- the fallback where a year is not available -- and it ends
# "This exception shall not apply if the feeder or service has a renewable
# energy system (i.e., solar photovoltaic or wind electric) or employs any form
# of peak load shaving." This service has PV on it, so the Exception is
# unavailable to it whatever the window length. Quoting 30 days as "the code
# minimum" here was wrong twice over: the applicable minimum is a year, and the
# 30-day route was never open to this service in the first place.
NEC_220_87_CONDITION_1_DAYS = 365

# NEC 625.42: EV supply equipment is a CONTINUOUS load, so its code-required
# value is 125% of its rated output.
NEC_625_42_FACTOR = 1.25

# NEC 705.12(B)(3)(2): busbar rating x 120% minus the main OCPD rating bounds
# the backfeed a panel may accept.
NEC_705_12_BUSBAR_FACTOR = 1.20

# The rule's own multiplier on a source's OUTPUT CIRCUIT CURRENT. The 2020 text
# counts 125% of source output current where this analysis counts the source
# breaker's rating; both figures are published side by side so the direction of
# the difference is visible rather than accidental. See source_current_basis().
NEC_705_12_SOURCE_FACTOR = 1.25

# Continuous-load derating: a breaker may carry 80% of its rating continuously,
# which is why a 60 A circuit hosts a 48 A continuous EVSE output.
CONTINUOUS_DERATE = 0.80

# Tesla Wall Connector (Gen 3) selectable output currents, amps. The existing
# unit runs at 48 A on a 60 A 2-pole circuit; a second unit could be set lower.
WALL_CONNECTOR_OUTPUTS_A = (12.0, 16.0, 20.0, 24.0, 32.0, 40.0, 48.0)
EXISTING_EVSE_OUTPUT_A = 48.0

# The repo's canonical battery is the Tesla Powerwall 3 at 11.5 kW -- the same
# unit battery_dispatch_policies.py and battery_plan_matrix.py model. Kept here
# as one number so the hardware cannot fork between scripts.
#
# research/battery-research-notes.md is the only place in this project that
# records a power rating for the unit, and it records ONE: 11.5 kW of
# "continuous power", with no split between what the unit can deliver and what
# it draws while charging. Everything below that uses 11.5 kW as a CHARGING draw
# is therefore applying that single figure to the demand side as an assumption,
# and says so where it is published. See BATTERY_CHARGING_BASIS.
BATTERY_INVERTER_KW = 11.5

# The hours a residential condenser works hardest, used only to READ the annual
# maximum -- whether it looks cooling-shaped or not. Nothing computed anywhere
# depends on the boundary, and the window is published beside the reading so a
# reader can disagree with it instead of guessing what was assumed.
COOLING_HOURS = range(11, 21)

# Standard branch-circuit ampere ratings, NEC 240.6(A), used to size a circuit
# from a continuous output; picked as the smallest standard rating that carries
# the output at 80%.
STANDARD_OCPD_A = (15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 60.0,
                   70.0, 80.0, 90.0, 100.0)

# ---------------------------------------------------------------------------
# Energy-conservation gates (AC-1). Each threshold is set well outside what this
# dataset actually produces, so a gate catches an instrument problem rather than
# ordinary instrument disagreement. The observed values on the committed window
# are quoted with each one so the margin is visible instead of implied, and each
# names the failure it exists to catch. A breach stops the run: the artifact is
# written atomically, so a failed run publishes nothing.
# ---------------------------------------------------------------------------

# At least 90 overlapping days. A conservation check resting on a handful of
# days is not evidence about the year the demand figure is taken from; 90 days
# is a full season. Observed: 363. Catches a reference export that only partly
# overlaps the meter window -- a truncated pull, or the wrong year -- which
# shrinks the comparison to a handful of days while every ratio still looks
# respectable.
CONSERVATION_MIN_DAYS = 90

# |residual| <= 5% per reference. Observed: 0.323% against the Enphase
# production CT and 1.69% against PVOutput, while the two references disagree
# with EACH OTHER by 2.05%. Five percent is more than double the worse residual
# and more than double the instruments' own spread, and far below the smallest
# error worth catching: a DC-for-AC substitution (15-20% on this array), a unit
# or scale error, or a reference series from the wrong year.
CONSERVATION_MAX_ABS_RESIDUAL_PCT = 5.0

# Mean absolute error <= 5 kWh/day. Mean production here is about 45 kWh/day, so
# this is roughly 11% of a typical day against 0.16 and 0.79 observed. This is
# the gate a whole-series time shift trips: shifting by a day leaves the annual
# total, and therefore the ratio, almost unchanged while the day-by-day error
# jumps by an order of magnitude.
CONSERVATION_MAX_MAE_KWH_PER_DAY = 5.0

# Correlation >= 0.95. Daily production is weather-driven and both references
# track the reconstruction at r >= 0.9998. A stale, shifted or partially
# overlapping series loses that day-shape structure long before it loses its
# total, so correlation catches what the ratio cannot.
CONSERVATION_MIN_CORRELATION = 0.95

# How far the meter window may run past the last hour the Enphase consumption-CT
# export covers. Every interval in that lag has NO production measurement behind
# it and takes the bare nameplate PV cap, and on this window one of them is what
# sets the conservative basis -- so the lag is not a detail, it is the thing the
# conservative verdicts are computed on.
#
# Seven days, and the reason is the pull, not the physics: the two exports are
# meant to come out of one data-gathering session, and the Enphase file
# ordinarily stops a few hours to a couple of days short of the meter's last
# interval because the two systems publish on different lags. Observed here:
# 64.75 h (2.7 days, 256 intervals, 0.67% of the window). A week is 2.6x that
# and still under 2% of a year-long window. Past it, the Enphase export is a
# STALE pull left in place beside a fresh meter export -- the failure this gate
# exists to catch -- and a growing tail of the record is bounded by the
# nameplate alone. The run then stops rather than publishing a conservative
# basis set by a lengthening stretch of unmeasured hours. Nothing is truncated
# to make the gate pass: shortening the window to hide the gap would delete real
# metered demand from a maximum-demand study.
ENPHASE_COVERAGE_MAX_LAG_HOURS = 168.0

CAVEAT = (
    "Scoping estimate only. The permit calculation belongs to a licensed "
    f"electrician. {nec('220.87')} permits an existing dwelling's calculated "
    "load to be taken from measured maximum demand where its three conditions "
    "are met; whether the data satisfies the authority having jurisdiction is "
    "the AHJ's call, since Approved means acceptable to the AHJ "
    f"({nec('100')}), and the AHJ may instead require a full "
    f"{nec('220.82')}/{nec('220.83')} nameplate calculation. Condition (3) -- "
    f"feeder overcurrent protection to {nec('240.4')} and service overload "
    f"protection to {nec('230.90')} -- is not verified here. Conductor sizes, "
    "the actual nameplate "
    "ratings of the existing equipment, and the panel's listing restrictions "
    "were not verified either, and no equipment should be ordered against "
    "these figures.")


def _repo_root():
    """Walk up for the repo root, so the script runs from any working directory
    (the private/verify sandbox pattern in CLAUDE.md relies on this)."""
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / "data").is_dir() and (parent / "analysis").is_dir():
            return parent
    raise SystemExit("service_headroom.py: could not locate the repo root")


ROOT = _repo_root()
RAW_DIR = ROOT / "private" / "1-raw-data"
OUT = ROOT / "data" / "service_headroom.json"
THREEWAY = ROOT / "data" / "threeway_production_validation.csv"
PVOUTPUT_5MIN = ROOT / "data" / "pvoutput_5min_sample.csv"


def _r(x, n=4):
    """Round for the artifact. Every float written out goes through this, so a
    regeneration cannot differ in the last bit of a division."""
    return round(float(x), n)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def only_match(pattern, what):
    """The single file matching `pattern`, or a hard stop.

    Taking glob()[0] is how a second export sitting in the directory silently
    changes which year the answer describes. Exactly one match, or refuse.
    """
    hits = sorted(RAW_DIR.glob(pattern))
    if len(hits) != 1:
        raise SystemExit(
            f"service_headroom.py: expected exactly one {what} matching "
            f"{pattern} in {RAW_DIR}, found {len(hits)}"
            + (": " + ", ".join(p.name for p in hits) if hits else ""))
    return hits[0]


def load_intervals(path):
    """[(date, hour_frac, consumption_kwh, generation_kwh)] from a Green Button
    export.

    The export is local wall-clock time with real DST days (a 25-hour day
    carries 100 intervals, a 23-hour day 92), so the printed hour is used
    as-is. No timezone conversion is applied or wanted -- the same rule
    tou_audit.load_intervals follows.

    Nothing above the data header row is read into memory beyond finding that
    row: the header block carries the customer name, service address, account
    and meter numbers, and none of it may reach a committed artifact.
    """
    rows, started = [], False
    with open(path, newline="") as fh:
        for rec in csv.reader(fh):
            if not rec:
                continue
            if not started:
                started = (rec[0].strip() == "Meter Number" and len(rec) > 4
                           and rec[1].strip() == "Date")
                continue
            if len(rec) < 7:
                continue
            d = dt.datetime.strptime(rec[1].strip(), "%m/%d/%Y").date()
            t = dt.datetime.strptime(rec[2].strip(), "%I:%M %p")
            rows.append((d, t.hour + t.minute / 60.0,
                         float(rec[4]), float(rec[5])))
    if not rows:
        raise SystemExit(f"service_headroom.py: no interval rows parsed from "
                         f"{path.name} -- is this a Green Button 15-minute export?")
    return rows


def truncate_sam_padding(values):
    """(real_values, padded_rows) for one Enphase 8760 export.

    The current-year file is emitted as a full year with every future hour
    zero-filled. Those zeros are not measurements -- a house with a refrigerator
    and a network never draws exactly 0.000 kWh in an hour -- and treating them
    as data would drag every mean down and invent a zero-load period. Truncate
    at the last nonzero row.
    """
    last = -1
    for i, v in enumerate(values):
        if v != 0.0:
            last = i
    if last < 0:
        raise SystemExit("service_headroom.py: an Enphase 8760 export is all "
                         "zeros -- it carries no measurement")
    return values[:last + 1], len(values) - (last + 1)


def load_sam(paths):
    """({(date, hour): gross_kwh}, per-file provenance) from Enphase 8760 exports.

    Row 0 is 00:00 local wall clock of the year in the filename; the grid is
    flat 24 h/day and knows nothing about DST, which is why the two DST dates
    are excluded downstream rather than corrected here.
    """
    out, prov = {}, []
    for p in paths:
        # The trailing _YYYY, not the first four digits -- "sam8760" is in the
        # filename and would otherwise be read as the year.
        m = re.search(r"_(\d{4})\.csv$", p.name)
        if not m:
            raise SystemExit(f"service_headroom.py: cannot read a trailing "
                             f"_YYYY year from {p.name}")
        year = int(m.group(1))
        with open(p, newline="") as fh:
            rd = csv.DictReader(fh)
            if rd.fieldnames != ["kWh"]:
                raise SystemExit(f"service_headroom.py: {p.name} has columns "
                                 f"{rd.fieldnames}, expected ['kWh']")
            vals = [float(r["kWh"]) for r in rd]
        expect = 8784 if (year % 4 == 0 and (year % 100 or year % 400 == 0)) else 8760
        if len(vals) != expect:
            raise SystemExit(f"service_headroom.py: {p.name} has {len(vals)} "
                             f"rows, expected {expect} for {year}")
        real, padded = truncate_sam_padding(vals)
        base = dt.datetime(year, 1, 1)
        for i, v in enumerate(real):
            ts = base + dt.timedelta(hours=i)
            out[(ts.date(), ts.hour)] = v
        prov.append({"file": p.name, "year": year, "rows": len(vals),
                     "rows_used": len(real), "zero_padded_rows": padded,
                     "last_measured_hour": str(base + dt.timedelta(hours=len(real) - 1))})
    return out, prov


def _flag(path):
    """Read an intake applicability flag. Present -> it must be a real YAML
    boolean; absent -> None, and the caller decides.

    The same helper behavior_rebuild.py uses on household.has_ev. A string
    "false", a 0 or a stray comment fragment is not a boolean and is not
    guessed at: the flag decides whether an entire analysis is published, so a
    value nobody can read is an intake defect, not a default.
    """
    v = HH.get(path, required=False)
    if v is not None and v is not True and v is not False:
        raise SystemExit(
            "service_headroom.py: %s in private/household.yaml must be a YAML "
            "boolean (true/false), got %r -- fix the intake before running"
            % (path, v))
    return v


NEW_LOAD_FLAG = "household.has_new_load_interest"

# The second applicability flag. It gates the EVSE half of the analysis, not the
# whole of it: a household scoping a heat pump has every reason to ask this
# question and no EV. Same contract as the flag above -- only an explicit false
# disables, absence is not false.
EV_FLAG = "household.has_ev"

NO_EV_REASON = (
    f"{EV_FLAG} is false (intake applicability flag, "
    f"DATA-SOURCES-CHEATSHEET.md) -- this household has no EV, so there is no "
    f"existing EV supply equipment to report and no second charger to add. "
    f"charger.kw is not read: the intake contract lets a household with no EV "
    f"omit the charger block, and a scenario priced from a default nobody "
    f"supplied would be an invented figure, not a conservative one.")

NO_EV_CONTRACT = (
    f"{EV_FLAG} is read the same way {NEW_LOAD_FLAG} is: only an explicit "
    f"false disables anything, and an ABSENT flag is not read as false. The "
    f"two flags are independent -- has_ev: false with "
    f"has_new_load_interest: true is an ordinary combination, and the "
    f"heat-pump and battery scenarios run on it unchanged.")

NEW_LOAD_FLAG_CONTRACT = (
    "household.has_new_load_interest is the intake APPLICABILITY flag for this "
    "analysis (DATA-SOURCES-CHEATSHEET.md, required_if: always), and the whole "
    "panel: block is tagged required_if: has_new_load_interest. The flag is the "
    "authority on whether the question is being asked; the presence or absence "
    "of panel data is never read as an answer to it. Explicit false is the only "
    "value that switches the analysis off. An ABSENT flag is NOT read as false "
    "-- the flag postdates some intake files, so its absence leaves the "
    "original hard requirement on panel.service_rating_a in place, and a "
    "household that asks for this answer without supplying the panel stops "
    "rather than being quietly excused.")


def not_applicable():
    """The artifact for a household whose intake says this question does not apply.

    not_applicable, NOT not_determined: the intake DID determine the answer --
    the analysis does not apply to this household. Same contract and vocabulary
    as behavior_rebuild.py's and extended_findings.py's stubs, which govern the
    sibling flags.

    Nothing is computed and nothing is read to build it. That is the point of
    checking the flag first: a bill-only household has no panel survey, no
    Enphase export and possibly no interval file, and the documented promise
    for a false flag is "not applicable", not a stack of fail-closed stops.

    No CAVEAT either. The scoping caveat exists to bound an estimate, and this
    artifact does not carry one.
    """
    return {
        "not_applicable": True,
        "flag": NEW_LOAD_FLAG,
        "reason": (
            f"{NEW_LOAD_FLAG} is false (intake applicability flag, "
            f"DATA-SOURCES-CHEATSHEET.md) -- this household is not asking "
            f"whether its electrical service has room for a new load, so no "
            f"service-headroom analysis was run and none is published."),
        "to_enable_it": (
            f"Set {NEW_LOAD_FLAG}: true in private/household.yaml and answer "
            f"the panel questions the cheatsheet tags required_if: "
            f"has_new_load_interest -- panel.service_rating_a, "
            f"panel.busbar_rating_a, panel.spaces, panel.max_circuits and "
            f"panel.schedule are the ones with no default -- then rerun "
            f"analysis/service_headroom.py."),
        "inputs_read": (
            "None beyond the flag itself. It is checked before the panel "
            "intake, the Green Button 15-minute export and the Enphase "
            "consumption-CT files are touched, so a household with none of "
            "them on hand still gets this artifact instead of an error."),
        "flag_contract": NEW_LOAD_FLAG_CONTRACT,
    }


def _optional_number(key):
    """A nullable intake field as a float, or None.

    The intake contract documents four panel fields as nullable, and `null`
    there is a MEANING, not a missing answer: no source backfeeds the panel, no
    continuous rating is printed on the socket, the breaker end was not read.
    float(None) raises, so a value that the committed template ships would
    otherwise stop the run.
    """
    v = HH.get(key, required=False)
    return None if v is None else float(v)


def _key_present(dotted):
    """Whether the intake RECORDS this key at all, whatever value it holds.

    household.get() cannot tell the two apart on its own: with required=False a
    key present with an explicit null and a key that is not there both return
    None. The difference is a fact about the intake rather than about the
    value, and here it carries meaning -- `panel.pv_backfeed_a: null` is a
    surveyor's answer ("I looked; nothing backfeeds this panel"), while an
    absent key is the question never having been asked. So the parent mapping is
    fetched and the leaf looked up in it, leaving household.get()'s contract
    alone.
    """
    parent, _dot, leaf = dotted.rpartition(".")
    if not parent:
        raise SystemExit(f"service_headroom.py: _key_present needs a dotted "
                         f"path with a parent, got {dotted!r}")
    node = HH.get(parent, required=False)
    return isinstance(node, dict) and leaf in node


# What the intake says about the source(s) already backfeeding the busbar. Three
# states, because the intake has three, and collapsing the middle one onto the
# last withholds a determination the data supports.
BACKFEED_READ = "read_off_the_breaker"
BACKFEED_SURVEYED_NONE = "surveyed_none"
BACKFEED_NOT_RECORDED = "not_recorded"

BACKFEED_NOTE = {
    BACKFEED_READ: "read off the existing backfeed breaker",
    BACKFEED_SURVEYED_NONE: (
        "panel.pv_backfeed_a is recorded as an explicit null: the panel was "
        "surveyed and NOTHING backfeeds it. The 0 A above is a known, complete "
        "answer -- not an unanswered question -- so the remaining allowance is "
        "the real one and the ampacity leg is decided on it"),
    BACKFEED_NOT_RECORDED: (
        "panel.pv_backfeed_a is ABSENT from the intake: nobody has answered "
        "whether anything backfeeds this panel. The 0 A above is the most "
        "generous reading available rather than a measurement, so the "
        "remaining allowance is an upper bound and a fit within it is not "
        "determined"),
}


def backfeed_known(basis):
    """Whether the existing backfeed is an answer rather than an assumption.

    Validates the token as well: a stale True/False passed where a basis is
    expected would read as truthy and silently promote an unanswered question
    to a known zero, which is the exact defect the three states exist to
    prevent.
    """
    if basis not in BACKFEED_NOTE:
        raise SystemExit(f"service_headroom.py: {basis!r} is not an "
                         f"existing-backfeed basis; expected one of "
                         f"{sorted(BACKFEED_NOTE)}")
    return basis != BACKFEED_NOT_RECORDED


# What the intake says about the meter socket's continuous rating. Same three
# states, same reason -- and here the collapsed middle case failed in the
# OPTIMISTIC direction: the socket is the tighter of the two ampacity
# constraints wherever it exists, so an unasked question that read as "does not
# apply" deleted the binding constraint and inflated every headroom below it.
SOCKET_READ = "read_off_the_socket"
SOCKET_SURVEYED_NONE = "surveyed_no_rating_printed"
SOCKET_NOT_RECORDED = "not_recorded"

SOCKET_CONSTRAINT = {
    SOCKET_READ: ("applies -- headroom is reported against it alongside the "
                  "service rating"),
    SOCKET_SURVEYED_NONE: (
        "does not apply -- no continuous rating is recorded for the meter "
        "socket, so the service rating is the only ampacity constraint and the "
        "socket is omitted from every headroom rather than carried as a null"),
    SOCKET_NOT_RECORDED: (
        "NOT DETERMINED -- panel.meter_socket_continuous_a is ABSENT from the "
        "intake, so whether this service has a printed continuous rating at the "
        "socket has never been looked at. That is not the same as a socket with "
        "no rating printed, which the intake records as an explicit null. The "
        "socket is the TIGHTER of the two ampacity constraints wherever it "
        "exists, so every headroom in this artifact is an upper limit that a "
        "socket rating could tighten, not a binding figure"),
}

SOCKET_SETTLE = (
    "Reading the meter socket, and recording either answer: the continuous "
    "ampere rating printed on a meter-main combination goes in "
    "panel.meter_socket_continuous_a, and an explicit null goes there if the "
    "meter is a separate enclosure with no rating printed. Either one decides "
    "the constraint; leaving the key out leaves it open, and the headrooms "
    "published here stand as upper limits until it is closed.")

BINDING_IS = {
    SOCKET_READ: (
        "the tightest of the ampacity constraints that apply -- the service "
        "rating and the meter socket's continuous rating, both evaluated"),
    SOCKET_SURVEYED_NONE: (
        "the headroom against the service rating, which is the only ampacity "
        "constraint on this service: the meter socket was read and carries no "
        "printed continuous rating"),
    SOCKET_NOT_RECORDED: (
        "an UPPER LIMIT, not the binding figure. It is the headroom against the "
        "service rating alone, because panel.meter_socket_continuous_a is "
        "absent from the intake -- nobody has looked at whether the socket "
        "carries a continuous rating, and one would be the tighter constraint"),
}


def socket_basis_of(panel):
    """Which of the three states the meter-socket rating is in."""
    if panel["meter_socket_continuous_a"] is not None:
        return SOCKET_READ
    return (SOCKET_SURVEYED_NONE if panel["meter_socket_recorded"]
            else SOCKET_NOT_RECORDED)


def existing_backfeed(panel):
    """(amps already spending the 120% allowance, on what basis).

    The arithmetic is the same 0 A in two of the three cases; what differs is
    whether that zero is a finding or a placeholder, and every verdict that
    rests on the allowance needs to know which.
    """
    if panel["pv_backfeed_a"] is not None:
        return float(panel["pv_backfeed_a"]), BACKFEED_READ
    if panel["pv_backfeed_recorded"]:
        return 0.0, BACKFEED_SURVEYED_NONE
    return 0.0, BACKFEED_NOT_RECORDED


PANEL_DOMAIN_NOTE = (
    "The panel intake feeds safety arithmetic, and a value outside its physical "
    "domain does not produce an obviously wrong answer -- it produces a "
    f"plausible one. {nec('705.12(B)(3)(2)')} computes the remaining backfeed "
    "allowance as busbar * 1.20 - main - existing_backfeed, so a NEGATIVE "
    "existing backfeed ENLARGES the allowance and can turn a failing panel into "
    "a passing one. Each field is checked before anything is computed on it.")


def _panel_domain_error(field, value, why):
    """Stop the run naming the field, the value read, and why it is impossible."""
    raise SystemExit(f"service_headroom.py: panel.{field} is {value!r}, which "
                     f"{why}. {PANEL_DOMAIN_NOTE}")


def validate_panel(p):
    """Domain checks on the panel intake, fail-closed and field-specific.

    Only what can flip a verdict or make the arithmetic meaningless is checked
    here; this is a guard on safety arithmetic, not a schema validator:

      * the two ampere ratings every figure divides the panel by must be
        positive;
      * a meter-socket rating, where one is recorded at all, must be positive --
        `null` is how the intake says the constraint does not apply, and a zero
        would be published as a binding constraint instead;
      * an existing backfeed must not be negative, which is the one that can
        turn a failing panel into a passing one;
      * the two position counts must be positive, and a panel cannot offer
        fewer pole positions than it has full-size spaces;
      * a main larger than the busbar it feeds is not a panel this method can
        score -- every 120% figure computed from that pair is meaningless.

    The schedule's own geometry is checked where it is counted, in
    panel_occupancy(): a schedule that fills more spaces or pole positions than
    the enclosure has stops the run there.
    """
    for f in ("service_rating_a", "busbar_rating_a"):
        if not p[f] > 0.0:
            _panel_domain_error(
                f, p[f], "is not a positive ampere rating -- a panel with no "
                "main or no busbar rating is not one this method can score")
    socket = p["meter_socket_continuous_a"]
    if socket is not None and not socket > 0.0:
        _panel_domain_error(
            "meter_socket_continuous_a", socket,
            "is recorded but is not a positive ampere rating; an explicit null "
            "is how the intake says the socket was read and carries no printed "
            "continuous rating, and a zero or negative one would be published "
            "as the binding ampacity constraint")
    backfeed = p["pv_backfeed_a"]
    if backfeed is not None and backfeed < 0.0:
        _panel_domain_error(
            "pv_backfeed_a", backfeed,
            "is negative; an existing source cannot spend a negative share of "
            "the 120% allowance, and a negative one increases the remaining "
            "allowance rather than reducing it")
    for f in ("spaces", "max_circuits"):
        if not p[f] > 0:
            _panel_domain_error(
                f, p[f], "is not a positive count of physical positions")
    if p["max_circuits"] < p["spaces"]:
        _panel_domain_error(
            "max_circuits", p["max_circuits"],
            f"is fewer than panel.spaces ({p['spaces']}); max_circuits counts "
            f"pole positions including twin-density devices, so it can equal "
            f"the full-size space count but never fall below it")
    if p["service_rating_a"] > p["busbar_rating_a"]:
        _panel_domain_error(
            "service_rating_a", p["service_rating_a"],
            f"exceeds panel.busbar_rating_a ({p['busbar_rating_a']}); a main "
            f"larger than the busbar it feeds is not a panel this method can "
            f"score")
    return p


def load_panel():
    """The private-tier panel intake, through the fail-closed accessor.

    Most values here are physical facts about one specific panel with no sane
    default, so household.get() is called with required=True and a missing key
    stops the run: service rating, busbar rating, spaces, max circuits and the
    device schedule are all REQUIRED.

    The nullable fields are the exceptions the intake contract itself names --
    `pv_backfeed_a` (null when the panel was surveyed and nothing backfeeds it),
    `meter_socket_continuous_a` (null when the socket was read and carries no
    printed continuous rating) and the three breaker positions. Each is carried
    as None and handled explicitly downstream; none is coerced to a number that
    would read as a measurement.

    `pv_backfeed_recorded` and `meter_socket_recorded` carry whether each key
    was there at all, which the value alone cannot say. A surveyed null and an
    unanswered question look identical through household.get() and mean
    opposite things -- see existing_backfeed() and socket_basis_of().

    `battery_breaker_position` is the PROPOSED source breaker's end of the bus,
    a separate question from where the existing PV breaker sits. It is read
    separately and never filled in from `pv_breaker_position`.

    `charger.kw` is the one field here that is NOT part of the panel survey, and
    it is conditional on `household.has_ev`. With that flag explicitly false the
    key is not read at all -- the intake contract lets a household with no EV
    omit the `charger:` block, so a required read would fail closed on a key the
    contract permits to be missing. `has_ev` travels with the panel dict so the
    scenarios that price a SECOND charger can report themselves not applicable
    rather than compute from a None. Absence is not false: only an explicit
    false disables, which is the same contract the new-load flag carries.

    Everything numeric then goes through validate_panel(), which rejects values
    outside their physical domain. A missing field already stops the run; an
    impossible one has to as well, because the busbar arithmetic turns it into a
    plausible answer rather than an obviously wrong one.
    """
    has_ev = _flag(EV_FLAG) is not False
    return validate_panel({
        "has_ev": has_ev,
        "service_rating_a": float(HH.get("panel.service_rating_a")),
        "busbar_rating_a": float(HH.get("panel.busbar_rating_a")),
        "pv_backfeed_a": _optional_number("panel.pv_backfeed_a"),
        "pv_backfeed_recorded": _key_present("panel.pv_backfeed_a"),
        "meter_socket_continuous_a": _optional_number(
            "panel.meter_socket_continuous_a"),
        "meter_socket_recorded": _key_present("panel.meter_socket_continuous_a"),
        "pv_breaker_position": HH.get("panel.pv_breaker_position", required=False),
        "battery_breaker_position": HH.get("panel.battery_breaker_position",
                                           required=False),
        "main_breaker_position": HH.get("panel.main_breaker_position",
                                        required=False),
        "spaces": int(HH.get("panel.spaces")),
        "max_circuits": int(HH.get("panel.max_circuits")),
        "schedule": HH.get("panel.schedule"),
        "charger_kw": float(HH.get("charger.kw")) if has_ev else None,
    })


# ---------------------------------------------------------------------------
# Panel schedule geometry
# ---------------------------------------------------------------------------

def breaker_geometry(entry, where="a schedule entry"):
    """(spaces, poles, [ocpd_a, ...]) for one device in the panel schedule.

    `where` names the entry POSITIONALLY in any failure message. The entry's
    own `label` is a transcribed door legend -- private-tier intake -- and a
    stop message on stderr is a place it does not need to be. The position is
    enough to find the row.

    `amps` is an int for a full-size breaker -- one overcurrent device spanning
    `poles` stab positions, so a 2-pole 60 A breaker is a single 60 A OCPD
    occupying two spaces.

    `amps` is a list for a twin-density (Class CTL) device, and the two shapes
    behave differently:
      * a tandem (2 poles, 2 amp values) is TWO 1-pole breakers sharing ONE
        space;
      * a quad (4 poles, 4 amp values) is TWO 2-pole breakers occupying TWO
        spaces, the outer pair common-trip and the inner pair common-trip --
        which is why the outer and inner values must mirror.
    """
    poles = int(entry["poles"])
    amps = entry["amps"]
    if not isinstance(amps, list):
        return poles, poles, [float(amps)]
    if len(amps) != poles:
        raise SystemExit(f"service_headroom.py: {where} lists {len(amps)} amp "
                         f"values for {poles} poles")
    if poles == 2:
        return 1, 2, [float(a) for a in amps]
    if poles == 4:
        if amps[0] != amps[3] or amps[1] != amps[2]:
            raise SystemExit(
                f"service_headroom.py: the quad at {where} has amps {amps}, "
                f"which is not an outer/inner common-trip pair")
        return 2, 4, [float(amps[0]), float(amps[1])]
    raise SystemExit(f"service_headroom.py: {where} has {poles} poles with a "
                     f"list of amps -- only tandems (2) and quads (4) are "
                     f"twin-density devices")


def panel_occupancy(schedule, spaces, max_circuits):
    """Spaces and pole positions used vs available, plus the branch-OCPD sum.

    A panel can pass every ampacity test and still have nowhere to land a
    breaker, so this is reported alongside the amps and never folded into them.

    The schedule is checked against the enclosure it claims to describe: a
    schedule filling more full-size spaces, or more pole positions, than the
    intake says the panel has is a disagreement between two intake answers, and
    every free-space figure below it would be negative or invented.
    """
    used_spaces = used_poles = 0
    ocpd = []
    twin = 0
    for i, e in enumerate(schedule, start=1):
        s, p, a = breaker_geometry(e, f"schedule entry {i} of {len(schedule)}")
        used_spaces += s
        used_poles += p
        ocpd += a
        if isinstance(e["amps"], list):
            twin += 1
    if used_spaces > spaces:
        raise SystemExit(
            f"service_headroom.py: the panel schedule occupies {used_spaces} "
            f"full-size spaces but panel.spaces records {spaces}. The schedule "
            f"and the enclosure disagree, so neither the free-space count nor "
            f"anything computed from it is a fact about this panel.")
    if used_poles > max_circuits:
        raise SystemExit(
            f"service_headroom.py: the panel schedule occupies {used_poles} "
            f"pole positions but panel.max_circuits records {max_circuits}. The "
            f"schedule and the enclosure disagree, so neither the free-pole "
            f"count nor anything computed from it is a fact about this panel.")
    free = spaces - used_spaces
    return {
        "devices": len(schedule),
        "twin_density_devices": twin,
        "spaces_total": spaces,
        "spaces_used": used_spaces,
        "spaces_free": free,
        "pole_positions_total": max_circuits,
        "pole_positions_used": used_poles,
        "pole_positions_free": max_circuits - used_poles,
        "branch_ocpd_sum_a": _r(sum(ocpd), 1),
        "largest_branch_ocpd_a": _r(max(ocpd), 1),
        "note": (
            "A 240 V circuit needs two adjacent full-size spaces; a tandem "
            f"cannot host one. With {free} space(s) free, "
            + ("there is nowhere to put one whatever the ampacity math says: "
               "it takes consolidating existing circuits onto twin-density "
               "devices or adding a subpanel." if free < 2 else
               "the count allows one, but the schedule records devices rather "
               "than slot positions, so whether two of the free spaces are "
               "ADJACENT is not established -- see each case's physical_fit.")),
    }


# ---------------------------------------------------------------------------
# Gross-load reconstruction
# ---------------------------------------------------------------------------

def hourly_sums(intervals):
    """{(date, hour): (import_kwh, export_kwh, n_intervals)}.

    The interval count is carried because the fall-back Sunday's 01:00 hour has
    eight of them covering two real hours. Anything that turns these into a
    power must divide by n * 0.25, never by 1.
    """
    acc = {}
    for d, hf, imp, exp in intervals:
        k = (d, int(hf))
        a = acc.setdefault(k, [0.0, 0.0, 0])
        a[0] += imp
        a[1] += exp
        a[2] += 1
    return {k: tuple(v) for k, v in acc.items()}


def hourly_mean_kw(hsums):
    """{(date, hour): mean import kW} using each hour's real elapsed time.

    This is the DST trap in one line: the fall-back Sunday's repeated hour
    carries eight 15-minute intervals, so its energy spans two hours and its
    mean power is the sum over 2.0 h, not over 1.0 h. Dividing by a hard-coded
    one hour manufactures a peak that never happened.
    """
    return {k: imp / (n * 0.25) for k, (imp, _e, n) in hsums.items()}


def dst_guard(hsums, dst_days):
    """What a naive hourly aggregation would report, and what really happened.

    `groupby(date, hour).sum()` on 15-minute kWh and calling the result kW is
    right on 8,759 hours of the year and wrong on one: the fall-back Sunday's
    repeated hour carries eight intervals spanning two real hours, so the naive
    figure is double the demand that occurred. It lands near the top of the
    distribution, which is exactly where a maximum-demand study looks. The
    spring-forward hour has the opposite shape and simply does not exist in the
    export, so nothing is reported for it.
    """
    naive = {k: imp for k, (imp, _e, _n) in hsums.items()}
    correct = hourly_mean_kw(hsums)
    kn = max(naive, key=lambda k: naive[k])
    kc = max(correct, key=lambda k: correct[k])
    rows = [{"hour": f"{d} {h:02d}:00", "intervals": nn,
             "elapsed_hours": _r(nn * 0.25, 2),
             "naive_kw": _r(imp, 3),
             "corrected_kw": _r(imp / (nn * 0.25), 3)}
            for (d, h), (imp, _e, nn) in sorted(hsums.items())
            if d in dst_days and nn != 4]
    return {
        "series": "metered import (net), hourly mean",
        "naive_max_kw": _r(naive[kn], 3),
        "naive_max_at": f"{kn[0]} {kn[1]:02d}:00",
        "naive_max_is_a_dst_artifact": kn[0] in dst_days,
        "corrected_max_kw": _r(correct[kc], 3),
        "corrected_max_at": f"{kc[0]} {kc[1]:02d}:00",
        "irregular_hours": rows,
        "rule": ("hourly mean kW = kWh / (intervals * 0.25 h); the interval "
                 "count is never assumed to be four"),
    }


def dst_dates_in(dates):
    """The DST transition Sundays that fall inside the window.

    Derived from rates.dst_transition_sundays, the single home of the tariff
    clock, rather than listed here -- a second copy of the DST rule is a second
    thing to get wrong.
    """
    out = set()
    for y in sorted({d.year for d in dates}):
        for d in R.dst_transition_sundays(y):
            if d in dates:
                out.add(d)
    return out


def derive_pv(hsums, sam, excluded_days):
    """{(date, hour): pv_kwh} for the hours the Enphase file actually covers.

    pv_hour = max(sam_hour - import_hour + export_hour, 0), which is the whole
    identity gross = import - export + pv rearranged. The clip at zero absorbs
    the instrument disagreement in dark hours, where the true value is zero and
    the residual can land either side of it.

    Hours outside this mapping have no production measurement at all, and no
    empirical stand-in may be manufactured for them. In particular a
    per-hour-of-day maximum -- the largest production seen at that clock
    position on OTHER days -- is not a ceiling on an hour nobody measured, so
    nothing of the kind is returned here. See gross_envelope().
    """
    pv = {}
    for (d, h), (imp, exp, _n) in hsums.items():
        if d in excluded_days or (d, h) not in sam:
            continue
        pv[(d, h)] = max(sam[(d, h)] - imp + exp, 0.0)
    return pv


PV_CEILING_MISSING = (
    "service_headroom.py: solar.kw_ac -- the array's inverter AC nameplate -- "
    "is not recorded in private/household.yaml. Without it there is no "
    "PHYSICAL ceiling on 15-minute PV output, and the daylight gross-load "
    "envelope stops being an upper bound: the largest observed hourly "
    "production is not a substitute, because a single quarter-hour can carry "
    "more than a quarter of the best full hour. Every ampacity verdict here is "
    "computed on that envelope being a bound, so the run stops rather than "
    "publish verdicts resting on a figure that only looks conservative.")


def load_pv_ac_nameplate():
    """The inverter AC nameplate in kW, or a hard stop.

    Read through the fail-closed accessor but raised with this script's own
    message, because the consequence of its absence is specific: the envelope
    would no longer be a bound and every ampacity verdict rests on it being
    one. There is no fallback to the empirical maximum.
    """
    kw = HH.get("solar.kw_ac", required=False)
    if kw is None:
        raise SystemExit(PV_CEILING_MISSING)
    kw = float(kw)
    if kw <= 0.0:
        raise SystemExit(PV_CEILING_MISSING)
    return kw


def pv_ac_ceiling(kw_ac, inverter_model, inverter_count, corroboration):
    """The physical per-interval PV ceiling, with the evidence that it holds.

    `kw_ac` is the inverters' AC nameplate: the array physically cannot put
    more than that onto the service, so a quarter-hour carries at most
    `kw_ac * 0.25` kWh of production. That is a bound in the strict sense, and
    it is the only figure here entitled to the word.

    `corroboration` is [(instrument, observed_kw, what_it_measures)] -- the
    empirical maxima. They are evidence that the nameplate is not being
    contradicted, NOT the basis of the ceiling. Any one of them exceeding the
    nameplate is a data-integrity failure (a mislabelled array, a rescaled
    export, the wrong system) and stops the run naming which and by how much,
    because in that case neither figure can be trusted as a ceiling.
    """
    rows, breached = [], []
    for name, kw, measures in corroboration:
        over = kw - kw_ac
        rows.append({
            "instrument": name,
            "measures": measures,
            "observed_kw": _r(kw, 3),
            "below_nameplate_by_kw": _r(-over, 3),
            "exceeds_nameplate": bool(over > 1e-9),
        })
        if over > 1e-9:
            breached.append(f"{name} reached {kw:.3f} kW, {over:.3f} kW above "
                            f"the {kw_ac:.2f} kW nameplate")
    if breached:
        raise SystemExit(
            "service_headroom.py: an observed PV maximum EXCEEDS the inverter "
            "AC nameplate, so the nameplate cannot be used as a physical "
            "ceiling and the observation cannot be trusted either -- " +
            "; ".join(breached) + ". Reconcile the array's nameplate against "
            "the monitoring exports before this artifact is regenerated.")
    return {
        "ceiling_kw": _r(kw_ac, 3),
        "per_interval_ceiling_kwh": _r(kw_ac * 0.25, 5),
        "basis": (
            f"inverter AC nameplate from intake (solar.kw_ac): "
            f"{inverter_count} x {inverter_model}, {kw_ac:.2f} kW AC. The array "
            f"cannot deliver more than its inverters are rated for, so one "
            f"quarter-hour carries at most {kw_ac * 0.25:.4f} kWh of "
            f"production. This is a physical bound, not an observation."),
        "why_not_the_observed_maximum": (
            "The largest DERIVED hourly production is not a per-interval "
            "ceiling: a quarter-hour can legitimately carry more than a "
            "quarter of the best full hour, through intra-hour ramps and "
            "cloud-edge lensing. Capping on it would produce an 'upper bound' "
            "able to sit below true gross demand, which is the direction a "
            "capacity answer must never fail in. The nameplate is looser and "
            "is the safe direction."),
        "corroboration": rows,
        "corroboration_reading": (
            "Every independent maximum sits below the nameplate, so nothing in "
            "the record contradicts it. These are corroboration, not the basis "
            "of the ceiling; any one of them exceeding the nameplate stops the "
            "run."),
    }


PV_BASIS_MEASURED = "measured_hour"
PV_BASIS_NAMEPLATE = "nameplate"

PV_BASIS_MEANING = {
    PV_BASIS_MEASURED: (
        "the containing hour HAS an Enphase reading, so the derived production "
        "for that hour is a measurement of it. The upper bound credits the "
        "whole hour to this one quarter-hour and is narrowed to the nameplate "
        "interval cap where the hour's own output could not physically land "
        "inside fifteen minutes -- narrowing by a measurement OF THAT HOUR, "
        "never by an observation of some other day."),
    PV_BASIS_NAMEPLATE: (
        "the containing hour has NO Enphase reading, so the only ceiling that "
        "holds is the inverters' AC nameplate. No empirical figure narrows it. "
        "The bound is loose on these intervals -- a 01:15 in the uncovered "
        "tail is credited with a full quarter-hour of nameplate production, "
        "which certainly did not happen -- and loose in the direction an "
        "upper bound is allowed to be wrong in."),
}

PV_BASIS_WHY_NOT_EMPIRICAL = (
    "For an hour with no reading, the largest production previously OBSERVED "
    "at that hour of day is not a ceiling on it: an uncovered hour can "
    "legitimately produce more than any hour yet seen at that clock position, "
    "through a clearer sky, a cooler cell temperature or a season the window "
    "sampled thinly. Taking the smaller of that figure and the nameplate cap "
    "selects the empirical one whenever it is lower, and the result is not an "
    "upper bound. It errs optimistically, which is the one direction a "
    "capacity verdict must never fail in, so the nameplate cap is used alone.")


def gross_envelope(intervals, pv, ac_ceiling_kw):
    """[(date, hour_frac, lower_kw, upper_kw, export_kwh, exact, pv_basis)].

    lower = import: every kWh the PV made was exported, none self-consumed.
    upper = import - export + pv_up, where pv_up is the most production that
            could have landed inside this one quarter-hour.

    `pv_up` has exactly two bases, and which one applies is recorded per
    interval rather than left to be inferred:

      * PV_BASIS_MEASURED -- the containing hour is covered by the Enphase
        file. `min(pv_hour, ac_ceiling * 0.25)`: the hour's whole measured
        output, narrowed by the physical fact that fifteen minutes cannot
        carry more than a quarter of the inverters' AC nameplate.
      * PV_BASIS_NAMEPLATE -- the hour is not covered (the zero-padded tail of
        the current-year export, and the two excluded DST days). `ac_ceiling *
        0.25` alone. Nothing empirical may narrow it -- see
        PV_BASIS_WHY_NOT_EMPIRICAL, which is the standing reason a
        per-hour-of-day maximum must not be reintroduced here.

    The bound collapses -- gross is the metered import, exactly -- wherever the
    hour made no PV and the interval exported none. `exact` marks those, and it
    can only happen on the measured basis: an uncovered hour always carries the
    full nameplate cap, so it is never point-determined.
    """
    cap = ac_ceiling_kw * 0.25
    out = []
    for d, hf, imp, exp in intervals:
        hour_pv = pv.get((d, int(hf)))
        if hour_pv is None:
            pv_up, basis = cap, PV_BASIS_NAMEPLATE
        else:
            pv_up, basis = min(hour_pv, cap), PV_BASIS_MEASURED
        lo = imp * 4.0
        up = max((imp - exp + pv_up) * 4.0, lo)
        out.append((d, hf, lo, up, exp, up - lo < 1e-9, basis))
    return out


UNCOVERED_DST = "excluded_dst_day"
UNCOVERED_AFTER = "after_the_last_hour_the_enphase_files_measured"
UNCOVERED_BEFORE = "before_the_first_hour_the_enphase_files_cover"
UNCOVERED_GAP = "missing_hour_inside_the_enphase_coverage"

UNCOVERED_WHY = {
    UNCOVERED_DST: (
        "its date is a DST transition Sunday, which is excluded from every "
        "meter x Enphase computation because the flat 8760-row Enphase grid "
        "cannot be aligned to a 23- or 25-hour day"),
    UNCOVERED_AFTER: (
        "it falls after the last hour the Enphase consumption-CT export "
        "measured: the meter window runs on past the end of the production "
        "record"),
    UNCOVERED_BEFORE: (
        "it falls before the first hour the Enphase consumption-CT export "
        "covers"),
    UNCOVERED_GAP: (
        "its hour is missing from the middle of the Enphase coverage -- the "
        "export skips it"),
}


def uncovered_reason(d, hf, excluded_days, first, last):
    """Why one interval's hour has no Enphase reading behind it.

    `first`/`last` are the (date, hour) ends of the Enphase coverage. The four
    reasons are exhaustive by construction: an hour is either excluded, past
    the end, before the start, or missing from the middle.
    """
    if d in excluded_days:
        return UNCOVERED_DST
    if (d, int(hf)) > last:
        return UNCOVERED_AFTER
    if (d, int(hf)) < first:
        return UNCOVERED_BEFORE
    return UNCOVERED_GAP


def _hour_dt(key):
    """(date, hour) as a datetime, for arithmetic on the coverage lag."""
    return dt.datetime.combine(key[0], dt.time(int(key[1])))


def _interval_dt(d, hf):
    return dt.datetime.combine(d, dt.time(0)) + dt.timedelta(hours=float(hf))


def coverage_lag(env, sam, lag_intervals):
    """How far the meter window runs past the Enphase record, GATED.

    The lag was computed and not acted on: `ceiling_basis_split` counted the
    intervals it produces and asserted only that they add up. Every one of them
    is bounded by the bare nameplate cap, and on this window one of them is
    what sets the conservative basis every `not_determined` verdict rests on --
    so the lag is published as a figure and judged against a declared threshold
    like every other gate here. A breach stops the run; see
    ENPHASE_COVERAGE_MAX_LAG_HOURS for the threshold and why it is that.
    """
    last = max(sam)
    meter_end = max(_interval_dt(d, hf) for d, hf, *_rest in env)
    hours = (meter_end - _hour_dt(last)).total_seconds() / 3600.0
    gate = _gate(
        "enphase_coverage_lag_hours", _r(hours, 2), "<=",
        ENPHASE_COVERAGE_MAX_LAG_HOURS, hours <= ENPHASE_COVERAGE_MAX_LAG_HOURS,
        f"the meter window may run at most "
        f"{ENPHASE_COVERAGE_MAX_LAG_HOURS:.0f} h past the last hour the "
        f"Enphase consumption-CT export measured",
        "a STALE Enphase export left in place beside a fresh meter export, "
        "which hands a growing tail of the window to the bare nameplate PV cap "
        "-- the loosest bound this analysis draws, and the one the "
        "conservative verdicts are computed on")
    if not gate["passed"]:
        raise SystemExit(
            "service_headroom.py: the Enphase consumption-CT export stops "
            f"{_r(hours, 2)} h before the end of the meter window, past the "
            f"{ENPHASE_COVERAGE_MAX_LAG_HOURS:.0f} h limit. Those "
            f"{lag_intervals} intervals carry the bare nameplate PV cap with "
            "no measurement to narrow them, and the conservative basis every "
            "verdict rests on would be set by unmeasured hours. Re-pull the "
            "Enphase export through the end of the meter window; nothing was "
            "written. Shortening the meter window instead is not the fix -- it "
            "would delete real metered demand from a maximum-demand study.")
    return {
        "enphase_coverage_last_hour": f"{last[0]} {last[1]:02d}:00",
        "meter_window_last_interval": fmt_ts(meter_end.date(),
                                             meter_end.hour + meter_end.minute / 60.0),
        "lag_hours": _r(hours, 2),
        "lag_intervals": lag_intervals,
        "gate": gate,
        "what_the_lag_costs": (
            f"{lag_intervals} interval(s) with no production measurement "
            f"behind them. Each is bounded by the inverters' AC nameplate "
            f"alone, which is the loosest bound in the envelope."),
        "what_would_close_it": (
            "An Enphase consumption-CT export pulled through the end of the "
            "meter window, so the tail has hourly production behind it like "
            "the rest of the record."),
    }


def ceiling_basis_split(env, excluded_days, sam):
    """How many intervals took each PV ceiling, why the nameplate ones did, and
    how far behind the meter window the production record stops.

    The nameplate intervals are the loose end of the envelope, so the artifact
    states their count and their CAUSE rather than leaving a reader to work out
    that some of the window has no production measurement behind it. Every
    nameplate interval is attributed to one of the four reasons an hour can be
    uncovered, and the attribution is checked to account for all of them.

    The coverage lag is then GATED rather than merely counted -- see
    coverage_lag().
    """
    first, last = min(sam), max(sam)
    reasons = collections.Counter()
    measured = nameplate = 0
    for d, hf, _lo, _up, _exp, _exact, basis in env:
        if basis == PV_BASIS_MEASURED:
            measured += 1
            continue
        nameplate += 1
        reasons[uncovered_reason(d, hf, excluded_days, first, last)] += 1
    if sum(reasons.values()) != nameplate:
        raise SystemExit(
            "service_headroom.py: the nameplate-ceiling intervals do not add up "
            f"({sum(reasons.values())} attributed, {nameplate} counted) -- the "
            "split published beside the envelope would be wrong")
    n = len(env)
    return {
        "measured_hour_intervals": measured,
        "measured_hour_pct": _r(100.0 * measured / n, 3),
        "measured_hour_basis": PV_BASIS_MEANING[PV_BASIS_MEASURED],
        "nameplate_intervals": nameplate,
        "nameplate_pct": _r(100.0 * nameplate / n, 3),
        "nameplate_basis": PV_BASIS_MEANING[PV_BASIS_NAMEPLATE],
        "nameplate_intervals_by_reason": dict(sorted(reasons.items())),
        "enphase_coverage_first_hour": f"{first[0]} {first[1]:02d}:00",
        "enphase_coverage_last_hour": f"{last[0]} {last[1]:02d}:00",
        "enphase_coverage_lag": coverage_lag(env, sam,
                                             reasons[UNCOVERED_AFTER]),
        "why_not_the_empirical_hour_of_day_maximum": PV_BASIS_WHY_NOT_EMPIRICAL,
    }


def binding_upper_interval(env, excluded_days, sam, ceiling_kw, lag):
    """The interval that SETS the conservative basis, and what bounded it.

    Every `not_determined` verdict in this artifact turns on one number -- the
    top of the gross-load envelope -- and that number comes from one interval.
    Which interval, and on which PV ceiling, decides what the conservative
    basis actually means and what would settle a case computed against it. A
    fixed sentence about daylight was published here while the binding interval
    was a 01:15 in the uncovered tail, so the description is derived from the
    interval instead of asserted about the method.
    """
    d, hf, _lo, up, _exp, _exact, basis = max(env, key=lambda e: e[3])
    first, last = min(sam), max(sam)
    covered = basis == PV_BASIS_MEASURED
    reason = None if covered else uncovered_reason(d, hf, excluded_days,
                                                   first, last)
    ts = fmt_ts(d, hf)
    if covered:
        reading = (
            f"The conservative basis is set by the interval at {ts}. Its "
            f"containing hour HAS an Enphase reading, so the upper bound there "
            f"credits that hour's whole measured production to this one "
            f"quarter-hour, capped at {_r(ceiling_kw * 0.25, 5)} kWh -- a "
            f"quarter-hour at the {ceiling_kw:.2f} kW inverter AC nameplate. "
            f"The looseness is the hourly resolution of the production "
            f"measurement.")
    else:
        reading = (
            f"The conservative basis is set by the interval at {ts}, and its "
            f"containing hour has NO production measurement behind it: "
            f"{UNCOVERED_WHY[reason]}. The Enphase export stops at "
            f"{lag['enphase_coverage_last_hour']} while the meter window runs "
            f"to {lag['meter_window_last_interval']}, {lag['lag_hours']} h "
            f"later. An uncovered hour is bounded by the inverters' AC "
            f"nameplate alone, so this quarter-hour is credited with "
            f"{_r(ceiling_kw * 0.25, 5)} kWh of production -- a full "
            f"quarter-hour at {ceiling_kw:.2f} kW, at {ts[-5:]}. Nothing "
            f"measured that hour, so nothing narrows the cap. What makes the "
            f"bound loose here is a COVERAGE GAP, not daylight.")
    return {
        "timestamp_local": ts,
        "upper_bound_kw": _r(up),
        "pv_ceiling_basis": basis,
        "hour_has_a_production_measurement": covered,
        "why_the_hour_is_uncovered": reason,
        "reading": reading,
    }


def conservative_basis_is(binding):
    """What the conservative basis IS, built from the interval that sets it."""
    return ("the top of the gross-load envelope: the largest upper bound over "
            "every 15-minute interval in the window, each interval capped at "
            "what the inverters can physically deliver in fifteen minutes. "
            + binding["reading"])


def what_would_settle_it(binding, lag):
    """What would settle a case the two bases disagree about.

    Derived from the basis the BINDING interval took. Where the binding
    interval sits in an hour the production export never covered, 15-minute
    production data settles nothing -- there is no measurement of that hour at
    any resolution -- and saying otherwise sends a reader after the wrong
    instrument.
    """
    if binding["hour_has_a_production_measurement"]:
        return (
            "15-minute PV production, which was never metered here. The "
            "consumption CT reads hourly, so each quarter-hour inside a "
            "producing hour is reported as a bound whose upper end credits a "
            "whole hour's production to one interval. A 15-minute production "
            "series would collapse the bound to a point and decide this case "
            f"either way. The interval that sets the conservative basis "
            f"({binding['timestamp_local']}) is one of those.")
    return (
        f"An Enphase consumption-CT export pulled through the end of the meter "
        f"window. The interval that sets the conservative basis "
        f"({binding['timestamp_local']}) sits in an hour the current export "
        f"does not cover -- it stops at {lag['enphase_coverage_last_hour']}, "
        f"{lag['lag_hours']} h short of the meter's last interval -- so there "
        f"is no production measurement of that hour AT ANY RESOLUTION, and "
        f"15-minute production data would not settle this case: the hour "
        f"itself is missing. Covering the tail would put an hourly reading "
        f"behind that interval and narrow the bound to it. Beyond that, the "
        f"quarter-hours inside producing hours stay bounds until 15-minute "
        f"production is metered, which it never was here.")


def fmt_ts(d, hf):
    """'2025-08-22 05:45' -- local wall clock, the meter's own basis."""
    return f"{d} {int(hf):02d}:{int(round((hf % 1) * 60)):02d}"


# ---------------------------------------------------------------------------
# NEC arithmetic
# ---------------------------------------------------------------------------

def amps(kw):
    """kW to amps at the service voltage."""
    return kw * 1000.0 / SERVICE_VOLTAGE_V


# The demand-side figure for a battery is what it DRAWS while charging from the
# grid, and this project records one power rating for the unit rather than two.
# Applying it to charging is an assumption; it is stated as one, and it is the
# conservative direction for a capacity answer.
BATTERY_CHARGING_BASIS = (
    f"{BATTERY_INVERTER_KW} kW is this project's canonical continuous power "
    f"rating for the unit -- research/battery-research-notes.md records it as "
    f"the Powerwall 3's continuous power and does not separate charging from "
    f"discharging. Applying it to GRID CHARGING is an assumption, not a reading "
    f"off a charging specification. It is the conservative direction: a unit "
    f"whose AC charge input is lower than its discharge output would draw less "
    f"than this, so the demand-side load counted here is if anything "
    f"overstated. On that basis the code value is "
    f"{amps(BATTERY_INVERTER_KW):.2f} A x 1.25 continuous = "
    f"{amps(BATTERY_INVERTER_KW) * NEC_625_42_FACTOR:.2f} A.")

# The EVSE load-sharing mitigation splits into a code claim and a hardware
# claim, and only one of them is this project's to make.
#
# The AMPS are code: NEC 625.42 sizes an EVSE load-management system on the
# system's maximum output rather than the sum of its connectors, so a second
# connector joined to the existing one adds no code load. That is the
# mitigation's actual content and it is cited.
#
# The BREAKER is hardware: whether two connectors in a power-sharing group may
# occupy ONE branch circuit, or whether each still needs its own circuit and
# breaker, is a manufacturer installation fact. Nothing in this repo records
# it -- not research/, not TECHNICAL.md, not the intake -- so it is not
# asserted in either direction, the same treatment the battery's charge input
# gets in BATTERY_CHARGING_NOT_DETERMINED. The physical-fit consequence is
# given BOTH ways instead, so the reader can act on whichever the
# manufacturer's instructions turn out to say.
EVSE_SHARING_AMPS_BASIS = (
    f"{nec('625.42')} permits an EVSE load-management system to be sized to the "
    "SYSTEM'S MAXIMUM OUTPUT rather than the sum of the connectors it serves. "
    "A second connector brought into a sharing group with the existing one "
    "therefore adds no code load: the group's maximum output is what it was. "
    "This is a code citation, and it is the whole of what this mitigation "
    "claims.")

EVSE_SHARING_CIRCUIT_NOT_DETERMINED = (
    "NOT DETERMINED -- whether a second connector in a power-sharing group may "
    "land on the EXISTING branch circuit and its breaker, or whether each "
    "connector still requires its own branch circuit. That is a manufacturer "
    "installation fact; this project holds no source for it and none is "
    "invented here, so nothing is asserted either way. The manufacturer's "
    "installation instructions for power sharing, together with the AHJ's "
    "acceptance of the wiring method, would settle it. The amps above do not "
    "depend on the answer; the panel-space consequence does, and is given for "
    "both answers.")

BATTERY_CHARGING_NOT_DETERMINED = (
    "NOT DETERMINED -- the selected unit's AC CHARGE INPUT. No figure for it "
    "exists anywhere in this project and none is invented here. The maximum "
    "continuous AC input on the selected unit's own nameplate or datasheet "
    "would settle it, and could only lower the demand-side figure used above.")


def nec_220_87_steps(max_demand_kw, service_rating_a, socket_rating_a, days,
                     socket_basis):
    """The 220.87 chain, written out so the artifact shows the arithmetic.

    Step 4 follows the socket's THREE states, not the one bit of information a
    null `socket_rating_a` carries:

      * a rating was read -- the step is computed, as it always has been;
      * the socket was read and carries no printed continuous rating -- the step
        is OMITTED rather than emitted with a null on both sides of a
        subtraction. A constraint that genuinely does not apply produces no row;
      * nobody looked -- the step is emitted as `not_determined`, with what
        would settle it. Dropping it here is what made the omission invisible:
        the chain would end at step 3 and read exactly like a service whose
        socket had been checked, while the tighter of the two constraints had
        silently left the calculation.
    """
    a_measured = amps(max_demand_kw)
    a_calc = a_measured * NEC_220_87_FACTOR
    steps = [
        {"step": 1,
         "label": "measured maximum demand converted to service amps",
         "formula": "A = kW * 1000 / V",
         "inputs": {"kW": _r(max_demand_kw), "V": SERVICE_VOLTAGE_V},
         "result_a": _r(a_measured)},
        {"step": 2,
         "label": f"{nec('220.87(2)')} calculated load: maximum demand x 125%",
         "formula": "A_calc = A_measured * 1.25",
         "inputs": {"A_measured": _r(a_measured),
                    "factor": NEC_220_87_FACTOR,
                    "measurement_days": days,
                    "condition_1_days_required": NEC_220_87_CONDITION_1_DAYS},
         "result_a": _r(a_calc)},
        {"step": 3,
         "label": "headroom against the main breaker rating",
         "formula": "headroom = service_rating - A_calc",
         "inputs": {"service_rating_a": service_rating_a, "A_calc": _r(a_calc)},
         "result_a": _r(service_rating_a - a_calc)},
    ]
    if socket_basis == SOCKET_READ:
        steps.append(
            {"step": 4,
             "label": ("headroom against the meter socket's continuous rating, "
                       "the tighter of the two constraints"),
             "formula": "headroom = meter_socket_continuous - A_calc",
             "inputs": {"meter_socket_continuous_a": socket_rating_a,
                        "A_calc": _r(a_calc)},
             "result_a": _r(socket_rating_a - a_calc)})
    elif socket_basis == SOCKET_NOT_RECORDED:
        steps.append(
            {"step": 4,
             "label": ("headroom against the meter socket's continuous rating, "
                       "the tighter of the two constraints where one exists"),
             "formula": "headroom = meter_socket_continuous - A_calc",
             "inputs": {"meter_socket_continuous_a": None,
                        "A_calc": _r(a_calc)},
             "result_a": None,
             "verdict": "not_determined",
             "reading": SOCKET_CONSTRAINT[SOCKET_NOT_RECORDED],
             "what_would_settle_it": SOCKET_SETTLE})
    elif socket_basis != SOCKET_SURVEYED_NONE:
        raise SystemExit(f"service_headroom.py: {socket_basis!r} is not a "
                         f"meter-socket basis; expected one of "
                         f"{sorted(SOCKET_CONSTRAINT)}")
    return steps


def nec_220_87_conditions(days, pv_kw_ac):
    """The three conditions 220.87 actually sets, and where each one stands.

    The method has conditions, and only one of them is arithmetic. Publishing
    the arithmetic without them let the artifact quote "the code minimum of 30
    days" -- which is not condition (1) at all. Condition (1) is a 1-YEAR
    period; the 30-day continuously-recorded route is the Exception to it, and
    the Exception is expressly closed to a service with a renewable energy
    system. That the household qualifies under (1) rather than under an
    Exception it could not use is a STRENGTHENING of the evidence, and it is
    reported that way.

    `pv_kw_ac` is what makes the Exception unavailable, so the finding is
    derived from the intake rather than asserted: solar.kw_ac is required and
    positive, which is the renewable energy system the Exception names.
    """
    margin = days / float(NEC_220_87_CONDITION_1_DAYS)
    # Read, not assumed: solar.kw_ac is a required positive intake field (the
    # run stops without it), and a positive AC nameplate IS the renewable
    # energy system the Exception names.
    has_renewable = pv_kw_ac > 0.0
    return {
        "rule": nec_rule("220.87"),
        "edition": NEC_EDITION,
        "condition_1": {
            "rule": nec_rule("220.87(1)"),
            "days_required": NEC_220_87_CONDITION_1_DAYS,
            "days_available": days,
            "margin_x": _r(margin, 2),
            "verdict": "pass" if days >= NEC_220_87_CONDITION_1_DAYS else "fail",
            "reading": (
                f"{days} days of continuous 15-minute revenue-meter data "
                f"covers the 1-year period condition (1) requires, with "
                f"{_r(margin, 2)}x the required span."
                if days >= NEC_220_87_CONDITION_1_DAYS else
                f"{days} days is short of the 1-year period condition (1) "
                f"requires, and the Exception that would otherwise permit a "
                f"30-day recording is closed to this service."),
        },
        "condition_1_exception_30_day_recording": {
            "rule": nec_rule("220.87(1) Exception"),
            "available_to_this_service": not has_renewable,
            "why": (
                f"This service has a renewable energy system on it -- "
                f"{pv_kw_ac:.2f} kW AC of solar photovoltaic, read from intake "
                f"(solar.kw_ac) -- and the Exception says in terms that it "
                f"does not apply to a feeder or service that has one. The "
                f"30-day recording route was therefore never open to this "
                f"household, whatever the window length, so no margin against "
                f"30 days means anything here."),
            "why_it_strengthens_rather_than_weakens": (
                "The route that is closed is the WEAKER one. The household "
                "qualifies under condition (1) itself, on a full year of "
                "revenue-meter data, which is the evidence the Exception "
                "exists to substitute for."),
        },
        "condition_2": {
            "rule": nec_rule("220.87(2)"),
            "where_it_is_evaluated": (
                "This is what the steps below and every case verdict compute: "
                "the measured maximum demand at 125% plus the new load, "
                "against the service rating and the meter socket's continuous "
                "rating. It is a per-case question, so it is answered per case "
                "rather than once."),
        },
        "condition_3": {
            "rule": nec_rule("220.87(3)"),
            "verdict": "not_determined",
            "reading": (
                "Neither the feeder's overcurrent protection nor the service's "
                "overload protection was inspected. Nothing in this project "
                "records them, and neither is inferable from interval data or "
                "from a panel schedule, so this condition is UNVERIFIED here "
                "rather than met. The artifact computes condition (2) and "
                "reads condition (1) off the window; condition (3) is the one "
                "it cannot answer."),
            "what_would_settle_it": (
                f"An on-site check by a licensed electrician: that the feeder "
                f"is protected in accordance with {nec('240.4')} and the "
                f"service has overload protection in accordance with "
                f"{nec('230.90')}. Both are readings off the installed "
                f"equipment, not calculations."),
        },
    }


# The two ends of a busbar. NEC 705.12(B)(3)(2) is a statement about which of
# them each supply lands on, so a position that reads as neither is not
# evidence about the condition.
BUSBAR_ENDS = ("top", "bottom")

POSITION_REQUIREMENT = (
    f"{nec('705.12(B)(3)(2)')} has a second, conjunctive condition: the backfeed "
    "breaker must be located at the opposite end of the busbar from the main "
    "supply. The 120% arithmetic is only the first half of the rule, and a "
    "positive remaining allowance is not by itself a compliant interconnection.")


def _end(value):
    """A recorded busbar end as 'top'/'bottom', or None if it is not one."""
    if value is None:
        return None
    v = str(value).strip().lower()
    return v if v in BUSBAR_ENDS else None


# The position condition is asked about ONE breaker. Which one has to be named,
# because the answer for the breaker already installed is not the answer for a
# breaker nobody has placed yet.
SOURCE_EXISTING_PV = "existing PV backfeed breaker"
SOURCE_PROPOSED_BATTERY = "proposed battery backfeed breaker"

WHAT_WOULD_SETTLE_THE_POSITION = {
    SOURCE_EXISTING_PV: (
        "Reading off the panel whichever of the two ends is not already "
        "recorded: which end of the breaker stack the main supply lands on, "
        "and which end the existing PV breaker lands on."),
    SOURCE_PROPOSED_BATTERY: (
        "A SURVEYED position for the new breaker: which end of the busbar the "
        "main lands on, and whether two adjacent full-size spaces exist at the "
        "opposite end for a 2-pole source breaker to occupy. The existing PV "
        "breaker's end is a fact about the device already installed and says "
        "nothing about where a new one could physically go, so it is not "
        "carried over."),
}


def position_condition(source_position, main_position,
                       source=SOURCE_EXISTING_PV):
    """The breaker-position half of NEC 705.12(B)(3)(2), three-valued.

    `source` names WHICH breaker is being asked about, and `source_position` is
    that breaker's own recorded end. A proposed battery breaker has no position
    until one is surveyed; the existing PV breaker's end is never substituted
    for it, because a panel can satisfy the rule for the breaker it already has
    and have nowhere at that end to land another.

    Fails closed on absent or unreadable evidence. A panel whose source breaker
    end was never read, or whose main's end is not carried by the intake
    schema, cannot be called compliant on this leg -- the honest answer is
    `not_determined` and what would settle it.
    """
    src, main = _end(source_position), _end(main_position)
    settle = WHAT_WOULD_SETTLE_THE_POSITION[source]
    if src is None:
        verdict = "not_determined"
        why = (f"No readable end is recorded for the {source}. "
               f"{nec('705.12(B)(3)(2)')} binds every source connected to the busbar, "
               f"and has to be satisfied by the installation rather than by "
               f"this calculation.")
    elif main is None:
        verdict = "not_determined"
        why = (f"The {source}'s end is recorded but the main supply's end is "
               f"not, so 'opposite end' cannot be evaluated. Reading which end "
               f"of the stack the main lands on would settle it.")
    elif src == main:
        verdict = "fail"
        why = (f"Both the {source} and the main supply are recorded at the "
               f"{src} of the busbar, which is what the rule forbids.")
    else:
        verdict = "pass"
        why = (f"The {source} is at the {src} of the busbar and the main "
               f"supply at the {main}: opposite ends, as the rule requires.")
    return {
        "requirement": POSITION_REQUIREMENT,
        "source": source,
        "source_breaker_position": src,
        "main_supply_position": main,
        "verdict": verdict,
        "reading": why,
        "what_would_settle_it": settle if verdict == "not_determined" else None,
    }


def source_current_basis(sources):
    """The rule's own 125%-of-output figures, beside the ratings used here.

    705.12(B)(3)(2) counts "125 percent of the power-source(s) output circuit
    current"; the arithmetic in this module counts each source's OVERCURRENT
    DEVICE RATING instead. On this equipment the ratings are the larger figures
    -- a 50 A breaker on an array whose 125%-of-output is 49.22 A, a 60 A
    breaker on a battery whose 125%-of-output is 59.90 A -- so using them is
    conservative and no published figure moves. That is worth showing rather
    than leaving to chance: a reader can see the direction of the difference,
    and a future source whose breaker is SMALLER than its code figure would
    show up here instead of quietly making the answer optimistic.

    `sources` is [(name, ocpd_rating_a, output_circuit_current_a, basis)].
    """
    rows = []
    for name, rating_a, output_a, output_basis in sources:
        code_a = output_a * NEC_705_12_SOURCE_FACTOR
        rows.append({
            "source": name,
            "ocpd_rating_a": _r(rating_a, 2),
            "output_circuit_current_a": _r(output_a, 4),
            "code_figure_125pct_of_output_a": _r(code_a, 4),
            "output_basis": output_basis,
            "rating_is_at_or_above_the_code_figure": bool(
                rating_a >= code_a - 1e-9),
            "rating_minus_code_figure_a": _r(rating_a - code_a, 4),
        })
    # None, not True, on an empty list: "every source is conservative" said of
    # no sources is a claim with nothing behind it, and vacuous truth is exactly
    # the shape of assertion this artifact refuses everywhere else.
    all_conservative = (all(r["rating_is_at_or_above_the_code_figure"]
                            for r in rows) if rows else None)
    return {
        "rule_as_written": NEC_RULES["705.12(B)(3)(2)"],
        "the_rule_counts": ("125 percent of the power-source(s) output circuit "
                            "current"),
        "this_calculation_counts": ("the rating of each source's overcurrent "
                                    "device"),
        "sources": rows,
        "every_rating_is_at_or_above_the_code_figure": all_conservative,
        "reading": (
            "No source's output circuit current is recorded here, so the two "
            "figures cannot be compared and nothing is claimed about the "
            "direction of the substitution." if all_conservative is None else
            "Every source's breaker rating is at or above the code's "
            "125%-of-output figure, so counting ratings spends MORE of the "
            "120% allowance than the rule requires and no verdict here rests "
            "on the substitution."
            if all_conservative else
            "At least one source's breaker rating is BELOW the code's "
            "125%-of-output figure, so counting ratings understates what the "
            "rule counts. The figures above are the ones to work from."),
    }


def busbar_120_percent(busbar_a, main_a, existing_backfeed_a,
                       basis=BACKFEED_READ, source_position=None,
                       main_position=None, source=SOURCE_EXISTING_PV,
                       sources=()):
    """NEC 705.12(B)(3)(2): the 120% arithmetic AND the position condition.

    busbar x 120% - main OCPD bounds the total backfeed a panel may accept; the
    existing PV breaker has already spent part of it. That is the first
    condition. The second -- the backfeed breaker at the opposite end of the
    busbar from the main -- is evaluated alongside it and defaults to
    `not_determined`, so the arithmetic can never be read as a compliant
    verdict on its own.

    `source_position` and `source` are about the breaker whose interconnection
    is being evaluated, which for a proposed battery is the PROPOSED breaker,
    not the one already on the bus.

    `existing_backfeed_a` is 0.0 both where the panel was surveyed and nothing
    backfeeds it and where nobody was asked; `basis` says which, and it is the
    difference between a complete allowance and an upper bound on one.
    """
    backfeed_known(basis)     # rejects a token this function cannot describe
    allowed = busbar_a * NEC_705_12_BUSBAR_FACTOR
    total_backfeed = allowed - main_a
    remaining = total_backfeed - existing_backfeed_a
    return {
        "rule": (f"{nec_rule('705.12(B)(3)(2)')}"),
        "rule_reading": (
            "The 120% arithmetic and the breaker-position condition are one "
            "conjunctive rule, and both are evaluated below."),
        "formula": ("remaining = busbar * 1.20 - main_ocpd - existing_backfeed"),
        "busbar_rating_a": busbar_a,
        "busbar_x_120pct_a": _r(allowed, 1),
        "main_ocpd_a": main_a,
        "total_backfeed_allowed_a": _r(total_backfeed, 1),
        "existing_pv_backfeed_a": existing_backfeed_a,
        "existing_pv_backfeed_basis": basis,
        "existing_pv_backfeed_note": BACKFEED_NOTE[basis],
        "remaining_backfeed_a": _r(remaining, 1),
        "remaining_backfeed_kva": _r(remaining * SERVICE_VOLTAGE_V / 1000.0, 2),
        "remaining_backfeed_is_the_ampacity_leg_only": (
            "A positive remainder satisfies the arithmetic half of the rule. "
            "The position condition below is the other half and both must "
            "hold."),
        "source_current_basis": source_current_basis(sources),
        "position_condition": position_condition(source_position,
                                                 main_position, source),
    }


def standard_circuit_for(output_a):
    """Smallest standard OCPD rating that carries `output_a` continuously."""
    for r in STANDARD_OCPD_A:
        if output_a <= r * CONTINUOUS_DERATE + 1e-9:
            return r
    raise SystemExit(f"service_headroom.py: no standard OCPD carries "
                     f"{output_a} A continuously")


def evse_code_load_a(output_a):
    """NEC 625.42: EVSE is a continuous load, so 125% of its rated output."""
    return output_a * NEC_625_42_FACTOR


def availability(service_rating_a, socket_rating_a, calc_a):
    """Headroom per ampacity constraint that APPLIES, at one calculated load.

    The meter socket appears only where a continuous rating was RECORDED, so
    nothing downstream can take a minimum over a placeholder. Its absence from
    the map is therefore ambiguous on its own -- a socket with no printed rating
    and a socket nobody read produce the same map -- and every consumer of this
    map is handed `socket_basis` alongside it to say which. See BINDING_IS.
    """
    out = {"service": _r(service_rating_a - calc_a)}
    if socket_rating_a is not None:
        out["meter_socket"] = _r(socket_rating_a - calc_a)
    return out


def remaining_headroom(avail, fixed_a, socket_basis):
    """What one case's fixed loads leave of an availability map.

    `avail` carries one entry per ampacity constraint with a NUMBER behind it --
    the service rating always, the meter socket only where a continuous rating
    was recorded. The binding figure is the minimum over those, never over a
    placeholder.

    `binding_is` travels with the number because the minimum alone cannot say
    whether it is the binding constraint or merely the tightest one anybody
    asked about. Where the socket was never read, the figure is an upper limit
    and says so at every exit rather than at one summary the reader may not
    reach.
    """
    if socket_basis not in BINDING_IS:
        raise SystemExit(f"service_headroom.py: {socket_basis!r} is not a "
                         f"meter-socket basis; expected one of "
                         f"{sorted(BINDING_IS)}")
    rem = {k: _r(v - fixed_a) for k, v in avail.items()}
    return {"vs_service_rating": rem["service"],
            "vs_meter_socket": rem.get("meter_socket"),
            "binding": _r(min(rem.values())),
            "binding_is": BINDING_IS[socket_basis]}


VERDICT_BASIS = (
    "Three-valued because the gross-load reconstruction is a BOUND wherever an "
    "interval's own hour is not point-determined -- inside a producing hour, "
    "where hourly production is credited to one quarter-hour, and on every "
    "hour the production export does not cover, where only the inverters' AC "
    "nameplate bounds it. pass = the case fits even on the conservative "
    "upper-bound basis; fail = it does not fit even on the measured, "
    "point-determined maximum; not_determined = it fits on one basis and not "
    "the other, so the answer lies inside the width of the reconstruction and "
    "the data does not decide it.")


def verdict_basis(binding):
    """VERDICT_BASIS plus which of the two the binding interval actually is."""
    return f"{VERDICT_BASIS} {binding['reading']}"


def battery_verdict(ampacity_leg, position_leg):
    """The interconnection verdict from BOTH legs of NEC 705.12(B)(3)(2).

    Conjunctive: either leg failing fails the panel, and either leg lacking its
    evidence makes the whole thing `not determined`, never "fits". Both legs are
    three-valued, so the verdict names which one is undecided rather than
    implying the other carried it.
    """
    if ampacity_leg == "fail" or position_leg == "fail":
        return "FAILS as the panel stands"
    undecided = []
    if ampacity_leg == "not_determined":
        undecided.append("the 120% allowance was computed as though nothing "
                         "already backfeeds the busbar")
    if position_leg == "not_determined":
        undecided.append("the breaker-position condition has no evidence "
                         "behind it")
    if undecided:
        return (f"NOT DETERMINED -- {nec('705.12(B)(3)(2)')} is conjunctive "
                f"and " + "; and ".join(undecided))
    return "fits within the 120% allowance"


def ampacity_verdict(measured_binding, conservative_binding):
    """pass / fail / not_determined, from the headroom on BOTH bases.

    A boolean here would be asserted from the measured basis alone, and the
    artifact's own sensitivity section shows that flipping to the upper bound
    moves the calculated load by more than a second EVSE is worth. A pass that
    does not survive the uncertainty the same artifact discloses is not a pass.
    """
    if conservative_binding > 0:
        return "pass"
    if measured_binding <= 0:
        return "fail"
    return "not_determined"


PHYSICAL_FIT_BASIS = (
    "Three-valued, for the same reason the ampacity verdict is. A 240 V circuit "
    "needs two ADJACENT full-size spaces, and the panel schedule records "
    "devices and their pole counts, not the slot positions those devices "
    "occupy. A count of free spaces can therefore establish a SHORTAGE -- fewer "
    "free spaces than the case needs is a fail whatever their arrangement -- "
    "but it can never establish a fit, because adjacency is not in the data. "
    "`pass` is reserved for a panel whose recorded slot positions show the "
    "adjacent pair.")

PHYSICAL_FIT_SETTLE = (
    "Slot positions in the panel schedule: which stab positions each device "
    "occupies, read off the panel and recorded per device, so the free spaces "
    "can be tested for adjacency instead of counted. No schedule in this intake "
    "carries them -- this household's is annotated 'position map partial' -- "
    "and no field for them is invented here.")


def physical_fit(new_2pole_breakers, spaces_free, adjacent_free_pairs):
    """fail / not_determined / pass on the breaker spaces one case needs.

    `adjacent_free_pairs` is the number of adjacent free full-size PAIRS
    established from the panel's recorded slot positions, or None where the
    schedule does not record positions -- which is every schedule this intake
    carries today. It is passed in rather than defaulted, so a panel that gains
    positions is one explicit change away from a determinable answer and nothing
    silently starts asserting one before then.

    Too few free spaces is a fail on the count alone: adjacency cannot rescue a
    shortage. Enough free spaces is NOT a pass, because two free spaces at
    opposite ends of the stack do not accept a 2-pole breaker.

    A case needing NO new breaker is decided before adjacency is consulted: a
    configuration that lands no device in the panel needs no adjacent pair, so
    an unrecorded slot map cannot leave it undetermined. The order used to be
    the other way round and returned `not_determined` for a case with nothing
    to fit.
    """
    if 2 * new_2pole_breakers > spaces_free:
        return "fail"
    if new_2pole_breakers == 0:
        return "pass"
    if adjacent_free_pairs is None:
        return "not_determined"
    return "pass" if new_2pole_breakers <= adjacent_free_pairs else "fail"


AMPACITY_LEG_BASIS = (
    "Three-valued, and which of the three turns on what the intake actually "
    "says about the existing backfeed. A rating read off the breaker, and an "
    "explicit null meaning the panel was surveyed and nothing backfeeds it, are "
    "both ANSWERS: the remaining allowance is complete and the leg resolves to "
    "pass or fail on it. With panel.pv_backfeed_a absent from the intake the "
    "existing backfeed is instead set to 0 A as though nothing backfed the "
    "panel, which makes the allowance the LARGEST it could be -- a shortfall "
    "against the largest "
    "possible allowance is a real shortfall, so `fail` still survives, but a "
    "fit does not and reads `not_determined` until the question is answered.")

AMPACITY_LEG_SETTLE = (
    "An answer to panel.pv_backfeed_a, which the intake does not carry at all: "
    "the ampere rating of the breaker(s) already backfeeding this busbar, or an "
    "explicit null if the panel was surveyed and nothing does. The allowance "
    "above was computed as though nothing did, which is the most generous "
    "reading available; a recorded null would confirm that reading and decide "
    "this leg, and a recorded rating would shrink the allowance.")


def busbar_ampacity_leg(breaker_a, remaining_a, basis):
    """The arithmetic leg of NEC 705.12(B)(3)(2), three-valued.

    See AMPACITY_LEG_BASIS: the asymmetry is the point. An unrecorded backfeed
    only ever makes the allowance look bigger, so a failure on it is sound and a
    fit on it is an assumption wearing a verdict's clothes. A surveyed null is
    not that case -- it is a measurement of zero, and a fit on it is a fit.
    """
    if breaker_a > remaining_a:
        return "fail"
    return "pass" if backfeed_known(basis) else "not_determined"


# Finding the existing A/C circuit in the panel schedule.
#
# The schedule's `label` is a transcribed door legend and is private-only
# intake, so it is searched with a DECLARED token list and only the resulting
# bare ampere rating is published. No label, no device marking and no
# per-device row leaves this function.
#
# The token list itself is NOT published either, and that is not fastidiousness:
# the private-only leak scan caught it. A token short enough to be useful ("a/c")
# is exactly what a door legend says, so publishing the list republishes one
# household's label verbatim. The count of matching entries is an aggregate and
# is published; the words are not.
# Deliberately short: a token has to be specific enough that an unrelated
# legend cannot contain it by accident ("a-c" sits inside "Sauna-Cabin"), and
# every match is counted rather than the first one taken, so a token that is too
# loose shows up as an ambiguity rather than as a wrong number.
AC_LABEL_TOKENS = ("a/c", "air cond", "air-cond", "condenser")

AC_READ = "read_off_the_schedule"
AC_NO_MATCH = "no_schedule_label_matched"
AC_AMBIGUOUS = "more_than_one_schedule_label_matched"
AC_NOT_ONE_DEVICE = "the_matched_entry_is_a_twin_density_device"

AC_SETTLE = (
    "The condenser's own nameplate -- its rated-load amps and minimum circuit "
    "ampacity -- or an intake answer naming the A/C circuit's overcurrent "
    "device directly. Reading it out of the door legend is a match on words, "
    "and words are what this cannot resolve.")


def existing_ac_ocpd(schedule):
    """The existing A/C branch device's ampere rating, three-valued.

    Four outcomes, and only one of them is a number:

      * exactly one full-size entry whose label matches an air-conditioning
        token -- its rating, read;
      * NO entry matched. That is NOT "this panel has no A/C": a legend that
        says CONDENSER, HP or nothing at all reads the same way from here, and
        the two mean opposite things for the credit below. It reports
        not_determined rather than a zero credit;
      * MORE than one matched. Taking the first was silently choosing which
        device the answer describes -- with two matching entries, which one the
        measured maximum contains is not established;
      * the matched entry is twin-density, so it carries a list of ratings
        rather than one device's rating.

    The count of matches is published; nothing else about the rows is.
    """
    matches = [e for e in schedule
               if any(t in str(e.get("label", "")).lower()
                      for t in AC_LABEL_TOKENS)]
    if not matches:
        return {
            "ocpd_a": None, "basis": AC_NO_MATCH, "matches": 0,
            "reading": (
                "NOT DETERMINED -- no entry in the panel schedule carries a "
                "label matching any air-conditioning token. That is not the "
                "same as a panel with no air-conditioning circuit: a legend "
                "wording it differently reads identically from here, and the "
                "two mean opposite things for the credit below. No credit "
                "bound is published on either reading."),
            "what_would_settle_it": AC_SETTLE,
        }
    if len(matches) > 1:
        return {
            "ocpd_a": None, "basis": AC_AMBIGUOUS, "matches": len(matches),
            "reading": (
                f"NOT DETERMINED -- {len(matches)} schedule entries carry a "
                f"label matching an air-conditioning token. Which of them is "
                f"the air-conditioning load already inside the measured "
                f"maximum is not established, and taking the first would be "
                f"choosing silently."),
            "what_would_settle_it": AC_SETTLE,
        }
    if isinstance(matches[0]["amps"], list):
        return {
            "ocpd_a": None, "basis": AC_NOT_ONE_DEVICE, "matches": 1,
            "reading": (
                "NOT DETERMINED -- the matched entry is a twin-density device "
                "carrying more than one overcurrent device, so it has no "
                "single ampere rating to credit."),
            "what_would_settle_it": AC_SETTLE,
        }
    amps_a = float(matches[0]["amps"])
    return {
        "ocpd_a": amps_a, "basis": AC_READ, "matches": 1,
        "reading": (
            f"One schedule entry matches an air-conditioning token, and its "
            f"branch overcurrent device is rated {amps_a:.0f} A. The rating is "
            f"the only thing taken from that row; the device marking and the "
            f"door-legend label are private-tier intake and stay there."),
        "what_would_settle_it": None,
    }


SUM_RULE_SETTLE = (
    "A complete device-by-device panel schedule, verified against the panel "
    "itself: the sum below counts the overcurrent devices the intake recorded, "
    "and a schedule cannot establish about itself that it missed nothing.")


def sum_of_breakers_rule(branch_ocpd_sum_a, busbar_a, proposed_breaker_a):
    """NEC 705.12(B)(3)(3) -- the sum-of-breakers alternative, three-valued.

    (B)(3)(3) is the rule that sums the overcurrent devices on the busbar.
    (B)(3)(1) is a different test entirely -- 125% of source output plus the
    busbar's own overcurrent device against the busbar ampacity -- and citing
    it here named the wrong rule for the arithmetic below.

    The sum counts every overcurrent device on the busbar other than the main,
    which for THIS question includes the proposed battery breaker: the rule is
    being asked as a compliance path for adding it, so leaving it out would
    score a panel that does not exist.

    A sum taken over the recorded schedule is a LOWER bound on the true sum -- a
    device the schedule missed adds to it and never subtracts -- so a sum
    already over the busbar rating is a real failure. A sum under it rests on
    the schedule being a complete enumeration, which the schedule cannot
    establish about itself, so it reads `not_determined`.
    """
    total = branch_ocpd_sum_a + proposed_breaker_a
    verdict = "fail" if total > busbar_a else "not_determined"
    return {
        "rule": nec_rule("705.12(B)(3)(3)"),
        "branch_ocpd_sum_a": branch_ocpd_sum_a,
        "proposed_battery_breaker_a": _r(proposed_breaker_a, 1),
        "counted_sum_a": _r(total, 1),
        "busbar_rating_a": busbar_a,
        "verdict": verdict,
        "verdict_basis": (
            "The proposed battery breaker is counted: this rule is only being "
            "asked as a way to add it. The recorded schedule can only "
            "understate the true sum, so a sum already over the busbar rating "
            "fails outright, while a sum under it depends on the schedule "
            "having missed no device and is not asserted as a pass."),
        "what_would_settle_it": (SUM_RULE_SETTLE
                                 if verdict == "not_determined" else None),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def _gate(name, observed, comparison, threshold, passed, requirement, catches):
    """One named conservation gate, with the number it was judged against.

    Every gate carries what was observed, what it had to satisfy, and what a
    breach would mean. A check whose threshold is not written down is not a
    check a reader can disagree with.
    """
    return {
        "gate": name,
        "observed": observed,
        "comparison": comparison,
        "threshold": threshold,
        "passed": bool(passed),
        "requirement": requirement,
        "catches": catches,
    }


def conservation_check(pv, excluded_days):
    """Daily derived PV against the two independent production references, GATED.

    This is the AC-1 closure: the revenue meter and the Enphase consumption CT
    together imply a production series, and the Enphase production CT and
    PVOutput each measured one. Three instruments, one quantity.

    The residuals are computed and then JUDGED, against thresholds declared at
    module level with the observed values and the failure each is meant to
    catch. A breach raises SystemExit, so a shifted, rescaled, stale or
    partially overlapping reference stops the run instead of being written out
    under a sentence saying the reconstruction is sound. The narrative reading
    is assembled from the outcomes; nothing is asserted that was not tested.

    Two tiers, and they are labelled as two tiers in the artifact:
      * GATES halt the run. They are set well outside ordinary instrument
        disagreement, so tripping one means an instrument problem.
      * CLAIMS are tested and shape the reading but do not halt. "The derived
        series sits inside the spread between the references" is one of these:
        a derived total sitting slightly outside a tight spread while every
        residual gate holds is a reason to write a different sentence, not a
        reason to refuse to publish.
    """
    daily = collections.defaultdict(float)
    for (d, _h), v in pv.items():
        if d in excluded_days:
            continue
        daily[d] += v
    with open(THREEWAY, newline="") as fh:
        rd = csv.reader(fh)
        head = next(rd)
        cols = head[1:]
        ref = {}
        for row in rd:
            d = dt.date.fromisoformat(row[0])
            ref[d] = [float(x) for x in row[1:]]
    common = sorted(d for d in daily if d in ref)
    if not common:
        raise SystemExit("service_headroom.py: derived PV and "
                         "threeway_production_validation.csv share no days")
    out = {"days_compared": len(common),
           "first_day": str(common[0]), "last_day": str(common[-1]),
           "dst_days_excluded": sorted(str(d) for d in excluded_days),
           "derived_total_kwh": _r(sum(daily[d] for d in common), 1),
           "against": {}}
    n = len(common)
    a = [daily[d] for d in common]
    ma = sum(a) / n
    for j, c in enumerate(cols):
        b = [ref[d][j] for d in common]
        mb = sum(b) / n
        sa = sum((x - ma) ** 2 for x in a) ** 0.5
        sb = sum((x - mb) ** 2 for x in b) ** 0.5
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        out["against"][c] = {
            "reference_total_kwh": _r(sum(b), 1),
            "ratio_derived_over_reference": _r(sum(a) / sum(b), 5),
            "residual_pct": _r((sum(a) / sum(b) - 1.0) * 100.0, 3),
            "mae_kwh_per_day": _r(sum(abs(x - y) for x, y in zip(a, b)) / n, 4),
            "correlation": _r(cov / (sa * sb), 5),
        }
    refs = list(out["against"])
    t = {c: out["against"][c]["reference_total_kwh"] for c in refs}
    out["references_disagree_pct"] = _r(
        abs(t[refs[0]] - t[refs[1]]) / min(t.values()) * 100.0, 2) if len(refs) == 2 else None

    # --- gates: these halt the run -----------------------------------------
    gates = [_gate(
        "minimum_overlapping_days", n, ">=", CONSERVATION_MIN_DAYS,
        n >= CONSERVATION_MIN_DAYS,
        f"the derived series and each reference must share at least "
        f"{CONSERVATION_MIN_DAYS} days",
        "a reference export that only partly overlaps the meter window -- a "
        "truncated pull or the wrong year -- which shrinks the comparison to a "
        "handful of days while every ratio still looks respectable")]
    for c in refs:
        m = out["against"][c]
        gates.append(_gate(
            f"abs_residual_pct_vs_{c}", _r(abs(m["residual_pct"]), 3), "<=",
            CONSERVATION_MAX_ABS_RESIDUAL_PCT,
            abs(m["residual_pct"]) <= CONSERVATION_MAX_ABS_RESIDUAL_PCT,
            f"the derived total must be within "
            f"{CONSERVATION_MAX_ABS_RESIDUAL_PCT}% of {c}",
            "a rescaled series -- DC read for AC, a unit error, a different "
            "system -- and a reference from the wrong year"))
        gates.append(_gate(
            f"mae_kwh_per_day_vs_{c}", m["mae_kwh_per_day"], "<=",
            CONSERVATION_MAX_MAE_KWH_PER_DAY,
            m["mae_kwh_per_day"] <= CONSERVATION_MAX_MAE_KWH_PER_DAY,
            f"the mean absolute daily error against {c} must not exceed "
            f"{CONSERVATION_MAX_MAE_KWH_PER_DAY} kWh/day",
            "a time-shifted series, which leaves the annual total and the "
            "ratio almost unchanged while the day-by-day error jumps"))
        gates.append(_gate(
            f"correlation_vs_{c}", m["correlation"], ">=",
            CONSERVATION_MIN_CORRELATION,
            m["correlation"] >= CONSERVATION_MIN_CORRELATION,
            f"daily production must track {c} at r >= "
            f"{CONSERVATION_MIN_CORRELATION}",
            "a stale or misaligned series, which loses the weather-driven "
            "day shape long before it loses its total"))
    failed = [g for g in gates if not g["passed"]]
    out["gates"] = gates
    out["gates_passed"] = len(gates) - len(failed)
    out["gates_total"] = len(gates)
    out["gates_are"] = (
        "halting checks. A breach raises SystemExit and the artifact is "
        "written atomically, so a failed conservation check publishes nothing.")
    if failed:
        raise SystemExit(
            "service_headroom.py: the energy-conservation check FAILED "
            f"({len(failed)} of {len(gates)} gates) -- " + "; ".join(
                f"{g['gate']}: observed {g['observed']}, must be "
                f"{g['comparison']} {g['threshold']}" for g in failed) +
            ". The gross-load reconstruction cannot be published as verified "
            "against the production references; nothing was written.")

    # --- claims: tested, not asserted; they shape the reading --------------
    inside = None
    closer = None
    if len(refs) == 2:
        lo, hi = min(t.values()), max(t.values())
        derived = out["derived_total_kwh"]
        inside = lo <= derived <= hi
        closer = all(abs(out["against"][c]["residual_pct"])
                     < out["references_disagree_pct"] for c in refs)
    out["claims_tested"] = {
        "claims_are": (
            "tested statements that shape the sentence below. They do not halt "
            "the run: a derived total just outside a tight reference spread, "
            "with every residual gate holding, calls for a different sentence "
            "rather than a refusal to publish."),
        "derived_total_sits_inside_the_reference_spread": inside,
        "reference_spread_kwh": ([_r(min(t.values()), 1), _r(max(t.values()), 1)]
                                 if len(refs) == 2 else None),
        "each_reference_is_closer_to_the_reconstruction_than_to_the_other":
            closer,
    }

    worst = max(refs, key=lambda c: abs(out["against"][c]["residual_pct"]))
    best = min(refs, key=lambda c: abs(out["against"][c]["residual_pct"]))
    parts = [
        f"All {len(gates)} conservation gates passed on {n} overlapping days "
        f"({out['first_day']} to {out['last_day']}). The derived series "
        f"differs from {best} by "
        f"{abs(out['against'][best]['residual_pct'])}% and from {worst} by "
        f"{abs(out['against'][worst]['residual_pct'])}%, against a "
        f"{CONSERVATION_MAX_ABS_RESIDUAL_PCT}% threshold, and tracks both at "
        f"r >= {min(out['against'][c]['correlation'] for c in refs)}."]
    if inside is True:
        parts.append(
            f"The derived total sits inside the spread between the two "
            f"reference instruments, which disagree with each other by "
            f"{out['references_disagree_pct']}%"
            + (", more than either disagrees with the reconstruction"
               if closer else "")
            + ". On this window the reconstruction is not the weakest link in "
              "the chain.")
    elif inside is False:
        parts.append(
            f"The derived total sits OUTSIDE the spread between the two "
            f"references, which disagree with each other by "
            f"{out['references_disagree_pct']}%, so it cannot be called the "
            f"middle of the three. The gates above are what the reconstruction "
            f"rests on here, not its position between the instruments.")
    else:
        parts.append(
            "With fewer than two reference instruments there is no spread to "
            "sit inside, so no claim is made about one.")
    out["reading"] = " ".join(parts)
    return out


def enphase_peak_invariant(sam_max_kw, sam_max_at, peak_kw, envelope_max_kw):
    """The Enphase consumption CT's hourly maximum, checked rather than claimed.

    Two statements, of different strength, both enforced.

      PHYSICS. An hourly mean cannot exceed the largest 15-minute mean inside
      the same hour, and the true 15-minute maximum cannot exceed the top of
      the gross-load envelope. So `sam_max <= envelope_max` is an invariant.
      A violation means the two instruments are not describing the same house
      at the same times -- a shifted, rescaled or wrong-year export -- and
      neither the reconstruction nor the corroboration can be trusted.

      PUBLICATION PRECONDITION. `sam_max <= peak_kw` is NOT a physical law:
      `peak_kw` is the maximum over the LOWER bounds, and where the true peak
      falls in daylight the lower bound understates it. But every headroom
      figure in this artifact is computed from `peak_kw`, so an independent
      instrument reading a HIGHER hourly mean would mean the headline maximum
      demand, the 220.87 calculated load and every case verdict resting on
      them are optimistic. That is the one direction a capacity answer must
      never fail in, so the run stops instead of publishing it.

    Both raise SystemExit. The margins are reported either way, so the reader
    can see how much room the checks actually had.
    """
    checks = [
        _gate("enphase_hourly_max_within_the_envelope", _r(sam_max_kw, 3), "<=",
              _r(envelope_max_kw, 3), sam_max_kw <= envelope_max_kw + 1e-9,
              "the hourly gross maximum cannot exceed the top of the "
              "15-minute gross-load envelope",
              "a shifted, rescaled or wrong-year consumption-CT export -- this "
              "one is physics, not a convention"),
        _gate("enphase_hourly_max_within_the_headline_peak", _r(sam_max_kw, 3),
              "<=", _r(peak_kw, 3), sam_max_kw <= peak_kw + 1e-9,
              "the hourly gross maximum must not exceed the point-determined "
              "15-minute maximum this artifact publishes as maximum demand",
              "a headline maximum demand contradicted from above by an "
              "independent instrument, which would make every headroom figure "
              "and every case verdict optimistic"),
    ]
    failed = [c for c in checks if not c["passed"]]
    if failed:
        raise SystemExit(
            "service_headroom.py: the independent-corroboration check FAILED "
            "-- " + "; ".join(
                f"{c['gate']}: Enphase hourly maximum {c['observed']} kW at "
                f"{sam_max_at}, must be {c['comparison']} {c['threshold']} kW"
                for c in failed) +
            ". The consumption CT and the reconstruction disagree in the "
            "direction that would make this answer optimistic; nothing was "
            "written.")
    return {
        "instrument": "Enphase consumption CT, hourly whole-home gross",
        "max_hourly_mean_kw": _r(sam_max_kw, 3),
        "at": sam_max_at,
        "checks": checks,
        "margin_below_the_headline_peak_kw": _r(peak_kw - sam_max_kw, 3),
        "margin_below_the_envelope_top_kw": _r(envelope_max_kw - sam_max_kw, 3),
        "reading": (
            f"An hourly mean can only understate a 15-minute peak. This one "
            f"sits {_r(peak_kw - sam_max_kw, 3)} kW below the point-determined "
            f"15-minute maximum and {_r(envelope_max_kw - sam_max_kw, 3)} kW "
            f"below the top of the envelope, which is the direction "
            f"consistency requires. Both comparisons are enforced: a "
            f"consumption CT reading above either one stops the run rather "
            f"than being published beside a sentence saying the instruments "
            f"agree."),
    }


def build():
    # The applicability flag comes FIRST, before any input is opened. A
    # household that told the intake it is not adding new load has no panel
    # survey to read and should not be stopped by the absence of one -- see
    # NEW_LOAD_FLAG_CONTRACT. Absent is not false.
    if _flag(NEW_LOAD_FLAG) is False:
        return not_applicable()

    panel = load_panel()
    # The second applicability flag, read inside load_panel() because it decides
    # whether charger.kw is read at all. Only an explicit false disables the
    # EVSE half of the analysis; everything else here runs either way.
    has_ev = panel["has_ev"]
    raw = only_match("Electric_15_Minute_*.csv", "Green Button 15-minute export")
    sam_paths = sorted(RAW_DIR.glob("enphase_sam8760_*.csv"))
    if not sam_paths:
        raise SystemExit(f"service_headroom.py: no enphase_sam8760_*.csv in "
                         f"{RAW_DIR} -- gross load cannot be reconstructed from "
                         f"the revenue meter alone")

    intervals = load_intervals(raw)
    days = sorted({d for d, _h, _c, _g in intervals})
    R.validate_interval_coverage([(d, h) for d, h, _c, _g in intervals],
                                 days[0], days[-1])
    sam, sam_prov = load_sam(sam_paths)
    hsums = hourly_sums(intervals)
    dst = dst_dates_in(set(days))
    pv = derive_pv(hsums, sam, dst)

    # The per-interval PV ceiling is the inverters' AC nameplate, a physical
    # bound -- not the largest observed hourly production, which is not one.
    # The empirical maxima are gathered as corroboration and any of them
    # exceeding the nameplate stops the run.
    kw_ac = load_pv_ac_nameplate()
    max_export_kw = max(exp for _d, _hf, _imp, exp in intervals) * 4.0
    with open(PVOUTPUT_5MIN, newline="") as fh:
        pvo_max_w = max(float(r["POWER_OUT"]) for r in csv.DictReader(fh))
    ac_ceiling = pv_ac_ceiling(
        kw_ac,
        HH.get("solar.inverter_model", required=False),
        HH.get("solar.inverter_count", required=False),
        [("derived hourly PV", max(pv.values()),
          "largest hourly production implied by the meter and the consumption "
          "CT, as a mean over the hour"),
         ("15-minute meter export", max_export_kw,
          "largest export the revenue meter recorded in one quarter-hour, a "
          "lower bound on production in that interval"),
         (f"5-minute inverter output ({PVOUTPUT_5MIN.name})", pvo_max_w / 1000.0,
          "largest 5-minute AC output the monitoring feed recorded")])
    env = gross_envelope(intervals, pv, kw_ac)

    n = len(env)
    zero_export = sum(1 for e in env if e[4] == 0.0)
    exact = sum(1 for e in env if e[5])
    # The two ceiling bases, counted -- and for the nameplate ones, WHY the hour
    # is uncovered. An uncovered interval is bounded by the nameplate alone, so
    # the split is the reader's handle on how much of the envelope's width is
    # measurement-narrowed and how much is the bare physical cap.
    ceiling_split = ceiling_basis_split(env, dst, sam)

    peak = max(env, key=lambda e: e[2])
    peak_kw = peak[2]
    over = [e for e in env if e[3] > peak_kw + 1e-9]
    env_max = max(e[3] for e in env)

    # WHICH interval sets the conservative basis, and on which PV ceiling. Every
    # not_determined verdict below is computed against env_max, so what bounded
    # that one interval decides what the conservative basis means and what would
    # settle a case that turns on it -- see binding_upper_interval().
    binding = binding_upper_interval(env, dst, sam, kw_ac,
                                     ceiling_split["enphase_coverage_lag"])
    case_settle = what_would_settle_it(
        binding, ceiling_split["enphase_coverage_lag"])

    # Independent corroboration: the Enphase consumption CT is a direct gross
    # reading. Both comparisons are enforced, not asserted -- see
    # enphase_peak_invariant().
    day_set = set(days)
    sam_max_key = max((k for k in sam if k[0] not in dst and k[0] in day_set),
                      key=lambda k: sam[k])
    corroboration = enphase_peak_invariant(
        sam[sam_max_key], f"{sam_max_key[0]} {sam_max_key[1]:02d}:00",
        peak_kw, env_max)

    # What was running at the annual peak. Whole-house 15-minute data cannot
    # answer it, and the question was previously left unanswered rather than
    # answered "not determined" -- which reads as though nobody asked. The
    # shape of the peak IS in the series, so what the data does show is
    # computed and published beside the not_determined; no appliance is named,
    # because naming one would be exactly the attribution the data cannot make.
    peak_ix = max(range(len(env)), key=lambda i: env[i][2])
    around = [{"timestamp_local": fmt_ts(env[j][0], env[j][1]),
               "gross_kw_lower_bound": _r(env[j][2]),
               "point_determined": bool(env[j][5])}
              for j in range(max(0, peak_ix - 2),
                             min(len(env), peak_ix + 3))]
    neighbour_max = max(r["gross_kw_lower_bound"] for r in around
                        if r["timestamp_local"] != fmt_ts(peak[0], peak[1]))
    evse_kw = panel["charger_kw"]
    peak_attribution = {
        "verdict": "not_determined",
        "reading": (
            "Which loads were running at the annual maximum cannot be "
            "determined from this data. The revenue meter and the consumption "
            "CT both read the WHOLE HOUSE; neither separates one circuit from "
            "another, and no quantity in this artifact identifies an "
            "appliance. Nothing here names one."),
        "what_would_settle_it": (
            "Circuit-level submetering on the panel's branch circuits, or a "
            "whole-house feed sampled fast enough to disaggregate loads by "
            "their switching signatures. Either one attributes the peak; "
            "15-minute whole-house energy cannot."),
        "what_the_series_does_show": {
            "intervals_around_the_peak": around,
            "peak_kw": _r(peak_kw),
            "largest_neighbouring_interval_kw": neighbour_max,
            "peak_minus_largest_neighbour_kw": _r(peak_kw - neighbour_max),
            "existing_evse_rated_kw": evse_kw,
            "peak_minus_existing_evse_rated_kw": (
                None if evse_kw is None else _r(peak_kw - float(evse_kw))),
            "reading": (
                f"The maximum is a single-interval spike: the quarter-hours "
                f"around it run "
                + " -> ".join(f"{r['gross_kw_lower_bound']}" for r in around)
                + f" kW, so the peak stands {_r(peak_kw - neighbour_max)} kW "
                f"above its largest neighbour."
                + ("" if evse_kw is None else
                   f" It also stands {_r(peak_kw - float(evse_kw))} kW above "
                   f"the {evse_kw} kW rating of the home EV charger "
                   f"(charger.kw), so the charger drawing its full rated power "
                   f"does not account for the interval on its own -- something "
                   f"else was drawing at the same time. WHAT else is not "
                   f"determined.")),
        },
    }

    # Maximum demand: full window and per calendar month.
    monthly = {}
    for e in env:
        ym = f"{e[0].year:04d}-{e[0].month:02d}"
        if ym not in monthly or e[2] > monthly[ym][2]:
            monthly[ym] = e
    # None means EITHER "read, no rating printed" OR "never read". The basis
    # says which, and it travels with every headroom computed below: the socket
    # is the tighter constraint wherever it exists, so an unasked question must
    # not read as one that does not apply.
    socket_a = panel["meter_socket_continuous_a"]
    socket_basis = socket_basis_of(panel)
    steps = nec_220_87_steps(peak_kw, panel["service_rating_a"],
                             socket_a, len(days), socket_basis)
    conditions = nec_220_87_conditions(len(days), kw_ac)
    a_calc = steps[1]["result_a"]

    # The measured basis is the headline. The conservative basis is the same
    # arithmetic on the top of the gross-load envelope, and every case verdict
    # is computed on both -- see ampacity_verdict().
    a_calc_upper = _r(amps(env_max) * NEC_220_87_FACTOR)
    avail = availability(panel["service_rating_a"], socket_a, a_calc)
    avail_upper = availability(panel["service_rating_a"], socket_a, a_calc_upper)

    # An explicit null pv_backfeed_a means the panel was surveyed and nothing
    # backfeeds it: 0 A of spent allowance, known. An ABSENT key is the same 0 A
    # and not known. The artifact says which, and the ampacity leg reads it.
    existing_backfeed_a, backfeed_basis = existing_backfeed(panel)

    # None, not zero, where there is no EV: a second charger is not a load of
    # 0 A, it is a scenario that does not apply. Nothing downstream may quietly
    # add it to a case.
    evse2_a = evse_code_load_a(EXISTING_EVSE_OUTPUT_A) if has_ev else None
    batt_a = amps(BATTERY_INVERTER_KW) * NEC_625_42_FACTOR
    # The position leg here is about the PROPOSED battery breaker, which has no
    # surveyed position. The existing PV breaker's end is a fact about the
    # device already on the bus and is reported separately -- inheriting it
    # would let the battery read as compliant because someone else's breaker
    # happens to sit opposite the main.
    batt_breaker_a = standard_circuit_for(amps(BATTERY_INVERTER_KW))
    # The rule counts 125% of each source's OUTPUT CIRCUIT CURRENT; this
    # arithmetic counts breaker ratings. Both figures go into the artifact so
    # the direction of the difference is visible -- see source_current_basis().
    # The existing source appears only where its backfeed rating was actually
    # read: with nothing recorded there is no source to compare.
    busbar_sources = []
    if backfeed_basis == BACKFEED_READ:
        busbar_sources.append((
            SOURCE_EXISTING_PV, existing_backfeed_a, amps(kw_ac),
            f"solar.kw_ac, the array's inverter AC nameplate: {kw_ac:.2f} kW "
            f"at {SERVICE_VOLTAGE_V:.0f} V"))
    busbar_sources.append((
        SOURCE_PROPOSED_BATTERY, batt_breaker_a, amps(BATTERY_INVERTER_KW),
        f"the unit's continuous power rating, {BATTERY_INVERTER_KW} kW at "
        f"{SERVICE_VOLTAGE_V:.0f} V"))
    busbar = busbar_120_percent(panel["busbar_rating_a"],
                                panel["service_rating_a"],
                                existing_backfeed_a,
                                backfeed_basis,
                                panel["battery_breaker_position"],
                                panel["main_breaker_position"],
                                SOURCE_PROPOSED_BATTERY,
                                busbar_sources)
    existing_pv_position = position_condition(panel["pv_breaker_position"],
                                              panel["main_breaker_position"],
                                              SOURCE_EXISTING_PV)

    occ = panel_occupancy(panel["schedule"], panel["spaces"], panel["max_circuits"])

    # No schedule this intake carries records slot positions, so the number of
    # ADJACENT free full-size pairs is not established for this panel. Passed
    # explicitly to physical_fit() rather than defaulted there: recording
    # positions later is one change at one named place, and nothing starts
    # asserting a fit before then.
    adjacent_free_pairs = None

    def case(name, fixed_a, new_2pole_breakers, solves_for_heat_pump, note):
        """One scenario. `fixed_a` is the sum of the code values of the loads
        that ARE specified; what is left is either spare headroom or, where a
        heat pump is part of the case, the largest MCA that would fit.

        Both bases are reported and the verdict is taken from both. The
        measured basis is what a reader should plan against; the conservative
        one is what decides whether the plan survives the reconstruction's own
        disclosed width."""
        measured = remaining_headroom(avail, fixed_a, socket_basis)
        conservative = remaining_headroom(avail_upper, fixed_a, socket_basis)
        verdict = ampacity_verdict(measured["binding"], conservative["binding"])
        spaces_needed = 2 * new_2pole_breakers
        fit = physical_fit(new_2pole_breakers, occ["spaces_free"],
                           adjacent_free_pairs)
        # What the remaining figure IS depends on its sign: a negative number is
        # not "the largest MCA that fits", it is the amount by which the case
        # has already overrun the calculated headroom.
        if measured["binding"] <= 0:
            remaining_is = (
                "a SHORTFALL, not headroom: the loads already specified in this "
                "case exceed the calculated headroom by this much, so no heat "
                "pump fits on either basis" if solves_for_heat_pump else
                "a SHORTFALL, not headroom: this case exceeds the calculated "
                "headroom by this much")
        elif conservative["binding"] <= 0:
            remaining_is = (
                "the largest heat-pump MCA that fits ON THE MEASURED BASIS; on "
                "the conservative basis the case is already over and the figure "
                "there is a shortfall" if solves_for_heat_pump else
                "spare headroom on the measured basis; on the conservative "
                "basis the figure is a shortfall")
        else:
            remaining_is = ("the largest heat-pump MCA that fits"
                            if solves_for_heat_pump else
                            "spare headroom above the calculated load")
        return {
            "case": name,
            "fixed_added_load_a": _r(fixed_a),
            "remaining_headroom_a": {
                "measured_basis": measured,
                "conservative_basis": conservative,
                "measured_basis_is": (
                    "the point-determined 15-minute maximum, the headline "
                    "figure"),
                "conservative_basis_is": conservative_basis_is(binding),
            },
            "remaining_is": remaining_is,
            "ampacity_verdict": verdict,
            "ampacity_verdict_basis": verdict_basis(binding),
            "what_would_settle_it": (case_settle
                                     if verdict == "not_determined" else None),
            "spaces": {
                "new_2pole_breakers_required": new_2pole_breakers,
                "full_size_spaces_required": spaces_needed,
                "spaces_free": occ["spaces_free"],
                "adjacent_free_pairs": adjacent_free_pairs,
                "physical_fit": fit,
                "physical_fit_basis": PHYSICAL_FIT_BASIS,
                "what_would_settle_it": (PHYSICAL_FIT_SETTLE
                                         if fit == "not_determined" else None),
                "note": (
                    "Every case published here ADDS equipment, and each added "
                    "240 V load -- a heat pump, a second EVSE, a battery -- "
                    "needs its own 2-pole breaker and therefore two adjacent "
                    "free spaces. A heat pump that REPLACES the existing A/C "
                    "on that circuit is a different configuration and is not "
                    "modelled here, so nothing in this artifact says what it "
                    "would need; see noncoincident_loads for the demand-side "
                    "half of that scenario."),
            },
            "note": note,
        }

    # Both legs of 705.12(B)(3)(2), computed before the cases because the
    # battery case's own note reports which constraint decided it and must not
    # assert that from a fixed string.
    amp_leg = busbar_ampacity_leg(batt_breaker_a,
                                  busbar["remaining_backfeed_a"],
                                  backfeed_basis)
    pos_leg = busbar["position_condition"]["verdict"]
    batt_verdict = battery_verdict(amp_leg, pos_leg)

    battery_case_note = (
        f"The battery is counted on the DEMAND side at {batt_a:.2f} A. "
        f"{BATTERY_CHARGING_BASIS} "
        + ("The 120% busbar rule fails independently of this arithmetic, "
           "so ampacity is not what decides the battery here."
           if amp_leg == "fail" else
           "The 120% busbar rule is evaluated separately under "
           "battery_inverter; neither constraint is assumed to decide the "
           "other."))

    heat_pump_case = case(
        "heat_pump_only", 0.0, 1, True,
        f"No heat pump has been selected, so the term is solved for rather "
        f"than assumed: this is the largest minimum circuit ampacity that "
        f"fits. MCA is the figure marked on the equipment nameplate "
        f"({nec('440.4(B)')}) and the one its conductors are sized to "
        f"({nec('440.35')}); it already embeds the 125% on the largest motor. "
        f"Conservative -- it adds the heat pump on top of a measured maximum "
        f"that already contains the existing A/C.")

    # With no EV the second-charger term is not zero, it is ABSENT: the three
    # cases carrying it are reported not applicable rather than priced, and the
    # two that would otherwise duplicate heat_pump_only once that term is gone
    # are not published twice. What is left is the same arithmetic with one
    # fewer load in it -- the heat pump, and the heat pump beside the battery.
    if not has_ev:
        cases = [
            heat_pump_case,
            case("heat_pump_and_battery", batt_a, 2, True,
                 battery_case_note + " There is no second EVSE in this case: "
                 + NO_EV_REASON),
        ]
    else:
        cases = [
            heat_pump_case,
            case("second_evse_only", evse2_a, 1, False,
                 f"A second Tesla Wall Connector at "
                 f"{EXISTING_EVSE_OUTPUT_A:.0f} A continuous on its own "
                 f"{standard_circuit_for(EXISTING_EVSE_OUTPUT_A):.0f} A 2-pole "
                 f"circuit. {nec('625.42')} makes EVSE a continuous load, so the "
                 f"code value is {EXISTING_EVSE_OUTPUT_A:.0f} x 1.25 = "
                 f"{evse2_a:.0f} A. What remains is spare headroom, not a heat "
                 f"pump."),
            case("heat_pump_and_second_evse", evse2_a, 2, True,
                 "The second EVSE at its code value, with the heat pump solved "
                 "for against what is left."),
            case("heat_pump_second_evse_and_battery", evse2_a + batt_a, 3, True,
                 battery_case_note),
        ]

    # Noncoincident-load refinement, bounded rather than asserted.
    ac = existing_ac_ocpd(panel["schedule"])
    ac_ocpd = ac["ocpd_a"]
    summer_peaks = {ym: e[2] for ym, e in monthly.items()
                    if int(ym[5:]) in R.SUMMER_MONTHS}
    winter_peaks = {ym: e[2] for ym, e in monthly.items()
                    if int(ym[5:]) not in R.SUMMER_MONTHS}
    season_gap = _r(max(summer_peaks.values()) - max(winter_peaks.values()), 2)
    # Whether the annual maximum is cooling-shaped is a reading, not an input to
    # any figure, and the window it is read against is published beside it so a
    # reader can disagree with the boundary rather than guess at it.
    cooling_shaped = int(peak[1]) in COOLING_HOURS
    noncoincident = {
        "rule": nec_rule("220.60"),
        "the_second_sentence_matters_here": (
            f"{nec('220.60')} does not stop at 'count only the largest'. Where "
            "a motor or "
            "air-conditioning load is one of the noncoincident loads and is NOT "
            "the largest of them, the calculation still carries 125% of the "
            "larger of the motor or air-conditioning load. A heat pump swapped "
            "in for this A/C is exactly that pairing, so the second sentence is "
            "part of the rule that applies and is stated with the first."),
        "why_it_matters": (
            "A heat pump that REPLACES the existing A/C does not add its whole "
            "MCA: whatever the A/C was drawing is already inside the measured "
            "maximum. The conservative cases above ignore that credit. The "
            "replacement configuration itself is not modelled here -- no case "
            "in this artifact removes a load."),
        "existing_ac_ocpd_a": ac_ocpd,
        "existing_ac_ocpd_basis": ac["basis"],
        "existing_ac_ocpd_reading": ac["reading"],
        "existing_ac_ocpd_what_would_settle_it": ac["what_would_settle_it"],
        "schedule_entries_matching_an_air_conditioning_token": ac["matches"],
        "credit_bounds_a": {
            "low": 0.0,
            "high": (_r(ac_ocpd * NEC_220_87_FACTOR)
                     if ac_ocpd is not None else None)},
        "evidence_on_where_the_credit_sits": {
            "annual_peak_month": f"{peak[0].year:04d}-{peak[0].month:02d}",
            "annual_peak_hour": int(peak[1]),
            "cooling_hours": f"{COOLING_HOURS[0]:02d}:00-{COOLING_HOURS[-1]:02d}:59",
            "annual_peak_falls_in_the_cooling_hours": cooling_shaped,
            "max_summer_month_peak_kw": _r(max(summer_peaks.values())),
            "max_winter_month_peak_kw": _r(max(winter_peaks.values())),
            "summer_minus_winter_peak_kw": season_gap,
            "reading": (
                f"The annual maximum falls at {int(peak[1]):02d}:00 in "
                f"{peak[0].year:04d}-{peak[0].month:02d}, "
                + ("inside" if cooling_shaped else "outside")
                + f" the {COOLING_HOURS[0]:02d}:00-{COOLING_HOURS[-1]:02d}:59 "
                f"hours a condenser works hardest, and the largest "
                f"non-cooling-season month peaks {abs(season_gap)} kW "
                + ("below" if season_gap > 0 else "above")
                + " the largest cooling-season month. "
                + ("Cooling may therefore be part of the measured maximum, and "
                   "where the credit sits inside the bounds above is not "
                   "determined from whole-house data." if cooling_shaped else
                   "Neither fact points at cooling setting the measured "
                   "maximum, so the A/C credit at the peak reads as near the "
                   "low end of the bounds above and the conservative figure is "
                   "the one to use.")),
        },
        "not_determined": (
            "The A/C's actual draw at the moment of the annual peak cannot be "
            "separated from whole-house data. Sub-metering the condenser, or "
            "its nameplate RLA/MCA, would settle it."),
    }

    # Mitigations, computed. The first two are ways of adding a SECOND charger
    # more cheaply, so they exist only where there is a first one -- with
    # has_ev false they are named in scenarios_not_applicable instead of being
    # published as advice about equipment this household does not have.
    evse_mitigations = []
    if has_ev:
        share_reduction = evse2_a
        rate_table = []
        for out_a in WALL_CONNECTOR_OUTPUTS_A:
            code_a = evse_code_load_a(out_a)
            m_row = remaining_headroom(avail, code_a, socket_basis)
            c_row = remaining_headroom(avail_upper, code_a, socket_basis)
            row_verdict = ampacity_verdict(m_row["binding"], c_row["binding"])
            rate_table.append({
                "evse_output_a": _r(out_a, 1),
                "min_circuit_a": _r(standard_circuit_for(out_a), 1),
                "code_load_a": _r(code_a, 2),
                "headroom_left_vs_service_a": _r(avail["service"] - code_a),
                "headroom_left_vs_meter_socket_a": (
                    None if socket_a is None
                    else _r(avail["meter_socket"] - code_a)),
                # The same two bases every case carries. A row published on the
                # measured basis alone read as available at settings the
                # conservative basis does not fit, including the 48 A setting
                # the second_evse_only case itself calls not_determined.
                "headroom_left_measured_basis": m_row,
                "headroom_left_conservative_basis": c_row,
                "ampacity_verdict": row_verdict,
                "what_would_settle_it": (case_settle
                                         if row_verdict == "not_determined"
                                         else None),
            })
        settings_that_pass = [r["evse_output_a"] for r in rate_table
                              if r["ampacity_verdict"] == "pass"]
        # A shared second connector adds no code load, so the headroom it leaves
        # is the whole of the calculated headroom -- on BOTH bases, with the
        # same binding_is label and the same three-valued verdict every case
        # carries. Published as one figure used twice, because it is one figure.
        shared_verdict = ampacity_verdict(_r(min(avail.values())),
                                          _r(min(avail_upper.values())))
        shared_headroom = {
            "measured_basis": remaining_headroom(avail, 0.0, socket_basis),
            "conservative_basis": remaining_headroom(avail_upper, 0.0,
                                                     socket_basis),
            "ampacity_verdict": shared_verdict,
            "what_would_settle_it": (case_settle
                                     if shared_verdict == "not_determined"
                                     else None),
        }
        evse_mitigations = [
            {"mitigation": "EVSE load sharing",
             "basis": EVSE_SHARING_AMPS_BASIS,
             "added_load_without_a": _r(evse2_a),
             "added_load_with_a": 0.0,
             "reduction_a": _r(share_reduction),
             # Both bases, and the same binding_is label the cases carry. These
             # are the headroom a shared second connector leaves -- the sharing
             # group adds no code load, so what is left is what was there.
             "case_second_evse_only_headroom_a": shared_headroom,
             "case_both_heat_pump_mca_a": shared_headroom,
             "both_figures_are": (
                 "the headroom left once the sharing group adds no code load: "
                 "the second connector's demand term is 0 A, so what remains is "
                 "the whole of the calculated headroom on each basis. The "
                 "measured figure is what to plan against; the conservative one "
                 "is what the verdict is taken from."),
             "shares_the_existing_branch_circuit": None,
             "shares_the_existing_branch_circuit_not_determined":
                 EVSE_SHARING_CIRCUIT_NOT_DETERMINED,
             "physical_fit_if_it_shares_the_existing_circuit": (
                 "No new breaker and no new space: the second connector joins "
                 "the circuit already there, and the panel's "
                 f"{occ['spaces_free']} free full-size space(s) are not called "
                 "on at all."),
             "physical_fit_if_it_needs_its_own_circuit": (
                 f"A 2-pole branch circuit takes two ADJACENT full-size "
                 f"spaces. The panel has {occ['spaces_free']} free"
                 + (", short of two on the count alone, so it does not fit "
                    "whatever their arrangement -- it would take consolidating "
                    "existing circuits onto twin-density devices or adding a "
                    "subpanel."
                    if occ["spaces_free"] < 2 else
                    ", enough on the count, but the schedule records devices "
                    "rather than slot positions, so whether two of them are "
                    "adjacent is not established -- see the second_evse_only "
                    "case's physical_fit.")),
             "what_is_determined_here": (
                 "The amps. The added demand goes from "
                 f"{_r(evse2_a)} A to 0 A on {nec('625.42')}, and that figure does "
                 "not depend on how the connectors are wired. Whether a new "
                 "breaker is needed is a separate, unsettled question and is "
                 "not folded into the saving.")},
            {"mitigation": "Charge-rate limit on the second EVSE",
             "basis": (f"The connector's output is settable; the code value "
                       f"follows it directly at 125% ({nec('625.42')}), and "
                       f"the minimum circuit is the smallest standard OCPD "
                       f"({nec('240.6(A)')}) carrying that output at 80%."),
             "table": rate_table,
             "table_verdict_vocabulary": VERDICT_BASIS,
             "settings_that_pass_on_both_bases_a": settings_that_pass,
             "reading": (
                 f"{len(settings_that_pass)} of "
                 f"{len(WALL_CONNECTOR_OUTPUTS_A)} selectable settings fit on "
                 f"the conservative basis as well as the measured one"
                 + (f" -- up to {max(settings_that_pass):.0f} A."
                    if settings_that_pass else
                    ": none of them, so a second connector is not settled at "
                    "any setting.")
                 + " A row's headroom on the measured basis is not by itself a "
                   "verdict: the settings above that band fit on the measured "
                   "maximum and not on the conservative one, which is the same "
                   "not_determined the second_evse_only case reports."),
             "reduction_a_at_lowest_setting": _r(evse2_a - evse_code_load_a(
                 WALL_CONNECTOR_OUTPUTS_A[0]))},
        ]
    pcs_reduction = (existing_backfeed_a + batt_breaker_a
                     - busbar["total_backfeed_allowed_a"])
    mitigations = evse_mitigations + [
        {"mitigation": f"Power control system on the sources ({nec('705.13')})",
         "basis": ("A listed PCS limits the combined output of the PV and the "
                   "battery, so the interconnection is evaluated against the "
                   "PCS setting instead of the sum of the source breakers."),
         "counted_backfeed_without_a": _r(existing_backfeed_a + batt_breaker_a, 1),
         "counted_backfeed_with_a": busbar["total_backfeed_allowed_a"],
         "reduction_a": _r(pcs_reduction, 1),
         "largest_combined_output_within_the_120pct_allowance_kva": _r(
             busbar["total_backfeed_allowed_a"] * SERVICE_VOLTAGE_V / 1000.0, 2),
         "that_figure_is_not_a_compliant_design": (
             "It is what the 120% arithmetic leaves, nothing more. The "
             "breaker-position condition, the panel's listing, and whether a "
             "PCS is listed for this equipment are separate questions, none of "
             "them settled here.")},
    ]

    battery = {
        "unit": "Tesla Powerwall 3",
        "inverter_kw": BATTERY_INVERTER_KW,
        "continuous_output_a": _r(amps(BATTERY_INVERTER_KW), 2),
        "backfeed_breaker_a": _r(batt_breaker_a, 1),
        "busbar_120_percent": busbar,
        "ampacity_leg": amp_leg,
        "ampacity_leg_basis": AMPACITY_LEG_BASIS,
        "ampacity_leg_what_would_settle_it": (
            AMPACITY_LEG_SETTLE if amp_leg == "not_determined" else None),
        "position_leg": pos_leg,
        # Mirrored beside the leg, not left to be found inside
        # busbar_120_percent: a reader quoting the leg should not have to go
        # looking for what would settle it.
        "position_leg_what_would_settle_it":
            busbar["position_condition"]["what_would_settle_it"],
        "position_leg_is_about": (
            "the PROPOSED battery backfeed breaker's own position "
            "(panel.battery_breaker_position), not the existing PV breaker's. "
            "No position has been surveyed for a new breaker here, so this leg "
            "is not_determined on that ground alone -- see "
            "position_condition.what_would_settle_it."),
        "verdict": batt_verdict,
        "verdict_basis": (
            f"{nec('705.12(B)(3)(2)')} is conjunctive: the 120% arithmetic AND the "
            "opposite-end breaker position. A failure on either leg fails the "
            "panel, and the arithmetic alone is never a compliant verdict."),
        "shortfall_a": _r(batt_breaker_a - busbar["remaining_backfeed_a"], 1),
        "sum_rule": sum_of_breakers_rule(occ["branch_ocpd_sum_a"],
                                         panel["busbar_rating_a"],
                                         batt_breaker_a),
        "alternatives_not_evaluated_here": [
            f"supply-side (line-side) tap ahead of the main disconnect, "
            f"{nec('705.11')}",
            f"the {nec('705.12(B)(3)(3)')} sum-of-breakers rule, if the branch "
            f"total can be brought under the busbar rating",
            f"a listed power control system limiting combined source output, "
            f"{nec('705.13')}",
            "a main-breaker downgrade, which trades backfeed allowance against "
            "the demand headroom computed above",
        ],
        "alternatives_note": (
            "Routes a licensed electrician would price and evaluate. None of "
            "them is computed here, and listing one is not a recommendation "
            "for or against it."),
        "note": (
            f"The busbar rule fails on its own arithmetic, independently of "
            f"the {nec('220.87')} demand result, so it is what decides the "
            f"battery here."
            if amp_leg == "fail" else
            f"The busbar rule and the {nec('220.87')} demand headroom are "
            f"reported separately; neither is assumed to decide the other."),
    }

    # What this run did NOT answer, and on whose say-so. An intake flag that
    # switches a scenario off leaves a hole in the artifact; naming the hole is
    # the difference between "not applicable" and "quietly missing". Empty
    # where nothing was skipped -- the key is always present, so a reader never
    # has to infer from an absent key that everything ran.
    scenarios_not_applicable = []
    if not has_ev:
        scenarios_not_applicable = [
            {"item": item, "kind": kind, "reason": NO_EV_REASON,
             "flag": EV_FLAG, "flag_contract": NO_EV_CONTRACT,
             "to_enable_it": (
                 f"Set {EV_FLAG}: true in private/household.yaml and answer "
                 f"charger.kw (DATA-SOURCES-CHEATSHEET.md, charger_kw), then "
                 f"rerun analysis/service_headroom.py.")}
            for item, kind in (
                ("second_evse_only", "case"),
                ("heat_pump_and_second_evse", "case"),
                ("heat_pump_second_evse_and_battery", "case"),
                ("EVSE load sharing", "mitigation"),
                ("Charge-rate limit on the second EVSE", "mitigation"),
                ("panel.existing_evse_kw", "intake fact"),
                ("added_load_code_values.second_evse_a", "code value"),
            )]

    return {
        "caveat": CAVEAT,
        "scenarios_not_applicable": scenarios_not_applicable,
        "provenance": {
            "meter_export": raw.name,
            "enphase_consumption_ct": sam_prov,
            "production_reference": THREEWAY.name,
            "inverter_power_reference": PVOUTPUT_5MIN.name,
            "window_start": str(days[0]),
            "window_end": str(days[-1]),
            "window_days": len(days),
            "interval_rows": n,
            "interval_minutes": 15,
            "intervals_per_day": {str(k): v for k, v in sorted(
                collections.Counter(
                    collections.Counter(d for d, *_ in env).values()).items())},
            "dst_days": sorted(str(d) for d in dst),
            "service_voltage_v": SERVICE_VOLTAGE_V,
            "voltage_basis": ("120/240 V single-phase 3-wire residential "
                              "service; service amps taken across the 240 V "
                              "legs"),
            "timezone_handling": ("local wall clock as exported; no timezone "
                                  "conversion applied"),
        },
        "panel": {
            "service_rating_a": panel["service_rating_a"],
            "busbar_rating_a": panel["busbar_rating_a"],
            "meter_socket_continuous_a": socket_a,
            "meter_socket_basis": socket_basis,
            "meter_socket_constraint": SOCKET_CONSTRAINT[socket_basis],
            "meter_socket_what_would_settle_it": (
                SOCKET_SETTLE if socket_basis == SOCKET_NOT_RECORDED else None),
            "existing_pv_backfeed_a": existing_backfeed_a,
            "existing_pv_backfeed_basis": backfeed_basis,
            "existing_pv_backfeed_note": busbar["existing_pv_backfeed_note"],
            "pv_breaker_position": panel["pv_breaker_position"],
            "battery_breaker_position": panel["battery_breaker_position"],
            "battery_breaker_position_note": (
                "the PROPOSED battery/source breaker's end of the busbar, a "
                "separate intake field from the existing PV breaker's. Null "
                "means no position has been surveyed for a new breaker, which "
                "is what makes the battery's position leg not_determined; the "
                "existing PV breaker's end is never substituted for it"),
            "main_breaker_position": panel["main_breaker_position"],
            "existing_pv_position_condition": existing_pv_position,
            "existing_pv_position_condition_note": (
                f"{nec('705.12(B)(3)(2)')} applied to the breaker ALREADY on the "
                "bus. It is a fact about the existing interconnection and is "
                "not the battery's position leg, which is evaluated on the "
                "proposed breaker's own position under battery_inverter"),
            # None here is "this household has no EV", never "the charger's
            # power was not recorded": the flag that produced it, and every
            # scenario it switched off, are listed in
            # scenarios_not_applicable at the top of the artifact.
            "existing_evse_kw": panel["charger_kw"],
            "existing_evse_kw_basis": (
                "charger.kw, the home EVSE's rated power from the intake"
                if has_ev else f"NOT APPLICABLE -- {NO_EV_REASON}"),
            "occupancy": occ,
        },
        "gross_reconstruction": {
            "identity": "gross = import - export + pv",
            "intervals": n,
            "zero_export_intervals": zero_export,
            "zero_export_fraction_pct": _r(100.0 * zero_export / n, 3),
            "point_determined_intervals": exact,
            "point_determined_fraction_pct": _r(100.0 * exact / n, 3),
            "bounded_intervals": n - exact,
            "pv_ceiling_basis_split": ceiling_split,
            "pv_ac_ceiling": ac_ceiling,
            "max_lower_bound_kw": _r(peak_kw),
            "max_upper_bound_kw": _r(env_max),
            # The loud field. max_upper_bound_kw is the conservative basis, and
            # a reader is entitled to know at a glance whether the interval
            # setting it had a production measurement behind it at all.
            "max_upper_bound_is_set_by_an_hour_with_no_production_measurement":
                not binding["hour_has_a_production_measurement"],
            "max_upper_bound_binding_interval": binding,
            "intervals_whose_upper_bound_exceeds_the_peak": len(over),
            "intervals_whose_upper_bound_exceeds_the_peak_pct": _r(
                100.0 * len(over) / n, 3),
            "of_those_with_nonzero_export": sum(1 for e in over if e[4] > 0.0),
            "honesty": (
                "Gross load is EXACT only where the containing hour produced no "
                "PV and the interval exported none. Everywhere else it is a "
                "bound, and the upper end is loose for one of two reasons: "
                "inside a producing hour it credits that whole hour's measured "
                "production to a single quarter-hour, capped at what the "
                "inverters can physically deliver in fifteen minutes; and on an "
                "hour the Enphase file does not cover it carries that physical "
                "cap with nothing to narrow it. The second is the looser of the "
                "two -- see pv_ceiling_basis_split for how many such intervals "
                "there are, how far the production record stops short of the "
                "meter window, and why no empirical figure is allowed to narrow "
                "them. Which of the two sets the published maximum is not "
                "assumed: it is read off the binding interval and reported in "
                "max_upper_bound_binding_interval. " + binding["reading"]),
            "conservation": conservation_check(pv, dst),
        },
        "maximum_demand": {
            # Whether the peak is a point or a bound is a fact about the peak
            # interval, not a property of the method: it is read off that
            # interval rather than asserted, because a window whose maximum fell
            # in daylight would make the same sentence false.
            "basis": ("15-minute gross load, lower bound" + (
                ", which is exact at the maximum: the peak interval exported "
                "nothing and its hour produced nothing" if peak[5] else
                ", and at the maximum it is a BOUND rather than a point -- the "
                "peak interval sits in a producing hour, so the true gross load "
                "there is at least this and at most the upper bound reported "
                "beside it")),
            "peak_kw": _r(peak_kw),
            "peak_a": _r(amps(peak_kw)),
            "peak_timestamp_local": fmt_ts(peak[0], peak[1]),
            "peak_coincident": {
                "export_kwh": _r(peak[4]),
                "point_determined": bool(peak[5]),
                "upper_bound_kw": _r(peak[3]),
                "hour_of_day": int(peak[1]),
                "weekday": peak[0].strftime("%A"),
                "month": f"{peak[0].year:04d}-{peak[0].month:02d}",
                "tariff_season": ("summer" if peak[0].month in R.SUMMER_MONTHS
                                  else "winter"),
                "tou_period": R.period(peak[1], R.off_peak_day(peak[0])),
                "derived_pv_that_hour_kwh": _r(pv.get((peak[0], int(peak[1])), 0.0), 3),
            },
            "what_was_running": peak_attribution,
            "independent_corroboration": corroboration,
            "dst_guard": dst_guard(hsums, dst),
            "by_month": [
                {"month": ym,
                 "peak_kw": _r(e[2]),
                 "peak_a": _r(amps(e[2])),
                 "upper_bound_kw": _r(e[3]),
                 "timestamp_local": fmt_ts(e[0], e[1]),
                 "point_determined": bool(e[5])}
                for ym, e in sorted(monthly.items())],
        },
        "nec_220_87": {
            "rule": nec_rule("220.87"),
            "conditions": conditions,
            "measurement_days": len(days),
            "condition_1_days_required": NEC_220_87_CONDITION_1_DAYS,
            "window_note": (
                f"{len(days)} days of continuous 15-minute data, "
                f"{_r(len(days) / float(NEC_220_87_CONDITION_1_DAYS), 2)}x the "
                f"1-year period condition (1) requires. The 30-day recording "
                f"route is the Exception to (1), not the condition, and it is "
                f"closed to a service with a photovoltaic system -- so a year "
                f"is the only span that qualifies this household, and it has "
                f"one. See conditions."),
            "steps": steps,
            "calculated_load_a": a_calc,
            "headroom_a": {"vs_service_rating": avail["service"],
                           "vs_meter_socket": avail.get("meter_socket"),
                           "binding": _r(min(avail.values())),
                           "binding_is": BINDING_IS[socket_basis]},
            "sensitivity_on_the_upper_bound": {
                "why": ("If the true 15-minute maximum sat at the top of the "
                        "gross-load envelope rather than at the exact peak, the "
                        "answer would move by this much. It is a bound, not an "
                        "estimate: every interval's PV credit is capped at the "
                        "inverters' AC nameplate, so no quarter-hour is "
                        "credited with more production than the array can "
                        "physically deliver. The headline maximum stays the "
                        "measured one, but no case verdict is asserted from it "
                        "alone -- this is the conservative basis every verdict "
                        "is also computed on. " + binding["reading"]),
                "max_upper_bound_kw": _r(env_max),
                "binding_interval": binding,
                "max_upper_bound_is_set_by_an_hour_with_no_production_measurement":
                    not binding["hour_has_a_production_measurement"],
                "what_would_settle_it": case_settle,
                "calculated_load_a": a_calc_upper,
                "headroom_vs_service_a": avail_upper["service"],
                "headroom_vs_meter_socket_a": avail_upper.get("meter_socket"),
                "binding": _r(min(avail_upper.values())),
                "binding_is": BINDING_IS[socket_basis],
            },
        },
        "added_load_code_values": {
            "second_evse_a": _r(evse2_a, 2) if has_ev else None,
            "second_evse_basis": (
                f"{nec('625.42')} continuous load: {EXISTING_EVSE_OUTPUT_A:.0f} A "
                f"output x 1.25 on a "
                f"{standard_circuit_for(EXISTING_EVSE_OUTPUT_A):.0f} A 2-pole "
                f"circuit" if has_ev else
                f"NOT APPLICABLE -- {NO_EV_REASON}"),
            "battery_charging_a": _r(batt_a, 2),
            "battery_charging_basis": BATTERY_CHARGING_BASIS,
            "battery_charging_not_determined": BATTERY_CHARGING_NOT_DETERMINED,
            "heat_pump_a": None,
            "heat_pump_basis": (
                f"NOT DETERMINED -- no unit has been selected anywhere in this "
                f"project, and a nameplate cannot be invented. The heat-pump "
                f"term is solved for instead: each case reports the largest "
                f"minimum circuit ampacity that fits, MCA being the figure "
                f"marked on the equipment ({nec('440.4(B)')}) and the one its "
                f"conductors are sized to ({nec('440.35')})."),
            "heat_pump_what_would_settle_it": (
                "The selected unit's own nameplate: its marked minimum circuit "
                f"ampacity ({nec('440.4(B)')}), which is directly comparable "
                f"with the solved-for figure each case publishes. Until a unit "
                f"is chosen there is nothing to read, and the largest MCA that "
                f"fits is the answer this data can give."),
        },
        "cases": cases,
        "noncoincident_loads": noncoincident,
        "battery_inverter": battery,
        "mitigations": mitigations,
    }


def main():
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(OUT.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(result, fh, indent=1, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, OUT)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    if result.get("not_applicable"):
        print(f"wrote {OUT.relative_to(ROOT)}")
        print(f"  {result['reason']}")
        print(f"  {result['to_enable_it']}")
        return 0
    md = result["maximum_demand"]
    nec = result["nec_220_87"]
    print(f"wrote {OUT.relative_to(ROOT)}")
    skipped = result["scenarios_not_applicable"]
    if skipped:
        print(f"  {len(skipped)} scenario(s)/figure(s) not applicable "
              f"({skipped[0]['flag']} is false): "
              + ", ".join(s["item"] for s in skipped))
    print(f"  peak {md['peak_kw']} kW ({md['peak_a']} A) at "
          f"{md['peak_timestamp_local']}, point-determined="
          f"{md['peak_coincident']['point_determined']}")
    pan = result["panel"]
    socket = {
        SOCKET_READ: (f"{nec['headroom_a']['vs_meter_socket']} A vs the "
                      f"{pan['meter_socket_continuous_a']:.0f} A meter socket"
                      if pan["meter_socket_continuous_a"] is not None else ""),
        SOCKET_SURVEYED_NONE: ("the socket carries no printed continuous "
                               "rating, so the main is the only constraint"),
        SOCKET_NOT_RECORDED: ("the meter socket was never read, so this is an "
                              "UPPER LIMIT: a socket rating would tighten it"),
    }[pan["meter_socket_basis"]]
    print(f"  {NEC_220_87_LABEL} calculated load {nec['calculated_load_a']} A "
          f"-> headroom "
          f"{nec['headroom_a']['vs_service_rating']} A vs the "
          f"{pan['service_rating_a']:.0f} A main, {socket}")
    for c in result["cases"]:
        rem = c["remaining_headroom_a"]
        print(f"  {c['case']}: {rem['measured_basis']['binding']} A measured / "
              f"{rem['conservative_basis']['binding']} A conservative -- "
              f"{c['remaining_is']}  ({c['ampacity_verdict']} on ampacity, "
              f"{c['spaces']['physical_fit']} on "
              f"{c['spaces']['full_size_spaces_required']} spaces)")
    b = result["battery_inverter"]
    print(f"  battery: {b['verdict']} -- "
          f"{b['busbar_120_percent']['remaining_backfeed_a']} A of backfeed "
          f"left, {b['backfeed_breaker_a']} A breaker required "
          f"(ampacity {b['ampacity_leg']}, position {b['position_leg']})")
    occ = result["panel"]["occupancy"]
    print(f"  spaces: {occ['spaces_used']}/{occ['spaces_total']} used, "
          f"{occ['spaces_free']} free; poles {occ['pole_positions_used']}/"
          f"{occ['pole_positions_total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
