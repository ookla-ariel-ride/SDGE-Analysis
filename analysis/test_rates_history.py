#!/usr/bin/env python3
"""Guards for the historical rate engine in rates_history.py.

Everything here runs in a clean checkout: the only inputs are the two committed
bill artifacts (data/bill_periods_electric.csv, data/bill_tou_detail.csv) — no
private PDFs, no interval export. The cases pin the issue-#2 acceptance
criteria: full-corpus coverage with every component sourced or explicitly
flagged, agreement with rates.py's current vintage to the cent, per-statement
re-billing within ±1%, piecewise segment billing proven better than collapsing
vintages, fail-closed behavior on out-of-coverage dates, absent cells and
malformed printed rates (messages naming the date/period and the cell), the
refusal to hand out a CCA-period generation tariff, the provider break, and a
byte-deterministic writer.

THE CCA AUTHORITY BOUNDARY IS PINNED AT EVERY EXIT, not just at
RateSet.generation(). Three cases cover it together: the per-cell refusal
(case_generation_refuses_on_cca_dates_...), the aggregations that hand out many
cells at once (case_the_aggregations_never_hand_out_a_cca_generation_tariff — for
cells() and current_vintage(), including that the retired (rate, tier, note)
unpacking now raises TypeError), and the committed artifact
(case_the_vintage_artifact_separates_charged_tariffs_from_comparisons — section
name, value column, authority column, and the corrected note wording). Delivery
is asserted charged in BOTH provider eras, because the UDC is what was charged
either way.

THE HOLDOUT GATE, and why it is not a coverage percentage. Each statement's
printed rate lines are classified by rates_history.corroboration(): a line is
corroborated when the rest of the corpus independently witnesses that cell across
the whole printed segment, and uncorroborated BY CONSTRUCTION when it cannot —
a rate whose first appearance in the corpus is the line under test has no second
opinion available, and neither does a line at a corpus edge. The gate here is
therefore two-sided: EVERY corroborated line must agree with its witness exactly,
and the per-statement counts (with the reason for every uncorroborated line) are
pinned in CORROBORATION below. An aggregate share of priceable dollars would have
been satisfied by a corpus of stable repeated rates while a corrupted new vintage
sat entirely outside it, and would not have noticed a line dropping out of
coverage; the pinned dict does.

THE UNIT OF INDEPENDENCE IS THE STATEMENT, not the billing period, and two cases
hold it there. One statement can print two billing periods (the 2025-10-31
statement prints 9/26/25-9/30/25 and 10/1/25-10/27/25), and sibling periods come
out of one parse of one PDF down one extraction path, so a sibling is not an
independent witness. case_the_holdout_unit_is_the_statement_not_the_billing_period
asserts the provenance directly — corroboration() names the statements behind each
witness, and the held-out statement is never among them — and
case_a_shared_statement_error_cannot_corroborate_its_sibling_period injects the
parser error the two units disagree about: with 10/1/25-10/27/25's delivery on-peak
rate misread as its sibling's, a period-level holdout calls the sibling's line
corroborated AND agreeing, on the strength of its own statement. The statement-
level holdout reports it uncorroborated.

Five of the cases are NEGATIVE fixtures: they corrupt a synthetic copy of the two
CSVs (in a temp directory, never the committed files) and assert what the
reconciliation does and does not notice. That is the only way to know the gate is
measuring something — the timeline-reconstruction residual is zero by algebraic
identity, so on its own it would score a corrupted corpus perfect. One of them
perturbs a corroborated line (caught, by exactly the injected dollars, and three
statements' pinned counts move); one perturbs an uncorroborated-by-construction
line and asserts it is reported as UNCORROBORATED rather than as agreement.

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


# A residual that is zero as an ALGEBRAIC identity is not bit-zero on every machine:
# the same sum came back 0.0 here and -2.842e-14 on a CI runner, which reorders the
# additions and contracts multiply-adds differently. Anything downstream of an
# arithmetic operation is therefore compared against an epsilon in its own unit.
# Comparisons of values read VERBATIM from an artifact (a printed rate against a
# witness's printed rate) stay exact on purpose: there the exactness is the evidence.
ZERO_USD = 1e-9        # dollars
ZERO_PCT = 1e-9        # percent


def _is_zero(x, eps=ZERO_USD):
    """True when x is zero to within float noise. None is NOT zero — it means the
    quantity could not be computed at all, which callers distinguish."""
    return x is not None and abs(x) <= eps


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


# One statement can print more than one billing period (CLAUDE.md §1: one PDF can
# contain several billing cycles, so periods are parsed, not files). On this corpus
# 26 periods come off 25 statements, and exactly one statement carries two of them.
# Pinned so a new multi-period statement has to be noticed rather than absorbed.
MULTI_PERIOD_STATEMENTS = {
    "2025-10-31": ("9/26/25 - 9/30/25", "10/1/25 - 10/27/25"),
}

# Pinned per STATEMENT, not as an aggregate:
# (corroborated, agreeing, disagreeing, uncorroborated, uncorroborated reasons).
# A line that is corroborated today and falls out of coverage tomorrow changes one
# of these tuples and fails the gate — the regression an aggregate coverage
# percentage would have absorbed. The uncorroborated counts are the honest half:
# the corpus cannot corroborate them, so they are pinned WITH their reason instead
# of being dropped, and 0 of the 26 statements is recorded as fully validated when
# it is not.
CORROBORATION = {
    "5/25/24 - 6/25/24":   (0, 0, 0, 6, "first_witness_is_later_than_this_statement:6"),
    "6/26/24 - 7/25/24":   (4, 4, 0, 0, ""),
    "7/26/24 - 8/26/24":   (4, 4, 0, 0, ""),
    "8/27/24 - 9/25/24":   (4, 4, 0, 0, ""),
    "9/26/24 - 10/25/24":  (6, 6, 0, 2,
                            "flanking_witnesses_disagree_the_change_is_undated:2"),
    "10/26/24 - 11/25/24": (2, 2, 0, 6,
                            "first_witness_is_later_than_this_statement:2;"
                            "flanking_witnesses_disagree_the_change_is_undated:4"),
    "11/26/24 - 12/26/24": (4, 4, 0, 0, ""),
    "12/27/24 - 1/27/25":  (8, 8, 0, 2, "first_witness_is_later_than_this_statement:2"),
    "1/28/25 - 2/26/25":   (0, 0, 0, 8,
                            "flanking_witnesses_disagree_the_change_is_undated:8"),
    "2/27/25 - 3/27/25":   (4, 4, 0, 0, ""),
    "3/28/25 - 4/28/25":   (4, 4, 0, 2,
                            "flanking_witnesses_disagree_the_change_is_undated:2"),
    "4/29/25 - 5/28/25":   (4, 4, 0, 0, ""),
    "5/29/25 - 6/26/25":   (3, 3, 0, 7,
                            "flanking_witnesses_disagree_the_change_is_undated:7"),
    "6/27/25 - 7/28/25":   (4, 4, 0, 0, ""),
    "7/29/25 - 8/26/25":   (4, 4, 0, 0, ""),
    "8/27/25 - 9/25/25":   (4, 4, 0, 0, ""),
    "9/26/25 - 9/30/25":   (2, 2, 0, 2,
                            "flanking_witnesses_disagree_the_change_is_undated:2"),
    "10/1/25 - 10/27/25":  (2, 2, 0, 2,
                            "flanking_witnesses_disagree_the_change_is_undated:2"),
    "10/28/25 - 11/25/25": (2, 2, 0, 6,
                            "flanking_witnesses_disagree_the_change_is_undated:6"),
    "11/26/25 - 12/26/25": (4, 4, 0, 0, ""),
    "12/27/25 - 1/27/26":  (0, 0, 0, 10,
                            "flanking_witnesses_disagree_the_change_is_undated:10"),
    "1/28/26 - 2/26/26":   (4, 4, 0, 0, ""),
    "2/27/26 - 3/27/26":   (4, 4, 0, 2,
                            "flanking_witnesses_disagree_the_change_is_undated:2"),
    "3/28/26 - 4/28/26":   (0, 0, 0, 10,
                            "flanking_witnesses_disagree_the_change_is_undated:10"),
    "4/29/26 - 5/28/26":   (4, 4, 0, 2,
                            "last_witness_is_earlier_than_this_statement:2"),
    "5/29/26 - 6/26/26":   (0, 0, 0, 10,
                            "last_witness_is_earlier_than_this_statement:8;"
                            "no_witness_anywhere_for_this_cell:2"),
}
# The mid-cycle change on each split statement, pinned separately:
# (differing cells, their corroborated lines, their uncorroborated lines).
SPLIT_DIFFERING = {
    "9/26/24 - 10/25/24":  (1, 0, 2),
    "12/27/24 - 1/27/25":  (0, 0, 0),      # split cycle, but no rate moved
    "1/28/25 - 2/26/25":   (4, 0, 8),
    "12/27/25 - 1/27/26":  (4, 0, 8),
    "3/28/26 - 4/28/26":   (4, 0, 8),
}


def _corroboration_snapshot():
    """({period: CORROBORATION tuple}, every_corroborated_line_agrees_exactly).

    Separate from the assertions so the negative fixtures can watch the same gate
    move under a corruption instead of re-deriving it."""
    out, exact = {}, True
    for p in H._engine().periods:
        c = H.corroboration(p["period"])
        for row in c["rows"]:
            if row["status"] == "corroborated" and \
                    row["witness_rate"] != row["printed_rate"]:
                exact = False
        out[p["period"]] = (
            c["corroborated_rows"], c["agree_rows"], c["disagree_rows"],
            c["uncorroborated_rows"],
            ";".join(f"{k}:{v}" for k, v in sorted(c["reasons"].items())))
    return out, exact


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
    the artifacts. 'Complete' means all 12 cells come back with an explicit
    classification — a bill-sourced CHARGED tariff (direct or carried), a printed
    CCA-generation COMPARISON labelled as not-a-tariff, or a flagged absence —
    never a silent hole, never an invented number, and never a comparison number
    in the .rate slot. The four-way counts over 763 days × 12 cells are pinned
    here, so a cell that changes authority or falls out of coverage fails.

    For the cells that bill the day (its own season) the absences are pinned
    exactly: the buckets whose printed rate is $0.00000 because they netted
    negative on every statement of their era (off-peak, on this solar household)
    plus the first seven corpus days, whose statement printed no positive on-peak
    line. Those counts are unchanged by the authority split — an absence is an
    absence in either provider era."""
    start, end = H.coverage()
    days = 0
    absent = {}
    classes = {}
    day = start
    while day <= end:
        rs = H.rates_on(day)                       # never raises in-window
        assert rs.provider in ("bundled", "CCA"), (day, rs.provider)
        cells = rs.cells()
        assert len(cells) == 12, day               # 2 sections × 2 seasons × 3 TOU
        season = "summer" if day.month in R.SUMMER_MONTHS else "winter"
        for (sec, sea, tp), cv in cells.items():
            where = (day, sec, sea, tp)
            assert (cv.section, cv.season, cv.tou_period) == (sec, sea, tp), where
            assert cv.date == day and cv.provider == rs.provider, where
            assert cv.tier in ("direct", "carried", "absent"), where
            assert cv.authority in (H.CHARGED_TARIFF, H.COMPARISON_ONLY), where
            # delivery is the charged tariff in BOTH provider eras; generation is
            # charged only while SDG&E was bundled
            assert cv.authority == (
                H.COMPARISON_ONLY if sec == "generation" and rs.provider == "CCA"
                else H.CHARGED_TARIFF), where
            # exactly one value slot may be populated, and which one is decided by
            # the authority — a comparison number can never land in .rate
            if cv.tier == "absent":
                assert (cv.rate, cv.comparison_rate) == (None, None), where
                assert cv.classification == "absent", where
            elif cv.authority == H.CHARGED_TARIFF:
                assert cv.rate is not None and cv.comparison_rate is None, where
                assert cv.is_charged_tariff and cv.classification == cv.tier, where
            else:
                assert cv.rate is None and cv.comparison_rate is not None, where
                assert not cv.is_charged_tariff, where
                assert cv.classification == "comparison", where
            classes[cv.classification] = classes.get(cv.classification, 0) + 1
            if sea == season and cv.tier == "absent":
                absent[(sec, tp)] = absent.get((sec, tp), 0) + 1
        days += 1
        day += dt.timedelta(days=1)
    assert days == 763, days
    assert classes == {"direct": 2102, "carried": 336, "absent": 4921,
                       "comparison": 1797}, classes
    assert sum(classes.values()) == 12 * days, classes
    want = {("delivery", "off_peak"): 306 + 269,
            ("generation", "off_peak"): 306 + 213,
            ("delivery", "on_peak"): 7, ("generation", "on_peak"): 7}
    assert absent == want, absent                  # sop: sourced on all 763 days
    frac = 1 - sum(absent.values()) / (6 * days)
    return (f"all {days} days return 12 classified cells "
            f"({classes['direct']} direct, {classes['carried']} carried, "
            f"{classes['comparison']} printed-comparison-only, "
            f"{classes['absent']} flagged absent); the day's own season is "
            f"{frac:.0%} classified from the bills, every absence a pinned "
            f"net-negative bucket")


