#!/usr/bin/env python3
"""Electrical service headroom from measured demand (NEC 220.87).

The question this answers: with a 175 A service already carrying a house, an EV
charger and a 10 kW PV array, is there room for a heat pump, a second EV charger,
or both -- and what happens if a battery inverter is added on top.

NEC 220.87 (existing dwelling unit) lets the calculated load be taken as the
ACTUAL maximum demand over at least 30 days times 125%, in place of a nameplate
220.82/220.83 calculation. A year of 15-minute revenue-meter data is exactly the
input that method wants, and this window is 395 days.

SCOPING ESTIMATE ONLY -- THE PERMIT CALCULATION BELONGS TO A LICENSED
ELECTRICIAN. NEC 220.87 requires that the maximum-demand data be acceptable to
the authority having jurisdiction; the AHJ may instead require a full 220.82 or
220.83 nameplate calculation, and only a licensed electrician working from the
actual equipment nameplates, conductor sizes and the panel's own labelling can
produce a number that will be permitted. Nothing here is a design, and no
equipment should be ordered against it.

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
    upper = import - export + min(pv_hour_bound, kw_ac * 0.25)

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
the containing hour produced no PV and the interval exported nothing. That is
where the annual maximum lives: every one of the 200 largest import intervals in
the window carries zero export and sits in an hour with zero derived production,
in hours 00-08 and 17-23. The peak is therefore an exact observation, not a
PV-masked one. In daylight the reconstruction is genuinely a bound and is
reported as one; the width is stated rather than papered over.

The headline maximum demand is the measured one and stays that way -- the upper
bound is loose by construction, because it credits a whole hour's production to
a single quarter-hour. But a PASS/FAIL verdict taken off the measured basis
alone would be asserting more than the data supports wherever the answer flips
inside the disclosed width. Every case verdict is therefore THREE-VALUED,
computed on both bases: `pass` where the case fits even on the conservative
upper-bound reconstruction, `fail` where it does not fit even on the
point-determined measured maximum, and `not_determined` where it fits on one and
not the other. What would settle a `not_determined` case is 15-minute production
data, which was never metered here.

Two joint-computation hazards are handled explicitly.

  DST. The Enphase file is a flat 8760-row grid; the meter is true wall clock
  (100 intervals on the fall-back Sunday, 92 on spring-forward, per
  rates.expected_day_hours). The two disagree only on those two days -- the
  Enphase file literally repeats one value at 01:00 and 02:00 of the fall-back
  day -- so those dates are excluded from every meter x Enphase computation and
  fall back to the empirical per-hour PV ceiling. They are NOT excluded from the
  maximum-demand search, which uses the meter alone.

  Zero padding. The current-year Enphase export is a full 8760 rows with the
  future zero-filled. The zero tail is truncated at the last nonzero row and
  never treated as measurement; treating it as data would invent hours of zero
  household load.

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
     result that passes every ampacity test;
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

Panel facts (service rating, busbar rating, spaces, max circuits, the device
schedule) are private-tier intake fields read through household.get(), which
fails closed. There are no defaults: a missing service rating stops the run
rather than producing a plausible-looking answer about a panel nobody measured.
Two fields are OPTIONAL because the intake contract documents them as nullable,
and each has a defined meaning when absent:

  * `panel.pv_backfeed_a` -- null means nothing backfeeds the panel, so the
    existing backfeed is 0 A. The artifact records that no source was declared
    rather than printing a bare zero;
  * `panel.meter_socket_continuous_a` -- null means the constraint does not
    apply (a separate meter enclosure with no rating printed), so the service
    rating is the only ampacity constraint. The socket is then omitted from the
    220.87 steps and from every headroom, never carried as a null in arithmetic.

`panel.pv_breaker_position`, `panel.battery_breaker_position` and
`panel.main_breaker_position` are optional too; their absence is what makes the
position condition `not_determined`.

`solar.kw_ac` is REQUIRED and has no default, for the reason given above.

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
# Code and equipment constants. Each carries its source; none is a guess.
# ---------------------------------------------------------------------------

# 120/240 V single-phase 3-wire residential service (the panel is a 120/240 V
# meter-main combination, meter class CL200). Amps at the service disconnect are
# taken across the 240 V legs: A = kW * 1000 / 240.
SERVICE_VOLTAGE_V = 240.0

# NEC 220.87: calculated load = maximum demand over >= 30 days x 125%.
NEC_220_87_FACTOR = 1.25
NEC_220_87_MIN_DAYS = 30

# NEC 625.42: EV supply equipment is a CONTINUOUS load, so its code-required
# value is 125% of its rated output.
NEC_625_42_FACTOR = 1.25

# NEC 705.12(B)(3)(2): busbar rating x 120% minus the main OCPD rating bounds
# the backfeed a panel may accept.
NEC_705_12_BUSBAR_FACTOR = 1.20

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
BATTERY_INVERTER_KW = 11.5

# Standard branch-circuit ampere ratings (NEC 240.6(A)) used to size a circuit
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

# At least 90 overlapping days. NEC 220.87 will not accept fewer than 30 days of
# demand data at all, and a conservation check resting on fewer days than the
# code's own measurement minimum is not evidence about the year; 90 days is a
# full season. Observed: 363. Catches a reference export that only partly
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

CAVEAT = (
    "Scoping estimate only. The permit calculation belongs to a licensed "
    "electrician. NEC 220.87 permits an existing dwelling's calculated load to "
    "be taken from measured maximum demand only where that data is acceptable "
    "to the authority having jurisdiction; the AHJ may instead require a full "
    "NEC 220.82/220.83 nameplate calculation. Conductor sizes, the actual "
    "nameplate ratings of the existing equipment, and the panel's listing "
    "restrictions were not verified here, and no equipment should be ordered "
    "against these figures.")


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


def load_panel():
    """The private-tier panel intake, through the fail-closed accessor.

    Most values here are physical facts about one specific panel with no sane
    default, so household.get() is called with required=True and a missing key
    stops the run: service rating, busbar rating, spaces, max circuits and the
    device schedule are all REQUIRED.

    The nullable fields are the exceptions the intake contract itself names --
    `pv_backfeed_a` (null when nothing backfeeds the panel),
    `meter_socket_continuous_a` (null when the socket carries no printed
    continuous rating) and the three breaker positions. Each is carried as None
    and handled explicitly downstream; none is coerced to a number that would
    read as a measurement.

    `battery_breaker_position` is the PROPOSED source breaker's end of the bus,
    a separate question from where the existing PV breaker sits. It is read
    separately and never filled in from `pv_breaker_position`.
    """
    return {
        "service_rating_a": float(HH.get("panel.service_rating_a")),
        "busbar_rating_a": float(HH.get("panel.busbar_rating_a")),
        "pv_backfeed_a": _optional_number("panel.pv_backfeed_a"),
        "meter_socket_continuous_a": _optional_number(
            "panel.meter_socket_continuous_a"),
        "pv_breaker_position": HH.get("panel.pv_breaker_position", required=False),
        "battery_breaker_position": HH.get("panel.battery_breaker_position",
                                           required=False),
        "main_breaker_position": HH.get("panel.main_breaker_position",
                                        required=False),
        "spaces": int(HH.get("panel.spaces")),
        "max_circuits": int(HH.get("panel.max_circuits")),
        "schedule": HH.get("panel.schedule"),
        "charger_kw": float(HH.get("charger.kw")),
    }


# ---------------------------------------------------------------------------
# Panel schedule geometry
# ---------------------------------------------------------------------------

def breaker_geometry(entry):
    """(spaces, poles, [ocpd_a, ...]) for one device in the panel schedule.

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
        raise SystemExit(f"service_headroom.py: schedule entry {entry.get('label')!r} "
                         f"lists {len(amps)} amp values for {poles} poles")
    if poles == 2:
        return 1, 2, [float(a) for a in amps]
    if poles == 4:
        if amps[0] != amps[3] or amps[1] != amps[2]:
            raise SystemExit(
                f"service_headroom.py: quad {entry.get('label')!r} has amps "
                f"{amps}, which is not an outer/inner common-trip pair")
        return 2, 4, [float(amps[0]), float(amps[1])]
    raise SystemExit(f"service_headroom.py: schedule entry {entry.get('label')!r} "
                     f"has {poles} poles with a list of amps -- only tandems (2) "
                     f"and quads (4) are twin-density devices")


