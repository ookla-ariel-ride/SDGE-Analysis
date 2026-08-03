#!/usr/bin/env python3
"""Reprice the 13-period billed year at its own tariff vintages (issue #30).

THE QUESTION. billing_model_nem.py prices the household's trailing 365-day
window (2025-07-25..2026-07-23) entirely at CURRENT (6/1/2026) rates and prints
roughly $4,904/yr. The bills for a DIFFERENT, bill-aligned 365-day window
(2025-06-27..2026-06-26, the 13 statements in data/bill_periods_electric.csv
that data/electric_bill_summary.csv also carries) accrued $3,282.22 of current
charges. TECHNICAL.md has long attributed the ~$1,622 gap to "rate vintage" --
the bills were rendered largely on cheaper 2025 tariffs. Issue #3 measured that
claim on two matched periods and found it does not hold up: delivery PRICE
LEVEL barely moved while its SHAPE rotated hard. This script actually measures
the vintage effect over the whole billed year, using analysis/rates_history.py
(the tariff actually in force on each historical date, sourced from the bill
PDFs' own printed rate lines) -- and separates it from a second, previously
unexamined confound: the two $4,904-vs-$3,282 figures are computed over
DIFFERENT 365-day windows in the first place.

THE DECOMPOSITION. Five quantities, chained so each is "the previous total plus
one more correction," landing exactly on the actual bills. (Adversarial review
pass 1, finding 1: an earlier version of this script had FOUR terms and
conflated window_effect with a large, mislabeled generation/fixed-charge
vintage effect -- see "THE BUG THIS FIX ADDRESSES" below for exactly how and
why. This is the corrected, 5-term version.)

    native_window_total   billing_model_nem's own native rolling window
                           (2025-07-25..2026-07-23), current vintage
                           throughout, computed by calling bmn.bill()
                           unmodified -- fresh here, never hardcoded.
  + window_effect          = bill_window_all_current_vintage_modeled_total
                             - native_window_total
                           the effect of moving to the BILL-ALIGNED window
                           (2025-06-27..2026-06-26), computed with bmn.bill()'s
                           EXACT SAME current-vintage methodology on BOTH
                           sides -- a clean, same-methodology comparison.
  + generation_and_fixed_charge_vintage_effect
                         = bill_window_current_vintage_total
                             - bill_window_all_current_vintage_modeled_total
                           the effect of substituting the REAL billed
                           generation and fixed-charge dollars for bmn.bill()'s
                           current-vintage MODEL of them (the CEA table
                           applied to interval kWh; BSC times days), window
                           held fixed at the bill-aligned one. Mostly, but not
                           purely, a vintage effect -- see "THE CAVEAT ON
                           generation_and_fixed_charge_vintage_effect" below.
  + delivery_vintage_effect
                         = bill_window_own_vintage_total
                             - bill_window_current_vintage_total
                           the effect of pricing UDC delivery at the vintage
                           actually in force each period, instead of current,
                           real generation/fixed-charge and window both held
                           fixed. Cleanly isolated: everything else in the two
                           totals being differenced is identical.
  + residual_total         = actual_total_sum - bill_window_own_vintage_total
                           whatever is left after every correction above: the
                           real, previously-undecomposed model-vs-bill gap.
  = actual_total_sum       the bills' own accrued current_charges, $3,282.22.

total_vintage_effect (= delivery_vintage_effect +
generation_and_fixed_charge_vintage_effect) is reported alongside the five
terms because it is the number that actually answers issue #30's question --
"how much of the gap is rate vintage" -- combining the one delivery effect
that rates_history.py can source cleanly with the one that includes generation
and the fixed charge, caveat and all.

This telescoping sum is a PURE ALGEBRAIC IDENTITY given the five definitions
above (each stage total minus the previous stage total, by construction) --
build() asserts it to the cent as a sanity check on the arithmetic, not as
evidence about the household. NOTE ON SIGN: this residual convention (actual
minus modeled, continuing the same "next stage minus previous stage" pattern
every other term uses) is the one that makes the five terms telescope to
actual_total_sum; a naive reading of "residual = own_vintage_total -
actual_total" for the running total would NOT telescope (it would double-count
bill_window_own_vintage_total). Each PER-PERIOD residual uses the same
actual-minus-modeled convention for exactly this reason, so per-period residuals
sum to residual_total exactly.

THE BUG THIS FIX ADDRESSES (adversarial review pass 1, finding 1). The
original 4-term version defined window_effect = bill_window_current_vintage_
total - native_window_total. That is NOT a clean window comparison, because
the two sides do not share a methodology for generation and the fixed charge:
native_window_total (via bmn.bill()) MODELS generation at the current-vintage
CEA table and the fixed charge at BSC*days; bill_window_current_vintage_total
SUBSTITUTES the real historical cca_generation and fixed_charge_total dollars
instead. That substitution is a genuine vintage correction, and it was being
counted as part of "window_effect" by construction, understating the true
vintage effect and overstating the true window effect. The fix inserts
bill_window_all_current_vintage_modeled_total -- the bill-aligned window
priced with bmn.bill()'s unmodified methodology, nothing substituted -- as the
missing bridge value, so window_effect compares like with like (modeled vs
modeled, differing only in which dates are included) and the generation/
fixed-charge substitution gets its own, correctly-attributed term.

THE CAVEAT ON generation_and_fixed_charge_vintage_effect. This term is mostly,
but not purely, a vintage effect, for the same reason PCIA/NBC vintage cannot
be cleanly isolated (see below): rates_history.py cannot source a historical
per-TOU CCA generation rate on any CCA-era date (the trust boundary below), so
there is no "own-vintage generation" figure to difference against a "current-
vintage generation" figure the way delivery_vintage_effect does. What this
term ACTUALLY differences is bill_window_current_vintage_total (delivery+PCIA
computed by _delivery_and_pcia_kwh, called separately PER BILL PERIOD and
summed) against bill_window_all_current_vintage_modeled_total (bmn.bill()
called ONCE on the whole bill-aligned window as a continuous frame). Real
generation and fixed-charge substitution is the dominant component of the gap
between these two totals, but they also disagree on a second, smaller,
mechanical effect (adversarial review pass 3, finding 1 -- corrected here
after an earlier version of this docstring misattributed the cause): bill
periods do not align with calendar-month boundaries, so a calendar month that
is net-positive OVERALL can be split at a bill-period boundary into two
per-period fragments where one fragment alone is net-negative;
_delivery_and_pcia_kwh, called separately per period, sees only that fragment
and zero-clamps it (as it must, for delivery_vintage_effect to compare like
with like -- see below), losing a netting offset the SAME month would keep if
priced continuously the way bmn.bill() actually is. This is NOT a case of
bmn.bill()'s real invocation crediting a bucket this script zero-clamps
instead -- verified directly: calling _delivery_and_pcia_kwh on the bill-
aligned window as ONE continuous frame (matching bmn.bill()'s real scope)
returns a mechanics gap of exactly $0.00, because no bucket is genuinely
net-negative anywhere in the continuous window; every occurrence this script
finds is an artifact of its own per-bill-period restart. build() computes and
reports negative_bucket_mechanics_gap_usd (summed from every period's own
_delivery_and_pcia_kwh call) specifically so this second component is
quantified, not just gestured at -- see the notes field. delivery_vintage_effect
is unaffected: both its sides (delivery_own_vintage and
delivery_current_vintage) come from the SAME per-bill-period-restarted calls,
so any phantom negative fragment zero-clamps identically on both sides and
cancels out of their difference.

WHAT CAN AND CANNOT BE REPRICED AT ITS OWN VINTAGE (the rates_history.py trust
boundary, read at the top of that module -- do not re-derive it here, cite it):
  * UDC delivery -- sourceable on MOST but not all corpus dates. Delivery is
    the charged tariff in both provider eras, but a cell can still be
    unsourceable on a given date if NO statement in the whole corpus ever
    printed a positive kWh line for it (rates_history.py never interpolates).
    That is exactly what happens here: this household net-EXPORTED during the
    off-peak TOU hours on nearly every statement, in both seasons, for most of
    the analysis year, so delivery/summer/off_peak and delivery/winter/
    off_peak carry long "absent" stretches (data/rate_vintages.csv). See
    _delivery_and_pcia_kwh's docstring for how this script handles it: priced
    day-by-day via RateSet.cells() (non-raising), with the unsourceable slice
    substituted at the current-vintage rate (contributing zero to
    delivery_vintage_effect, not a historical guess) instead of a whole-period
    rates_history.bill_nem_monthly(..., delivery_only=True) call, which raises
    on the entire period the instant it meets one such day.
  * CEA/CCA generation -- NOT sourceable on any of these 13 periods: they are
    all CCA-billed (12/27/24 onward), and on a CCA date the printed generation
    TOU table is SDG&E's bundled-generation comparison, not the CCA's charged
    tariff (RateSet.generation / RateSet.cca_generation both refuse). Per issue
    #30 AC4, this is handled by an EXPLICITLY SEPARATE supply treatment: the
    real billed cca_generation dollar amount (bill_periods_electric.csv) is
    substituted directly, identically in both the current-vintage and
    own-vintage totals, so it cancels out of delivery_vintage_effect by
    construction (it CANNOT cancel out of generation_and_fixed_charge_vintage_
    effect -- that term exists precisely to hold the generation/fixed-charge
    substitution, caveat and all).
  * PCIA, NBC -- genuinely not sourceable historically at all (no committed
    artifact carries the historical PCIA or non-bypassable-charge line); held
    at the current rates.py vintage in BOTH the current-vintage and own-vintage
    totals AND in bill_window_all_current_vintage_modeled_total, so they cancel
    out of every vintage term and cannot explain any of them, but they CAN be
    part of residual_total (see notes field for what this implies).
  * Base Services Charge / Monthly Service Fee -- the real billed
    fixed_charge_total is substituted directly (real, not modeled), same
    reasoning as generation.

Run from a directory containing usage.csv (the private/verify sandbox
convention). Writes data/reprice_by_vintage.json directly (found via the
standard _repo_root() walk-up, exactly like rates_history.py's own generator
output -- NOT the cwd-then-promote convention some other generators use).
"""
import csv
import datetime as dt
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rates                                    # noqa: E402
import rates_history                            # noqa: E402
import billing_model_nem as bmn                 # noqa: E402