def case_missing_rate_cells_raise_naming_date_and_cell():
    """The three genuinely unsourceable shapes: a bucket that netted negative on
    every statement of its era (summer off-peak 2024), an undatable mid-gap
    change (generation winter off-peak spring 2025), and the season not billed
    at the corpus edge. Asked through the accessor that is legitimate for the
    date: generation() on a bundled date, generation_comparison_table() on the
    CCA dates, where generation() refuses for a different reason entirely."""
    _raises(lambda: H.rates_on(dt.date(2024, 7, 15)).delivery("summer", "off_peak"),
            "delivery/summer/off_peak", "2024-07-15")
    _raises(lambda: H.rates_on(dt.date(2024, 7, 15)).generation("summer", "off_peak"),
            "generation/summer/off_peak", "2024-07-15")     # bundled: the primary API
    _raises(lambda: H.rates_on(dt.date(2025, 2, 15))
            .generation_comparison_table("winter", "off_peak"),
            "generation/winter/off_peak", "2025-02-15", "0.11850", "0.12379")
    _raises(lambda: H.rates_on(dt.date(2026, 6, 20))
            .generation_comparison_table("winter", "on_peak"),
            "generation/winter/on_peak", "2026-06-20")
    # sourced neighbours of those absences still resolve
    assert H.rates_on(dt.date(2024, 7, 15)).delivery("summer", "on_peak") == 0.26438
    assert H.rates_on(dt.date(2025, 4, 1)).generation_comparison_table(
        "winter", "off_peak") == 0.12379
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


