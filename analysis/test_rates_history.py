#!/usr/bin/env python3
"""Guards for the historical rate engine in rates_history.py.

Everything here runs in a clean checkout: the only inputs are the two committed
bill artifacts (data/bill_periods_electric.csv, data/bill_tou_detail.csv) — no
private PDFs, no interval export. The cases pin the issue-#2 acceptance
criteria: full-corpus coverage with every component sourced or explicitly
flagged, agreement with rates.py's current vintage to the cent, per-statement
re-billing within ±1%, piecewise segment billing proven better than collapsing,
fail-closed behavior on out-of-coverage dates and absent cells (messages naming
the date and the cell), the provider break, and a byte-deterministic writer.

Run from the repo root:  ./.venv/bin/python analysis/test_rates_history.py
"""
import datetime as dt
import pathlib
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
    start, end = H.coverage()
    periods = [p["period"] for p in H._engine().periods]
    assert len(periods) == 26, len(periods)
    worst = max((H.rebill_statement(p) for p in periods),
                key=lambda r: abs(r["residual_pct"]))
    assert abs(worst["residual_pct"]) <= 1.0, worst
    return (f"26/26 statements re-billed within ±1% "
            f"(worst |residual| {abs(worst['residual_pct']):.4f}%)")


def case_collapsing_segments_measurably_worsens_the_residual():
    """AC: multi-segment periods must be billed piecewise. Collapsing each season
    block to the single vintage in force at its end re-nets buckets across the
    printed segments and misprices the changed rates; on the five split
    statements that visibly worsens the residual, past ±1% on two of them."""
    split = [p["period"] for p in H._engine().periods
             if H.rebill_statement(p["period"])["n_segments"] > 1]
    assert len(split) == 5, split
    worsened = beyond_1pct = 0
    for period in split:
        piece = H.rebill_statement(period)
        coll = H.rebill_statement(period, collapse=True)
        assert abs(coll["residual_pct"]) > abs(piece["residual_pct"]), (period, coll)
        worsened += 1
        beyond_1pct += abs(coll["residual_pct"]) > 1.0
    single = H._engine().periods[1]["period"]        # 6/26/24-7/25/24: one segment
    p1 = H.rebill_statement(single)
    c1 = H.rebill_statement(single, collapse=True)
    assert p1["rebilled"] == c1["rebilled"], "collapse must be a no-op on one segment"
    return (f"collapse worsens all {worsened} split statements "
            f"({beyond_1pct} beyond ±1%, up to "
            f"{max(abs(H.rebill_statement(p, True)['residual_pct']) for p in split):.2f}%)"
            )


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
    text = (H.DATA / "rate_rebilling_residuals.csv").read_text().splitlines()
    header = text[0].split(",")
    assert header[-1] == "worst_residual", header
    flagged = [l for l in text[1:] if l.endswith(",worst")]
    assert len(flagged) == 1, f"exactly one worst statement expected: {flagged}"
    return f"the residual artifact names the worst statement: {flagged[0].split(',')[1]}"


def case_bill_nem_swaps_in_for_the_rates_engine():
    """Signature-style parity with rates.bill_nem: same frame contract, but
    priced per-date at the historical vintages. A 2024-10 frame spanning the
    10/1/24 delivery on-peak change (0.26438 -> 0.26687) must bill each side at
    its own vintage, i.e. per constant-rate span, never one blended rate."""
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
    # and it fails closed when the frame reaches a date the corpus cannot price
    bad = frame.assign(dt=[pd.Timestamp("2026-06-27 17:00")] * 2)
    _raises(lambda: H.bill_nem(bad), "2026-06-27")
    return "bill_nem prices per historical vintage and fails closed off-corpus"


CASES = [
    case_coverage_window_is_derived_from_the_artifacts,
    case_out_of_coverage_dates_raise_and_name_themselves,
    case_every_corpus_day_yields_a_fully_classified_rate_set,
    case_missing_rate_cells_raise_naming_date_and_cell,
    case_unsourceable_components_fail_closed,
    case_current_vintage_matches_rates_py_to_the_cent,
    case_rebilling_reproduces_all_26_statements_within_1pct,
    case_collapsing_segments_measurably_worsens_the_residual,
    case_provider_break_is_where_the_bills_put_it,
    case_writer_is_deterministic_and_atomic,
    case_worst_statement_is_named_in_the_committed_artifact,
    case_bill_nem_swaps_in_for_the_rates_engine,
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
