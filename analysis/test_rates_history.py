#!/usr/bin/env python3
"""Guards for the historical rate engine in rates_history.py.

Everything here runs in a clean checkout: the only inputs are the two committed
bill artifacts (data/bill_periods_electric.csv, data/bill_tou_detail.csv) — no
private PDFs, no interval export. The cases pin the issue-#2 acceptance
criteria: full-corpus coverage with every component sourced or explicitly
flagged, agreement with rates.py's current vintage to the cent, per-statement
re-billing within ±1%, piecewise segment billing proven better than collapsing
vintages, fail-closed behavior on out-of-coverage dates and absent cells
(messages naming the date and the cell), the provider break, and a
byte-deterministic writer.

Two of the cases are NEGATIVE fixtures: they corrupt a synthetic copy of the two
CSVs (in a temp directory, never the committed files) and assert what the
reconciliation does and does not notice. That is the only way to know the ±1%
gate is measuring something — the timeline-reconstruction residual is zero by
algebraic identity, so on its own it would score a corrupted corpus perfect.

Run from the repo root:  ./.venv/bin/python analysis/test_rates_history.py
"""
import contextlib
import csv
import datetime as dt
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates as R
import rates_history as H


def _raises(fn, *needles):
    """fn must raise SystemExit whose message contains every needle."""
    try:
        fn()
    except SystemExit as e:
        msg = str(e)
        for n in needles:
            assert n in msg, f"raised, but message lacks {n!r}: {msg}"
        return msg
    raise AssertionError(f"{fn} did not raise")


