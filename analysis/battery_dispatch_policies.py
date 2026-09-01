#!/usr/bin/env python3
"""Battery dispatch policies — the report's battery economics (integrated engine).

Principle: a stored kWh costs 11.7c when it displaces a MIDDAY super-off-peak export
and 14.0c from a super-off-peak grid top-up; every import priced above that is worth
serving. Non-super-off-peak imports price at 51-87c, so the price-aware policy
discharges against ALL of them; super-off-peak imports (12.5c) are never served.

BOTH FIGURES ARE DERIVED, NOT ASSERTED (issue #189). stored_energy_cost() below
prices the price-aware run's OWN charging intervals through the same settlement
rule rates.bill_nem_monthly() applies, and writes the result to the artifact's
stored_kwh_cost key -- which is the authority, not this paragraph.

WHY THE MIDDAY FIGURE IS 11.7c AND NOT THE 8.4c THIS DOCSTRING USED TO CLAIM. A
forgone export settles at the NETTING end of the export bracket, not the surplus
end. rates.bill_nem_monthly() nets per (month, season, TOU period) bucket and pays
the export credit only on what a bucket has left AFTER netting: a bucket with net
>= 0 is billed at rates.energy(), and only a net-NEGATIVE bucket settles at
rates.credit(). Every one of this window's 13 super-off-peak buckets is net-IMPORT
-- the super-off-peak period lumps overnight EV charging in with midday solar --
so a super-off-peak export cancels an import of its own month and period, and what
storing it forgoes is energy() (10.5c), not credit() (7.6c). Divided by the 90%
round trip that is 11.7c, not 8.4c. Same distinction, one level up, as the NBC-on-
gross-imports rule in CLAUDE.md section 9; the same bracket analysis/report_tokens.py
publishes as EXPORT_VALUE_SURPLUS_BOUND / EXPORT_VALUE_NETTING_BOUND.

The POLICY CONCLUSION IS UNCHANGED: non-super-off-peak imports at 51-87c are far
above either figure, so the price-aware policy still discharges against all of
them. What changes is the margin of the argument. Headroom against a super-off-peak
import (12.5c) narrows from ~4.1c to ~0.8c -- storing midday surplus to serve a
super-off-peak import is now barely worth the round trip, which is a real change in
the argument even though the decision it supports is the same.

AND ONLY HALF THE SURPLUS CHARGING IS MIDDAY. Averaged over the whole price-aware
run, a kWh stored from solar surplus costs 34.3c delivered, not 11.7c: 335 of the
705 kWh charged from surplus displace super-off-peak exports at 11.7c, but 352 kWh
displace OFF-PEAK exports (53.5c delivered) and 17 kWh displace ON-PEAK exports
(79.5c). Solar surplus is not a super-off-peak phenomenon on this tariff -- the
14-16h and 6-10h shoulders are off-peak and the summer sun is still up well into
the 16-21h on-peak window. Quoting the midday cell as the price of stored solar is
the same one-end-of-the-bracket error as quoting credit() for the midday cell.

NOR IS ONE BUCKET ALWAYS ON ONE END OF THE BRACKET. Storing a block of surplus
raises its bucket's net, and where that move crosses zero part of the block
settles at credit() and part at energy() -- so the bucket's final sign cannot
price the whole block. Each block is priced piecewise across that boundary
(_span_value below), which is what makes the off-peak cell 53.5c rather than the
53.9c a single end-rate gives: two off-peak buckets are net-positive by less than
the surplus taken out of them. It changes no super-off-peak figure, because no
super-off-peak bucket comes near zero, and it is not asserted -- every published
stream and period price is checked against an exact rates.bill_nem() re-bill of
its own counterfactual, and the residual is published beside it.

Three policies x two configurations (13.5 kWh Powerwall 3 / 27 kWh PW3+Expansion,
both 11.5 kW continuous discharge -- but DIFFERENT continuous charge rates: 5 kW
for the bare 13.5 kWh unit, 8 kW for the 27 kWh with-expansion unit -- Tesla's
own datasheet, see research/battery-research-notes.md -- 90% round-trip split
as sqrt-eta per direction):
  evening  discharge 16-21h only; overnight grid top-up to 60% capacity
  twowin   + 6-9am house load
  greedy   price-aware: any non-super-off-peak import; top-up toward full in any
           super-off-peak gap; solar surplus always charges first
  value    greedy with both decisions priced at the margin (issue #240): a kWh
           of surplus is stored only when its forgone export, divided by the
           round trip, is below the import it can serve, and a stored kWh
           serves only an import worth more than it cost. Not the published
           basis; see _run_batt_value() and TECHNICAL.md section 3.13.
EV exclusion (twowin and greedy, and ONLY on a household that has an EV):
intervals >= 2.5 kW outside on-peak are EV spillover; the (free) schedule fix
moves that load, so the battery never serves it outside on-peak. The exclusion
is gated on br.EV_ANALYSIS (household.has_ev) -- see the comment at the rule in
run_batt() and EV_EXCLUSION_NOTE_* below, which is what the artifact publishes.

INTEGRATED VALUATION: the battery modifies the physical import/export series and the
modified year is re-billed with the canonical engine (rates.bill_nem: monthly
per-period NEM netting, NBC on gross imports) — the same engine used for the
behavior model. The post-behavior block runs THIS HOUSEHOLD'S FREE FIX FIRST,
then the battery on the shifted load: one pipeline, one rate set, no cross-model
splicing.

WHICH free fix is not a constant. It is whichever behavior scenario the LOW
package is, and that depends on the intake flag household.has_ev:

  has_ev true   -> scenario a, the EV-only 100%-compliance charge reschedule
  has_ev false  -> scenario c, the pure 25% flexible house-load shift

free_fix_shift() below selects between them and applies it with behavior_
rebuild.py's OWN shift functions, so there is one implementation of each shift in
the repo. This used to call br.shift_ev() unconditionally. On a household with no
EV that is a NO-OP (the detector returns no sessions), so post_behavior was the
battery on the bare baseline while packages.LOW was scenario c -- MID and HIGH
then meant "free fix + battery" for one household and "battery only" for another,
which is exactly the composite-from-different-pipelines blend CLAUDE.md §9's
one-pipeline-per-package-figure rule forbids. The block publishes the scenario key
it applied (post_behavior.free_fix_scenario) so package_results.py can refuse a
mismatch against packages.LOW.free_fix_scenario rather than re-derive the branch.

Output: battery_dispatch_policies.json — savings per policy/config, kWh served,
cycles/day, summer hourly grid-import profiles, escalation ladder, post_behavior
package figures. This script fully regenerates the committed artifact.

run_batt() also takes an optional power_kw (default 11.5, matching every config
above and preserving this module's own output byte-for-byte) so it can be reused
as a shared dispatch primitive at other capacities/power caps — battery_sizing_
curve.py (issue #12) sweeps it across an energy and a power grid. Every call
asserts its own energy-conservation identity (final SOC must equal the initial
SOC plus charge throughput minus discharge, net of round-trip loss) and raises
SystemExit if it does not hold — a real invariant, not just a signature change.

power_kw is the DISCHARGE cap (what the unit delivers). run_batt() also takes an
optional charge_kw (issue #40), the CHARGE cap (what the unit draws, from solar
surplus or grid top-up), defaulting to reuse power_kw when not given so every
existing call site is byte-for-byte unchanged. Tesla's own official 2025
Powerwall 3 Datasheet gives these as genuinely different figures for a single
unit with no expansions — 11.5 kW continuous discharge vs. 5 kW continuous
charge (canonical URL:
https://energylibrary.tesla.com/docs/Public/EnergyStorage/Powerwall/3/Datasheet/en-us/Powerwall-3-Datasheet.pdf,
retrieved 2026-08-03 via a third-party mirror; see research/battery-research-
notes.md for the full citation) — so a caller that wants the asymmetric,
datasheet-accurate limit passes charge_kw=5.0 explicitly; nothing here assumes
that value by default.

The unconditional discharge-window clause reads p[i] == "on" (the TOU period
column ANY caller's frame already carries), not a hardcoded clock-hour test
(issue #14 adversarial review, first pass): an earlier version tested
16 <= h[i] < 21 directly, which happens to equal p[i] == "on" for every frame
built from rates.period_at() (rates.py itself defines "on" as exactly that
window, unconditionally, before its own weekday/weekend branching) but silently
stopped tracking on-peak the moment a caller supplied a frame whose p column
encoded a DIFFERENT on-peak window -- exactly what tou_structure_stress.py
(issue #14) does to stress-test alternate tariff structures. Reading p[i]
directly is a no-op for every existing caller (verified: this module's own
committed artifact, and every downstream artifact that reuses run_batt --
battery_sizing_curve.json, battery_plan_matrix.json, perfect_foresight_
dispatch.json's greedy comparison, package_results.json, extended_results.json
-- all regenerate byte-identically) and makes the function genuinely portable
to a caller's own TOU structure, which its own module docstring already claimed
before this fix made the claim true.
"""
import numpy as np, pandas as pd, json
import behavior_rebuild as br
import rates as R

