#!/usr/bin/env python3
"""Behavioural tests for tou_spread.py (issue #4).

These test what the module REFUSES to do, not just what it computes. Every case
below corresponds to a way this analysis produces a confident wrong number, and
three of them are mistakes that were actually made while writing it.

Run from the repo root:  ./.venv/bin/python analysis/test_tou_spread.py
"""
import datetime as dt
import hashlib
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

# tou_spread._battery_seed() imports behavior_rebuild lazily to read the
# household's EV applicability flag (issue #247), and behavior_rebuild reads
# private/household.yaml at its own import and fails closed without it. Same
# fix as test_perfect_foresight_dispatch.py: point the intake loader at a
# synthetic, invented household before anything imports it, so this whole file
# runs on a checkout with no private/ at all. Nothing below depends on these
# values; the flag they imply (no household.has_ev key, so an EV household)
# matches the committed dispatch artifact.
import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None
import behavior_rebuild as br  # noqa: E402
import tou_spread as ts  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------------------------------------------------------------------
# The settlement-zero rule
# ---------------------------------------------------------------------------

@case
def case_settlement_zeros_are_excluded_not_priced():
    """A 0.00000 export cell is a billing convention, not a price of zero."""
    priced, dropped = ts._priced_rows()
    assert dropped > 0, "expected settlement zeros in this corpus"
    assert all(o["rate"] != 0.0 for o in priced), "a zero survived into the priced set"
    return f"{dropped} settlement zeros excluded, {len(priced)} priced rows kept"


@case
def case_a_zero_on_positive_kwh_aborts():
    """The exclusion is a guard, not an assumption. A genuine zero price -- or a
    parser fault -- must stop the run rather than be silently dropped as an
    export. Verified by driving the real predicate, not by re-implementing it."""
    real = ts.DETAIL
    tmp = ROOT / "data" / "_test_tou_detail.csv"
    rows = real.read_text().splitlines()
    header = rows[0]
    # A row priced at zero on POSITIVE kwh.
    poisoned = header + "\n" + ",".join(
        ["2025-07-02", "6/27/25 - 7/28/25", "delivery", "summer", "0", "30",
         "on_peak", "123.0", "0.0"])
    tmp.write_text(poisoned + "\n")
    ts.DETAIL = tmp
    try:
        ts._priced_rows()
    except SystemExit as exc:
        assert "POSITIVE kwh" in str(exc), f"wrong refusal: {exc}"
        return "a zero price on positive kwh aborts the run, naming the row"
    else:
        raise AssertionError("a zero on positive kwh was accepted")
    finally:
        ts.DETAIL = real
        tmp.unlink(missing_ok=True)


