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


CCA_RATES_HEADER = ["statement_date", "period", "season", "tou_period", "kwh",
                    "rate_usd_per_kwh", "usd", "provider", "authority", "evidence",
                    "source_pdf", "note"]


def _cca_rate_row(period, season, tou_period, rate, authority="charged_tariff"):
    return ["2026-01-01", period, season, tou_period, 100.0, rate, round(100.0 * rate, 2),
            "CCA", authority, "direct", "x.pdf", ""]


class _TempCCARates:
    """Context manager patching rbv.CCA_RATES_CSV to a fabricated file,
    restoring the real (committed) path on exit."""

    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        path = pathlib.Path(self._tmp.name) / "cca_generation_rates.csv"
        _write_csv(path, CCA_RATES_HEADER, self.rows)
        self._saved = rbv.CCA_RATES_CSV
        rbv.CCA_RATES_CSV = path
        return self

    def __exit__(self, *exc):
        rbv.CCA_RATES_CSV = self._saved
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
    (delivery_current, delivery_own, pcia_kwh, unpriced_kwh,
     unpriced_days) = rbv._delivery_and_pcia_kwh(sub)

    expected_rate = rbv.rates.UDC["W"]["on"]
    assert abs(delivery_current - 10.0 * expected_rate) < 1e-9, (
        f"expected January's +10 kWh bucket billed alone at {expected_rate} "
        f"({10.0 * expected_rate:.4f}), got delivery_current_vintage="
        f"{delivery_current:.4f} -- looks like the two days were netted "
        "together across the month boundary instead of restarting at it")
    assert abs(pcia_kwh - 10.0) < 1e-9, (
        "PCIA's positive-net-kWh accumulator must also restart at the month "
        f"boundary (same bug shape): expected 10.0, got {pcia_kwh}")
    # February's -10 kWh bucket zero-clamps to $0 here -- this function no
    # longer tries to quantify the resulting per-bill-period-restart artifact
    # itself (that moved to the aggregate level -- see
    # case_continuous_components_sum_to_bmn_bill_total and
    # case_delivery_pcia_restart_artifact_matches_direct_difference below).
    assert unpriced_kwh == 0.0 and unpriced_days == set()
    return (f"delivery_current_vintage=${delivery_current:.4f} correctly bills only "
            "January's positive bucket after the month restart, not the "
            "merged-to-zero net across the boundary; delivery_own_vintage="
            f"${delivery_own:.4f} sourced from rates_history.py's real historical "
            "rate for those same two dates")


# ---------------------------------------------------------------------------
# (b3) _continuous_current_vintage_components() -- fabricated frame, no
# archive needed. Verifies it reproduces bmn.bill()'s own total exactly (the
# claim its own docstring makes), and that the per-bill-period-restart
# artifact this test file's fixture creates matches a hand-independent
# derivation.
# ---------------------------------------------------------------------------
@case
def case_continuous_components_sum_to_bmn_bill_total():
    """_continuous_current_vintage_components()'s five components (delivery,
    generation, pcia, nbc, fixed_charge) must sum EXACTLY to bmn.bill()'s own
    total for the same frame -- the claim its own docstring makes, verified
    here rather than assumed. Uses a small multi-month fabricated frame with
    both positive and negative buckets so PCIA's sign-dependent branch is
    actually exercised."""
    rows = [
        dict(dt=pd.Timestamp("2026-01-15 16:00"), Consumption=12.0, Generation=0.0,
            seas="W", p="on", ym=pd.Period("2026-01", "M")),
        dict(dt=pd.Timestamp("2026-01-20 07:00"), Consumption=0.0, Generation=5.0,
            seas="W", p="off", ym=pd.Period("2026-01", "M")),
        dict(dt=pd.Timestamp("2026-02-03 16:00"), Consumption=8.0, Generation=2.0,
            seas="W", p="on", ym=pd.Period("2026-02", "M")),
        dict(dt=pd.Timestamp("2026-06-10 16:00"), Consumption=20.0, Generation=1.0,
            seas="S", p="on", ym=pd.Period("2026-06", "M")),
    ]
    sub = pd.DataFrame(rows)
    start, end = dt.date(2026, 1, 15), dt.date(2026, 6, 10)
    components = rbv._continuous_current_vintage_components(sub, start, end)
    reconstructed = sum(components.values())
    direct = rbv.bmn.bill(sub)
    assert abs(reconstructed - direct) < 1e-9, (
        f"components sum to {reconstructed} but bmn.bill() gives {direct} on the "
        "same frame -- _continuous_current_vintage_components() no longer "
        "reproduces bmn.bill()'s own total")
    return (f"the five components ({', '.join(f'{k}={v:.4f}' for k, v in components.items())}) "
            f"sum exactly to bmn.bill()'s own total (${direct:.4f})")


