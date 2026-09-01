#!/usr/bin/env python3
"""What one more kW of panels is worth here (issue #190) -> data/marginal_capacity_value.json

THE QUESTION, AND WHY A RATE TIMES A QUANTITY CANNOT ANSWER IT. Section 8 of
the report prices what the year's EXPORTS fetched (a bounded range, issue
#182). Exports are the residual left after household load, not the shape of
what an added panel produces: some of an added panel's output displaces an
import in the interval it is made, and the rest leaves the meter as an extra
export. Under NEM 2.0 those two are settled differently again, month by month
and TOU period by TOU period, so the only defensible value is a counterfactual:
put the added production into the real intervals, re-bill the whole year
through the same engine every other published figure uses, and take the
difference (CLAUDE.md sections 1b and 9).

THE COUNTERFACTUAL, step by step:

  1. Marginal generation profile -- PRODUCTION-SHAPED, and its source named.
     No hourly production feed is committed (data/enphase_daily_production.csv
     is daily), so the hourly series is derived from two independent meters
     with the identity

         pv_hour = max(sam_hour - import_hour + export_hour, 0)

     where sam_hour is the Enphase SAM 8760 whole-home consumption CT's gross
     load (private/1-raw-data/enphase_sam8760_*.csv, staged as samA.csv /
     samB.csv, loaded by threeway_production_validation.load_sam_hourly()) and
     import/export are the SDG&E revenue meter's own 15-minute columns
     (usage.csv, through behavior_rebuild.load()) summed to the hour. This is
     the same identity threeway_production_validation.py's meter_derived and
     service_headroom.py's derive_pv() use; the clip at zero absorbs dark-hour
     instrument noise. The derived series is tied out two ways before
     anything is priced: day by day against the committed meter_derived
     column (same identity, must agree to the rounding of that file), and as
     an annual total against the Enphase production CT's own daily record
     over the window (an independent instrument; the ratio is published).

  2. The increment. Adding ADDED_KW_DC of panels to a kw_dc array scales the
     derived hourly production by (kw_dc + added) / kw_dc, so the increment
     is pv_hour * added / kw_dc. It is spread evenly over the quarter-hours
     of its hour (the SAM grid is hourly; the intra-hour shape is a stated
     assumption) and placed into each 15-minute interval: it first reduces
     that interval's import, and whatever is left after the import reaches
     zero becomes export. Energy is conserved by construction and asserted.

  3. Re-bill both years through rates.bill_nem_monthly() (monthly per-period
     NEM 2.0 netting, NBC on gross imports, BSC per day) at CONSTANT CURRENT
     RATES. The value is the bill delta. A decomposition (kWh that net inside
     a still-net-positive bucket at rates.energy(), kWh that turn a bucket
     net-negative and settle at rates.credit(), plus NBC saved on the offset
     imports) is computed independently and must reproduce the engine's
     delta to the cent, so the artifact states WHY the number is what it is.

ASSUMPTIONS, every one stated in the artifact:
  * DC/AC ratio: the added kW carries the array's own ratio (kw_dc / kw_ac
    from private/household.yaml), i.e. it is more of the same hardware.
  * Clipping: two cases are priced and the intake decides which is
    published as per_added_kw. With one inverter per module
    (solar.inverter_count == solar.module_count) the AC ceiling scales with
    the added capacity, because each added module brings its own inverter.
    With any other architecture the ceiling stays at today's AC nameplate
    (solar.kw_ac, the AC_CEILING_KW intake fact) and the scaled hourly energy
    is clipped there. The artifact publishes the rule, its two inputs, the
    case it chose and both cases' figures. Clipping is evaluated on hourly
    energy; a sub-hourly peak above the hourly mean is invisible in the SAM
    grid, so each case is a lower bound on its own clipping loss.
  * Degradation: the added kW produces at the measured array's own specific
    yield in the measured year. No first-year uplift for newer modules and no
    degradation adjustment is applied, because nothing committed measures
    either (the report's own health check finds no degradation signal).
  * Rates: constant current rates, the same vintage as every other modeled
    figure. The bills the engine is validated against were partly on older
    tariffs.
  * DST: the two DST transition Sundays cannot be aligned between the flat
    SAM grid and the wall-clock meter (threeway_production_validation.py's
    own exclusion), so they carry NO increment. The artifact publishes the
    excluded days' share of the year's metered production, which bounds the
    understatement.
  * NEM 2.0: the netting structure is NEM 2.0's. An expansion inside the
    growth cap keeps that status; the section 8 verdict about exceeding the
    cap is a separate, artifact-backed question, and this script re-checks
    the derived value against the grandfathering bracket it rests on.

THE PAYBACK IS NOT A FIGURE THIS REPO CAN PUBLISH. Retrofit dollars per watt
is a fact about a local installer market on a date, which nothing here
collects. The artifact carries a $/W SENSITIVITY LADDER labelled as an
assumption, so a reader with a quote can read the years off it; the
EXPANSION_PAYBACK_YEARS token stays a report_tokens.KNOWN_GAPS entry.

Run from private/verify per the standard sandbox (needs usage.csv, samA.csv,
samB.csv beside it, plus the committed data/threeway_production_validation.csv,
data/enphase_daily_production.csv and data/nem3_grandfathering.json). Reads
private/household.yaml through analysis/household.py and fails closed without
it. Writes data/marginal_capacity_value.json directly (repo-root discovery,
atomic tmp-then-replace, the same convention quiet_night_floor.py and
threeway_production_validation.py use).
"""
import collections
import csv
import datetime as dt
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import household as HH   # noqa: E402  -- fails closed without private/household.yaml
import rates as R        # noqa: E402