ETA = np.sqrt(0.90); PWRQ = 11.5 / 4

# Real, cited Maximum Continuous Charge Power for a BARE single Powerwall 3
# unit, no expansions (issue #40) -- 5 kW AC, vs. the 11.5 kW continuous
# DISCHARGE rating (PWRQ above), which is the SAME for bare and
# with-expansion configurations (discharge is not re-rated by adding
# expansion capacity). Tesla's own official 2025 Powerwall 3 Datasheet,
# canonical URL
# https://energylibrary.tesla.com/docs/Public/EnergyStorage/Powerwall/3/Datasheet/en-us/Powerwall-3-Datasheet.pdf
# (retrieved 2026-08-03 via a third-party mirror; see research/battery-
# research-notes.md for the full citation). Applies ONLY to the base 13.5
# kWh unit with NO expansion packs -- see CHARGE_KW_WITH_EXPANSION
# immediately below for the 27 kWh (PW3 + 1 Expansion) configuration, which
# the SAME datasheet gives a materially higher, separately cited charge
# rating for (corrected: an earlier version of this constant incorrectly
# claimed the expansion pack "shares the base unit's inverter" and applied
# this bare-unit figure to the expansion configuration too, contradicting
# the very datasheet split research/battery-research-notes.md already
# recorded -- Codex adversarial review caught this). Used below as the
# production default for every run_batt() call that models this
# household's real bare-unit Powerwall 3 hardware; run_batt() itself keeps
# charge_kw=None as its own general-purpose default so it stays usable as a
# reusable primitive at other, uncited capacities/powers (battery_sizing_
# curve.py's sweep).
CHARGE_KW = 5.0

# Real, cited Maximum Continuous Charge Power for a Powerwall 3 WITH UP TO 3
# EXPANSION UNITS (issue #40 correction) -- 8 kW AC, 33.3 A, from the SAME
# Tesla datasheet cited above (see research/battery-research-notes.md: "PW3
# with up to 3 Expansion units, 33.3 A / 8 kW"). This household's 27 kWh
# "PW3 + 1 Expansion" configuration has one expansion pack, which is within
# this "up to 3" bracket, so it takes this figure, NOT the bare-unit
# CHARGE_KW above. The discharge rating (11.5 kW) is unchanged by adding
# expansion capacity, so only the charge constant differs by configuration.
CHARGE_KW_WITH_EXPANSION = 8.0

# Which discharge the "value" policy's charge test compares a stored kWh against
# (issue #240). The policy cannot see the future, so it does not know WHICH
# import a stored kWh will serve; what it can know is the range of import
# values over the intervals it is allowed to discharge into. "floor" is the
# smallest import value over the season's discharge-window intervals, whether
# or not an import occurs in them: a kWh is stored only when that value covers
# its delivered cost, so nothing stored ever waits for a better import. "best"
# is the largest such value, and relies on the discharge rule to hold the kWh
# until an import worth that much arrives. Both read only the tariff and the
# bucket signs known from the pre-battery frame. On this house's measured year
# "floor" wins by a wide margin ($2,466/yr against greedy's $2,328 for one
# Powerwall 3; "best" $2,335): a shoulder kWh held for the evening occupies
# room that the cheaper midday surplus, arriving a few hours later, then
# cannot use, and a shoulder kWh still in the pack at 21:00 displaces a
# 14c grid top-up instead of an 87c on-peak import. See TECHNICAL.md
# section 3.13 and test_battery_dispatch_policies.py's measured-year case.
VALUE_CHARGE_REF = "floor"