def panel_occupancy(schedule, spaces, max_circuits):
    """Spaces and pole positions used vs available, plus the branch-OCPD sum.

    A panel can pass every ampacity test and still have nowhere to land a
    breaker, so this is reported alongside the amps and never folded into them.
    """
    used_spaces = used_poles = 0
    ocpd = []
    twin = 0
    for e in schedule:
        s, p, a = breaker_geometry(e)
        used_spaces += s
        used_poles += p
        ocpd += a
        if isinstance(e["amps"], list):
            twin += 1
    return {
        "devices": len(schedule),
        "twin_density_devices": twin,
        "spaces_total": spaces,
        "spaces_used": used_spaces,
        "spaces_free": spaces - used_spaces,
        "pole_positions_total": max_circuits,
        "pole_positions_used": used_poles,
        "pole_positions_free": max_circuits - used_poles,
        "branch_ocpd_sum_a": _r(sum(ocpd), 1),
        "largest_branch_ocpd_a": _r(max(ocpd), 1),
        "note": (
            "A 240 V circuit needs two adjacent full-size spaces; a tandem "
            "cannot host one. With "
            f"{spaces - used_spaces} space(s) free, adding a 2-pole breaker "
            "requires consolidating existing circuits onto twin-density "
            "devices or adding a subpanel, whatever the ampacity math says."),
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
    """({(date, hour): pv_kwh}, per-hour-of-day empirical ceiling).

    pv_hour = max(sam_hour - import_hour + export_hour, 0), which is the whole
    identity gross = import - export + pv rearranged. The clip at zero absorbs
    the instrument disagreement in dark hours, where the true value is zero and
    the residual can land either side of it.

    The per-hour-of-day ceiling is the largest production this array has ever
    delivered in that hour across the window. It is the fallback bound for the
    handful of hours the Enphase file does not cover, and it is what keeps a
    01:15 AM interval from being credited with a full quarter-hour of sunshine.
    """
    pv, ceiling = {}, {h: 0.0 for h in range(24)}
    for (d, h), (imp, exp, _n) in hsums.items():
        if d in excluded_days or (d, h) not in sam:
            continue
        v = max(sam[(d, h)] - imp + exp, 0.0)
        pv[(d, h)] = v
        ceiling[h] = max(ceiling[h], v)
    return pv, ceiling


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


def gross_envelope(intervals, pv, ceiling, ac_ceiling_kw):
    """[(date, hour_frac, lower_kw, upper_kw, export_kwh, exact)] per interval.

    lower = import: every kWh the PV made was exported, none self-consumed.
    upper = import - export + min(pv_hour_bound, ac_ceiling * 0.25): the whole
            hour's production landed inside this one quarter-hour, capped by
            the inverters' AC nameplate, which is what they can physically
            deliver in fifteen minutes.

    The two coincide -- gross is the metered import, exactly -- wherever the
    hour made no PV and the interval exported none. `exact` marks those.
    """
    out = []
    for d, hf, imp, exp in intervals:
        h = int(hf)
        bound = pv.get((d, h))
        if bound is None:
            bound = ceiling[h]
        pv_up = min(bound, ac_ceiling_kw * 0.25)
        lo = imp * 4.0
        up = max((imp - exp + pv_up) * 4.0, lo)
        out.append((d, hf, lo, up, exp, up - lo < 1e-9))
    return out


def fmt_ts(d, hf):
    """'2025-08-22 05:45' -- local wall clock, the meter's own basis."""
    return f"{d} {int(hf):02d}:{int(round((hf % 1) * 60)):02d}"


# ---------------------------------------------------------------------------
# NEC arithmetic
# ---------------------------------------------------------------------------

def amps(kw):
    """kW to amps at the service voltage."""
    return kw * 1000.0 / SERVICE_VOLTAGE_V


def nec_220_87_steps(max_demand_kw, service_rating_a, socket_rating_a, days):
    """The 220.87 chain, written out so the artifact shows the arithmetic.

    `socket_rating_a` is None where the meter socket carries no printed
    continuous rating. Step 4 is then OMITTED rather than emitted with a null
    on both sides of a subtraction: a constraint that does not apply produces
    no row, and the service rating stands alone.
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
         "label": "NEC 220.87 calculated load: maximum demand x 125%",
         "formula": "A_calc = A_measured * 1.25",
         "inputs": {"A_measured": _r(a_measured),
                    "factor": NEC_220_87_FACTOR,
                    "measurement_days": days,
                    "code_minimum_days": NEC_220_87_MIN_DAYS},
         "result_a": _r(a_calc)},
        {"step": 3,
         "label": "headroom against the main breaker rating",
         "formula": "headroom = service_rating - A_calc",
         "inputs": {"service_rating_a": service_rating_a, "A_calc": _r(a_calc)},
         "result_a": _r(service_rating_a - a_calc)},
    ]
    if socket_rating_a is not None:
        steps.append(
            {"step": 4,
             "label": ("headroom against the meter socket's continuous rating, "
                       "the tighter of the two constraints"),
             "formula": "headroom = meter_socket_continuous - A_calc",
             "inputs": {"meter_socket_continuous_a": socket_rating_a,
                        "A_calc": _r(a_calc)},
             "result_a": _r(socket_rating_a - a_calc)})
    return steps


# The two ends of a busbar. NEC 705.12(B)(3)(2) is a statement about which of
# them each supply lands on, so a position that reads as neither is not
# evidence about the condition.
BUSBAR_ENDS = ("top", "bottom")

POSITION_REQUIREMENT = (
    "NEC 705.12(B)(3)(2) has a second, conjunctive condition: the backfeed "
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
        why = (f"No readable end is recorded for the {source}. NEC "
               f"705.12(B)(3)(2) binds every source connected to the busbar, "
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


def busbar_120_percent(busbar_a, main_a, existing_backfeed_a,
                       backfeed_declared=True, source_position=None,
                       main_position=None, source=SOURCE_EXISTING_PV):
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

    `existing_backfeed_a` is 0.0 where nothing backfeeds the panel;
    `backfeed_declared` says which of the two a zero means.
    """
    allowed = busbar_a * NEC_705_12_BUSBAR_FACTOR
    total_backfeed = allowed - main_a
    remaining = total_backfeed - existing_backfeed_a
    return {
        "rule": ("NEC 705.12(B)(3)(2) -- 120% busbar allowance, plus the "
                 "breaker-position condition that goes with it"),
        "formula": ("remaining = busbar * 1.20 - main_ocpd - existing_backfeed"),
        "busbar_rating_a": busbar_a,
        "busbar_x_120pct_a": _r(allowed, 1),
        "main_ocpd_a": main_a,
        "total_backfeed_allowed_a": _r(total_backfeed, 1),
        "existing_pv_backfeed_a": existing_backfeed_a,
        "existing_pv_backfeed_declared": bool(backfeed_declared),
        "existing_pv_backfeed_note": (
            "read off the existing backfeed breaker" if backfeed_declared else
            "no existing backfeed source was declared in the intake, so the "
            "spent allowance is 0 A -- not a measured zero"),
        "remaining_backfeed_a": _r(remaining, 1),
        "remaining_backfeed_kva": _r(remaining * SERVICE_VOLTAGE_V / 1000.0, 2),
        "remaining_backfeed_is_the_ampacity_leg_only": (
            "A positive remainder satisfies the arithmetic half of the rule. "
            "The position condition below is the other half and both must "
            "hold."),
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

    The meter socket appears only where a continuous rating was recorded. A
    socket that does not apply is absent from the map rather than present as a
    None, so nothing downstream can take a minimum over it.
    """
    out = {"service": _r(service_rating_a - calc_a)}
    if socket_rating_a is not None:
        out["meter_socket"] = _r(socket_rating_a - calc_a)
    return out


def remaining_headroom(avail, fixed_a):
    """What one case's fixed loads leave of an availability map.

    `avail` carries one entry per ampacity constraint that APPLIES -- the
    service rating always, the meter socket only where a continuous rating was
    recorded. The binding figure is the minimum over the constraints that
    exist, never over a placeholder for one that does not.
    """
    rem = {k: _r(v - fixed_a) for k, v in avail.items()}
    return {"vs_service_rating": rem["service"],
            "vs_meter_socket": rem.get("meter_socket"),
            "binding": _r(min(rem.values()))}


VERDICT_BASIS = (
    "Three-valued because the gross-load reconstruction is a bound in daylight, "
    "not a point. pass = the case fits even on the conservative upper-bound "
    "basis; fail = it does not fit even on the measured, point-determined "
    "maximum; not_determined = it fits on one basis and not the other, so the "
    "answer lies inside the width of the reconstruction and the data does not "
    "decide it.")

WHAT_WOULD_SETTLE_IT = (
    "15-minute PV production, which was never metered here. The consumption CT "
    "reads hourly, so each daylight quarter-hour is reported as a bound whose "
    "upper end credits a whole hour's production to one interval. A 15-minute "
    "production series would collapse the bound to a point and decide this "
    "case either way.")


def battery_verdict(ampacity_leg, position_leg):
    """The interconnection verdict from BOTH legs of NEC 705.12(B)(3)(2).

    Conjunctive: either leg failing fails the panel, and a passing arithmetic
    leg with no position evidence is `not determined`, never "fits". The rule
    the artifact publishes and the rule the code applies have to be the same
    rule.
    """
    if ampacity_leg == "fail" or position_leg == "fail":
        return "FAILS as the panel stands"
    if position_leg == "not_determined":
        return ("NOT DETERMINED -- the 120% allowance is sufficient, but the "
                "breaker-position condition of NEC 705.12(B)(3)(2) has no "
                "evidence behind it")
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
    panel = load_panel()
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
    pv, ceiling = derive_pv(hsums, sam, dst)

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
    env = gross_envelope(intervals, pv, ceiling, kw_ac)

    n = len(env)
    zero_export = sum(1 for _d, _h, _lo, _up, exp, _e in env if exp == 0.0)
    exact = sum(1 for e in env if e[5])
    sam_hours = sum(1 for d, hf, *_ in env if (d, int(hf)) in pv)

    peak = max(env, key=lambda e: e[2])
    peak_kw = peak[2]
    over = [e for e in env if e[3] > peak_kw + 1e-9]
    env_max = max(e[3] for e in env)

    # Independent corroboration: the Enphase consumption CT is a direct gross
    # reading. Both comparisons are enforced, not asserted -- see
    # enphase_peak_invariant().
    sam_max_key = max((k for k in sam if k[0] not in dst and k[0] in set(days)),
                      key=lambda k: sam[k])
    corroboration = enphase_peak_invariant(
        sam[sam_max_key], f"{sam_max_key[0]} {sam_max_key[1]:02d}:00",
        peak_kw, env_max)

    # Maximum demand: full window and per calendar month.
    monthly = {}
    for e in env:
        ym = f"{e[0].year:04d}-{e[0].month:02d}"
        if ym not in monthly or e[2] > monthly[ym][2]:
            monthly[ym] = e
    socket_a = panel["meter_socket_continuous_a"]      # None => does not apply
    steps = nec_220_87_steps(peak_kw, panel["service_rating_a"],
                             socket_a, len(days))
    a_calc = steps[1]["result_a"]

    # The measured basis is the headline. The conservative basis is the same
    # arithmetic on the top of the daylight envelope, and every case verdict is
    # computed on both -- see ampacity_verdict().
    a_calc_upper = _r(amps(env_max) * NEC_220_87_FACTOR)
    avail = availability(panel["service_rating_a"], socket_a, a_calc)
    avail_upper = availability(panel["service_rating_a"], socket_a, a_calc_upper)
    avail_binding = _r(min(avail.values()))

    # A null pv_backfeed_a means nothing backfeeds the panel, which spends 0 A
    # of the allowance. The zero is stated as "nothing declared", not printed
    # as though a breaker had been read.
    backfeed_declared = panel["pv_backfeed_a"] is not None
    existing_backfeed_a = panel["pv_backfeed_a"] if backfeed_declared else 0.0

    evse2_a = evse_code_load_a(EXISTING_EVSE_OUTPUT_A)
    batt_a = amps(BATTERY_INVERTER_KW) * NEC_625_42_FACTOR
    # The position leg here is about the PROPOSED battery breaker, which has no
    # surveyed position. The existing PV breaker's end is a fact about the
    # device already on the bus and is reported separately -- inheriting it
    # would let the battery read as compliant because someone else's breaker
    # happens to sit opposite the main.
    busbar = busbar_120_percent(panel["busbar_rating_a"],
                                panel["service_rating_a"],
                                existing_backfeed_a,
                                backfeed_declared,
                                panel["battery_breaker_position"],
                                panel["main_breaker_position"],
                                SOURCE_PROPOSED_BATTERY)
    existing_pv_position = position_condition(panel["pv_breaker_position"],
                                              panel["main_breaker_position"],
                                              SOURCE_EXISTING_PV)
    batt_breaker_a = standard_circuit_for(amps(BATTERY_INVERTER_KW))

    occ = panel_occupancy(panel["schedule"], panel["spaces"], panel["max_circuits"])

    def case(name, fixed_a, new_2pole_breakers, solves_for_heat_pump, note):
        """One scenario. `fixed_a` is the sum of the code values of the loads
        that ARE specified; what is left is either spare headroom or, where a
        heat pump is part of the case, the largest MCA that would fit.

        Both bases are reported and the verdict is taken from both. The
        measured basis is what a reader should plan against; the conservative
        one is what decides whether the plan survives the reconstruction's own
        disclosed width."""
        measured = remaining_headroom(avail, fixed_a)
        conservative = remaining_headroom(avail_upper, fixed_a)
        verdict = ampacity_verdict(measured["binding"], conservative["binding"])
        spaces_needed = 2 * new_2pole_breakers
        return {
            "case": name,
            "fixed_added_load_a": _r(fixed_a),
            "remaining_headroom_a": {
                "measured_basis": measured,
                "conservative_basis": conservative,
                "measured_basis_is": (
                    "the point-determined 15-minute maximum, the headline "
                    "figure"),
                "conservative_basis_is": (
                    "the top of the daylight gross-load envelope: an upper "
                    "bound that credits a whole hour's production to one "
                    "quarter-hour, capped at the inverters' AC nameplate, "
                    "which is a physical ceiling on what the array can put on "
                    "the service in fifteen minutes"),
            },
            "remaining_is": ("the largest heat-pump MCA that fits"
                             if solves_for_heat_pump else
                             "spare headroom above the calculated load"),
            "ampacity_verdict": verdict,
            "ampacity_verdict_basis": VERDICT_BASIS,
            "what_would_settle_it": (WHAT_WOULD_SETTLE_IT
                                     if verdict == "not_determined" else None),
            "spaces": {
                "new_2pole_breakers_required": new_2pole_breakers,
                "full_size_spaces_required": spaces_needed,
                "spaces_free": occ["spaces_free"],
                "fits_without_panel_work": spaces_needed <= occ["spaces_free"],
                "note": ("A heat pump also needs a 2-pole breaker, so every "
                         "case here needs at least two adjacent free spaces."),
            },
            "note": note,
        }

    cases = [
        case("heat_pump_only", 0.0, 1, True,
             "No heat pump has been selected, so the term is solved for rather "
             "than assumed: this is the largest minimum circuit ampacity (NEC "
             "440.6 MCA, which already embeds the 125% on the largest motor) "
             "that fits. Conservative -- it adds the heat pump on top of a "
             "measured maximum that already contains the existing A/C."),
        case("second_evse_only", evse2_a, 1, False,
             f"A second Tesla Wall Connector at {EXISTING_EVSE_OUTPUT_A:.0f} A "
             f"continuous on its own "
             f"{standard_circuit_for(EXISTING_EVSE_OUTPUT_A):.0f} A 2-pole "
             f"circuit. NEC 625.42 makes EVSE a continuous load, so the code "
             f"value is {EXISTING_EVSE_OUTPUT_A:.0f} x 1.25 = {evse2_a:.0f} A. "
             f"What remains is spare headroom, not a heat pump."),
        case("heat_pump_and_second_evse", evse2_a, 2, True,
             "The second EVSE at its code value, with the heat pump solved for "
             "against what is left."),
        case("heat_pump_second_evse_and_battery", evse2_a + batt_a, 3, True,
             f"The battery is counted on the DEMAND side at its grid-charging "
             f"draw: {BATTERY_INVERTER_KW} kW = "
             f"{amps(BATTERY_INVERTER_KW):.2f} A, x1.25 continuous = "
             f"{batt_a:.2f} A. Ampacity is not the binding constraint here -- "
             f"the 120% busbar rule is, and it fails first."),
    ]

    # Noncoincident-load refinement, bounded rather than asserted.
    ac_entry = next((e for e in panel["schedule"]
                     if "a/c" in str(e.get("label", "")).lower()), None)
    ac_ocpd = float(ac_entry["amps"]) if ac_entry and not isinstance(
        ac_entry["amps"], list) else None
    summer_peaks = {ym: e[2] for ym, e in monthly.items()
                    if int(ym[5:]) in R.SUMMER_MONTHS}
    winter_peaks = {ym: e[2] for ym, e in monthly.items()
                    if int(ym[5:]) not in R.SUMMER_MONTHS}
    noncoincident = {
        "rule": ("NEC 220.60 -- space heating and air conditioning are "
                 "noncoincident loads, so only the larger is counted."),
        "why_it_matters": (
            "A heat pump that REPLACES the existing A/C does not add its whole "
            "MCA: whatever the A/C was drawing is already inside the measured "
            "maximum. The conservative cases above ignore that credit."),
        "existing_ac_ocpd_a": ac_ocpd,
        "credit_bounds_a": {"low": 0.0,
                            "high": _r(ac_ocpd * NEC_220_87_FACTOR) if ac_ocpd else None},
        "evidence_the_credit_is_small": {
            "annual_peak_month": f"{peak[0].year:04d}-{peak[0].month:02d}",
            "annual_peak_hour": int(peak[1]),
            "max_summer_month_peak_kw": _r(max(summer_peaks.values())),
            "max_winter_month_peak_kw": _r(max(winter_peaks.values())),
            "reading": (
                "The largest non-cooling-season month peaks within "
                f"{_r(max(summer_peaks.values()) - max(winter_peaks.values()), 2)} "
                "kW of the largest cooling-season month, and the annual maximum "
                "falls in an early-morning hour rather than an afternoon "
                "cooling hour. The measured maximum is not cooling-driven, so "
                "the A/C credit at the peak is near the low end of its bounds "
                "and the conservative figure is the one to use."),
        },
        "not_determined": (
            "The A/C's actual draw at the moment of the annual peak cannot be "
            "separated from whole-house data. Sub-metering the condenser, or "
            "its nameplate RLA/MCA, would settle it."),
    }

    # Mitigations, computed.
    share_reduction = evse2_a
    rate_table = []
    for out_a in WALL_CONNECTOR_OUTPUTS_A:
        code_a = evse_code_load_a(out_a)
        rate_table.append({
            "evse_output_a": _r(out_a, 1),
            "min_circuit_a": _r(standard_circuit_for(out_a), 1),
            "code_load_a": _r(code_a, 2),
            "headroom_left_vs_service_a": _r(avail["service"] - code_a),
            "headroom_left_vs_meter_socket_a": (
                None if socket_a is None else _r(avail["meter_socket"] - code_a)),
        })
    pcs_reduction = (existing_backfeed_a + batt_breaker_a
                     - busbar["total_backfeed_allowed_a"])
    mitigations = [
        {"mitigation": "EVSE load sharing on the existing circuit",
         "basis": ("Tesla Wall Connectors share power across one circuit, and "
                   "NEC 625.42 permits an EVSE load-management system to be "
                   "sized to the system's maximum output rather than the sum "
                   "of the connectors."),
         "added_load_without_a": _r(evse2_a),
         "added_load_with_a": 0.0,
         "reduction_a": _r(share_reduction),
         "case_second_evse_only_headroom_a": avail_binding,
         "case_both_heat_pump_mca_a": avail_binding,
         "also": ("It needs no new breaker, which matters more than the amps: "
                  "the panel does not have two adjacent free spaces for one.")},
        {"mitigation": "Charge-rate limit on the second EVSE",
         "basis": ("The connector's output is settable; the code value follows "
                   "it directly at 125% (NEC 625.42), and the minimum circuit "
                   "is the smallest standard OCPD carrying that output at 80%."),
         "table": rate_table,
         "reduction_a_at_lowest_setting": _r(evse2_a - evse_code_load_a(
             WALL_CONNECTOR_OUTPUTS_A[0]))},
        {"mitigation": "Power control system on the sources (NEC 705.13)",
         "basis": ("A listed PCS limits the combined output of the PV and the "
                   "battery, so the interconnection is evaluated against the "
                   "PCS setting instead of the sum of the source breakers."),
         "counted_backfeed_without_a": _r(existing_backfeed_a + batt_breaker_a, 1),
         "counted_backfeed_with_a": busbar["total_backfeed_allowed_a"],
         "reduction_a": _r(pcs_reduction, 1),
         "largest_compliant_combined_output_kva": _r(
             busbar["total_backfeed_allowed_a"] * SERVICE_VOLTAGE_V / 1000.0, 2)},
    ]

    # Both legs of 705.12(B)(3)(2). The ampacity leg is the one this panel
    # fails; the position leg cannot make a failing panel pass, and cannot be
    # skipped when the arithmetic does pass.
    amp_leg = ("fail" if batt_breaker_a > busbar["remaining_backfeed_a"]
               else "pass")
    pos_leg = busbar["position_condition"]["verdict"]
    batt_verdict = battery_verdict(amp_leg, pos_leg)

    battery = {
        "unit": "Tesla Powerwall 3",
        "inverter_kw": BATTERY_INVERTER_KW,
        "continuous_output_a": _r(amps(BATTERY_INVERTER_KW), 2),
        "backfeed_breaker_a": _r(batt_breaker_a, 1),
        "busbar_120_percent": busbar,
        "ampacity_leg": amp_leg,
        "position_leg": pos_leg,
        "position_leg_is_about": (
            "the PROPOSED battery backfeed breaker's own position "
            "(panel.battery_breaker_position), not the existing PV breaker's. "
            "No position has been surveyed for a new breaker here, so this leg "
            "is not_determined on that ground alone -- see "
            "position_condition.what_would_settle_it."),
        "verdict": batt_verdict,
        "verdict_basis": (
            "NEC 705.12(B)(3)(2) is conjunctive: the 120% arithmetic AND the "
            "opposite-end breaker position. A failure on either leg fails the "
            "panel, and the arithmetic alone is never a compliant verdict."),
        "shortfall_a": _r(batt_breaker_a - busbar["remaining_backfeed_a"], 1),
        "sum_rule": {
            "rule": ("NEC 705.12(B)(3)(1) -- sum of all breakers other than the "
                     "main must not exceed the busbar rating"),
            "branch_ocpd_sum_a": None,   # filled below from panel_occupancy
            "busbar_rating_a": panel["busbar_rating_a"],
        },
        "alternatives_not_recommended": [
            "supply-side (line-side) tap ahead of the main disconnect, NEC 705.11",
            "the 705.12(B)(3)(1) sum-of-breakers rule, if the branch total can "
            "be brought under the busbar rating",
            "a listed power control system limiting combined source output, "
            "NEC 705.13",
            "a main-breaker downgrade, which trades backfeed allowance against "
            "the demand headroom computed above",
        ],
        "note": ("The binding constraint on the battery is the busbar rule, not "
                 "demand headroom. It fails independently of the 220.87 result."),
    }

    battery["sum_rule"]["branch_ocpd_sum_a"] = occ["branch_ocpd_sum_a"]
    battery["sum_rule"]["passes"] = (occ["branch_ocpd_sum_a"]
                                     <= panel["busbar_rating_a"])

    return {
        "caveat": CAVEAT,
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
                              "service, meter class CL200; service amps taken "
                              "across the 240 V legs"),
            "timezone_handling": ("local wall clock as exported; no timezone "
                                  "conversion applied"),
        },
        "panel": {
            "service_rating_a": panel["service_rating_a"],
            "busbar_rating_a": panel["busbar_rating_a"],
            "meter_socket_continuous_a": socket_a,
            "meter_socket_constraint": (
                "applies -- headroom is reported against it alongside the "
                "service rating" if socket_a is not None else
                "does not apply -- no continuous rating is recorded for the "
                "meter socket, so the service rating is the only ampacity "
                "constraint and the socket is omitted from every headroom "
                "rather than carried as a null"),
            "existing_pv_backfeed_a": existing_backfeed_a,
            "existing_pv_backfeed_declared": backfeed_declared,
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
                "NEC 705.12(B)(3)(2) applied to the breaker ALREADY on the "
                "bus. It is a fact about the existing interconnection and is "
                "not the battery's position leg, which is evaluated on the "
                "proposed breaker's own position under battery_inverter"),
            "existing_evse_kw": panel["charger_kw"],
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
            "hours_with_enphase_reading": sam_hours,
            "intervals_on_the_empirical_hour_ceiling": n - sam_hours,
            "pv_ac_ceiling": ac_ceiling,
            "max_lower_bound_kw": _r(peak_kw),
            "max_upper_bound_kw": _r(env_max),
            "intervals_whose_upper_bound_exceeds_the_peak": len(over),
            "intervals_whose_upper_bound_exceeds_the_peak_pct": _r(
                100.0 * len(over) / n, 3),
            "of_those_with_nonzero_export": sum(1 for e in over if e[4] > 0.0),
            "honesty": (
                "Gross load is EXACT only where the containing hour produced no "
                "PV and the interval exported none; in daylight it is a bound, "
                "and the upper bound is loose because it credits a whole hour's "
                "production to a single quarter-hour, capped at what the "
                "inverters can physically deliver in fifteen minutes. The bound "
                "is reported, not resolved -- 15-minute production was never "
                "metered here."),
            "conservation": conservation_check(pv, dst),
        },
        "maximum_demand": {
            "basis": ("15-minute gross load, lower bound, which is exact at the "
                      "maximum: the peak interval exported nothing and its hour "
                      "produced nothing"),
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
            "rule": ("NEC 220.87 -- for an existing dwelling unit the calculated "
                     "load may be taken as the maximum demand measured over at "
                     "least 30 days, times 125%, in place of a nameplate "
                     "calculation, where the data is acceptable to the AHJ."),
            "measurement_days": len(days),
            "code_minimum_days": NEC_220_87_MIN_DAYS,
            "window_note": (f"{len(days)} days of continuous 15-minute data, "
                            f"{len(days) // NEC_220_87_MIN_DAYS}x the code "
                            f"minimum of {NEC_220_87_MIN_DAYS}"),
            "steps": steps,
            "calculated_load_a": a_calc,
            "headroom_a": {"vs_service_rating": avail["service"],
                           "vs_meter_socket": avail.get("meter_socket"),
                           "binding": avail_binding},
            "sensitivity_on_the_upper_bound": {
                "why": ("If the true 15-minute maximum sat at the top of the "
                        "daylight envelope rather than at the exact peak, the "
                        "answer would move by this much. It is a bound, not an "
                        "estimate: the daylight cap is the inverters' AC "
                        "nameplate, so no quarter-hour is credited with more "
                        "production than the array can physically deliver, and "
                        "nothing in the record suggests a whole hour's output "
                        "ever landed in one quarter-hour. "
                        "The headline maximum stays the measured one, but no "
                        "case verdict is asserted from it alone -- this is the "
                        "conservative basis every verdict is also computed on."),
                "max_upper_bound_kw": _r(env_max),
                "calculated_load_a": a_calc_upper,
                "headroom_vs_service_a": avail_upper["service"],
                "headroom_vs_meter_socket_a": avail_upper.get("meter_socket"),
                "binding": _r(min(avail_upper.values())),
            },
        },
        "added_load_code_values": {
            "second_evse_a": _r(evse2_a, 2),
            "second_evse_basis": (
                f"NEC 625.42 continuous load: {EXISTING_EVSE_OUTPUT_A:.0f} A "
                f"output x 1.25 on a "
                f"{standard_circuit_for(EXISTING_EVSE_OUTPUT_A):.0f} A 2-pole "
                f"circuit"),
            "battery_charging_a": _r(batt_a, 2),
            "battery_charging_basis": (
                f"{BATTERY_INVERTER_KW} kW grid charging = "
                f"{amps(BATTERY_INVERTER_KW):.2f} A x 1.25 continuous"),
            "heat_pump_a": None,
            "heat_pump_basis": (
                "NOT DETERMINED -- no unit has been selected anywhere in this "
                "project, and a nameplate cannot be invented. The heat-pump "
                "term is solved for instead: each case reports the largest NEC "
                "440.6 MCA that fits."),
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
    md = result["maximum_demand"]
    nec = result["nec_220_87"]
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  peak {md['peak_kw']} kW ({md['peak_a']} A) at "
          f"{md['peak_timestamp_local']}, point-determined="
          f"{md['peak_coincident']['point_determined']}")
    pan = result["panel"]
    socket = (f"{nec['headroom_a']['vs_meter_socket']} A vs the "
              f"{pan['meter_socket_continuous_a']:.0f} A meter socket"
              if pan["meter_socket_continuous_a"] is not None else
              "no meter-socket rating recorded, so the main is the only "
              "constraint")
    print(f"  220.87 calculated load {nec['calculated_load_a']} A -> headroom "
          f"{nec['headroom_a']['vs_service_rating']} A vs the "
          f"{pan['service_rating_a']:.0f} A main, {socket}")
    for c in result["cases"]:
        rem = c["remaining_headroom_a"]
        print(f"  {c['case']}: {rem['measured_basis']['binding']} A measured / "
              f"{rem['conservative_basis']['binding']} A conservative -- "
              f"{c['remaining_is']}  ({c['ampacity_verdict']} on ampacity, "
              f"{'fits' if c['spaces']['fits_without_panel_work'] else 'NO ROOM'}"
              f" in {c['spaces']['full_size_spaces_required']} spaces)")
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
