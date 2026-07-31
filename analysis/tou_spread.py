#!/usr/bin/env python3
"""Is the on-peak to super-off-peak spread widening? (issue #4)

A battery is a spread trade: it buys at super-off-peak and sells at on-peak, so
its value tracks the GAP between those two prices, not the average price level.
`battery_dispatch_policies.escalation()` escalates the whole year-one saving at
one uniform rate (3/5/8/12%), which silently assumes the gap grows at the same
speed as the level. This module tests that assumption against the bill corpus
and re-runs the payback on the measured spread instead.

THREE THINGS THIS MODULE REFUSES TO DO, each because doing it produces a
confident wrong number:

1. IT NEVER TREATS A SETTLEMENT ZERO AS A PRICE. 58 of the 216 rows in
   data/bill_tou_detail.csv carry rate_per_kwh 0.00000, and every one of them
   has kwh <= 0: under NEM 2.0 an export cell defers settlement to the annual
   true-up, so the statement prints no unit price. Those rows are observations
   of a billing convention, not of a tariff. Fitting a trend through them drags
   every slope toward zero and reports it as evidence of a flat spread. They are
   excluded, and _priced_rows() ASSERTS the kwh <= 0 correlation rather than
   assuming it: a future corpus with a genuine 0.00000 price on positive kwh
   must fail loudly here instead of being dropped as if it were an export.

2. IT NEVER ESCALATES GENERATION. Issue #3 established, and rates_history.py now
   enforces at the type level, that on a CCA date there is no charged generation
   tariff in this corpus at all -- the printed generation table is SDG&E's
   bundled comparison, off the actual CEA charge by -6.8% to +104.3%. The
   provider broke on 2024-12-27, so 547 of the corpus's 763 days (72%) have no
   charged generation price. Delivery is the charged tariff in BOTH eras, so
   delivery escalation is measurable and generation escalation is not. Splicing
   a charged tariff to a comparison figure and fitting the join would be a
   fabricated trend wearing a slope's clothing.

3. IT NEVER REPORTS A CELL THIN ON EVIDENCE AS MEASURED. The adequacy test below
   was fixed before any fit was run and is not tuned to the answer.

THE ADEQUACY TEST, STATED BEFORE THE RESULT (issue #4 AC-3)
    A cell's escalation is reportable only if it is backed by >= MIN_VINTAGES
    distinct non-zero rates spanning >= MIN_SPAN_DAYS.
    A season's spread trend is reportable only if BOTH its cells qualify AND the
    fitted slope's 95% interval excludes zero AND the trend survives the
    structural-break check below AND the post-break slope still excludes zero
    once its interval is widened for the fact that the breakpoint was CHOSEN
    from the data (see _selection_adjusted_ci).
    Anything failing any bar is published as "not determined" together with
    the data that would settle it. "Not determined" is an acceptable outcome
    (CLAUDE.md 0) and is not a failure of the analysis.

WHY A STRUCTURAL-BREAK CHECK EXISTS AT ALL
    The bars above are about evidence DENSITY. They say nothing about SHAPE, and
    on this corpus that gap is not academic: the super-off-peak delivery rate
    does not decay, it steps once. It held 0.04013 through late 2024, dropped to
    0.02255 in a single tariff redesign in early 2025, and has been flat-to-
    rising since (0.02164 -> 0.02606). Fitted as an exponential across the whole
    window that one step reads as -21.4%/yr, clears every density bar, and --
    propagated fifteen years into a battery payback -- turns a single historical
    redesign into a permanent forecast of collapsing off-peak prices.

    So every series is ALSO fitted on the window after its largest single step,
    and a trend is reportable only if both fits agree in sign and the post-break
    fit is itself adequate. This bar can only ever suppress a claim, never create
    one, which is why adding it after seeing the full-window result is not tuning
    the test toward a preferred answer.

    THE BREAK CHECK HAS ITS OWN COST, AND IT IS PAID HERE
    Refitting after a break CHOSEN by looking at the data is a selection
    procedure, so the textbook interval on the tail no longer has 95% coverage.
    On this corpus that is not a rounding detail: winter's post-break slope is
    +10.95%/yr with an unadjusted interval of [1.89, 20.81]%, and [-6.90, 32.22]%
    once the choice among 4 candidate steps is paid for. The adjusted interval
    includes zero, so winter is published as NOT DETERMINED rather than as a
    measured widening. Four independent rate changes are simply not enough to
    locate a break and estimate a slope from the same data.

Writes data/tou_spread.json atomically: a partial or failed run changes nothing.
"""
import csv
import datetime as dt
import json
import math
import os
import pathlib
import statistics
import sys
import tempfile