def run_batt(d, imp0, gen0, cap, policy, power_kw=11.5, charge_kw=None, soc0=None,
             charge_ref=None, trace=None):
    """power_kw is the discharge cap; charge_kw is the charge cap (solar-surplus
    and grid-top-up charging both use it), defaulting to power_kw so a caller
    that does not pass charge_kw gets the prior symmetric behavior byte-for-
    byte. See the module docstring for the datasheet citation behind the real
    asymmetric figures (11.5 kW discharge / 5 kW charge, single unit).

    policy "value" (issue #240) is the price-aware policy with its charge and
    discharge decisions priced at the margin; see _run_batt_value() for the
    rule, and VALUE_CHARGE_REF for what `charge_ref` selects (None reads that
    constant at call time). `trace`, a list, receives one row per decision that
    policy takes; both are ignored by the three original policies, whose code
    path below is unchanged."""
    if policy == "value":
        return _run_batt_value(d, imp0, gen0, cap, power_kw, charge_kw, soc0,
                               charge_ref, trace)
    imp = imp0.copy(); exp = gen0.copy()
    pwrq_dis = power_kw / 4
    pwrq_chg = (power_kw if charge_kw is None else charge_kw) / 4
    soc0 = cap / 2 if soc0 is None else soc0
    soc = soc0; served = 0.0; thru = 0.0
    p = d.p.values; h = d.hour.values; kw = imp0 * 4
    # THE EV-SPILLOVER EXCLUSION IS CONDITIONAL ON THE HOUSEHOLD HAVING AN EV
    # (issue #147). The >= 2.5 kW test below encodes one claim and one only: an
    # import that big outside on-peak is EV charging spilling out of its window,
    # the free schedule fix moves it, and the battery must not be credited with
    # serving load that is about to move. On a household with household.has_ev
    # false there is no such load, and the same test then withholds ORDINARY
    # HOUSE LOAD -- a heat pump, an oven, a well pump -- from a battery that
    # should serve it, understating the battery marginal and every MID/HIGH
    # package, sizing-curve cell and plan-matrix cell built on it.
    #
    # KEYED OFF THE INTAKE FLAG, NOT OFF THE DETECTOR. br.EV_ANALYSIS is
    # household.has_ev, the same predicate free_fix_shift() below uses. A
    # detector that found no sessions is NOT the same fact as a household with
    # no EV -- br.detect_sessions() returns nothing on a household whose EV
    # charges away from home for a month, and reading that silence as "no EV"
    # would switch the exclusion off on a household that needs it. Only the
    # declared flag decides.
    ev_spillover_excluded = br.EV_ANALYSIS
    for i in range(len(d)):
        # True when this interval's import is servable at all: with an EV, only
        # sub-2.5 kW imports outside on-peak are (the rest is spillover); with
        # no EV, every one of them is.
        serve_ok = (kw[i] < 2.5) if ev_spillover_excluded else True
        disch_win = (p[i] == "on") or \
                    (policy == "twowin" and 6 <= h[i] < 9 and serve_ok) or \
                    (policy == "greedy" and p[i] != "sop" and serve_ok)
        if exp[i] > 0 and not (disch_win and imp[i] > 0):
            # charge from surplus — unless this interval also has import inside a
            # discharge window (6.3% of intervals carry both flows; serving the
            # import wins, but by LESS than it looks: an interval that carries
            # both flows is by definition outside super-off-peak, so the surplus
            # it would store forgoes an OFF- or ON-peak export worth 46-85c
            # against the 51-87c import it would instead serve. The margin is a
            # few cents plus the round-trip loss avoided, not the ~43c a
            # super-off-peak surplus figure would suggest — see the module
            # docstring and stored_energy_cost() below.)
            c = min(exp[i], (cap - soc) / ETA, pwrq_chg)
            if c > 0: soc += c * ETA; exp[i] -= c; thru += c * ETA
            continue
        if p[i] == "sop":
            grid_ok = (policy == "greedy") or (h[i] < 6)
            lim = cap if policy == "greedy" else 0.6 * cap
            take = min(max((lim - soc) / ETA, 0), pwrq_chg) if grid_ok else 0
            if take > 0: soc += take * ETA; imp[i] += take; thru += take * ETA
            continue
        if disch_win:
            dd = min(imp[i], soc * ETA, pwrq_dis)
            if dd > 0: soc -= dd / ETA; imp[i] -= dd; served += dd
    # energy-conservation identity (CLAUDE.md 1b): every joule leaving the pack
    # equals a joule that entered it, net of round-trip loss — soc0 + thru
    # (AC-in, already at pack-side value) - served/ETA (AC-out, back to pack-side)
    # must equal the final soc to the float epsilon.
    if abs(soc - (soc0 + thru - served / ETA)) > 1e-6:
        raise SystemExit("run_batt: energy-conservation identity failed — "
                          "soc drifted from the charge/discharge throughput")
    return imp, exp, served, thru


def bucket_net_sign(d, imp, gen):
    """Per interval: is its (month, season, TOU period) bucket net >= 0?

    This is the one test rates.bill_nem_monthly() applies when it decides
    whether a bucket bills at rates.energy() or credits at rates.credit(),
    evaluated on the frame given. The "value" policy evaluates it on the
    PRE-battery frame: the sign of a month's bucket is a fact about the house
    (does it import more than it exports in that period, that month), which a
    controller knows from its own history, and it is the sign every bucket of
    this house keeps unless the battery itself flips it. Where the battery
    does flip one, stored_energy_cost() publishes the count, and the priced
    figures it reports are on the post-dispatch frame, not this one.
    """
    f = pd.DataFrame({"ym": d.ym.astype(str).values, "seas": d.seas.values,
                      "p": d.p.values, "n": np.asarray(imp) - np.asarray(gen)})
    return (f.groupby(["ym", "seas", "p"]).n.transform("sum").values >= 0)