def repo_root():
    """Nearest ancestor of the CWD (sandbox convention) or of this file that
    contains both analysis/ and data/ -- same contract as
    threeway_production_validation.py and quiet_night_floor.py."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("marginal_capacity_value.py: repo root not found: no ancestor "
                     "contains analysis/ and data/")


ROOT = repo_root()
DATA = ROOT / "data"
OUT = DATA / "marginal_capacity_value.json"
THREEWAY_CSV = DATA / "threeway_production_validation.csv"
ENPHASE_CSV = DATA / "enphase_daily_production.csv"
NEM3_JSON = DATA / "nem3_grandfathering.json"

ADDED_KW_DC = 1.0
# ASSUMED retrofit prices, dollars per DC watt installed. Not a quote and not
# a market survey: rungs a reader with a real quote can interpolate between.
RETROFIT_USD_PER_W_LADDER = (2.0, 2.5, 3.0, 3.5, 4.0)
# How closely the derived daily production must reproduce the committed
# meter_derived column (data/threeway_production_validation.csv rounds to
# 0.001 kWh, and a day sums 24 rounded hours at most).
THREEWAY_TIE_OUT_KWH = 0.05
# The bill delta and its analytical decomposition must agree to the cent.
DECOMPOSITION_TOLERANCE_USD = 0.01


# ---------------------------------------------------------------------------
# 1. Inputs: the array's nameplate (household.yaml), the meter, the SAM grid.
# ---------------------------------------------------------------------------
def _positive(key, cast, what):
    raw = HH.get(key)   # SystemExit through household.py when absent
    try:
        val = cast(raw)
    except (TypeError, ValueError):
        raise SystemExit(f"marginal_capacity_value.py: private/household.yaml:{key} "
                         f"is {raw!r}, not a number")
    whole = cast is not int or float(raw) == float(val)   # 30.5 modules is not a count
    if isinstance(raw, bool) or not math.isfinite(val) or val <= 0 or not whole:
        raise SystemExit(f"marginal_capacity_value.py: private/household.yaml:{key} "
                         f"is {raw!r}; {what} must be positive")
    return val


def array_nameplate():
    """(kw_dc, kw_ac) from private/household.yaml, both required and positive.

    kw_dc scales the increment; kw_ac is the clipping ceiling. Both are
    public-ok intake fields (they print in section 2 of the report)."""
    return (_positive("solar.kw_dc", float, "the array's DC nameplate in kW"),
            _positive("solar.kw_ac", float, "the array's AC ceiling in kW"))


def inverter_architecture():
    """The intake facts that decide which clipping case is primary, and the
    decision itself, published together so a reader can see the branch.

    per_module_inverters is True when the intake lists one inverter per
    module (inverter_count == module_count, more than one of each): an added
    module then brings its own inverter and the AC ceiling grows with the
    array. Any other architecture (one string inverter, a few string
    inverters, optimizers under one inverter) keeps today's AC ceiling, so
    the added DC is clipped there. The rule is a claim about the count only;
    the model string is carried for provenance, not matched."""
    module_count = _positive("solar.module_count", int, "the module count")
    inverter_count = _positive("solar.inverter_count", int, "the inverter count")
    model = HH.get("solar.inverter_model")
    if not isinstance(model, str) or not model.strip():
        raise SystemExit("marginal_capacity_value.py: private/household.yaml:"
                         f"solar.inverter_model is {model!r}, not a model name")
    per_module = inverter_count > 1 and inverter_count == module_count
    return {
        "module_count": module_count,
        "inverter_count": inverter_count,
        "inverter_model": model.strip(),
        "per_module_inverters": per_module,
        "rule": ("one inverter per module (solar.inverter_count == solar.module_count, "
                 "both above one) means an added module brings its own inverter and "
                 "the AC ceiling scales with the array; any other count keeps today's "
                 "AC ceiling (solar.kw_ac) and clips the added output there"),
    }


def load_frame():
    """behavior_rebuild.load(): the report's own 365-day window, coverage
    validated, TOU period and season assigned by rates.py."""
    for fname in ("usage.csv", "samA.csv", "samB.csv"):
        if not pathlib.Path(fname).is_file():
            raise SystemExit(f"marginal_capacity_value.py: {fname} not found in the "
                             "working directory -- run from private/verify with the "
                             "raw Green Button export and both SAM 8760 exports staged")
    import behavior_rebuild as br
    return br.load()


def hourly_meter(d):
    """{(date, hour): [import_kwh, export_kwh, n_intervals]} from the frame."""
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for ts, imp, exp in zip(d.dt, d.Consumption, d.Generation):
        a = acc[(ts.date(), ts.hour)]
        a[0] += float(imp)
        a[1] += float(exp)
        a[2] += 1
    return dict(acc)


def derive_production(hsums, sam, excluded_days):
    """({(date, hour): pv_kwh}, clipped_negative_kwh) over every hour of the
    frame, via pv = max(sam - import + export, 0). Refuses an hour the SAM
    grid does not cover: nothing may stand in for an unmeasured hour."""
    pv, clipped = {}, 0.0
    for key in sorted(hsums):
        if key[0] in excluded_days:
            continue
        if key not in sam:
            raise SystemExit(f"marginal_capacity_value.py: the SAM 8760 exports carry "
                             f"no row for {key[0]} hour {key[1]:02d}, which the meter "
                             "window needs; no production can be derived for it and "
                             "nothing may be substituted")
        imp, exp, _n = hsums[key]
        resid = sam[key] - imp + exp
        if resid < 0.0:
            clipped += -resid
        pv[key] = max(resid, 0.0)
    return pv, clipped


# ---------------------------------------------------------------------------
# 2. Tie-outs on the derived profile, BEFORE anything is priced.
# ---------------------------------------------------------------------------
def tie_out_threeway(pv, dates, excluded_days):
    """The derived series, summed per day, against the committed
    meter_derived column: same identity, same loader, so they must agree to
    that file's own rounding on every non-DST day the two share."""
    if not THREEWAY_CSV.is_file():
        raise SystemExit(f"marginal_capacity_value.py: {THREEWAY_CSV} is missing; the "
                         "derived production profile has nothing to tie out against")
    committed = {}
    with open(THREEWAY_CSV, newline="") as fh:
        for row in csv.DictReader(fh):
            val = row["meter_derived"]
            if val.strip() == "":
                continue
            committed[dt.date.fromisoformat(row[""])] = float(val)
    daily = collections.defaultdict(float)
    for (day, _h), kwh in pv.items():
        daily[day] += kwh
    shared = [x for x in dates if x in committed and x not in excluded_days]
    if not shared:
        raise SystemExit("marginal_capacity_value.py: data/threeway_production_"
                         "validation.csv shares no non-DST day with this window, so "
                         "the derived profile cannot be tied out")
    worst = max(abs(daily[x] - committed[x]) for x in shared)
    if worst > THREEWAY_TIE_OUT_KWH:
        raise SystemExit(f"marginal_capacity_value.py: the derived daily production "
                         f"disagrees with data/threeway_production_validation.csv's "
                         f"meter_derived by up to {worst:.3f} kWh on a shared day "
                         f"(tolerance {THREEWAY_TIE_OUT_KWH}); the two are the same "
                         "identity on the same meters and must agree")
    return {"days_compared": len(shared), "max_abs_daily_diff_kwh": round(worst, 3),
            "tolerance_kwh": THREEWAY_TIE_OUT_KWH}