# ---------------------------------------------------------------- the test ---
MIN_VINTAGES = 3          # distinct non-zero rates behind a cell
MIN_SPAN_DAYS = 365       # observations must span at least one season cycle
CONFIDENCE = 0.95

# Battery inputs, mirrored from battery_dispatch_policies.escalation() so the
# comparison is like-for-like. Asserted against the artifact at run time.
BATT_COST = 14500
BATT_FADE = 0.01
BATT_DISC = 0.05
UNIFORM_LADDER = (0.03, 0.05, 0.08, 0.12)
HORIZON_YR = 15
NPV_YR = 10

# Two-sided 95% Student-t critical values by degrees of freedom. scipy is not a
# dependency of this repo (requirements.txt is pandas/numpy/pyyaml/pdfplumber),
# so the table is inlined rather than adding one for a single quantile.
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}


def _t_crit(df):
    """Two-sided 95% t critical value; the normal limit for large df."""
    if df <= 0:
        return None
    return _T95.get(df, 1.960)


def _t_pdf(t, df):
    return (math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2))
            / math.sqrt(df * math.pi) * (1 + t * t / df) ** (-(df + 1) / 2))


def _t_quantile(p, df, _n=2048):
    """Two-sided-usable Student-t quantile for p > 0.5, by Simpson quadrature of
    the pdf plus bisection.

    scipy is not a dependency of this repo, and _T95 only covers the 95% column.
    A selection adjustment needs arbitrary levels (alpha/K), so the quantile is
    computed rather than looked up. Pure arithmetic with a fixed node count, so
    it is deterministic and the artifact stays byte-identical across runs.
    """
    def cdf(t):
        if t <= 0:
            return 0.5
        h = t / _n
        s = _t_pdf(0, df) + _t_pdf(t, df)
        for i in range(1, _n):
            s += _t_pdf(i * h, df) * (4 if i % 2 else 2)
        return 0.5 + s * h / 3

    lo, hi = 0.0, 1.0
    while cdf(hi) < p and hi < 1e6:
        hi *= 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _selection_adjusted_ci(slope, se, df, k_candidates):
    """Bonferroni-widened interval for a slope fitted on a DATA-SELECTED window.

    _dominant_break picks the largest observed step and the trend is then refit
    on the tail after it. Applying a textbook OLS interval to that tail ignores
    that the split point was chosen by looking at the same data, so the stated
    95% coverage is not the real coverage. Correcting over the k steps that
    could have been chosen is the conservative end of the valid range; a
    sup-Wald critical value would be tighter. Conservative is the right
    direction here for the same reason the break check itself is: it can only
    ever suppress a claim, never manufacture one.
    """
    if se is None or df <= 0 or k_candidates < 1:
        return None
    t = _t_quantile(1 - (1 - CONFIDENCE) / k_candidates / 2, df)
    return [slope - t * se, slope + t * se]


def _repo_root():
    """Walk up for the repo root, so the script runs from any working directory
    (the private/verify sandbox pattern in CLAUDE.md relies on this)."""
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / "data").is_dir() and (parent / "analysis").is_dir():
            return parent
    raise SystemExit("tou_spread.py: could not locate the repo root")


ROOT = _repo_root()
DETAIL = ROOT / "data" / "bill_tou_detail.csv"
OUT = ROOT / "data" / "tou_spread.json"

# The provider break. Delivery is the charged tariff on both sides of it;
# generation is charged only before it. See rates_history.py.
CCA_START = dt.date(2024, 12, 27)


def _parse_period(text):
    """'5/25/24 - 6/25/24' -> (date, date). Raises on anything else: a silently
    unparsed period would drop an observation and shrink the evidence base
    without saying so."""
    try:
        a, b = [p.strip() for p in text.split("-")]
        fmt = "%m/%d/%y"
        return dt.datetime.strptime(a, fmt).date(), dt.datetime.strptime(b, fmt).date()
    except Exception as exc:
        raise SystemExit(f"tou_spread.py: unparseable period {text!r}: {exc}")


