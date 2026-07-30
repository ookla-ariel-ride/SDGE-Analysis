#!/usr/bin/env python3
"""Session-based behavior-savings rebuild (replaces the crude "cap at 2.5 kW" shift).

What the old approach did wrong: it clipped every 15-min import above 2.5 kW and repriced
the clipped energy at a year-average super-off-peak rate. >60% of what it moved was house
load (HVAC/cooking) that is not obviously movable, and it never physically placed the
energy, so destination-period costs and monthly NEM netting were ignored.

This model:
  1. Detects EV charging sessions explicitly:
       - baseline = centered rolling 24 h (96-interval) 20th-percentile of import power
         (tracks the always-on house floor, immune to multi-hour charge blocks);
       - candidate intervals: import power >= baseline + 2.5 kW;
       - a session = a contiguous candidate run lasting >= 30 min whose PEAK excess
         reaches the charger-derived gate min(8 kW, 0.7 * charger.kw) — derived from
         charger.kw in private/household.yaml (for this house's 11.5 kW charger the
         gate is 8 kW; nothing else in the house sustains that). The derivation
         fails closed: it REFUSES (SystemExit) unless 0.7 * charger.kw clears the
         2.5 kW candidate threshold by at least 1 kW, i.e. charger.kw >= 5 —
         below that the peak gate degenerates into the appliance-noise test and
         sustained HVAC/oven blocks would be classified as EV charging;
       - EV energy per interval = clip(excess, 0, charger.kw) * 0.25 h, capped at the
         interval's actual import.
  2. Scenario ladder: physically REMOVES shifted kWh from source intervals and ADDS them
     to super-off-peak intervals starting at the next midnight (overnight 0-6 window,
     spilling into later SOP windows if full), honoring the charger.kw power cap net of
     any EV charging already present in the destination interval.
  3. Re-bills the modified year with the bill-validated monthly per-TOU-period NEM netting
     model (rates read off actual bills, EV-TOU-5 + CEA Clean Impact Plus, 6/1/2026).
  4. Re-runs the battery simulation ON TOP of scenario (a) so behavior and battery savings
     are not double-counted (13.5 kWh usable, 11.5 kW, charge from would-be exports outside
     on-peak then overnight SOP top-up, discharge on-peak, 90% round-trip efficiency).

Households with no EV (household.has_ev false, the intake applicability flag):
this module is the pipeline's data loader as well as its EV model, and a house
with no EV still has a load profile, so it does NOT refuse — it drops the EV
domain and keeps the rest:
  - charger.kw is not read at all (a leftover one contradicts the flag and aborts);
  - detect_sessions() returns no sessions and zero EV energy by construction, so
    every consumer that re-detects through this module (battery_dispatch_policies,
    extended_findings, carbon_fullyear, report_data, battery_plan_matrix) sees an
    EV-free year instead of dying at import;
  - the artifact publishes the detection block and the EV-only scenarios (a) and
    (b) as explicit not_applicable stubs naming the flag — never a $0 that could
    be mistaken for a computed finding;
  - scenarios (c) and (d) still compute, as pure house-load shifts; their
    destination power cap comes from the largest 15-min import this house has
    actually drawn (there is no charger to cap them, and shifted load must not
    create a draw the house has never demonstrated);
  - the battery block reports its marginal on the baseline, which with no EV
    shift to sit on top of is the only figure there is.

Absolute model dollars run high vs the audited bills ($3,282/yr actual over these 365 days);
use the DELTAS, which are driven by correctly-priced on-peak arbitrage.
"""
import json
import sys
import datetime as dt

import numpy as np
import pandas as pd

import household as hh

CSV = "usage.csv"  # SDG&E Green Button 15-min (skiprows=13)
WINDOW_END = dt.datetime(2026, 7, 24)   # analysis year = the 365 days before this

# ---- rates: identical to billing_model_nem.py (actual bills, 6/1/2026) ----
import rates as R                                          # canonical module
from rates import UDC, CEA, NBC, PCIA, BSC, energy, credit  # canonical bill-derived rates
retail = energy  # netted energy rate (NBC applied on gross imports in bill_monthly)