def _run_batt_value(d, imp0, gen0, cap, power_kw, charge_kw, soc0, charge_ref, trace):
    """The price-aware policy with both decisions priced at the margin (issue #240).

    The original "greedy" policy stores every kWh of solar surplus it has room
    for and discharges against every non-super-off-peak import. Half the
    surplus it stores on this house is not midday surplus but 6-10h and 14-16h
    off-peak surplus, whose forgone export is worth rates.energy(off) in a
    net-import bucket: 49-50c, or 54-55c per kWh delivered after the round
    trip. Served against an off-peak import worth allin(off) = 51-52c, that
    kWh loses 2-3c. Nothing in "greedy" can see that, because it prices
    neither side.

    This policy prices both sides with the same rule bill_nem_monthly() bills
    by. For every interval, from the bucket sign bucket_net_sign() returns:

      export value  E_i = energy(seas, p) if the bucket is net >= 0 else credit(seas, p)
      import value  I_i = E_i + NBC   (a displaced import also stops paying NBC;
                                       a grid top-up starts paying it)

    A kWh stored from surplus in interval i costs E_i / RTE per kWh delivered;
    a kWh of grid top-up costs I_i / RTE. Every kWh in the pack carries that
    cost with it, as a lot.

    Charge rule: surplus in interval i is stored only if E_i / RTE is below the
    reference discharge value for its season (see VALUE_CHARGE_REF): the
    smallest I_j over the season's discharge-window intervals, whether or not
    an import occurs in them ("floor", the default), or the largest ("best").
    Grid top-up is tested the same way.
    On this tariff "floor" means: midday super-off-peak surplus (11.7c
    delivered) is stored, 6-10h and 14-16h off-peak surplus (51-55c) and
    on-peak surplus are exported. The gain is mostly not the 2-3c margin on
    the shoulder kWh itself but the room it frees: greedy fills the pack from
    the morning shoulder first, so the cheaper midday surplus that follows has
    nowhere to go and is exported at 10.4c while the 49c shoulder export was
    forgone in its place.

    Discharge rule: an import of value I_i may be served only from lots whose
    delivered cost is below I_i, most expensive eligible lot first, so cheap
    lots stay available for cheap imports. No served import is therefore ever
    cheaper than the energy serving it, by construction; the optional `trace`
    records every charge, skip and discharge with the two figures compared, so
    a test can check that on the actual run rather than on this paragraph.

    Everything else is "greedy": the discharge window is every non-super-off-
    peak import (with the EV-spillover gate), an interval carrying both flows
    inside that window serves its import rather than storing its surplus, and
    super-off-peak gaps top the pack up toward full. Super-off-peak imports
    are still never served: a midday lot at 11.7c delivered would clear a
    12.5c import by 0.8c, and the grid top-up that then refilled the pack for
    the evening would cost 14.0c, a losing round trip the window rule spares.
    """
    imp = imp0.copy(); exp = gen0.copy()
    pwrq_dis = power_kw / 4
    pwrq_chg = (power_kw if charge_kw is None else charge_kw) / 4
    soc0 = cap / 2 if soc0 is None else soc0
    served = 0.0; thru = 0.0
    p = d.p.values; kw = imp0 * 4
    seas = d.seas.values
    rte = float(ETA * ETA)
    sign = bucket_net_sign(d, imp0, gen0)
    e_val = np.array([(R.energy(s, q) if ok else R.credit(s, q))
                      for s, q, ok in zip(seas, p, sign)])
    i_val = e_val + R.NBC
    ev_spillover_excluded = br.EV_ANALYSIS
    serve_ok = (kw < 2.5) if ev_spillover_excluded else np.ones(len(d), bool)
    # same window as "greedy": on-peak unconditionally, every other non-super-
    # off-peak import subject to the EV-spillover gate
    disch_win = (p == "on") | ((p != "sop") & serve_ok)
    charge_ref = VALUE_CHARGE_REF if charge_ref is None else charge_ref
    if charge_ref not in ("best", "floor"):
        raise SystemExit(f"run_batt: charge_ref {charge_ref!r} is not 'best' or 'floor'")
    ref = {}
    for s in np.unique(seas):
        vals = i_val[(seas == s) & disch_win]
        ref[s] = (float(vals.max()) if charge_ref == "best" else float(vals.min())) \
            if len(vals) else 0.0
    # The pack: lots of [pack-side kWh, delivered cost per kWh]. The starting
    # charge is priced at zero: it is not this run's decision, and pricing it
    # would let it refuse an import it could serve. It is the only lot that can
    # carry a cost no interval produced.
    lots = [[soc0, 0.0]] if soc0 > 0 else []
    soc = soc0

    def _log(i, kind, kwh, cost, value):
        if trace is not None:
            trace.append({"i": int(i), "kind": kind, "kwh": float(kwh),
                          "cost": float(cost), "value": float(value)})

    def _add_lot(kwh, cost):
        if lots and abs(lots[-1][1] - cost) < 1e-12:
            lots[-1][0] += kwh
        else:
            lots.append([kwh, cost])

    for i in range(len(d)):
        if exp[i] > 0 and not (disch_win[i] and imp[i] > 0):
            cost = e_val[i] / rte
            if cost < ref[seas[i]]:
                c = min(exp[i], (cap - soc) / ETA, pwrq_chg)
                if c > 0:
                    soc += c * ETA; exp[i] -= c; thru += c * ETA
                    _add_lot(c * ETA, cost)
                    _log(i, "charge_surplus", c, cost, ref[seas[i]])
            else:
                # kwh here is the surplus declined, whether or not the pack
                # had room for it, so a skip row bounds what greedy might
                # have stored rather than measuring it
                _log(i, "skip_surplus", exp[i], cost, ref[seas[i]])
            continue
        if p[i] == "sop":
            cost = i_val[i] / rte
            take = min(max((cap - soc) / ETA, 0), pwrq_chg) if cost < ref[seas[i]] else 0
            if take > 0:
                soc += take * ETA; imp[i] += take; thru += take * ETA
                _add_lot(take * ETA, cost)
                _log(i, "charge_grid", take, cost, ref[seas[i]])
            continue
        if disch_win[i]:
            need = min(imp[i], pwrq_dis)
            # most expensive eligible lot first; lots are appended in time
            # order, so sort by cost for the pick and keep the list otherwise
            for lot in sorted(lots, key=lambda l: -l[1]):
                if need <= 0:
                    break
                if lot[1] >= i_val[i]:
                    continue
                dd = min(need, lot[0] * ETA)
                if dd > 0:
                    lot[0] -= dd / ETA; soc -= dd / ETA; imp[i] -= dd
                    served += dd; need -= dd
                    _log(i, "discharge", dd, lot[1], i_val[i])
            lots[:] = [l for l in lots if l[0] > 1e-12]
    if abs(soc - (soc0 + thru - served / ETA)) > 1e-6:
        raise SystemExit("run_batt: energy-conservation identity failed — "
                          "soc drifted from the charge/discharge throughput")
    if abs(soc - sum(l[0] for l in lots)) > 1e-6:
        raise SystemExit("run_batt: the lot ledger does not sum to the pack's SOC")
    return imp, exp, served, thru


def billed(d, imp, exp):
    f = d.copy(); f["I"] = imp; f["E"] = exp
    return R.bill_nem(f, "I", "E")

_PERIODS = ("on", "off", "sop")
_TREATMENTS = ("netting_energy", "surplus_credit")


def _span_value(q, net_without, seas, per):
    """What adding q kWh to a bucket's NET does to its bill, priced across zero.

    rates.bill_nem_monthly() bills a (month, season, TOU period) bucket at
    rates.energy() when its net is >= 0 and credits it at rates.credit() when the
    net is negative, so the bucket's energy term is piecewise-linear in its net with
    a kink at zero. Moving that net from `net_without` to `net_without + q` costs
    the INTEGRAL of the marginal rate over the span, not q times any one rate: the
    part of the span lying below zero settles at credit(), the part at or above zero
    at energy().

      at_credit = clamp(min(q, -net_without), 0, q)
      value     = credit(s, p) * at_credit + energy(s, p) * (q - at_credit)

    A block whose own bucket stays on one side of zero gets a single rate either
    way, which is why this changes nothing for the super-off-peak cells. A block big
    enough to carry its bucket across zero is the case no single rate can price, and
    that case is real here, not theoretical: two of the off-peak buckets this run
    charges from are net-positive by LESS than the surplus block being taken out of
    them, so part of that block settles as surplus credit and part as netting.

    Returns (value, kwh_priced_at_credit) so the caller can publish the split rather
    than assert it. The `net >= 0` test is exactly bill_nem_monthly()'s own; no rate
    card is consulted anywhere in this module.
    """
    at_credit = min(max(min(q, -net_without), 0.0), q)
    return (R.credit(seas, per) * at_credit + R.energy(seas, per) * (q - at_credit),
            at_credit)


def _recon(priced, rebilled, counterfactual):
    """A stream's priced cost against the exact re-bill of its own counterfactual.

    `rebilled` comes from rates.bill_nem() run twice — once on the dispatch as it
    stands and once with this one stream's modification undone — so it is what the
    billing engine actually charges for that stream, with no pricing model in
    between. Publishing the residual next to the cost is the point: a reader can
    bound the error in the specific number being read instead of inferring it from
    a combined figure in which three streams' errors can cancel.

    Residuals are rounded to 6 decimals, well inside the float noise of summing
    ~35k intervals two different ways, so the artifact stays byte-stable across
    platforms while still showing a real disagreement if one ever appears.
    """
    residual = round(priced - rebilled, 6) + 0.0
    # FAIL CLOSED, like every other reconciliation in this repo
    # (bill_decomposition.py, irreducible_bill.py). index.html section 6 tells the
    # reader each per-kWh cost "reconciles to the penny against a re-bill of its
    # own counterfactual". Publishing the residual without asserting it left that
    # claim unenforced: a later edit to _span_value or to the reference frame
    # would still regenerate a valid artifact, and no test would notice, because
    # the only consumer compares the COSTS to the prose and both sides move
    # together (Codex/review, issue #189).
    if abs(residual) > 0.01:
        raise SystemExit(
            f"battery_dispatch_policies: {counterfactual} -- priced {priced:.6f} "
            f"against an exact re-bill of {rebilled:.6f}, a residual of "
            f"{residual:.6f}. The published per-kWh costs claim to reconcile to "
            "the penny, so this is a pricing error, not a rounding one; refusing "
            "to write an artifact that would make that claim falsely.")
    block = {"priced_usd": round(priced, 2),
             "counterfactual_rebill_usd": round(rebilled, 2),
             # + 0.0 normalises the -0.0 that rounding a tiny negative produces.
             "residual_usd": residual,
             "counterfactual": counterfactual}
    if rebilled:
        block["residual_pct"] = round(100 * (priced - rebilled) / rebilled, 6) + 0.0
    return block


