#!/usr/bin/env python3
"""Guard suite for soiling_analysis.py -- run main() END TO END on a synthetic
production record.

soiling_analysis.py sits in NEEDS_PRIVATE_ARCHIVE (test_scripts_runnable.py):
CI has no pvoutput_daily.csv/enphase_daily_production.csv, so before this file
existed the whole regression pipeline (clear-day flagging, the seasonal-
harmonic + days-since-rain OLS, the annual-loss model) ran only on the
machine holding the private archive (issue #44).

The rain calendar (PRECIP_IN/PRECIP_MM) is hardcoded PUBLIC weather-station
data already committed in soiling_analysis.py's own source -- not private, and
not something this fixture can override. That is used as an ADVANTAGE: with
days_since_rain() fully known in advance (it depends only on that public
table), a synthetic production series can be constructed as an EXACT
log-linear function of it --

    prod(d) = SCALE * clearsky_ghi_kwh_m2(d) * exp(-RATE * days_since_rain(d))

so that perf(d) = prod(d)/clearsky(d) = SCALE * exp(-RATE * dsr(d)) exactly,
and ln(perf) is an EXACT linear function of dsr with zero residual and no
seasonal term. The seasonal-harmonic + days-since-rain regression in
run_regression() should therefore recover a dsr coefficient of very nearly
-RATE (harmonic terms fit to ~0), regardless of which subset of days the
clear-day filter keeps -- OLS on points that lie exactly on one line recovers
that line from any non-degenerate subset. clearsky_ghi_kwh_m2 and
days_since_rain are called directly from the real module (imported without
running main(), which only executes under __main__), so this fixture is
built from the SAME ground truth the generator itself will use -- no
independent transcription of the Haurwitz formula is needed to construct it.

The pvoutput and enphase series are made IDENTICAL, so production_crosscheck
is hand-verified exactly (zero mean_abs_diff).

SkipCase matches test_parse_bills.py's typed-exception convention (issue #44
AC4); there is no skip path in this file since the fixture is fully synthetic.
"""
import json
import math
import pathlib
import shutil
import subprocess
import sys

import suite_runner
import tempfile
from datetime import date, timedelta

ANALYSIS = pathlib.Path(__file__).resolve().parent


class SkipCase(Exception):
    pass


START, END = date(2025, 7, 24), date(2026, 7, 23)   # hardcoded in main()
LAT = 33.0
SCALE, RATE = 3.0, 0.003   # RATE is the injected TRUE ln-per-day soiling rate

SYNTH_HOUSEHOLD = "location:\n  lat: 33.0\ncleaning_history: []\n"


_PROBE = """
import json, sys
sys.path.insert(0, ".")
import soiling_analysis as S
from datetime import date, timedelta
start, end = date(2025, 7, 24), date(2026, 7, 23)
out = {"lat": S.LAT}
days = {}
d = start
while d <= end:
    dsr = S.days_since_rain(d)
    days[d.isoformat()] = [S.clearsky_ghi_kwh_m2(d), dsr]
    d += timedelta(days=1)
out["days"] = days
print(json.dumps(out))
"""