def load_enphase_daily():
    """{date: kWh} from data/enphase_daily_production.csv (the production
    CT's own daily record; same reader as threeway_production_validation.py)."""
    out = {}
    with open(ENPHASE_CSV) as fh:
        for row in csv.DictReader(fh):
            ds = row["Date/Time"].strip()
            if ds.count("/") != 2:
                continue
            m, d, y = ds.split("/")
            try:
                val = float(row["Energy Delivered (kWh)"])
            except ValueError:
                continue
            out[dt.date(int(y), int(m), int(d))] = val
    return out


def reconcile_with_meter_record(pv, dates, excluded_days):
    """The derived annual total against the Enphase production CT's daily
    record over the same window: an independent instrument, so this is a
    reconciliation (published), not an identity (asserted)."""
    if not ENPHASE_CSV.is_file():
        raise SystemExit(f"marginal_capacity_value.py: {ENPHASE_CSV} is missing")
    meter = load_enphase_daily()
    missing = [x for x in dates if x not in meter]
    if missing:
        raise SystemExit(f"marginal_capacity_value.py: data/enphase_daily_production.csv "
                         f"has no row for {len(missing)} day(s) of the window "
                         f"(first {missing[0]}); the annual reconciliation needs every "
                         "day")
    derived = sum(pv.values())
    metered = sum(meter[x] for x in dates)
    excluded_kwh = sum(meter[x] for x in sorted(excluded_days))
    if metered <= 0.0:
        raise SystemExit("marginal_capacity_value.py: the production CT's record sums "
                         "to zero over the window; nothing can be reconciled")
    return {
        "derived_kwh_non_dst_days": round(derived, 1),
        "meter_record_kwh_all_days": round(metered, 1),
        "meter_record_kwh_non_dst_days": round(metered - excluded_kwh, 1),
        "derived_over_meter_non_dst_ratio": round(derived / (metered - excluded_kwh), 4),
        "excluded_dst_days_meter_kwh": round(excluded_kwh, 2),
        "excluded_dst_days_share_of_annual_pct": round(excluded_kwh / metered * 100, 2),
    }