def _census(counts):
    """Which end of the export bracket a stream's kWh actually settled at.

    Counted in kWh rather than in whole buckets, because a bucket can settle at
    both: `buckets` is how many buckets contributed any kWh at that end, and
    buckets_split_across_zero how many contributed to both, so the two bucket
    counts deliberately do not have to sum to the number of buckets.
    """
    out = {t: {"buckets": counts[t]["buckets"], "kwh": round(counts[t]["kwh"], 2)}
           for t in _TREATMENTS}
    out["buckets_split_across_zero"] = counts["buckets_split_across_zero"]
    return out


def stored_energy_cost(d, imp0, gen0, cap, policy, charge_kw, charge_ref=None):
    """What a stored kWh cost THIS dispatch run, from its own charging intervals.

    Issue #189. The report's section 6 quotes a per-kWh cost for stored energy and
    compares it against the import prices the battery discharges into. That cost
    used to be a constant asserted in this module's docstring; here it is measured
    off the run, on the settlement treatment the run's own charging intervals
    actually meet.

    WHERE THE TWO CHARGE STREAMS COME FROM. run_batt() returns the modified import
    and export series, so both streams read straight off the difference against the
    pre-battery baseline, with no second copy of the dispatch logic to drift:

      solar surplus  gen0 - exp2   export the battery consumed instead of selling
      grid top-up    imp2 - imp0   import the battery drew to charge (where > 0)

    A single interval can only be in one of them -- run_batt's surplus-charge,
    grid-top-up and discharge branches each `continue` -- so the two are disjoint
    and their sum, scaled by the one-way efficiency, must rebuild run_batt's own
    reported throughput. That identity is asserted, not assumed.

    HOW EACH kWh IS PRICED, and why it is not one rate per bucket.
    rates.bill_nem_monthly() settles NEM 2.0 by monthly per-period netting: inside
    one (month, season, TOU period) bucket an exported kWh first cancels an imported
    one, and only a bucket left net-NEGATIVE is paid the export credit. So the value
    forgone by storing a kWh instead of exporting it is:

      bucket net >= 0   rates.energy()  = UDC + CEA + PCIA   the export would have
                                                             cancelled an import
      bucket net <  0   rates.credit()  = UDC + CEA          the export would have
                                                             settled as surplus

    A bucket does not have to sit on one side of that line for the whole block being
    priced. Storing E kWh instead of exporting them raises the bucket's net by E, and
    if the span it travels crosses zero, part of the block settles at credit() and
    part at energy(); no single rate is right for all of it. Each block is therefore
    priced piecewise, by _span_value() above, over the span its own stream moves the
    bucket through. That is not a rounding refinement here -- it moves the off-peak
    and blended solar figures, because two off-peak buckets are net-positive by less
    than the surplus block taken out of them.

    NOT rates.allin() for an export. allin() = energy() + NBC is what a GROSS IMPORT
    costs, and an export does not reduce gross imports -- bill_nem_monthly() charges
    NBC on m[imp].sum() before any netting, so the NBC on the cancelled import is on
    the statement either way. This is the same distinction report_tokens.py's
    EXPORT_VALUE_SURPLUS_BOUND / EXPORT_VALUE_NETTING_BOUND carry, and it is not
    decided here by argument: the split is computed per bucket and the census of how
    many kWh took which end is published alongside the cost.

    A GRID top-up is the other case, and it does pay NBC: it IS a gross import, on
    top of whatever the netting adds. NBC rides on every top-up kWh regardless of
    where the bucket's net sits, so it is added outside the piecewise term rather
    than folded into a rate; the artifact records whether every top-up kWh in fact
    landed on energy() + NBC = allin() (it does here -- every super-off-peak bucket
    is net-import by hundreds of kWh, far more than the top-up itself).

    WHICH REFERENCE EACH STREAM IS PRICED FROM, and why that one. Each stream is
    valued at the margin of the FULL dispatch: the span runs from the bucket's net
    WITHOUT that stream's own modification, with everything else the battery did
    left in place, to the net rates.bill_nem() actually prices. For the two charge
    streams that reference is `net - q` (removing the stream's own q kWh from the
    post-dispatch net); for discharge the block runs the other way, so its span is
    [net, net + D]. The reference is not a matter of taste: it is the one that makes
    each stream's priced cost equal, to the float epsilon, an exact counterfactual
    re-bill of that stream -- which is checked here rather than claimed, per stream
    and per period, and published as `reconciliation`.

    The pre-battery sign of each bucket is reported too, as
    buckets_whose_sign_the_battery_flipped: where the two frames disagree the bucket
    flipped BECAUSE the battery discharged into it, and a reader should be able to
    see that rather than take one frame on trust.

    WHAT THE COMBINED RECONCILIATION STILL MEANS. It no longer measures block
    mispricing -- the per-stream residuals above are zero. It measures the one thing
    left: three modifications land in the same buckets at once, and each is valued at
    the margin of the full dispatch, so a zero crossing inside a bucket is credited to
    every stream that crosses it. Summing the three therefore does not reproduce the
    billed saving exactly, and the gap is that interaction, published as an amount
    and a percentage of the billed saving.

    Returns the artifact block; it re-runs run_batt() rather than taking a series in
    so the caller cannot hand it a frame priced on a different dispatch.
    """
    imp2, exp2, served, thru = run_batt(d, imp0, gen0, cap, policy, charge_kw=charge_kw,
                                        charge_ref=charge_ref)
    surplus = gen0 - exp2
    grid = np.maximum(imp2 - imp0, 0.0)
    disch = np.maximum(imp0 - imp2, 0.0)
    if surplus.min() < -1e-9:
        raise SystemExit("stored_energy_cost: the battery run RAISED export in some "
                          "interval, so the surplus it charged from cannot be read "
                          "off the export reduction")
    if abs(ETA * (surplus.sum() + grid.sum()) - thru) > 1e-6:
        raise SystemExit("stored_energy_cost: surplus + grid charging does not "
                          "rebuild run_batt's own reported throughput")

    f = d.copy()
    f["_ym"] = f.ym.astype(str)
    f["_imp2"] = imp2; f["_exp2"] = exp2; f["_imp0"] = imp0; f["_gen0"] = gen0
    f["_surp"] = surplus; f["_grid"] = grid; f["_disch"] = disch
    cols = ["_imp2", "_exp2", "_imp0", "_gen0", "_surp", "_grid", "_disch"]
    g = f.groupby(["_ym", "seas", "p"], sort=True)[cols].sum()

    streams = {"solar_surplus": "_surp", "grid_topup": "_grid"}
    tot = {s: [0.0, 0.0] for s in streams}                       # kWh, $ forgone/paid
    census = {s: dict({t: {"buckets": 0, "kwh": 0.0} for t in _TREATMENTS},
                      buckets_split_across_zero=0) for s in streams}
    by_period = {p: {"kwh": 0.0, "value": 0.0} for p in _PERIODS}
    grid_is_allin = True
    sign_flips = 0
    disch_value = 0.0
    for (_ym, seas, per), row in g.iterrows():
        net = row["_imp2"] - row["_exp2"]
        if (net >= 0) != ((row["_imp0"] - row["_gen0"]) >= 0):
            sign_flips += 1
        for stream, col in streams.items():
            q = float(row[col])
            if q <= 0:
                continue
            # The span this stream moves the bucket through: from the net it would
            # have WITHOUT this stream's own q kWh (the rest of the dispatch left in
            # place, because that is the frame rates.bill_nem() prices) up to the
            # post-dispatch net. Priced piecewise across the zero boundary; a grid
            # kWh additionally pays NBC, which rides on gross imports whatever the
            # bucket's net does, and an avoided export never does.
            value, at_credit = _span_value(q, net - q, seas, per)
            if stream == "grid_topup":
                value += q * R.NBC
                # Same 1e-12 guard the census below uses. With a bare `> 0`,
                # a bucket sitting within float dust of zero could publish
                # every_bucket_priced_at_allin: false beside a census reporting
                # surplus_credit.kwh: 0.0 -- two fields of one artifact
                # disagreeing, with no way to tell which is the artifact of
                # rounding (Codex/review, issue #189).
                if at_credit > 1e-12:
                    grid_is_allin = False
            tot[stream][0] += q
            tot[stream][1] += value
            for treat, share in (("surplus_credit", at_credit),
                                 ("netting_energy", q - at_credit)):
                if share > 1e-12:
                    census[stream][treat]["buckets"] += 1
                    census[stream][treat]["kwh"] += share
            if at_credit > 1e-12 and q - at_credit > 1e-12:
                census[stream]["buckets_split_across_zero"] += 1
            if stream == "solar_surplus":
                by_period[per]["kwh"] += q
                by_period[per]["value"] += value
        dq = float(row["_disch"])
        if dq > 0:
            # Discharge removes import, so it walks the bucket's net the other way:
            # the span runs from the post-dispatch net up to what it would have been
            # without the discharge. Same piecewise rule, plus the NBC the displaced
            # gross import would have paid.
            dv, _ = _span_value(dq, net, seas, per)
            disch_value += dv + dq * R.NBC

    rte = float(ETA * ETA)

    def _cost(kwh, value):
        """A charge stream's per-kWh cost, or the kWh alone when there was none.

        A dispatch can genuinely charge nothing from a stream -- a house with no
        array grid-charges only, a winter-only run may never top up -- and there is
        then no cost per kWh to state. The block still records the zero, and the
        two cost fields are ABSENT rather than zero or null: a consumer reading a
        cost it will publish gets a named refusal from its own missing key, which
        is the right place to fail, while every other figure this generator writes
        keeps being written.
        """
        block = {"kwh": round(kwh, 2), "value_usd": round(value, 2)}
        if kwh <= 0:
            block["note"] = ("this dispatch charged nothing from this stream, so it "
                             "has no cost per kWh")
            return block
        # AC INPUT, not what lands in the pack. `kwh` is measured at the meter:
        # the export that stopped happening, or the import that started. Only
        # kwh*ETA reaches the cells, so calling this "per kWh stored" overstates
        # what the pack holds by 1/ETA (5.4% at 90% round-trip). The name says
        # AC input; cost_per_kwh_delivered below is the figure to quote, and it
        # carries the full round-trip loss (Codex review, issue #189).
        block["cost_per_kwh_ac_input"] = round(value / kwh, 5)
        block["cost_per_kwh_delivered"] = round(value / kwh / rte, 5)
        return block

    save_priced = disch_value - tot["solar_surplus"][1] - tot["grid_topup"][1]
    dispatch_bill = billed(d, imp2, exp2)
    save_billed = billed(d, imp0, gen0) - dispatch_bill

    # EXACT COUNTERFACTUALS, one per stream, each undoing that stream's own
    # modification and leaving the rest of the dispatch alone -- the same reference
    # the piecewise pricing above uses, run through rates.bill_nem() itself so the
    # published cost is checked against the billing engine and not against a second
    # copy of its rules. exp2 + surplus IS gen0 by construction (surplus is defined
    # as gen0 - exp2), so undoing the solar-surplus stream is billing the dispatch's
    # imports against the untouched export series.
    cf_solar = _recon(tot["solar_surplus"][1], dispatch_bill - billed(d, imp2, gen0),
                      "rates.bill_nem() on this dispatch's imports with the export "
                      "series left un-consumed (imp2, gen0), minus the dispatch's "
                      "own bill")
    cf_grid = _recon(tot["grid_topup"][1], dispatch_bill - billed(d, imp2 - grid, exp2),
                     "rates.bill_nem() on this dispatch with the grid top-up removed "
                     "from the import series (imp2 - grid, exp2), minus the "
                     "dispatch's own bill")

    solar = _cost(*tot["solar_surplus"])
    # Only the periods the run actually charged surplus in: a period with no
    # surplus charging has no cost, and inventing a zero-kWh row for it would let
    # a consumer read a share or a rate off a period this dispatch never touched.
    # Each period carries its OWN counterfactual re-bill (that period's surplus put
    # back, the other periods' left consumed), because §6 quotes the super-off-peak
    # cell on its own and a reader of that one number is entitled to its own bound
    # rather than the whole stream's.
    per_of = d.p.values
    hour_of = d.hour.values
    solar["by_period"] = {
        p: dict(_cost(by_period[p]["kwh"], by_period[p]["value"]),
                share_of_surplus_kwh=round(
                    by_period[p]["kwh"] / tot["solar_surplus"][0], 4),
                # Section 6 calls the super-off-peak cell "midday", and on a
                # WEEKEND rates.period_at() calls everything before 14:00 sop --
                # so that label is a claim about this household's data, not
                # about the TOU classification. Measure it rather than assume
                # it: the share of this period's surplus charging that falls in
                # 10:00-14:00. It is 1.0 here (there is no surplus to store at
                # 07:00, weekend or not), and a consistency test refuses the
                # "midday" wording if it ever stops being 1.0 (Codex review,
                # issue #189).
                share_inside_midday_window=round(float(
                    surplus[(per_of == p) & (hour_of >= 10) & (hour_of < 14)].sum()
                    / by_period[p]["kwh"]), 4),
                reconciliation=_recon(
                    by_period[p]["value"],
                    dispatch_bill - billed(d, imp2, exp2 + surplus * (per_of == p)),
                    f"rates.bill_nem() with only this period's forgone exports put "
                    f"back (imp2, exp2 + surplus where p == {p!r}), minus the "
                    f"dispatch's own bill"))
        for p in _PERIODS if by_period[p]["kwh"] > 0}
    solar["census"] = _census(census["solar_surplus"])
    solar["reconciliation"] = cf_solar
    grid_block = _cost(*tot["grid_topup"])
    grid_block["census"] = _census(census["grid_topup"])
    grid_block["every_bucket_priced_at_allin"] = grid_is_allin
    grid_block["reconciliation"] = cf_grid

    return {
        "config": {"name": "pw3" if cap == 13.5 else "pw3x", "capacity_kwh": cap,
                   "policy": policy, "charge_kw": charge_kw,
                   "power_kw": 11.5, "round_trip": round(rte, 2)},
        "solar_surplus": solar,
        "grid_topup": grid_block,
        "buckets_whose_sign_the_battery_flipped": sign_flips,
        "reconciliation": dict(
            {"discharge_value_usd": round(disch_value, 2),
             "charge_cost_usd": round(tot["solar_surplus"][1] + tot["grid_topup"][1], 2),
             "saving_from_marginal_prices_usd": round(save_priced, 2),
             "saving_billed_by_rates_bill_nem_usd": round(save_billed, 2),
             "residual_usd": round(save_priced - save_billed, 2),
             "note": ("what is left here is NOT block mispricing -- each stream's own "
                      "reconciliation above is exact against its counterfactual "
                      "re-bill. It is the interaction between three modifications "
                      "made to the same buckets at once: each is valued at the margin "
                      "of the full dispatch, so a zero crossing inside a bucket is "
                      "credited to every stream that crosses it and the three do not "
                      "re-sum to the billed saving. Read a per-stream residual to "
                      "bound a per-stream price; this bounds only the three-way sum")},
            # A dispatch that saved exactly nothing has no percentage to take the
            # residual against; the two dollar figures above still say everything
            # the check knows, so this omits the ratio rather than dividing by it.
            **({"residual_pct": round(100 * (save_priced - save_billed) / save_billed, 2)}
               if save_billed else {})),
        "method": ("charging intervals of the price-aware run itself: solar surplus "
                   "is the reduction in the export series against the same run's "
                   "pre-battery baseline, grid top-up the rise in the import series. "
                   "Each block is priced across the zero boundary rather than at one "
                   "rate: storing a kWh instead of exporting it raises its (month, "
                   "season, TOU period) bucket's net, and the span that move travels "
                   "settles at rates.credit() below zero and rates.energy() at or "
                   "above it -- the same test bill_nem_monthly() applies, so a bucket "
                   "the block carries across zero splits between the two. Each grid-"
                   "charged kWh additionally pays NBC, because it is a gross import "
                   "and an avoided export is not. Each stream is priced from the "
                   "bucket net it would have WITHOUT that stream, the rest of the "
                   "dispatch in place, and every figure is checked against an exact "
                   "rates.bill_nem() re-bill of that counterfactual (see each block's "
                   "reconciliation). Divided by the round trip to give the cost of a "
                   "kWh DELIVERED, which is what section 6 quotes."),
    }