def _corpus_bounds():
    """First and last day the bill corpus covers, from every period in the file.

    Read from ALL rows, not just priced ones: a period whose cells are all
    settlement zeros still covers those days, and excluding it would understate
    coverage in exactly the direction CLAUDE.md 1 warns about.
    """
    rows = list(csv.DictReader(DETAIL.open()))
    if not rows:
        raise SystemExit(f"tou_spread.py: {DETAIL} is empty")
    bounds = [_parse_period(r["period"]) for r in rows]
    return min(b[0] for b in bounds), max(b[1] for b in bounds)


def _priced_rows():
    """Every row that states an actual unit price, with its observation date.

    Segments split a statement at a mid-cycle rate change. Segment 0 starts at
    the period start; segment 1 follows it. Each observation is dated at the
    MIDPOINT of its own segment, so a rate that ran seven days is not given the
    same weight of placement as one that ran twenty-five.
    """
    if not DETAIL.exists():
        raise SystemExit(f"tou_spread.py: missing {DETAIL} -- run parse_bills.py first")
    rows = list(csv.DictReader(DETAIL.open()))
    if not rows:
        raise SystemExit(f"tou_spread.py: {DETAIL} is empty")

    priced, dropped = [], 0
    for r in rows:
        rate = float(r["rate_per_kwh"])
        kwh = float(r["kwh"])
        if rate == 0.0:
            # Guard, not an assumption: a zero here is only legitimate as a NEM
            # export settlement. A zero on positive kwh would be a genuine zero
            # price -- or a parser fault -- and must not be quietly discarded.
            if kwh > 0:
                raise SystemExit(
                    "tou_spread.py: rate 0.00000 on POSITIVE kwh "
                    f"({r['statement_date']} {r['section']} {r['season']} "
                    f"{r['tou_period']}, {kwh} kWh). That is not a settlement "
                    "zero. Refusing to guess whether it is a real zero price or "
                    "a parse fault.")
            dropped += 1
            continue

        start, end = _parse_period(r["period"])
        seg = int(r["segment"])
        seg_days = int(r["segment_days"])
        # Segment 0 occupies the first seg_days of the period; segment 1 the rest.
        seg_start = start if seg == 0 else start + dt.timedelta(
            days=sum(int(x["segment_days"]) for x in rows
                     if x["statement_date"] == r["statement_date"]
                     and x["period"] == r["period"]
                     and x["section"] == r["section"]
                     and x["season"] == r["season"]
                     and x["tou_period"] == r["tou_period"]
                     and int(x["segment"]) < seg))
        mid = seg_start + dt.timedelta(days=max(seg_days, 1) / 2)
        priced.append({
            "date": mid if isinstance(mid, dt.date) else seg_start,
            "section": r["section"],
            "season": r["season"],
            "tou": r["tou_period"],
            "rate": rate,
            "kwh": kwh,
            "statement": r["statement_date"],
            "period": r["period"],
        })

    if dropped == 0:
        raise SystemExit(
            "tou_spread.py: no settlement zeros found. This corpus has always "
            "had them (58 of 216 rows); their disappearance means the input "
            "changed shape and the exclusion rule needs rechecking.")
    return priced, dropped


def _ols(xs, ys):
    """Ordinary least squares with a 95% interval on the slope.

    Returns None when the fit is not defined (fewer than three points, or no
    spread in x) rather than returning a slope with an infinite interval.
    """
    n = len(xs)
    if n < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    intercept = my - slope * mx
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    df = n - 2
    if df <= 0:
        return None
    se = math.sqrt(sum(r * r for r in resid) / df / sxx)
    t = _t_crit(df)
    lo, hi = slope - t * se, slope + t * se
    sst = sum((y - my) ** 2 for y in ys)
    r2 = 1 - sum(r * r for r in resid) / sst if sst > 0 else None
    return {"slope": slope, "intercept": intercept, "se": se, "n": n, "df": df,
            "ci95": [lo, hi], "excludes_zero": (lo > 0) or (hi < 0),
            "r2": r2}


def _years_from(dates, origin):
    return [(d - origin).days / 365.25 for d in dates]


def _independent_units(pairs):
    """One (date, value) per distinct consecutive level, dated at its first print.

    A tariff reprinted unchanged on next month's statement is not a second
    observation of the price. Fitting every print treats one rate change as
    several independent draws, which shrinks the standard error without adding
    any evidence, and the shrinkage is large here: winter's post-break window
    prints 15 statement segments carrying only 4 distinct spreads.

    The adequacy gate already counts DISTINCT VINTAGES as the unit of evidence.
    Inference is fitted on the same unit, so the two cannot disagree about what
    counts as an observation. `pairs` must be in date order.
    """
    out = []
    for d, v in pairs:
        if not out or v != out[-1][1]:
            out.append((d, v))
    return out