@case
def case_delivery_pcia_restart_artifact_matches_direct_difference():
    """The per-bill-period-restart artifact _aggregate() computes
    (delivery_pcia_restart_artifact_usd) must equal the ACTUAL difference
    between per-period-summed delivery/PCIA and the continuous-window
    delivery/PCIA totals -- not a per-negative-bucket UDC+CEA formula (the
    retired negative_bucket_mechanics_gap_usd used that shortcut and
    overstated the artifact on the real corpus by about $6.66, adversarial
    review pass 4's finding). Fabricates a period that straddles a month
    boundary with a sign flip (the same shape as the monthly-restart test
    above), computes the per-period (restarted) and continuous totals
    independently, and confirms _aggregate()'s reported artifact matches
    their direct difference exactly."""
    rows = [
        dict(dt=pd.Timestamp("2026-01-31 16:00"), Consumption=10.0, Generation=0.0,
            seas="W", p="on", ym=pd.Period("2026-01", "M")),
        dict(dt=pd.Timestamp("2026-02-01 16:00"), Consumption=0.0, Generation=10.0,
            seas="W", p="on", ym=pd.Period("2026-02", "M")),
    ]
    sub = pd.DataFrame(rows)
    start, end = dt.date(2026, 1, 31), dt.date(2026, 2, 1)

    (delivery_current, _delivery_own, pcia_kwh, _unpriced_kwh,
     _unpriced_days) = rbv._delivery_and_pcia_kwh(sub)
    pcia_current = rbv.rates.PCIA * pcia_kwh
    components = rbv._continuous_current_vintage_components(sub, start, end)

    expected_delivery_artifact = delivery_current - components["delivery"]
    expected_pcia_artifact = pcia_current - components["pcia"]

    # Now drive the SAME fixture through the real per-period pipeline (one
    # fabricated bill_periods_electric.csv row spanning the whole fixture as
    # a single "period") and confirm _aggregate() reports the identical
    # artifact -- exercising the real code path, not just re-deriving it.
    row = dict(period="1/31/26 - 2/1/26", start=start, end=end, days=2,
              net_kwh=0.0, gross_kwh=10.0, sdge_delivery=0.0, cca_generation=0.0,
              current_charges=0.0, fixed_charge_total=0.0)
    figures = rbv._per_period_figures(sub, row)
    per_period = [figures]
    native = 0.0
    all_modeled = rbv.bmn.bill(sub)
    agg = rbv._aggregate(per_period, native, all_modeled, components)

    assert abs(agg["delivery_restart_artifact_usd"] - expected_delivery_artifact) < 1e-9
    assert abs(agg["pcia_restart_artifact_usd"] - expected_pcia_artifact) < 1e-9
    assert abs(agg["delivery_pcia_restart_artifact_usd"]
              - (expected_delivery_artifact + expected_pcia_artifact)) < 1e-9
    return (f"delivery_pcia_restart_artifact_usd={agg['delivery_pcia_restart_artifact_usd']:.4f} "
            "matches the direct per-period-vs-continuous difference, computed independently")


# ---------------------------------------------------------------------------
# (b5) _verify_cca_generation_rate_flat() -- the evidence generation_tou_
# window_effect's construction depends on (a fresh Codex adversarial review
# of this branch flagged the prior "generation vintage" claim as unsupported).
# The "passes on the real corpus" case reads the real, committed, PUBLIC
# data/cca_generation_rates.csv -- no private archive needed. The fail-closed
# cases patch rbv.CCA_RATES_CSV to fabricated files.
# ---------------------------------------------------------------------------
@case
def case_verify_cca_generation_rate_flat_passes_on_the_real_corpus():
    """The real committed data/cca_generation_rates.csv, checked against the
    real 13-period corpus, must show CEA's charged rate flat and equal to
    rates.CEA for every (season, TOU) cell -- this is the actual evidence
    generation_tou_window_effect's zero-vintage construction depends on,
    verified here fresh rather than assumed from cca_rate_extraction.py's own
    docstring."""
    periods = rbv._load_periods()
    rbv._verify_cca_generation_rate_flat(periods)  # must not raise
    return ("the real 13-period corpus's CEA generation rate is flat and equal "
            "to rates.CEA for every (season, TOU) cell -- verified against "
            "data/cca_generation_rates.csv, not assumed")