def summer_profile(d, imp):
    f = d.copy(); f["gi"] = imp; su = f[f.seas == "S"]
    return [round(float(su[su.hour.astype(int) == hh].groupby(
        su[su.hour.astype(int) == hh].dt.dt.date).gi.sum().mean()), 2) for hh in range(24)]

def escalation(save1, cost=14500, fade=0.01, disc=0.05):
    out = {}
    for esc in (0.03, 0.05, 0.08, 0.12):
        cum = 0; pay = None; npv = -cost
        for y in range(1, 16):
            sv = save1 * ((1 + esc) ** (y - 1)) * ((1 - fade) ** (y - 1)); cum += sv
            if pay is None and cum >= cost: pay = y - 1 + (cost - (cum - sv)) / sv
            if y <= 10: npv += sv / ((1 + disc) ** y)
        out[f"{int(esc*100)}%"] = {"payback": round(pay, 1), "npv10": round(npv)}
    return out

# The behavior scenario each household's FREE FIX is, keyed by the intake flag
# household.has_ev. These are behavior_rebuild.py's own scenario letters, and
# package_results.py picks the SAME two for packages.LOW -- the two scripts must
# agree, which is why the choice is published and cross-checked rather than
# re-derived on each side.
FREE_FIX_SCENARIO_EV = "a"      # EV-only charge reschedule, 100% compliance
FREE_FIX_SCENARIO_NO_EV = "c"   # 25% of flexible on-peak house load, moved to SOP
# behavior_rebuild.py's scenario (c) fraction. Named here so the two scripts
# cannot silently drift to different definitions of "the same" scenario.
HOUSE_SHIFT_FRAC = 0.25