def _cell_escalation(obs, origin):
    """Annualised escalation of one season x TOU cell, with its own adequacy verdict.

    Fitted on ln(rate), so the slope is a continuous growth rate and
    exp(slope) - 1 is the annual percentage.
    """
    vintages = sorted({o["rate"] for o in obs})
    dates = [o["date"] for o in obs]
    span = (max(dates) - min(dates)).days if dates else 0
    adequate = len(vintages) >= MIN_VINTAGES and span >= MIN_SPAN_DAYS

    units = _independent_units([(o["date"], o["rate"]) for o in obs])
    fit = _ols(_years_from([d for d, _ in units], origin),
               [math.log(v) for _, v in units])
    out = {
        "observations": len(obs),
        "independent_units": len(units),
        "distinct_vintages": len(vintages),
        "span_days": span,
        "first": min(dates).isoformat() if dates else None,
        "last": max(dates).isoformat() if dates else None,
        "rate_first": obs[0]["rate"] if obs else None,
        "rate_last": obs[-1]["rate"] if obs else None,
        "adequate": adequate,
        "label": "measured" if adequate else "estimated",
    }
    if fit is None:
        out["escalation_pct_yr"] = None
        out["verdict"] = "not determined -- fit undefined"
        return out
    out["escalation_pct_yr"] = round(100 * (math.exp(fit["slope"]) - 1), 2)
    out["escalation_ci95_pct_yr"] = [
        round(100 * (math.exp(fit["ci95"][0]) - 1), 2),
        round(100 * (math.exp(fit["ci95"][1]) - 1), 2)]
    out["r2"] = round(fit["r2"], 3) if fit["r2"] is not None else None
    out["fit_df"] = fit["df"]
    out["verdict"] = ("measured" if adequate else
                      f"estimated -- {len(vintages)} vintage(s) over {span} d, "
                      f"below the {MIN_VINTAGES}/{MIN_SPAN_DAYS}d bar")
    return out


def _spread_series(priced, season, origin):
    """On-peak minus super-off-peak DELIVERY price, per observation date.

    Paired on the date both cells were in force. Delivery only: see the module
    docstring on why generation cannot join this series.
    """
    def by_date(tou):
        d = {}
        for o in priced:
            if (o["section"] == "delivery" and o["season"] == season
                    and o["tou"] == tou):
                d.setdefault(o["date"], o["rate"])
        return d

    on, sop = by_date("on_peak"), by_date("super_off_peak")
    dates = sorted(set(on) & set(sop))
    return [(d, on[d] - sop[d]) for d in dates]


def _dominant_break(series):
    """The date of the largest single step between consecutive DISTINCT levels.

    Returns (date, jump, relative_jump) or None when the series never steps.
    'Largest' is measured in absolute price movement; the relative size is
    reported alongside so a reader can see whether the step dominates the range.
    """
    levels = []
    for d, v in series:
        if not levels or v != levels[-1][1]:
            levels.append((d, v))
    if len(levels) < 2:
        return None
    best = None
    for (_, prev), (d, cur) in zip(levels, levels[1:]):
        jump = abs(cur - prev)
        if best is None or jump > best[1]:
            best = (d, jump, jump / prev if prev else float("inf"))
    return best