@case
def case_verify_cca_generation_rate_flat_rejects_a_non_flat_rate():
    """Two periods billing DIFFERENT rates for the same (season, TOU) cell
    must SystemExit naming the cell, not silently average or pick one."""
    p1, p2 = "1/1/26 - 1/30/26", "1/31/26 - 3/1/26"
    with _TempCCARates([
        _cca_rate_row(p1, "winter", "on_peak", 0.2443),
        _cca_rate_row(p2, "winter", "on_peak", 0.2500),  # different!
    ]):
        try:
            rbv._verify_cca_generation_rate_flat(
                [dict(period=p1), dict(period=p2)])
            raise AssertionError("expected SystemExit for a non-flat rate")
        except SystemExit as e:
            assert "not flat" in str(e) and "winter/on_peak" in str(e), str(e)
    return "two periods charging different rates for the same cell raise SystemExit naming it"


@case
def case_verify_cca_generation_rate_flat_rejects_a_rate_that_moved_from_current():
    """A flat rate that does NOT match rates.CEA's current value must
    SystemExit -- the whole point of the check is confirming equality to
    CURRENT, not just internal flatness."""
    p1 = "1/1/26 - 1/30/26"
    with _TempCCARates([_cca_rate_row(p1, "winter", "on_peak", 0.99999)]):
        try:
            rbv._verify_cca_generation_rate_flat([dict(period=p1)])
            raise AssertionError("expected SystemExit for a rate that differs from current")
        except SystemExit as e:
            assert "winter/on_peak" in str(e) and "!=" in str(e), str(e)
    return "a flat rate that disagrees with rates.CEA's current value raises SystemExit naming it"


@case
def case_verify_cca_generation_rate_flat_rejects_a_missing_period():
    """A period with no charged_tariff generation rows at all must SystemExit
    naming it, rather than silently skipping it (which would let a missing
    period masquerade as 'nothing to check, so it passes')."""
    p1, p2 = "1/1/26 - 1/30/26", "1/31/26 - 3/1/26"
    with _TempCCARates([_cca_rate_row(p1, "winter", "on_peak", 0.2443)]):
        try:
            rbv._verify_cca_generation_rate_flat(
                [dict(period=p1), dict(period=p2)])
            raise AssertionError("expected SystemExit for a period missing from the CSV")
        except SystemExit as e:
            assert p2 in str(e), str(e)
    return "a period with no charged_tariff generation rows raises SystemExit naming it"


# ---------------------------------------------------------------------------
# (c) Step 4 telescoping-identity test -- pure arithmetic, fabricated
# per-period dollar figures, genuinely exercising _aggregate().
# ---------------------------------------------------------------------------
def _fabricated_period(delivery_current, delivery_own, pcia, nbc, fixed_charge,
                       generation, actual_total):
    """A fully self-consistent fabricated per-period row: current_vintage_total_usd
    and own_vintage_total_usd are DERIVED from the fine-grained fields (mirroring
    _per_period_figures's own formula), never chosen independently -- so a test
    using this helper genuinely exercises _aggregate()'s real per-period-sum
    logic rather than a hand-picked total that might not correspond to anything
    _per_period_figures could actually produce."""
    current_vintage_total = delivery_current + pcia + nbc + fixed_charge + generation
    own_vintage_total = delivery_own + pcia + nbc + fixed_charge + generation
    return dict(
        delivery_current_vintage_usd=delivery_current,
        pcia_usd=pcia,
        nbc_usd=nbc,
        fixed_charge_actual_usd=fixed_charge,
        generation_actual_usd=generation,
        current_vintage_total_usd=current_vintage_total,
        own_vintage_total_usd=own_vintage_total,
        actual_total_usd=actual_total,
        residual_usd=actual_total - own_vintage_total,
    )