# ---------------------------------------------------------------------------
# 3. The increment, placed interval by interval.
# ---------------------------------------------------------------------------
def increment_per_interval(d, pv, hsums, kw_dc, added_kw, ceiling_kwh=None):
    """Per-interval added production (kWh) for `added_kw` more DC capacity.

    Each hour's increment is pv_hour * added_kw / kw_dc, spread evenly over
    the intervals the frame carries for that hour. With `ceiling_kwh` given,
    the SCALED hourly energy is clipped there first and the increment is what
    survives (the fixed-ceiling sensitivity). Hours with no derived production
    (the excluded DST days) get zero. Returns (deltas, clipped_kwh, hours_at_ceiling)."""
    scale = added_kw / kw_dc
    deltas, clipped, at_ceiling = [], 0.0, 0
    per_hour = {}
    for key, kwh in pv.items():
        grown = kwh * (1.0 + scale)
        if ceiling_kwh is not None and grown > ceiling_kwh:
            clipped += grown - ceiling_kwh
            at_ceiling += 1
            grown = ceiling_kwh
        per_hour[key] = max(grown - kwh, 0.0)
    for ts in d.dt:
        key = (ts.date(), ts.hour)
        if key not in per_hour:
            deltas.append(0.0)
            continue
        deltas.append(per_hour[key] / hsums[key][2])
    return deltas, clipped, at_ceiling


def apply_increment(imp0, exp0, deltas):
    """(imp1, exp1, offset, export) per interval: the increment first reduces
    the interval's import; the remainder past zero import becomes export."""
    imp1, exp1, offset, export = [], [], [], []
    for i, e, x in zip(imp0, exp0, deltas):
        used = min(i, x)
        left = x - used
        imp1.append(i - used)
        exp1.append(e + left)
        offset.append(used)
        export.append(left)
    total_in = sum(deltas)
    total_out = sum(offset) + sum(export)
    if abs(total_in - total_out) > 1e-6 * max(1.0, total_in):
        raise SystemExit(f"marginal_capacity_value.py: energy not conserved placing the "
                         f"increment ({total_in:.6f} kWh in, {total_out:.6f} kWh out)")
    return imp1, exp1, offset, export