# ---- detection / shifting parameters ----
BASE_WIN = 96          # rolling-baseline window (24 h of 15-min intervals)
BASE_Q = 0.20          # baseline percentile
EXCESS_KW = 2.5        # candidate threshold above baseline
# EXCESS_KW deliberately does NOT scale with charger.kw: it screens house-load
# noise above the rolling baseline (HVAC compressors, ovens run 2-4 kW over it),
# which is a property of the house, not the charger — shrinking it for a small
# charger would readmit exactly that noise into candidate runs.
PEAK_MARGIN_KW = 1.0   # minimum clearance of the peak gate over EXCESS_KW
MIN_INTERVALS = 2      # >= 30 min sustained


def _flag(path):
    """Read an intake applicability flag. Present -> it must be a real YAML
    boolean; absent -> None, and the caller decides (failing closed)."""
    v = hh.get(path, required=False)
    if v is not None and v is not True and v is not False:
        raise SystemExit(
            "%s in private/household.yaml must be a YAML boolean (true/false), "
            "got %r — fix the intake before running" % (path, v))
    return v


# household.has_ev — the intake APPLICABILITY flag (DATA-SOURCES-CHEATSHEET.md,
# required_if: always). The FLAG is the authority on whether this household has
# an EV; the presence of data is never read as an inference (CLAUDE.md 0).
#   false  -> no charger.kw is read (a present one contradicts the flag), the EV
#             detector returns nothing, and the EV-domain outputs are published
#             as explicit not_applicable stubs;
#   true   -> charger.kw is required, as it always has been;
#   absent -> NOT read as "no EV". The flag postdates some intake files, so its
#             absence keeps the original hard requirement on charger.kw, which
#             is the fail-closed backstop: a household with no EV has no
#             charger.kw either, so such a run refuses (naming the flag) rather
#             than publishing EV findings for a house that has none.
HAS_EV = _flag("household.has_ev")
# Everything EV in this module keys off this one predicate:
EV_ANALYSIS = HAS_EV is not False
# charger power — per-house hardware, from private/household.yaml
# (analysis/household.py; fails closed — run the intake interview in
# DATA-SOURCES-CHEATSHEET.md). Used both as the destination cap when shifting
# EV energy and to derive the session-detection gate below.
_charger_kw = hh.get("charger.kw", required=False)
if not EV_ANALYSIS:
    if _charger_kw is not None:
        raise SystemExit(
            "behavior_rebuild: household.has_ev is false but charger.kw = %s is\n"
            "present in private/household.yaml — the flag and the data contradict\n"
            "each other. The flag is the authority, so the intake has to settle\n"
            "it: set household.has_ev: true, or remove the stale charger block."
            % (_charger_kw,))
    CHARGER_KW = CAP_KWH = PEAK_KW = None
elif _charger_kw is None:
    raise SystemExit(
        "behavior_rebuild: charger.kw is missing from private/household.yaml.\n"
        "It gates EV session detection and caps the destination power when\n"
        "shifted charging is re-placed, so it is required whenever the household\n"
        "has an EV — and a missing charger is NOT read as evidence of no EV.\n"
        "If this household HAS an EV: run the intake interview\n"
        "(DATA-SOURCES-CHEATSHEET.md, charger_kw).\n"
        "If it has NO EV: say so explicitly — set household.has_ev: false in\n"
        "private/household.yaml — and the EV scenarios publish as not_applicable\n"
        "instead of being guessed.")