@case
def case_aggregate_identity_holds_for_arbitrary_fabricated_numbers():
    """The 6-term telescoping identity native_window_total + window_effect +
    generation_tou_window_effect + fixed_charge_vintage_effect +
    delivery_vintage_effect + residual_total == actual_total_sum must hold
    EXACTLY regardless of the actual dollar values involved -- it is pure
    arithmetic given _aggregate()'s own definitions, not a data finding.
    Exercised on TWO unrelated, made-up scenarios via the script's own
    _aggregate() function: one where the continuous-window components exactly
    equal the naive per-period sums (zero restart artifact, zero generation-
    TOU effect -- the simplest consistent case), and one where they are
    DELIBERATELY perturbed away from the per-period sums (a nonzero restart
    artifact AND a nonzero generation-TOU effect), with all_modeled always
    derived as the sum of the SAME components used elsewhere, never picked
    independently (that constraint is what the internal 'old_combined
    reconstruction' cross-check inside _aggregate() enforces -- see
    case_aggregate_rejects_a_generation_fixed_charge_decomposition_that_
    doesnt_reconstruct below for what happens when it's violated)."""
    scenarios = [
        dict(native=1000.0, continuous=None, periods=[
            dict(delivery_current=200.0, delivery_own=190.0, pcia=20.0, nbc=40.0,
                fixed_charge=45.0, generation=250.0, actual_total=300.0),
            dict(delivery_current=250.0, delivery_own=245.0, pcia=25.0, nbc=40.0,
                fixed_charge=45.0, generation=280.0, actual_total=320.0),
        ]),
        dict(native=-50.0,
            continuous=dict(delivery=95.0, generation=110.0, pcia=8.0, nbc=15.0,
                            fixed_charge=22.0),
            periods=[
                dict(delivery_current=100.0, delivery_own=80.0, pcia=10.0, nbc=15.0,
                    fixed_charge=20.0, generation=90.0, actual_total=150.0),
            ]),
    ]
    for sc in scenarios:
        per_period = [_fabricated_period(**p) for p in sc["periods"]]
        if sc["continuous"] is None:
            continuous = dict(
                delivery=sum(p["delivery_current"] for p in sc["periods"]),
                generation=sum(p["generation"] for p in sc["periods"]),
                pcia=sum(p["pcia"] for p in sc["periods"]),
                nbc=sum(p["nbc"] for p in sc["periods"]),
                fixed_charge=sum(p["fixed_charge"] for p in sc["periods"]))
        else:
            continuous = sc["continuous"]
        all_modeled = sum(continuous.values())
        agg = rbv._aggregate(per_period, sc["native"], all_modeled, continuous)
        actual_total_sum = sum(p["actual_total"] for p in sc["periods"])
        assert abs(agg["actual_total_sum"] - actual_total_sum) < 1e-9
        identity = (agg["native_window_total"] + agg["window_effect"]
                   + agg["generation_tou_window_effect"]
                   + agg["fixed_charge_vintage_effect"]
                   + agg["delivery_vintage_effect"] + agg["residual_total"])
        assert abs(identity - actual_total_sum) < 1e-9, (identity, actual_total_sum)
        assert agg["identity_holds"] is True
        # total_vintage_effect must equal the sum of its two named parts
        # (generation deliberately excluded)
        assert abs(agg["total_vintage_effect"]
                  - (agg["delivery_vintage_effect"]
                     + agg["fixed_charge_vintage_effect"])) < 1e-9
        # generation_tou_window_effect must equal its own two named parts
        assert abs(agg["generation_tou_window_effect"]
                  - (agg["generation_clean_tou_effect"]
                     + agg["delivery_pcia_restart_artifact_usd"])) < 1e-9
    return ("the 6-term telescoping identity holds exactly across two unrelated "
            "fabricated scenarios (zero-artifact and perturbed-artifact)")


@case
def case_aggregate_rejects_an_inconsistent_residual():
    """If a caller hands _aggregate() per-period rows whose residual_usd was
    computed inconsistently with actual_total_usd - own_vintage_total_usd, the
    cross-check between the two routes to residual_total must SystemExit
    rather than silently publish a broken aggregate."""
    per_period = [_fabricated_period(delivery_current=100.0, delivery_own=90.0,
                                     pcia=10.0, nbc=20.0, fixed_charge=25.0,
                                     generation=80.0, actual_total=200.0)]
    per_period[0]["residual_usd"] = 999.0  # wrong on purpose
    continuous = dict(delivery=100.0, generation=80.0, pcia=10.0, nbc=20.0,
                      fixed_charge=25.0)
    try:
        rbv._aggregate(per_period, 1000.0, sum(continuous.values()), continuous)
        raise AssertionError("expected SystemExit for an inconsistent residual")
    except SystemExit:
        pass
    return "_aggregate() refuses a per-period residual inconsistent with actual - own_vintage"


@case
def case_aggregate_rejects_a_nonzero_nbc_cancellation_diff():
    """NBC is linear in gross kWh with no bucketing or sign dependence, so the
    per-period sum and the continuous-window total MUST be identical; a
    mismatch means the window boundaries or frames have drifted apart, and
    must SystemExit rather than silently absorb the difference into a vintage
    term."""
    per_period = [_fabricated_period(delivery_current=100.0, delivery_own=90.0,
                                     pcia=10.0, nbc=20.0, fixed_charge=25.0,
                                     generation=80.0, actual_total=200.0)]
    continuous = dict(delivery=100.0, generation=80.0, pcia=10.0, nbc=25.0,  # != 20.0
                      fixed_charge=25.0)
    try:
        rbv._aggregate(per_period, 1000.0, sum(continuous.values()), continuous)
        raise AssertionError("expected SystemExit for a nonzero NBC cancellation diff")
    except SystemExit as e:
        assert "NBC does not cancel" in str(e), str(e)
    return "_aggregate() refuses when NBC does not cancel between the per-period sum and the continuous total"


