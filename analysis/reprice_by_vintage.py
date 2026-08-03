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

THE DECOMPOSITION. Eight quantities, chained so each is "the previous total
plus one more correction," landing exactly on the actual bills. (A fresh
Codex adversarial review of this branch flagged the 5-term version's headline
total_vintage_effect as unsupported: generation_and_fixed_charge_vintage_
effect's -$281.59 was being counted almost entirely as "vintage," but
analysis/cca_rate_extraction.py's own committed data/cca_generation_rates.csv
independently proves CEA's charged per-TOU generation rate never moved once
across the whole CCA era and is IDENTICAL to rates.CEA's current table --
re-verified fresh here, against these specific 13 periods, not assumed from
that module's docstring (see "GENERATION RATE VINTAGE IS ZERO, BY EVIDENCE"
below). The true cause of that -$281.59 was almost entirely the SAME
TOU-window-shape confound this script already named as a residual_total
candidate, showing up in generation dollars specifically -- now quantified
instead of "not determined." A SECOND Codex review pass then found that the
6-term version's generation_tou_window_effect was still not purely a
window-shape effect: it silently carried two real, small, unmodeled
generation-side line items -- CEA's "Clean Impact Plus" (CIP) per-kWh product
adder and a per-period state surcharge tax -- that have no rates.py
counterpart at all, so they cannot support a vintage OR a window-shape claim;
they are just real money bmn.bill()'s model never counts, in every year. Both
are now separated into their own terms (cip_adder_usd, state_surcharge_tax_
usd), narrowing generation_tou_window_effect to what the TOU-window-shape
confound can actually support. This is the corrected, 8-term version.)

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
  + generation_tou_window_effect
                         = generation_clean_tou_effect
                             + delivery_pcia_restart_artifact_usd
                           NOT a vintage effect -- CEA's charged rate is
                           proven flat and equal to today's, so there is no
                           rate-vintage story to tell here. This is instead a
                           quantified measurement of the TOU-window-shape
                           confound (rates.period_at()'s CURRENT window shape
                           applied to historical interval kWh, misclassifying
                           it between on/off/super-off-peak relative to the
                           window shape actually in force -- same confound
                           residual_total's own candidate list already names)
                           acting on generation dollars specifically, plus a
                           small folded-in per-bill-period-restart artifact
                           -- see "GENERATION RATE VINTAGE IS ZERO, BY
                           EVIDENCE" and "THE RESTART ARTIFACT" below. The CIP
                           adder and the state surcharge tax are DELIBERATELY
                           EXCLUDED from this term (see the next two).
  + cip_adder_usd          real, directly-billed CEA "Clean Impact Plus"
                           per-kWh product adder -- no rates.py counterpart at
                           all, so neither a vintage nor a window-shape claim
                           applies; just real money the model never counts.
  + state_surcharge_tax_usd real, directly-billed per-period state surcharge
                           tax -- a flat dollar fee, no per-kWh rate, same
                           reasoning as the CIP adder.
  + fixed_charge_vintage_effect
                         = fixed_charge_actual_sum - fixed_charge_continuous
                           genuinely a vintage/regime effect: the historical
                           flat Monthly Service Fee vs. the current per-day
                           Base Services Charge -- a real, settled structural
                           billing change (issue #7), not disputed.
  + delivery_vintage_effect
                         = bill_window_own_vintage_total
                             - bill_window_current_vintage_total
                           the effect of pricing UDC delivery at the vintage
                           actually in force each period, instead of current,
                           everything else held fixed. Cleanly isolated:
                           everything else in the two totals being differenced
                           is identical -- BUT see "THE SOURCED-VS-TOTAL
                           CAVEAT" below: this cleanliness covers only the kWh
                           rates_history.py can actually source a historical
                           rate for.
  + residual_total         = actual_total_sum - bill_window_own_vintage_total
                           whatever is left after every correction above: the
                           real, previously-undecomposed model-vs-bill gap.
  = actual_total_sum       the bills' own accrued current_charges, $3,282.22.

total_vintage_effect (= delivery_vintage_effect + fixed_charge_vintage_effect
-- generation, the CIP adder, and the state surcharge tax all deliberately
EXCLUDED, per the flatness proof and the "no rates.py counterpart" reasoning
above) is reported alongside the eight terms because it is the number that
answers issue #30's question -- "how much of the gap is rate vintage" -- but
see "THE SOURCED-VS-TOTAL CAVEAT" immediately below before treating it as a
ceiling.

THE SOURCED-VS-TOTAL CAVEAT (a Codex review finding: this is a report-wording
precision issue, not a code bug -- the numbers below were always correct).
delivery_vintage_effect is a CLEAN comparison, but only over the kWh
rates_history.py can actually source a historical delivery rate for.
unpriced_delivery_limitation (see notes) already discloses that 1,168.3 kWh
across 247 days -- the off-peak delivery bucket this household net-exported
through for most of the analysis year -- has NO sourced historical rate at
all, and that slice's own vintage effect is therefore folded into
residual_total, indistinguishable there from a genuine model-vs-bill
mechanics gap. total_vintage_effect is consequently the SOURCED, MEASURABLE
portion of rate vintage, not a ceiling on the true total: if that unpriced
slice's real historical delivery rate differed materially from today's, the
true total vintage effect could be somewhat larger than total_vintage_effect
reports. Any report text quoting total_vintage_effect as a percentage of the
gap must say so.

This telescoping sum is a PURE ALGEBRAIC IDENTITY given the eight definitions
above (each stage total minus the previous stage total, by construction) --
build() asserts it to the cent as a sanity check on the arithmetic, not as
evidence about the household. NOTE ON SIGN: this residual convention (actual
minus modeled, continuing the same "next stage minus previous stage" pattern
every other term uses) is the one that makes the eight terms telescope to
actual_total_sum; a naive reading of "residual = own_vintage_total -
actual_total" for the running total would NOT telescope (it would double-count
bill_window_own_vintage_total). Each PER-PERIOD residual uses the same
actual-minus-modeled convention for exactly this reason, so per-period residuals
sum to residual_total exactly.

GENERATION RATE VINTAGE IS ZERO, BY EVIDENCE, NOT ASSUMPTION.
_verify_cca_generation_rate_flat() reads data/cca_generation_rates.csv (built
by analysis/cca_rate_extraction.py from every CCA-era bill PDF), filters to
authority == "charged_tariff" rows for the three real TOU cells (on_peak,
off_peak, super_off_peak) on exactly these 13 periods, and fails closed
(SystemExit, naming the offending cell) unless EVERY period is represented and
EVERY (season, TOU) cell's charged rate is both (a) identical across every
period that bills it and (b) equal to rates.CEA's current value to 5 decimal
places. On this corpus it passes cleanly: all 13 periods are covered, and all
six cells (summer/winter x on/off/sop) charge exactly rates.CEA's current
values with zero variation. This is why generation_tou_window_effect's
definition below EXCLUDES a "generation vintage" term entirely -- there is
direct bill evidence the rate never changed, so pricing generation at
rates.CEA for the whole window (as bmn.bill() already does) is not a
current-vintage ASSUMPTION here, it is what CEA actually, verifiably charged
throughout. Whatever gap remains between the real cca_generation dollars and
that rates.CEA-priced model is therefore NOT a rate effect; see below for what
it actually is. If a future regeneration of data/cca_generation_rates.csv
ever shows a rate change, this check fails closed and generation_tou_window_
effect's construction must be revisited (it would then need a real generation
rate-vintage term, which does not exist today because the evidence says there
is nothing to price).