def bill(d, imp, exp):
    """Annual and monthly $ through the canonical engine."""
    f = d[["dt", "seas", "p", "ym"]].copy()
    f["imp"] = list(imp)
    f["exp"] = list(exp)
    monthly = R.bill_nem_monthly(f, "imp", "exp")
    return sum(monthly.values()), monthly


def bill_delta(base_total, total):
    """The value: the year's bill before the increment minus after it.

    rates.bill_nem_monthly() is monotone in the netted kWh (every energy()
    and credit() cell is positive, NBC is charged on gross imports), so added
    production can only lower the bill. A rise means the frame or the engine
    is broken, not a finding, and the run stops rather than publish it."""
    delta = base_total - total
    if delta < 0.0:
        raise SystemExit(f"marginal_capacity_value.py: the re-billed year costs "
                         f"${-delta:.2f} MORE with the added production; the NEM "
                         "engine cannot price added output below zero, so the inputs "
                         "are wrong")
    return delta


def primary_clipping_case(arch):
    """Which clipping case per_added_kw publishes, from the intake's inverter
    architecture (inverter_architecture()): the ceiling scales with the array
    under per-module inverters and stays at today's nameplate otherwise."""
    return "scaled_ceiling" if arch["per_module_inverters"] else "fixed_ceiling"


def decompose(d, imp0, exp0, imp1, exp1, offset):
    """The bill delta rebuilt from the tariff's own pieces, bucket by bucket.

    In a (month, season, period) bucket the increment lowers the net by its
    whole amount, offset and export alike. While the bucket stays net-positive
    that kWh is worth rates.energy(); the part that pushes a bucket below zero
    settles at rates.credit(); and NBC is saved only on the kWh that displaced
    a gross import. BSC does not move. The caller asserts this reproduces
    rates.bill_nem_monthly()'s delta."""
    f = d[["ym", "seas", "p"]].copy()
    f["i0"], f["e0"], f["i1"], f["e1"], f["off"] = (
        list(imp0), list(exp0), list(imp1), list(exp1), list(offset))
    netted_kwh = surplus_kwh = netted_usd = surplus_usd = 0.0
    flipped = []
    by_period = {}
    for (ym, s, p), g in f.groupby(["ym", "seas", "p"]):
        net0 = float(g.i0.sum() - g.e0.sum())
        net1 = float(g.i1.sum() - g.e1.sum())
        drop = net0 - net1
        off = float(g.off.sum())
        if net1 >= 0.0:
            n_kwh, s_kwh = drop, 0.0
        elif net0 >= 0.0:
            n_kwh, s_kwh = net0, -net1
            flipped.append(f"{ym}:{s}:{p}")
        else:
            n_kwh, s_kwh = 0.0, drop
        netted_kwh += n_kwh
        surplus_kwh += s_kwh
        netted_usd += n_kwh * R.energy(s, p)
        surplus_usd += s_kwh * R.credit(s, p)
        cell = by_period.setdefault(f"{s}:{p}", {"offset_kwh": 0.0, "export_kwh": 0.0,
                                                  "value_usd": 0.0})
        cell["offset_kwh"] += off
        cell["export_kwh"] += drop - off
        cell["value_usd"] += n_kwh * R.energy(s, p) + s_kwh * R.credit(s, p) + off * R.NBC
    nbc_usd = sum(offset) * R.NBC
    return {
        "netted_kwh": round(netted_kwh, 1),
        "netted_value_usd": round(netted_usd, 2),
        "surplus_kwh": round(surplus_kwh, 1),
        "surplus_value_usd": round(surplus_usd, 2),
        "nbc_saved_on_offset_imports_usd": round(nbc_usd, 2),
        "buckets_pushed_net_negative": flipped,
        "total_usd": netted_usd + surplus_usd + nbc_usd,
        "by_period": {k: {kk: round(vv, 2 if kk.endswith("usd") else 1)
                          for kk, vv in v.items()} for k, v in sorted(by_period.items())},
    }


def payback_ladder(delta_usd_yr, added_kw):
    """Simple payback at each ASSUMED $/W rung; null where nothing pays back."""
    rungs = []
    for usd_per_w in RETROFIT_USD_PER_W_LADDER:
        cost = usd_per_w * 1000.0 * added_kw
        years = round(cost / delta_usd_yr, 1) if delta_usd_yr > 0 else None
        rungs.append({"assumed_usd_per_w": usd_per_w, "cost_usd": round(cost),
                      "simple_payback_years": years})
    return rungs