@contextlib.contextmanager
def _corrupted_corpus(mutate):
    """Run the engine against a mutated COPY of the two committed artifacts.

    `mutate(rows)` edits the bill_tou_detail rows in place (dicts of strings, as
    read). The copies live in a temp directory; data/ is never touched, and the
    module's engine caches are reset on the way in and on the way out so no test
    can inherit a corrupted corpus."""
    rd = csv.DictReader(open(H.DATA / "bill_tou_detail.csv", newline=""))
    rows, fields = list(rd), rd.fieldnames
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        shutil.copy(H.DATA / "bill_periods_electric.csv", d)
        mutate(rows)
        with open(d / "bill_tou_detail.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        saved = H.DATA
        H.DATA, H._ENGINE = d, None
        H._HOLDOUT.clear()
        try:
            yield
        finally:
            H.DATA, H._ENGINE = saved, None
            H._HOLDOUT.clear()


def case_coverage_window_is_derived_from_the_artifacts():
    start, end = H.coverage()
    assert start == dt.date(2024, 5, 25), start
    assert end == dt.date(2026, 6, 26), end
    return "coverage is 2024-05-25..2026-06-26, read off the 26 committed periods"


def case_out_of_coverage_dates_raise_and_name_themselves():
    for day in (dt.date(2024, 5, 24), dt.date(2026, 6, 27), dt.date(2020, 1, 1)):
        _raises(lambda d=day: H.rates_on(d), str(day), "2024-05-25", "2026-06-26")
    return "rates_on refuses dates outside the corpus, naming the date and the window"


def case_every_corpus_day_yields_a_fully_classified_rate_set():
    """AC: a complete rate set for every day 5/25/24-6/26/26, sourced only from
    the artifacts. 'Complete' means every component is either a bill-sourced
    value or an explicitly flagged absence — never a silent hole, never an
    invented number. For the cells that bill the day (its own season), the
    absences are pinned exactly: they are the buckets whose printed rate is
    $0.00000 because they netted negative on every statement of their era
    (off-peak, on this solar household) plus the first seven corpus days,
    whose statement printed no positive on-peak line."""
    start, end = H.coverage()
    days = 0
    absent = {}
    day = start
    while day <= end:
        rs = H.rates_on(day)                       # never raises in-window
        assert rs.provider in ("bundled", "CCA"), (day, rs.provider)
        cells = rs.cells()
        assert len(cells) == 12, day               # 2 sections × 2 seasons × 3 TOU
        season = "summer" if day.month in R.SUMMER_MONTHS else "winter"
        for (sec, sea, tp), (rate, tier, note) in cells.items():
            assert tier in ("direct", "carried", "absent"), (day, sec, sea, tp)
            assert (rate is None) == (tier == "absent"), (day, sec, sea, tp)
            if sea == season and tier == "absent":
                absent[(sec, tp)] = absent.get((sec, tp), 0) + 1
        days += 1
        day += dt.timedelta(days=1)
    assert days == 763, days
    want = {("delivery", "off_peak"): 306 + 269,
            ("generation", "off_peak"): 306 + 213,
            ("delivery", "on_peak"): 7, ("generation", "on_peak"): 7}
    assert absent == want, absent                  # sop: sourced on all 763 days
    frac = 1 - sum(absent.values()) / (6 * days)
    return (f"all {days} days return 12 classified cells; the day's own season is "
            f"{frac:.0%} sourced, every absence a pinned net-negative bucket")


def case_missing_rate_cells_raise_naming_date_and_cell():
    """The three genuinely unsourceable shapes: a bucket that netted negative on
    every statement of its era (summer off-peak 2024), an undatable mid-gap
    change (generation winter off-peak spring 2025), and the season not billed
    at the corpus edge."""
    _raises(lambda: H.rates_on(dt.date(2024, 7, 15)).delivery("summer", "off_peak"),
            "delivery/summer/off_peak", "2024-07-15")
    _raises(lambda: H.rates_on(dt.date(2025, 2, 15)).generation("winter", "off_peak"),
            "generation/winter/off_peak", "2025-02-15", "0.11850", "0.12379")
    _raises(lambda: H.rates_on(dt.date(2026, 6, 20)).generation("winter", "on_peak"),
            "generation/winter/on_peak", "2026-06-20")
    # sourced neighbours of those absences still resolve
    assert H.rates_on(dt.date(2024, 7, 15)).delivery("summer", "on_peak") == 0.26438
    assert H.rates_on(dt.date(2025, 4, 1)).generation("winter", "off_peak") == 0.12379
    return "an absent cell raises with the date and the cell; its neighbours resolve"


def case_unsourceable_components_fail_closed():
    """PCIA and NBC are not in any committed artifact; the CCA's own per-TOU
    rates exist only as period totals; BSC exists only from the 10/1/25 period
    (new charge vs renamed line vs parser gap is OPEN — never a silent 0)."""
    rs = H.rates_on(dt.date(2025, 3, 10))
    _raises(rs.pcia, "PCIA", "not parsed")
    _raises(rs.nbc, "GROSS")
    _raises(lambda: rs.cca_generation("winter", "on_peak"), "CCA")
    _raises(rs.bsc_per_day, "2025-03-10", "OPEN")
    got = H.rates_on(dt.date(2025, 10, 5)).bsc_per_day()
    assert abs(got - 21.42 / 27) < 1e-12, got
    return "PCIA/NBC/CCA-TOU/pre-10/25-BSC all fail closed; sourced BSC is total/days"


def case_current_vintage_matches_rates_py_to_the_cent():
    """AC: the engine's final delivery vintage equals rates.py's UDC on all six
    season×TOU cells. (rates.py's CEA table comes from the CCA's own schedule,
    which the bill artifacts do not carry — see the module docstring.)"""
    cv = H.current_vintage()
    for seas, season in (("S", "summer"), ("W", "winter")):
        for short, long_tp in zip(tuple(R.UDC["S"]),
                                  ("on_peak", "off_peak", "super_off_peak")):
            want = R.UDC[seas][short]
            got, s, e = cv[("delivery", season, long_tp)]
            assert abs(got - want) < 0.005, (seas, short, got, want)
            assert got == want, (seas, short, got, want)   # in fact exact
    return "all six UDC season×TOU cells match rates.py exactly, not just to the cent"


def case_rebilling_reproduces_all_26_statements_within_1pct():
    """AC: each statement re-billed from its own per-TOU kWh within ±1%. Note what
    that gate does and does not prove. The reference is the statement's OWN printed
    TOU energy lines and the timeline is built from those same rows, so the
    piecewise residual is zero as an identity — exactly 0 on all 26, and it would
    stay 0 with corrupted rates (case_a_perturbed_rate_... proves that). The
    check with teeth is the holdout re-bill, asserted here too."""
    periods = [p["period"] for p in H._engine().periods]
    assert len(periods) == 26, len(periods)
    for period in periods:
        r = H.rebill_statement(period)
        assert r["residual"] == 0.0, (period, r)     # identity, not just ±1%
        assert abs(r["residual_pct"]) <= 1.0, r      # the AC as written
    printed = priced = 0.0
    worst = 0.0
    for period in periods:
        h = H.rebill_holdout(period)
        printed += H.rebill_statement(period)["printed"]
        priced += h["printed_priced"]
        if h["residual_pct"] is not None:
            assert abs(h["residual_pct"]) <= 1.0, (period, h)
            worst = max(worst, abs(h["residual_pct"]))
    assert priced / printed > 0.6, priced / printed
    return (f"26/26 statements re-bill to exactly $0.000000 residual (an identity); "
            f"the holdout re-bill prices {100 * priced / printed:.1f}% of printed "
            f"dollars from the OTHER statements' rates, worst |residual| {worst:.4f}%")


def case_rate_only_vintage_collapse_worsens_the_split_statements():
    """AC: multi-segment periods must be billed piecewise. The proof has to change
    ONE thing — the vintage. mode='vintage_collapse' keeps the printed segment kWh
    and the positive-only rule identical to the piecewise path and only prices every
    row at the rates in force on its block's LAST day, so any residual is
    attributable to collapsing vintages and to nothing else.

    Four of the five split statements worsen measurably. The fifth
    (12/27/24-1/27/25) does not, and the artifact says why: every priced cell
    printed the SAME rate in both of its segments there (the bill split the cycle
    but those rates did not move), and the two cells that could differ — winter
    off-peak, delivery and generation — have no bill-sourced rate on the block's
    last day, so they are excluded from both sides and counted."""
    eng = H._engine()
    split = [p["period"] for p in eng.periods
             if H.rebill_statement(p["period"])["n_segments"] > 1]
    assert len(split) == 5, split
    worsened = beyond_1pct = 0
    flat = []
    for period in split:
        piece = H.rebill_statement(period)
        vint = H.rebill_statement(period, "vintage_collapse")
        assert piece["residual_pct"] == 0.0, (period, piece)
        if vint["residual_pct"] in (0.0, None):     # None = nothing was priceable
            flat.append((period, vint["unpriced_rows"]))
            continue
        assert abs(vint["residual_pct"]) > abs(piece["residual_pct"]), (period, vint)
        worsened += 1
        beyond_1pct += abs(vint["residual_pct"]) > 1.0
    assert worsened == 4, worsened
    assert beyond_1pct == 1, beyond_1pct
    assert flat == [("12/27/24 - 1/27/25", 2)], flat
    single = eng.periods[1]["period"]                # 6/26/24-7/25/24: one segment
    p1 = H.rebill_statement(single)
    for mode in ("vintage_collapse", "netting_collapse"):
        c1 = H.rebill_statement(single, mode)
        assert p1["rebilled"] == c1["rebilled"], f"{mode} must be a no-op on 1 segment"
    worst = max(abs(H.rebill_statement(p, "vintage_collapse")["residual_pct"])
                for p in split)
    return (f"the rate-only collapse worsens {worsened} of the 5 split statements "
            f"(1 beyond ±1%, up to {worst:.2f}%); on the 5th every priced cell "
            f"printed one rate across both segments and 2 rows have no sourced "
            f"block-end rate")


def case_netting_collapse_is_a_separate_counterfactual():
    """The other collapse variant changes the netting as well as the vintage (it
    sums signed kWh across the printed segments before re-applying the
    positive-only rule), so it cannot be read as evidence about vintages. It is
    reported under its own name, and on two split statements the two
    counterfactuals disagree by more than a percentage point — which is exactly
    why the vintage claim must rest on the rate-only column."""
    disagree = {}
    for p in H._engine().periods:
        period = p["period"]
        v = H.rebill_statement(period, "vintage_collapse")["residual_pct"]
        n = H.rebill_statement(period, "netting_collapse")["residual_pct"]
        if abs(v - n) > 1.0:
            disagree[period] = (round(v, 4), round(n, 4))
    assert set(disagree) == {"12/27/24 - 1/27/25", "3/28/26 - 4/28/26",
                             "12/27/25 - 1/27/26"}, disagree
    assert disagree["12/27/24 - 1/27/25"] == (0.0, -6.0773), disagree
    _raises(lambda: H.rebill_statement("12/27/24 - 1/27/25", "collapse"),
            "unknown mode")
    return ("netting collapse is labeled separately; it disagrees with the "
            f"rate-only collapse by >1pp on {len(disagree)} statements "
            "(12/27/24-1/27/25: 0.0000% vs -6.0773%)")


def case_a_perturbed_rate_is_invisible_to_the_identity_but_caught_by_the_holdout():
    """NEGATIVE fixture (issue #2 AC3 has to be able to fail). Add $0.05/kWh to one
    printed delivery on-peak rate on the 7/29/25-8/26/25 statement, which the rest
    of the corpus fully corroborates:
      * the timeline residual stays exactly $0.000000 — the reconstruction check
        cannot see a misread rate, which is why it is labeled an identity;
      * the holdout re-bill, priced from the other 25 statements, moves by exactly
        the perturbation (371 kWh × $0.05 = $18.55)."""
    period = "7/29/25 - 8/26/25"
    cell = ("delivery", "summer", "on_peak")
    base = [r for r in H._engine().tou
            if r["period"] == period and (r["section"], r["season"],
                                          r["tou_period"]) == cell and r["kwh"] > 0]
    assert len(base) == 1, base
    kwh, rate = base[0]["kwh"], base[0]["rate"]
    assert H.rebill_holdout(period)["residual"] == 0.0     # clean before

    def mutate(rows):
        hits = [r for r in rows if r["period"] == period
                and (r["section"], r["season"], r["tou_period"]) == cell
                and float(r["kwh"]) > 0]
        assert len(hits) == 1, hits
        hits[0]["rate_per_kwh"] = f"{rate + 0.05:.5f}"

    with _corrupted_corpus(mutate):
        piece = H.rebill_statement(period)
        hold = H.rebill_holdout(period)
        assert piece["residual"] == 0.0, piece            # blind, by construction
        assert hold["priced_pct"] == 100.0, hold
        assert abs(hold["residual"] - (-kwh * 0.05)) < 1e-9, hold
        assert abs(hold["residual_pct"]) > 4.8, hold
    assert H.rebill_holdout(period)["residual"] == 0.0     # and clean after
    return (f"a +$0.05/kWh corruption leaves the reconstruction residual at $0 and "
            f"moves the holdout residual to ${hold['residual']:.2f} "
            f"({hold['residual_pct']:.2f}% of the priced lines)")


def case_a_mis_associated_segment_is_caught_or_pinned():
    """NEGATIVE fixture, part two: mis-associate a rate segment.

    Reassigning ONE cell's segment ids inside a split statement is caught at engine
    construction — the printed day counts for that segment then disagree, and the
    message names the statement, the section, the season and both day counts.

    Swapping BOTH segments' date ranges consistently across a whole (statement,
    section) is NOT caught, and this pins that blind spot rather than implying
    coverage: the segment→date mapping IS the bill's own ordering, and the only
    statement that dates the mid-cycle change is the corrupted one, so nothing in
    the corpus contradicts the swap (the affected cells are exactly the ones the
    holdout cannot price — 10 of 12 rows on this statement)."""
    period = "12/27/25 - 1/27/26"

    def reassign_one_cell(rows):
        for r in rows:
            if r["period"] == period and r["section"] == "delivery" \
                    and r["tou_period"] == "on_peak":
                r["segment"] = "1" if r["segment"] == "0" else "0"

    with _corrupted_corpus(reassign_one_cell):
        msg = _raises(lambda: H.rebill_statement(period),
                      period, "delivery", "winter", "segment 0 appears with two "
                      "day counts", "5, 27")

    def swap_whole_section(rows):
        for r in rows:
            if r["period"] == period and r["section"] == "delivery":
                r["segment"] = "1" if r["segment"] == "0" else "0"

    with _corrupted_corpus(swap_whole_section):
        piece = H.rebill_statement(period)
        hold = H.rebill_holdout(period)
        assert piece["residual"] == 0.0, piece
        assert hold["priced_pct"] == 0.0 and hold["unpriced_rows"] == 10, hold
    return ("a reassigned segment id fails closed at construction (" +
            msg.split(": ")[-1] + "); a consistent whole-section date-range swap "
            "is not detectable from the artifacts, and the artifact records the "
            "0% holdout coverage that says so")


def case_cca_generation_gap_is_recorded_as_data():
    """The printed generation TOU table is not the CCA tariff, and the artifact
    carries the measurement instead of only prose: Σ(printed generation kWh ×
    printed rate) against the CCA generation charge the statement actually billed.
    Bundled statements have no CCA charge and the columns stay empty there."""
    rows = list(csv.DictReader(
        open(H.DATA / "rate_rebilling_residuals.csv", newline="")))
    assert len(rows) == 26, len(rows)
    gaps = {}
    for row in rows:
        got = H.cca_generation_gap(row["period"])
        if row["provider"] == "bundled":
            assert got is None, row["period"]
            assert row["cca_generation_gap_usd"] == "", row
            continue
        assert abs(got["gap"] - float(row["cca_generation_gap_usd"])) < 0.005, row
        gaps[row["period"]] = (float(row["printed_generation_usd"]),
                               float(row["cca_billed_generation_usd"]),
                               float(row["cca_generation_gap_pct"]))
    assert len(gaps) == 19, len(gaps)
    assert gaps["1/28/26 - 2/26/26"] == (116.09, 56.82, 104.32), gaps["1/28/26 - 2/26/26"]
    worst = max(gaps.values(), key=lambda t: abs(t[2]))
    span = (min(t[2] for t in gaps.values()), max(t[2] for t in gaps.values()))
    assert worst[2] > 100 and span[0] < 0, (worst, span)
    return (f"all 19 CCA statements carry the printed-vs-billed generation gap "
            f"({span[0]:.1f}% to {span[1]:.1f}%; 1/28/26-2/26/26 prints $116.09 "
            f"against a $56.82 charge)")


def case_provider_break_is_where_the_bills_put_it():
    assert H.rates_on(dt.date(2024, 12, 26)).provider == "bundled"
    assert H.rates_on(dt.date(2024, 12, 27)).provider == "CCA"
    assert H.rates_on(dt.date(2024, 5, 25)).provider == "bundled"
    assert H.rates_on(dt.date(2026, 6, 26)).provider == "CCA"
    # bundled-era generation rates are what SDG&E actually billed, and they are
    # readable; the CCA's own rates never become readable in either era
    assert H.rates_on(dt.date(2024, 8, 1)).generation("summer", "on_peak") == 0.38826
    _raises(lambda: H.rates_on(dt.date(2024, 8, 1)).cca_generation("summer", "on_peak"),
            "CCA")
    return "bundled through 2024-12-26, CCA from 2024-12-27, per the bill artifacts"


def case_writer_is_deterministic_and_atomic():
    """AC: data/rate_vintages.csv (and the residual table) must regenerate
    byte-identically. Two independent writes must produce identical bytes, no
    temp files may survive, and the committed copies must match what the writer
    produces from the committed inputs."""
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        pa = H._write_artifacts(a)
        pb = H._write_artifacts(b)
        for x, y in zip(pa, pb):
            assert x.read_bytes() == y.read_bytes(), f"nondeterministic: {x.name}"
            committed = H.DATA / x.name
            assert committed.exists(), f"{x.name} not committed"
            assert committed.read_bytes() == x.read_bytes(), (
                f"committed {x.name} is stale — rerun analysis/rates_history.py")
        leftovers = [f for d in (a, b) for f in pathlib.Path(d).glob("*.tmp")]
        assert not leftovers, leftovers
    return "both artifacts regenerate byte-identically and leave no temp files"


def case_worst_statement_is_named_in_the_committed_artifact():
    """AC: the worst statement is named in the artifact. Also pins the column names
    that say what the reference is — the reconciliation columns must not read as an
    independent dollar validation."""
    text = (H.DATA / "rate_rebilling_residuals.csv").read_text().splitlines()
    header = text[0].split(",")
    assert header[-1] == "worst_residual", header
    for name in ("printed_tou_energy_usd", "timeline_energy_usd",
                 "timeline_residual_usd", "timeline_residual_pct",
                 "holdout_printed_priced_usd", "holdout_priced_pct",
                 "holdout_residual_pct", "vintage_collapse_residual_pct",
                 "netting_collapse_residual_pct", "cca_generation_gap_usd"):
        assert name in header, (name, header)
    assert not any(c in header for c in ("billed_energy_usd", "rebilled_energy_usd",
                                         "collapsed_residual_pct")), header
    flagged = [l for l in text[1:] if l.endswith(",worst")]
    assert len(flagged) == 1, f"exactly one worst statement expected: {flagged}"
    return (f"the residual artifact names the worst statement "
            f"({flagged[0].split(',')[1]}) and its columns name their reference")


def case_bill_nem_prices_bundled_dates_and_refuses_cca_ones():
    """Signature-style parity with rates.bill_nem: same frame contract, but priced
    per-date at the historical vintages. A 2024-10 frame spanning the 10/1/24
    delivery on-peak change (0.26438 -> 0.26687) must bill each side at its own
    vintage, i.e. per constant-rate span, never one blended rate.

    delivery + generation is a real tariff only while SDG&E was the bundled
    provider, so any frame touching a CCA date is refused by default — naming the
    date, the period and the provider — rather than adding the printed
    bundled-generation comparison table to delivery and returning a
    plausible-looking wrong number. delivery_only=True prices the UDC component
    alone and is allowed on CCA dates."""
    import pandas as pd
    seas, p_on = "S", tuple(R.UDC["S"])[0]
    frame = pd.DataFrame([
        dict(dt=pd.Timestamp("2024-09-30 17:00"), seas=seas, p=p_on,
             ym="2024-09", Consumption=10.0, Generation=0.0),
        dict(dt=pd.Timestamp("2024-10-02 17:00"), seas=seas, p=p_on,
             ym="2024-10", Consumption=10.0, Generation=0.0),
    ])
    got = H.bill_nem_monthly(frame)
    want_sep = 10.0 * (0.26438 + 0.38826)
    want_oct = 10.0 * (0.26687 + 0.38826)
    assert abs(got["2024-09"] - want_sep) < 1e-9, got
    assert abs(got["2024-10"] - want_oct) < 1e-9, got
    assert abs(H.bill_nem(frame) - (want_sep + want_oct)) < 1e-9
    # delivery-only is the documented opt-in, and it is delivery ALONE
    assert abs(H.bill_nem(frame, delivery_only=True)
               - 10.0 * (0.26438 + 0.26687)) < 1e-9
    # a CCA date is refused by default, naming date, period, provider and reason
    cca = frame.assign(dt=[pd.Timestamp("2025-08-01 17:00")] * 2,
                       ym=["2025-08"] * 2)
    for fn in (H.bill_nem, H.bill_nem_monthly):
        _raises(lambda f=fn: f(cca), "2025-08-01", "7/29/25 - 8/26/25", "CCA",
                "comparison", "cca_generation", "delivery_only=True")
    # the last bundled day still prices, the first CCA day does not
    last_bundled = frame.assign(dt=[pd.Timestamp("2024-12-26 17:00")] * 2,
                               seas=["W"] * 2, ym=["2024-12"] * 2)
    assert H.bill_nem(last_bundled) > 0
    _raises(lambda: H.bill_nem(last_bundled.assign(
        dt=[pd.Timestamp("2024-12-27 17:00")] * 2)), "2024-12-27", "CCA")
    # delivery-only prices a CCA date, and carries no generation
    cca_udc = H.bill_nem(cca, delivery_only=True)
    assert abs(cca_udc - 20.0 * H.rates_on(dt.date(2025, 8, 1))
               .delivery("summer", "on_peak")) < 1e-9, cca_udc
    # and it fails closed when the frame reaches a date the corpus cannot price
    bad = frame.assign(dt=[pd.Timestamp("2026-06-27 17:00")] * 2)
    _raises(lambda: H.bill_nem(bad), "2026-06-27")
    _raises(lambda: H.bill_nem(bad, delivery_only=True), "2026-06-27")
    return ("bill_nem prices bundled dates per historical vintage, refuses CCA "
            "dates unless delivery_only=True, and fails closed off-corpus")


CASES = [
    case_coverage_window_is_derived_from_the_artifacts,
    case_out_of_coverage_dates_raise_and_name_themselves,
    case_every_corpus_day_yields_a_fully_classified_rate_set,
    case_missing_rate_cells_raise_naming_date_and_cell,
    case_unsourceable_components_fail_closed,
    case_current_vintage_matches_rates_py_to_the_cent,
    case_rebilling_reproduces_all_26_statements_within_1pct,
    case_rate_only_vintage_collapse_worsens_the_split_statements,
    case_netting_collapse_is_a_separate_counterfactual,
    case_a_perturbed_rate_is_invisible_to_the_identity_but_caught_by_the_holdout,
    case_a_mis_associated_segment_is_caught_or_pinned,
    case_cca_generation_gap_is_recorded_as_data,
    case_provider_break_is_where_the_bills_put_it,
    case_writer_is_deterministic_and_atomic,
    case_worst_statement_is_named_in_the_committed_artifact,
    case_bill_nem_prices_bundled_dates_and_refuses_cca_ones,
]


def main():
    ran = failures = 0
    for case in CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    print(f"\n{ran}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
