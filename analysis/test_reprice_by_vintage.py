#!/usr/bin/env python3
"""Tests for reprice_by_vintage.py (issue #30).

reprice_by_vintage.py's own module-level imports (rates, rates_history,
billing_model_nem) are private-data-free, so the module imports cleanly on any
checkout. The fail-closed loader (_load_periods) and the pure aggregation
(_aggregate) are tested against FABRICATED small csv/dict inputs, exercising
the script's own code paths rather than hand-recomputing the arithmetic. The
one case that needs the real private Green Button archive gates on its
presence and SKIPs rather than fails -- this checkout DOES have it staged, so
that case runs for real.

Run from the repo root:  ./.venv/bin/python analysis/test_reprice_by_vintage.py
"""
import csv
import datetime as dt
import glob
import json
import pathlib
import sys
import tempfile

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import reprice_by_vintage as rbv  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button archive). Counted as neither pass nor fail."""


def _require_archive():
    files = sorted(glob.glob(USAGE_GLOB))
    if not files:
        raise SkipCase(f"needs the private Green Button archive ({USAGE_GLOB}), "
                       "which this checkout does not have")
    return files[0]


# ---------------------------------------------------------------------------
# Fabricated-CSV helpers for the Step 1 fail-closed tests.
# ---------------------------------------------------------------------------
BPE_HEADER = ["statement_date", "period", "days", "generation_provider", "net_kwh",
              "gross_kwh", "sdge_delivery", "cca_generation", "current_charges",
              "base_services_charge", "monthly_service_fee", "fixed_charge_total"]
SUMMARY_HEADER = ["period", "days", "net_kwh", "gross_kwh", "sdge_delivery",
                  "cca_generation", "current_charges"]


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _bpe_row(period, days, provider="CCA", net=100.0, gross=200.0, delivery=20.0,
            gen=10.0, current=None, fixed=16.0):
    if current is None:
        current = delivery + gen
    return ["2026-01-01", period, days, provider, net, gross, delivery, gen,
            current, "", "", fixed]


def _summary_row(period, days, net=100.0, gross=200.0, delivery=20.0, gen=10.0,
                 current=None):
    if current is None:
        current = delivery + gen
    return [period, days, net, gross, delivery, gen, current]


class _TempCorpus:
    """Context manager patching rbv.PERIODS_CSV / rbv.SUMMARY_CSV to fabricated
    files, restoring the real (committed) paths on exit."""

    def __init__(self, bpe_rows, summary_rows):
        self.bpe_rows = bpe_rows
        self.summary_rows = summary_rows

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = pathlib.Path(self._tmp.name)
        bpe_path, summary_path = d / "bpe.csv", d / "summary.csv"
        _write_csv(bpe_path, BPE_HEADER, self.bpe_rows)
        _write_csv(summary_path, SUMMARY_HEADER, self.summary_rows)
        self._saved = (rbv.PERIODS_CSV, rbv.SUMMARY_CSV)
        rbv.PERIODS_CSV, rbv.SUMMARY_CSV = bpe_path, summary_path
        return self

    def __exit__(self, *exc):
        rbv.PERIODS_CSV, rbv.SUMMARY_CSV = self._saved
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# (a) Step 1 fail-closed tests -- fabricated CSVs, no private data needed.
# ---------------------------------------------------------------------------
@case
def case_load_periods_accepts_a_clean_matching_corpus():
    """Sanity check the fabrication helpers themselves before testing failure
    paths against them: a clean, internally-consistent 2-period fabricated
    corpus must load without raising."""
    # 1/1/26-6/30/26 (181 real days) + 7/1/26-12/31/26 (184 real days) = a
    # genuinely contiguous, gap-free 365-day span -- both the days-sum-365
    # gate AND the contiguity check (calendar span must equal the declared
    # day-count sum) need to pass here, unlike the failure-path tests below,
    # which only need to clear whichever single gate they're testing.
    p1 = "1/1/26 - 6/30/26"
    p2 = "7/1/26 - 12/31/26"
    with _TempCorpus(
        bpe_rows=[_bpe_row(p1, 181), _bpe_row(p2, 184)],
        summary_rows=[_summary_row(p1, 181), _summary_row(p2, 184)],
    ):
        periods = rbv._load_periods()
    assert len(periods) == 2
    assert {p["period"] for p in periods} == {p1, p2}
    return "a clean fabricated 2-period corpus loads without raising"


@case
def case_load_periods_rejects_a_non_cca_provider():
    """Step 1's CCA-boundary assertion: a fabricated row with
    generation_provider='bundled' among otherwise-CCA rows must SystemExit,
    naming the offending period."""
    p1, p2 = "1/1/26 - 1/30/26", "1/31/26 - 3/1/26"
    with _TempCorpus(
        bpe_rows=[_bpe_row(p1, 30, provider="CCA"),
                  _bpe_row(p2, 30, provider="bundled")],
        summary_rows=[_summary_row(p1, 30), _summary_row(p2, 30)],
    ):
        try:
            rbv._load_periods()
            raise AssertionError("expected SystemExit for a non-CCA period")
        except SystemExit as e:
            assert p2 in str(e), str(e)
    return "a fabricated non-CCA period among the 13-period corpus raises SystemExit naming it"


@case
def case_load_periods_rejects_a_period_set_mismatch():
    """Step 1's cross-artifact assertion: electric_bill_summary.csv naming a
    period bill_periods_electric.csv does not have must SystemExit."""
    p1, p2 = "1/1/26 - 1/30/26", "1/31/26 - 3/1/26"
    with _TempCorpus(
        bpe_rows=[_bpe_row(p1, 30)],
        summary_rows=[_summary_row(p1, 30), _summary_row(p2, 30)],
    ):
        try:
            rbv._load_periods()
            raise AssertionError("expected SystemExit for a period-set mismatch")
        except SystemExit as e:
            assert p2 in str(e), str(e)
    return "electric_bill_summary.csv naming a period absent from bill_periods_electric.csv raises SystemExit naming it"


@case
def case_load_periods_rejects_a_bad_current_charges_arithmetic():
    """Step 1's arithmetic assertion: current_charges must equal
    sdge_delivery + cca_generation to the cent."""
    # days=365 on a single row so this case clears the days-sum-365 gate and
    # reaches the arithmetic check under test.
    p1 = "1/1/26 - 1/30/26"
    with _TempCorpus(
        bpe_rows=[_bpe_row(p1, 365, delivery=20.0, gen=10.0, current=999.99)],
        summary_rows=[_summary_row(p1, 365, delivery=20.0, gen=10.0, current=999.99)],
    ):
        try:
            rbv._load_periods()
            raise AssertionError("expected SystemExit for bad current_charges arithmetic")
        except SystemExit as e:
            assert p1 in str(e), str(e)
    return "current_charges != sdge_delivery + cca_generation raises SystemExit naming the period"


@case
def case_load_periods_rejects_days_not_summing_to_365():
    """Step 1's 365-day assertion."""
    p1, p2 = "1/1/26 - 1/30/26", "1/31/26 - 3/1/26"
    with _TempCorpus(
        bpe_rows=[_bpe_row(p1, 30), _bpe_row(p2, 30)],
        summary_rows=[_summary_row(p1, 30), _summary_row(p2, 30)],
    ):
        try:
            rbv._load_periods()
            raise AssertionError("expected SystemExit for days not summing to 365")
        except SystemExit as e:
            assert "365" in str(e), str(e)
    return "a fabricated corpus whose days column does not sum to 365 raises SystemExit"