def verdict_check(delta_usd_yr, kw_dc, added_kw):
    """The section 8 verdict re-checked: the growth cap the tariff permits,
    whether the increment sits inside it, and the derived value against the
    grandfathering bracket that exceeding the cap puts at risk."""
    if not NEM3_JSON.is_file():
        raise SystemExit(f"marginal_capacity_value.py: {NEM3_JSON} is missing; the "
                         "verdict check needs the grandfathering bracket")
    nem = json.loads(NEM3_JSON.read_text())["grandfathering_value_range_usd_per_yr"]
    low, high = float(nem["low"]), float(nem["high"])
    if not (math.isfinite(low) and math.isfinite(high) and 0.0 < low <= high):
        raise SystemExit(f"marginal_capacity_value.py: data/nem3_grandfathering.json's "
                         f"grandfathering bracket is {nem!r}, not a positive low-high range")
    # Schedule NEM Special Condition 7(b): the greater of 10% of capacity or 1 kW.
    cap_kw = max(0.10 * kw_dc, 1.0)
    return {
        "nem2_growth_cap_kw": round(cap_kw, 3),
        "cap_rule": "greater of 10% of the array's DC nameplate or 1 kW "
                    "(Schedule NEM, Special Condition 7(b))",
        "added_kw_within_cap": bool(added_kw <= cap_kw + 1e-9),
        "grandfathering_at_risk_usd_yr": {"low": low, "high": high,
                                          "source": "data/nem3_grandfathering.json"},
        "grandfathering_over_added_kw_value": {
            "low": round(low / delta_usd_yr, 1) if delta_usd_yr > 0 else None,
            "high": round(high / delta_usd_yr, 1) if delta_usd_yr > 0 else None},
    }