generation_clean_tou_effect (= generation_tou_actual_sum - the continuous-
window, current-vintage-priced CEA generation total, computed by
_continuous_current_vintage_components()) is the properly, PURELY isolated
TOU-window effect on generation dollars: interval kWh gets bucketed into
on/off/super-off-peak using rates.period_at()'s CURRENT window shape
(billing_model_nem.load(), applied uniformly to every historical date -- the
SAME limitation _residual_concentration_note documents for delivery/PCIA/NBC),
so kWh that was actually billed in one TOU bucket under the window shape in
force at the time can be modeled in a DIFFERENT bucket here. Since CEA's
on/off/sop rates differ by roughly $0.11-0.47/kWh, even a modest amount of
reclassified kWh produces a real dollar gap -- quantified, not "not
determined," now that generation's rate vintage is proven zero.
generation_tou_actual_sum (= generation_actual_sum - cip_adder_usd -
state_surcharge_tax_usd) is the real billed generation dollars with the two
known, real, non-modeled line items subtracted out FIRST (a second Codex
review finding: an earlier version of this computation compared the FULL real
generation total, CIP and the surcharge tax included, against the TOU-only
model, silently folding two real-but-unmodeled line items into a figure
reported as a pure TOU-window-shape effect). What remains in generation_
clean_tou_effect after that subtraction is believed to be predominantly the
TOU-window-shape confound plus ordinary rounding; _verify_and_compute_
generation_side_fees() independently confirms, for every period, that the
three real TOU-cell dollars plus the CIP adder plus the surcharge tax
reconstruct the real cca_generation figure to the cent, so no OTHER
generation-side line item is hiding in this figure.