def case_generation_refuses_on_cca_dates_and_only_the_diagnostic_answers():
    """The trust boundary on the PRIMARY api. rates_on(date).generation(season, tou)
    is the one a caller reaches for by default, and during a CCA period the printed
    generation table is SDG&E's bundled comparison, not the tariff charged — the
    same reason bill_nem and cca_generation already refuse. So generation() itself
    refuses there, naming the date, the period, the provider and the CCA charge,
    and the printed value is reachable only through the accessor whose name says
    what it is. Bundled dates are unaffected, and the two agree there."""
    bundled = H.rates_on(dt.date(2024, 8, 1))                  # SDG&E billed it
    assert bundled.provider == "bundled"
    assert bundled.generation("summer", "on_peak") == 0.38826
    assert bundled.generation_comparison_table("summer", "on_peak") == 0.38826
    for day, season, period, billed, printed in (
            (dt.date(2025, 8, 1), "summer", "7/29/25 - 8/26/25", "223.02", 0.40592),
            (dt.date(2026, 2, 1), "winter", "1/28/26 - 2/26/26", "56.82", 0.20013)):
        rs = H.rates_on(day)
        assert rs.provider == "CCA"
        _raises(lambda r=rs, s=season: r.generation(s, "on_peak"),
                f"generation/{season}/on_peak", str(day), period, "CCA", billed,
                "COMPARISON", "not the tariff charged",
                "generation_comparison_table")
        # and the diagnostic answers, with the printed value
        assert rs.generation_comparison_table(season, "on_peak") == printed
    # the printed CCA comparison rate is NOT the same number as the delivery rate
    # it would have been silently added to, i.e. the refusal is load-bearing
    aug = H.rates_on(dt.date(2025, 8, 1))
    assert aug.generation_comparison_table("summer", "on_peak") != \
        aug.delivery("summer", "on_peak")
    return ("rates_on(date).generation refuses on every CCA date, naming the period "
            "and the CCA charge; only generation_comparison_table() returns the "
            "printed comparison value, and bundled dates are unchanged")


def case_the_aggregations_never_hand_out_a_cca_generation_tariff():
    """The same trust boundary at the AGGREGATE exits, not just at generation().

    RateSet.generation() refuses one cell at a time, but cells() and
    current_vintage() hand out every cell at once, and a caller of those never
    types the word "comparison". So they carry the authority with each value:
    on a CCA date the generation cells come back authority
    printed_comparison_only, .rate None, and SDG&E's printed bundled-generation
    number in .comparison_rate. Pinned here against the SAME literals the
    diagnostic accessor returns, so the boundary is about labelling and not about
    losing the number.

    Also pinned: the old tuple shapes are gone. `rate, tier, note = cells[cell]`
    and `rate, start, end = current_vintage()[cell]` raise TypeError instead of
    binding a printed comparison rate to a variable called `rate`."""
    cca = H.rates_on(dt.date(2026, 2, 1))                # 1/28/26 - 2/26/26, CCA
    assert cca.provider == "CCA"
    cells = cca.cells()
    gen = cells[("generation", "winter", "on_peak")]
    assert gen.authority == H.COMPARISON_ONLY, gen
    assert gen.rate is None and gen.is_charged_tariff is False, gen
    assert gen.comparison_rate == 0.20013, gen           # the printed number, named
    assert gen.comparison_rate == cca.generation_comparison_table(
        "winter", "on_peak"), gen
    assert gen.provider == "CCA" and gen.classification == "comparison", gen
    assert "COMPARISON" in gen.note and "NOT a tariff" in gen.note, gen.note
    # delivery on the very same date is a charged tariff, and it is the UDC
    udc = cells[("delivery", "winter", "on_peak")]
    assert udc.authority == H.CHARGED_TARIFF and udc.is_charged_tariff, udc
    assert udc.rate == cca.delivery("winter", "on_peak"), udc
    assert udc.comparison_rate is None, udc
    # a bundled date is untouched: generation there IS the charged tariff
    bundled = H.rates_on(dt.date(2024, 8, 1)).cells()[
        ("generation", "summer", "on_peak")]
    assert bundled.provider == "bundled", bundled
    assert bundled.authority == H.CHARGED_TARIFF and bundled.rate == 0.38826, bundled
    assert bundled.comparison_rate is None, bundled
    # current_vintage(): the corpus ends inside the CCA era, so NO generation cell
    # has a current charged tariff, and all six say so with the printed number
    # parked in comparison_rate
    cv = H.current_vintage()
    assert len(cv) == 12, len(cv)
    comparisons = {}
    for cell, entry in cv.items():
        if cell[0] == "generation":
            assert entry.authority == H.COMPARISON_ONLY, entry
            assert entry.rate is None and not entry.is_charged_tariff, entry
            assert entry.comparison_rate is not None, entry
            assert entry.provider == "CCA", entry
            comparisons[cell[1:]] = entry.comparison_rate
        else:
            assert entry.authority == H.CHARGED_TARIFF, entry
            assert entry.rate is not None and entry.comparison_rate is None, entry
    assert comparisons == {
        ("summer", "on_peak"): 0.47019, ("summer", "off_peak"): 0.17311,
        ("summer", "super_off_peak"): 0.08147, ("winter", "on_peak"): 0.19990,
        ("winter", "off_peak"): 0.14337, ("winter", "super_off_peak"): 0.07410,
    }, comparisons
    # no charged generation rate is reachable from either aggregation, on any date
    leaked, cca_days = [], 0
    day, end = H.coverage()
    while day <= end:
        rs = H.rates_on(day)
        if rs.provider == "CCA":
            cca_days += 1
            for cell, cv_ in rs.cells().items():
                if cell[0] == "generation" and cv_.rate is not None:
                    leaked.append((day, cell, cv_.rate))
        day += dt.timedelta(days=1)
    assert not leaked, leaked[:5]
    assert cca_days == 547, cca_days               # 2024-12-27..2026-06-26
    # the retired tuple shapes must not silently unpack
    for fn in (lambda: [x for x in gen],
               lambda: gen[0],
               lambda: [x for x in cv[("generation", "winter", "on_peak")]]):
        try:
            fn()
        except TypeError:
            continue
        raise AssertionError("a CellValue/VintageEntry still unpacks like a tuple")
    return ("cells() and current_vintage() label every value: on CCA dates all "
            "generation cells are printed_comparison_only with .rate None and the "
            f"printed number in .comparison_rate (0 charged generation rates "
            f"reachable on any of the {cca_days} CCA days), delivery stays charged "
            "in both eras, and the old tuple unpacking raises TypeError")