# ---------------------------------------------------------------------------
# 4. The run.
# ---------------------------------------------------------------------------
def build():
    kw_dc, kw_ac = array_nameplate()
    arch = inverter_architecture()
    nem_version = HH.get("household.nem_version")
    d = load_frame()
    import threeway_production_validation as tpv
    sam = tpv.load_sam_hourly()
    dates = sorted(set(ts.date() for ts in d.dt))
    excluded = tpv.dst_dates_in(dates)
    hsums = hourly_meter(d)
    pv, clipped_neg = derive_production(hsums, sam, excluded)
    if not pv or sum(pv.values()) <= 0.0:
        raise SystemExit("marginal_capacity_value.py: the derived production profile is "
                         "empty or zero; nothing to scale")
    max_hour = max(pv.values())
    if max_hour > kw_ac + 1e-9:
        raise SystemExit(f"marginal_capacity_value.py: a derived production hour "
                         f"({max_hour:.3f} kWh) exceeds the {kw_ac} kW AC nameplate, so "
                         "neither the profile nor the ceiling can be trusted")
    threeway = tie_out_threeway(pv, dates, excluded)
    recon = reconcile_with_meter_record(pv, dates, excluded)

    imp0 = [float(v) for v in d.Consumption]
    exp0 = [float(v) for v in d.Generation]
    base_total, base_monthly = bill(d, imp0, exp0)

    def price(deltas):
        imp1, exp1, offset, export = apply_increment(imp0, exp0, deltas)
        total, monthly = bill(d, imp1, exp1)
        dec = decompose(d, imp0, exp0, imp1, exp1, offset)
        delta = bill_delta(base_total, total)
        if abs(dec["total_usd"] - delta) > DECOMPOSITION_TOLERANCE_USD:
            raise SystemExit(f"marginal_capacity_value.py: the engine's bill delta "
                             f"(${delta:.4f}) and its tariff decomposition "
                             f"(${dec['total_usd']:.4f}) disagree; the value cannot be "
                             "explained by the tariff's own pieces")
        dec["reconciles_to_engine_delta_within_usd"] = round(abs(dec["total_usd"] - delta), 4)
        dec["total_usd"] = round(dec["total_usd"], 2)
        added = sum(deltas)
        return {
            "added_production_kwh": round(added, 1),
            "import_offset_kwh": round(sum(offset), 1),
            "import_offset_pct": round(sum(offset) / added * 100, 1),
            "exported_kwh": round(sum(export), 1),
            "exported_pct": round(sum(export) / added * 100, 1),
            "bill_before_usd": round(base_total, 2),
            "bill_after_usd": round(total, 2),
            "bill_delta_usd_yr": round(delta, 2),
            "value_per_added_kwh_cents": round(delta / added * 100, 2),
            "monthly_delta_usd": {m: round(base_monthly[m] - monthly[m], 2)
                                  for m in base_monthly},
            "settlement_decomposition": dec,
        }

    # Both clipping cases are priced; the intake's inverter architecture
    # decides which one is the published per_added_kw figure.
    scaled_ceiling = kw_ac * (kw_dc + ADDED_KW_DC) / kw_dc
    scaled_deltas, scaled_clipped, scaled_hours = increment_per_interval(
        d, pv, hsums, kw_dc, ADDED_KW_DC, ceiling_kwh=scaled_ceiling)
    scaled = price(scaled_deltas)
    fixed_deltas, fixed_clipped, fixed_hours = increment_per_interval(
        d, pv, hsums, kw_dc, ADDED_KW_DC, ceiling_kwh=kw_ac)
    fixed = price(fixed_deltas)
    cases = {
        "scaled_ceiling": {
            "assumption": ("the AC ceiling scales with the added capacity (each added "
                           "module brings its own microinverter, so the array's DC/AC "
                           "ratio is unchanged)"),
            "ceiling_kw_ac": round(scaled_ceiling, 3),
            "hours_clipped": scaled_hours,
            "clipped_kwh": round(scaled_clipped, 1),
            "added_production_kwh": scaled["added_production_kwh"],
            "bill_delta_usd_yr": scaled["bill_delta_usd_yr"],
            "import_offset_pct": scaled["import_offset_pct"],
        },
        "fixed_ceiling": {
            "assumption": ("the AC ceiling stays at today's nameplate, solar.kw_ac (a "
                           "string inverter, or modules added under existing inverters); "
                           "scaled hourly energy above it is clipped"),
            "ceiling_kw_ac": kw_ac,
            "hours_clipped": fixed_hours,
            "clipped_kwh": round(fixed_clipped, 1),
            "added_production_kwh": fixed["added_production_kwh"],
            "bill_delta_usd_yr": fixed["bill_delta_usd_yr"],
            "import_offset_pct": fixed["import_offset_pct"],
        },
    }
    primary_case = primary_clipping_case(arch)
    primary = dict(scaled if primary_case == "scaled_ceiling" else fixed)
    primary["added_kw_dc"] = ADDED_KW_DC
    primary["clipping_case"] = primary_case

    delta = primary["bill_delta_usd_yr"]
    out = {
        "issue": 190,
        "window": {
            "start": str(dates[0]), "end": str(dates[-1]), "days": len(dates),
            "days_with_increment": len(dates) - len(excluded),
            "excluded_dst_days": [str(x) for x in sorted(excluded)],
            "excluded_dst_days_reason": (
                "the flat 24-hour SAM grid and the wall-clock meter do not describe "
                "the same hours on a DST transition Sunday, so no hourly production "
                "can be derived for them and they carry no increment; the "
                "production_profile block bounds the understatement"),
        },
        "array": {
            "kw_dc": kw_dc, "kw_ac": kw_ac,
            "dc_ac_ratio": round(kw_dc / kw_ac, 4),
            "module_count": arch["module_count"],
            "inverter_model": arch["inverter_model"],
            "inverter_count": arch["inverter_count"],
            "nem_version": nem_version,
            "source": "private/household.yaml:solar.kw_dc, solar.kw_ac, solar.module_count, "
                      "solar.inverter_model, solar.inverter_count, household.nem_version "
                      "(all public-ok intake fields)",
        },
        "production_profile": {
            "source": ("derived hourly: max(SAM 8760 gross load - meter import + meter "
                       "export, 0), the identity threeway_production_validation.py's "
                       "meter_derived and service_headroom.py's derive_pv() use; SAM "
                       "grid from private/1-raw-data/enphase_sam8760_*.csv, meter from "
                       "the raw Green Button 15-minute export"),
            "hours": len(pv),
            "max_hour_kwh": round(max_hour, 3),
            "hours_above_ac_nameplate": sum(1 for v in pv.values() if v > kw_ac),
            "dark_hour_noise_clipped_kwh": round(clipped_neg, 1),
            "tie_out_threeway_meter_derived": threeway,
            "reconciliation_enphase_meter_record": recon,
        },
        "per_added_kw": primary,
        "clipping": {
            "primary_case": primary_case,
            "per_module_inverters": arch["per_module_inverters"],
            "rule": arch["rule"],
            "rule_inputs": {"solar.inverter_count": arch["inverter_count"],
                            "solar.module_count": arch["module_count"]},
            "resolution_caveat": ("both cases clip hourly energy, so a sub-hourly peak "
                                  "above the hourly mean is not visible and each case is "
                                  "a lower bound on its own clipping loss"),
            "scaled_max_hour_kwh": round(max_hour * (kw_dc + ADDED_KW_DC) / kw_dc, 3),
            "scaled_ceiling": cases["scaled_ceiling"],
            "fixed_ceiling": cases["fixed_ceiling"],
        },
        "payback_sensitivity": {
            "assumption": ("retrofit dollars per DC watt is ASSUMED at each rung, not a "
                           "quote and not collected by this repo; read the years off "
                           "the rung nearest a real quote"),
            "annual_value_usd": delta,
            "ladder": payback_ladder(delta, ADDED_KW_DC),
        },
        "verdict_check": verdict_check(delta, kw_dc, ADDED_KW_DC),
        "notes": {
            "engine": "rates.bill_nem_monthly (monthly per-period NEM 2.0 netting, NBC "
                      "on gross imports, BSC per day), the engine behind every other "
                      "published battery and behaviour figure",
            "rates": "constant current rates (rates.py, bill-derived); the historical "
                     "bills were partly on older tariffs, so this is a modeled delta, "
                     "not a bill forecast",
            "confidence": "modeled",
            "method": "interval-level counterfactual: the increment is placed into each "
                      "real 15-minute interval (import offset first, export after) and "
                      "the whole year is re-billed; the value is the bill delta, never a "
                      "rate times a quantity",
            "intra_hour_shape": "the SAM grid is hourly; each hour's increment is spread "
                                "evenly over its quarter-hours",
            "degradation": "the added kW produces at the measured array's own specific "
                           "yield in the measured year; no first-year uplift and no "
                           "degradation adjustment is applied, because nothing "
                           "committed measures either",
            "export_crediting": "an added kWh that leaves the meter is credited the way "
                                "the bills credit it: it cancels a same-period import "
                                "inside the month at rates.energy() while the bucket "
                                "stays net-positive, and settles at rates.credit() only "
                                "once the bucket runs to surplus; the decomposition "
                                "reports which happened",
            "grandfathering": "the value applies to an expansion inside the NEM 2.0 "
                              "growth cap; exceeding the cap forfeits the grandfathering "
                              "bracket in verdict_check, which this value is compared "
                              "against and does not approach",
        },
    }
    return out