THE RESTART ARTIFACT (delivery_pcia_restart_artifact_usd). delivery_current_
vintage and pcia_current (used in bill_window_current_vintage_total) are
computed by _delivery_and_pcia_kwh(), called separately PER BILL PERIOD and
summed; bill_window_all_current_vintage_modeled_total is bmn.bill() called
ONCE on the whole bill-aligned window as a continuous frame. Bill periods do
not align with calendar-month boundaries, so a calendar month that is
net-positive OVERALL can be split at a bill-period boundary into two
per-period fragments where one fragment alone is net-negative;
_delivery_and_pcia_kwh, seeing only that fragment, zero-clamps it (as it must,
for delivery_vintage_effect to compare like with like -- see below), losing a
netting offset the SAME month would keep if priced continuously the way
bmn.bill() actually is. This is NOT bmn.bill()'s real invocation crediting a
bucket this script zero-clamps instead: verified directly, calling
_delivery_and_pcia_kwh on the bill-aligned window as ONE continuous frame
returns zero net-negative buckets, so bmn.bill() never reaches its credit()
branch for any of them -- every occurrence this script finds is an artifact of
its own per-bill-period restart. delivery_pcia_restart_artifact_usd =
(delivery_current_vintage_sum - the continuous-window delivery total) +
(pcia_current_sum - the continuous-window PCIA total), computed in build()
from _continuous_current_vintage_components()'s clean, whole-window
delivery/PCIA totals -- NOT from a per-negative-bucket UDC+CEA formula (an
earlier version of this diagnostic, negative_bucket_mechanics_gap_usd, used
exactly that formula and OVERSTATED this artifact by about $6.66, because
folding CEA into a "credit-rate" placeholder double-counts part of the
generation effect that generation_clean_tou_effect now cleanly owns; that
diagnostic is retired). This artifact folds into generation_tou_window_effect
(NOT fixed_charge_vintage_effect, which is a clean real-vs-modeled
substitution with no bill-period-vs-continuous scope sensitivity at all,
since BSC*days is linear in day-count and has no sign-dependent bucketing).
delivery_vintage_effect is unaffected: both its sides (delivery_own_vintage
and delivery_current_vintage) come from the SAME per-bill-period-restarted
calls, so any phantom negative fragment zero-clamps identically on both sides
and cancels out of their difference.

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
  * CEA/CCA generation RATE -- sourceable and PROVEN FLAT, by direct bill
    evidence, for every one of these 13 periods (see "GENERATION RATE VINTAGE
    IS ZERO, BY EVIDENCE" above) -- but the real generation DOLLAR total is
    still substituted directly (bill_periods_electric.csv's cca_generation),
    identically in both the current-vintage and own-vintage totals, so it
    cancels out of delivery_vintage_effect by construction. The gap between
    that real total and a current-vintage CEA model of it splits into THREE
    terms, none of them a vintage effect: generation_tou_window_effect (the
    TOU-window-shape confound plus the restart artifact), cip_adder_usd, and
    state_surcharge_tax_usd (the latter two: real, unmodeled line items with
    no rates.py counterpart at all).
  * PCIA, NBC -- genuinely not sourceable historically at all (no committed
    artifact carries the historical PCIA or non-bypassable-charge line); held
    at the current rates.py vintage in BOTH the current-vintage and own-vintage
    totals AND in bill_window_all_current_vintage_modeled_total, so they cancel
    out of every vintage term and cannot explain any of them, but they CAN be
    part of residual_total (see notes field for what this implies).
  * Base Services Charge / Monthly Service Fee -- the real billed
    fixed_charge_total is substituted directly (real, not modeled); the gap
    against a continuous-window BSC model of it IS fixed_charge_vintage_effect
    -- a genuine, settled vintage/regime change (issue #7), unlike generation.

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


def _check_slot_coverage(d_full, start, end):
    """Fail closed on 15-minute SLOT-level gaps within the bill-aligned window,
    not just calendar-date presence (a Codex review finding: _check_coverage()
    above only confirms each date appears at least once in the frame -- a
    badly truncated day, e.g. one interval reading instead of 96, would pass
    that check and silently understate every kWh sum downstream). Delegates to
    rates.validate_interval_coverage(), the SAME slot-completeness check
    _native_window_total() already applies to the native window -- this
    closes the exact inconsistency the review named: a stronger check this
    script already has and uses elsewhere wasn't applied to the bill-aligned
    window too. Called ONCE on the whole contiguous bill-aligned window
    (start..end, inclusive) rather than per period, since _load_periods()
    already proves the 13 periods tile it with no gap or overlap."""
    sub = d_full[(d_full.dt.dt.date >= start) & (d_full.dt.dt.date <= end)]
    rates.validate_interval_coverage(
        zip(sub.dt.dt.date, sub.dt.dt.hour + sub.dt.dt.minute / 60), start, end)


# ---------------------------------------------------------------------------
# Step 3c/3d/3e: delivery at both vintages + PCIA's current-vintage net kWh.
# ---------------------------------------------------------------------------
def _delivery_and_pcia_kwh(sub):
    """(delivery_current_vintage_usd, delivery_own_vintage_usd,
    pcia_positive_net_kwh, unpriced_net_kwh, unpriced_days) for one period's
    interval slice `sub`.

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
    delivery split entirely. A net-negative (month, season, TOU) bucket
    contributes ZERO to both accumulators (matching rates_history.py's own
    "in-period exports settle at true-up" convention, which delivery_own_
    vintage is built on -- both current- and own-vintage delivery must use the
    SAME negative-bucket convention for delivery_vintage_effect to compare
    like with like). The per-bill-period restart this creates, and how it is
    quantified and attributed, is handled at the aggregate level -- see
    _continuous_current_vintage_components() and the module docstring's "THE
    RESTART ARTIFACT" section, not here: an earlier version of this function
    tried to quantify it locally, per negative bucket, using a UDC+CEA
    "credit-rate" placeholder, and that placeholder OVERSTATED the artifact by
    conflating part of generation's own (now separately, cleanly handled)
    effect into it -- retired in favor of the aggregate-level computation."""
    delivery_current_vintage = 0.0
    delivery_own_vintage = 0.0
    pcia_positive_kwh = 0.0
    unpriced_kwh = 0.0
    unpriced_days = set()

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
            unpriced_kwh, unpriced_days)


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
     unpriced_kwh, unpriced_days) = _delivery_and_pcia_kwh(sub)
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


def _continuous_current_vintage_components(d_full, start, end):
    """{delivery, generation, pcia, nbc, fixed_charge}: billing_model_nem.
    bill()'s own per-bucket formula, decomposed into its five dollar
    components, computed in ONE continuous pass over the bill-aligned window
    (start..end inclusive) -- the SAME scope bmn.bill() itself uses, no
    per-bill-period restart at all.

    Delivery (UDC) and generation (CEA) are SIGN-INVARIANT in bmn.bill()'s own
    formula: energy(s,p) = UDC+CEA+PCIA and credit(s,p) = UDC+CEA both include
    UDC and CEA identically regardless of the bucket's net sign, so summing
    net*UDC[s][p] and net*CEA[s][p] unconditionally over every (month, season,
    TOU) bucket reproduces bmn.bill()'s own delivery and generation dollars
    exactly -- with NO sensitivity to how the window gets sliced into pieces
    (a per-bill-period sum of this same unconditional formula would give the
    IDENTICAL number, since summing net*rate over sub-periods of a bucket
    equals summing it over the whole bucket -- linearity, not a per-period
    restart artifact). PCIA is the one component bmn.bill() prices only on
    net>=0 buckets, matching its own documented mechanic -- so PCIA (like
    delivery in _delivery_and_pcia_kwh, for the same underlying reason) IS
    sensitive to how the window is sliced, which is exactly why delivery_pcia_
    restart_artifact_usd exists (module docstring, "THE RESTART ARTIFACT").
    NBC and the fixed charge (BSC*days) don't depend on TOU buckets at all.

    Summing these five components exactly reproduces bmn.bill()'s own total
    for the same frame -- verified by
    case_continuous_components_sum_to_bmn_bill_total in the test suite, not
    merely assumed."""
    sub = d_full[(d_full.dt.dt.date >= start) & (d_full.dt.dt.date <= end)].copy()
    delivery = generation = pcia = nbc = fixed_charge = 0.0
    for _ym, m in sub.groupby("ym"):
        fixed_charge += m.dt.dt.date.nunique() * rates.BSC
        nbc += float(m["Consumption"].sum()) * rates.NBC
        # rates.py's own short period labels, read off its own rate table
        # rather than re-declared here: the TOU-label AST guard in
        # test_scripts_runnable.py treats a private copy of the short trio as
        # a reimplemented window rule (same convention rates_history.py's own
        # _SHORT uses, and for the same reason).
        for s in ("S", "W"):
            for p in rates_history._SHORT:
                grp = m[(m.seas == s) & (m.p == p)]
                net = float(grp["Consumption"].sum() - grp["Generation"].sum())
                delivery += net * rates.UDC[s][p]
                generation += net * rates.CEA[s][p]
                pcia += max(net, 0.0) * rates.PCIA
    return dict(delivery=delivery, generation=generation, pcia=pcia, nbc=nbc,
               fixed_charge=fixed_charge)


CCA_RATES_CSV = DATA / "cca_generation_rates.csv"


def _verify_cca_generation_rate_flat(periods):
    """Confirm, ON THE DATA, that CEA's own charged per-TOU generation rate is
    flat across every one of these 13 periods AND identical to rates.CEA's
    current table -- the evidence generation_tou_window_effect's construction
    depends on (a fresh Codex adversarial review flagged the prior version's
    generation_and_fixed_charge_vintage_effect as an unsupported "vintage"
    claim; analysis/cca_rate_extraction.py's own docstring already argues
    CEA's rate never moved across the whole CCA era, but this function
    verifies that claim FRESH, against these specific 13 periods, rather than
    trusting the docstring's corpus-wide claim to cover them without checking).

    Reads data/cca_generation_rates.csv, filters to authority == "charged_
    tariff" rows for the three real TOU cells (on_peak, off_peak, super_off_
    peak) on exactly these 13 periods, and fails closed (SystemExit, naming
    the offending period or cell) unless:
      (a) every one of the 13 periods is represented, and
      (b) every (season, TOU) cell's charged rate is IDENTICAL across every
          period that bills it, and
      (c) that flat rate equals rates.CEA's current value to 5 decimal places.

    If a future regeneration of data/cca_generation_rates.csv ever shows CEA's
    rate moving, this fails closed rather than silently continuing to treat
    generation as having zero rate-vintage effect -- generation_tou_window_
    effect's construction would then need a real generation rate-vintage term,
    which this script does not compute today because the evidence says there
    is nothing to price."""
    if not CCA_RATES_CSV.exists():
        raise SystemExit(f"reprice_by_vintage: missing {CCA_RATES_CSV}")
    rows = list(csv.DictReader(open(CCA_RATES_CSV, newline="")))
    our_periods = {p["period"] for p in periods}
    tariff_rows = [r for r in rows
                  if r["authority"] == "charged_tariff"
                  and r["tou_period"] in ("on_peak", "off_peak", "super_off_peak")
                  and r["period"] in our_periods]
    found_periods = {r["period"] for r in tariff_rows}
    missing = sorted(our_periods - found_periods)
    if missing:
        raise SystemExit(
            f"reprice_by_vintage: {CCA_RATES_CSV} has no charged_tariff generation "
            f"rows for period(s) {missing} -- generation_tou_window_effect's "
            "construction assumes CEA's rate is sourceable and flat for every one "
            "of the 13 periods; cannot verify that here, refusing to assume it")
    _SEASON_SHORT = {"summer": "S", "winter": "W"}
    # Inverted from rates_history's own long-for-short mapping rather than a
    # fresh {"on_peak": "on", ...} literal here: the TOU-label AST guard in
    # test_scripts_runnable.py treats a private copy of the short trio as a
    # reimplemented window rule (same reason _continuous_current_vintage_
    # components() reads the trio off rates_history._SHORT instead).
    _TOU_SHORT = {long: short for short, long in rates_history._LONG_FOR_SHORT.items()}
    seen = {}
    for r in tariff_rows:
        key = (r["season"], r["tou_period"])
        seen.setdefault(key, set()).add(round(float(r["rate_usd_per_kwh"]), 5))
    problems = []
    for (season, tou), rates_seen in sorted(seen.items()):
        if len(rates_seen) != 1:
            problems.append(f"{season}/{tou}: not flat across the 13 periods: "
                            f"{sorted(rates_seen)}")
            continue
        observed = next(iter(rates_seen))
        current = rates.CEA[_SEASON_SHORT[season]][_TOU_SHORT[tou]]
        if abs(observed - current) > 1e-5:
            problems.append(f"{season}/{tou}: charged {observed:.5f} != current "
                            f"rates.CEA {current:.5f}")
    if problems:
        raise SystemExit(
            "reprice_by_vintage: CEA generation-rate flatness/equality check FAILED "
            "-- generation_tou_window_effect's construction assumes CEA's charged "
            "rate is flat and equal to today's rates.CEA table; it is not, for:\n  "
            + "\n  ".join(problems))


def _verify_and_compute_generation_side_fees(periods):
    """(cip_adder_usd, state_surcharge_tax_usd): two REAL, directly-billed
    generation-side dollar totals data/cca_generation_rates.csv carries that
    bmn.bill()'s current-vintage CEA-table model has NO line for at all
    (a Codex review finding: an earlier version of this script folded both
    into generation_tou_window_effect, silently mislabeling them as part of
    the TOU-window-shape confound when they are neither modeled, vintage, nor
    window-shape effects -- they are simply real money the model structurally
    never counts, in every year, regardless of vintage or window):
      * CIP -- CEA's flat "Clean Impact Plus" per-kWh product adder
        (tou_period == "clean_impact_plus", authority == "charged_tariff").
        Unlike core CEA generation, rates.py has NO current-vintage line for
        this at all, so there is nothing to compare it against for a
        "vintage" claim -- but its own rate IS verified flat across the 13
        periods (the same way core CEA generation is verified flat, not
        assumed), which is the evidence for calling its own vintage effect
        essentially zero.
      * the state surcharge tax (tou_period == "state_surcharge_tax",
        authority == "charged_fee") -- a flat PER-PERIOD DOLLAR fee, not a
        per-kWh rate at all (no kwh/rate_usd_per_kwh columns), so there is no
        rate to check for flatness; it is simply summed as printed.

    Both are also verified, per period, to reconstruct bill_periods_electric.
    csv's own cca_generation figure exactly (to the cent) together with the
    three real TOU-cell dollars -- confirming these five line items are the
    COMPLETE real generation charge, not an assumption carried over from
    cca_generation_rates.csv's own total_printed row (which already asserts
    this in its note field; this re-derives it independently). Fails closed,
    naming the offender, on any gap: a missing period, a non-flat CIP rate, or
    a period whose real cca_generation isn't fully accounted for by these five
    line items (which would mean some OTHER generation-side charge exists that
    this script does not know about)."""
    if not CCA_RATES_CSV.exists():
        raise SystemExit(f"reprice_by_vintage: missing {CCA_RATES_CSV}")
    rows = list(csv.DictReader(open(CCA_RATES_CSV, newline="")))
    our_periods = {p["period"] for p in periods}
    cca_by_period = {p["period"]: p["cca_generation"] for p in periods}

    cip_rows = [r for r in rows if r["period"] in our_periods
               and r["tou_period"] == "clean_impact_plus"
               and r["authority"] == "charged_tariff"]
    surcharge_rows = [r for r in rows if r["period"] in our_periods
                     and r["tou_period"] == "state_surcharge_tax"
                     and r["authority"] == "charged_fee"]
    tariff_rows = [r for r in rows if r["period"] in our_periods
                  and r["authority"] == "charged_tariff"
                  and r["tou_period"] in ("on_peak", "off_peak", "super_off_peak")]

    missing_cip = sorted(our_periods - {r["period"] for r in cip_rows})
    if missing_cip:
        raise SystemExit(
            f"reprice_by_vintage: {CCA_RATES_CSV} has no clean_impact_plus row for "
            f"period(s) {missing_cip} -- cip_adder_usd's construction assumes every "
            "one of the 13 periods carries this line item; cannot verify that here, "
            "refusing to assume it")
    missing_surcharge = sorted(our_periods - {r["period"] for r in surcharge_rows})
    if missing_surcharge:
        raise SystemExit(
            f"reprice_by_vintage: {CCA_RATES_CSV} has no state_surcharge_tax row for "
            f"period(s) {missing_surcharge} -- state_surcharge_tax_usd's construction "
            "assumes every one of the 13 periods carries this line item; cannot "
            "verify that here, refusing to assume it")

    cip_rates_seen = {round(float(r["rate_usd_per_kwh"]), 5) for r in cip_rows}
    if len(cip_rates_seen) != 1:
        raise SystemExit(
            "reprice_by_vintage: CEA's Clean Impact Plus product adder rate is NOT "
            f"flat across the 13 periods: {sorted(cip_rates_seen)} -- reporting "
            "cip_adder_usd as a real dollar total does not depend on this, but the "
            "notes' 'essentially zero vintage effect' claim does; refusing to make "
            "that claim without checking")

    cip_adder_usd = sum(float(r["usd"]) for r in cip_rows)
    state_surcharge_tax_usd = sum(float(r["usd"]) for r in surcharge_rows)

    tou_by_period = {}
    for r in tariff_rows:
        tou_by_period[r["period"]] = tou_by_period.get(r["period"], 0.0) + float(r["usd"])
    cip_by_period = {r["period"]: float(r["usd"]) for r in cip_rows}
    surcharge_by_period = {r["period"]: float(r["usd"]) for r in surcharge_rows}
    problems = []
    for period in sorted(our_periods):
        reconstructed = (tou_by_period.get(period, 0.0) + cip_by_period[period]
                        + surcharge_by_period[period])
        real = cca_by_period[period]
        if abs(reconstructed - real) > 0.005:
            problems.append(f"{period}: TOU+CIP+surcharge=${reconstructed:.2f} != "
                            f"real cca_generation=${real:.2f}")
    if problems:
        raise SystemExit(
            "reprice_by_vintage: data/cca_generation_rates.csv's line items do not "
            "reconstruct bill_periods_electric.csv's real cca_generation to the "
            "cent for:\n  " + "\n  ".join(problems) + "\n-- some generation-side "
            "charge is not accounted for by the TOU cells, the CIP adder, and the "
            "state surcharge tax; refusing to build cip_adder_usd/state_surcharge_"
            "tax_usd from an incomplete decomposition")

    return cip_adder_usd, state_surcharge_tax_usd


# ---------------------------------------------------------------------------
# Step 4: pure aggregation + the telescoping identity check -- kept free of
# usage.csv/rates_history so it is directly testable on fabricated per-period
# figures.
# ---------------------------------------------------------------------------
def _aggregate(per_period, native_window_total,
               bill_window_all_current_vintage_modeled_total,
               continuous_components, cip_adder_usd, state_surcharge_tax_usd):
    bill_window_current_vintage_total = sum(p["current_vintage_total_usd"] for p in per_period)
    bill_window_own_vintage_total = sum(p["own_vintage_total_usd"] for p in per_period)
    actual_total_sum = sum(p["actual_total_usd"] for p in per_period)
    residual_total = sum(p["residual_usd"] for p in per_period)

    delivery_current_sum = sum(p["delivery_current_vintage_usd"] for p in per_period)
    pcia_current_sum = sum(p["pcia_usd"] for p in per_period)
    nbc_current_sum = sum(p["nbc_usd"] for p in per_period)
    fixed_charge_actual_sum = sum(p["fixed_charge_actual_usd"] for p in per_period)
    generation_actual_sum = sum(p["generation_actual_usd"] for p in per_period)

    # window_effect: native window -> bill-aligned window, BOTH sides modeled
    # by bmn.bill()'s own unmodified methodology -- a clean, same-methodology
    # comparison (adversarial review pass 1, finding 1's fix).
    window_effect = bill_window_all_current_vintage_modeled_total - native_window_total

    # NBC must cancel exactly between the per-period sum and the continuous
    # total: NBC is linear in gross kWh with no bucketing/sign dependence at
    # all, so the two computations are the SAME sum sliced two different
    # ways. A nonzero difference means something upstream is broken (a period
    # boundary mismatch, a filtering bug), not a real effect -- fail closed
    # rather than silently absorb it into a vintage term.
    nbc_diff = nbc_current_sum - continuous_components["nbc"]
    if abs(nbc_diff) > 0.005:
        raise SystemExit(
            "reprice_by_vintage: NBC does not cancel between the per-period sum "
            f"(${nbc_current_sum:.2f}) and the continuous-window total "
            f"(${continuous_components['nbc']:.2f}) -- NBC has no bucketing or "
            "sign dependence, so these must be identical; the window boundaries "
            "or the per-period/continuous frames have drifted apart")

    # fixed_charge_vintage_effect: real billed fixed-charge dollars vs.
    # bmn.bill()'s continuous-window BSC*days model. Clean: BSC is linear in
    # day-count, no sign/bucket dependence, so this has no restart-scope
    # sensitivity at all (module docstring, "THE RESTART ARTIFACT").
    fixed_charge_vintage_effect = fixed_charge_actual_sum - continuous_components["fixed_charge"]

    # generation_tou_actual_sum: the real billed generation dollars with the
    # two known, real, non-modeled line items (CIP adder, state surcharge tax)
    # subtracted out FIRST -- what's left is purely the real TOU-cell dollars,
    # the only piece bmn.bill()'s CEA-table model has any counterpart for at
    # all (a Codex review finding: an earlier version compared the FULL real
    # generation total, CIP and surcharge included, against the TOU-only
    # model, silently folding two real-but-unmodeled line items into what was
    # billed as a pure "TOU-window-shape" effect).
    generation_tou_actual_sum = generation_actual_sum - cip_adder_usd - state_surcharge_tax_usd

    # generation_clean_tou_effect: real TOU-only generation dollars vs.
    # bmn.bill()'s continuous-window CEA-priced model -- NOW the properly,
    # PURELY isolated TOU-window-shape effect on generation (module
    # docstring, "GENERATION RATE VINTAGE IS ZERO, BY EVIDENCE"). Diagnostic,
    # reported for transparency; NOT itself one of the identity terms.
    generation_clean_tou_effect = generation_tou_actual_sum - continuous_components["generation"]

    # delivery_pcia_restart_artifact_usd: the per-bill-period-restart artifact
    # (module docstring, "THE RESTART ARTIFACT"), computed as the ACTUAL
    # difference between the per-period-summed and continuous-window totals
    # for delivery and PCIA -- not reconstructed from a per-negative-bucket
    # formula (the retired negative_bucket_mechanics_gap_usd did that, and
    # overstated this artifact by conflating part of generation's own effect
    # into it).
    delivery_restart_artifact = delivery_current_sum - continuous_components["delivery"]
    pcia_restart_artifact = pcia_current_sum - continuous_components["pcia"]
    delivery_pcia_restart_artifact = delivery_restart_artifact + pcia_restart_artifact

    # generation_tou_window_effect: the term used in the identity. Equal to
    # generation_clean_tou_effect PLUS the restart artifact, by construction
    # -- this is what makes generation_tou_window_effect + cip_adder_usd +
    # state_surcharge_tax_usd + fixed_charge_vintage_effect reconstruct
    # (current_vintage_total - all_modeled) EXACTLY (asserted below), which is
    # what the 8-term identity requires. It no longer contains CIP or the
    # surcharge tax at all -- both are now their own separate, fully-known,
    # non-vintage, non-window-shape terms.
    generation_tou_window_effect = generation_clean_tou_effect + delivery_pcia_restart_artifact

    # Internal cross-check: the four new/separated terms must decompose the
    # OLD combined quantity (current_vintage_total - all_modeled) exactly,
    # with nothing left over -- a more specific diagnostic than waiting for
    # the final 8-term identity check below to catch the same bug less
    # legibly.
    old_combined = bill_window_current_vintage_total - bill_window_all_current_vintage_modeled_total
    reconstructed = (generation_tou_window_effect + cip_adder_usd + state_surcharge_tax_usd
                     + fixed_charge_vintage_effect)
    if abs(reconstructed - old_combined) > 0.005:
        raise SystemExit(
            "reprice_by_vintage: generation_tou_window_effect + cip_adder_usd + "
            f"state_surcharge_tax_usd + fixed_charge_vintage_effect (${reconstructed:.2f}) "
            "does not reconstruct current_vintage_total - bill_window_all_current_"
            f"vintage_modeled_total (${old_combined:.2f}) -- the generation/fixed-"
            "charge decomposition is inconsistent with its own inputs")

    # delivery_vintage_effect: own-vintage UDC vs current-vintage UDC, real
    # generation/fixed-charge held fixed on both sides (cancels out).
    delivery_vintage_effect = bill_window_own_vintage_total - bill_window_current_vintage_total
    # generation excluded, per the flatness proof (module docstring); CIP and
    # the state surcharge tax excluded too -- neither has a rates.py
    # counterpart to compare against, so neither can support a vintage claim
    # at all (they are simply real, always-present money the model never
    # counts, not a "changed over time" story).
    total_vintage_effect = delivery_vintage_effect + fixed_charge_vintage_effect

    # residual_total, defined above as the sum of the (actual - own_vintage)
    # per-period residuals, is ALSO exactly actual_total_sum -
    # bill_window_own_vintage_total (same telescoping convention) -- assert
    # the two routes to it agree before trusting either. Unaffected by the
    # generation/fixed-charge restructuring above: it never depended on how
    # (current_vintage_total - all_modeled) gets further decomposed.
    residual_total_direct = actual_total_sum - bill_window_own_vintage_total
    if abs(residual_total - residual_total_direct) > 0.005:
        raise SystemExit(
            "reprice_by_vintage: residual_total (sum of per-period residuals, "
            f"${residual_total:.2f}) does not match actual_total_sum - "
            f"bill_window_own_vintage_total (${residual_total_direct:.2f}) -- "
            "per-period arithmetic is inconsistent with the aggregate")

    identity_lhs = (native_window_total + window_effect + generation_tou_window_effect
                    + cip_adder_usd + state_surcharge_tax_usd + fixed_charge_vintage_effect
                    + delivery_vintage_effect + residual_total)
    if abs(identity_lhs - actual_total_sum) > 0.005:
        raise SystemExit(
            "reprice_by_vintage: the telescoping identity native_window_total + "
            "window_effect + generation_tou_window_effect + cip_adder_usd + "
            "state_surcharge_tax_usd + fixed_charge_vintage_effect + "
            "delivery_vintage_effect + residual_total == actual_total_sum does not "
            f"hold: {identity_lhs:.2f} != {actual_total_sum:.2f} -- this is pure "
            "arithmetic and must be exact; an aggregation bug, not a data finding")

    return dict(
        native_window_total=native_window_total,
        bill_window_all_current_vintage_modeled_total=bill_window_all_current_vintage_modeled_total,
        window_effect=window_effect,
        generation_tou_window_effect=generation_tou_window_effect,
        generation_clean_tou_effect=generation_clean_tou_effect,
        delivery_pcia_restart_artifact_usd=delivery_pcia_restart_artifact,
        delivery_restart_artifact_usd=delivery_restart_artifact,
        pcia_restart_artifact_usd=pcia_restart_artifact,
        cip_adder_usd=cip_adder_usd,
        state_surcharge_tax_usd=state_surcharge_tax_usd,
        fixed_charge_vintage_effect=fixed_charge_vintage_effect,
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


def _build_notes(per_period, agg):
    kwh = _kwh_reconciliation_notes(per_period)
    total_unpriced_kwh = sum(p["unpriced_delivery_kwh"] for p in per_period)
    total_unpriced_days = sum(p["unpriced_delivery_days"] for p in per_period)
    residual_total = agg["residual_total"]
    generation_tou_window_effect = agg["generation_tou_window_effect"]
    generation_clean_tou_effect = agg["generation_clean_tou_effect"]
    delivery_pcia_restart_artifact = agg["delivery_pcia_restart_artifact_usd"]
    fixed_charge_vintage_effect = agg["fixed_charge_vintage_effect"]
    cip_adder_usd = agg["cip_adder_usd"]
    state_surcharge_tax_usd = agg["state_surcharge_tax_usd"]
    material = abs(residual_total) >= 5.0

    return dict(
        generation_rate_vintage_is_zero_by_evidence=(
            "_verify_cca_generation_rate_flat() confirmed, against data/"
            "cca_generation_rates.csv (built by analysis/cca_rate_extraction.py "
            "from every CCA-era bill PDF), that CEA's charged per-TOU generation "
            "rate is flat across all 13 periods in this corpus AND identical to "
            "rates.CEA's current table for every one of the six (season, TOU) "
            "cells, to 5 decimal places -- direct bill evidence, not an assumption "
            "carried over from that module's own docstring. There is therefore NO "
            "generation rate-vintage term in this decomposition: CEA charged "
            "exactly today's rate throughout the analysis year, so pricing "
            "generation at rates.CEA for the whole window is not a current-vintage "
            "modeling choice here, it is what was actually charged. If a future "
            "regeneration of data/cca_generation_rates.csv ever shows this rate "
            "moving, build() fails closed rather than silently continuing to "
            "assume zero generation-vintage effect."),
        generation_tou_window_effect_explanation=(
            f"generation_tou_window_effect (${generation_tou_window_effect:+.2f}) is "
            "NOT a vintage effect (see generation_rate_vintage_is_zero_by_evidence) "
            "-- it is a quantified measurement of the SAME TOU-window-shape "
            "confound this script's residual_concentration note already documents "
            "for delivery/PCIA/NBC, now determined for generation dollars "
            "specifically instead of folded into 'not determined.' "
            "billing_model_nem.load() assigns every historical 15-minute interval's "
            "TOU period via rates.period_at()'s CURRENT window shape, applied "
            "uniformly to every historical date; the real statements billed "
            "generation against kWh bucketed by whichever window shape was "
            "actually in force on each date. Since CEA's on/off/sop rates differ "
            "by roughly $0.11-0.47/kWh, reclassified kWh produces a real dollar "
            f"gap: generation_clean_tou_effect (${generation_clean_tou_effect:+.2f}) "
            "is real billed TOU-cell generation dollars ONLY -- the CIP adder and "
            "the state surcharge tax are subtracted out first (see cip_adder_usd_"
            "explanation and state_surcharge_tax_usd_explanation; a Codex review "
            "finding: an earlier version of this script compared the FULL real "
            "generation total, both adders included, against the TOU-only model, "
            "silently folding two real-but-unmodeled line items into a figure "
            "billed as a pure TOU-window-shape effect) -- minus the continuous-"
            "window, current-vintage-priced CEA model of them "
            "(_continuous_current_vintage_components()). "
            f"generation_tou_window_effect adds delivery_pcia_restart_artifact_usd "
            f"(${delivery_pcia_restart_artifact:+.2f}) on top of "
            "generation_clean_tou_effect -- see delivery_pcia_restart_artifact_usd's "
            "own note for what that is and why it lands here rather than in "
            "fixed_charge_vintage_effect. What remains in generation_clean_tou_"
            "effect after separating the two known adders is believed to be "
            "predominantly the TOU-window-shape confound plus ordinary rounding; "
            "data/cca_generation_rates.csv's own per-period reconciliation "
            "(TOU + CIP + surcharge == real cca_generation, to the cent, verified "
            "by _verify_and_compute_generation_side_fees()) confirms no OTHER "
            "generation-side line item is hiding in it."),
        cip_adder_usd_explanation=(
            f"${cip_adder_usd:.2f} is CEA's real, directly-billed 'Clean Impact "
            "Plus' per-kWh product adder (data/cca_generation_rates.csv's "
            "clean_impact_plus rows), separated out of generation_tou_window_"
            "effect because it is neither a vintage effect nor a TOU-window-shape "
            "effect: rates.py's CEA table has no line for it at all, so there is "
            "nothing to compare it against for either claim -- it is simply real "
            "money bmn.bill()'s current-vintage model structurally never counts, "
            "in every year, regardless of vintage or window. Its own rate "
            "(_verify_and_compute_generation_side_fees()) is verified flat at "
            "$0.001/kWh across all 13 periods, which is the evidence for calling "
            "its OWN vintage effect essentially zero -- unlike PCIA/NBC, this "
            "isn't assumed."),
        state_surcharge_tax_usd_explanation=(
            f"${state_surcharge_tax_usd:.2f} is a real, directly-billed per-period "
            "state surcharge tax (data/cca_generation_rates.csv's "
            "state_surcharge_tax rows, authority == charged_fee) -- a flat DOLLAR "
            "fee with no per-kWh rate at all (no kwh or rate_usd_per_kwh columns), "
            "so no flatness or vintage question even applies to it; it is simply "
            "summed as printed. Separated out of generation_tou_window_effect for "
            "the same reason as the CIP adder: rates.py has no counterpart line "
            "for it, so it cannot be a vintage or window-shape effect, only real, "
            "always-present money the model never counts."),
        delivery_pcia_restart_artifact_usd_explanation=(
            f"${delivery_pcia_restart_artifact:+.2f} "
            "(delivery ${:+.2f}, PCIA ${:+.2f}) is the per-bill-period-restart "
            "artifact (module docstring, 'THE RESTART ARTIFACT'): "
            "delivery_current_vintage and pcia_current are computed by "
            "_delivery_and_pcia_kwh(), called separately PER BILL PERIOD and "
            "summed; bill_window_all_current_vintage_modeled_total prices the "
            "same window CONTINUOUSLY (one bmn.bill() call). Bill periods don't "
            "align with calendar-month boundaries, so a calendar month that nets "
            "positive OVERALL can split at a bill-period boundary into a "
            "negative-looking fragment that this script's own zero-clamp drops -- "
            "verified directly: _delivery_and_pcia_kwh() on the bill-aligned "
            "window as ONE continuous frame finds ZERO net-negative buckets, so "
            "bmn.bill() never actually credits any of them; every occurrence this "
            "script finds is an artifact of its own per-bill-period restart, not a "
            "real NEM credit bmn.bill() applies differently. This artifact folds "
            "into generation_tou_window_effect (not fixed_charge_vintage_effect, "
            "which has no restart-scope sensitivity at all: BSC*days is linear in "
            "day-count). An earlier diagnostic (negative_bucket_mechanics_gap_usd, "
            "retired) tried to compute this per negative bucket using a UDC+CEA "
            "'credit-rate' placeholder and OVERSTATED it by about $6.66 by folding "
            "in part of generation's own effect; this figure is instead the direct, "
            "actual difference between the per-period-summed and continuous-window "
            "delivery/PCIA totals. delivery_vintage_effect is NOT affected: both "
            "its sides come from the SAME per-bill-period-restarted calls, so this "
            "artifact cancels out of their difference."
        ).format(agg["delivery_restart_artifact_usd"], agg["pcia_restart_artifact_usd"]),
        fixed_charge_vintage_note=(
            f"fixed_charge_vintage_effect (${fixed_charge_vintage_effect:+.2f}) is "
            "genuinely a vintage/regime effect, not disputed: the real billed "
            "fixed charge (Monthly Service Fee before 2025-10-01, Base Services "
            "Charge from then on -- both from bill_periods_electric.csv's "
            "fixed_charge_total) minus a continuous-window model of BSC*days at "
            "today's rate. SDG&E's fixed-charge structure genuinely changed "
            "(settled, issue #7, CPUC Resolution E-5355), so this is a real "
            "structural billing difference, cleanly isolated with no restart-scope "
            "sensitivity (BSC*days is linear in day-count)."),
        pcia_nbc_vintage_limitation=(
            "rates_history.py can source the UDC delivery tariff actually in force "
            "on any historical date in the bill corpus (bill_tou_detail.csv's "
            "printed 'Rate/kWh' lines), but it cannot source historical PCIA or "
            "non-bypassable-charge (NBC) rates -- no committed artifact carries "
            "either (rates_history.py's own module docstring; RateSet.pcia/.nbc "
            "both refuse for this reason). Unlike generation (see "
            "generation_rate_vintage_is_zero_by_evidence), there is no independent "
            "evidence PCIA or NBC stayed flat -- they are simply held at the "
            "current rates.py vintage in current_vintage_total, own_vintage_total "
            "AND bill_window_all_current_vintage_modeled_total alike, so they "
            "cancel out of every one of these totals (delivery_vintage_effect, "
            "generation_tou_window_effect, fixed_charge_vintage_effect, all three) "
            "and cannot explain any of them. If the actual historical PCIA/NBC "
            "rates differed from what is substituted here, that difference shows "
            "up in residual_total with no way for this script to separate it from "
            "a genuine model-vs-bill mechanics gap."
            + (" Given residual_total is not close to zero, this is one of three "
               "candidate contributors to it (see unpriced_delivery_limitation and "
               "residual_concentration for the other two)."
               if material else
               " residual_total is small enough here that this limitation is "
               "largely academic for this dataset, but it remains structurally "
               "unresolved.")),
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
               "candidate contributors to it (see pcia_nbc_vintage_limitation and "
               "residual_concentration for the other two)."
               if material else
               " residual_total is small enough here that this limitation is "
               "largely academic for this dataset, but it remains structurally "
               "unresolved.")),
        residual_concentration=_residual_concentration_note(per_period),
        kwh_reconciliation=kwh,
        out_of_scope=(
            "Sourcing historical PCIA/NBC rates by any means beyond what "
            "rates_history.py already provides is out of scope for this script "
            "(issue #27, 'independent oracle for historical rate vintages'). "
            "CEA generation rate vintage is NOT in this category any more -- it "
            "is determined, by direct evidence, to be zero (see "
            "generation_rate_vintage_is_zero_by_evidence)."),
    )