@case
def case_load_periods_rejects_a_gap_between_periods():
    """The contiguity check added alongside the Bug 1 fix:
    _bill_window_all_current_vintage_modeled() filters the interval frame to
    [periods[0].start, periods[-1].end] and treats that as THE bill-aligned
    window, which is only correct if the periods tile it with no gap or
    overlap. This check runs AFTER the days-sum-365 gate, so the declared
    "days" values must still sum to 365 to reach it; fabricate two periods
    whose calendar span (Jan 1-21, only 21 days -- a 1-day gap on Jan 11
    between the two 10-day sub-ranges) does not match their declared 365-day
    total, and expect SystemExit naming the mismatch rather than a silently
    wrong window being built from it."""
    p1, p2 = "1/1/26 - 1/10/26", "1/12/26 - 1/21/26"  # gap on 1/11/26
    with _TempCorpus(
        bpe_rows=[_bpe_row(p1, 200), _bpe_row(p2, 165)],
        summary_rows=[_summary_row(p1, 200), _summary_row(p2, 165)],
    ):
        try:
            rbv._load_periods()
            raise AssertionError("expected SystemExit for a gap between periods")
        except SystemExit as e:
            assert "not contiguous" in str(e), str(e)
    return "a fabricated corpus whose calendar span disagrees with its own declared day count raises SystemExit naming it as not contiguous"