def free_fix_shift(d, imp0):
    """Apply THIS household's free behavior fix; return (imp, kwh_moved, scenario).

    The battery is valued on top of the free fix, not on top of the bare
    baseline, so the package figures are one integrated re-billed run (CLAUDE.md
    §9). Which fix that is comes from behavior_rebuild.py's own intake predicate
    EV_ANALYSIS (household.has_ev), never from whether a shift happens to move
    anything: br.shift_ev() on a household with no EV silently returns the
    baseline unchanged, and reading that no-op as "the free fix is already in"
    is precisely the defect this function exists to remove.

    Both branches call behavior_rebuild.py's OWN shift function with the same
    arguments behavior_rebuild.main() gives it for that scenario, so the shifted
    series here is the series behind the scenario's published `saved` figure and
    not a second implementation of it:

      scenario a  br.shift_ev  every detected session, moved to the next
                  overnight SOP window, capped at the charger's power (CAP_KWH)
      scenario c  br.shift_house  HOUSE_SHIFT_FRAC of each day's remaining
                  on-peak non-EV import, capped at the largest 15-min import
                  this house has actually drawn — the data-derived destination
                  cap behavior_rebuild.main() derives for a house with no
                  charger, measured off this meter rather than assumed.

    Returns the scenario KEY too. A consumer must be able to read which fix ran
    instead of re-deriving the branch from an intake flag it cannot see.
    """
    sop_idx, sop_ts = br.build_sop_index(d)
    ev, sessions = br.detect_sessions(d)
    if br.EV_ANALYSIS:
        imp_sh, moved = br.shift_ev(d, ev, sessions, [True] * len(sessions),
                                    sop_idx, sop_ts)
        return imp_sh, moved, FREE_FIX_SCENARIO_EV
    # No charger, so no hardware cap on the destination intervals; the cap is
    # this house's own largest observed 15-min import (behavior_rebuild.main()).
    house_cap = float(np.max(imp0))
    imp_sh, moved = br.shift_house(d, imp0, ev, HOUSE_SHIFT_FRAC,
                                   sop_idx, sop_ts, house_cap)
    return imp_sh, moved, FREE_FIX_SCENARIO_NO_EV


# What the escalation ladder is seeded from, per free-fix scenario. The ladder
# runs on the battery's marginal saving AFTER the free fix, so the note has to
# name the fix that actually preceded it.
_ESCALATION_SEED_NOTE = {
    FREE_FIX_SCENARIO_EV:
        "seeded from the post-EV-fix battery marginal (the decision-relevant figure)",
    FREE_FIX_SCENARIO_NO_EV:
        ("seeded from the post-house-shift battery marginal (the "
         "decision-relevant figure; household.has_ev is false, so the free fix "
         "that precedes the battery is behavior scenario c, not the EV shift)"),
}