# ---------------------------------------------------------------------------
# build() / write / main
# ---------------------------------------------------------------------------
def build():
    periods = _load_periods()
    _verify_cca_generation_rate_flat(periods)
    d = bmn.load()

    available_dates = set(d.dt.dt.date)
    _check_coverage(available_dates, periods)
    _check_slot_coverage(d, periods[0]["start"], periods[-1]["end"])

    cip_adder_usd, state_surcharge_tax_usd = _verify_and_compute_generation_side_fees(periods)

    per_period = [_per_period_figures(d, row) for row in periods]
    native_window_total = _native_window_total(d)
    bill_window_all_current_vintage_modeled_total = _bill_window_all_current_vintage_modeled(
        d, periods[0]["start"], periods[-1]["end"])
    continuous_components = _continuous_current_vintage_components(
        d, periods[0]["start"], periods[-1]["end"])
    agg = _aggregate(per_period, native_window_total,
                     bill_window_all_current_vintage_modeled_total,
                     continuous_components, cip_adder_usd, state_surcharge_tax_usd)
    notes = _build_notes(per_period, agg)

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
                "analysis/cca_rate_extraction.py -> data/cca_generation_rates.csv "
                "(CEA's own charged per-TOU generation rate, verified flat and "
                "equal to current)",
                "private raw Green Button 15-min export (usage.csv)",
            ],
        ),
        native_window_total=agg["native_window_total"],
        bill_window_all_current_vintage_modeled_total=(
            agg["bill_window_all_current_vintage_modeled_total"]),
        window_effect=agg["window_effect"],
        generation_tou_window_effect=agg["generation_tou_window_effect"],
        generation_clean_tou_effect=agg["generation_clean_tou_effect"],
        delivery_pcia_restart_artifact_usd=agg["delivery_pcia_restart_artifact_usd"],
        delivery_restart_artifact_usd=agg["delivery_restart_artifact_usd"],
        pcia_restart_artifact_usd=agg["pcia_restart_artifact_usd"],
        cip_adder_usd=agg["cip_adder_usd"],
        state_surcharge_tax_usd=agg["state_surcharge_tax_usd"],
        fixed_charge_vintage_effect=agg["fixed_charge_vintage_effect"],
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
    print(f"  + generation TOU-window effect (NOT vintage; see notes): ${result['generation_tou_window_effect']:+.2f}")
    print(f"  + CIP adder (real, unmodeled, not vintage):            ${result['cip_adder_usd']:+.2f}")
    print(f"  + state surcharge tax (real, unmodeled, not vintage):  ${result['state_surcharge_tax_usd']:+.2f}")
    print(f"  + fixed-charge vintage effect (regime change):         ${result['fixed_charge_vintage_effect']:+.2f}")
    print(f"  + delivery vintage effect (own historical rate):       ${result['delivery_vintage_effect']:+.2f}")
    print(f"  + residual (not determined further):                   ${result['residual_total']:+.2f}")
    print(f"  = actual billed total:                                ${result['actual_total_sum']:.2f}")
    print(f"total vintage effect (delivery + fixed-charge, generation/CIP/surcharge excluded): "
          f"${result['total_vintage_effect']:+.2f}")
    print(f"identity check: {result['identity_check_usd']:.4f} vs "
          f"{result['actual_total_sum']:.4f} (holds={result['identity_holds']})")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