# ---------------------------------------------------------------------------
# (b) Step 2/3b coverage-gap test -- fabricated frame, no usage.csv needed.
# ---------------------------------------------------------------------------
@case
def case_check_coverage_rejects_a_gap_in_the_middle_of_a_period():
    period = dict(period="1/1/26 - 1/10/26", start=dt.date(2026, 1, 1),
                 end=dt.date(2026, 1, 10))
    available = {dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)}
    available.discard(dt.date(2026, 1, 5))  # a gap in the middle
    try:
        rbv._check_coverage(available, [period])
        raise AssertionError("expected SystemExit for a coverage gap")
    except SystemExit as e:
        assert "2026-01-05" in str(e) and period["period"] in str(e), str(e)
    return "a fabricated usage.csv missing a mid-period date raises SystemExit naming the gap"


@case
def case_check_coverage_accepts_full_coverage():
    period = dict(period="1/1/26 - 1/10/26", start=dt.date(2026, 1, 1),
                 end=dt.date(2026, 1, 10))
    available = {dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)}
    rbv._check_coverage(available, [period])  # must not raise
    return "full coverage of a fabricated period does not raise"


# ---------------------------------------------------------------------------
# (b2) Bug 2 (adversarial review pass 1, finding 2): NEM 2.0 nets per
# CALENDAR MONTH, restarting at every month boundary -- not over a whole
# ~30-day bill period in one shot. Exercises _delivery_and_pcia_kwh() directly
# on a tiny fabricated 2-row frame using REAL corpus dates (2026-01-31 /
# 2026-02-01, both within rates_history's covered range), so no private
# archive is needed -- rates_history.py reads only the committed, public
# data/bill_periods_electric.csv and data/bill_tou_detail.csv.
# ---------------------------------------------------------------------------
@case
def case_delivery_and_pcia_kwh_restarts_netting_at_month_boundary():
    """A (season, TOU) bucket whose sign flips across a calendar-month
    boundary must be netted SEPARATELY per month, not merged over the whole
    slice. Without the month restart, January's +10 kWh on-peak import and
    February's -10 kWh on-peak export cancel to a net of zero and delivery
    prices to $0 (the bug: an earlier version bucketed by (season, TOU) alone
    over the whole period, so a period straddling a month boundary with a
    sign flip like this netted incorrectly). With the restart, January's
    positive bucket bills alone at the current rate and February's negative
    bucket zero-clamps, giving a nonzero total this test pins exactly."""
    rows = [
        # 2026-01-31 16:00 (winter on-peak): pure import -> January nets +10.
        dict(dt=pd.Timestamp("2026-01-31 16:00"), Consumption=10.0, Generation=0.0,
            seas="W", p="on", ym=pd.Period("2026-01", "M")),
        # 2026-02-01 16:00 (winter on-peak): pure export -> February nets -10.
        dict(dt=pd.Timestamp("2026-02-01 16:00"), Consumption=0.0, Generation=10.0,
            seas="W", p="on", ym=pd.Period("2026-02", "M")),
    ]
    sub = pd.DataFrame(rows)
    (delivery_current, delivery_own, pcia_kwh, unpriced_kwh, unpriced_days,
     mechanics_gap) = rbv._delivery_and_pcia_kwh(sub)

    expected_rate = rbv.rates.UDC["W"]["on"]
    assert abs(delivery_current - 10.0 * expected_rate) < 1e-9, (
        f"expected January's +10 kWh bucket billed alone at {expected_rate} "
        f"({10.0 * expected_rate:.4f}), got delivery_current_vintage="
        f"{delivery_current:.4f} -- looks like the two days were netted "
        "together across the month boundary instead of restarting at it")
    assert abs(pcia_kwh - 10.0) < 1e-9, (
        "PCIA's positive-net-kWh accumulator must also restart at the month "
        f"boundary (same bug shape): expected 10.0, got {pcia_kwh}")

    # negative_bucket_mechanics_gap_usd (adversarial review pass 2, finding 1:
    # an earlier version had this backwards -- it stored bmn.bill()'s raw
    # per-bucket credit contribution instead of the DISAGREEMENT against it).
    # Derive the expected value by actually CALLING bmn.bill() on February's
    # row alone, rather than re-deriving the same energy()/credit() formula
    # written inside _delivery_and_pcia_kwh -- a test that only restates the
    # implementation's own formula can't catch a sign error in that formula.
    # bmn.bill() on a single-day, single-month frame also adds one day's BSC
    # (NBC*gross is 0 here since that row's own Consumption is 0), so subtract
    # BSC to isolate the bucket's own credit contribution.
    feb_only = sub[sub.dt.dt.date == dt.date(2026, 2, 1)].copy()
    bmn_feb_bucket_contribution = rbv.bmn.bill(feb_only) - rbv.bmn.BSC * 1
    # current_vintage_total contributes ZERO for this negative bucket (the
    # zero-clamp); the disagreement the diagnostic reports is that zero MINUS
    # what bmn.bill() actually contributes for the same bucket.
    expected_gap = 0.0 - bmn_feb_bucket_contribution
    assert abs(mechanics_gap - expected_gap) < 1e-9, (
        f"negative_bucket_mechanics_gap_usd={mechanics_gap} does not match "
        f"the disagreement independently derived from a real bmn.bill() call "
        f"({expected_gap}) -- check the sign in _delivery_and_pcia_kwh's "
        "elif bucket_net < 0 branch")
    assert expected_gap > 0, (
        "sanity check on the test fixture itself: bmn.bill() credits the "
        "negative bucket (a real dollar reduction), so 0 minus that "
        "contribution must be POSITIVE -- if this fails the fixture, not the "
        "code under test, has a sign error")
    assert unpriced_kwh == 0.0 and unpriced_days == set()
    return (f"delivery_current_vintage=${delivery_current:.4f} correctly bills only "
            "January's positive bucket after the month restart, not the "
            "merged-to-zero net across the boundary; delivery_own_vintage="
            f"${delivery_own:.4f} sourced from rates_history.py's real historical "
            f"rate for those same two dates; negative_bucket_mechanics_gap_usd="
            f"${mechanics_gap:.4f} matches an independent bmn.bill() cross-check")