else:
    CHARGER_KW = float(_charger_kw)
    CAP_KWH = CHARGER_KW * 0.25   # max EV kWh per 15-min interval
    # Session gate (EV signature): a session's peak excess over the house baseline
    # must reach ~70% of the charger's nameplate — a real charge block sustains near
    # nameplate, and the 30% margin absorbs voltage derating, ramp edges landing in
    # a 15-min average, and concurrent solar offset. Capped at 8 kW: no house load
    # here sustains 8 kW above baseline, so demanding more of a bigger charger adds
    # nothing. For this house's 11.5 kW charger: min(8.0, 0.7*11.5) = 8.0, the
    # original bill-validated signature, unchanged.
    # Fail-closed floor: the peak gate must sit a real margin ABOVE the candidate
    # threshold or it stops discriminating anything — once 0.7*charger.kw falls to
    # EXCESS_KW the "session peak" test collapses into the appliance-noise test and
    # every sustained HVAC/oven block reads as an EV session, silently corrupting
    # the behavior scenarios and every downstream consumer. Require at least
    # PEAK_MARGIN_KW of clearance (0.7*charger.kw >= EXCESS_KW + 1.0, i.e.
    # charger.kw >= 5.0); below that this power-signature detector cannot tell the
    # charger from the house, so it refuses rather than guesses.
    if 0.7 * CHARGER_KW < EXCESS_KW + PEAK_MARGIN_KW:
        raise SystemExit(
            "behavior_rebuild: cannot discriminate EV charging from house load with\n"
            "charger.kw = %g kW (private/household.yaml).\n"
            "The session-peak gate would be 0.7 * %g = %.2f kW above the rolling\n"
            "baseline, less than %g kW clear of the %g kW appliance-noise threshold.\n"
            "Household loads (HVAC compressors, ovens) run 2-4 kW above baseline, so\n"
            "at this level a power-signature detector classifies sustained house load\n"
            "as EV charging and silently corrupts the behavior-savings scenarios and\n"
            "every downstream consumer. This detector needs 0.7 * charger.kw >= %g kW\n"
            "(a charger of at least ~5 kW sustained output).\n"
            "EV-shift analysis is NOT DETERMINED for this hardware. To settle it,\n"
            "bring independent evidence of when the EV charged instead of inferring\n"
            "it from whole-house power: the vehicle's or EVSE vendor's charging\n"
            "records, or a sub-metered (CT-clamped) EVSE circuit."
            % (CHARGER_KW, CHARGER_KW, 0.7 * CHARGER_KW, PEAK_MARGIN_KW, EXCESS_KW,
               EXCESS_KW + PEAK_MARGIN_KW))
    PEAK_KW = min(8.0, 0.7 * CHARGER_KW)


def _not_applicable(what):
    """Explicit stub for an EV-domain output in a household the intake says has
    no EV. not_applicable, NOT not_determined: the intake DID determine the
    answer — the domain does not exist for this household. Same contract and
    wording as extended_findings.py's stubs, which govern the same flag."""
    return {
        "not_applicable": True,
        "reason": ("household.has_ev is false (intake applicability flag, "
                   "DATA-SOURCES-CHEATSHEET.md) — %s does not apply to this "
                   "household; set the flag true and complete the intake "
                   "(charger.kw) to compute it" % what),
    }

# ---- battery parameters (Powerwall 3 hardware spec, NOT household config) ----
BATT_KWH = 13.5        # usable
BATT_KW = 11.5
BATT_STEP = BATT_KW * 0.25
ETA = np.sqrt(0.90)    # one-way efficiency (90% round-trip)


def load():
    df = pd.read_csv(CSV, skiprows=13)
    df.columns = [c.strip() for c in df.columns]
    df["dt"] = pd.to_datetime(df["Date"] + " " + df["Start Time"],
                              format="%m/%d/%Y %I:%M %p")
    for c in ["Consumption", "Generation"]:
        df[c] = pd.to_numeric(df[c])
    end = WINDOW_END
    df = df[(df.dt >= end - dt.timedelta(days=365)) & (df.dt < end)]
    df = df.sort_values("dt").reset_index(drop=True)
    # Fail closed before ANY consumer computes: this loader feeds the dispatch,
    # package, findings and carbon pipelines, and a wholly missing day silently
    # shrinks every figure they publish (the October-gap failure shape). The
    # expected calendar comes from the fixed window, not from the data.
    R.validate_interval_coverage(
        zip(df.dt.dt.date, df.dt.dt.hour + df.dt.dt.minute / 60),
        (end - dt.timedelta(days=365)).date(), (end - dt.timedelta(days=1)).date())
    df["hour"] = df.dt.dt.hour + df.dt.dt.minute / 60
    # Weekend windows also apply on the eight tariff holidays; rates.off_peak_day
    # is the single source of that rule (confirmed against the bills, see
    # analysis/tou_audit.py). A bare weekday test silently drops it.
    df["wkend"] = df.dt.dt.date.map(R.off_peak_day)
    df["seas"] = np.where(df.dt.dt.month.isin(sorted(R.SUMMER_MONTHS)), "S", "W")
    df["ym"] = df.dt.dt.to_period("M")

    # TOU assignment comes from the canonical module, not a local copy of the rule.
    df["p"] = [R.period_at(t) for t in df.dt]
    return df


from rates import bill_nem_monthly as _bnm

def bill_monthly(frame, imp="imp", exp="exp"):
    """Monthly per-TOU-period NEM netting -> {month: $} (canonical engine)."""
    return _bnm(frame, imp, exp)