# What the discharge-eligibility rule in run_batt() DID, per household, published
# as notes.ev_exclusion. Two strings rather than one because the rule itself is
# two rules (issue #147): a household with no EV has no spillover to exclude, and
# an artifact that claimed one would be describing a filter that never ran.
EV_EXCLUSION_NOTE_EV = ">=2.5 kW outside on-peak = EV spillover, never battery-served"
EV_EXCLUSION_NOTE_NO_EV = (
    "no EV-spillover exclusion applied: household.has_ev is false, so every "
    "import outside super-off-peak is battery-servable, including ordinary "
    "house load at or above 2.5 kW")


def ev_exclusion_note():
    """The notes.ev_exclusion string this household's dispatch earned.

    Reads the SAME predicate run_batt() gates the exclusion on, so the artifact
    cannot describe a filter the dispatch did not apply.
    """
    return EV_EXCLUSION_NOTE_EV if br.EV_ANALYSIS else EV_EXCLUSION_NOTE_NO_EV


if __name__ == "__main__":
    d = br.load()
    imp0 = d.Consumption.values.astype(float); gen0 = d.Generation.values.astype(float)
    base = billed(d, imp0, gen0)
    out = {"baseline_bill_current_rates": round(base)}
    # Serviceable-load inputs behind the report's §6 sentence. Period assignment is
    # rates.period_at, which applies the eight tariff holidays that tou_audit.py
    # confirmed against the bills, so this now agrees with the holiday-as-weekend
    # convention the legacy ranking model has always used. The two bases used to
    # disagree by ~40 kWh of off-peak import; that gap is closed.
    _p = d.p.values; _kw = imp0 * 4
    # The off-peak slice is a RESTATEMENT of run_batt()'s own discharge-eligibility
    # rule, so it carries the same EV gate (issue #147). With an EV, only the
    # sub-2.5 kW part of off-peak import is servable — the rest is spillover the
    # free schedule fix moves. With household.has_ev false there is no spillover,
    # the dispatch serves all of it, and a figure that still subtracted a 2.5 kW
    # slice would contradict notes.ev_exclusion in the same artifact.
    _servable_off = ((_p == "off") & (_kw < 2.5)) if br.EV_ANALYSIS else (_p == "off")
    inp = {"nonsop_import_kwh": round(float(imp0[_p != "sop"].sum())),
           "onpeak_import_kwh": round(float(imp0[_p == "on"].sum())),
           "servable_offpeak_house_kwh": round(float(imp0[_servable_off].sum()))}
    inp["serviceable_total_kwh"] = inp["onpeak_import_kwh"] + inp["servable_offpeak_house_kwh"]
    inp["note"] = ("canonical rates.period_at assignment, including the eight "
                   "bill-confirmed tariff holidays as weekend days")
    out["inputs"] = inp
    # charge_kw is BARE-UNIT 5 kW for the 13.5 kWh "pw3" config, WITH-EXPANSION
    # 8 kW for the 27 kWh "pw3x" (PW3 + 1 Expansion) config -- issue #40
    # correction: these are genuinely different, cited datasheet figures, not
    # one number applied uniformly across configurations.
    for cap, name, chg_kw in [(13.5, "pw3", CHARGE_KW), (27.0, "pw3x", CHARGE_KW_WITH_EXPANSION)]:
        row = {}
        for pol in ("evening", "twowin", "greedy"):
            i2, e2, served, thru = run_batt(d, imp0, gen0, cap, pol, charge_kw=chg_kw)
            row[pol] = {"save": round(base - billed(d, i2, e2)),
                        "kwh_served": round(served),
                        "cycles_per_day": round(thru / cap / 365, 2)}
        i2, e2, _, _ = run_batt(d, imp0, gen0, cap, "greedy", charge_kw=chg_kw)
        row["greedy_profile_S"] = summer_profile(d, i2)
        f = d.copy(); f["gi"] = i2
        row["onpeak_after_greedy"] = round(f[(f.hour >= 16) & (f.hour < 21)].gi.sum())
        row["charge_kw"] = chg_kw
        out[name] = row
        print(name, {k: v for k, v in row.items() if isinstance(v, dict)})
    # post-behavior integrated package: this household's free fix (scenario a
    # with an EV, scenario c without), then the battery on the shifted load
    imp_sh, moved, fix_scenario = free_fix_shift(d, imp0)
    b_sh = billed(d, imp_sh, gen0)
    pb = {"behavior_save": round(base - b_sh), "kwh_moved": round(moved),
          # WHICH free fix the two figures above, and every mid/high combined_save
          # below, sit on top of. package_results.py refuses to compose packages.MID
          # from this block unless it matches packages.LOW.free_fix_scenario, so a
          # cross-run blend is caught rather than published.
          "free_fix_scenario": fix_scenario}
    for cap, name, chg_kw in [(13.5, "mid", CHARGE_KW), (27.0, "high", CHARGE_KW_WITH_EXPANSION)]:
        i3, e3, _, _ = run_batt(d, imp_sh, gen0, cap, "greedy", charge_kw=chg_kw)
        b2 = billed(d, i3, e3)
        pb[name] = {"battery_marginal": round(b_sh - b2),
                    "combined_save": round(base - b2), "bill": round(b2),
                    "charge_kw": chg_kw}
    out["post_behavior"] = pb
    out["escalation_greedy_pw3_post_behavior"] = escalation(pb["mid"]["battery_marginal"])
    out["escalation_note"] = _ESCALATION_SEED_NOTE[fix_scenario]
    out["notes"] = {"engine": "rates.bill_nem (monthly per-period NEM netting, NBC on gross imports)",
                    "ev_exclusion": ev_exclusion_note(),
                    "rte": 0.9, "power_kw": 11.5,
                    "charge_kw_bare_unit": CHARGE_KW,
                    "charge_kw_with_expansion": CHARGE_KW_WITH_EXPANSION,
                    "charge_kw_note": ("13.5 kWh pw3/mid configs use the bare-unit charge "
                                       "rate; 27 kWh pw3x/high configs use the with-"
                                       "expansion rate -- see CHARGE_KW/"
                                       "CHARGE_KW_WITH_EXPANSION in this module"),
                    "requires": "multi-window time-based control"}
    # Issue #189: what a stored kWh cost, derived from the price-aware run's own
    # charging intervals rather than asserted as a constant. The 13.5 kWh "pw3"
    # config on the "greedy" policy is the run section 6's narrative describes;
    # the block names it so no reader has to infer which dispatch it came from.
    out["stored_kwh_cost"] = stored_energy_cost(d, imp0, gen0, 13.5, "greedy", CHARGE_KW)
    print("stored_kwh_cost:", {k: v for k, v in out["stored_kwh_cost"].items()
                               if k in ("solar_surplus", "grid_topup")})
    json.dump(out, open("battery_dispatch_policies.json", "w"), indent=1)
    print("post_behavior:", pb)
    print("escalation:", out["escalation_greedy_pw3_post_behavior"])