# ---------------------------------------------------------------------------
# (c) Step 4 telescoping-identity test -- pure arithmetic, fabricated
# per-period dollar figures, genuinely exercising _aggregate().
# ---------------------------------------------------------------------------
def _fabricated_period(current_vintage_total, own_vintage_total, actual_total):
    return dict(
        current_vintage_total_usd=current_vintage_total,
        own_vintage_total_usd=own_vintage_total,
        actual_total_usd=actual_total,
        residual_usd=actual_total - own_vintage_total,
    )


@case
def case_aggregate_identity_holds_for_arbitrary_fabricated_numbers():
    """The 5-term telescoping identity native_window_total + window_effect +
    generation_and_fixed_charge_vintage_effect + delivery_vintage_effect +
    residual_total == actual_total_sum must hold EXACTLY regardless of the
    actual dollar values involved -- it is pure arithmetic given
    _aggregate()'s own definitions, not a data finding (adversarial review
    pass 1, finding 1: the ORIGINAL 4-term version conflated window_effect
    with a mislabeled generation/fixed-charge vintage effect because
    bill_window_current_vintage_total -- which substitutes real generation and
    fixed-charge dollars -- was compared directly against native_window_total,
    which models them instead; this 5-term version inserts
    bill_window_all_current_vintage_modeled_total as the missing bridge value
    so window_effect and generation_and_fixed_charge_vintage_effect are each
    clean, single-purpose differences). Exercised on THREE unrelated,
    made-up scenarios via the script's own _aggregate() function."""
    scenarios = [
        # (native_window_total, all_current_vintage_modeled_total,
        #  [(current, own, actual), ...])
        (1000.0, 1050.0,
         [(100.0, 90.0, 80.0), (200.0, 210.0, 195.0), (50.0, 45.0, 60.0)]),
        (0.0, 0.0, [(10.0, 10.0, 10.0)]),  # everything equal: every effect zero
        (-500.0, -480.0,
         [(300.0, -100.0, 250.0), (75.5, 80.25, 70.0)]),  # negatives allowed
    ]
    for native, all_modeled, triples in scenarios:
        per_period = [_fabricated_period(*t) for t in triples]
        agg = rbv._aggregate(per_period, native, all_modeled)
        actual_total_sum = sum(t[2] for t in triples)
        assert abs(agg["actual_total_sum"] - actual_total_sum) < 1e-9
        identity = (agg["native_window_total"] + agg["window_effect"]
                   + agg["generation_and_fixed_charge_vintage_effect"]
                   + agg["delivery_vintage_effect"] + agg["residual_total"])
        assert abs(identity - actual_total_sum) < 1e-9, (identity, actual_total_sum)
        assert agg["identity_holds"] is True
        # total_vintage_effect must equal the sum of its two named parts
        assert abs(agg["total_vintage_effect"]
                  - (agg["delivery_vintage_effect"]
                     + agg["generation_and_fixed_charge_vintage_effect"])) < 1e-9
    return "the 5-term telescoping identity holds exactly across three unrelated fabricated scenarios"