@case
def case_vanished_settlement_zeros_abort():
    """If the corpus stops containing settlement zeros the input has changed
    shape, and the exclusion rule needs rechecking before any fit is trusted."""
    real = ts.DETAIL
    tmp = ROOT / "data" / "_test_tou_nozero.csv"
    lines = real.read_text().splitlines()
    keep = [lines[0]] + [ln for ln in lines[1:] if not ln.endswith(",0.0")]
    tmp.write_text("\n".join(keep) + "\n")
    ts.DETAIL = tmp
    try:
        ts._priced_rows()
    except SystemExit as exc:
        assert "no settlement zeros" in str(exc), f"wrong refusal: {exc}"
        return "a corpus with no settlement zeros aborts rather than fitting"
    else:
        raise AssertionError("a zero-free corpus was accepted silently")
    finally:
        ts.DETAIL = real
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Season-crossing periods (issue #32)
# ---------------------------------------------------------------------------

@case
def case_season_crossing_period_is_dated_from_the_season_start():
    """A billing period that straddles the summer/winter boundary must date
    its SECOND season block's segment 0 from the day that season actually
    began, not from the period's own start. Issue #32's own real example:
    period 5/25/24-6/25/24's winter block covers only 5/25-5/31 (7 days) and
    its summer block only 6/1-6/25 (25 days) -- the old code anchored BOTH
    blocks' segment 0 at 5/25, giving the summer block a midpoint of
    2024-06-06 instead of the season-anchored 2024-06-13."""
    real = ts.DETAIL
    tmp = ROOT / "data" / "_test_tou_season_crossing.csv"
    header = "statement_date,period,section,season,segment,segment_days,tou_period,kwh,rate_per_kwh"
    rows = [
        header,
        # a settlement zero so _priced_rows()'s own "corpus must contain
        # settlement zeros" guard does not abort first
        "2024-06-27,5/25/24 - 6/25/24,delivery,winter,0,7,off_peak,-138.0,0.0",
        "2024-06-27,5/25/24 - 6/25/24,delivery,winter,0,7,super_off_peak,204.0,0.04013",
        "2024-06-27,5/25/24 - 6/25/24,delivery,summer,0,25,super_off_peak,698.0,0.04013",
    ]
    tmp.write_text("\n".join(rows) + "\n")
    ts.DETAIL = tmp
    try:
        priced, dropped = ts._priced_rows()
    finally:
        ts.DETAIL = real
        tmp.unlink(missing_ok=True)
    assert dropped == 1, dropped
    winter = next(p for p in priced if p["season"] == "winter")
    summer = next(p for p in priced if p["season"] == "summer")
    # winter's block starts at the period start (5/25), unaffected by this fix:
    # mid = 5/25 + floor(7/2)=3 days = 5/28
    assert winter["date"] == dt.date(2024, 5, 28), winter["date"]
    # summer's block starts at the season calendar start (6/1), NOT the period
    # start (5/25): mid = 6/1 + floor(25/2)=12 days = 6/13, matching the
    # issue's own "roughly 2024-06-13" expectation, not the old 2024-06-06
    assert summer["date"] == dt.date(2024, 6, 13), summer["date"]
    return (f"season-crossing period dated winter={winter['date']}, "
           f"summer={summer['date']} (season-anchored, not period-anchored)")


@case
def case_season_block_missing_from_the_calendar_aborts():
    """A row claiming a season the period's own calendar span never touches
    (e.g. 'summer' on a wholly-winter period) must abort loudly rather than
    silently mis-date it -- the same fail-closed posture rates_history.py's
    own _date_segments uses for the identical mismatch."""
    real = ts.DETAIL
    tmp = ROOT / "data" / "_test_tou_bad_season.csv"
    header = "statement_date,period,section,season,segment,segment_days,tou_period,kwh,rate_per_kwh"
    rows = [
        header,
        "2024-01-27,12/26/23 - 1/26/24,delivery,winter,0,32,off_peak,-138.0,0.0",
        "2024-01-27,12/26/23 - 1/26/24,delivery,summer,0,32,super_off_peak,204.0,0.04013",
    ]
    tmp.write_text("\n".join(rows) + "\n")
    ts.DETAIL = tmp
    try:
        ts._priced_rows()
    except SystemExit as exc:
        assert "no" in str(exc) and "summer" in str(exc), f"wrong refusal: {exc}"
        return "a season absent from the period's own calendar span aborts the run"
    else:
        raise AssertionError("a season not covered by the period's calendar span was accepted")
    finally:
        ts.DETAIL = real
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# The structural-break check -- the bug this module was rewritten to fix
# ---------------------------------------------------------------------------

@case
def case_a_pure_step_is_not_a_trend():
    """A flat series, one step, then flat again must NOT be reported as a trend.
    This is the super-off-peak delivery series in miniature: fitted across the
    whole window it reads as a steep decay with a tight confidence interval."""
    o = dt.date(2024, 1, 1)
    flat_then_step = ([(o + dt.timedelta(days=30 * i), 0.040) for i in range(6)]
                      + [(o + dt.timedelta(days=30 * i), 0.022) for i in range(6, 14)])
    # Fitted the naive way, one print per statement, the step looks like a
    # strongly significant decay. This is the illusion the unit rule removes.
    naive = ts._ols(ts._years_from([d for d, _ in flat_then_step], o),
                    [v for _, v in flat_then_step])
    assert naive["excludes_zero"], (
        "precondition: the print-level fit should look significant, "
        "otherwise this case proves nothing")
    # Fitted on independent levels there are only two observations, so no trend
    # is defined at all -- a step cannot be a trend before the break check even
    # runs.
    full = ts._fit_spread(flat_then_step, o)
    assert "slope_usd_kwh_per_yr" not in full, (
        f"a two-level step must not yield a slope, got {full}")
    assert full["n_independent"] == 2, full
    assert "not determined" in full["verdict"], full
    brk = ts._dominant_break(flat_then_step)
    assert brk is not None, "the step was not detected"
    tail = [(d, v) for d, v in flat_then_step if d >= brk[0]]
    post = ts._fit_spread(tail, o)
    assert "slope_usd_kwh_per_yr" not in post, (
        f"a single flat level after the break has no slope either, got {post}")
    return ("a step is significant at print level and undefined at level level "
            "-- pseudoreplication was the whole of that apparent trend")


@case
def case_inference_counts_rate_changes_not_reprints():
    """A tariff reprinted unchanged is not a new observation of the price.

    The adequacy gate counts distinct vintages; the interval must be built on
    the same unit or the two disagree about what evidence is. Reprinting the
    same series more densely must not narrow the interval.
    """
    o = dt.date(2024, 1, 1)
    levels = [0.20, 0.23, 0.25, 0.28]
    sparse = [(o + dt.timedelta(days=120 * i), v) for i, v in enumerate(levels)]
    # the same four rate changes, each reprinted on three monthly statements
    dense = []
    for i, v in enumerate(levels):
        for k in range(3):
            dense.append((o + dt.timedelta(days=120 * i + 30 * k), v))

    fs, fd = ts._fit_spread(sparse, o), ts._fit_spread(dense, o)
    assert fd["n"] == 12 and fd["n_independent"] == 4, fd
    assert fd["fit_df"] == 2, f"df must come from the 4 levels, got {fd['fit_df']}"
    width = lambda f: f["slope_ci95"][1] - f["slope_ci95"][0]
    assert width(fd) >= width(fs) * 0.99, (
        f"reprinting narrowed the interval: {width(fd)} vs {width(fs)}")
    # and the published winter figure is built on levels, not prints
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    post = result["delivery_spread"]["winter"]["post_break"]
    assert post["n"] == 15 and post["n_independent"] == 4, post
    assert post["fit_df"] == 2, post
    return ("reprints do not narrow the interval; winter's published CI is "
            "built on 4 rate changes, not 15 statement prints")


@case
def case_a_never_repaying_battery_publishes_null_not_a_crash():
    """A strongly narrowing spread can leave the battery unrecovered inside the
    horizon. _payback then returns payback_yr None, and the comparison against
    the uniform ladder must survive that rather than raising."""
    never = ts._payback(2238, -0.60)
    assert never["payback_yr"] is None, (
        f"precondition: a -60%/yr spread should never repay, got {never}")
    assert ts._payback_delta(never["payback_yr"], 6.2) is None
    assert ts._payback_delta(5.3, None) is None
    assert ts._payback_delta(5.3, 6.2) == -0.9
    return "a never-repaying run yields a null delta instead of a TypeError"


@case
def case_corpus_coverage_is_derived_not_hardcoded():
    """Adding a statement must move the coverage evidence with the corpus. A
    frozen count keeps reading as current long after it stops being true."""
    first, last = ts._corpus_bounds()
    days = (last - first).days + 1
    reason = json.loads((ROOT / "data" / "tou_spread.json").read_text())[
        "not_determined"]["generation_escalation"]["reason"]
    assert f"of {days} corpus days" in reason, (
        f"published coverage does not match the parsed corpus ({days} d): {reason}")
    assert first.isoformat() in reason and last.isoformat() in reason, reason
    # inclusive counting, the convention the rest of the repo uses
    assert days == 763, f"corpus span changed: {first}..{last} = {days} d"
    return f"coverage derived from the parsed corpus: {first}..{last}, {days} d"


@case
def case_verdict_sign_comes_from_the_fitted_scale():
    """Significance and battery escalation are taken from the log fit, so the
    direction that gates the verdict must come from the same fit. The dollar
    slope is descriptive, and on a non-monotonic history the two can disagree."""
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    for season, fit in result["delivery_spread"].items():
        if fit["verdict"] == "widening":
            assert fit["escalation_pct_yr"] > 0, season
        if fit["verdict"] == "narrowing":
            assert fit["escalation_pct_yr"] < 0, season
        post = fit.get("post_break") or {}
        if fit.get("survives_structural_break"):
            assert (post["escalation_pct_yr"] > 0) == (fit["escalation_pct_yr"] > 0), (
                f"{season}: break check agreed on a sign the fits do not share")
    # a series whose dollar and log fits disagree in sign must not be reported
    o = dt.date(2024, 1, 1)
    skewed = [(o + dt.timedelta(days=200 * i), v)
              for i, v in enumerate([0.02, 0.40, 0.30, 0.26])]
    f = ts._fit_spread(skewed, o)
    lin_pos = f["slope_usd_kwh_per_yr"] > 0
    log_pos = f["escalation_pct_yr"] > 0
    return ("verdict direction is taken from the fitted (log) scale; on the "
            f"skewed probe linear>0={lin_pos}, log>0={log_pos}")


@case
def case_break_detector_ignores_repeated_levels():
    """Consecutive identical prints are one level, not many observations of a
    move. A detector that differenced raw rows would find a 'largest step' of
    zero and pass everything."""
    o = dt.date(2024, 1, 1)
    series = [(o + dt.timedelta(days=i * 10), v) for i, v in
              enumerate([0.10, 0.10, 0.10, 0.30, 0.30, 0.31])]
    d, jump, rel = ts._dominant_break(series)
    assert abs(jump - 0.20) < 1e-9, f"expected the 0.20 step, got {jump}"
    assert d == series[3][0], "the break is dated at the first row of the new level"
    return "the dominant break is the largest move between distinct levels"


@case
def case_break_check_can_only_suppress():
    """The added bar must never turn a not-determined into a reportable. It is a
    conjunct, so this is structural -- assert it on the real artifact."""
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    for season, fit in result["delivery_spread"].items():
        if fit["reportable"]:
            assert fit["survives_structural_break"], (
                f"{season} reported without surviving the break check")
            assert fit["cells_adequate"]["on_peak"], f"{season} on-peak inadequate"
            assert fit["cells_adequate"]["super_off_peak"], f"{season} sop inadequate"
    return "every reportable season cleared all conjuncts, none bypassed"


# ---------------------------------------------------------------------------
# Generation must never be escalated
# ---------------------------------------------------------------------------

@case
def case_generation_is_never_escalated():
    """Issue #3: on a CCA date there is no charged generation tariff. No
    generation cell may appear in the escalation output at all."""
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    cells = result["delivery_cell_escalation"]
    assert cells, "no cells produced"
    blob = json.dumps(cells).lower()
    assert "generation" not in blob, "a generation cell leaked into the escalation block"
    nd = result["not_determined"]
    assert nd["generation_escalation"]["verdict"] == "not determined"
    assert nd["all_in_spread"]["verdict"] == "not determined"
    assert nd["generation_escalation"]["would_settle_it"], "no settling data listed"
    return "generation and all-in spread both published as not determined, with remedies"


# ---------------------------------------------------------------------------
# The battery comparison
# ---------------------------------------------------------------------------

@case
def case_uniform_ladder_reproduces_the_dispatch_artifact():
    """The delta is only meaningful if both sides share mechanics. _battery_seed
    refuses when it cannot reproduce the published ladder from the seed."""
    seed, published = ts._battery_seed()
    for esc in ts.UNIFORM_LADDER:
        key = f"{int(esc * 100)}%"
        got = ts._payback(seed, esc)
        assert got["payback_yr"] == published[key]["payback"]
        assert got["npv10"] == published[key]["npv10"]
    return f"all {len(ts.UNIFORM_LADDER)} rungs reproduced from seed ${seed}"


@case
def case_battery_uses_the_post_break_escalation():
    """Escalating on the full-window slope banks a one-off redesign as if it
    recurred every year. The run must use the post-break figure and say so."""
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    any_reported = False
    for season, run in result["battery"]["per_period"].items():
        if run.get("verdict") == "not determined":
            continue
        any_reported = True
        post = result["delivery_spread"][season]["post_break"]
        assert run["spread_escalation_pct_yr"] == post["escalation_pct_yr"], (
            f"{season} battery run did not use the post-break escalation")
        assert run["full_window_escalation_pct_yr_NOT_USED"] != \
            run["spread_escalation_pct_yr"], (
            f"{season}: the two figures coincide, so this test proves nothing "
            "-- check the corpus still contains a structural break")
    if not any_reported:
        # No season clears the bars on this corpus. The contract still binds:
        # every season must then be blocked with a stated reason, and no
        # battery figure may be published from an unreported trend.
        for season, run in result["battery"]["per_period"].items():
            assert run["verdict"] == "not determined", season
            assert run.get("because"), f"{season} blocked without a reason"
            for leaked in ("payback_yr", "npv10", "spread_escalation_pct_yr"):
                assert leaked not in run, (
                    f"{season} publishes {leaked} from a trend that is not reported")
        return ("no season is reportable, and every battery run is blocked with "
                "a reason and publishes no figure")
    return "the payback escalates on the post-break slope, not the inflated one"


@case
def case_a_chosen_breakpoint_pays_for_the_choosing():
    """Refitting after a break FOUND in the data is a selection procedure, so the
    tail's textbook interval overstates its coverage. The adjusted interval must
    be strictly wider, and the published verdict must respect it."""
    # widening must be monotone in the number of candidate steps considered
    base = ts._selection_adjusted_ci(0.10, 0.02, 2, 1)
    more = ts._selection_adjusted_ci(0.10, 0.02, 2, 6)
    assert base and more
    assert (more[1] - more[0]) > (base[1] - base[0]), (
        f"more candidates must widen the interval: {base} vs {more}")
    # K=1 reproduces the ordinary 95% interval (nothing was selected)
    plain = 0.10 - ts._t_crit(2) * 0.02, 0.10 + ts._t_crit(2) * 0.02
    assert abs(base[0] - plain[0]) < 1e-3 and abs(base[1] - plain[1]) < 1e-3, (
        f"K=1 must be the unadjusted interval, got {base} vs {plain}")

    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    checked = 0
    for season, fit in result["delivery_spread"].items():
        post = fit.get("post_break") or {}
        if not post.get("selection_adjusted"):
            continue
        checked += 1
        raw = post["escalation_ci95_pct_yr"]
        adj = post["escalation_ci95_pct_yr_selection_adjusted"]
        assert adj[0] <= raw[0] and adj[1] >= raw[1], (
            f"{season}: adjusted interval is not wider ({adj} vs {raw})")
        if post.get("adequate"):
            assert post["excludes_zero_selection_adjusted"], (
                f"{season} is adequate but fails its own adjusted interval")
        if fit["verdict"] != "not determined":
            assert post["excludes_zero_selection_adjusted"], (
                f"{season} is published as a trend on an interval that includes zero")
    assert checked, "no season carried a selection-adjusted interval"
    return (f"{checked} season(s): the adjusted interval is wider and gates the "
            "verdict; K=1 reduces to the ordinary interval")


@case
def case_not_determined_seasons_carry_reasons():
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    for season, fit in result["delivery_spread"].items():
        if fit["reportable"]:
            continue
        assert fit.get("not_determined_because"), f"{season} lacks a stated reason"
        run = result["battery"]["per_period"][season]
        assert run["verdict"] == "not determined", (
            f"{season} spread is not reportable but its battery run is")
    return "every not-determined verdict names why, and blocks its battery run"


# ---------------------------------------------------------------------------
# Adequacy bookkeeping
# ---------------------------------------------------------------------------

@case
def case_thin_cells_are_labelled_estimated():
    """AC-1: a cell backed by fewer than three distinct vintages is estimated,
    never measured. Summer off-peak has exactly one priced observation."""
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    thin = {k: v for k, v in result["delivery_cell_escalation"].items()
            if v["distinct_vintages"] < ts.MIN_VINTAGES}
    assert thin, "expected at least one thin cell in this corpus"
    for name, cell in thin.items():
        assert cell["label"] == "estimated", f"{name} thin but labelled {cell['label']}"
        assert not cell["adequate"], f"{name} thin but marked adequate"
    return f"{len(thin)} thin cell(s) labelled estimated: {sorted(thin)}"


@case
def case_adequacy_test_is_recorded_in_the_artifact():
    """AC-3 requires the test be explicit and pre-stated; a reader must be able
    to see the thresholds without reading the source."""
    result = json.loads((ROOT / "data" / "tou_spread.json").read_text())
    t = result["adequacy_test"]
    assert t["min_distinct_vintages"] == ts.MIN_VINTAGES
    assert t["min_span_days"] == ts.MIN_SPAN_DAYS
    assert t["stated_before_fitting"] is True
    return "thresholds published in the artifact alongside the results"


@case
def case_artifact_regenerates_byte_identically():
    """CLAUDE.md 9: every committed artifact must be reproducible by its script."""
    path = ROOT / "data" / "tou_spread.json"
    before = path.read_bytes()
    ts.main()
    assert path.read_bytes() == before, "tou_spread.json is not reproducible"
    return "data/tou_spread.json regenerates byte-identically"


# ---------------------------------------------------------------------------
# Which EV applicability was the dispatch artifact built under? (issue #247)
# A flag match: a different household with the same flag passes it.
#
# _battery_seed() reads data/battery_dispatch_policies.json and seeds every
# payback in the battery block from it. The artifact states its applicability
# through post_behavior.free_fix_scenario ("a": the EV charge reschedule, only
# an EV household runs it; "c": the house-load shift a no-EV household gets).
# The intake flag br.EV_ANALYSIS (household.has_ev) is the authority; the seed
# must be refused, in both directions, when the artifact disagrees with it.
# ---------------------------------------------------------------------------
def _dispatch_copy(scenario, drop=False):
    """A copy of the committed dispatch artifact with only its household
    identity changed: free_fix_scenario set to `scenario`, or removed when
    `drop`. The ladder is left intact, so the copy passes the reproduction
    check and the only thing that can refuse it is the applicability check."""
    doc = json.loads((ROOT / "data" / "battery_dispatch_policies.json").read_text())
    if drop:
        del doc["post_behavior"]["free_fix_scenario"]
    else:
        doc["post_behavior"]["free_fix_scenario"] = scenario
    fd, tmp = tempfile.mkstemp(suffix=".json")
    with open(fd, "w") as fh:
        json.dump(doc, fh)
    return pathlib.Path(tmp), doc["post_behavior"]["mid"]["battery_marginal"]


def _seed_under(has_ev, path=None):
    """Run _battery_seed() with the intake flag forced and the artifact path
    optionally redirected; restore both. Returns ('seed', value) or
    ('refused', message)."""
    was_flag, was_path = br.EV_ANALYSIS, ts.DISPATCH
    br.EV_ANALYSIS = has_ev
    if path is not None:
        ts.DISPATCH = path
    try:
        seed, _published = ts._battery_seed()
        return "seed", seed
    except SystemExit as exc:
        return "refused", str(exc)
    finally:
        br.EV_ANALYSIS = was_flag
        ts.DISPATCH = was_path
        if path is not None:
            path.unlink(missing_ok=True)


def _assert_refusal(msg, artifact_name):
    assert "EV APPLICABILITY MISMATCH" in msg, msg
    assert artifact_name in msg, f"the refusal does not name the artifact: {msg}"
    assert "household.has_ev" in msg, f"the refusal does not name the flag: {msg}"
    assert "battery_dispatch_policies.py" in msg, f"the refusal does not name the remedy: {msg}"


@case
def case_battery_seed_refuses_an_ev_artifact_on_a_no_ev_household():
    """household.has_ev false, committed artifact from an EV household: the
    seed would be another household's money, so it is refused."""
    outcome, msg = _seed_under(has_ev=False)
    assert outcome == "refused", f"an EV household's seed was accepted on a no-EV intake: {msg}"
    _assert_refusal(msg, "battery_dispatch_policies.json")
    assert "NO EV" in msg, msg
    return "no-EV intake + EV artifact is refused, naming artifact, flag and remedy"


@case
def case_battery_seed_refuses_a_no_ev_artifact_on_an_ev_household():
    """The mirror: household.has_ev true, artifact from a no-EV household. A
    one-directional guard passes this; it must not."""
    path, _ = _dispatch_copy("c")
    outcome, msg = _seed_under(has_ev=True, path=path)
    assert outcome == "refused", f"a no-EV household's seed was accepted on an EV intake: {msg}"
    _assert_refusal(msg, path.name)
    assert "HAS an EV" in msg, msg
    return "EV intake + no-EV artifact is refused, naming artifact, flag and remedy"


@case
def case_battery_seed_accepts_a_matching_household_both_ways():
    """Positive control: a build that refuses everything passes the two cases
    above. Matching households must yield the artifact's own seed, in both
    directions."""
    committed = json.loads((ROOT / "data" / "battery_dispatch_policies.json").read_text())
    want = committed["post_behavior"]["mid"]["battery_marginal"]
    assert committed["post_behavior"]["free_fix_scenario"] == "a", (
        "this repo's committed artifact is an EV household's",
        committed["post_behavior"]["free_fix_scenario"])
    outcome, got = _seed_under(has_ev=True)
    assert outcome == "seed" and got == want, (outcome, got, want)
    path, want_c = _dispatch_copy("c")
    outcome, got_c = _seed_under(has_ev=False, path=path)
    assert outcome == "seed" and got_c == want_c, (outcome, got_c, want_c)
    return f"EV+EV and no-EV+no-EV both seed ${want} from the artifact"


@case
def case_battery_seed_refuses_an_artifact_that_does_not_state_its_household():
    """An artifact with no free_fix_scenario predates the shape that states
    its household. Silence is not agreement; regenerate it."""
    path, _ = _dispatch_copy(None, drop=True)
    outcome, msg = _seed_under(has_ev=True, path=path)
    assert outcome == "refused", f"an artifact with no applicability was accepted: {msg}"
    assert "free_fix_scenario" in msg and path.name in msg, msg
    assert "battery_dispatch_policies.py" in msg, msg
    return "an artifact without post_behavior.free_fix_scenario is refused, naming the remedy"


@case
def case_a_refused_seed_writes_nothing():
    """A refusal must leave data/tou_spread.json byte-for-byte as it was, and
    no temp file behind."""
    out = ROOT / "data" / "tou_spread.json"
    before = hashlib.sha256(out.read_bytes()).hexdigest()
    tmps_before = set(out.parent.glob("*.tmp"))
    was = br.EV_ANALYSIS
    br.EV_ANALYSIS = False
    try:
        ts.main()
    except SystemExit as exc:
        _assert_refusal(str(exc), "battery_dispatch_policies.json")
    else:
        raise AssertionError("main() ran to completion on a mismatched household")
    finally:
        br.EV_ANALYSIS = was
    after = hashlib.sha256(out.read_bytes()).hexdigest()
    assert after == before, "a refused run changed data/tou_spread.json"
    assert set(out.parent.glob("*.tmp")) == tmps_before, "a refused run left a temp file"
    return "a refused run leaves data/tou_spread.json unchanged (sha256 equal) and no temp file"


def main():
    passed = failed = 0
    for fn in CASES:
        try:
            detail = fn()
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"ok   {fn.__name__} -- {detail}")
    print(f"\n{passed}/{passed + failed} cases passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
