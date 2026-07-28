#!/usr/bin/env python3
"""Tie the report's chart arrays to the committed artifacts.

index.html hard-codes its chart series in a `const D = {...}` block rather than
fetching them, which is right for a single self-contained file but means the
numbers can drift from the artifacts that justify them. They did: after the
2026-07-27 interval correction the periods chart carried corrected kWh beside
pre-correction dollars, and nothing failed.

These cases fail instead. They need no private data, only the two committed files.

Run from the repo root:  ./.venv/bin/python analysis/test_report_consistency.py
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = (ROOT / "index.html").read_text()
RD = json.loads((ROOT / "data" / "report_data.json").read_text())
DISPATCH = json.loads((ROOT / "data" / "battery_dispatch_policies.json").read_text())


def _array(name):
    """The numeric array assigned to `name` in the report's const D block."""
    m = re.search(re.escape(name) + r":\s*(\[[-\d.,\s]*\])", HTML)
    assert m, f"{name} not found in index.html"
    return [float(x) for x in m.group(1).strip("[]").split(",") if x.strip()]


def _close(a, b, tol, what):
    assert len(a) == len(b), f"{what}: length {len(a)} vs {len(b)}"
    bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if abs(x - y) > tol]
    assert not bad, f"{what}: {bad[:4]}"


def case_periods_chart_matches_its_artifact():
    pc = RD["periods_chart"]
    assert pc["order"] == ["sop", "off", "on"], (
        "periods_chart order changed; the positional indexing below and the "
        "chart labels both assume sop/off/on")
    kwh = _array("data")            # first `data:` array is the periods chart kWh
    _close(kwh, pc["import_kwh"], 1, "periods chart kWh")
    m = re.search(r"Annual import cost \$',data:(\[[\d,.\s]*\])", HTML)
    assert m, "periods chart cost series not found"
    cost = [float(x) for x in m.group(1).strip("[]").split(",")]
    _close(cost, pc["import_cost"], 1, "periods chart cost")
    return "the periods chart's kWh and dollar series both match report_data.json"


def case_monthly_series_match_their_artifact():
    mon = RD["monthly"]
    _close(_array("mImp"), [round(v) for v in mon["imp"]], 1, "mImp")
    _close(_array("mExp"), [round(v) for v in mon["exp"]], 1, "mExp")
    _close(_array("mCost"), mon["cost"], 1, "mCost")
    return "the monthly import, export and cost series match report_data.json"


def case_hourly_profiles_match_their_artifact():
    for seas in ("S", "W"):
        for kind in ("imp", "exp"):
            _close(_array(f"hourly{seas}_{kind}"), RD[f"hourly_{seas}"][kind],
                   0.001, f"hourly{seas}_{kind}")
    return "both seasonal hour-of-day profiles match report_data.json"


def case_battery_chart_series_match_their_artifacts():
    """The three §5 battery series were the one chart family with no pin, and the
    'today' series survived a re-base carrying pre-correction data as a result.
    bat_now_S is hourly_S.imp rounded to 2 dp; the PW3 series come from the
    dispatch artifact's greedy profiles."""
    _close(_array("bat_now_S"), [round(v, 2) for v in RD["hourly_S"]["imp"]],
           0.01, "bat_now_S")
    _close(_array("bat_pw3_S"), DISPATCH["pw3"]["greedy_profile_S"], 0.01, "bat_pw3_S")
    _close(_array("bat_pw3x_S"), DISPATCH["pw3x"]["greedy_profile_S"], 0.01, "bat_pw3x_S")
    return "all three battery chart series match their committed artifacts"


def case_headline_figures_present_and_stale_ones_absent():
    """One pinned figure per artifact class, plus absence of the retired value:
    presence-anywhere alone cannot catch a partial re-base (the §3 failure), but
    new-present AND old-absent catches every drift this branch actually had."""
    BR = json.loads((ROOT / "data" / "behavior_rebuild.json").read_text())
    PK = json.loads((ROOT / "data" / "package_results.json").read_text())
    checks = [
        (f"{BR['detection']['sessions']} charging sessions", "560 charging sessions"),
        (f"${BR['scenarios']['a']['saved']:,.0f}/yr", None),
        (f"${DISPATCH['pw3']['greedy']['save']:,}", "$2,325"),
        (f"${PK['packages']['MID']['savings_yr']:,}", "$3,438"),
        (f"${DISPATCH['baseline_bill_current_rates']:,}", "$4,884"),
    ]
    for new_form, old_form in checks:
        assert new_form in HTML, f"current figure missing from the report: {new_form!r}"
        if old_form:
            assert old_form not in HTML, f"stale figure survives in the report: {old_form!r}"
    return "headline figures per artifact class are present and their stale forms absent"


def case_chart_and_dispatch_agree_on_non_super_off_peak():
    """The §6 prose quotes the dispatch artifact; the §5 chart draws report_data.

    These are two independent roundings of one quantity, so they may differ by a
    kWh, but not more: a real divergence means the two pipelines have drifted onto
    different period assignments again.
    """
    pc = RD["periods_chart"]
    chart = pc["import_kwh"][1] + pc["import_kwh"][2]      # off-peak + on-peak
    quoted = DISPATCH["inputs"]["nonsop_import_kwh"]
    assert abs(chart - quoted) <= 1, f"chart {chart} vs dispatch {quoted}"
    assert abs(pc["import_kwh"][2]
               - DISPATCH["inputs"]["onpeak_import_kwh"]) <= 1
    return "the periods chart and the dispatch inputs agree on non-super-off-peak kWh"


def case_no_retired_holiday_discrepancy_note():
    """The report used to explain a ~40 kWh gap between two period assignments.

    Both pipelines now use the canonical rule, so any surviving copy of that
    explanation is false.
    """
    for phrase in ("8,467", "treats seven weekday holidays as weekends",
                   "holiday convention"):
        assert phrase not in HTML, f"retired explanation still present: {phrase!r}"
    return "the retired holiday-convention explanation is gone from the report"


def case_report_totals_match_the_artifact():
    t = RD["totals"]
    assert f"{t['imp']:,}" in HTML, f"totals.imp {t['imp']:,} not cited in the report"
    return "the report's annual import total matches report_data.json"


CASES = [
    case_periods_chart_matches_its_artifact,
    case_monthly_series_match_their_artifact,
    case_hourly_profiles_match_their_artifact,
    case_battery_chart_series_match_their_artifacts,
    case_headline_figures_present_and_stale_ones_absent,
    case_chart_and_dispatch_agree_on_non_super_off_peak,
    case_no_retired_holiday_discrepancy_note,
    case_report_totals_match_the_artifact,
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