def write(out):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    tmp.replace(OUT)


def main():
    out = build()
    write(out)
    per = out["per_added_kw"]
    print(f"wrote data/marginal_capacity_value.json")
    print(f"one more kW DC: +{per['added_production_kwh']} kWh/yr, "
          f"{per['import_offset_pct']}% displaces an import, "
          f"{per['exported_pct']}% exports; bill delta ${per['bill_delta_usd_yr']}/yr "
          f"({per['value_per_added_kwh_cents']} cents/kWh) at constant current rates")
    dec = per["settlement_decomposition"]
    print(f"settlement: {dec['netted_kwh']} kWh netted at energy(), "
          f"{dec['surplus_kwh']} kWh surplus at credit(), "
          f"${dec['nbc_saved_on_offset_imports_usd']} NBC saved; "
          f"buckets pushed net-negative: {dec['buckets_pushed_net_negative'] or 'none'}")
    cl = out["clipping"]
    print(f"clipping: primary case {cl['primary_case']} (per-module inverters: "
          f"{cl['per_module_inverters']})")
    for name in ("scaled_ceiling", "fixed_ceiling"):
        c = cl[name]
        print(f"  {name}: ceiling {c['ceiling_kw_ac']} kW AC, {c['clipped_kwh']} kWh "
              f"clipped in {c['hours_clipped']} hours, bill delta ${c['bill_delta_usd_yr']}/yr")
    vc = out["verdict_check"]
    print(f"verdict check: cap {vc['nem2_growth_cap_kw']} kW, grandfathering at risk is "
          f"{vc['grandfathering_over_added_kw_value']['low']}-"
          f"{vc['grandfathering_over_added_kw_value']['high']}x the added kW's value")


if __name__ == "__main__":
    main()