def case_the_vintage_artifact_separates_charged_tariffs_from_comparisons():
    """data/rate_vintages.csv must not present a printed CCA-generation comparison
    in the schema a consumer reads as a historical tariff vintage.

    Three separations, all pinned: the section name
    (generation_printed_comparison, so filtering section == "generation" yields
    charged tariffs only), the value column (rate_per_kwh holds charged tariffs,
    comparison_rate_per_kwh holds printed comparisons, never both on one row), and
    an explicit authority column on every row with the provider beside it. The
    "billed on" note wording is gone everywhere: a direct span's note says which
    statements PRINTED the rate, and the authority column says whether printing it
    and charging it were the same act.

    Generation spans are split at the provider break so no row mixes the two
    authorities — the winter on-peak 0.16516 observation is a charged SDG&E tariff
    through 12/26/24 and a printed comparison from 12/27/24, and the artifact
    carries it as two rows saying exactly that."""
    rows = list(csv.DictReader(open(H.DATA / "rate_vintages.csv", newline="")))
    header = (H.DATA / "rate_vintages.csv").read_text().splitlines()[0].split(",")
    assert header == ["section", "season", "tou_period", "vintage_start",
                      "vintage_end", "provider", "authority", "rate_per_kwh",
                      "comparison_rate_per_kwh", "evidence", "note"], header
    cca_start = dt.date(2024, 12, 27)
    counts = {}
    for row in rows:
        a, b = dt.date.fromisoformat(row["vintage_start"]), \
            dt.date.fromisoformat(row["vintage_end"])
        assert a <= b, row
        assert row["authority"] in H.AUTHORITIES, row
        assert row["provider"] in ("bundled", "CCA", "bundled+CCA"), row
        assert "billed on" not in row["note"], row       # the corrected wording
        # never both value columns, and never a comparison number under a name a
        # consumer reads as a tariff
        assert not (row["rate_per_kwh"] and row["comparison_rate_per_kwh"]), row
        if row["authority"] == H.COMPARISON_ONLY:
            assert row["section"] == "generation_printed_comparison", row
            assert row["rate_per_kwh"] == "", row
            assert row["provider"] == "CCA" and a >= cca_start, row
            assert (row["comparison_rate_per_kwh"] == "") == \
                (row["evidence"] == "absent"), row
            assert "NOT a tariff" in row["note"], row
        else:
            assert row["comparison_rate_per_kwh"] == "", row
            if row["section"] == "generation":
                # a charged generation row may not touch a CCA date at all
                assert b < cca_start, row
                assert row["provider"] == "bundled", row
            if row["section"] == "provider":
                assert row["authority"] == H.NOT_A_RATE, row
                assert not row["rate_per_kwh"], row
        counts[(row["section"], row["authority"])] = \
            counts.get((row["section"], row["authority"]), 0) + 1
    assert counts == {
        ("delivery", H.CHARGED_TARIFF): 47,
        ("generation", H.CHARGED_TARIFF): 13,
        ("generation_printed_comparison", H.COMPARISON_ONLY): 36,
        ("base_services_charge", H.CHARGED_TARIFF): 26,
        ("provider", H.NOT_A_RATE): 26,
    }, counts
    # the split at the provider break, both halves, same printed number
    def find(section, season, tp, start):
        hits = [r for r in rows if (r["section"], r["season"], r["tou_period"],
                                    r["vintage_start"]) == (section, season, tp,
                                                            start)]
        assert len(hits) == 1, (section, season, tp, start, hits)
        return hits[0]

    charged = find("generation", "winter", "on_peak", "2024-11-01")
    printed = find("generation_printed_comparison", "winter", "on_peak",
                   "2024-12-27")
    assert (charged["vintage_end"], charged["rate_per_kwh"],
            charged["comparison_rate_per_kwh"]) == ("2024-12-26", "0.16516", ""), charged
    assert (printed["vintage_end"], printed["rate_per_kwh"],
            printed["comparison_rate_per_kwh"]) == ("2025-01-31", "", "0.16516"), printed
    assert "portion of the observed span" in printed["note"], printed
    # the two headline CCA comparison numbers appear ONLY as comparisons
    for value in ("0.20013", "0.40592"):
        where = {r["section"] for r in rows if value in (
            r["rate_per_kwh"], r["comparison_rate_per_kwh"])}
        assert where == {"generation_printed_comparison"}, (value, where)
        assert not any(r["rate_per_kwh"] == value for r in rows), value
    return (f"{len(rows)} vintage rows: {counts[('generation', H.CHARGED_TARIFF)]} "
            f"charged generation spans (all ending before 2024-12-27) carry "
            f"rate_per_kwh, the "
            f"{counts[('generation_printed_comparison', H.COMPARISON_ONLY)]} CCA-era "
            f"spans sit under generation_printed_comparison with an empty "
            f"rate_per_kwh, and no note says 'billed on'")


def case_current_vintage_matches_rates_py_to_the_cent():
    """AC: the engine's final delivery vintage equals rates.py's UDC on all six
    season×TOU cells. Delivery is the charged tariff in both provider eras, so all
    six entries are authority=charged_tariff and their .rate is the number to
    compare. (rates.py's CEA table comes from the CCA's own schedule, which the
    bill artifacts do not carry — see the module docstring.)"""
    cv = H.current_vintage()
    for seas, season in (("S", "summer"), ("W", "winter")):
        for short, long_tp in zip(tuple(R.UDC["S"]),
                                  ("on_peak", "off_peak", "super_off_peak")):
            want = R.UDC[seas][short]
            entry = cv[("delivery", season, long_tp)]
            assert entry.authority == H.CHARGED_TARIFF, entry
            assert entry.is_charged_tariff and entry.comparison_rate is None, entry
            got = entry.rate
            assert abs(got - want) < 0.005, (seas, short, got, want)
            assert got == want, (seas, short, got, want)   # in fact exact
    return "all six UDC season×TOU cells match rates.py exactly, not just to the cent"


def case_rebilling_reproduces_all_26_statements_within_1pct():
    """AC: each statement re-billed from its own per-TOU kWh within ±1%. Note what
    that gate does and does not prove. The reference is the statement's OWN printed
    TOU energy lines and the timeline is built from those same rows, so the
    piecewise residual is zero as an identity — exactly 0 on all 26, and it would
    stay 0 with corrupted rates (case_a_perturbed_rate_... proves that). The check
    with teeth is the corroboration gate in the next case; the share of dollars the
    holdout can price is reported here as a descriptive figure and is deliberately
    NOT asserted against a threshold (see that case's docstring)."""
    periods = [p["period"] for p in H._engine().periods]
    assert len(periods) == 26, len(periods)
    for period in periods:
        r = H.rebill_statement(period)
        assert _is_zero(r["residual"]), (period, r)  # identity, not just ±1%
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
    return (f"26/26 statements re-bill to exactly $0.000000 residual (an identity); "
            f"the holdout re-bill prices {100 * priced / printed:.1f}% of printed "
            f"dollars from the OTHER statements' rates, worst |residual| {worst:.4f}%")


def case_every_corroborable_line_agrees_and_the_rest_is_pinned_as_uncorroborated():
    """THE HOLDOUT GATE. Two halves, and the second one is the honest half.

    (1) Every printed line the rest of the corpus can independently witness must
        agree with that witness EXACTLY — not to the cent, to the digit. 81 lines
        qualify and 81 agree, so the corroborated dollars reconcile to $0.00 on
        every statement.
    (2) The 77 lines that cannot be witnessed are uncorroborated BY CONSTRUCTION,
        and this pins their count and reason per statement rather than averaging
        them away. A rate whose first appearance in the corpus is the line under
        test has no second opinion to appeal to; saying otherwise would be
        inventing corroboration. What the pinning buys is the regression the old
        aggregate could not see: if a line that is corroborated today stops being
        corroborated, its statement's tuple changes and this fails.

    There is no coverage threshold on purpose. A corpus of stable repeated rates
    clears any such threshold while a corrupted new vintage sits entirely outside
    it — which is exactly the pair of negative fixtures below.

    Also pinned: no corroborating witness is ever a DIRECT observation. No other
    statement bills these dates, so every witness is a carried span between two
    flanking statements that printed the same rate, and the artifact says "carried"
    rather than implying a stronger kind of evidence than exists."""
    got, exact = _corroboration_snapshot()
    assert exact, "a corroborated line disagrees with its witness"
    assert got == CORROBORATION, {k: (CORROBORATION[k], v)
                                  for k, v in got.items() if CORROBORATION[k] != v}
    corr = unc = direct = 0
    providers = {p["period"]: p["provider"] for p in H._engine().periods}
    for period in CORROBORATION:
        c = H.corroboration(period)
        h = H.rebill_holdout(period)
        # every row here is a PRINTED line, and each one says whether printing it
        # was the same act as charging it — on a CCA statement the generation rows
        # are SDG&E's comparison table, and printed_rate/witness_rate are that
        for row in c["rows"]:
            assert row["authority"] == (
                H.COMPARISON_ONLY if row["cell"][0] == "generation"
                and providers[period] == "CCA" else H.CHARGED_TARIFF), (period, row)
        assert c["disagree_rows"] == 0, (period, c["disagree_rows"])
        # the corroborated dollars are the holdout residual's whole basis
        assert abs(h["residual"]) < 0.005, (period, h)
        assert h["uncorroborated_rows"] == c["uncorroborated_rows"], (period, h)
        assert set(c["reasons"]) <= set(H.UNCORROBORATED_REASONS), c["reasons"]
        corr += c["corroborated_rows"]
        unc += c["uncorroborated_rows"]
        direct += c["direct_witness_rows"]
    assert (corr, unc, direct) == (81, 77, 0), (corr, unc, direct)
    _raises(lambda: H.corroboration("1/1/26 - 1/2/26"), "no billing period")
    # the committed artifact carries the same five numbers per statement, so the
    # published table is gated too, not just the API behind it
    for row in csv.DictReader(open(H.DATA / "rate_rebilling_residuals.csv",
                                   newline="")):
        assert (int(row["holdout_corroborated_rows"]),
                int(row["holdout_corroborated_agree_rows"]),
                int(row["holdout_corroborated_disagree_rows"]),
                int(row["holdout_uncorroborated_rows"]),
                row["holdout_uncorroborated_reasons"]) == \
            CORROBORATION[row["period"]], row
    return (f"{corr} printed lines are corroborated by the rest of the corpus and "
            f"all {corr} agree exactly (holdout residual $0.00 on 26/26); the other "
            f"{unc} are pinned per statement as uncorroborated-by-construction with "
            f"their reason, and no witness is stronger than a carried span")