def bill(frame, imp="imp", exp="exp"):
    return sum(bill_monthly(frame, imp, exp).values())


# ------------------------------------------------------------------ detection
def detect_sessions(d):
    """(ev kWh per interval, [(start, end, kWh)]) — EV charge blocks in `d`.

    With household.has_ev false there is nothing to detect: the detector is a
    power-signature test that needs charger.kw to set its gate, and inventing a
    gate for a house the intake says has no EV would classify house load as
    charging. Every consumer re-detects through this function, so returning an
    EV-free year here is what keeps the whole pipeline runnable for such a
    household."""
    if not EV_ANALYSIS:
        return np.zeros(len(d)), []
    kW = d.Consumption.values * 4.0
    base = pd.Series(kW).rolling(BASE_WIN, center=True, min_periods=24) \
                        .quantile(BASE_Q).values
    exc = kW - base
    cand = exc >= EXCESS_KW
    ev = np.zeros(len(d))
    sessions = []
    i, n = 0, len(d)
    while i < n:
        if not cand[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and cand[j + 1]:
            j += 1
        if (j - i + 1) >= MIN_INTERVALS and exc[i:j + 1].max() >= PEAK_KW:
            e = np.clip(exc[i:j + 1], 0, CHARGER_KW) * 0.25
            e = np.minimum(e, d.Consumption.values[i:j + 1])
            ev[i:j + 1] = e
            sessions.append((i, j, e.sum()))
        i = j + 1
    return ev, sessions


# ------------------------------------------------------------------ shifting
def build_sop_index(d):
    """Chronological array of SOP interval indices + their timestamps."""
    idx = np.where(d.p.values == "sop")[0]
    return idx, d.dt.values[idx]


def place_energy(add, amount, start_ts, sop_idx, sop_ts, headroom_base, cap):
    """Pour `amount` kWh into SOP intervals at/after start_ts, respecting a
    destination cap of `cap` kWh per 15-min interval net of EV charging already
    present. `cap` is the charger's power for shifted EV energy (CAP_KWH); for
    house load in a household with no charger it is derived in main(). Mutates
    `add`. Returns unplaced remainder (0 unless we run off the end of the data;
    then we retry from the latest available overnight window)."""
    k = np.searchsorted(sop_ts, np.datetime64(start_ts))
    for q in range(k, len(sop_idx)):
        if amount <= 1e-9:
            return 0.0
        i = sop_idx[q]
        room = cap - headroom_base[i] - add[i]
        if room > 1e-9:
            put = min(room, amount)
            add[i] += put
            amount -= put
    if amount > 1e-9:  # sessions near the end of the data window: fill backward
        for q in range(len(sop_idx) - 1, -1, -1):
            if amount <= 1e-9:
                break
            i = sop_idx[q]
            room = cap - headroom_base[i] - add[i]
            if room > 1e-9:
                put = min(room, amount)
                add[i] += put
                amount -= put
    return amount


def _conserve(imp_in, out, what):
    """CLAUDE.md 1b: shifted load is MOVED, never lost. place_energy hands back
    anything it could not place (every destination interval at the cap for the
    rest of the year); that energy has already been subtracted from the source
    intervals, so an unplaced remainder would silently delete load and make the
    shifted year look cheaper than the physics allows."""
    lost = float(imp_in.sum() - out.sum())
    if abs(lost) > 1e-6:
        raise SystemExit(
            "behavior_rebuild: %s lost %.3f kWh — the destination cap left no "
            "room for energy already taken out of the source intervals. The "
            "scenario is not a shift, so it is not published; check the "
            "destination cap (charger.kw, or the observed-import cap used when "
            "household.has_ev is false)." % (what, lost))
    return out


def shift_ev(d, ev, sessions, session_mask, sop_idx, sop_ts):
    """Move on-peak + off-peak EV energy of selected sessions to SOP starting
    at the next midnight. Returns (new_imp, kwh_moved)."""
    imp = d.Consumption.values.astype(float).copy()
    add = np.zeros(len(d))
    p = d.p.values
    moved = 0.0
    for sel, (a, b, _tot) in zip(session_mask, sessions):
        if not sel:
            continue
        sl = slice(a, b + 1)
        movable = np.where(np.isin(p[sl], ["on", "off"]), ev[sl], 0.0)
        amt = movable.sum()
        if amt <= 0:
            continue
        imp[sl] -= movable
        start = (pd.Timestamp(d.dt.values[a]).normalize() + pd.Timedelta(days=1))
        left = place_energy(add, amt, start, sop_idx, sop_ts, ev, CAP_KWH)
        moved += amt - left
    return _conserve(d.Consumption.values, imp + add, "the EV shift"), moved


def shift_house(d, imp_in, ev, frac, sop_idx, sop_ts, cap):
    """Move `frac` of remaining (non-EV) on-peak import, per day, to the next
    overnight SOP window (spread by the same fill rule, capped at `cap` kWh per
    destination interval)."""
    imp = imp_in.copy()
    add = np.zeros(len(d))
    p = d.p.values
    onpk = (p == "on")
    take = np.where(onpk, np.clip(imp - ev, 0, None) * frac, 0.0)
    # note: imp_in already has EV session energy removed in scenarios c/d, and
    # `ev` is zeroed at moved intervals before calling (see below), so `take`
    # never double-taxes EV energy.
    moved = 0.0
    days = pd.Series(d.dt.dt.normalize())
    for day, grp in pd.DataFrame({"day": days, "i": np.arange(len(d))}).groupby("day"):
        ii = grp.i.values
        amt = take[ii].sum()
        if amt <= 1e-9:
            continue
        imp[ii] -= take[ii]
        left = place_energy(add, amt, day + pd.Timedelta(days=1), sop_idx, sop_ts,
                            ev, cap)
        moved += amt - left
    return _conserve(imp_in, imp + add, "the house-load shift"), moved


# ------------------------------------------------------------------ battery
def battery_sim(d, imp_in, exp_in):
    """13.5 kWh / 11.5 kW, 90% RTE. Charge from would-be exports outside on-peak,
    top up from grid during the overnight (0-6) SOP window, discharge on-peak."""
    imp = imp_in.copy(); exp = exp_in.copy()
    p = d.p.values; hour = d.hour.values
    soc = 0.0
    for i in range(len(d)):
        step = BATT_STEP
        if p[i] == "on":
            out = min(imp[i], step, soc * ETA)
            imp[i] -= out
            soc -= out / ETA
        else:
            if exp[i] > 0 and soc < BATT_KWH:
                ch = min(exp[i], step, (BATT_KWH - soc) / ETA)
                exp[i] -= ch
                soc += ch * ETA
                step -= ch
            if p[i] == "sop" and hour[i] < 6 and soc < BATT_KWH and step > 0:
                ch = min(step, (BATT_KWH - soc) / ETA)
                imp[i] += ch
                soc += ch * ETA
    return imp, exp


# ------------------------------------------------------------------ main
def main():
    d = load()
    d["imp"] = d.Consumption.astype(float)
    d["exp"] = d.Generation.astype(float)
    base_monthly = bill_monthly(d)
    base_bill = sum(base_monthly.values())

    ev, sessions = detect_sessions(d)
    if EV_ANALYSIS and not sessions:
        # Loud but non-fatal: an export from a genuinely EV-free house is a
        # legitimate zero, but a mis-set charger.kw must never zero out the EV
        # scenarios silently.
        bang = "!" * 74
        print("\n".join([
            "", bang,
            "WARNING: ZERO EV charging sessions detected across the whole year.",
            bang,
            "Detection is tuned via charger.kw in private/household.yaml:",
            "  charger.kw = %g kW  ->  session peak gate %g kW above the house"
            % (CHARGER_KW, PEAK_KW),
            "  baseline (candidate threshold %g kW, >= %d contiguous 15-min"
            % (EXCESS_KW, MIN_INTERVALS),
            "  intervals).",
            "If this house HAS an EV, check before trusting these results:",
            "  - charger.kw matches the EVSE's real sustained output (circuit or",
            "    vehicle onboard-charger limited, not the wall unit's nameplate);",
            "  - the Green Button export actually covers charging days;",
            "  - a charger near the ~5 kW admission floor clears the %g kW peak"
            % (PEAK_KW,),
            "    gate only barely; 15-min averaging can smear its peaks below it.",
            "If the house genuinely has no EV, scenarios a/b correctly show $0.",
            bang, "",
        ]), file=sys.stderr)
    d["ev"] = ev
    p = d.p.values
    ev_tot = ev.sum()
    ev_on = ev[p == "on"].sum()
    ev_off = ev[p == "off"].sum()
    ev_sop = ev[p == "sop"].sum()
    sop_idx, sop_ts = build_sop_index(d)

    def run(imp_new, label):
        f = d.copy()
        f["imp"] = imp_new
        monthly = bill_monthly(f)
        tot = sum(monthly.values())
        return dict(label=label,
                    bill=round(tot, 2),
                    saved=round(base_bill - tot, 2),
                    month_min=round(min(monthly.values()), 2),
                    month_max=round(max(monthly.values()), 2))

    results = {"window": {"start": str(d.dt.min()), "end": str(d.dt.max()),
                          "days": int(d.dt.dt.date.nunique())},
               "baseline": {"model_bill": round(base_bill, 2),
                            "actual_billed": 3282,
                            "month_min": round(min(base_monthly.values()), 2),
                            "month_max": round(max(base_monthly.values()), 2),
                            "imports_kwh": round(float(d.imp.sum()), 1),
                            "exports_kwh": round(float(d.exp.sum()), 1),
                            "onpeak_import_kwh": round(float(d.imp[p == "on"].sum()), 1)},
               "detection": ({"rule": ("power >= rolling-24h-20th-pct baseline + %g kW, "
                                       ">=30 min contiguous, session peak excess >= %g kW; "
                                       "EV kWh = clip(excess,0,%gkW)*0.25h, <= interval import"
                                       % (EXCESS_KW, PEAK_KW, CHARGER_KW)),
                              "sessions": len(sessions),
                              "ev_kwh_total": round(float(ev_tot), 1),
                              "ev_kwh_expected": 13100,
                              "ev_kwh_onpeak": round(float(ev_on), 1),
                              "ev_kwh_offpeak": round(float(ev_off), 1),
                              "ev_kwh_sop_already": round(float(ev_sop), 1),
                              "avg_session_kwh": round(float(ev_tot) / max(len(sessions), 1), 1)}
                             if EV_ANALYSIS else
                             _not_applicable("EV charging detection")),
               "scenarios": {}}

    if EV_ANALYSIS:
        all_mask = [True] * len(sessions)
        rng = np.random.default_rng(42)
        mask80 = [bool(rng.random() < 0.80) for _ in sessions]

        # (a) EV-only, 100 %
        impA, movedA = shift_ev(d, ev, sessions, all_mask, sop_idx, sop_ts)
        a = run(impA, "a: EV-only, 100% compliance"); a["kwh_moved"] = round(movedA, 1)
        results["scenarios"]["a"] = a

        # (b) EV-only, 80 %
        impB, movedB = shift_ev(d, ev, sessions, mask80, sop_idx, sop_ts)
        b = run(impB, "b: EV-only, 80% compliance (seeded)")
        b["kwh_moved"] = round(movedB, 1)
        b["sessions_moved"] = int(sum(mask80))
        results["scenarios"]["b"] = b

        # remaining-EV map after scenario (a) moves: EV left only in SOP intervals
        ev_left = np.where(np.isin(p, ["on", "off"]), 0.0, ev)
        house_cap = CAP_KWH          # destination cap: the charger's power
        base_moved = movedA          # (c)/(d) sit on top of scenario (a)
        c_label, d_label = ("c: EV + 25% flexible house load",
                            "d: stretch - EV + 50% house load")
    else:
        # No EV: the EV-only rungs of the ladder do not exist for this household.
        # Publish them as stubs rather than as a $0 that reads like a finding.
        results["scenarios"]["a"] = _not_applicable("the EV-only shift scenario")
        results["scenarios"]["b"] = _not_applicable("the EV-only shift scenario")
        impA = d.imp.values.astype(float).copy()   # nothing shifted yet
        ev_left = ev                               # zeros
        base_moved = 0.0
        # There is no charger, so no hardware cap on where shifted HOUSE load may
        # land. The data-derived cap is the largest 15-min import this house has
        # actually drawn in the analysis year: shifted load may fill the overnight
        # window, but it may not create an interval draw the house has never
        # demonstrated. (Measured off this household's own meter, not assumed.)
        house_cap = float(np.max(d.imp.values))
        c_label, d_label = ("c: 25% flexible house load",
                            "d: stretch - 50% house load")

    # (c) 25 % of remaining on-peak non-EV load, on top of scenario (a) where
    #     there is one (no EV -> impA is the untouched baseline)
    impC, movedC_house = shift_house(d, impA, ev_left, 0.25, sop_idx, sop_ts, house_cap)
    c = run(impC, c_label)
    c["kwh_moved"] = round(base_moved + movedC_house, 1)
    c["house_kwh_moved"] = round(movedC_house, 1)
    results["scenarios"]["c"] = c

    # (d) stretch: the same at 50 %
    impD, movedD_house = shift_house(d, impA, ev_left, 0.50, sop_idx, sop_ts, house_cap)
    sd = run(impD, d_label)
    sd["kwh_moved"] = round(base_moved + movedD_house, 1)
    sd["house_kwh_moved"] = round(movedD_house, 1)
    results["scenarios"]["d"] = sd

    # battery marginal: on baseline (for reference) and after scenario (a)
    bi, be = battery_sim(d, d.imp.values.copy(), d.exp.values.copy())
    f = d.copy(); f["imp"], f["exp"] = bi, be
    batt_alone = base_bill - bill(f)

    SPEC = ("13.5 kWh usable, 11.5 kW, 90% RTE, export-charge + overnight SOP "
            "top-up, on-peak discharge")
    if EV_ANALYSIS:
        bi2, be2 = battery_sim(d, impA, d.exp.values.copy())
        f2 = d.copy(); f2["imp"], f2["exp"] = bi2, be2
        batt_after_a = a["bill"] - bill(f2)

        results["battery"] = {
            "spec": SPEC,
            "marginal_on_baseline": round(batt_alone, 2),
            "marginal_after_scenario_a": round(batt_after_a, 2),
            "double_count_avoided": round(batt_alone - batt_after_a, 2)}

        results["note"] = ("Model absolute bills run high vs audited $3,282/yr; report DELTAS. "
                           "Old crude 2.5 kW-cap method claimed ~$1,330/yr; see scenarios for "
                           "honest session-based range.")
    else:
        # Scenario (a) does not exist here, so neither does a post-EV-shift
        # marginal: with nothing shifted out of the on-peak window there is no
        # behavior/battery overlap to net out, and the baseline marginal is the
        # whole answer. The post-shift key is a stub, not a repeat of the
        # baseline number, so no consumer can read one as the other.
        results["battery"] = {
            "spec": SPEC,
            "marginal_on_baseline": round(batt_alone, 2),
            "marginal_after_ev_shift": _not_applicable("the post-EV-shift battery marginal"),
            "note": ("no EV shift to sit on top of, so there is no "
                     "behavior/battery double-count to avoid")}

        results["note"] = ("household.has_ev is false: the EV rungs (detection, "
                           "scenarios a/b) are not applicable and scenarios c/d "
                           "are pure house-load shifts. Report DELTAS between "
                           "scenarios, not the absolute model bill.")

    with open("behavior_rebuild.json", "w") as fh:
        json.dump(results, fh, indent=1)

    print("model baseline: $%.0f/yr (actual billed $3,282; use deltas)" % base_bill)
    if EV_ANALYSIS:
        print("EV sessions: %d, %.0f kWh/yr (expected ~13,100) | on-peak %.0f, off-peak %.0f, already-SOP %.0f"
              % (len(sessions), ev_tot, ev_on, ev_off, ev_sop))
        shown = "abcd"
    else:
        print("household.has_ev is false: no EV detection, no EV shift — "
              "scenarios a/b published as not_applicable stubs; c/d are "
              "house-load-only, capped at %.2f kWh per destination interval "
              "(this house's largest observed 15-min import)" % house_cap)
        shown = "cd"
    print("%-38s %10s %10s %12s %12s" % ("scenario", "kWh moved", "$ saved/yr", "mo bill min", "mo bill max"))
    for k in shown:
        s = results["scenarios"][k]
        print("%-38s %10.0f %10.0f %12.0f %12.0f"
              % (s["label"], s["kwh_moved"], s["saved"], s["month_min"], s["month_max"]))
    if EV_ANALYSIS:
        print("battery marginal: $%.0f/yr on baseline, $%.0f/yr after scenario (a) [double-count avoided: $%.0f]"
              % (batt_alone, batt_after_a, batt_alone - batt_after_a))
    else:
        print("battery marginal: $%.0f/yr on baseline (no EV shift, so no "
              "double-count to avoid)" % batt_alone)


if __name__ == "__main__":
    main()