@case
def case_aggregate_rejects_a_generation_fixed_charge_decomposition_that_doesnt_reconstruct():
    """If bill_window_all_current_vintage_modeled_total is inconsistent with
    the SAME continuous_components dict (i.e. not literally their sum),
    generation_tou_window_effect + fixed_charge_vintage_effect will not
    reconstruct (current_vintage_total - all_modeled) exactly -- _aggregate()
    must catch this internally rather than silently publish two terms that
    don't actually decompose what they claim to."""
    per_period = [_fabricated_period(delivery_current=100.0, delivery_own=90.0,
                                     pcia=10.0, nbc=20.0, fixed_charge=25.0,
                                     generation=80.0, actual_total=200.0)]
    continuous = dict(delivery=100.0, generation=80.0, pcia=10.0, nbc=20.0,
                      fixed_charge=25.0)  # sums to 235.0
    try:
        rbv._aggregate(per_period, 1000.0, 999.0, continuous)  # all_modeled != 235.0
        raise AssertionError("expected SystemExit for a non-reconstructing decomposition")
    except SystemExit as e:
        assert "does not reconstruct" in str(e), str(e)
    return "_aggregate() refuses when all_modeled is inconsistent with the continuous components it's built from"


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
               + result["generation_tou_window_effect"]
               + result["fixed_charge_vintage_effect"]
               + result["delivery_vintage_effect"] + result["residual_total"])
    assert abs(identity - result["actual_total_sum"]) < 0.01
    assert abs(result["total_vintage_effect"]
              - (result["delivery_vintage_effect"]
                 + result["fixed_charge_vintage_effect"])) < 0.01
    assert abs(result["generation_tou_window_effect"]
              - (result["generation_clean_tou_effect"]
                 + result["delivery_pcia_restart_artifact_usd"])) < 0.01

    # regression guard for the bug adversarial review pass 1 found: window_effect
    # must be small relative to total_vintage_effect on the real data (the
    # inverted, pre-fix relationship had window >> vintage).
    assert abs(result["window_effect"]) < abs(result["total_vintage_effect"]), (
        "window_effect is no longer smaller than total_vintage_effect on the real "
        "archive -- this is the exact relationship adversarial review pass 1 found "
        "inverted; check bill_window_all_current_vintage_modeled_total's "
        "construction before trusting this result")
    # regression guard for the bug a fresh Codex review found: total_vintage_effect
    # (delivery + fixed-charge only) must be small relative to
    # generation_tou_window_effect on the real data -- the pre-fix version folded
    # nearly all of generation_tou_window_effect INTO total_vintage_effect,
    # reporting an unsupported ~20% "rate vintage" headline.
    assert abs(result["total_vintage_effect"]) < abs(result["generation_tou_window_effect"]), (
        "total_vintage_effect is no longer smaller than generation_tou_window_effect "
        "on the real archive -- this is the exact relationship the Codex review found "
        "inverted (generation miscounted as vintage); check "
        "_verify_cca_generation_rate_flat()'s evidence and the generation/"
        "fixed-charge split before trusting this result")
    # CEA's generation rate vintage is proven zero, by evidence -- confirm build()
    # actually ran that check (it would have raised otherwise, but assert the
    # positive claim explicitly here too).
    assert "generation_rate_vintage_is_zero_by_evidence" in result["notes"]

    with tempfile.TemporaryDirectory() as td:
        path = rbv._write(result, td)
        assert path.exists()
        reloaded = json.loads(path.read_text())
        assert reloaded["actual_total_sum"] == result["actual_total_sum"]

    return (f"build() on the real archive: native ${result['native_window_total']:.2f} "
            f"+ window ${result['window_effect']:+.2f} + generation TOU-window "
            f"${result['generation_tou_window_effect']:+.2f} + fixed-charge vintage "
            f"${result['fixed_charge_vintage_effect']:+.2f} + delivery vintage "
            f"${result['delivery_vintage_effect']:+.2f} + residual "
            f"${result['residual_total']:+.2f} = actual "
            f"${result['actual_total_sum']:.2f} (total vintage effect, generation "
            f"excluded: ${result['total_vintage_effect']:+.2f}); identity holds and "
            "the artifact write round-trips")


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