def _period_level_holdout(period):
    """The RETIRED period-level holdout, rebuilt here so the guards can MEASURE what
    the statement-level one changes instead of asserting the code agrees with itself.

    Identical to _Engine(exclude_statement=...) in every respect but the rows it
    withholds: by PERIOD, which leaves a sibling period printed on the same
    statement in the witness pool. Nothing in rates_history.py builds this."""
    eng = H._engine()
    old = H._Engine(eng.periods, eng.tou)     # segment DATES from every statement
    old.exclude_statement = period           # affects gap NOTE wording only
    old.timelines = old._timelines([r for r in eng.tou if r["period"] != period])
    return old


def _witness_under(engine, row):
    """(corroborated?, witness statement_dates) for one corroboration row under any
    holdout timeline — the same one-span test corroboration() itself applies."""
    first = engine.span_at(row["cell"], row["start"])
    last = engine.span_at(row["cell"], row["end"])
    ok = first is last and first.tier != "absent"
    return ok, (first.sources if ok else ()), (first.rate if ok else None)


def case_the_holdout_unit_is_the_statement_not_the_billing_period():
    """The holdout's unit of independence is the STATEMENT (the file), not the
    billing period, and the witness provenance is asserted rather than assumed.

    One statement can print two billing periods — the 2025-10-31 statement prints
    9/26/25-9/30/25 and 10/1/25-10/27/25 — and those two periods' rate rows come
    from one parse of one PDF down one extraction path. They share every failure
    mode, so a sibling cannot be an independent second opinion about the other. If
    the holdout withheld only the period under test, the sibling would stay in the
    witness pool and could mark the held-out lines corroborated: a parser error
    shared across that statement would corroborate itself, while the artifact
    described the witness as another statement.

    So corroboration() exposes which statements are behind each witness, and this
    pins three things:
      * the inventory: 26 periods, 25 statements, one of them carrying two periods;
      * on EVERY statement, no corroborated line's witness includes the held-out
        statement (the universal form, so a second multi-period statement is
        covered the day it arrives);
      * the difference is real: under the retired period-level unit, 4 lines on the
        2025-10-31 statement listed 2025-10-31 itself among their witnesses. Their
        verdict does not change here — the flanking statements print the same
        generation rates either way, so the pinned counts stay 81/77 — but the
        witness list does, and the fixture below shows a corpus where the verdict
        changes too."""
    eng = H._engine()
    by_stmt = {}
    for p in eng.periods:
        by_stmt.setdefault(p["statement_date"], []).append(p["period"])
    multi = {s: tuple(v) for s, v in by_stmt.items() if len(v) > 1}
    assert len(eng.periods) == 26 and len(by_stmt) == 25, (len(eng.periods),
                                                           len(by_stmt))
    assert multi == MULTI_PERIOD_STATEMENTS, multi
    # every TOU row's statement_date agrees with bill_periods_electric (the loader
    # refuses otherwise — see below), so "the statement" is a well-defined unit
    assert {r["statement_date"] for r in eng.tou} == set(by_stmt), "statement mismatch"

    # (1) universal: nothing is ever its own witness, on any statement
    self_witnessed = []
    for p in eng.periods:
        c = H.corroboration(p["period"])
        assert c["held_out_statement"] == p["statement_date"], c["held_out_statement"]
        for row in c["rows"]:
            if row["status"] == "corroborated":
                assert row["witness_statements"], (p["period"], row)
                if c["held_out_statement"] in row["witness_statements"]:
                    self_witnessed.append((p["period"], row["cell"]))
            else:
                assert row["witness_statements"] == (), (p["period"], row)
    assert self_witnessed == [], self_witnessed

    # (2) the sibling periods share ONE holdout timeline, and it withholds all 12 of
    # that statement's rows, not just the 6 of the period under test
    stmt, siblings = next(iter(MULTI_PERIOD_STATEMENTS.items()))
    engines = {H._holdout_engine(
        next(p["statement_date"] for p in eng.periods if p["period"] == per))
        for per in siblings}
    assert len(engines) == 1, "the sibling periods must share one holdout engine"
    hold = engines.pop()
    assert hold.exclude_statement == stmt, hold.exclude_statement
    withheld = [r for r in eng.tou if r["statement_date"] == stmt]
    assert len(withheld) == 12 and {r["period"] for r in withheld} == set(siblings), \
        len(withheld)

    # (3) the retired unit self-witnessed 4 lines on that statement; measure it
    leaked = []
    for p in eng.periods:
        old = _period_level_holdout(p["period"])
        for row in H.corroboration(p["period"])["rows"]:
            ok, sources, _ = _witness_under(old, row)
            if ok and p["statement_date"] in sources:
                leaked.append((p["period"], "/".join(row["cell"])))
                assert row["witness_statements"] and \
                    p["statement_date"] not in row["witness_statements"], row
    assert leaked == [
        ("9/26/25 - 9/30/25", "generation/summer/on_peak"),
        ("9/26/25 - 9/30/25", "generation/summer/super_off_peak"),
        ("10/1/25 - 10/27/25", "generation/summer/on_peak"),
        ("10/1/25 - 10/27/25", "generation/summer/super_off_peak"),
    ], leaked

    # (4) statement_date is the unit, so the two artifacts must agree on it
    def break_statement_date(rows):
        for r in rows:
            if r["period"] == "10/1/25 - 10/27/25":
                r["statement_date"] = "2025-11-30"
    with _corrupted_corpus(break_statement_date):
        _raises(H._engine, "10/1/25 - 10/27/25", "'2025-11-30'", "'2025-10-31'",
                "unit of independence", "one statement can print two")
    return (f"the holdout unit is the statement: {len(by_stmt)} statements over "
            f"{len(eng.periods)} periods, one of them ({stmt}) printing two, whose "
            f"12 rows are withheld together; no corroborated line anywhere is "
            f"witnessed by its own statement, where the retired period-level unit "
            f"self-witnessed {len(leaked)}")