def _probe_ground_truth(tmp):
    """Query clearsky_ghi_kwh_m2()/days_since_rain() from the REAL module, as
    a subprocess run with cwd=tmp (so household.py's repo-root walk resolves
    the SYNTHETIC private/household.yaml this fixture wrote, never whatever
    real household.yaml happens to be staged in the actual checkout).
    Module-level code only -- main() never runs, since it is gated behind
    __main__ in the real script."""
    r = subprocess.run([sys.executable, "-c", _PROBE], cwd=tmp,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"ground-truth probe failed: {r.stderr[-2000:]}"
    doc = json.loads(r.stdout)
    assert doc["lat"] == LAT, doc["lat"]   # confirms the SYNTHETIC household was read
    return doc["days"]


def case_soiling_regression_recovers_the_injected_rate():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "analysis").mkdir()
        (tmp / "data").mkdir()
        (tmp / "private").mkdir()
        for name in ("soiling_analysis.py", "household.py"):
            shutil.copy(ANALYSIS / name, tmp / name)
            shutil.copy(ANALYSIS / name, tmp / "analysis" / name)
        (tmp / "private" / "household.yaml").write_text(SYNTH_HOUSEHOLD)

        ground_truth = _probe_ground_truth(tmp)

        rows_pv, rows_en = [], []
        d = START
        while d <= END:
            cs, dsr = ground_truth[d.isoformat()]
            assert dsr is not None, f"{d}: no rain in the hardcoded record within lookback"
            prod = SCALE * cs * math.exp(-RATE * dsr)
            rows_pv.append(f"{d.isoformat()},{prod:.6f}")
            rows_en.append(f"{d.month}/{d.day}/{d.year},{prod:.6f}")
            d += timedelta(days=1)
        (tmp / "pvoutput_daily.csv").write_text(
            "date,generated_kwh\n" + "\n".join(rows_pv) + "\n")
        (tmp / "enphase_daily_production.csv").write_text(
            "Date/Time,Energy Delivered (kWh)\n" + "\n".join(rows_en) + "\n")

        r = subprocess.run([sys.executable, "soiling_analysis.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f"soiling_analysis.py failed: {r.stderr[-2000:]}"
        out = json.loads((tmp / "soiling_results.json").read_text())

    # ---- production cross-check: identical series by construction ---------
    xc = out["production_crosscheck"]
    assert xc["n_common_days"] == (END - START).days + 1, xc
    assert xc["mean_abs_diff_kwh"] == 0.0, xc
    assert abs(xc["pvoutput_mean_kwh"] - xc["enphase_mean_kwh"]) < 1e-6, xc

    # ---- regression: recovers the injected ln-per-day rate almost exactly -
    exp_rate_pct_day = (1 - math.exp(-RATE)) * 100
    exp_rate_pct_month = (1 - math.exp(-RATE * 30.44)) * 100
    for name in ("pvoutput", "enphase"):
        reg = out[name]["regression"]
        # tight relative tolerance: exact log-linear construction with zero
        # residual should recover the coefficient to within numerical noise
        # from the clear-day subsetting; still far tighter than a 10%-style
        # defect (10% of exp_rate_pct_day here is ~0.03).
        assert abs(reg["rate_pct_per_day"] - exp_rate_pct_day) < 0.01, (name, reg, exp_rate_pct_day)
        assert abs(reg["rate_pct_per_month"] - exp_rate_pct_month) < 0.3, (name, reg, exp_rate_pct_month)
        assert reg["p"] < 0.01, (name, reg)   # an exact linear relationship must be significant
        assert reg["n"] > 100, (name, reg)    # the clear-day filter kept a real sample

    assert json.dumps(out), "soiling_results.json is not JSON-serializable"
    return ("soiling_analysis.py runs end to end on a synthetic production "
            "record built from an exact log-linear soiling law over the "
            "generator's OWN clearsky/days-since-rain ground truth, and its "
            "regression recovers the injected rate to within 0.01 pct/day")


# ---------------------------------------------------------------------------
# THE CLEANING'S DIFFERENCE-IN-DIFFERENCES GAIN (ISSUE #164).
#
# The report's headline cleaning figure used to be `cleaning_gain_known = 11.8
# # verified prior work` -- a literal no committed script derived, published
# under a `measured` label, and carrying the load-bearing conclusion in §9
# that a single validated soiling swing is larger than the entire naive
# four-year change. cleaning_diff_in_diff() now computes it from
# data/cleaning_study_daily.csv, and these two cases hold it from both sides:
# a SYNTHETIC record with a gain injected into it (the derivation is correct)
# and the COMMITTED record against the COMMITTED artifact (the published
# figure is that derivation's output, not a constant restated beside it).
#
# Neither needs the private archive: the study CSV and soiling_results.json
# are committed de-identified public data, and the household the probe reads
# is synthetic.
# ---------------------------------------------------------------------------
_DID_PROBE = """
import json, sys
sys.path.insert(0, ".")
import soiling_analysis as S
from datetime import date
did, why = S.cleaning_diff_in_diff(date.fromisoformat(sys.argv[1]),
                                   S.load_cleaning_study())
print(json.dumps({"did": did, "why": why}))
"""


def _did_probe(tmp, clean_date):
    """cleaning_diff_in_diff() against tmp/data/cleaning_study_daily.csv, run
    as a subprocess with cwd=tmp so household.py's repo-root walk resolves the
    SYNTHETIC private/household.yaml rather than whatever is staged here."""
    r = subprocess.run([sys.executable, "-c", _DID_PROBE, clean_date], cwd=tmp,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"diff-in-diff probe failed: {r.stderr[-2000:]}"
    return json.loads(r.stdout)


def _did_sandbox(td):
    """A tmp tree the probe can run in: analysis/ + data/ + a synthetic
    private/household.yaml, with soiling_analysis.py and household.py in it."""
    tmp = pathlib.Path(td)
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    (tmp / "private").mkdir()
    for name in ("soiling_analysis.py", "household.py"):
        shutil.copy(ANALYSIS / name, tmp / name)
        shutil.copy(ANALYSIS / name, tmp / "analysis" / name)
    (tmp / "private" / "household.yaml").write_text(SYNTH_HOUSEHOLD)
    return tmp


def case_the_cleaning_gain_is_the_diff_in_diff_of_an_injected_record():
    """A record with a KNOWN gain built into it, recovered exactly.

    Every day inside a window carries the same value, so each median is that
    value and every ratio is exact: three control years decline by the same
    seasonal factor, the treated year declines by that factor and then gains
    GAIN on top. The answer is arithmetic, not a tolerance -- if the estimator
    were the naive post/pre ratio, or averaged the wrong way, or counted the
    treated year among its own controls, it could not land on it."""
    seasonal, gain = 0.90, 0.125          # control post/pre, injected treated gain
    clean = date(2024, 8, 12)
    with tempfile.TemporaryDirectory() as td:
        tmp = _did_sandbox(td)
        rows = []
        for year in (2021, 2022, 2023, 2024):
            anchor = clean.replace(year=year)
            pre = 60.0 if year != 2024 else 50.0     # treated year's own level
            post = pre * seasonal * ((1 + gain) if year == 2024 else 1.0)
            for k in range(1, 31):
                rows.append(((anchor - timedelta(days=k)), pre))
                rows.append(((anchor + timedelta(days=k)), post))
            # The wash day itself: zero production, present in the record, and
            # excluded from both windows. If the estimator ever stopped
            # excluding it, this row would drag the treated medians and the
            # exact answer below would fail.
            rows.append((anchor, 0.0))
        (tmp / "data" / "cleaning_study_daily.csv").write_text(
            "date,generated_kwh\n" + "\n".join(
                f"{d.strftime('%Y%m%d')},{v:.6f}" for d, v in sorted(rows)) + "\n")
        out = _did_probe(tmp, clean.isoformat())

    assert out["why"] is None, out["why"]
    did = out["did"]
    assert did["control_years"] == [2021, 2022, 2023], did["control_years"]
    assert did["treated_year"] == 2024, did
    assert did["window_days"] == 30, did
    assert abs(did["gain_pct_unrounded"] - gain * 100) < 0.01, (
        f"an injected {gain * 100}% gain came back as {did['gain_pct_unrounded']}%")
    assert abs(did["control_mean_post_over_pre"] - seasonal) < 1e-9, did
    for year, w in did["year_windows"].items():
        assert (w["n_pre_days"], w["n_post_days"]) == (30, 30), (year, w)

    # Fails closed rather than inventing a counterfactual: with only the
    # treated year in the record there is no seasonal decline to measure
    # against, and no figure is produced at all.
    with tempfile.TemporaryDirectory() as td:
        tmp = _did_sandbox(td)
        (tmp / "data" / "cleaning_study_daily.csv").write_text(
            "date,generated_kwh\n" + "\n".join(
                f"{d.strftime('%Y%m%d')},{v:.6f}"
                for d, v in sorted(rows) if d.year == 2024) + "\n")
        alone = _did_probe(tmp, clean.isoformat())
    assert alone["did"] is None and "no year other than 2024" in alone["why"], alone

    # ... and with no record at all, rather than raising out of the generator.
    with tempfile.TemporaryDirectory() as td:
        none_at_all = _did_probe(_did_sandbox(td), clean.isoformat())
    assert none_at_all["did"] is None and "missing or empty" in none_at_all["why"], \
        none_at_all
    return (f"an injected {gain * 100:.1f}% cleaning gain over a {seasonal} seasonal "
            f"decline is recovered as {did['gain_pct_unrounded']}% from three control "
            "years; a record with no control year, and no record at all, each produce "
            "no figure and a reason instead")


def case_the_published_cleaning_gain_is_that_derivation_on_the_committed_record():
    """ISSUE #164's own acceptance criterion, from the other side: the figure
    in data/soiling_results.json IS cleaning_diff_in_diff()'s output on
    data/cleaning_study_daily.csv, recomputed here rather than restated.

    A pin asserting `known_gain == 11.8` cannot tell a derived figure from a
    constant; this one moves the moment either the record or the estimator
    does, which is what "a script per headline number" asks for."""
    root = ANALYSIS.parent
    committed = json.loads((root / "data" / "soiling_results.json").read_text())
    sc = committed["sanity_check_2024_cleaning"]
    if "known_cleaning_gain_pct" not in sc:
        raise SkipCase("the committed artifact carries the not-determined shape, so "
                       "there is no published gain to pin")
    with tempfile.TemporaryDirectory() as td:
        tmp = _did_sandbox(td)
        shutil.copy(root / "data" / "cleaning_study_daily.csv", tmp / "data")
        out = _did_probe(tmp, sc["cleaning_date"])
    assert out["why"] is None, out["why"]
    did = out["did"]
    assert did == sc["known_cleaning_gain_basis"], (
        "data/soiling_results.json's known_cleaning_gain_basis is not what "
        "cleaning_diff_in_diff() computes from data/cleaning_study_daily.csv today")
    assert sc["known_cleaning_gain_pct"] == round(did["gain_pct_unrounded"], 1), (
        f"the published gain {sc['known_cleaning_gain_pct']}% is not the derivation's "
        f"{did['gain_pct_unrounded']}% rounded")
    # The two figures the report derives FROM the published gain, re-derived.
    implied = sc["known_cleaning_gain_pct"] / (100 + sc["known_cleaning_gain_pct"]) * 100
    assert sc["known_implied_soiling_loss_pct"] == round(implied, 1), sc
    assert sc["known_rate_equiv_pct_per_month"] == round(
        implied / sc["dry_days_before_cleaning_ge5mm"] * 30.44, 2), sc
    return (f"the published {sc['known_cleaning_gain_pct']}% gain is "
            f"cleaning_diff_in_diff()'s {did['gain_pct_unrounded']}% on "
            f"data/cleaning_study_daily.csv (treated {did['treated_year']}, controls "
            f"{did['control_years']}, {did['window_days']}-day windows), and the "
            f"{sc['known_implied_soiling_loss_pct']}% implied soiling loss and "
            f"{sc['known_rate_equiv_pct_per_month']}%/month rate follow from it")


CASES = [case_soiling_regression_recovers_the_injected_rate,
         case_the_cleaning_gain_is_the_diff_in_diff_of_an_injected_record,
         case_the_published_cleaning_gain_is_that_derivation_on_the_committed_record]


def main():
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            print(f"PASS  {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped += 1
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(case, e)
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
