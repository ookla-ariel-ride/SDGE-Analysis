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


CASES = [case_soiling_regression_recovers_the_injected_rate]


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
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