def case_a_shared_statement_error_cannot_corroborate_its_sibling_period():
    """NEGATIVE fixture for the unit of independence: the parser error the two units
    disagree about.

    The delivery summer on-peak rate steps 0.29773 -> 0.29426 on 10/1/25, and BOTH
    sides of that step are printed on the one 2025-10-31 statement. So the plausible
    misread is the second period inheriting the first's rate — one file, two adjacent
    blocks, one wrong number carried down. Inject exactly that (10/1/25-10/27/25
    delivery on-peak at 0.29773) and the two holdout units disagree about the
    9/26/25-9/30/25 line:
      * period-level (retired): the gap 9/26..9/30 is now flanked by 0.29773 on both
        sides — 8/27/25-9/25/25 on one side and the corrupted SIBLING on the other —
        so the line comes back corroborated, agreeing exactly, with 2025-10-31 in its
        own witness list. The corpus would have reported one more agreeing line than
        it does clean: a false pass manufactured by the statement under test.
      * statement-level (current): both sibling periods are withheld, the flanking
        witnesses are 0.29773 and 0.29426, they disagree, and the line is reported
        uncorroborated with the reason that names the undated change.
    Nothing else moves: the corruption sits on lines that are uncorroborated either
    way, so the pinned counts are unchanged — which is the point. The gate does not
    reward the corruption, and it does not have to notice it to stay honest."""
    period, sibling = "9/26/25 - 9/30/25", "10/1/25 - 10/27/25"
    cell = ("delivery", "summer", "on_peak")
    printed = [r for r in H._engine().tou
               if r["period"] == period and (r["section"], r["season"],
                                             r["tou_period"]) == cell][0]["rate"]
    assert printed == 0.29773, printed

    def carry_the_rate_down(rows):
        hits = [r for r in rows if r["period"] == sibling
                and (r["section"], r["season"], r["tou_period"]) == cell]
        assert len(hits) == 1 and hits[0]["rate_per_kwh"] == "0.29426", hits
        hits[0]["rate_per_kwh"] = f"{printed:.5f}"

    with _corrupted_corpus(carry_the_rate_down):
        row = [r for r in H.corroboration(period)["rows"] if r["cell"] == cell][0]
        # the current unit: uncorroborated, and nothing claims a witness
        assert row["status"] == "uncorroborated", row
        assert row["reason"] == \
            "flanking_witnesses_disagree_the_change_is_undated", row
        assert (row["witness_rate"], row["witness_statements"]) == (None, ()), row
        # the retired unit: corroborated, agreeing, on the strength of its own file
        ok, sources, rate = _witness_under(_period_level_holdout(period), row)
        assert ok and rate == printed, (ok, rate)
        assert "2025-10-31" in sources, sources
        # and the pinned counts are untouched by the corruption
        got, exact = _corroboration_snapshot()
        assert exact, "no corroborated line is touched by this corruption"
        assert got == CORROBORATION, {k: v for k, v in got.items()
                                      if CORROBORATION[k] != v}
    assert _corroboration_snapshot() == (CORROBORATION, True)
    return ("a rate misread across one statement's two periods would have been "
            "corroborated — exactly, at 0.29773 — by that statement's own sibling "
            "period under the retired period-level holdout; the statement-level "
            "holdout reports the line uncorroborated instead")