def _fit_spread(series, origin):
    if len(series) < 3:
        return {"n": len(series),
                "verdict": "not determined -- fewer than 3 paired observations"}
    dates = [d for d, _ in series]
    vals = [v for _, v in series]
    units = _independent_units(series)
    udates = [d for d, _ in units]
    uvals = [v for _, v in units]
    xs = _years_from(udates, origin)
    fit = _ols(xs, uvals)
    # Escalation is fitted on ln(spread), like _cell_escalation, because
    # _payback COMPOUNDS the rate it is given. A slope in $/kWh/yr divided by
    # the mean is an additive growth rate; compounding it geometrically projects
    # a curve that was never fitted (worth ~$2.1k of 10-yr NPV on this corpus).
    # The dollar slope is kept as a descriptive figure only.
    gfit = _ols(xs, [math.log(v) for v in uvals])
    if fit is None or gfit is None:
        return {"n": len(series), "n_independent": len(units),
                "verdict": "not determined -- fit undefined on "
                           f"{len(units)} independent level(s)"}
    pct = lambda g: round(100 * (math.exp(g) - 1), 2)
    return {
        "n": len(series),
        "n_independent": fit["n"],
        "fit_df": fit["df"],
        "first": dates[0].isoformat(),
        "last": dates[-1].isoformat(),
        "span_days": (dates[-1] - dates[0]).days,
        "spread_first_usd_kwh": round(vals[0], 5),
        "spread_last_usd_kwh": round(vals[-1], 5),
        "slope_usd_kwh_per_yr": round(fit["slope"], 5),
        "slope_ci95": [round(fit["ci95"][0], 5), round(fit["ci95"][1], 5)],
        "excludes_zero": gfit["excludes_zero"],
        "r2": round(gfit["r2"], 3) if gfit["r2"] is not None else None,
        "mean_spread_usd_kwh": round(statistics.fmean(vals), 5),
        "mean_fitted_usd_kwh": round(statistics.fmean(uvals), 5),
        "escalation_pct_yr": pct(gfit["slope"]),
        "escalation_ci95_pct_yr": [pct(gfit["ci95"][0]), pct(gfit["ci95"][1])],
        "_g_slope": gfit["slope"], "_g_se": gfit["se"], "_g_df": gfit["df"],
    }


def _payback_delta(mine, theirs):
    """Difference in payback years, or None when either side never pays back.

    A narrowing spread can leave the battery unrecovered inside HORIZON_YR, and
    _payback then reports payback_yr None. That is a real result -- arguably the
    most decision-relevant one -- so it must publish as a null delta rather than
    crash the generator on the verdict it most needs to be able to state.
    """
    if mine is None or theirs is None:
        return None
    return round(mine - theirs, 1)


def _payback(save1, esc, cost=BATT_COST, fade=BATT_FADE, disc=BATT_DISC):
    """Payback year and 10-year NPV at a constant escalation -- the same
    arithmetic as battery_dispatch_policies.escalation(), reproduced so the
    per-period run is compared against the uniform run on identical mechanics
    rather than against a remembered number."""
    cum, pay, npv = 0.0, None, -float(cost)
    for y in range(1, HORIZON_YR + 1):
        sv = save1 * ((1 + esc) ** (y - 1)) * ((1 - fade) ** (y - 1))
        cum += sv
        if pay is None and cum >= cost:
            pay = y - 1 + (cost - (cum - sv)) / sv
        if y <= NPV_YR:
            npv += sv / ((1 + disc) ** y)
    return {"payback_yr": round(pay, 1) if pay is not None else None,
            "npv10": round(npv)}


def _battery_seed():
    """The year-one battery saving the uniform ladder is built on, read from the
    committed dispatch artifact rather than restated here -- restating it is how
    the retired extra_results.json came to publish a different payback than the
    live engine for the same house."""
    path = ROOT / "data" / "battery_dispatch_policies.json"
    if not path.exists():
        raise SystemExit(f"tou_spread.py: missing {path}")
    d = json.loads(path.read_text())
    try:
        seed = d["post_behavior"]["mid"]["battery_marginal"]
        published = d["escalation_greedy_pw3_post_behavior"]
    except KeyError as exc:
        raise SystemExit(f"tou_spread.py: dispatch artifact missing {exc}")
    # Reproduce the published ladder from the seed. If this fails the two models
    # have drifted and any delta computed against the ladder would be meaningless.
    for esc in UNIFORM_LADDER:
        key = f"{int(esc * 100)}%"
        got = _payback(seed, esc)
        want = published[key]
        if got["payback_yr"] != want["payback"] or got["npv10"] != want["npv10"]:
            raise SystemExit(
                "tou_spread.py: cannot reproduce the published uniform ladder at "
                f"{key} from seed {seed} -- got {got}, artifact says {want}. The "
                "escalation mechanics have drifted; fix that before comparing.")
    return seed, published