@case
def case_aggregate_rejects_an_inconsistent_residual():
    """If a caller hands _aggregate() per-period rows whose residual_usd was
    computed inconsistently with actual_total_usd - own_vintage_total_usd, the
    cross-check between the two routes to residual_total must SystemExit
    rather than silently publish a broken aggregate."""
    per_period = [dict(current_vintage_total_usd=100.0, own_vintage_total_usd=90.0,
                       actual_total_usd=80.0, residual_usd=999.0)]  # wrong on purpose
    try:
        rbv._aggregate(per_period, 1000.0, 1010.0)
        raise AssertionError("expected SystemExit for an inconsistent residual")
    except SystemExit:
        pass
    return "_aggregate() refuses a per-period residual inconsistent with actual - own_vintage"


# ---------------------------------------------------------------------------
# (d) end-to-end: the real generator against the real staged archive.
# ---------------------------------------------------------------------------
@case
def case_build_runs_end_to_end_on_the_real_archive_and_the_identity_holds():
    usage_csv = _require_archive()
    saved_csv = rbv.bmn.CSV
    rbv.bmn.CSV = usage_csv
    try:
        result = rbv.build()
    finally:
        rbv.bmn.CSV = saved_csv

    assert result["identity_holds"] is True
    assert abs(result["actual_total_sum"] - 3282.22) < 0.01, result["actual_total_sum"]
    assert len(result["per_period"]) == 13
    identity = (result["native_window_total"] + result["window_effect"]
               + result["generation_and_fixed_charge_vintage_effect"]
               + result["delivery_vintage_effect"] + result["residual_total"])
    assert abs(identity - result["actual_total_sum"]) < 0.01
    assert abs(result["total_vintage_effect"]
              - (result["delivery_vintage_effect"]
                 + result["generation_and_fixed_charge_vintage_effect"])) < 0.01
    # regression guard for the specific bug adversarial review pass 1 found:
    # window_effect must be small relative to total_vintage_effect on the real
    # data (the inverted, pre-fix relationship had window >> vintage).
    assert abs(result["window_effect"]) < abs(result["total_vintage_effect"]), (
        "window_effect is no longer smaller than total_vintage_effect on the real "
        "archive -- this is the exact relationship adversarial review pass 1 found "
        "inverted; check bill_window_all_current_vintage_modeled_total's "
        "construction before trusting this result")

    with tempfile.TemporaryDirectory() as td:
        path = rbv._write(result, td)
        assert path.exists()
        reloaded = json.loads(path.read_text())
        assert reloaded["actual_total_sum"] == result["actual_total_sum"]

    return (f"build() on the real archive: native ${result['native_window_total']:.2f} "
            f"+ window ${result['window_effect']:+.2f} + generation/fixed-charge "
            f"vintage ${result['generation_and_fixed_charge_vintage_effect']:+.2f} "
            f"+ delivery vintage ${result['delivery_vintage_effect']:+.2f} + residual "
            f"${result['residual_total']:+.2f} = actual "
            f"${result['actual_total_sum']:.2f} (total vintage effect "
            f"${result['total_vintage_effect']:+.2f}); identity holds and the "
            "artifact write round-trips")


@case
def case_build_is_deterministic_on_the_real_archive():
    usage_csv = _require_archive()
    saved_csv = rbv.bmn.CSV
    rbv.bmn.CSV = usage_csv
    try:
        out1 = rbv.build()
        out2 = rbv.build()
    finally:
        rbv.bmn.CSV = saved_csv
    s1 = json.dumps(out1, sort_keys=True, default=str)
    s2 = json.dumps(out2, sort_keys=True, default=str)
    assert s1 == s2, "build() must be deterministic across repeated runs on the same inputs"
    return "build() is deterministic end-to-end on the real archive"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran, skipped = 0, 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP {fn.__name__}\n     {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