def case_split_statement_mid_cycle_changes_are_recorded_as_uncorroborated():
    """The five split statements are where the corpus is weakest, so the artifact has
    to say so line by line rather than let a statement look validated.

    A split statement prints two rate segments; the cells whose rate actually MOVED
    between them are the mid-cycle change itself. Holding that statement out cannot
    corroborate those lines — the change is printed nowhere else, so the flanking
    witnesses disagree and the date of the change inside the gap is undetermined.
    That is pinned here per statement, and it is 0 corroborated lines out of every
    differing cell on all five. Three of the five have no corroborated line at all,
    which is why their holdout coverage reads 0% in the artifact — recorded as a
    hole, not as agreement."""
    split = [p["period"] for p in H._engine().periods
             if H.rebill_statement(p["period"])["n_segments"] > 1]
    assert set(split) == set(SPLIT_DIFFERING), (split, list(SPLIT_DIFFERING))
    got = {}
    for period in split:
        c = H.corroboration(period)
        got[period] = (c["differing_cells"], c["differing_corroborated_rows"],
                       c["differing_uncorroborated_rows"])
        for row in c["rows"]:
            if row["differing_cell"]:
                assert row["status"] == "uncorroborated", (period, row)
                assert row["reason"] == \
                    "flanking_witnesses_disagree_the_change_is_undated", (period, row)
    assert got == SPLIT_DIFFERING, got
    zero = [p for p in split if H.corroboration(p)["corroborated_rows"] == 0]
    assert zero == ["1/28/25 - 2/26/25", "12/27/25 - 1/27/26",
                    "3/28/26 - 4/28/26"], zero
    # and the artifact carries the same numbers, per statement
    rows = {r["period"]: r for r in csv.DictReader(
        open(H.DATA / "rate_rebilling_residuals.csv", newline=""))}
    for period, (cells, dcorr, dunc) in SPLIT_DIFFERING.items():
        row = rows[period]
        assert int(row["split_differing_cells"]) == cells, row
        assert int(row["split_differing_corroborated_rows"]) == dcorr, row
        assert int(row["split_differing_uncorroborated_rows"]) == dunc, row
    return ("on all 5 split statements every cell whose rate moved mid-cycle is "
            "recorded uncorroborated (0 corroborated lines among them, reason: the "
            "flanking witnesses disagree and nothing else dates the change); 3 of "
            "the 5 have no corroborated line at all")


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
        assert _is_zero(piece["residual_pct"], ZERO_PCT), (period, piece)
        if vint["residual_pct"] is None or _is_zero(vint["residual_pct"], ZERO_PCT):
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
      * the holdout re-bill, priced from every other statement, moves by exactly
        the perturbation (371 kWh × $0.05 = $18.55);
      * the corroboration gate fails in both of its halves: that line is
        corroborated and now disagrees with its witness, AND the corrupted rate
        makes this statement a bad witness for its two neighbours, so a
        previously-corroborated line on each of them falls out of coverage. Three
        of the pinned tuples move. That is the regression the retired aggregate
        threshold would have absorbed."""
    period = "7/29/25 - 8/26/25"
    cell = ("delivery", "summer", "on_peak")
    base = [r for r in H._engine().tou
            if r["period"] == period and (r["section"], r["season"],
                                          r["tou_period"]) == cell and r["kwh"] > 0]
    assert len(base) == 1, base
    kwh, rate = base[0]["kwh"], base[0]["rate"]
    assert _is_zero(H.rebill_holdout(period)["residual"])   # clean before

    def mutate(rows):
        hits = [r for r in rows if r["period"] == period
                and (r["section"], r["season"], r["tou_period"]) == cell
                and float(r["kwh"]) > 0]
        assert len(hits) == 1, hits
        hits[0]["rate_per_kwh"] = f"{rate + 0.05:.5f}"

    with _corrupted_corpus(mutate):
        piece = H.rebill_statement(period)
        hold = H.rebill_holdout(period)
        assert _is_zero(piece["residual"]), piece         # blind, by construction
        assert hold["priced_pct"] == 100.0, hold
        assert abs(hold["residual"] - (-kwh * 0.05)) < 1e-9, hold
        assert abs(hold["residual_pct"]) > 4.8, hold
        # the gate: exactness broken on this statement, coverage lost on the two
        # neighbours that were relying on it as a witness
        got, exact = _corroboration_snapshot()
        assert not exact, "the perturbed line must disagree with its witness"
        assert got[period] == (4, 3, 1, 0, ""), got[period]
        moved = {k for k in got if got[k] != CORROBORATION[k]}
        assert moved == {period, "6/27/25 - 7/28/25", "8/27/25 - 9/25/25"}, moved
        for neighbour in ("6/27/25 - 7/28/25", "8/27/25 - 9/25/25"):
            assert got[neighbour] == (
                3, 3, 0, 1,
                "flanking_witnesses_disagree_the_change_is_undated:1"), got[neighbour]
        bad = [r for r in H.corroboration(period)["rows"] if r["agrees"] is False]
        assert len(bad) == 1 and bad[0]["cell"] == cell, bad
        assert abs(bad[0]["printed_usd"] - bad[0]["witness_usd"] - kwh * 0.05) < 1e-9
    assert _is_zero(H.rebill_holdout(period)["residual"])   # and clean after
    assert _corroboration_snapshot() == (CORROBORATION, True)
    return (f"a +$0.05/kWh corruption leaves the reconstruction residual at $0, "
            f"moves the holdout residual to ${hold['residual']:.2f} "
            f"({hold['residual_pct']:.2f}% of the priced lines), and moves 3 of the "
            f"26 pinned corroboration tuples")


def case_a_common_mode_shift_of_one_repeated_vintage_is_invisible_to_the_holdout():
    """NEGATIVE fixture proving issue #27's premise, not just asserting it: the
    holdout gate's blind spot to a COMMON-MODE parser regression.

    delivery/summer/on_peak's $0.26438 rate is a genuine repeated historical
    vintage: it is printed, unchanged, on five consecutive 2024 statements. Three
    of those five (6/26/24-7/25/24, 7/26/24-8/26/24, 8/27/24-9/25/24) are already
    corroborated-and-agreeing in the real corpus (the other two sit at a corpus
    edge / a mid-cycle split and are uncorroborated by construction either way —
    see CORROBORATION). Shift EVERY one of the five occurrences by the identical
    +$0.05/kWh, exactly the shape of bug issue #27 describes ("a parser regression
    shifts every occurrence of a repeated historical vintage by the same amount"),
    and confirm the three corroborated lines stay corroborated AND agreeing: the
    held-out statement and both its flanking witnesses now all carry the same
    wrong value, so nothing here can tell the difference between this and a real,
    correctly-repeated $0.26438 vintage. That is the literal blind spot this
    module's docstring names ("THE HOLDOUT GATE'S BLIND SPOT") — this case is the
    evidence it is real, not hypothetical, and it is why the fix (parse_bills.py's
    charge-line cross-foot, tested in test_parse_bills.py) had to live upstream of
    this module rather than here: nothing in bill_tou_detail.csv could have told
    the two apart."""
    cell = ("delivery", "summer", "on_peak")
    target = 0.26438
    clean_corroborated = ["6/26/24 - 7/25/24", "7/26/24 - 8/26/24", "8/27/24 - 9/25/24"]
    all_occurrences = ["5/25/24 - 6/25/24"] + clean_corroborated + ["9/26/24 - 10/25/24"]
    # pin the starting shape so a future corpus edit can't silently invalidate this
    # case's premise (a real repeated vintage with >=3 corroborated occurrences)
    for period in clean_corroborated:
        row = next(r for r in H.corroboration(period)["rows"] if r["cell"] == cell)
        assert row["status"] == "corroborated" and row["agrees"] is True, (period, row)

    def mutate(rows):
        hit = 0
        for r in rows:
            if ((r["section"], r["season"], r["tou_period"]) == cell
                    and r["period"] in all_occurrences
                    and abs(float(r["rate_per_kwh"]) - target) < 1e-9
                    and float(r["kwh"]) > 0):
                r["rate_per_kwh"] = f"{target + 0.05:.5f}"
                hit += 1
        assert hit == 5, hit    # every printed occurrence of this vintage, shifted

    with _corrupted_corpus(mutate):
        for period in clean_corroborated:
            row = next(r for r in H.corroboration(period)["rows"] if r["cell"] == cell)
            assert row["status"] == "corroborated", (period, row)
            assert row["agrees"] is True, (
                period, row, "the holdout should NOT notice — every witness was "
                "shifted by the same amount as the line under test")
            assert row["printed_rate"] == row["witness_rate"] == target + 0.05
    # and clean again once the mutation is undone (no cross-test leakage)
    for period in clean_corroborated:
        row = next(r for r in H.corroboration(period)["rows"] if r["cell"] == cell)
        assert row["agrees"] is True and row["printed_rate"] == target
    return (f"a uniform +$0.05/kWh shift applied to all 5 printed occurrences of "
            f"the {'/'.join(cell)} ${target:.5f} vintage leaves all "
            f"{len(clean_corroborated)} corroborable occurrences reporting "
            f"corroborated=True, agrees=True — the holdout gate raises zero "
            f"anomalies against a corpus whose published rate is wrong")


def case_a_corruption_on_an_uncorroborable_line_is_reported_as_uncorroborated():
    """NEGATIVE fixture, the honest counterpart: corrupt a line the corpus CANNOT
    corroborate and check the machinery says so instead of scoring a pass.

    Target: summer off-peak delivery on the last statement (5/29/26-6/26/26). That
    cell's only positive-kWh line anywhere in the corpus is this one — every earlier
    summer off-peak bucket netted negative and printed $0.00000 — so no witness
    exists, and no amount of holding-out can produce one. Add $0.05/kWh and:
      * nothing moves: the reconstruction residual stays $0, the holdout has no
        priced basis at all (residual_pct None, coverage 0%), and every pinned
        corroboration tuple is unchanged, because a corruption here is invisible;
      * but the line is not silently accepted either. Its status is
        "uncorroborated" with reason no_witness_anywhere_for_this_cell, in the
        artifact as well as in the API, so a reader sees a hole rather than a tick.
    This is the shape the retired >60%-coverage gate mis-scored: aggregate coverage
    stayed comfortably high while a line like this sat entirely outside it."""
    period, cell = "5/29/26 - 6/26/26", ("delivery", "summer", "off_peak")
    before = [r for r in H.corroboration(period)["rows"] if r["cell"] == cell]
    assert len(before) == 1 and before[0]["status"] == "uncorroborated", before
    assert before[0]["reason"] == "no_witness_anywhere_for_this_cell", before

    def mutate(rows):
        hits = [r for r in rows if r["period"] == period
                and (r["section"], r["season"], r["tou_period"]) == cell
                and float(r["kwh"]) > 0]
        assert len(hits) == 1, hits
        hits[0]["rate_per_kwh"] = f"{float(hits[0]['rate_per_kwh']) + 0.05:.5f}"

    with _corrupted_corpus(mutate):
        assert _is_zero(H.rebill_statement(period)["residual"])
        hold = H.rebill_holdout(period)
        assert (hold["residual_pct"], hold["priced_pct"]) == (None, 0.0), hold
        got, exact = _corroboration_snapshot()
        assert exact, "no corroborated line is touched by this corruption"
        assert got == CORROBORATION, {k: v for k, v in got.items()
                                      if CORROBORATION[k] != v}
        row = [r for r in H.corroboration(period)["rows"] if r["cell"] == cell][0]
        assert row["printed_rate"] == before[0]["printed_rate"] + 0.05, row
        assert row["status"] == "uncorroborated", row
        assert row["reason"] == "no_witness_anywhere_for_this_cell", row
        assert (row["witness_rate"], row["agrees"]) == (None, None), row
        # and the artifact this corpus would publish says the same thing
        with tempfile.TemporaryDirectory() as d:
            written = {r["period"]: r for r in csv.DictReader(
                open(H._write_artifacts(d)[1], newline=""))}[period]
        assert written["holdout_corroborated_rows"] == "0", written
        assert written["holdout_uncorroborated_reasons"] == (
            "last_witness_is_earlier_than_this_statement:8;"
            "no_witness_anywhere_for_this_cell:2"), written
        assert written["holdout_priced_pct"] == "0.00", written
    return ("a corruption on a line no other statement witnesses moves nothing, and "
            "is reported as uncorroborated (no_witness_anywhere_for_this_cell) in "
            "both the API and the artifact — never as agreement")


def case_a_malformed_printed_rate_on_a_billed_bucket_is_refused():
    """NEGATIVE fixture for the missing-rate-cell boundary (AC5). The timeline treats
    every positive-kWh row as a direct rate observation, so the row's rate has to be
    validated before any timeline exists: a parser regression writing 0, a negative,
    NaN or inf into rate_per_kwh on a BILLED bucket would otherwise be published as
    a directly-evidenced free (or negative) tariff, tier "direct", with no absence
    flag anywhere. The loader refuses the whole corpus and names the period and the
    cell. The legitimate $0.00000 rows — net-negative buckets, which carry no rate
    evidence — are untouched by this: the corpus as committed passes."""
    period, cell = "7/29/25 - 8/26/25", ("delivery", "summer", "on_peak")

    def corrupt_to(value):
        def mutate(rows):
            hits = [r for r in rows if r["period"] == period
                    and (r["section"], r["season"], r["tou_period"]) == cell
                    and float(r["kwh"]) > 0]
            assert len(hits) == 1, hits
            hits[0]["rate_per_kwh"] = value
        return mutate

    for value in ("0.00000", "nan", "inf", "-0.29773"):
        with _corrupted_corpus(corrupt_to(value)):
            # construction itself must fail — not the lookup, not the re-bill
            _raises(H._engine, period, "delivery/summer/on_peak", "371.0 kWh",
                    repr(value), "finite, strictly positive", "parser regression")
            _raises(lambda: H.rates_on(dt.date(2025, 8, 1)), period, repr(value))
            _raises(lambda: H.rebill_statement(period), period, repr(value))
            _raises(lambda: H.corroboration(period), period, repr(value))
    # a net-negative bucket printing $0.00000 is legitimate and still loads
    zeros = [r for r in H._engine().tou if r["kwh"] <= 0 and r["rate"] == 0.0]
    assert H.rates_on(dt.date(2025, 8, 1)).delivery("summer", "on_peak") == 0.29773
    return (f"a billed bucket whose printed rate is 0, NaN, inf or negative is "
            f"refused at load, naming the period and the cell, on all four shapes; "
            f"the {len(zeros)} legitimate $0.00000 net-negative rows still load")


def case_a_mis_associated_segment_is_caught_or_pinned():
    """NEGATIVE fixture, part two: mis-associate a rate segment.

    Reassigning ONE cell's segment ids inside a split statement is caught at engine
    construction — the printed day counts for that segment then disagree, and the
    message names the statement, the section, the season and both day counts.

    Swapping BOTH segments' date ranges consistently across a whole (statement,
    section) contradicts no rate: the segment→date mapping IS the bill's own
    ordering, the only statement that dates this mid-cycle change is the corrupted
    one, and all 10 of its billed lines are uncorroborated-by-construction before
    and after the swap (0% holdout coverage, with a reason on every line). So no
    dollar residual and no disagreement can appear — pinned here rather than
    implied away.

    What the swap DOES move is corroboration coverage on the two adjacent
    statements: the swapped dates relocate this statement's observed rate
    boundaries, so 2 lines on 11/26/25-12/26/25 and 2 on 1/28/26-2/26/26 stop
    having a witness that spans their printed segment. Their pinned tuples change,
    and the gate fails on that — a weaker signal than a disagreement, and an honest
    one: the corpus cannot say the swap is wrong, only that it costs corroboration
    elsewhere."""
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
        assert _is_zero(piece["residual"]), piece
        assert hold["priced_pct"] == 0.0 and hold["uncorroborated_rows"] == 10, hold
        got, exact = _corroboration_snapshot()
        assert exact, "nothing in the corpus can contradict the swapped dates"
        assert got[period] == CORROBORATION[period] == (
            0, 0, 0, 10,
            "flanking_witnesses_disagree_the_change_is_undated:10"), got[period]
        moved = {k: got[k] for k in got if got[k] != CORROBORATION[k]}
        assert moved == {
            "11/26/25 - 12/26/25": (
                2, 2, 0, 2, "flanking_witnesses_disagree_the_change_is_undated:2"),
            "1/28/26 - 2/26/26": (
                2, 2, 0, 2, "flanking_witnesses_disagree_the_change_is_undated:2"),
        }, moved
    return ("a reassigned segment id fails closed at construction (" +
            msg.split(": ")[-1] + "); a consistent whole-section date-range swap "
            "contradicts no rate — all 10 of that statement's lines are "
            "uncorroborated-by-construction either way — but it costs 2+2 "
            "corroborated lines on the two adjacent statements, which the pinned "
            "counts catch")


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


def case_an_all_export_statement_serialises_instead_of_crashing():
    """A statement with no positive TOU basis must not break the generator.

    rebill_statement returns residual_pct=None when a mode can price nothing --
    every bucket net-exported, so there is no positive basis to divide by. That
    is a legitimate statement shape for a high-production month, and the artifact
    the section-9 gate regenerates has to survive it: the percentage serialises
    empty and the statement sorts as zero residual rather than raising TypeError
    on abs(None).
    """
    # A mid-corpus summer statement: its cells are observed by other summer
    # statements either side, so zeroing its lines leaves every cell evidenced
    # and isolates the one thing under test — an unpriceable statement.
    victim = "7/29/25 - 8/26/25"

    def mutate(rows):
        # A real all-export statement prints credit lines the way every other
        # net-negative bucket in the corpus does: negative kWh at $0.00000. The
        # loader enforces that pairing, so the fixture has to honour it.
        for r in rows:
            if r["period"] == victim and float(r["kwh"]) > 0:
                r["kwh"], r["rate_per_kwh"] = f"-{r['kwh']}", "0.00000"

    with _corrupted_corpus(mutate):
        piece = H.rebill_statement(victim)
        assert piece["printed_basis"] == 0.0, piece["printed_basis"]
        assert piece["residual_pct"] is None, piece["residual_pct"]
        with tempfile.TemporaryDirectory() as d:
            paths = H._write_artifacts(pathlib.Path(d))
            body = [r for r in csv.DictReader(open(paths[1], newline=""))
                    if r["period"] == victim]
        assert len(body) == 1, body
        assert body[0]["timeline_residual_pct"] == "", body[0]
        assert body[0]["worst_residual"] == "", (
            "an unpriceable statement must not be crowned worst on a None residual")
    return "a statement with no positive TOU basis serialises an empty percentage"


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
                 "netting_collapse_residual_pct", "cca_generation_gap_usd",
                 # the corroboration status, per statement, with its reasons
                 "holdout_corroborated_rows", "holdout_corroborated_agree_rows",
                 "holdout_corroborated_disagree_rows", "holdout_uncorroborated_rows",
                 "holdout_uncorroborated_reasons", "split_differing_cells",
                 "split_differing_corroborated_rows",
                 "split_differing_uncorroborated_rows"):
        assert name in header, (name, header)
    # names that describe the wrong reference or hide why a line went unpriced
    assert not any(c in header for c in ("billed_energy_usd", "rebilled_energy_usd",
                                         "collapsed_residual_pct",
                                         "holdout_unpriced_rows")), header
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
    case_generation_refuses_on_cca_dates_and_only_the_diagnostic_answers,
    case_the_aggregations_never_hand_out_a_cca_generation_tariff,
    case_the_vintage_artifact_separates_charged_tariffs_from_comparisons,
    case_current_vintage_matches_rates_py_to_the_cent,
    case_rebilling_reproduces_all_26_statements_within_1pct,
    case_every_corroborable_line_agrees_and_the_rest_is_pinned_as_uncorroborated,
    case_the_holdout_unit_is_the_statement_not_the_billing_period,
    case_a_shared_statement_error_cannot_corroborate_its_sibling_period,
    case_split_statement_mid_cycle_changes_are_recorded_as_uncorroborated,
    case_rate_only_vintage_collapse_worsens_the_split_statements,
    case_netting_collapse_is_a_separate_counterfactual,
    case_a_perturbed_rate_is_invisible_to_the_identity_but_caught_by_the_holdout,
    case_a_common_mode_shift_of_one_repeated_vintage_is_invisible_to_the_holdout,
    case_a_corruption_on_an_uncorroborable_line_is_reported_as_uncorroborated,
    case_a_malformed_printed_rate_on_a_billed_bucket_is_refused,
    case_a_mis_associated_segment_is_caught_or_pinned,
    case_cca_generation_gap_is_recorded_as_data,
    case_provider_break_is_where_the_bills_put_it,
    case_an_all_export_statement_serialises_instead_of_crashing,
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