def build():
    priced, dropped = _priced_rows()
    origin = min(o["date"] for o in priced)

    # ---- per-cell escalation, delivery only ------------------------------
    cells = {}
    for season in ("summer", "winter"):
        for tou in ("on_peak", "off_peak", "super_off_peak"):
            obs = sorted((o for o in priced
                          if o["section"] == "delivery"
                          and o["season"] == season and o["tou"] == tou),
                         key=lambda o: o["date"])
            if not obs:
                cells[f"{season}_{tou}"] = {
                    "observations": 0, "distinct_vintages": 0,
                    "adequate": False, "label": "estimated",
                    "verdict": "not determined -- no priced observation"}
                continue
            cells[f"{season}_{tou}"] = _cell_escalation(obs, origin)

    # ---- spread series ---------------------------------------------------
    spreads = {}
    for season in ("summer", "winter"):
        series = _spread_series(priced, season, origin)
        fit = _fit_spread(series, origin)
        on_ok = cells[f"{season}_on_peak"]["adequate"]
        sop_ok = cells[f"{season}_super_off_peak"]["adequate"]

        # Shape check: refit on everything after the largest single step.
        brk = _dominant_break(series)
        post = None
        if brk is not None:
            brk_date, jump, rel = brk
            tail = [(d, v) for d, v in series if d >= brk_date]
            post = _fit_spread(tail, origin) if len(tail) >= 3 else {
                "n": len(tail),
                "verdict": "not determined -- fewer than 3 observations after the break"}
            post["break_date"] = brk_date.isoformat()
            post["break_jump_usd_kwh"] = round(jump, 5)
            post["break_jump_pct_of_prior_level"] = round(100 * rel, 1)
            post["distinct_levels"] = len({v for _, v in tail})
            # The break was CHOSEN by looking at the data, so the tail's own
            # interval overstates its coverage. Re-test against an interval
            # widened over every step that could have been chosen instead.
            k = max(1, len(_independent_units(series)) - 1)
            adj = _selection_adjusted_ci(
                post.get("_g_slope"), post.get("_g_se"), post.get("_g_df", 0), k)
            post["candidate_breakpoints"] = k
            post["selection_adjusted"] = adj is not None
            if adj is not None:
                pct = lambda g: round(100 * (math.exp(g) - 1), 2)
                post["escalation_ci95_pct_yr_selection_adjusted"] = [pct(adj[0]), pct(adj[1])]
                post["excludes_zero_selection_adjusted"] = bool(adj[0] > 0 or adj[1] < 0)
            post["adequate"] = bool(
                post.get("n", 0) >= MIN_VINTAGES
                and post.get("span_days", 0) >= MIN_SPAN_DAYS
                and post.get("excludes_zero", False)
                and post.get("excludes_zero_selection_adjusted", False)
                and post["distinct_levels"] >= MIN_VINTAGES)
        for scratch in (fit, post):
            if isinstance(scratch, dict):
                for k_ in ("_g_slope", "_g_se", "_g_df"):
                    scratch.pop(k_, None)
        fit["post_break"] = post

        # Sign is compared on escalation_pct_yr, i.e. on the LOG fit, because that
        # is the scale significance and the battery escalation are taken from.
        # The raw-dollar slope is descriptive only, and for a non-monotonic rate
        # history the two scales can disagree in sign -- which would let the gate
        # pass a trend whose published escalation reverses after the break.
        full_dir = fit.get("escalation_pct_yr", 0)
        post_dir = (post or {}).get("escalation_pct_yr", 0)
        agree = bool(
            post and post.get("adequate") and (post_dir > 0) == (full_dir > 0))
        reportable = bool(on_ok and sop_ok and fit.get("excludes_zero") and agree)

        fit["cells_adequate"] = {"on_peak": on_ok, "super_off_peak": sop_ok}
        fit["survives_structural_break"] = agree
        fit["reportable"] = reportable
        fit["verdict"] = (
            "widening" if reportable and full_dir > 0 else
            "narrowing" if reportable and full_dir < 0 else
            "not determined")
        if not reportable:
            why = []
            if not on_ok:
                why.append("on-peak cell below the vintage/span bar")
            if not sop_ok:
                why.append("super-off-peak cell below the vintage/span bar")
            if not fit.get("excludes_zero", False):
                why.append("full-window slope 95% interval includes zero")
            if (post and post.get("selection_adjusted")
                    and post.get("excludes_zero", False)
                    and not post.get("excludes_zero_selection_adjusted", False)):
                why.append(
                    "the post-break slope is significant on its own interval "
                    f"({post['escalation_ci95_pct_yr']}%/yr) but not once that "
                    "interval is widened for having CHOSEN the breakpoint from "
                    f"the data ({post['escalation_ci95_pct_yr_selection_adjusted']}"
                    f"%/yr over {post['candidate_breakpoints']} candidate steps). "
                    f"{post['distinct_levels']} independent rate changes cannot "
                    "support a trend claim once breakpoint selection is paid for")
            if not agree and not (post and post.get("selection_adjusted")
                                  and post.get("excludes_zero", False)):
                why.append(
                    "the full-window slope does not survive the structural-break "
                    "check: it is carried by a single step"
                    + (f" of ${post['break_jump_usd_kwh']}/kWh on "
                       f"{post['break_date']} ({post['break_jump_pct_of_prior_level']}% "
                       "of the prior level)" if post and "break_date" in post else "")
                    + ", not by an ongoing trend")
            fit["not_determined_because"] = why
            fit["would_settle_it"] = [
                "More rate CHANGES, not more statements: the interval is set by "
                f"the {(post or {}).get('distinct_levels', 'few')} distinct "
                "spreads in the window, and reprinting the same tariff adds "
                "none. At roughly two delivery redesigns a year, a corpus "
                "reaching into 2028 would roughly double the independent units.",
                "A breakpoint fixed in advance from published tariff effective "
                "dates rather than chosen as the largest observed step. That "
                "removes the selection penalty entirely and the unadjusted "
                "interval becomes legitimate.",
                "Statements from before 2024-05-25, which would extend the "
                "pre-break window and let the two segments be compared as a "
                "formal change-point model rather than a refit.",
            ]
        fit["series"] = [[d.isoformat(), round(v, 5)] for d, v in series]
        spreads[season] = fit

    # ---- battery payback on the measured spread --------------------------
    seed, published = _battery_seed()
    battery = {
        "seed_year1_saving_usd": seed,
        "seed_source": "battery_dispatch_policies.json post_behavior.mid.battery_marginal",
        "cost_usd": BATT_COST, "fade_per_yr": BATT_FADE, "discount": BATT_DISC,
        "uniform_ladder": {f"{int(e*100)}%": _payback(seed, e) for e in UNIFORM_LADDER},
        "per_period": {},
    }
    for season, fit in spreads.items():
        if not fit.get("reportable"):
            battery["per_period"][season] = {
                "verdict": "not determined",
                "because": fit.get("not_determined_because", ["spread not reportable"])}
            continue
        # The POST-BREAK slope, not the full-window one. The full window includes
        # the one-off tariff step, which inflates the winter slope by ~38%
        # (0.04119 -> 0.02978 $/kWh/yr); escalating a battery fifteen years on the
        # inflated figure would bank a redesign that has already happened as if it
        # recurred annually.
        post = fit["post_break"]
        esc = post["escalation_pct_yr"] / 100.0
        run = _payback(seed, esc)
        run["spread_escalation_pct_yr"] = post["escalation_pct_yr"]
        run["escalation_basis"] = (
            f"post-break window {post['first']}..{post['last']}, "
            f"{post['n']} observations, {post['distinct_levels']} distinct levels")
        run["full_window_escalation_pct_yr_NOT_USED"] = fit["escalation_pct_yr"]
        # A narrowing spread can leave the battery unpaid inside HORIZON_YR, in
        # which case _payback returns payback_yr None. That is a real result, not
        # an error, so the delta is published as null rather than crashing the
        # generator on the one verdict it most needs to be able to report.
        run["vs_uniform"] = {
            f"{int(e*100)}%": {
                "payback_delta_yr": _payback_delta(
                    run["payback_yr"],
                    battery["uniform_ladder"][f"{int(e*100)}%"]["payback_yr"]),
                "npv10_delta_usd": run["npv10"] - battery["uniform_ladder"][f"{int(e*100)}%"]["npv10"],
            } for e in UNIFORM_LADDER}
        run["applied_to"] = (
            f"the ENTIRE ${seed}/yr battery marginal, uniformly -- exactly as "
            "the 3/5/8/12% rungs apply their rate. That seed spans both seasons "
            "and includes generation, while the measured rate is WINTER DELIVERY "
            "spread only. Summer and generation are not determined, so this run "
            "escalates components this corpus cannot measure. It is a ladder rung "
            "whose RATE is measured, not a decomposed all-in forecast.")
        run["scenario_zero_escalation"] = _payback(seed, 0.0)
        run["basis"] = (
            "DELIVERY spread only. The generation half of the on-peak price "
            "cannot be escalated from this corpus, so this figure is what the "
            "delivery component alone implies -- not an all-in forecast. "
            "scenario_zero_escalation (nothing escalates) and this run "
            "(everything escalates at the measured winter delivery rate) are "
            "SENSITIVITY SCENARIOS, not bounds: the undetermined components can "
            "move either way, and faster than winter delivery in either "
            "direction, so neither endpoint constrains the true value. What is "
            "measured is that one component of the gap grew at this rate over "
            "the post-break window. Bounding the battery would need a dispatch "
            "re-run reporting savings per season x component, which "
            "battery_dispatch_policies.json does not currently expose.")
        battery["per_period"][season] = run

    # ---- what is not determined, and what would settle it ----------------
    # Derived from the parsed periods, never hard-coded: adding a statement must
    # move this evidence with the corpus rather than leave a frozen count that
    # reads as current (CLAUDE.md 1 -- coverage is counted in DAYS).
    corpus_first, corpus_last = _corpus_bounds()
    # INCLUSIVE of both endpoints, which is the convention the rest of this repo
    # counts coverage in (issue #2's engine covers 2024-05-25..2026-06-26 = 763
    # days). Mixing the two conventions is how the docstring and the artifact
    # came to disagree by one day about the same corpus.
    corpus_days = (corpus_last - corpus_first).days + 1
    cca_days = max(0, (corpus_last - max(CCA_START, corpus_first)).days + 1)
    not_determined = {
        "generation_escalation": {
            "verdict": "not determined",
            "reason": (
                "On a CCA date this corpus carries no charged generation tariff. "
                "The printed generation table is SDG&E's bundled comparison, off "
                "the actual CEA charge by -6.8% to +104.3% (issue #3). The "
                f"provider broke on {CCA_START.isoformat()}, leaving {cca_days} "
                f"of {corpus_days} corpus days "
                f"({corpus_first.isoformat()}..{corpus_last.isoformat()}) "
                "with no charged generation price."),
            "would_settle_it": [
                "The CCA's own per-TOU rates, which appear only on the CCA pages "
                "of the statements; parse_bills.py does not extract those pages.",
                "A second bundled-era stretch long enough to fit generation "
                f"separately -- the corpus has only {corpus_days - cca_days} "
                "bundled days.",
            ]},
        "all_in_spread": {
            "verdict": "not determined",
            "reason": ("The all-in on-peak price is delivery plus generation. "
                       "With generation undetermined past the provider break, an "
                       "all-in spread trend would splice a charged tariff to a "
                       f"comparison figure across {round(100 * cca_days / corpus_days)}% "
                       "of the corpus."),
            "would_settle_it": ["Same as generation_escalation above."]},
    }

    return {
        "window": {"first": origin.isoformat(),
                   "last": max(o["date"] for o in priced).isoformat()},
        "inputs": {
            "source": "data/bill_tou_detail.csv",
            "rows_total": len(priced) + dropped,
            "rows_priced": len(priced),
            "rows_excluded_settlement_zero": dropped,
            "exclusion_rule": (
                "rate_per_kwh == 0 with kwh <= 0 is a NEM export settlement "
                "deferral, not a price. Asserted, not assumed: a zero on "
                "positive kwh aborts the run."),
        },
        "adequacy_test": {
            "min_distinct_vintages": MIN_VINTAGES,
            "min_span_days": MIN_SPAN_DAYS,
            "spread_rule": (
                "both cells adequate AND the full-window 95% CI excludes zero "
                "AND the post-break refit agrees in sign and is itself adequate "
                "AND the post-break CI still excludes zero after being widened "
                "for having chosen the breakpoint from the data"),
            "inference_unit": (
                "distinct rate CHANGES, not statement prints: a tariff reprinted "
                "unchanged is not a second observation of the price"),
            "fitted_scale": (
                "ln(spread), so the rate that _payback compounds is the rate "
                "that was estimated"),
            "stated_before_fitting": True,
        },
        "delivery_cell_escalation": cells,
        "delivery_spread": spreads,
        "battery": battery,
        "not_determined": not_determined,
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
    print(f"wrote {OUT.relative_to(ROOT)}")
    for season, fit in result["delivery_spread"].items():
        # The post-break figure, matching what the battery run uses. Printing the
        # full-window number here while the payback used the post-break one is
        # exactly the two-figures-for-one-thing drift CLAUDE.md 3 warns about.
        esc = (fit.get("post_break") or {}).get("escalation_pct_yr")
        print(f"  {season}: {fit['verdict']}"
              + (f"  {esc}%/yr (post-break)" if fit.get("reportable") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