def _repo_root():
    """Nearest ancestor holding both analysis/ and data/ (matches every other
    generator, so the private/verify sandbox works unchanged)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor contains both analysis/ and data/")


ROOT = _repo_root()
DATA = ROOT / "data"

# CWD-relative / DATA-relative inputs, overridable by tests (matching this
# repo's br.CSV / gi.USAGE_CSV convention).
PERIODS_CSV = DATA / "bill_periods_electric.csv"
SUMMARY_CSV = DATA / "electric_bill_summary.csv"

# billing_model_nem's own native rolling window (its __main__ block) -- the
# CURRENT-vintage figure this script decomposes against. Not the bill-aligned
# window below; that mismatch is window_effect.
NATIVE_WINDOW_END = dt.datetime(2026, 7, 24)
NATIVE_WINDOW_DAYS = 365


def _period_dates(period):
    """'6/27/25 - 7/28/25' -> (date, date), inclusive both ends. Same 2-digit-
    year convention as bill_decomposition._period_dates -- read, not modified."""
    def one(s):
        m, d, y = (int(x) for x in s.strip().split("/"))
        return dt.date(2000 + y, m, d)
    a, b = period.split(" - ")
    return one(a), one(b)


# ---------------------------------------------------------------------------
# Step 1: load and cross-validate the 13-period corpus.
# ---------------------------------------------------------------------------
def _load_periods():
    """The 13 CCA-billed periods common to bill_periods_electric.csv and
    electric_bill_summary.csv, with every design-required invariant checked and
    the corpus refusing to load (SystemExit, naming the offender) if any fail."""
    if not PERIODS_CSV.exists():
        raise SystemExit(f"reprice_by_vintage: missing {PERIODS_CSV}")
    if not SUMMARY_CSV.exists():
        raise SystemExit(f"reprice_by_vintage: missing {SUMMARY_CSV}")

    bpe_rows = list(csv.DictReader(open(PERIODS_CSV, newline="")))
    summary_rows = list(csv.DictReader(open(SUMMARY_CSV, newline="")))

    summary_periods = {r["period"] for r in summary_rows}
    bpe_by_period = {r["period"]: r for r in bpe_rows}
    bpe_periods = set(bpe_by_period)

    only_in_summary = sorted(summary_periods - bpe_periods)
    if only_in_summary:
        raise SystemExit(
            "reprice_by_vintage: electric_bill_summary.csv names periods absent from "
            f"bill_periods_electric.csv: {only_in_summary}. The two artifacts must agree "
            "on the 13-period corpus; refusing to reprice a drifted corpus.")

    matched = [bpe_by_period[p] for p in sorted(summary_periods, key=lambda p: _period_dates(p)[0])]
    if len(matched) != len(summary_periods):
        raise SystemExit(
            "reprice_by_vintage: internal error matching periods between "
            "bill_periods_electric.csv and electric_bill_summary.csv")

    bad_provider = [r["period"] for r in matched if r["generation_provider"] != "CCA"]
    if bad_provider:
        raise SystemExit(
            "reprice_by_vintage: this script's design assumes every one of the 13 "
            "corpus periods is CCA-billed (generation is substituted from the real "
            "billed cca_generation figure, never modeled). Found non-CCA "
            f"generation_provider on: {bad_provider}. Refusing to silently do the "
            "wrong thing -- this script needs a bundled-period design before it can "
            "run on a corpus that includes one.")

    total_days = sum(int(float(r["days"])) for r in matched)
    if total_days != 365:
        raise SystemExit(
            f"reprice_by_vintage: the 13 corpus periods' days column sums to "
            f"{total_days}, not 365 -- the bill-aligned window is not a clean "
            "365-day year; refusing to compare it against a 365-day model window.")

    for r in matched:
        delivery = float(r["sdge_delivery"])
        gen = float(r["cca_generation"])
        cur = float(r["current_charges"])
        if abs((delivery + gen) - cur) > 0.005:
            raise SystemExit(
                f"reprice_by_vintage: [{r['period']}] current_charges {cur:.2f} != "
                f"sdge_delivery {delivery:.2f} + cca_generation {gen:.2f} = "
                f"{delivery + gen:.2f} -- bill_periods_electric.csv is internally "
                "inconsistent for this period; refusing to build actual_total_sum "
                "from an inconsistent row.")

    out = []
    for r in matched:
        start, end = _period_dates(r["period"])
        out.append(dict(
            period=r["period"], start=start, end=end,
            days=int(float(r["days"])),
            net_kwh=float(r["net_kwh"]), gross_kwh=float(r["gross_kwh"]),
            sdge_delivery=float(r["sdge_delivery"]),
            cca_generation=float(r["cca_generation"]),
            current_charges=float(r["current_charges"]),
            fixed_charge_total=float(r["fixed_charge_total"])))

    # _bill_window_all_current_vintage_modeled() filters the interval frame to
    # [out[0]["start"], out[-1]["end"]] and treats that as THE bill-aligned
    # window -- correct only if the 13 periods are contiguous and
    # non-overlapping (a gap would silently drop days from that window; an
    # overlap would silently double-count them). The days columns already sum
    # to 365 (checked above); the calendar span of the first-to-last date must
    # match that exactly, or the periods don't tile the window cleanly.
    window_span_days = (out[-1]["end"] - out[0]["start"]).days + 1
    if window_span_days != total_days:
        raise SystemExit(
            f"reprice_by_vintage: the 13 periods span {window_span_days} calendar "
            f"days from {out[0]['start']} to {out[-1]['end']}, but their own days "
            f"columns sum to {total_days} -- the periods are not contiguous (a gap "
            "or overlap), so the bill-aligned window cannot be treated as one "
            "continuous span")
    return out


# ---------------------------------------------------------------------------
# Step 2/3b: interval-data coverage check, kept separate and pure so it is
# testable on fabricated frames without touching usage.csv or rates_history.
# ---------------------------------------------------------------------------
def _check_coverage(available_dates, periods):
    """Fail closed, naming EVERY uncoverable period and its shortfall, if
    usage.csv's date coverage does not fully span every one of the 13 periods.
    A gap here is a real limitation to report, not a bug to paper over."""
    problems = []
    for p in periods:
        want = {p["start"] + dt.timedelta(days=i)
                for i in range((p["end"] - p["start"]).days + 1)}
        missing = sorted(want - available_dates)
        if missing:
            problems.append(
                f"{p['period']}: missing {len(missing)} of {len(want)} days "
                f"({', '.join(str(d) for d in missing[:5])}"
                f"{', ...' if len(missing) > 5 else ''})")
    if problems:
        raise SystemExit(
            "reprice_by_vintage: usage.csv does not fully cover the 13-period bill "
            "window -- cannot reprice on partial data. Uncoverable periods:\n  "
            + "\n  ".join(problems))


# ---------------------------------------------------------------------------
# Step 3c/3d/3e: delivery at both vintages + PCIA's current-vintage net kWh.
# ---------------------------------------------------------------------------
def _delivery_and_pcia_kwh(sub):
    """(delivery_current_vintage_usd, delivery_own_vintage_usd,
    pcia_positive_net_kwh, unpriced_net_kwh, unpriced_days,
    negative_bucket_mechanics_gap_usd) for one period's interval slice `sub`.

    DEVIATION FROM THE LITERAL DESIGN (flagged, not hidden -- see the module
    docstring and the delivered report): design step 3c calls
    rates_history.bill_nem_monthly(frame, delivery_only=True) directly on the
    whole period. Run against the real corpus, that raises for 11 of the 13
    periods: this household net-EXPORTED during the off-peak TOU hours
    (weekday 6-10am/2-4pm, weekend 2-4pm) on every single statement, in both
    seasons, for most of the analysis year -- so no bill ever printed a
    positive off-peak delivery kWh line there, and rates_history has no
    evidence to source a historical off-peak rate from (data/rate_vintages.csv:
    delivery/summer/off_peak is flagged "absent" 2024-05-25..2026-05-31;
    delivery/winter/off_peak is absent across several sub-ranges). Because
    bill_nem_monthly raises the INSTANT it meets one such day, a single
    unpriced day anywhere in a 30-day period (sometimes just 3-4 days at a
    period's edge) discards every other day's perfectly good rate evidence.

    This function reproduces bill_nem_monthly's own per-(CALENDAR MONTH,
    season, TOU-period) netting algorithm (bucket by day, group by rate value,
    bill only buckets whose aggregated net kWh is positive) via
    RateSet.cells() -- a NON-RAISING per-cell lookup the same public API
    already exposes -- so a day with no sourced rate is excluded from the
    OWN-vintage rate-bucketing instead of aborting the whole period, and its
    net kWh is priced at the CURRENT-vintage rate (rates.UDC) as a separate
    "unpriced" bucket within the same per-rate-value netting rule
    bill_nem_monthly already uses. Using the current rate for the unpriced
    slice is not a historical guess: it is the only delivery rate this repo has
    ANY evidence for on that specific (season, TOU) cell, so it contributes
    IDENTICALLY to both the current-vintage and own-vintage totals -- adding
    exactly zero to delivery_vintage_effect for that energy, and leaving
    whatever true historical vintage difference existed there (if any) inside
    residual_total instead, where it is reported as not determined rather than
    guessed.

    THE MONTHLY RESTART (fixed after adversarial review pass 1, finding 2):
    NEM 2.0 nets per CALENDAR MONTH, not per ~30-day bill cycle (rates.py's own
    docstring: "energy charges netted per (month, season, TOU period)";
    billing_model_nem.bill() and rates_history.bill_nem_monthly() both group by
    calendar month, restarting the netting at every month boundary). An
    earlier version of this function bucketed by (season, TOU) alone over the
    WHOLE bill period, so a period straddling a calendar-month boundary with a
    sign flip in some bucket across that boundary netted incorrectly
    (under-billed by ~$11 total across the affected periods). Grouping by
    (ym, season, TOU) -- `ym` is the interval frame's own calendar-month column
    from billing_model_nem.load(), never overridden here -- restarts the
    netting at each true month boundary WITHIN this period's own date range,
    matching bill_nem_monthly's convention exactly. This never reaches across
    a BILL PERIOD boundary into another period's data (each call only ever
    sees this period's own `sub`), which is the correct scope: a real
    statement settles NEM only over its own printed dates.

    Current-vintage delivery (rates.UDC) needs no absence handling: it is a
    fixed table, sourced for every (season, TOU) cell regardless of bill
    evidence, so it nets each (month, season, TOU) bucket's FULL kWh (priced +
    unpriced) in one shot, exactly as design step 3d describes. PCIA's
    positive-net-kWh accumulator uses that same full per-bucket net (design
    step 3e: "same grouping as (d)"), independent of the own/current-vintage
    delivery split entirely.

    NEGATIVE-BUCKET MECHANICS GAP (diagnostic for issue #30's Bug 1 fix). A
    net-negative (month, season, TOU) bucket contributes ZERO here (matching
    rates_history.py's own "in-period exports settle at true-up" convention,
    which delivery_own_vintage is built on -- both current- and own-vintage
    delivery must use the SAME negative-bucket convention for
    delivery_vintage_effect to compare like with like).

    THE CAUSE IS NOT "bmn.bill() credits a bucket this function zero-clamps"
    (adversarial review pass 3, finding 1: an earlier version of this
    docstring said exactly that, and it is wrong about the mechanism, even
    though the dollar figure it produces is correct). bmn.bill()'s REAL
    invocation, inside _bill_window_all_current_vintage_modeled(), runs on the
    WHOLE bill-aligned window as one continuous frame -- and on this
    household's data, no (calendar month, season, TOU) bucket is genuinely
    net-negative anywhere in that continuous scope: calling THIS function on
    the continuous window (one frame spanning periods[0].start..periods[-1].end,
    not summed per bill period) returns a mechanics gap of EXACTLY $0.00 (this
    is a checkable fact, not an assumption -- run it yourself). bmn.bill()
    never reaches its credit() branch for any of these buckets, because
    there is nothing to reach it for.

    What actually happens is narrower: bill periods do not align with
    calendar-month boundaries, and this function is called separately PER BILL
    PERIOD (build() sums its per-period results into
    bill_window_current_vintage_total, never calling it on the continuous
    window). A calendar month that is net-positive OVERALL, when split at a
    bill-period boundary into two per-period fragments, can have one fragment
    individually net-negative even though the whole month is not -- and this
    function, seeing only that fragment (never the whole month, since each
    call is scoped to one bill period's own dates), zero-clamps it. The value
    returned here is what THIS function's own accumulator would have credited
    (at UDC+CEA, the credit() rate) for every such fragment, across every bill
    period -- a measure of the SIZE of the per-bill-period-restart artifact,
    not of a real negative NEM bucket bmn.bill() prices differently. Restarting
    at bill-period boundaries is still the correct scope for
    current_vintage_total and delivery_own_vintage (a real statement settles
    NEM only over its own printed dates -- see the monthly-restart note above),
    so this artifact is an unavoidable side effect of computing per-period
    totals that are later compared against a continuous-window reference, not
    a bug to fix here; it is reported so the caveat on
    generation_and_fixed_charge_vintage_effect is quantified, not asserted.

    delivery_vintage_effect is NOT affected by this scope difference: both
    delivery_own_vintage and delivery_current_vintage are computed by THIS
    SAME function, with the SAME per-bill-period restart, so any phantom
    negative fragment is zero-clamped identically on both sides and cancels
    out of their difference exactly like the unpriced-delivery slice does.

    SIGN, stated explicitly because it is easy to get backwards (adversarial
    review pass 2, finding 1 -- an earlier, separate issue from the causal one
    above, already fixed): this function's own accumulator contributes ZERO
    for a negative fragment;
    what it WOULD have credited there is net*(UDC+CEA) (negative, since
    net<0), so the disagreement this diagnostic reports is 0 - net*(UDC+CEA) =
    -net*(UDC+CEA), summed over every such fragment, at current-vintage rates.
    A POSITIVE return value means the sum of per-period zero-clamping made
    bill_window_current_vintage_total HIGHER than a continuous-window
    computation would have been, for this mechanical reason alone --
    consistent with generation_and_fixed_charge_vintage_effect
    (current_vintage_total minus the all-current-vintage-modeled total) being
    pushed upward (less negative, or more positive) by this same amount."""
    delivery_current_vintage = 0.0
    delivery_own_vintage = 0.0
    pcia_positive_kwh = 0.0
    unpriced_kwh = 0.0
    unpriced_days = set()
    negative_bucket_mechanics_gap_usd = 0.0

    for (_ym, seas, p), grp in sub.groupby(["ym", "seas", "p"]):
        season = rates_history._SEASON_FOR_SEAS[seas]
        long_tp = rates_history._LONG_FOR_SHORT[p]
        cur_rate = rates.UDC[seas][p]

        # current vintage + PCIA: the (month, season, TOU) bucket's FULL net,
        # one shot (3d/3e) -- restarted at every calendar-month boundary.
        bucket_net = float(grp["Consumption"].sum() - grp["Generation"].sum())
        if bucket_net > 0:
            delivery_current_vintage += bucket_net * cur_rate
            pcia_positive_kwh += bucket_net
        elif bucket_net < 0:
            # disagreement = current_vintage_total's contribution (0) minus
            # bmn.bill()'s real-credit contribution (net*(UDC+CEA)) -- i.e.
            # the NEGATION of bmn.bill()'s own per-bucket credit term, not the
            # credit term itself (adversarial review pass 2, finding 1: an
            # earlier version stored bmn.bill()'s raw credit contribution
            # here, sign and all, rather than the disagreement it was
            # documented as).
            negative_bucket_mechanics_gap_usd += -bucket_net * (cur_rate + rates.CEA[seas][p])

        # own vintage: bucket by (sourced rate | "unpriced, current-rate
        # fallback") across calendar days WITHIN this (month, season, TOU)
        # group, then bill only positive buckets -- bill_nem_monthly's own
        # rule, generalized with one extra bucket.
        own_spans = {}
        for day, dsub in grp.groupby(grp.dt.dt.date):
            net_day = float(dsub["Consumption"].sum() - dsub["Generation"].sum())
            cv = rates_history.rates_on(day).cells()[("delivery", season, long_tp)]
            if cv.rate is None:
                key = "unpriced"
                unpriced_days.add(day)
            else:
                key = cv.rate
            own_spans[key] = own_spans.get(key, 0.0) + net_day
        for key, net in own_spans.items():
            if net > 0:
                if key == "unpriced":
                    delivery_own_vintage += net * cur_rate
                    unpriced_kwh += net
                else:
                    delivery_own_vintage += net * key

    return (delivery_current_vintage, delivery_own_vintage, pcia_positive_kwh,
            unpriced_kwh, unpriced_days, negative_bucket_mechanics_gap_usd)


# ---------------------------------------------------------------------------
# Step 3: per-period dollar figures and kWh reconciliation.
# ---------------------------------------------------------------------------
def _per_period_figures(d, row):
    """One period's reconciliation + dollar figures (design steps 3b-3i).
    `d` is the FULL interval frame from billing_model_nem.load() (or a
    structurally identical fabricated frame in tests); `row` is one entry from
    _load_periods() (or a fabricated equivalent with the same keys)."""
    start, end, label = row["start"], row["end"], row["period"]
    sub = d[(d.dt.dt.date >= start) & (d.dt.dt.date <= end)]

    interval_gross_kwh = float(sub["Consumption"].sum())
    interval_export_kwh = float(sub["Generation"].sum())
    interval_net_kwh = interval_gross_kwh - interval_export_kwh

    # (c)/(d)/(e): delivery at both vintages, plus PCIA's current-vintage net
    # kWh, computed together per (calendar month, season, TOU) bucket -- see
    # _delivery_and_pcia_kwh's own docstring for why this deviates from a
    # direct rates_history.bill_nem_monthly() call (design step 3c's literal
    # instruction): most of this corpus's off-peak delivery cells have NO
    # bill-sourced rate at all for most of the analysis year (this household
    # net-exported during those specific hours until a recent shift), which
    # makes bill_nem_monthly refuse the ENTIRE period the moment it meets one
    # such day, discarding every other day's perfectly good evidence. The
    # (calendar month, season, TOU) bucketing (not just (season, TOU)) matches
    # NEM 2.0's real monthly-restart netting rule (adversarial review pass 1,
    # finding 2).
    (delivery_current_vintage, delivery_own_vintage, pcia_positive_kwh,
     unpriced_kwh, unpriced_days,
     negative_bucket_mechanics_gap_usd) = _delivery_and_pcia_kwh(sub)
    pcia_current = rates.PCIA * pcia_positive_kwh

    # (f) NBC on GROSS imports, never netted -- matches billing_model_nem.bill().
    nbc_current = rates.NBC * interval_gross_kwh

    # (g)/(h): real billed figures, substituted rather than modeled, identical
    # in both vintage variants (this is what makes them cancel out of
    # delivery_vintage_effect and not explain it).
    fixed_charge_actual = row["fixed_charge_total"]
    generation_actual = row["cca_generation"]

    current_vintage_total = (delivery_current_vintage + pcia_current + nbc_current
                             + fixed_charge_actual + generation_actual)
    own_vintage_total = (delivery_own_vintage + pcia_current + nbc_current
                         + fixed_charge_actual + generation_actual)
    actual_total = row["current_charges"]

    # own vs current, delivery-only vintage difference (rates_history.py's
    # historical UDC vs rates.py's current UDC) -- everything else in the two
    # totals above is identical, so this isolates delivery cleanly.
    delivery_vintage_effect = own_vintage_total - current_vintage_total
    # Actual minus modeled (see module docstring's sign-convention note) --
    # this is the convention that lets per-period residuals sum exactly to
    # residual_total in the aggregate telescoping identity.
    residual = actual_total - own_vintage_total

    gross_kwh_diff = interval_gross_kwh - row["gross_kwh"]
    net_kwh_diff = interval_net_kwh - row["net_kwh"]

    return dict(
        period=label, start=str(start), end=str(end),
        bill_gross_kwh=row["gross_kwh"], interval_gross_kwh=interval_gross_kwh,
        gross_kwh_diff=gross_kwh_diff,
        gross_kwh_diff_pct=(100.0 * gross_kwh_diff / row["gross_kwh"])
        if row["gross_kwh"] else None,
        bill_net_kwh=row["net_kwh"], interval_net_kwh=interval_net_kwh,
        net_kwh_diff=net_kwh_diff,
        delivery_current_vintage_usd=delivery_current_vintage,
        delivery_own_vintage_usd=delivery_own_vintage,
        pcia_usd=pcia_current,
        nbc_usd=nbc_current,
        fixed_charge_actual_usd=fixed_charge_actual,
        generation_actual_usd=generation_actual,
        current_vintage_total_usd=current_vintage_total,
        own_vintage_total_usd=own_vintage_total,
        actual_total_usd=actual_total,
        delivery_vintage_effect_usd=delivery_vintage_effect,
        residual_usd=residual,
        unpriced_delivery_kwh=unpriced_kwh,
        unpriced_delivery_days=len(unpriced_days),
        negative_bucket_mechanics_gap_usd=negative_bucket_mechanics_gap_usd,
    )


# ---------------------------------------------------------------------------
# Step 4: native window total (billing_model_nem's own __main__ block, fresh).
# ---------------------------------------------------------------------------
def _native_window_total(d_full):
    """billing_model_nem's own native 365-day rolling window
    (2025-07-25..2026-07-23), current vintage throughout, computed exactly as
    that script's own __main__ block does -- from the SAME loaded frame
    (billing_model_nem.load() is a deterministic function of usage.csv, so
    filtering the already-loaded frame is byte-identical to a fresh load())."""
    end = NATIVE_WINDOW_END
    start = end - dt.timedelta(days=NATIVE_WINDOW_DAYS)
    d = d_full[(d_full.dt >= start) & (d_full.dt < end)].copy()
    rates.validate_interval_coverage(
        zip(d.dt.dt.date, d.dt.dt.hour + d.dt.dt.minute / 60),
        start.date(), (end - dt.timedelta(days=1)).date())
    return bmn.bill(d)


def _bill_window_all_current_vintage_modeled(d_full, start, end):
    """The bill-aligned window (start..end, inclusive), priced with
    billing_model_nem.bill()'s EXACT current-vintage methodology --
    unmodified, called directly, not reimplemented -- restricted to these
    dates instead of the native window's. This is the correct "current
    vintage, modeled, nothing substituted" reference point for isolating
    window_effect (fixed after adversarial review pass 1, finding 1 -- see the
    module docstring's "THE DECOMPOSITION" section for why
    bill_window_current_vintage_total, which substitutes REAL generation and
    fixed-charge dollars, is NOT this reference point and must not be compared
    directly against native_window_total)."""
    sub = d_full[(d_full.dt.dt.date >= start) & (d_full.dt.dt.date <= end)].copy()
    return bmn.bill(sub)


# ---------------------------------------------------------------------------
# Step 4: pure aggregation + the telescoping identity check -- kept free of
# usage.csv/rates_history so it is directly testable on fabricated per-period
# figures.
# ---------------------------------------------------------------------------
def _aggregate(per_period, native_window_total,
               bill_window_all_current_vintage_modeled_total):
    bill_window_current_vintage_total = sum(p["current_vintage_total_usd"] for p in per_period)
    bill_window_own_vintage_total = sum(p["own_vintage_total_usd"] for p in per_period)
    actual_total_sum = sum(p["actual_total_usd"] for p in per_period)
    residual_total = sum(p["residual_usd"] for p in per_period)

    # window_effect: native window -> bill-aligned window, BOTH sides modeled
    # by bmn.bill()'s own unmodified methodology -- a clean, same-methodology
    # comparison (adversarial review pass 1, finding 1's fix).
    window_effect = bill_window_all_current_vintage_modeled_total - native_window_total
    # generation_and_fixed_charge_vintage_effect: substituting the REAL billed
    # generation and fixed-charge dollars for bmn.bill()'s current-vintage
    # MODEL of them (CEA table on interval kWh; BSC*days), window held fixed.
    # See _build_notes' caveat on this term -- it is mostly, but not purely, a
    # vintage effect (module docstring, "THE DECOMPOSITION").
    generation_and_fixed_charge_vintage_effect = (
        bill_window_current_vintage_total - bill_window_all_current_vintage_modeled_total)
    # delivery_vintage_effect: own-vintage UDC vs current-vintage UDC, real
    # generation/fixed-charge held fixed on both sides (cancels out).
    delivery_vintage_effect = bill_window_own_vintage_total - bill_window_current_vintage_total
    total_vintage_effect = delivery_vintage_effect + generation_and_fixed_charge_vintage_effect
    # residual_total, defined above as the sum of the (actual - own_vintage)
    # per-period residuals, is ALSO exactly actual_total_sum -
    # bill_window_own_vintage_total (same telescoping convention) -- assert
    # the two routes to it agree before trusting either.
    residual_total_direct = actual_total_sum - bill_window_own_vintage_total
    if abs(residual_total - residual_total_direct) > 0.005:
        raise SystemExit(
            "reprice_by_vintage: residual_total (sum of per-period residuals, "
            f"${residual_total:.2f}) does not match actual_total_sum - "
            f"bill_window_own_vintage_total (${residual_total_direct:.2f}) -- "
            "per-period arithmetic is inconsistent with the aggregate")

    identity_lhs = (native_window_total + window_effect
                    + generation_and_fixed_charge_vintage_effect
                    + delivery_vintage_effect + residual_total)
    if abs(identity_lhs - actual_total_sum) > 0.005:
        raise SystemExit(
            "reprice_by_vintage: the telescoping identity native_window_total + "
            "window_effect + generation_and_fixed_charge_vintage_effect + "
            "delivery_vintage_effect + residual_total == actual_total_sum does not "
            f"hold: {identity_lhs:.2f} != {actual_total_sum:.2f} -- this is pure "
            "arithmetic and must be exact; an aggregation bug, not a data finding")

    return dict(
        native_window_total=native_window_total,
        bill_window_all_current_vintage_modeled_total=bill_window_all_current_vintage_modeled_total,
        window_effect=window_effect,
        generation_and_fixed_charge_vintage_effect=generation_and_fixed_charge_vintage_effect,
        delivery_vintage_effect=delivery_vintage_effect,
        total_vintage_effect=total_vintage_effect,
        residual_total=residual_total,
        actual_total_sum=actual_total_sum,
        bill_window_current_vintage_total=bill_window_current_vintage_total,
        bill_window_own_vintage_total=bill_window_own_vintage_total,
        identity_check_usd=identity_lhs,
        identity_holds=True,
    )


# ---------------------------------------------------------------------------
# notes: computed from the actual numbers, never asserted ahead of seeing them.
# ---------------------------------------------------------------------------
def _kwh_reconciliation_notes(per_period):
    gross_diffs = [p["gross_kwh_diff"] for p in per_period]
    net_diffs = [p["net_kwh_diff"] for p in per_period]
    gross_pcts = [abs(p["gross_kwh_diff_pct"]) for p in per_period
                  if p["gross_kwh_diff_pct"] is not None]
    max_abs_gross_diff_kwh = max(abs(x) for x in gross_diffs)
    max_abs_gross_diff_pct = max(gross_pcts) if gross_pcts else None
    total_abs_gross_diff_kwh = sum(abs(x) for x in gross_diffs)
    total_signed_gross_diff_kwh = sum(gross_diffs)
    max_abs_net_diff_kwh = max(abs(x) for x in net_diffs)
    total_abs_net_diff_kwh = sum(abs(x) for x in net_diffs)

    material = max_abs_gross_diff_pct is not None and max_abs_gross_diff_pct >= 1.0
    verdict = (
        "the interval data's per-period gross kWh diverges from the bills' own "
        f"gross_kwh by up to {max_abs_gross_diff_pct:.2f}% in at least one period "
        "-- large enough that metering/window-slicing is a plausible contributor "
        "to residual_total alongside this artifact's other named candidates "
        "(pcia_nbc_generation_vintage_limitation, unpriced_delivery_limitation, "
        "residual_concentration); none should be ruled out until further narrowed"
        if material else
        "every period's interval-derived gross kWh agrees with the bill's own "
        f"gross_kwh to within {max_abs_gross_diff_pct:.2f}% (worst period) -- small "
        "enough that metering/window-slicing is NOT a material contributor to "
        "residual_total, which strengthens (without proving) this artifact's other "
        "named candidates (pcia_nbc_generation_vintage_limitation, "
        "unpriced_delivery_limitation, residual_concentration) as the more likely "
        "explanations"
    )
    return dict(
        max_abs_gross_kwh_diff=max_abs_gross_diff_kwh,
        max_abs_gross_kwh_diff_pct=max_abs_gross_diff_pct,
        total_abs_gross_kwh_diff=total_abs_gross_diff_kwh,
        total_signed_gross_kwh_diff=total_signed_gross_diff_kwh,
        max_abs_net_kwh_diff=max_abs_net_diff_kwh,
        total_abs_net_kwh_diff=total_abs_net_diff_kwh,
        material=material,
        verdict=verdict,
    )


# The date rates.py's own docstring records for the weekday 10am-2pm
# super-off-peak window taking effect (before which those hours were
# off-peak). billing_model_nem.load() assigns every historical interval's TOU
# period via rates.period_at(), which applies THIS CURRENT window uniformly to
# every date -- a documented, pre-existing limitation of that reused,
# unmodified module (rates.py's own "Two things it deliberately does not do"
# note), not something this script introduces or can correct without
# rebuilding TOU assignment from historical windows (out of scope here).
_TOU_WINDOW_CHANGE_DATE = dt.date(2026, 3, 1)


def _residual_concentration_note(per_period):
    """Whether the residual concentrates before/after the one documented TOU-
    window vintage change in this corpus -- computed and reported because the
    data actually shows a strong split, not asserted ahead of seeing it."""
    before = [p for p in per_period if p["end"] < str(_TOU_WINDOW_CHANGE_DATE)]
    after = [p for p in per_period if p["end"] >= str(_TOU_WINDOW_CHANGE_DATE)]
    before_sum = sum(p["residual_usd"] for p in before)
    after_sum = sum(p["residual_usd"] for p in after)
    total = before_sum + after_sum
    pct_before = (100.0 * before_sum / total) if total else None
    return dict(
        window_change_date=str(_TOU_WINDOW_CHANGE_DATE),
        periods_ending_before=[p["period"] for p in before],
        periods_ending_on_or_after=[p["period"] for p in after],
        residual_before_usd=before_sum,
        residual_on_or_after_usd=after_sum,
        pct_of_residual_before=pct_before,
        note=(
            "OBSERVED, NOT CONFIRMED: "
            f"{pct_before:.1f}% of residual_total falls in periods ending before "
            f"{_TOU_WINDOW_CHANGE_DATE} (the weekday 10am-2pm super-off-peak window's "
            "documented effective date -- before it, those hours were off-peak). "
            "billing_model_nem.load() assigns every historical 15-minute interval's "
            "TOU period via rates.period_at(), the CURRENT window rule, applied "
            "uniformly to every date in this analysis (both here and in the native "
            "$4,904 model) -- a documented, pre-existing limitation of that reused, "
            "unmodified module (rates.py's own docstring: 'wrong for reproducing a "
            "historical statement, where it misallocates 250-360 kWh per period "
            "between off-peak and super-off-peak'), not something this script "
            "introduces. The near-exact alignment of the residual's drop-off with "
            "this date is circumstantial, not a proven causal decomposition -- "
            "rebuilding TOU assignment from the historical window shapes is out of "
            "scope for this script." if pct_before is not None else
            "residual_total is zero; no concentration to report."),
    )


def _build_notes(per_period, residual_total, generation_and_fixed_charge_vintage_effect):
    kwh = _kwh_reconciliation_notes(per_period)
    total_unpriced_kwh = sum(p["unpriced_delivery_kwh"] for p in per_period)
    total_unpriced_days = sum(p["unpriced_delivery_days"] for p in per_period)
    total_mechanics_gap = sum(p["negative_bucket_mechanics_gap_usd"] for p in per_period)
    material = abs(residual_total) >= 5.0
    return dict(
        pcia_nbc_generation_vintage_limitation=(
            "rates_history.py can source the UDC delivery tariff actually in force "
            "on any historical date in the bill corpus (bill_tou_detail.csv's "
            "printed 'Rate/kWh' lines), but it cannot source historical PCIA, "
            "non-bypassable-charge (NBC), or CCA/CEA generation rates -- no "
            "committed artifact carries any of the three (rates_history.py's own "
            "module docstring; RateSet.pcia/.nbc/.cca_generation all refuse for "
            "this reason). PCIA and NBC are held at the current rates.py vintage in "
            "current_vintage_total, own_vintage_total AND bill_window_all_current_"
            "vintage_modeled_total alike, so they cancel out of every vintage term "
            "(delivery_vintage_effect and generation_and_fixed_charge_vintage_effect "
            "both) and cannot explain either. All 13 corpus periods are CCA-billed, "
            "so generation is instead substituted from the real billed "
            "cca_generation dollar figure (issue #30 AC4's 'explicitly separate "
            "supply treatment') everywhere this script computes a vintage-current "
            "total that isn't bmn.bill()'s own model -- see generation_and_fixed_"
            "charge_vintage_caveat for where that substitution DOES show up. PCIA "
            "and NBC CAN still be part of residual_total: if the actual historical "
            "PCIA/NBC rates differed from what is substituted here, that difference "
            "would show up in residual_total with no way for this script to "
            "separate it from a genuine model-vs-bill mechanics gap."
            + (" Given residual_total is not close to zero, this is one of three "
               "candidate contributors to it (see unpriced_delivery_limitation and "
               "residual_concentration for the other two)."
               if material else
               " residual_total is small enough here that this limitation is "
               "largely academic for this dataset, but it remains structurally "
               "unresolved.")),
        generation_and_fixed_charge_vintage_caveat=(
            "generation_and_fixed_charge_vintage_effect is mostly, but not purely, "
            "a vintage effect. It differences bill_window_current_vintage_total "
            "(real billed generation and fixed-charge dollars substituted; delivery "
            "and PCIA computed by _delivery_and_pcia_kwh called separately PER BILL "
            "PERIOD and summed) against bill_window_all_current_vintage_modeled_"
            "total (billing_model_nem.bill() called ONCE on the whole bill-aligned "
            "window as a continuous frame). Those two totals disagree on a second, "
            "smaller, MECHANICAL effect having nothing to do with generation or the "
            "fixed charge: bill periods do not align with calendar-month "
            "boundaries, so a calendar month that is net-positive OVERALL can be "
            "split at a bill-period boundary into two per-period fragments where "
            "one fragment alone is net-negative; computed per period (as "
            "bill_window_current_vintage_total is), that fragment zero-clamps to "
            "$0, losing a netting offset the same month would keep if priced "
            "continuously the way bmn.bill() actually is. This is NOT bmn.bill()'s "
            "real invocation crediting a bucket this script zero-clamps instead -- "
            "verified directly: calling _delivery_and_pcia_kwh on the bill-aligned "
            "window as ONE continuous frame returns a mechanics gap of exactly "
            "$0.00, because no bucket is genuinely net-negative anywhere in the "
            "continuous window bmn.bill() actually sees; every occurrence this "
            "script finds is an artifact of its own per-bill-period restart, not a "
            "real NEM credit bmn.bill() applies differently. "
            f"negative_bucket_mechanics_gap_usd = ${total_mechanics_gap:.2f} is "
            "exactly the size of that restart artifact, summed over every bill "
            "period's own _delivery_and_pcia_kwh call, so it is quantified rather "
            "than merely asserted. Against a generation_and_fixed_charge_vintage_"
            f"effect of ${generation_and_fixed_charge_vintage_effect:.2f}, the "
            "mechanics gap is "
            + (f"{abs(100.0 * total_mechanics_gap / generation_and_fixed_charge_vintage_effect):.1f}% "
               "of the term's own size -- material enough that this term should be "
               "read as 'mostly generation/fixed-charge vintage, with a real, "
               "quantified per-bill-period-restart contribution,' not as a pure "
               "vintage measurement."
               if generation_and_fixed_charge_vintage_effect else
               "not meaningfully comparable (the term itself is at or near zero).")
            + " total_vintage_effect therefore carries the same caveat: it is the "
            "right combined number to answer 'how much of the gap is rate vintage,' "
            "but it is not as cleanly isolated as delivery_vintage_effect alone. "
            "delivery_vintage_effect itself is NOT affected by this per-period-vs-"
            "continuous scope difference: both delivery_own_vintage and "
            "delivery_current_vintage come from the SAME per-bill-period-restarted "
            "calls, so any phantom negative fragment zero-clamps identically on "
            "both sides and cancels out of their difference."),
        unpriced_delivery_limitation=(
            "delivery/off_peak (both seasons) is unsourceable on "
            f"{total_unpriced_days} calendar-day(s) across the 13-period corpus "
            f"(totaling {total_unpriced_kwh:.1f} net kWh at current-vintage rates) "
            "because no statement in the whole bill corpus ever printed a positive "
            "off-peak delivery kWh line there -- this household net-exported during "
            "those specific hours until a recent shift (see "
            "_delivery_and_pcia_kwh's docstring and data/rate_vintages.csv's "
            "'absent' delivery/off_peak spans). That energy is priced at the "
            "current-vintage rate on BOTH sides of delivery_vintage_effect (the "
            "only rate this repo has any evidence for there), so it contributes "
            "zero to delivery_vintage_effect by construction, but any genuine "
            "historical difference for that specific slice is absorbed into "
            "residual_total instead, indistinguishable there from a real "
            "model-vs-bill mechanics gap."
            + (" Given residual_total is not close to zero, this is one of three "
               "candidate contributors to it (see pcia_nbc_generation_vintage_"
               "limitation and residual_concentration for the other two)."
               if material else
               " residual_total is small enough here that this limitation is "
               "largely academic for this dataset, but it remains structurally "
               "unresolved.")),
        residual_concentration=_residual_concentration_note(per_period),
        kwh_reconciliation=kwh,
        out_of_scope=(
            "Sourcing historical PCIA/NBC/CCA-generation rates by any means beyond "
            "what rates_history.py already provides is out of scope for this "
            "script (issue #27, 'independent oracle for historical rate "
            "vintages')."),
    )


# ---------------------------------------------------------------------------
# build() / write / main
# ---------------------------------------------------------------------------
def build():
    periods = _load_periods()
    d = bmn.load()

    available_dates = set(d.dt.dt.date)
    _check_coverage(available_dates, periods)

    per_period = [_per_period_figures(d, row) for row in periods]
    native_window_total = _native_window_total(d)
    bill_window_all_current_vintage_modeled_total = _bill_window_all_current_vintage_modeled(
        d, periods[0]["start"], periods[-1]["end"])
    agg = _aggregate(per_period, native_window_total,
                     bill_window_all_current_vintage_modeled_total)
    notes = _build_notes(per_period, agg["residual_total"],
                         agg["generation_and_fixed_charge_vintage_effect"])

    return dict(
        meta=dict(
            issue="#30 -- reprice the billed year at its own tariff vintages",
            bill_window=[str(periods[0]["start"]), str(periods[-1]["end"])],
            native_window=[str((NATIVE_WINDOW_END
                               - dt.timedelta(days=NATIVE_WINDOW_DAYS)).date()),
                          str((NATIVE_WINDOW_END - dt.timedelta(days=1)).date())],
            n_periods=len(periods),
            data_sources=[
                "data/bill_periods_electric.csv (13-period corpus, cross-checked "
                "against data/electric_bill_summary.csv)",
                "analysis/rates_history.py (historical UDC delivery tariff, "
                "delivery_only)",
                "analysis/rates.py (current-vintage UDC/PCIA/NBC constants)",
                "analysis/billing_model_nem.py (native rolling-window model and "
                "current-vintage modeled reference, reused read-only)",
                "private raw Green Button 15-min export (usage.csv)",
            ],
        ),
        native_window_total=agg["native_window_total"],
        bill_window_all_current_vintage_modeled_total=(
            agg["bill_window_all_current_vintage_modeled_total"]),
        window_effect=agg["window_effect"],
        generation_and_fixed_charge_vintage_effect=(
            agg["generation_and_fixed_charge_vintage_effect"]),
        delivery_vintage_effect=agg["delivery_vintage_effect"],
        total_vintage_effect=agg["total_vintage_effect"],
        residual_total=agg["residual_total"],
        actual_total_sum=agg["actual_total_sum"],
        bill_window_current_vintage_total=agg["bill_window_current_vintage_total"],
        bill_window_own_vintage_total=agg["bill_window_own_vintage_total"],
        identity_check_usd=agg["identity_check_usd"],
        identity_holds=agg["identity_holds"],
        per_period=per_period,
        notes=notes,
    )


def _write(result, dest_dir):
    dest_dir = pathlib.Path(dest_dir)
    path = dest_dir / "reprice_by_vintage.json"
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(result, fh, indent=1, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def main():
    result = build()
    path = _write(result, DATA)
    print(f"native window total (current vintage, modeled):        ${result['native_window_total']:.2f}")
    print(f"  + window effect (bill-aligned window, modeled both):   ${result['window_effect']:+.2f}")
    print(f"  + generation/fixed-charge vintage effect (see caveat): ${result['generation_and_fixed_charge_vintage_effect']:+.2f}")
    print(f"  + delivery vintage effect (own historical rate):       ${result['delivery_vintage_effect']:+.2f}")
    print(f"  + residual (not determined further):                   ${result['residual_total']:+.2f}")
    print(f"  = actual billed total:                                ${result['actual_total_sum']:.2f}")
    print(f"total vintage effect (delivery + generation/fixed-charge): "
          f"${result['total_vintage_effect']:+.2f}")
    print(f"identity check: {result['identity_check_usd']:.4f} vs "
          f"{result['actual_total_sum']:.4f} (holds={result['identity_holds']})")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
