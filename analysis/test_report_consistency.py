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
import calendar
import datetime as dt
import html as htmlmod
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

HTML = (ROOT / "index.html").read_text()
RD = json.loads((ROOT / "data" / "report_data.json").read_text())
BEHAVIOR = json.loads((ROOT / "data" / "behavior_rebuild.json").read_text())
DISPATCH = json.loads((ROOT / "data" / "battery_dispatch_policies.json").read_text())

_MONTH_ABBR = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
               7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}


def _expected_month_labels():
    """Independently recomputes generate_report.py's own
    _month_labels_with_partial_marks() from the same two committed artifacts,
    without calling that function -- so a bug in its partial-month logic fails
    this check too, not just a stale index.html copy (Codex review, issue #36)."""
    bw = BEHAVIOR["window"]
    start = dt.date.fromisoformat(bw["start"].split(" ")[0])
    end = dt.date.fromisoformat(bw["end"].split(" ")[0])
    labels = []
    for lab in RD["monthly"]["labels"]:
        year, month = (int(x) for x in lab.split("-"))
        last_day = calendar.monthrange(year, month)[1]
        month_start, month_end = dt.date(year, month, 1), dt.date(year, month, last_day)
        partial = month_start < start or month_end > end
        labels.append(f"{_MONTH_ABBR[month]}{str(year)[2:]}" + ("*" if partial else ""))
    return labels

# ---------------------------------------------------------------------------
# FORK NOTE: the two lists below pin RETIRED figures from THIS dataset's
# revision history -- values that once appeared in index.html, were superseded
# when an artifact was corrected, and must never resurface in a re-base. They
# mean nothing for any other house and would false-positive on a fork's
# legitimate numbers: when reproducing this analysis for your own data, delete
# the list ENTRIES (keep the empty lists -- the presence checks that pair with
# them still tie your report to your artifacts).
# ---------------------------------------------------------------------------

# Figures retired by artifact corrections; checked absent in
# case_headline_figures_present_and_stale_ones_absent alongside the presence
# of each figure's current artifact-derived form.
RETIRED_FIGURES = [
    "560 charging sessions",   # pre-correction EV session count
    "$2,325",                  # pre-correction PW3 price-aware annual save
    "$3,438",                  # pre-correction MID package savings
    "$4,884",                  # pre-correction baseline bill at current rates
    "9.4-yr median",           # pre-correction Monte Carlo payback (now 6.0)
    "median 9.4 yr",           # same retired figure, its other prose form
]

# The retired holiday-convention explanation; checked absent in
# case_no_retired_holiday_discrepancy_note. Both pipelines now share the
# canonical day-type rule, so any surviving copy of this note is false.
RETIRED_HOLIDAY_PHRASES = [
    "8,467",
    "treats seven weekday holidays as weekends",
    "holiday convention",
]


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
    # TEMPLATE-ORDER DEPENDENCY: `data` matches the FIRST `data:` array in
    # index.html, which is the periods chart's kWh series only because that
    # chart is the first Chart.js config in report-template.html. Adding a
    # chart above it would silently retarget this match; keep the periods
    # chart first, or anchor this lookup to its config, if the template changes.
    kwh = _array("data")
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
    m = re.search(r'mLabels:(\[[^\]]*\])', HTML)
    assert m, "mLabels not found in index.html"
    drawn_labels = json.loads(m.group(1))
    assert drawn_labels == _expected_month_labels(), (
        "mLabels disagrees with an independent recomputation from "
        "report_data.json's monthly.labels and behavior_rebuild.json's window "
        "(not generate_report.py's own _month_labels_with_partial_marks -- a "
        "bug there must fail this check too, not just a stale html copy)")
    return "the monthly labels, import, export and cost series match report_data.json"


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


def case_carb_chart_matches_its_artifact():
    """issue #36: carb was the one const D array with no pin -- every other
    array in that block is covered by a sibling case in this file. Both
    sides are already rounded to 1dp in their own committed form, so the
    tolerance only needs to absorb float-repr noise, not a real rounding
    gap -- matching hourly_profiles' own 0.001, not a looser value that
    could paper over a genuine last-digit drift."""
    carbon = json.loads((ROOT / "data" / "carbon_fullyear_results.json").read_text())
    _close(_array("carb"), carbon["intensity_kg_per_mwh"]["annual_avg_by_hour"],
          0.001, "carb")
    return "the §13 carbon chart's 24 hourly CO2-intensity values match carbon_fullyear_results.json"


def _sparse(name):
    """A `name:[...]` array from const D that may carry nulls (JSON-compatible)."""
    m = re.search(re.escape(name) + r":\s*(\[[-\d.,\s a-z\"]*\])", HTML)
    assert m, f"{name} not found in index.html"
    return json.loads(m.group(1))


def case_spread_chart_series_match_their_artifact():
    """The §13 spread chart is drawn from tou_spread.json, and its two series are
    interleaved on a merged date axis -- the shape most likely to drift silently if
    the artifact is regenerated and only one season's array is re-pasted."""
    sp = json.loads((ROOT / "data" / "tou_spread.json").read_text())["delivery_spread"]
    labels = _sparse("spLabels")
    for season, key in (("summer", "spSummer"), ("winter", "spWinter")):
        series = dict(sp[season]["series"])
        drawn = _sparse(key)
        assert len(drawn) == len(labels), f"{key}: {len(drawn)} values vs {len(labels)} labels"
        # Every label is a date in one season or the other; a season's array must
        # carry its own value there and null everywhere else.
        expected = [series.get("20" + lab) for lab in labels]
        bad = [(labels[i], d, e) for i, (d, e) in enumerate(zip(drawn, expected))
               if (d is None) != (e is None) or (d is not None and abs(d - e) > 1e-9)]
        assert not bad, f"{key} disagrees with tou_spread.json at {bad[:4]}"
        assert sum(v is not None for v in drawn) == sp[season]["n"], (
            f"{key}: plotted points != artifact n ({sp[season]['n']})")
    # The dashed pre-break span is a claim about which points the fit excludes.
    m = re.search(r"spBreak:\{summer:(\d+),winter:(\d+)\}", HTML)
    assert m, "spBreak indices not found in index.html"
    for season, idx in (("summer", int(m.group(1))), ("winter", int(m.group(2)))):
        want = sp[season]["post_break"]["break_date"]
        assert "20" + labels[idx] == want, (
            f"spBreak.{season} points at {labels[idx]}, artifact break is {want}")
    return "the §13 spread series and break indices match tou_spread.json"


def case_every_lazy_chart_id_resolves_to_a_unique_canvas():
    """getElementById returns the FIRST match, so a canvas sharing its id with a
    heading hands Chart.js an <h3> and the chart silently never renders -- while
    the registry still marks it built. Guards both halves: ids are unique, and
    every lazyChart target is a canvas.
    """
    for name in ("index.html", "report-template.html"):
        html = (ROOT / name).read_text()
        body = re.sub(r"<!--.*?-->", "", html, flags=re.S)   # section-map comments
        # ...and JS line comments, so the template's "how to add a chart"
        # instruction is not read as a real registration. `(?<!:)` keeps URLs.
        body = re.sub(r"(?<!:)//[^\n]*", "", body)
        ids = re.findall(r'\bid="([^"]+)"', body)
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        assert not dupes, f"{name}: duplicate element ids {dupes}"
        for cid in re.findall(r"lazyChart\('([^']+)'", body):
            assert re.search(r'<canvas id="' + re.escape(cid) + r'"', body), (
                f"{name}: lazyChart('{cid}') has no matching <canvas id=\"{cid}\">")
    return "every lazyChart id is unique and resolves to a canvas in both files"


def case_headline_figures_present_and_stale_ones_absent():
    """One pinned figure per artifact class, plus absence of the retired value:
    presence-anywhere alone cannot catch a partial re-base (the §3 failure), but
    new-present AND old-absent catches every drift this branch actually had."""
    BR = json.loads((ROOT / "data" / "behavior_rebuild.json").read_text())
    PK = json.loads((ROOT / "data" / "package_results.json").read_text())
    DP = json.loads((ROOT / "data" / "deep_results.json").read_text())
    current = [
        f"{BR['detection']['sessions']} charging sessions",
        f"${BR['scenarios']['a']['saved']:,.0f}/yr",
        f"${DISPATCH['pw3']['greedy']['save']:,}",
        f"${PK['packages']['MID']['savings_yr']:,}",
        f"${DISPATCH['baseline_bill_current_rates']:,}",
        f"median payback {DP['monte_carlo']['payback_median']:.1f} yr",
    ]
    for new_form in current:
        assert new_form in HTML, f"current figure missing from the report: {new_form!r}"
    for old_form in RETIRED_FIGURES:
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
    for phrase in RETIRED_HOLIDAY_PHRASES:
        assert phrase not in HTML, f"retired explanation still present: {phrase!r}"
    return "the retired holiday-convention explanation is gone from the report"


def case_report_totals_match_the_artifact():
    t = RD["totals"]
    assert f"{t['imp']:,}" in HTML, f"totals.imp {t['imp']:,} not cited in the report"
    return "the report's annual import total matches report_data.json"


def case_cca_cheaper_period_count_matches_the_artifact():
    """A Codex review pass caught the report claiming the 2026-07-02 statement was
    "the one period in the sample" where CEA read cheaper than bundled -- directly
    falsifiable: the committed artifact has 7 such periods, not 1, and one of the
    other 6 (-$14.31) is MORE extreme than the -$9.87 the report was built around.
    This locks the corrected count against the live artifact so a future
    regeneration can't silently drift back to an unverified claim."""
    ccj_path = ROOT / "data" / "cca_bundled_counterfactual.json"
    assert ccj_path.exists(), f"{ccj_path} is committed public data and must exist"
    ccj = json.loads(ccj_path.read_text())
    from collections import defaultdict
    by_period = defaultdict(float)
    for r in ccj["direction_a_cca_repriced_at_bundled"]["priced_detail"]:
        by_period[(r["statement_date"], r["period"])] += r["provider_delta_usd"]
    n_cheaper = sum(1 for v in by_period.values() if v < 0)
    assert f"{n_cheaper} of the 19 priced periods" in HTML or f"{n_cheaper} of 19" in HTML, (
        f"the artifact shows {n_cheaper} CEA-cheaper periods, but the report's own "
        "count phrase doesn't match it")
    assert "the one period in the sample" not in HTML, (
        "the retracted false-uniqueness claim has resurfaced in the report")
    return f"the report's CEA-cheaper period count ({n_cheaper}) matches the live artifact"


def case_cca_verdict_annualized_figure_matches_the_artifact():
    """A third Codex review pass caught the section-10 .verdict teaser stating
    "about $49/yr" after a later fix moved the artifact's own annualized delta to
    $50.10/yr -- the exact-figure grep sweep that updated every other occurrence
    missed this one because it was written as a rounded whole dollar, not the
    literal old figure being searched for. Checked directly against the live
    artifact and rounded the same way, so a future regeneration can't silently
    leave a rounded verdict figure one fix behind the exact one."""
    ccj_path = ROOT / "data" / "cca_bundled_counterfactual.json"
    assert ccj_path.exists(), f"{ccj_path} is committed public data and must exist"
    ccj = json.loads(ccj_path.read_text())
    per_year = ccj["direction_a_cca_repriced_at_bundled"]["delta_usd_per_year"]
    rounded = round(per_year)
    verdict_match = re.search(
        r'class="verdict">.*?staying on the CCA.*?about \$(\d+)/yr more', HTML)
    assert verdict_match, "section-10 verdict sentence not found in the expected shape"
    assert int(verdict_match.group(1)) == rounded, (
        f"verdict says about ${verdict_match.group(1)}/yr, but the artifact's "
        f"delta_usd_per_year ({per_year}) rounds to ${rounded}/yr")
    return f"the §10 verdict's rounded ${rounded}/yr matches the live artifact's {per_year}/yr"


def _fmt_usd(x):
    return f"${round(x):,}"


def case_sizing_curve_table_matches_the_artifact_row_by_row():
    """The §6 sizing-curve table is hand-written, not templated. An earlier
    version of this case only spot-checked the knee capacity and the two
    shipping products' paybacks -- a regeneration that changed any OTHER row's
    Save/yr, Marginal $/kWh, or fitted-cost Payback would have desynced the
    table from data/battery_sizing_curve.json with nothing to catch it. This
    case parses every row of the actual HTML table and checks every numeric
    cell (both scenarios, both payback columns) against the artifact, the same
    rigor this file already applies to the chart-series cases above (issue
    #12, adversarial review finding 3)."""
    bsc_path = ROOT / "data" / "battery_sizing_curve.json"
    assert bsc_path.exists(), f"{bsc_path} is committed public data and must exist"
    bsc = json.loads(bsc_path.read_text())
    cur_rows = {r["kwh"]: r for r in bsc["current_behavior"]["energy_sweep_at_11.5kw"]}
    post_rows = {r["kwh"]: r for r in bsc["post_behavior"]["energy_sweep_at_11.5kw"]}
    assert cur_rows.keys() == post_rows.keys(), "current/post-behavior grids diverge"

    start = HTML.index("Sizing as a curve")
    end = HTML.index("Outage endurance (simulated", start)
    table_html = HTML[start:end]
    tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
    td_re = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    tag_re = re.compile(r"<[^>]+>")
    kwh_re = re.compile(r"(\d+(?:\.\d+)?)\s*kWh")

    body_rows = [m.group(1) for m in tr_re.finditer(table_html)][1:]  # skip <thead> row
    assert len(body_rows) == len(cur_rows), (
        f"expected {len(cur_rows)} table rows (one per energy-grid point), "
        f"found {len(body_rows)}")

    checked = 0
    for row_html in body_rows:
        cells = [tag_re.sub("", c).strip() for c in td_re.findall(row_html)]
        assert len(cells) == 7, f"expected 7 cells, got {len(cells)}: {cells}"
        m = kwh_re.search(cells[0])
        assert m, f"could not find a kWh figure in the row's first cell: {cells[0]!r}"
        kwh = float(m.group(1))
        kwh = int(kwh) if kwh == int(kwh) else kwh
        assert kwh in cur_rows, f"table cites {kwh} kWh, which is not an artifact grid point"
        cur, post = cur_rows[kwh], post_rows[kwh]

        assert cells[1] == _fmt_usd(cur["save_usd"]), (
            f"{kwh} kWh: Save/yr (current) {cells[1]} != {_fmt_usd(cur['save_usd'])}")
        assert cells[2] == _fmt_usd(post["save_usd"]), (
            f"{kwh} kWh: Save/yr (post-EV-fix) {cells[2]} != {_fmt_usd(post['save_usd'])}")

        for cell, row, key in ((cells[3], cur, "marginal_save_usd_per_kwh"),
                               (cells[4], post, "marginal_save_usd_per_kwh")):
            m_val = row[key]
            if m_val is None:
                assert cell == "—", f"{kwh} kWh: expected no marginal at the first grid point, got {cell!r}"
            else:
                rounded = round(m_val)
                expected = "~$0" if rounded == 0 else f"${rounded}"
                assert cell == expected, f"{kwh} kWh: marginal cell {cell!r} != {expected!r} (raw {m_val})"

        for cell, row in ((cells[5], cur), (cells[6], post)):
            expected = f"{round(row['asset_alone_payback_years'], 1)} yr"
            assert cell == expected, (
                f"{kwh} kWh: payback cell {cell!r} != {expected!r} "
                f"(raw {row['asset_alone_payback_years']})")
        checked += 1

    assert checked == len(cur_rows), "not every artifact grid point was matched to a table row"

    knee_kwh = bsc["current_behavior"]["knee"]["kwh"]
    assert f"{knee_kwh} kWh — the knee" in HTML, (
        f"the artifact's knee ({knee_kwh} kWh) is not cited in the §6 table as expected")
    for scen_key in ("current_behavior", "post_behavior"):
        for prod in bsc[scen_key]["shipping_products_on_curve"]:
            payback = round(prod["payback_years"], 1)
            assert f"{payback} yr" in HTML, (
                f"{scen_key} {prod['name']} real-quote payback {payback} yr not found in the report")

    return f"all {checked} rows of the §6 sizing-curve table match the live artifact exactly"


def case_sizing_curve_knee_direction_is_not_reversed():
    """Codex adversarial review caught the §6 prose describing the knee's OWN
    marginal kWh as one that 'still pays back... within 10 years' when the
    artifact's knee is, by its own pre-declared rule, the first capacity whose
    marginal kWh FAILS that payback -- the exact opposite claim, which could
    steer a reader toward buying capacity the report's own rule rejects. This
    locks the corrected direction against the live artifact so a future
    regeneration can't silently reintroduce the reversal (issue #12)."""
    bsc_path = ROOT / "data" / "battery_sizing_curve.json"
    bsc = json.loads(bsc_path.read_text())
    assert "still pays back the" not in HTML, (
        "the reversed knee-direction claim ('still pays back') has resurfaced in the report")
    for scen_key in ("current_behavior", "post_behavior"):
        knee = bsc[scen_key]["knee"]
        assert knee["marginal_payback_years"] > knee["threshold_years"], (
            f"{scen_key}: the artifact's own knee must be a FAILING point "
            "(marginal payback > threshold), or the prose fix's premise is wrong")
        payback_str = f"{round(knee['marginal_payback_years'], 1)} yr"
        assert payback_str in HTML, (
            f"{scen_key} knee's marginal payback ({payback_str}) -- the number "
            "that proves the knee fails the 10-yr bar -- is not cited in the report")
    return "the report correctly states the knee's marginal kWh FAILS the 10-yr payback bar"


def case_optimality_gap_table_matches_the_artifact():
    """The §6 'How good is the controller?' table and its verdict figures are
    hand-written, not templated -- lock every cited number against the live
    artifact so a regeneration can't silently drift them (issue #13)."""
    pfd_path = ROOT / "data" / "perfect_foresight_dispatch.json"
    assert pfd_path.exists(), f"{pfd_path} is committed public data and must exist"
    pfd = json.loads(pfd_path.read_text())
    gc = pfd["greedy_comparison"]
    da = pfd["day_ahead_forecast"]
    ps = pfd["purchasing_statement"]

    checks = [
        f"${gc['greedy_save_usd']:,}",
        f"${gc['perfect_foresight_save_usd']:,.2f}",
        f"${gc['optimality_gap_usd']:,.2f}",
        f"{gc['optimality_gap_pct_of_greedy']:.1f}%",
        f"${da['save_usd']:,.2f}",
        f"${ps['remaining_gap_day_ahead_to_perfect_usd']:,.2f}",
        f"${ps['gap_attributed_to_forecast_error_usd']:,.2f}",
        f"${ps['gap_attributed_to_myopic_horizon_usd']:,.2f}",
    ]
    for value in checks:
        assert value in HTML, f"§6 controller-quality table: {value!r} not found in the report"

    assert gc["perfect_foresight_save_usd"] >= gc["greedy_save_usd"], (
        "the true optimum must never save less than the greedy policy")
    assert da["save_usd"] <= gc["perfect_foresight_save_usd"], (
        "the day-ahead case must never beat the true optimum")
    ph = pfd["day_ahead_perfect_horizon"]
    assert ph["save_usd"] <= gc["perfect_foresight_save_usd"], (
        "the perfect-horizon day-ahead case must never beat the true optimum")
    # day-ahead vs greedy is deliberately NOT constrained either way -- a
    # pre-committed schedule based on an imperfect forecast can genuinely
    # underperform a simpler real-time reactive heuristic (a real, disclosed
    # finding on this house's data: day-ahead $1,711.28 < greedy $2,329),
    # so asserting an ordering here would encode a false assumption.
    assert abs(pfd["verification"]["agreement_usd"]) < 1.0, (
        "the LP's own required $1 agreement with rates.bill_nem is not met")
    return "the §6 controller-quality table matches the live perfect-foresight artifact"


def case_tou_structure_stress_table_matches_the_artifact():
    """The §7 tariff-structure-risk table and its worst-scenario sentence are
    hand-written, not templated -- lock every cited number against the live
    artifact so a regeneration can't silently drift them (issue #14)."""
    tss_path = ROOT / "data" / "tou_structure_stress.json"
    assert tss_path.exists(), f"{tss_path} is committed public data and must exist"
    tss = json.loads(tss_path.read_text())

    def fmt(v):
        sign = "&minus;$" if v < 0 else "+$"
        return f"{sign}{abs(v):,.2f}"

    checks = []
    for key in ("onpeak_widened", "onpeak_shifted_later", "midday_sop_narrowed",
                "summer_extended"):
        s = tss["scenarios"][key]
        checks += [fmt(s["baseline_delta_usd"]), fmt(s["behavior_save_delta_usd"]),
                  fmt(s["battery_marginal_delta_usd"]), fmt(s["total_package_impact_usd"])]
    for value in checks:
        assert value in HTML, f"§7 tariff-structure-risk table: {value!r} not found in the report"

    worst = tss["worst_scenario"]
    assert f"{worst['total_package_impact_usd']:.2f}" in HTML, (
        "the worst-scenario dollar figure is not cited in the report prose")
    # every scenario's precedent label must be one that the AC allows, and the
    # summer-extension scenario specifically must be labeled hypothetical --
    # nothing here is fabricated as a fake precedent
    assert "hypothetical" in HTML, "the ungrounded scenario must be labeled hypothetical in prose"
    for key, spec in tss["scenarios"].items():
        assert spec["precedent"] in {"measured, in-corpus", "historically motivated", "hypothetical"}
    return "the §7 tariff-structure-risk table matches the live tou_structure_stress artifact"


def _dsgs_prestaging_paragraph():
    """The one <p> holding the event-aware pre-staging disclosure, isolated
    so every check below can only match INSIDE it -- several of its own
    figures ($139.95, $128.47, kWh totals) are also cited elsewhere in the
    report for the reactive baseline, and a match anywhere in the whole
    document would pass even if this specific paragraph never mentioned
    the figure at all."""
    m = re.search(r"<p><b>Event-aware pre-staging.*?</p>", HTML, re.S)
    assert m, "the DSGS event-aware pre-staging paragraph was not found in index.html"
    return m.group(0)


def case_dsgs_prestaged_sensitivity_matches_the_artifact():
    """The §6 event-aware pre-staging disclosure is hand-written, not
    templated -- lock every cited number (and the direction of the
    opportunity-cost change) against the live artifact, scoped to this
    paragraph alone (Codex review, issue #85, pass 2: several of these
    figures recur elsewhere for the reactive baseline, so an unscoped check
    would pass even on a paragraph that cited none of them), so a
    regeneration can't silently drift them and so a sign error like the one
    caught in adversarial review pass 1 ("opportunity cost falls to $0.14"
    had it backwards -- it actually WORSENS, from -$11.48 to +$0.14) can't
    recur silently."""
    dsgs_path = ROOT / "data" / "dsgs_vpp_backtest.json"
    assert dsgs_path.exists(), f"{dsgs_path} is committed public data and must exist"
    dsgs = json.loads(dsgs_path.read_text())
    reactive_rev = dsgs["revenue"]["reserve_20pct"]
    ps = dsgs["prestaged_sensitivity"]
    delta = ps["delta_vs_reactive"]
    para = _dsgs_prestaging_paragraph()

    opp_delta = ps["opportunity_cost_usd"] - reactive_rev["opportunity_cost_usd"]
    discharge_pct = delta["total_discharge_kwh"] / reactive_rev["total_discharge_kwh"] * 100

    checks = [
        f"${reactive_rev['net_usd']:,.2f}",
        f"${reactive_rev['gross_usd']:,.2f}",
        f"${ps['net_usd']:,.2f}",
        f"${ps['gross_usd']:,.2f}",
        f"+${delta['net_usd']:,.2f}",
        f"+{delta['net_usd_pct']:.1f}%",
        f"{reactive_rev['total_discharge_kwh']:,.2f} kWh",
        f"{ps['total_discharge_kwh']:,.2f} kWh",
        f"+{delta['total_discharge_kwh']:,.2f} kWh",
        f"+{discharge_pct:.1f}%",
        f"{ps['reserve_frac'] * 100:.0f}%",
        f"{ps['miss_rate']['misses']} of {ps['miss_rate']['total']}",
        f"{dsgs['miss_rate']['reserve_20pct']['misses']} reactive",
        f"{ps['miss_rate']['rate'] * 100:.1f}%",
        f"−${abs(reactive_rev['opportunity_cost_usd']):,.2f}",
        f"${ps['opportunity_cost_usd']:,.2f}",
        f"${abs(delta['gross_usd']):,.2f}",
        f"${opp_delta:,.2f}",
    ]
    for value in checks:
        assert value in para, (
            f"§6 DSGS pre-staging paragraph: {value!r} not found in it "
            f"(present elsewhere in the report doesn't count)")

    # the sign-direction bug itself: opportunity cost WORSENS (a negative
    # "cost" -- itself a net gain -- shrinking toward, then past, zero), not
    # improves, so the report must say "moves from"/"rises", never "falls"
    assert reactive_rev["opportunity_cost_usd"] < 0 < ps["opportunity_cost_usd"], (
        "this test's own premise is wrong if the artifact's signs ever change -- "
        "re-derive the assertion below, don't just delete it")
    assert abs(delta["gross_usd"] - opp_delta - delta["net_usd"]) < 0.01, (
        "gross delta minus the opportunity-cost swing must equal the net delta")
    assert "opportunity cost falls" not in para, (
        "opportunity cost RISES under pre-staging (a smaller net gain than gross "
        "alone suggests) -- 'falls' has the direction backwards")
    return "the §6 DSGS pre-staging disclosure matches the live artifact, direction included, every figure scoped to its own paragraph"


def case_heat_pump_conversion_section_matches_the_artifact():
    """The §10 heat-pump-conversion subsection is hand-written, not
    templated -- lock its headline figures (annual gas savings, the
    electric-cost-increase bracket, all three COPs' central AND bracket
    net-savings figures, and the payback/NPV figures) against the live
    artifact so a regeneration can't silently drift them (issue #1)."""
    hpc_path = ROOT / "data" / "heat_pump_conversion.json"
    assert hpc_path.exists(), f"{hpc_path} is committed public data and must exist"
    hpc = json.loads(hpc_path.read_text())
    assert hpc["applicable"], "this household's own household.has_gas must be true"

    m = re.search(r"<h3>Replacing the furnace \+ AC with a heat pump.*?</p>\s*<p><b>Going all-electric",
                  HTML, re.S)
    assert m, "the heat-pump-conversion subsection was not found in index.html"
    section = m.group(0)

    gas_savings = hpc["gas_savings_annual_usd"]
    e = hpc["electric_cost_by_scenario"]
    pb = hpc["payback"]

    def fmt_signed(v):
        sign = "−$" if v < 0 else "+$"
        return f"{sign}{abs(v):,.2f}"

    checks = [
        f"{hpc['isolation']['annual_heating_therms']} therms/yr",
        f"${gas_savings:,.2f}/yr",
        f"${e['central_3.5']['off_peak']['electric_cost_increase_usd']:,.0f}/yr",
        f"${e['central_3.5']['on_peak']['electric_cost_increase_usd']:,.0f}/yr",
        f"${e['central_3.5']['uniform']['electric_cost_increase_usd']:,.0f}/yr",
        fmt_signed(pb["central_3.5"]["annual_net_savings_usd"]) + "/yr",
        fmt_signed(pb["high_4.2"]["annual_net_savings_usd"]) + "/yr",
        fmt_signed(pb["low_2.8"]["annual_net_savings_usd"]) + "/yr",
        f"${hpc['install_cost']['standalone_usd']:,}",
        f"${hpc['install_cost']['ac_only_replacement_usd']:,}",
        f"${hpc['install_cost']['marginal_over_ac_replacement_usd']:,}",
    ]
    for value in checks:
        assert value in section, f"§10 heat-pump-conversion section: {value!r} not found in it"

    # Payback years, only for COP/basis combinations that actually pay back
    # (a None here means no positive annual net savings on that basis, and
    # the report must not cite a specific year count for it). At least one
    # combination must cite a real number, or this check would trivially
    # pass on an all-None artifact without the report actually naming
    # anything.
    cited_a_payback_year = False
    for cop_key in ("low_2.8", "central_3.5", "high_4.2"):
        for basis in ("standalone", "marginal_over_ac_replacement"):
            years = pb[cop_key][basis]["payback_years"]
            if years is not None:
                assert f"{years} years" in section, (cop_key, basis, years)
                cited_a_payback_year = True
    assert cited_a_payback_year, "no COP/basis pays back -- the report must cite at least one real payback figure"

    # the on-peak/off-peak NET-SAVINGS bracket for all three COPs (gas savings
    # minus each bracket's own electric cost) -- this is the report's own
    # central claim ("every one of the three flips sign across the bracket"),
    # so it must be independently re-derivable from the artifact, not merely
    # asserted in prose
    for cop_key in ("low_2.8", "central_3.5", "high_4.2"):
        off_net = gas_savings - e[cop_key]["off_peak"]["electric_cost_increase_usd"]
        on_net = gas_savings - e[cop_key]["on_peak"]["electric_cost_increase_usd"]
        assert off_net > 0 > on_net, (
            f"{cop_key}: the report's claim that every COP flips sign across the "
            f"bracket requires off_net>0>on_net; got off={off_net}, on={on_net}")
        assert fmt_signed(off_net) + "/yr" in section, (cop_key, off_net)
        assert fmt_signed(on_net) + "/yr" in section, (cop_key, on_net)

    # NPV figures cited on the marginal-over-AC basis, both discount rates,
    # only for COPs that actually have one (a COP with no payback reports no
    # npv key at all -- see payback_and_npv()'s own non-positive-savings path)
    cited_an_npv = False
    for cop_key in ("low_2.8", "central_3.5", "high_4.2"):
        npv = pb[cop_key]["marginal_over_ac_replacement"].get("npv")
        if npv is None:
            continue
        for rate_key in ("4pct", "5pct"):
            v = npv[rate_key]
            assert f"{'+' if v >= 0 else '−'}${abs(v):,}" in section, (cop_key, rate_key, v)
            cited_an_npv = True
    assert cited_an_npv, "no COP pays back -- the report must cite at least one real NPV figure"
    return "the §10 heat-pump-conversion section matches the live artifact, including the full sign-flip bracket"


def case_all_electric_endgame_section_matches_the_artifact():
    """The §10 water-heater / all-electric-endgame subsections are
    hand-written, not templated -- lock their headline figures against the
    live data/all_electric_endgame.json artifact (issue #20)."""
    aee_path = ROOT / "data" / "all_electric_endgame.json"
    assert aee_path.exists(), f"{aee_path} is committed public data and must exist"
    aee = json.loads(aee_path.read_text())
    assert aee["applicable"], "this household's own household.has_gas must be true"

    m = re.search(r'<h3 id="wh-real-interval">.*?<h2 id="s11">', HTML, re.S)
    assert m, "the water-heater-real-interval subsection was not found in index.html"
    section = m.group(0)

    wh = aee["water_heater_conversion"]
    fc = aee["fixed_charge_check"]["regression"]
    seq = aee["sequencing_and_paybacks"]
    hr = aee["service_headroom_check"]
    recon = aee["reconciliation"]
    headline = wh["payback"][wh["headline_uef"]]
    e = wh["electric_cost_by_scenario"][wh["headline_uef"]]
    mb = seq["share_robustness"]["marginal_basis"]

    checks = [
        f"${wh['floor_savings_annual_usd']:,.2f}/yr",
        f"${e['uniform']['electric_cost_increase_usd']:,.2f}/yr",
        f"${headline['annual_net_savings_usd']:,.2f}/yr",
        f"${e['super_off_peak']['electric_cost_increase_usd']:,.2f}/yr",
        f"${e['on_peak']['electric_cost_increase_usd']:,.2f}/yr",
        f"{headline['low_install']['payback_years']} years",
        f"{headline['central_install']['payback_years']} years",
        f"{headline['high_install']['payback_years']} years",
        f"−${abs(fc['intercept_usd']):,.2f} ± ${fc['intercept_std_error_usd']:,.2f}",
        f"{hr['water_heater_code_load_a']} A",
        f"{seq['complete_transition_payback']['combined_install_usd']:,}",
        f"${seq['complete_transition_payback']['combined_annual_net_savings_usd']:,.2f}/yr",
        f"{seq['complete_transition_payback']['payback_years']}-year",
        f"{recon['unattributed_heating_signal']['unattributed_therms_yr']:g} therms/yr",
        f"${recon['unattributed_heating_signal']['unattributed_usd']:,.2f}/yr",
        f"{mb['furnace_payback_years']}-year",
        f"{mb['crossover_water_heater_share'] * 100:.1f}%",
    ]
    for value in checks:
        assert value in section, f"§10 all-electric-endgame section: {value!r} not found in it"

    assert hr["hard_blocker"] is True, (
        "the artifact's own hard_blocker must stay True for the report's "
        "panel-space-blocker claim to be honest -- if this ever flips, the "
        "report prose needs rewriting, not just this test")
    assert "only 1 free full-size space" in section or "1 free full-size space" in section, (
        "the report must state the panel-space hard blocker plainly")
    assert seq["fixed_charge_release_usd"] == 0.0, (
        "the report's own 'no fixed charge to release' framing depends on "
        "this artifact figure staying exactly zero")

    final = seq["final_step_alone_payback"]
    assert final["with_fixed_charge_credit"]["payback_years"] == \
        final["without_fixed_charge_credit"]["payback_years"], (
        "the report claims the final-step payback is identical with/without "
        "the credit -- the artifact must actually agree")
    assert f"{final['with_fixed_charge_credit']['payback_years']} years" in section

    gap = round(headline["annual_net_savings_usd"]
               - aee["reconciliation"]["water_heater_vs_extended_results_gas_decomposition"]
               ["old_estimate_net_usd_yr"], 2)
    assert f"${abs(gap):,.2f}/yr" in section, (gap, "reconciliation gap not cited in prose")
    return ("the §10 water-heater / all-electric-endgame subsections match "
           "the live all_electric_endgame.json artifact")


def case_all_electric_paragraph_furnace_savings_matches_the_artifact():
    """Issue #109 round 3 (Codex adversarial review, pass 3): the §10
    "Going all-electric" paragraph cites the furnace's own annual gas
    savings a SECOND time, independently of the main heat-pump-conversion
    subsection case above -- and that case's own regex deliberately stops
    right before this paragraph starts (it ends the section at "<p><b>Going
    all-electric"), so a re-based gas_savings_annual_usd drifted there
    silently: three rounds of fixes updated the main subsection's own
    citation but left this second one at the pre-#109 $483/yr for two
    commits before Codex's third adversarial-review pass caught it. This
    pins the second citation independently so that gap can't reopen."""
    hpc = json.loads((ROOT / "data" / "heat_pump_conversion.json").read_text())
    if not hpc["applicable"]:
        raise SkipCase("household.has_gas is false")
    m = re.search(r"<p><b>Going all-electric.*?</p>", HTML, re.S)
    assert m, "the 'Going all-electric' paragraph was not found in index.html"
    rounded = round(hpc["gas_savings_annual_usd"])
    assert f"~${rounded}/yr heating gas" in m.group(0), (
        f"the 'Going all-electric' paragraph's own furnace-savings citation "
        f"must match the live gas_savings_annual_usd ({rounded}), not a "
        f"stale copy from an earlier regeneration")
    return "the 'Going all-electric' paragraph's furnace gas-savings citation matches the live heat-pump artifact"


def case_monte_carlo_paragraph_matches_uncertainty_results():
    """issue #106: uncertainty_results.json had zero pinning cases despite
    the §6 Monte Carlo paragraph quoting its payback median/p10/p90 and
    10-yr NPV at two discount rates in running prose -- found only by an
    adversarial review pass re-deriving the numbers by hand during issue
    #89's own soil-slope fix, which shifted these exact figures and nothing
    caught it. Regex-extracts the prose's own numbers (not the other way
    around) so a stale hand-edit in either direction fails this."""
    ur = json.loads((ROOT / "data" / "uncertainty_results.json").read_text())
    m = ur["battery_marginal_only_full_model"]
    pb_re = re.search(
        r"battery-alone payback of ([\d.]+)-yr median \(p10–p90 "
        r"([\d.]+)–([\d.]+) yr\)", HTML)
    assert pb_re, "Monte Carlo payback median/p10/p90 sentence not found in index.html"
    median, p10, p90 = (float(x) for x in pb_re.groups())
    # round(..., 1) rather than exact ==: the artifact is written at 1dp
    # today, but this pin shouldn't start failing a legitimate regeneration
    # over unrelated float noise if that ever changes upstream (adversarial
    # review, issue #106).
    assert median == round(m["payback_median"], 1), (median, m["payback_median"])
    assert p10 == round(m["payback_p10"], 1), (p10, m["payback_p10"])
    assert p90 == round(m["payback_p90"], 1), (p90, m["payback_p90"])

    npv_re = re.search(
        r"10-yr NPV: \$([\d,]+) median at 4% discount, \$([\d,]+) at 7%", HTML)
    assert npv_re, "Monte Carlo 10-yr NPV sentence not found in index.html"
    npv_4pct, npv_7pct = (int(x.replace(",", "")) for x in npv_re.groups())
    assert npv_4pct == m["npv"]["4pct"]["10yr"]["median"], (
        npv_4pct, m["npv"]["4pct"]["10yr"]["median"])
    assert npv_7pct == m["npv"]["7pct"]["10yr"]["median"], (
        npv_7pct, m["npv"]["7pct"]["10yr"]["median"])
    return ("the §6 Monte Carlo paragraph's payback median/p10/p90 and "
            "10-yr NPV at 4%/7% all match uncertainty_results.json")


def _fmt_usd2(x):
    return f"${x:,.2f}"


def case_backup_endurance_table_matches_the_artifact():
    """issue #112: the §6 outage-endurance table is hand-written, not
    templated -- lock every cited hour/day figure against the live
    artifact so a regeneration can't silently drift them."""
    be_path = ROOT / "data" / "backup_endurance.json"
    assert be_path.exists(), f"{be_path} is committed public data and must exist"
    be = json.loads(be_path.read_text())

    start = HTML.index("<h3>Outage endurance")
    end = HTML.index("</table>", start) + len("</table>")
    table_html = HTML[start:end]

    tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
    td_re = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    tag_re = re.compile(r"<[^>]+>")

    def fmt_hours(h):
        h = int(h)
        if h % 24 == 0 and h >= 24:
            return f"{h // 24} d"
        return f"{h} h"

    rows = [m.group(1) for m in tr_re.finditer(table_html)][1:]  # skip <thead> row
    configs = [("IQ 5P", "IQ 5P"), ("IQ 10C", "IQ 10C"), ("PW3", "PW3"), ("PW3 + Exp", "PW3+Exp")]
    assert len(rows) == len(configs), f"expected {len(configs)} config rows, found {len(rows)}"

    for (label, key), row_html in zip(configs, rows):
        cells = [tag_re.sub("", c).strip() for c in td_re.findall(row_html)]
        assert len(cells) == 3, f"{label}: expected 3 cells, got {cells}"
        assert cells[0] == label, f"row order drifted: expected {label!r}, found {cells[0]!r}"
        t1, t2 = be[f"{key}|t1"], be[f"{key}|t2"]
        # issue #112 Codex review: `in` is a bare substring check -- an
        # artifact median of "7 h" would also match a stale cell reading
        # "17 h". Require the duration at a word boundary so a stale digit
        # prefix/suffix can't slip through.
        median1 = re.escape(fmt_hours(t1["median_h"]))
        assert re.search(rf"(?<!\d){median1}(?!\d)", cells[1]), (
            f"{label} essentials cell {cells[1]!r} does not contain the artifact's "
            f"median ({fmt_hours(t1['median_h'])}) at a word boundary")
        assert cells[2] == fmt_hours(t2["median_h"]), (
            f"{label} house-minus-EV cell {cells[2]!r} != {fmt_hours(t2['median_h'])!r}")
        if key == "PW3":
            # issue #112 Codex review: the worst-case annotation was
            # searched across the whole table, so it could pass even if
            # moved onto a different config's row. Bind it to PW3's own
            # essentials cell specifically.
            pw3_p10 = be["PW3|t1"]["p10_h"]
            assert f"({pw3_p10} h worst-case)" in cells[1], (
                f"PW3's p10_h ({pw3_p10}) worst-case annotation not found in PW3's own "
                f"essentials cell {cells[1]!r}")
    return "the §6 outage-endurance table's hour/day figures match the live backup_endurance artifact"


def case_battery_plan_matrix_table_matches_the_artifact():
    """issue #112: the §4 battery×plan matrix table and its canonical
    cross-check paragraph are hand-written, not templated -- lock every
    cited dollar figure against the live artifact."""
    bpm_path = ROOT / "data" / "battery_plan_matrix.json"
    assert bpm_path.exists(), f"{bpm_path} is committed public data and must exist"
    bpm = json.loads(bpm_path.read_text())
    plans = bpm["plans"]

    start = HTML.index('<h2 id="s4">')
    table_end = HTML.index("</table>", start) + len("</table>")
    table_html = HTML[start:table_end]
    tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
    td_re = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    tag_re = re.compile(r"<[^>]+>")
    rows = [m.group(1) for m in tr_re.finditer(table_html)][1:]
    plan_order = ["EV-TOU-5", "EV-TOU-2", "TOU-ELEC"]
    assert len(rows) == len(plan_order), f"expected {len(plan_order)} plan rows, found {len(rows)}"
    for plan, row_html in zip(plan_order, rows):
        cells = [tag_re.sub("", c).strip() for c in td_re.findall(row_html)]
        assert cells[0] == plan, f"row order drifted: expected {plan!r}, found {cells[0]!r}"
        p = plans[plan]
        assert cells[1] == _fmt_usd(p["no_battery"]), (plan, "no_battery", cells[1])
        assert cells[2] == _fmt_usd(p["with_battery"]), (plan, "with_battery", cells[2])
        assert cells[3] == f"{_fmt_usd(p['battery_value'])}/yr", (plan, "battery_value", cells[3])

    # issue #112 adversarial review (Codex): the canonical-crosscheck prose
    # sentence lives in the <p class="small"> right AFTER the table, not
    # inside it -- searching the whole HTML document for these bare dollar
    # figures (as an earlier version of this case did) would still pass if
    # that entire crosscheck sentence were deleted, since $4,904/$2,328 are
    # also cited elsewhere in the report (e.g. §11's own baseline-bill
    # figure). Bound the search to the crosscheck paragraph specifically.
    crosscheck_start = HTML.index('<p class="small">', table_end)
    crosscheck_end = HTML.index("</p>", crosscheck_start) + len("</p>")
    crosscheck_html = HTML[crosscheck_start:crosscheck_end]
    cc = bpm["canonical_crosscheck_ev_tou_5"]
    assert f"${cc['no_battery']:,}" in crosscheck_html, (
        f"cross-check no_battery {cc['no_battery']} not cited in the §4 crosscheck paragraph")
    assert f"${cc['battery_value']:,}/yr" in crosscheck_html, (
        f"cross-check battery_value {cc['battery_value']} not cited in the §4 crosscheck paragraph")
    return "the §4 battery×plan matrix table and its canonical cross-check figures match battery_plan_matrix.json"


def case_battery_hardware_sizing_table_matches_battery_sim_artifact():
    """issue #112: the §6 'Arbitrage value (current behavior)' table is
    hand-written, not templated -- lock its four cited configs' kWh/kW and
    savings-per-year figures against the live artifact."""
    bs_path = ROOT / "data" / "battery_sim.json"
    assert bs_path.exists(), f"{bs_path} is committed public data and must exist"
    bs = {row["config"]: row for row in json.loads(bs_path.read_text())}

    start = HTML.index("<h3>Arbitrage value (current behavior)</h3>")
    end = HTML.index("</table>", start) + len("</table>")
    table_html = HTML[start:end]
    tr_re = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
    td_re = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
    tag_re = re.compile(r"<[^>]+>")
    rows = [m.group(1) for m in tr_re.finditer(table_html)][1:]

    labels = [("1× Enphase IQ 5P", "1x Enphase IQ 5P"),
              ("1× Enphase IQ 10C", "1x Enphase IQ 10C"),
              ("1× Tesla PW3", "1x Tesla Powerwall 3"),
              ("PW3 + Expansion", "PW3 + 1 Expansion")]
    assert len(rows) == len(labels), f"expected {len(labels)} config rows, found {len(rows)}"

    def fmt_kwh(v):
        return str(int(v)) if v == int(v) else str(v)

    for (row_label, json_key), row_html in zip(labels, rows):
        cells = [tag_re.sub("", c).strip() for c in td_re.findall(row_html)]
        assert cells[0] == row_label, f"row order drifted: expected {row_label!r}, found {cells[0]!r}"
        row = bs[json_key]
        expected_kwh_kw = f"{fmt_kwh(row['usable_kwh'])} / {round(row['power_kw'], 1)}"
        assert cells[1] == expected_kwh_kw, (row_label, cells[1], expected_kwh_kw)
        assert cells[2] == _fmt_usd(row["net_annual_savings"]), (row_label, cells[2])
    return "the §6 arbitrage-value table's kWh/kW and savings figures match battery_sim.json"


def case_bill_decomposition_finding_matches_the_artifact():
    """issue #112: the §10 '$48.25 became $398.56' finding is hand-written,
    not templated -- lock its two bill totals and the applied-NEM-credit
    swing that drives them against the live artifact.

    Issue #112 adversarial review (Codex, round 2): an earlier version of
    this case abs()'d both NEM-credit values and checked their presence
    anywhere in the whole finding block -- it could not tell base_usd from
    current_usd, or a credit from a charge, so REVERSING the row
    ("-128.39 -> -5.06" printed as "-5.06 -> -128.39") or flipping both
    signs positive still passed. Parses the actual table row -- the same
    "applied NEM generation credit" row -- and asserts its cells in their
    real signed, ordered form."""
    bd_path = ROOT / "data" / "bill_decomposition.json"
    assert bd_path.exists(), f"{bd_path} is committed public data and must exist"
    bd = json.loads(bd_path.read_text())
    base, current = bd["periods"]["base"], bd["periods"]["current"]
    nem = next(t for t in bd["non_energy_bridge"] if t["term"] == "applied_nem_generation_credit")

    start = HTML.index('<div class="finding"><p class="claim">A $48.25')
    end = HTML.index("</div>", start) + len("</div>")
    section = HTML[start:end]

    # issue #112 Codex review: two separate presence checks don't enforce
    # DIRECTION -- "$398.56 became $48.25" would satisfy both equally.
    # Joined into the ordered "A $X ... became $Y" claim the report prints.
    assert (f"A {_fmt_usd2(base['current_charges'])} early-summer bill became "
            f"{_fmt_usd2(current['current_charges'])}") in section, (
        "the ordered 'A $base became $current' claim not found -- bill totals may be "
        "reversed or stale")

    def fmt_signed_bare(v):
        return f"−{abs(v):.2f}" if v < 0 else f"{v:.2f}"

    tr_re = re.compile(r"<tr>\s*<td>applied NEM generation credit</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*</tr>")
    m = tr_re.search(section)
    assert m, "the 'applied NEM generation credit' table row was not found in its expected form"
    quantity_cell, change_cell = m.group(1), m.group(2)
    expected_quantity = f"{fmt_signed_bare(nem['base_usd'])} → {fmt_signed_bare(nem['current_usd'])}"
    assert quantity_cell == expected_quantity, (
        f"applied NEM generation credit row: expected {expected_quantity!r}, found {quantity_cell!r} "
        "-- base_usd/current_usd may be reversed, sign-flipped, or stale")
    expected_change = f"+{nem['change_usd']:.2f}" if nem["change_usd"] >= 0 else fmt_signed_bare(nem["change_usd"])
    assert change_cell == expected_change, (
        f"applied NEM generation credit row: expected change {expected_change!r}, found {change_cell!r}")
    return "the §10 bill-decomposition finding's dollar figures, including the signed/ordered NEM-credit row, match bill_decomposition.json"


def case_carbon_dispatch_tradeoff_paragraph_matches_the_artifact():
    """issue #112: the §13 cost-vs-carbon dispatch tradeoff paragraph is
    hand-written, not templated -- lock every cited figure (thresholds,
    per-policy CO2/savings, the tradeoff penalties, and the union policy's
    comparison ratios) against the live artifact."""
    cdt_path = ROOT / "data" / "carbon_dispatch_tradeoff.json"
    assert cdt_path.exists(), f"{cdt_path} is committed public data and must exist"
    cdt = json.loads(cdt_path.read_text())
    th = cdt["threshold"]
    base = cdt["baseline"]
    A, B, C = cdt["policies"]["A_cost_min"], cdt["policies"]["B_carbon_min"], cdt["policies"]["C_union"]
    tr = cdt["tradeoff"]
    rca = cdt["run_c_analysis"]

    m = re.search(r"<p>The price-aware dispatch this report recommends throughout.*?</p>", HTML, re.S)
    assert m, "§13 carbon-vs-cost dispatch paragraph not found in index.html"
    para = m.group(0)

    b_vs_c_gap_kg = round(B["co2_avoided_vs_baseline_kg"] - C["co2_avoided_vs_baseline_kg"], 1)
    ratio_c_vs_b = round(C["savings_vs_baseline_usd"] / B["savings_vs_baseline_usd"], 1)
    c_pct_of_a = round(C["savings_vs_baseline_usd"] / A["savings_vs_baseline_usd"] * 100)

    # issue #112 adversarial review, round 3 (Codex): an earlier version of
    # this case checked each formatted value's presence ANYWHERE in the
    # paragraph -- five of these values share the identical "X kg/yr" shape
    # (policy A's/B's/C's own avoided-CO2 figures, the cost-penalty-of-clean
    # figure, and the B-vs-C gap), so swapping e.g. policy B's 244.1 with
    # policy C's 181.1 left every check satisfied despite publishing the
    # wrong figure against the wrong policy. Anchored to sequential,
    # document-ordered preceding phrases (as case_reprice_by_vintage_note_
    # matches_the_artifact does above) so each value is tied to its own
    # clause, not just present somewhere in a paragraph this dense.
    # issue #112 /review: abs() drops the sign, so the check below only
    # pinned the magnitude, not the "above"/"below" direction word that
    # carries this paragraph's actual conclusion (cost-dispatch EMITS MORE
    # than the no-battery baseline) -- a flipped direction word would still
    # pass. Bound to the direction the artifact's own sign implies.
    a_direction = "above" if A["co2_avoided_vs_baseline_kg"] < 0 else "below"
    checks = [
        ("comes out", f"{abs(A['co2_avoided_vs_baseline_kg']):.1f} kg/yr <b>{a_direction}</b>"),
        ("baseline (", f"{A['net_co2_kg']:,.1f} vs {base['net_co2_kg']:,.1f} kg/yr"),
        ("sized to the same", f"{round(th['target_clean_frac'] * 100, 1)}%/{round(th['target_dirty_frac'] * 100, 1)}%"),
        ("thresholds (", f"{th['kg_per_mwh']:.1f} vs {round(th['discharge_kg_per_mwh'], 1)} kg/MWh"),
        ("avoids", f"{B['co2_avoided_vs_baseline_kg']:.1f} kg/yr net against that baseline but keeps only"),
        ("but keeps only", _fmt_usd2(B["savings_vs_baseline_usd"])),
        ("cost-minimizing policy's", f"{_fmt_usd2(A['savings_vs_baseline_usd'])}/yr saving"),
        ("saving —", f"{_fmt_usd2(tr['cost_penalty_of_clean_policy_usd'])}/yr cost penalty"),
        ("cost penalty (", f"{round(tr['cost_penalty_of_clean_policy_usd_per_kwh_cycled'] * 100, 1)}¢"),
        ("cheapness carries a", f"{tr['co2_penalty_of_cheap_policy_kg']:.1f} kg/yr net carbon penalty"),
        ("carbon penalty (", f"{round(tr['co2_penalty_of_cheap_policy_kg_per_kwh_cycled'], 3)} kg"),
        ("recovers", f"{_fmt_usd2(C['savings_vs_baseline_usd'])}/yr ("),
        ("/yr (", f"{c_pct_of_a}%"),
        ("still avoiding", f"{C['co2_avoided_vs_baseline_kg']:.1f} kg/yr net"),
        ("a stated", f"{round(rca['meaningful_threshold_pct'] * 100)}%-on-both-metrics"),
        ("sits within", f"{round(rca['pct_diff_co2_vs_b'] * 100, 1)}%"),
        ("but small", f"{b_vs_c_gap_kg:.1f} kg/yr"),
        ("capture roughly", f"{ratio_c_vs_b} times"),
        ("cost-minimizing run cycles", f"{round(A['kwh_cycled_thru']):,} kWh/yr"),
        ("carbon-minimizing run's", f"{round(B['kwh_cycled_thru']):,}"),
        ("agrees with its", f"{_fmt_usd2(A['savings_vs_baseline_usd'])}/${cdt['cross_check']['run_a_computed_save_usd']:,}"),
    ]
    cursor = 0
    for anchor, value in checks:
        anchor_idx = para.find(anchor, cursor)
        assert anchor_idx != -1, f"§13 carbon-dispatch-tradeoff paragraph: anchor phrase {anchor!r} not found (in order) after position {cursor}"
        window = para[anchor_idx:anchor_idx + 100]
        assert value in window, (
            f"§13 carbon-dispatch-tradeoff paragraph: {value!r} not found within 100 chars "
            f"after anchor {anchor!r} -- either drifted from its own artifact field or been "
            f"swapped with a same-shaped sibling figure")
        cursor = anchor_idx + len(anchor)
    return "the §13 carbon-vs-cost dispatch paragraph matches carbon_dispatch_tradeoff.json"


def case_tornado_battery_sensitivity_matches_extended_results():
    """issue #112: the §6 payback-sensitivity (tornado) sentence is
    hand-written, not templated -- lock its base payback and all four
    levers' swing years against the live artifact."""
    er_path = ROOT / "data" / "extended_results.json"
    assert er_path.exists(), f"{er_path} is committed public data and must exist"
    er = json.loads(er_path.read_text())
    tb = er["tornado_battery"]

    m = re.search(r"<p class=\"small\"><b>Payback sensitivity \(tornado\).*?</p>", HTML, re.S)
    assert m, "§6 tornado payback-sensitivity sentence not found in index.html"
    para = m.group(0)

    checks = [
        f"{tb['base_payback_yr']}-yr base",
        f"{tb['levers']['dispatch_policy']['swing_yr']}-yr swing",
        # issue #112 adversarial review, self-swept: three bare "(N)"/
        # "(N yr)" swing values with no lever name of their own baked in
        # would still pass if reordered among the three levers. Joined
        # into the one ordered "install quote (A yr), escalation (B), and
        # EV-fix (C)" clause the report actually prints.
        (f"the install quote ({tb['levers']['install_cost']['swing_yr']} yr), "
         f"rate escalation ({tb['levers']['escalation_5yr_avg']['swing_yr']}), and "
         f"the EV-fix interaction ({tb['levers']['post_behavior']['swing_yr']})"),
    ]
    for value in checks:
        assert value in para, f"§6 tornado sentence: {value!r} not found in it"
    return "the §6 tornado payback-sensitivity sentence matches extended_results.json's tornado_battery"


def case_ev_fleet_fuel_cost_matches_extended_results():
    """issue #112: the §9 EV-fleet fuel-cost paragraph and its supercharging
    upper-bound bullet are hand-written, not templated -- lock every cited
    dollar and kWh figure against the live artifact."""
    er_path = ROOT / "data" / "extended_results.json"
    assert er_path.exists(), f"{er_path} is committed public data and must exist"
    er = json.loads(er_path.read_text())
    ed = er["electrification_dividend"]
    sc = er["supercharge_delta"]

    m = re.search(r"<p>The fleet.s ~34,000 mi/yr costs.*?</p>", HTML, re.S)
    assert m, "§9 electrification-dividend paragraph not found in index.html"
    para = m.group(0)
    checks = [
        f"{_fmt_usd(ed['total_ev_fuel_cost'])}/yr in fuel",
        f"{_fmt_usd(ed['home_ev_cost_current_rates'])} of home charging",
        f"{ed['home_ev_kwh']:,} kWh priced at current rates",
        f"{_fmt_usd(ed['supercharge_cost_est'])} of supercharging",
        f"{ed['supercharge_kwh']:,} kWh at ~${sc['sc_price_est']}/kWh",
        f"{_fmt_usd(ed['gas_counterfactual_cost'])}/yr",
        # issue #112 Codex review round 2: the current-behavior and
        # post-fix dividend figures (both bare "~$X/yr") could swap
        # without detection. Joined into the ordered "dividend of ~$A/yr
        # ... falls to $B/yr) it rises to ~$C/yr" clause.
        (f"electrification dividend of ~{_fmt_usd(ed['dividend_yr'])}/yr</b>, and once charging is "
         f"fully super-off-peak (home charging cost falls to "
         f"{_fmt_usd(ed['home_ev_cost_if_all_sop'])}/yr) it rises to <b>"
         f"~{_fmt_usd(ed['dividend_yr_post_fix'])}/yr"),
    ]
    for value in checks:
        assert value in para, f"§9 electrification-dividend paragraph: {value!r} not found in it"

    m2 = re.search(r"<li><b>Supercharging shift.*?</li>", HTML, re.S)
    assert m2, "§9 supercharging-shift bullet not found in index.html"
    bullet = m2.group(0)
    assert f"{sc['sc_kwh']:,} kWh/yr" in bullet, "supercharge_delta.sc_kwh not cited in the bullet"
    assert f"≤{_fmt_usd(sc['full_shift_value_yr'])}/yr" in bullet, (
        "supercharge_delta.full_shift_value_yr not cited in the bullet")
    return "the §9 EV-fleet fuel-cost paragraph and supercharging bullet match extended_results.json"


def case_gas_hdd_decomposition_matches_extended_results():
    """issue #112: the §9 HDD gas-decomposition paragraph is hand-written,
    not templated -- lock its floor/slope regression figures against the
    live artifact."""
    er_path = ROOT / "data" / "extended_results.json"
    assert er_path.exists(), f"{er_path} is committed public data and must exist"
    er = json.loads(er_path.read_text())
    gd = er["gas_decomposition"]

    m = re.search(r"<p><b>The HDD decomposition agrees with the bills.*?</p>", HTML, re.S)
    assert m, "§9 HDD gas-decomposition paragraph not found in index.html"
    para = m.group(0)
    # issue #112 adversarial review, self-swept: floor_therms_day/annual_
    # floor_therms, slope_therms_per_hdd/annual_heating_therms, and
    # heating_gas_cost_yr/hpwh_saving_yr are each a same-shaped pair (both
    # "N therms/yr", or both "~$N/yr") that would still pass if swapped
    # within their own pair. Joined into the ordered clauses the report
    # actually prints.
    checks = [
        f"{gd['floor_therms_day']} therms/day → {gd['annual_floor_therms']} therms/yr",
        (f"{gd['slope_therms_per_hdd']} therms/HDD → {gd['annual_heating_therms']} therms/yr</b> "
         f"of space heating (~{_fmt_usd(gd['heating_gas_cost_yr'])}/yr at bill rates)"),
        f"~{_fmt_usd(gd['hpwh_saving_yr'])}/yr</b> priced the same midday-timer way",
    ]
    for value in checks:
        assert value in para, f"§9 HDD gas-decomposition paragraph: {value!r} not found in it"
    return "the §9 HDD gas-decomposition paragraph matches extended_results.json's gas_decomposition"


def case_nbt_flat_credit_sensitivity_matches_the_artifacts():
    """issue #112: the §13 NBT-2039 flat-credit-sensitivity paragraph is
    hand-written, not templated -- lock its real-hourly figure (from
    nem3_grandfathering.json) and its flat 3/5/8¢ bracket (from
    extended_results.json's nbt_2039) against the live artifacts."""
    er_path = ROOT / "data" / "extended_results.json"
    n3_path = ROOT / "data" / "nem3_grandfathering.json"
    assert er_path.exists(), f"{er_path} is committed public data and must exist"
    assert n3_path.exists(), f"{n3_path} is committed public data and must exist"
    er = json.loads(er_path.read_text())
    n3 = json.loads(n3_path.read_text())
    nbt = er["nbt_2039"]
    real_hourly = n3["battery_marginal_reconciliation_vs_nbt_2039"]["battery_marginal_real_hourly_usd_yr"]["NBT26"]

    m = re.search(r"<p>NEM 2\.0 runs to ~Dec 2039.*?</p>", HTML, re.S)
    assert m, "§13 NBT-2039 flat-credit-sensitivity paragraph not found in index.html"
    para = m.group(0)

    v3c = nbt["battery_marginal_under_nbt"]["3c"]["battery_marginal_yr"]
    v5c = nbt["battery_marginal_under_nbt"]["5c"]["battery_marginal_yr"]
    v8c = nbt["battery_marginal_under_nbt"]["8c"]["battery_marginal_yr"]
    checks = [
        # issue #112 Codex review round 2: these two values were bare
        # presence checks -- swapping which figure is cited "under NBT"
        # vs "under NEM 2.0" would still pass. Joined into the ordered
        # "rises to $A/yr under NBT vs $B/yr under NEM 2.0" clause.
        (f"rises</i> to <b>{_fmt_usd2(real_hourly['battery_marginal_usd_yr'])}/yr</b> under NBT vs "
         f"<b>${nbt['battery_marginal_under_nem2']:,}/yr</b> under NEM 2.0"),
        f"${v8c:,}–{v3c:,}/yr",  # the bracket, low (8c) to high (3c)
        f"3¢→${v3c:,}/yr",
        f"5¢→${v5c:,}/yr",
        f"8¢→${v8c:,}/yr",
    ]
    for value in checks:
        assert value in para, f"§13 NBT-2039 flat-credit-sensitivity paragraph: {value!r} not found in it"
    return "the §13 NBT-2039 flat-credit-sensitivity paragraph matches nem3_grandfathering.json and extended_results.json"


def case_extra_results_cleaning_cadence_matches_the_artifact():
    """issue #112: the §12 cleaning-cadence paragraph is hand-written, not
    templated -- lock its second-cleaning marginal-value range and its
    full-blended-value best case against the live artifact."""
    xr_path = ROOT / "data" / "extra_results.json"
    assert xr_path.exists(), f"{xr_path} is committed public data and must exist"
    xr = json.loads(xr_path.read_text())
    cleaning = xr["cleaning"]
    marginals = [v["marginal2nd"] for v in cleaning.values()]
    best_case = cleaning["2.4"]["save1"]

    m = re.search(r"<p>Modeling soiling accumulation over the Apr–Nov dry season.*?</p>", HTML, re.S)
    assert m, "§12 cleaning-cadence paragraph not found in index.html"
    para = m.group(0)
    assert f"${min(marginals)}–{max(marginals)}/yr" in para, (
        f"second-cleaning marginal range ${min(marginals)}-{max(marginals)}/yr not found")
    assert f"${best_case}/yr" in para, f"best-case cleaning save1 (${best_case}/yr) not found"
    return "the §12 cleaning-cadence paragraph's marginal range and best case match extra_results.json's cleaning"


def case_extra_results_trueup_ledger_matches_the_artifact():
    """issue #112: the §13 true-up ledger cross-check sentence is
    hand-written, not templated -- lock its charges/credits/net figures
    against the live artifact."""
    xr_path = ROOT / "data" / "extra_results.json"
    assert xr_path.exists(), f"{xr_path} is committed public data and must exist"
    xr = json.loads(xr_path.read_text())
    tu = xr["trueup"]

    m = re.search(r"True-up ledger cross-check:.*?reconcile exactly", HTML, re.S)
    assert m, "§13 true-up ledger cross-check sentence not found in index.html"
    sentence = m.group(0)
    assert f"{_fmt_usd2(tu['charges'])} charges" in sentence, "trueup.charges not cited"
    assert f"{_fmt_usd2(tu['credits'])} credits" in sentence, "trueup.credits not cited"
    assert f"{_fmt_usd2(tu['net'])} net" in sentence, "trueup.net not cited"
    return "the §13 true-up ledger cross-check sentence matches extra_results.json's trueup"


def case_extra_results_phantom_baseload_matches_the_artifact():
    """issue #112: the §13 phantom-baseload paragraph is hand-written, not
    templated -- lock its quiet-night count and baseload kW percentiles
    against the live artifact."""
    xr_path = ROOT / "data" / "extra_results.json"
    assert xr_path.exists(), f"{xr_path} is committed public data and must exist"
    xr = json.loads(xr_path.read_text())
    ph = xr["phantom"]

    m = re.search(r"<p>On the 44 nights with zero EV charging.*?</p>", HTML, re.S)
    assert m, "§13 phantom-baseload paragraph not found in index.html"
    para = m.group(0)
    checks = [
        f"the {ph['quiet_nights']} nights",
        # issue #112 /review: a bare "{median} kW" check would still pass
        # if the median got swapped with the paragraph's own seasonal
        # figures (also bare "N kW", e.g. "Sep-Oct nights run 1.37 kW") --
        # joined with its own "median" label and its own p10/p90 pair.
        f"median <b>{ph['baseload_kw_median']} kW</b> (p10 {ph['baseload_kw_p10']}, p90 {ph['baseload_kw_p90']})",
        f"Sep–Oct nights run {ph['monthly_kw']['9']} kW vs {ph['monthly_kw']['5']} kW in May",
    ]
    for value in checks:
        assert value in para, f"§13 phantom-baseload paragraph: {value!r} not found in it"
    return "the §13 phantom-baseload paragraph's nights/kW figures match extra_results.json's phantom"


def case_away_days_finding_matches_the_artifact():
    """issue #113: the 'Away-days corroborate the baseload floor' bullet
    said 12 strictly-away days while extended_results.json's away_days.
    n_away has always said 11 (confirmed byte-identical on regeneration,
    so this was never a stale artifact, only a hand-typed prose count that
    drifted from it) -- a plain transcription error, since the bullet's
    own median/occupied/implied-kW figures already matched the artifact
    exactly. Pins the count, and joins the two same-shaped 'N kWh/day'
    median figures with their own away/occupied labels so they can't
    silently swap (the same defect class issue #112's own review found
    repeatedly elsewhere in this file)."""
    er_path = ROOT / "data" / "extended_results.json"
    assert er_path.exists(), f"{er_path} is committed public data and must exist"
    er = json.loads(er_path.read_text())
    aw = er["away_days"]

    m = re.search(r"<li><b>Away-days corroborate the baseload floor.*?</li>", HTML, re.S)
    assert m, "'Away-days corroborate the baseload floor' bullet not found in index.html"
    bullet = m.group(0)

    assert f"the {aw['n_away']} strictly-away days" in bullet, (
        f"away_days.n_away ({aw['n_away']}) not cited as 'the N strictly-away days'")
    assert (f"import a median <b>{aw['away_median_import_kwh_day']} kWh/day "
            f"≈ {aw['implied_unattended_kw']} kW of unattended draw</b> "
            f"(occupied days: {aw['occupied_median_import_kwh_day']} kWh/day)") in bullet, (
        "the away/occupied median kWh-day figures and implied unattended kW "
        "not found together in their own ordered clause -- may be stale or swapped")
    return "the away-days finding's day count and median figures match extended_results.json"


def case_gross_import_decomposition_section_matches_the_artifact():
    """issue #112: the §9 'Gross imports are climbing' subsection is
    hand-written, not templated -- lock its bill-ground-truth kWh figures
    and both shape-assumption decomposition splits against the live
    artifact."""
    gd_path = ROOT / "data" / "gross_import_decomposition.json"
    assert gd_path.exists(), f"{gd_path} is committed public data and must exist"
    gd = json.loads(gd_path.read_text())
    bgt = gd["bill_ground_truth"]
    dec = gd["decomposition"]
    robust = gd["decomposition_identifiability_robustness_check"]

    m = re.search(r"<h3>Gross imports are climbing.*?</p>", HTML, re.S)
    assert m, "§9 'Gross imports are climbing' subsection not found in index.html"
    section = m.group(0)

    checks = [
        # issue #112 adversarial review, self-swept: bare "X kWh"/"Y kWh"
        # checked separately would pass even if the report swapped which
        # year rose and which fell (the underlying claim of this whole
        # subsection). Joined into the one ordered "2024-figure ... to
        # 2026-figure" clause the report actually prints.
        (f"{bgt['period_2024']['gross_kwh']:,.0f} kWh "
         f"({bgt['period_2024']['period'].replace(' - ', '–')}, {bgt['period_2024']['days']} days) to "
         f"{bgt['period_2026']['gross_kwh']:,.0f} kWh"),
        f"{bgt['period_2024']['net_kwh']:,.0f} → {bgt['period_2026']['net_kwh']:,.0f} kWh",
        f"{bgt['observed_delta_gross_kwh']:,.0f} kWh",
        f"+{dec['consumption_term_kwh']:,.0f} kWh consumption against +{dec['production_term_kwh']:,.0f} kWh production",
        (f"+{round(robust['consumption_term_kwh_this_scenario']):,} kWh consumption against "
         f"+{round(robust['production_term_kwh_this_scenario']):,} kWh production"),
    ]
    for value in checks:
        assert value in section, f"§9 gross-imports section: {value!r} not found in it"
    return "the §9 'Gross imports are climbing' subsection matches gross_import_decomposition.json"


def case_irreducible_bill_figures_match_the_artifact():
    """issue #112: the §7 irreducible-bill-floor paragraphs are
    hand-written, not templated -- lock the baseline floor, the per-package
    fixed/non-bypassable dollar figures, and every package's own share
    fractions against the live artifact."""
    ib_path = ROOT / "data" / "irreducible_bill.json"
    assert ib_path.exists(), f"{ib_path} is committed public data and must exist"
    ib = json.loads(ib_path.read_text())
    base = ib["baseline_floor"]
    pf = ib["package_floor_fractions"]

    m = re.search(
        r'<p class="small"><b>\$264\.10.*?</p>\s*<p class="small">Priced at each package.*?</p>',
        HTML, re.S)
    assert m, "§7 irreducible-bill-floor paragraphs not found in index.html"
    section = m.group(0)

    checks = [
        _fmt_usd2(base["strictly_irreducible_usd"]),
        f"{round(pf['LOW']['strictly_irreducible_fraction_of_projected_bill'] * 100, 1)}% of LOW's bill",
        f"{round(pf['MID']['strictly_irreducible_fraction_of_projected_bill'] * 100, 1)}% of MID's",
        f"{round(pf['HIGH']['strictly_irreducible_fraction_of_projected_bill'] * 100, 1)}% of HIGH's",
        f"{_fmt_usd2(pf['LOW']['non_bypassable_usd'])} for LOW",
        f"{_fmt_usd2(pf['MID']['non_bypassable_usd'])} for MID",
        f"{_fmt_usd2(pf['HIGH']['non_bypassable_usd'])} for HIGH",
        # issue #112 adversarial review, self-swept (same pattern Codex
        # found elsewhere in this file): these three bare percentages have
        # no LOW/MID/HIGH label of their own baked into the string, unlike
        # the checks above -- but the report always prints them together,
        # in this exact order, in one "X% / Y% / Z%" clause, so checking
        # the joined literal (not three separate substring checks) enforces
        # that order and catches a swap between any pair of them.
        (f"{round(pf['LOW']['combined_fraction_of_projected_bill'] * 100, 1)}% / "
         f"{round(pf['MID']['combined_fraction_of_projected_bill'] * 100, 1)}% / "
         f"{round(pf['HIGH']['combined_fraction_of_projected_bill'] * 100, 1)}%"),
    ]
    for value in checks:
        assert value in section, f"§7 irreducible-bill paragraphs: {value!r} not found in it"

    m2 = re.search(r'<p class="small"><b>That \$4,904/yr already includes the floor.*?</p>', HTML, re.S)
    assert m2, "§7 baseline-floor recap sentence not found in index.html"
    recap = m2.group(0)
    # issue #112 Codex review round 2: each amount/percentage pair was
    # already internally ordered, but nothing tied the FIXED-charge pair
    # to its own "is the fixed daily charge" label vs the non-bypassable
    # pair's "is non-bypassable charges" label -- swapping which pair gets
    # which label would still pass. Each check now includes its own label.
    recap_checks = [
        (f"{_fmt_usd2(base['strictly_irreducible_usd'])}/yr "
         f"({round(base['strictly_irreducible_fraction_of_projected_bill'] * 100, 1)}%) is the "
         f"fixed daily charge"),
        (f"{_fmt_usd2(base['non_bypassable_usd'])}/yr "
         f"({round(base['non_bypassable_fraction_of_projected_bill'] * 100, 1)}%) is "
         f"non-bypassable charges"),
        f"{_fmt_usd2(base['combined_usd'])}/yr",
        f"{round(base['combined_fraction_of_projected_bill'] * 100, 1)}%",
    ]
    for value in recap_checks:
        assert value in recap, f"§7 baseline-floor recap sentence: {value!r} not found in it"
    return "the §7 irreducible-bill-floor paragraphs match irreducible_bill.json"


def case_lifetime_payback_recovered_figures_match_the_artifact():
    """issue #112: the §11 lifetime-payback recommendation box is
    hand-written, not templated -- lock the invoice cost, the crossover
    dates, the no-solar counterfactual bill, and the blended $/kWh figures
    against the live artifact."""
    lp_path = ROOT / "data" / "lifetime_payback.json"
    assert lp_path.exists(), f"{lp_path} is committed public data and must exist"
    lp = json.loads(lp_path.read_text())

    m = re.search(r'<div class="rec"><b>The \$37,845 gross cost was recovered.*?</div>', HTML, re.S)
    assert m, "§11 lifetime-payback recommendation box not found in index.html"
    box = m.group(0)

    checks = [
        f"${lp['invoice_usd']:,}",
        f"{round(lp['crossover']['gross']['fraction_through_year'] * 100)}% of the way through "
        f"{lp['crossover']['gross']['year']}",
        str(lp["crossover"]["net_itc"]["year"]),
        f"${lp['nosolar_bill_usd']:,}/yr",
        # issue #112 adversarial review, self-swept: two bare "$X.XX/kWh"
        # values (current-rate vs pre-2026-TOU) with no label of their own
        # baked in would still both pass if swapped. Joined into the one
        # ordered "blended ~$A/kWh; ~$B/kWh under the pre-2026" clause.
        f"blended ~${lp['blended_new_tou']:.2f}/kWh; ~${lp['blended_old_tou']:.2f}/kWh under the pre-2026",
    ]
    for value in checks:
        assert value in box, f"§11 lifetime-payback box: {value!r} not found in it"
    return "the §11 lifetime-payback recommendation box matches lifetime_payback.json"


def case_nem3_grandfathering_section_matches_the_artifact():
    """issue #112: the §13 'What NEM 2.0 grandfathering is worth' subsection
    is hand-written, not templated -- lock its NEM-2.0/NBT bill totals, the
    grandfathering-value gap, and the Delivery-only alternative against the
    live artifact."""
    n3_path = ROOT / "data" / "nem3_grandfathering.json"
    assert n3_path.exists(), f"{n3_path} is committed public data and must exist"
    n3 = json.loads(n3_path.read_text())
    nem2 = n3["nem2"]["annual_bill_usd_modeled"]
    nbt = n3["nbt_counterfactual"]["NBT26"]
    alt = n3["generation_component_sensitivity"]["alternative_bills"]["NBT26"]

    m = re.search(r"<h3>What NEM 2\.0 grandfathering is worth.*?</p>", HTML, re.S)
    assert m, "§13 NEM-2.0-grandfathering subsection not found in index.html"
    section = m.group(0)

    # issue #112 Codex review round 3: the <h3> heading itself independently
    # cites this same primary-to-Delivery-only range ("What NEM 2.0
    # grandfathering is worth: $A-$B/yr") -- a stale/swapped HEADING would
    # go undetected by the paragraph-body checks below, since the body's
    # own correct copy of the same two numbers (found anywhere in the whole
    # `section`, heading included) would already satisfy them regardless of
    # what the heading itself said. Scoped explicitly to the heading text.
    heading_end = section.index("</h3>")
    heading = section[:heading_end]
    expected_heading_range = f"{_fmt_usd2(nbt['grandfathering_value_usd'])}–{_fmt_usd2(alt['grandfathering_value_usd'])}/yr"
    assert expected_heading_range in heading, (
        f"the <h3> heading's own '{expected_heading_range}' range not found -- it may have "
        f"drifted from, or been swapped independently of, the paragraph body's own figures")

    # issue #112 Codex review: bare presence checks don't associate each
    # total with its own scenario (NEM 2.0 vs NBT) or the two grandfathering
    # values with their own basis (primary vs Delivery-only) -- reversing
    # either pair left every check satisfied. Anchored to the report's own
    # labels (the two bill totals) and sequential document order (the two
    # grandfathering values, which share no distinguishing label of their
    # own in the prose).
    assert f"from {_fmt_usd2(nem2)} (NEM 2.0) to {_fmt_usd2(nbt['annual_bill_usd'])} (NBT)" in section, (
        "the ordered 'from $X (NEM 2.0) to $Y (NBT)' bill-total claim not found -- "
        "the two scenarios' totals may be reversed or stale")
    cursor = section.index("(NBT)")
    gap_idx = section.find("a gap of", cursor)
    assert gap_idx != -1, "'a gap of' clause not found after the NEM2/NBT bill totals"
    assert f"{_fmt_usd2(nbt['grandfathering_value_usd'])}/yr" in section[gap_idx:gap_idx + 60], (
        "the primary grandfathering-value gap not found within its own clause")
    alt_idx = section.find("prices the same measured year at", gap_idx)
    assert alt_idx != -1, "'prices the same measured year at' (Delivery-only) clause not found"
    assert f"{_fmt_usd2(alt['grandfathering_value_usd'])}/yr" in section[alt_idx:alt_idx + 60], (
        "the Delivery-only alternative grandfathering value not found within its own clause")
    return "the §13 NEM-2.0-grandfathering subsection matches nem3_grandfathering.json"


def case_reprice_by_vintage_note_matches_the_artifact():
    """issue #112: the §7 'Model vs. actual' note is hand-written, not
    templated -- lock every one of its six decomposition terms and the
    residual, plus the totals they reconcile against, to the live
    artifact.

    Issue #112 adversarial review (Codex): an earlier version of this case
    checked each formatted value's presence ANYWHERE in the note, with no
    link to its own label -- two same-shaped values (e.g. the fixed-charge
    and delivery-vintage effects, both small negative dollar figures) could
    be silently swapped in the prose and this case would still pass, since
    both formatted strings still appear somewhere in the note. Each check
    below is anchored to a short, unique phrase of its own surrounding
    prose (drawn from the report's own wording) immediately before the
    number, so a swap breaks the anchor-plus-value pairing even though
    both values remain present in the note somewhere."""
    rv_path = ROOT / "data" / "reprice_by_vintage.json"
    assert rv_path.exists(), f"{rv_path} is committed public data and must exist"
    rv = json.loads(rv_path.read_text())

    m = re.search(r'<div class="note" data-label="Model vs\. actual">.*?</div>', HTML, re.S)
    assert m, "§7 'Model vs. actual' note not found in index.html"
    note = m.group(0)

    checks = [
        ("365 days and", _fmt_usd2(rv["actual_total_sum"])),
        ("native 365-day window at", _fmt_usd2(rv["native_window_total"])),
        ("accounts for only", f"-{_fmt_usd2(abs(rv['window_effect']))}"),
        ("The largest correction,", f"-{_fmt_usd2(abs(rv['generation_tou_window_effect']))}"),
        ("generation dollars specifically (", f"-{_fmt_usd2(abs(rv['generation_clean_tou_effect']))}"),
        ("a disclosed", f"+{_fmt_usd2(rv['delivery_pcia_restart_artifact_usd'])}"),
        ('"Clean Impact Plus" product adder (', f"+{_fmt_usd2(rv['cip_adder_usd'])}"),
        ("a per-period state surcharge tax (", f"+{_fmt_usd2(rv['state_surcharge_tax_usd'])}"),
        ("Base Services Charge moves the total by", f"-{_fmt_usd2(abs(rv['fixed_charge_vintage_effect']))}"),
        ("moves the total by a further", f"-{_fmt_usd2(abs(rv['delivery_vintage_effect']))}"),
        ("accounts for only", f"-{_fmt_usd2(abs(rv['total_vintage_effect']))}"),
        ("The remaining", f"-{_fmt_usd2(abs(rv['residual_total']))}"),
    ]
    # Each anchor is searched for STARTING right after the previous anchor's
    # own position (not from the top of the note each time), and its value
    # must appear within a short window after it -- this both disambiguates
    # anchor phrases that legitimately repeat (e.g. "accounts for only"
    # appears twice: once for window_effect, once for total_vintage_effect)
    # and, per the finding above, defeats a swap: a value printed next to
    # the WRONG anchor's own nearby text fails the windowed search even
    # though it's still present somewhere else in the note.
    cursor = 0
    for anchor, value in checks:
        anchor_idx = note.find(anchor, cursor)
        assert anchor_idx != -1, f"§7 'Model vs. actual' note: anchor phrase {anchor!r} not found (in order) after position {cursor}"
        window = note[anchor_idx:anchor_idx + 120]
        assert value in window, (
            f"§7 'Model vs. actual' note: {value!r} not found within 120 chars "
            f"after anchor {anchor!r} -- either drifted from its own artifact "
            f"field or been swapped with a neighboring figure")
        cursor = anchor_idx + len(anchor)
    return "the §7 'Model vs. actual' note's decomposition terms match reprice_by_vintage.json"


def case_soiling_annual_economics_matches_the_artifact():
    """issue #112: the §12 soiling-range paragraph is hand-written, not
    templated -- lock its $/yr loss bracket against the live artifact's
    two scenario estimates."""
    sr_path = ROOT / "data" / "soiling_results.json"
    assert sr_path.exists(), f"{sr_path} is committed public data and must exist"
    sr = json.loads(sr_path.read_text())
    lo = sr["annual_economics"]["scenario_A_this_years_evidence"]["annual_lost_usd_at_0.315"]
    hi = sr["annual_economics"]["scenario_B_2024_cleaning_evidence"]["annual_lost_usd_at_0.315"]

    m = re.search(r"<p>An independent soiling study.*?</p>", HTML, re.S)
    assert m, "§12 soiling-range paragraph not found in index.html"
    para = m.group(0)
    assert f"${round(lo)}–{round(hi)}/yr" in para, (
        f"soiling loss bracket ${round(lo)}-{round(hi)}/yr not found in the §12 paragraph")
    return "the §12 soiling-range paragraph's $/yr loss bracket matches soiling_results.json"


def case_weather_regression_paragraph_matches_the_artifact():
    """issue #112: the §9 weather-regression paragraph is hand-written, not
    templated -- lock its base load, cooling-degree-day coefficient, annual
    cooling kWh, and pre-cooling/setpoint dollar values against the live
    artifact."""
    wr_path = ROOT / "data" / "weather_results.json"
    assert wr_path.exists(), f"{wr_path} is committed public data and must exist"
    wr = json.loads(wr_path.read_text())

    m = re.search(r"<p>Regressing daily non-EV load against local daily temperatures.*?</p>", HTML, re.S)
    assert m, "§9 weather-regression paragraph not found in index.html"
    para = m.group(0)

    def round10(v):
        return int(round(v / 10.0)) * 10

    checks = [
        f"~{round(wr['base_kwh_day'])} kWh/day",
        f"{wr['kwh_per_cdd65']} kWh per cooling-degree-day",
        f"{round10(wr['annual_cooling_kwh']):,} kWh/yr",
        # issue #112 Codex review: two bare "$X/yr" values (pre-cooling vs
        # setpoint) with no label of their own baked in would still pass
        # if swapped. Joined into the ordered "Pre-cooling ... $A/yr; ...
        # setpoint ... $B/yr" clause the report actually prints.
        (f"Pre-cooling in the 10am–2pm super-off-peak window (shifting ~half of "
         f"on-peak cooling) is worth ≈ <b>${round10(wr['precool_shift_value'])}/yr</b>; "
         f"a modest efficiency/setpoint improvement another ≈ <b>${round10(wr['setpoint_value'])}/yr"),
    ]
    for value in checks:
        assert value in para, f"§9 weather-regression paragraph: {value!r} not found in it"
    return "the §9 weather-regression paragraph matches weather_results.json"


# ---------------------------------------------------------------------------
# Issue #131: CLAUDE.md section 10 requires every h2 to open with a one-line
# conclusion, and the report carries one three different ways. The structural
# half of these cases -- every conclusion PRESENT, none doubled, none over the
# density cap -- is pure HTML and runs everywhere, in CI exactly as the chart
# pins above do. One half of one case goes further and asks whether each
# in-heading verdict AGREES with the token that owns it; that resolves through
# report_tokens, and a token needing private/household.yaml (S4_VERDICT_SHORT
# does) is left uncompared and named in the summary line rather than failing
# or quietly passing. Nothing here is gated as a whole.
# ---------------------------------------------------------------------------
TEMPLATE_HTML = (ROOT / "report-template.html").read_text()

_SECTION_H2_RE = re.compile(r'<h2 id="([^"]+)"[^>]*>(.*?)</h2>', re.S)
_TEMPLATE_VERDICT_TOKEN_RE = re.compile(r"\{\{[A-Z0-9_]*VERDICT[A-Z0-9_]*\}\}")


def _in_heading_verdict_scaffold():
    """{section id: (text before the slot, text after it, the slot's token
    name)} for every section whose conclusion sits INSIDE the <h2> itself,
    taken from the template's own {{..._VERDICT_SHORT}} slot.

    The template stays the source of truth for WHICH sections use the
    mechanism -- index.html is that template rendered, so it inherits the set
    by section id -- but it now also yields the literal scaffolding around the
    slot, which is what lets the rendered heading be inspected instead of
    assumed. The conclusion text itself is never pinned here: pinning it would
    make this test a second copy of the report, and it would then only ever
    fail when someone forgot to update the copy.

    The token NAME is carried alongside because the template is also the only
    place that records which token owns a given heading's conclusion; the
    agreement check below resolves it and compares.

    A slot must have literal scaffolding to be checkable, so a template that
    put another token BEFORE the verdict slot fails loudly rather than
    silently reverting to the trust-the-template behaviour this replaced."""
    out = {}
    for sid, inner in _SECTION_H2_RE.findall(TEMPLATE_HTML):
        m = _TEMPLATE_VERDICT_TOKEN_RE.search(inner)
        if not m:
            continue
        prefix, suffix = inner[:m.start()], inner[m.end():]
        assert "{{" not in prefix and "{{" not in suffix, (
            f"report-template.html's {sid} heading wraps its verdict slot in other "
            f"tokens ({inner!r}); the rendered heading can no longer be located by "
            "its literal scaffolding")
        out[sid] = (prefix, suffix, m.group(0).strip("{}"))
    return out


# A conclusion is a clause, not a word: two words is the floor, and it is the
# ONLY quantity pinned about the rendered text. Anything stricter would start
# pinning the sentences themselves.
#
# The floor is the same for all THREE mechanisms. An in-heading verdict, a
# <summary> .teaser and a <p class="verdict"> are interchangeable ways of
# carrying the one conclusion CLAUDE.md section 10 requires, so an emptied
# <p class="verdict"></p> has to fail exactly as a heading that lost its
# verdict text does. Crediting the opening TAG alone (which is what the
# paragraph and teaser mechanisms used to do) let a section keep its
# conclusion line in name only: index.html's section 2 line replaced by
# <p class="verdict"></p> left both of these cases green.
_MIN_CONCLUSION_WORDS = 2

_TOKEN_REF_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def _visible_text(fragment):
    """`fragment`'s reader-visible text: comments and tags removed, entities
    unescaped, whitespace-stripped."""
    fragment = re.sub(r"<!--.*?-->", "", fragment, flags=re.S)
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _element_text(rest, open_tag, close_tag):
    """The visible text of the element `rest` STARTS with, or None when `rest`
    does not open with `open_tag` or that element is never closed. Neither
    element this is used for (<p>, <span class="teaser">) nests in the report,
    so the first close tag is the right one."""
    if not rest.startswith(open_tag):
        return None
    end = rest.find(close_tag, len(open_tag))
    if end < 0:
        return None
    return _visible_text(rest[len(open_tag):end])


def _carries_conclusion(text, rendered):
    """Whether `text` is a conclusion rather than an empty or stub element.

    In report-template.html an unrendered {{TOKEN}} slot IS the conclusion --
    the same reason the in-heading mechanism is taken on trust there -- so a
    slot satisfies the floor. In the RENDERED report nothing is taken on
    trust: the words have to be there."""
    if text is None:
        return False
    if not rendered and _TOKEN_REF_RE.search(text):
        return True
    return len(text.split()) >= _MIN_CONCLUSION_WORDS


def _rendered_heading_verdict(inner, prefix, suffix):
    """The conclusion a rendered <h2> carries in its template slot's own
    position, or None if the heading is the bare section title.

    Everything before the slot is the section number and title -- literal text
    in the template -- so whatever sits in the slot is by construction the part
    of the heading that goes BEYOND the title. That is the claim this returns,
    which is also where the density cap's lead sentence starts."""
    if not inner.startswith(prefix) or not inner.endswith(suffix):
        return None
    end = len(inner) - len(suffix) if suffix else len(inner)
    text = _visible_text(inner[len(prefix):end])
    if len(text.split()) < _MIN_CONCLUSION_WORDS:
        return None
    return text


def _conclusion_mechanisms(doc, rendered):
    """{section id: set of conclusion mechanisms present}, parsed from the
    document rather than read off a hardcoded id list, so a section added
    later shows up here (with an empty set) instead of slipping through.

    `rendered` says whether each mechanism has to be proved by its own
    CONTENT. For report-template.html it does not -- there the unrendered
    {{...}} slot IS the mechanism, in a heading or in a paragraph. For
    index.html it does: this case exists to check the rendered report, and
    crediting a rendered section because its TEMPLATE has a slot let the
    published heading lose its verdict text (leaving the bare '4 · Does a
    battery change which plan is best?') while this test still reported a
    conclusion present. The paragraph and teaser mechanisms had the same
    hole, one tag lower down: they were credited from the opening tag."""
    scaffold = _in_heading_verdict_scaffold()
    found = {}
    for m in _SECTION_H2_RE.finditer(doc):
        sid = m.group(1)
        rest = doc[m.end():].lstrip()
        mech = set()
        if sid in scaffold and (
                not rendered
                or _rendered_heading_verdict(m.group(2), *scaffold[sid][:2])):
            mech.add("in-heading")
        if (doc[:m.start()].rstrip().endswith("<summary>")
                and _carries_conclusion(
                    _element_text(rest, '<span class="teaser">', "</span>"), rendered)):
            mech.add("summary-teaser")
        if _carries_conclusion(
                _element_text(rest, '<p class="verdict">', "</p>"), rendered):
            mech.add("verdict-line")
        found[sid] = mech
    return found


# Sections whose rendered <h2> verdict does NOT say what its own token says.
# This is a PRE-EXISTING divergence in the report, tracked in issue #141 and
# out of scope for the case below, which only has to stop it spreading:
#
#   s4  renders "No — it strengthens it." while S4_VERDICT_SHORT resolves to
#       "Yes — the battery widens EV-TOU-5's lead ... $961/yr to $1,612/yr"
#       (the token is also inverted against its own heading question -- #141).
#   s8  renders "No, no, and not yet." while S8_VERDICT_SHORT resolves to the
#       same verdict continued into its figures.
#
# WHEN ISSUE #141 LANDS THIS SET MUST BECOME EMPTY. It is not a permanent
# allowance: the case asserts in BOTH directions, so the moment a listed
# section starts agreeing with its token, this case FAILS with a message
# telling whoever fixed #141 to delete the id from here. A third divergence,
# or any new section drifting from its token, fails the other direction.
_HEADING_VERDICT_TOKEN_DIVERGENCE = {"s4", "s8"}


def _normalized_verdict(text):
    """A heading verdict reduced to what it CLAIMS: whitespace collapsed and a
    terminating period dropped, since a token supplies the clause and the
    heading may end the sentence for it."""
    return re.sub(r"\s+", " ", text).strip().rstrip(".").strip()


def _heading_verdict_agreement(rendered_headings, resolved_tokens):
    """(agreeing, diverged, unresolved) section-id sets, comparing each
    rendered in-heading verdict against the token that owns it.

    A section whose token is absent from `resolved_tokens` is `unresolved`
    and is NOT compared -- token resolution can need private/household.yaml
    (S4_VERDICT_SHORT does), which CI does not have. Kept pure so the case
    below can be driven with synthetic inputs."""
    agreeing, diverged, unresolved = set(), set(), set()
    for sid, text in rendered_headings.items():
        if sid not in resolved_tokens:
            unresolved.add(sid)
        elif _normalized_verdict(text) == _normalized_verdict(resolved_tokens[sid]):
            agreeing.add(sid)
        else:
            diverged.add(sid)
    return agreeing, diverged, unresolved


def _raised_in(exc, path):
    """Whether `exc` -- or any exception it chains from -- has a frame in the
    file `path`.

    Attribution by RAISE SITE, which is a fact about the stack, rather than by
    message text, which is prose the raising module is free to reword. The
    chain matters: report_tokens catches the household loader's SystemExit and
    re-raises its own ("failed to resolve token X: ..."), so the loader's
    frames sit on the chained __context__, not on the exception the caller
    catches. Both are walked, and a self-referential chain terminates."""
    target = pathlib.Path(path).resolve()
    seen, cur = set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        tb = cur.__traceback__
        while tb is not None:
            if pathlib.Path(tb.tb_frame.f_code.co_filename).resolve() == target:
                return True
            tb = tb.tb_next
        cur = cur.__cause__ or cur.__context__
    return False


def _missing_archive_exit(exc, archive_present, loader_path):
    """Whether this SystemExit means "this checkout has no private archive" --
    the ONLY reason a token may be dropped from the agreement comparison.

    SystemExit is report_tokens' GENERAL fail-closed signal. A missing
    private/household.yaml raises it, but so does a broken artifact, a guard
    firing correctly, an unknown token name and a bad format spec. Reading
    every one of them as "needs the private archive" turned the agreement half
    of the case below into a no-op that still reported success -- the same
    defect class the case exists to catch.

    Two conditions, neither of them a message match:
      * the archive really is absent -- `household.PATH.is_file()` is the
        loader's OWN precondition, the one household._load() tests before it
        raises, so this tracks the loader rather than paraphrasing it;
      * the failure really came out of that loader -- a frame in household.py
        somewhere on the exception chain.
    Each condition alone is too weak. Frames alone would write off a missing
    household KEY, which raises from the same module while the archive is
    present and is a real defect. Absence alone would write off any unrelated
    breakage on a machine that happens to have no archive, which is CI."""
    return not archive_present and _raised_in(exc, loader_path)


def _resolve_heading_verdict_tokens(scaffold):
    """{section id: resolved token text} for as many in-heading verdict slots
    as this machine can resolve, plus a one-line note on what was skipped.

    Resolution goes through report_tokens, the module that owns these values.
    A token that fails to resolve raises SystemExit, and exactly one kind of
    SystemExit is survivable: the one a checkout without private/household.yaml
    produces (S4_VERDICT_SHORT needs the archive; CI has none). That one leaves
    its section uncompared and named in the summary line. Every other
    SystemExit is a real token failure and fails the case -- raised as an
    AssertionError, since a SystemExit escaping would end the whole run instead
    of being reported as this case failing."""
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import household
    archive, loader = household.PATH.is_file(), household.__file__
    try:
        import report_tokens as rt
    except SystemExit as e:                       # pragma: no cover - archive-dependent
        assert _missing_archive_exit(e, archive, loader), (
            f"importing report_tokens failed, and NOT because this checkout lacks the "
            f"private archive (present: {archive}) -- that is a real failure, not "
            f"something to skip: {e}")
        return {}, f"no token resolved ({e})"
    resolved, skipped = {}, []
    for sid, (_, _, token) in sorted(scaffold.items()):
        spec = rt.TOKENS.get(token)
        assert spec is not None, (
            f"report-template.html's {sid} heading references {{{{{token}}}}}, which is "
            "not a report_tokens.TOKENS entry")
        try:
            resolved[sid] = rt.resolve_token(token, spec)
        except SystemExit as e:                   # pragma: no cover - archive-dependent
            assert _missing_archive_exit(e, archive, loader), (
                f"report_tokens could not resolve {token} (§{sid}), and NOT because this "
                f"checkout lacks the private archive (present: {archive}) -- the heading "
                f"cannot be compared against a token that is itself broken: {e}")
            skipped.append(sid)
    note = (f"{len(skipped)} unresolvable without the private archive ({sorted(skipped)})"
            if skipped else "all resolved")
    return resolved, note


def case_every_h2_section_opens_with_exactly_one_conclusion_line():
    index_mech = _conclusion_mechanisms(HTML, rendered=True)
    template_mech = _conclusion_mechanisms(TEMPLATE_HTML, rendered=False)
    assert len(index_mech) >= 16, (
        f"only {len(index_mech)} <h2 id=...> sections parsed out of index.html -- "
        "the parser probably broke")
    assert set(index_mech) == set(template_mech), (
        "index.html and report-template.html disagree about which sections exist: "
        f"index-only {sorted(set(index_mech) - set(template_mech))}, "
        f"template-only {sorted(set(template_mech) - set(index_mech))}")

    # Exactly one, in BOTH files, with no exceptions.
    for label, mechanisms in (("index.html", index_mech),
                              ("report-template.html", template_mech)):
        silent = sorted(sid for sid, m in mechanisms.items() if not m)
        assert not silent, (
            f"{label} sections with no conclusion line at all: {silent} -- every h2 needs "
            'an in-heading verdict, a <summary> .teaser, or a <p class="verdict">')
        doubled = {sid: sorted(m) for sid, m in mechanisms.items() if len(m) > 1}
        assert not doubled, (
            f"{label} sections carrying MORE than one conclusion mechanism: {doubled} -- "
            "exactly one per section (CLAUDE.md section 10)")

    drifted = {sid: (sorted(template_mech[sid]), sorted(index_mech[sid]))
               for sid in template_mech if template_mech[sid] != index_mech[sid]}
    assert not drifted, (
        f"the rendered report uses a different conclusion mechanism than its "
        f"template for: {drifted}")

    # PRESENCE is now proved; AGREEMENT is the other half. A heading carrying
    # two words at the slot's position satisfies the mechanism without saying
    # what the token that owns the slot says, so compare them. This half needs
    # report_tokens and, for some tokens, the private archive -- everything
    # above runs everywhere, and the ONLY comparison that may be skipped is one
    # a missing archive makes impossible (reported in the summary line, never
    # silently); a token that fails for any other reason fails the case.
    scaffold = _in_heading_verdict_scaffold()
    assert _HEADING_VERDICT_TOKEN_DIVERGENCE <= set(scaffold), (
        "the issue #141 divergence allowance names sections that no longer use the "
        f"in-heading mechanism: {sorted(_HEADING_VERDICT_TOKEN_DIVERGENCE - set(scaffold))}")
    rendered_headings = {sid: _rendered_heading_verdict(inner, *scaffold[sid][:2])
                         for sid, inner in _SECTION_H2_RE.findall(HTML) if sid in scaffold}
    resolved, note = _resolve_heading_verdict_tokens(scaffold)
    agreeing, diverged, unresolved = _heading_verdict_agreement(rendered_headings, resolved)

    unexpected = sorted(diverged - _HEADING_VERDICT_TOKEN_DIVERGENCE)
    assert not unexpected, (
        "rendered <h2> verdict disagrees with the token that owns it for "
        + ", ".join(f"§{sid} (heading {rendered_headings[sid]!r} vs "
                    f"{scaffold[sid][2]} {resolved[sid]!r})" for sid in unexpected)
        + " -- the heading and its token must state the same conclusion")
    healed = sorted(_HEADING_VERDICT_TOKEN_DIVERGENCE & agreeing)
    assert not healed, (
        f"§{', §'.join(healed)} now AGREES with its token -- issue #141 is fixed for it, so "
        "delete the id from _HEADING_VERDICT_TOKEN_DIVERGENCE (the set exists only to hold "
        "that known divergence and must end up empty)")

    counts = {}
    for mech in index_mech.values():
        counts[next(iter(mech))] = counts.get(next(iter(mech)), 0) + 1
    return (f"all {len(index_mech)} h2 sections in both files carry exactly one conclusion "
            f"line, by the same mechanism ({counts}); {len(agreeing)} in-heading verdict(s) "
            f"match their token, {len(diverged)} carry the known issue #141 divergence, "
            f"{len(unresolved)} not compared -- {note}")


# The density cap governs the BASIC tier only (CLAUDE.md section 10); the
# advanced tier is exempt because that audience reads for the derivation. The
# boundary is read off the document itself rather than listed, so a section
# moved across it is scoped correctly without editing this test.
#
# CLAUDE.md section 10 defines the break exactly: "35 words or fewer up to its
# first sentence-ending period (a period followed by a space or the tag's end;
# ignore periods glued to digits, as in `$264.10` or `91.5%` -- a human reader
# skips those too)". Glued means BETWEEN digits, which is why the "followed by
# a space or the end" lookahead is the whole rule: a period sitting between two
# digits is followed by a digit and can never match it. An added lookbehind for
# a preceding digit is NOT the rule and over-rejects -- it skips the real break
# in a compliant lead that ends on a figure ("...saves a modeled $1,221.") and
# runs the word count on into the next sentence.
_LEAD_SENTENCE_BREAK_RE = re.compile(r"\.(?=\s|$)")


def _lead_sentence(text):
    """Everything up to the first real sentence-ending period. A period glued
    to digits ($264.10, 91.5%, 6.2–6.5-yr) is not a break -- a human reader
    skips those too -- but a period that ENDS on a figure ($1,221.) is."""
    m = _LEAD_SENTENCE_BREAK_RE.search(text)
    return text[:m.end()] if m else text


def _over_the_density_cap(label, text):
    """A "Nw/Aa" complaint string when `text`'s lead sentence blows CLAUDE.md
    section 10's 35-word / 1-aside cap, else None."""
    lead = _lead_sentence(text)
    words = len(lead.split())
    asides = lead.count("(") + lead.count("—")
    if words > 35 or asides > 1:
        return f"{label} {words}w/{asides} asides: {lead[:70]}..."
    return None


def case_basic_tier_verdict_lines_stay_inside_the_density_cap():
    cut = HTML.find('<details id="advanced"')
    assert cut > 0, 'the advanced-tier <details id="advanced"> wrapper is missing'
    basic = HTML[:cut]
    lines = re.findall(r'<p class="verdict">(.*?)</p>', basic, re.S)
    assert len(lines) >= 8, (
        f"only {len(lines)} basic-tier .verdict lines found -- sections 0-7 and the "
        "Monday appendix should each have one")
    over = []
    for raw in lines:
        text = htmlmod.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        over.append(_over_the_density_cap(".verdict", text))

    # A .verdict line is not the only way a basic-tier section states its
    # conclusion: section 4 carries its whole conclusion inside its <h2>, so
    # collecting <p class="verdict"> alone left an overlong or aside-heavy
    # heading verdict passing a gate that claims to cover the basic tier.
    # Sections 8 and 11 use the same mechanism but sit in the ADVANCED tier
    # and are exempt (that audience reads for the derivation), which is why
    # the split is the SAME `cut` the .verdict scan above uses -- read off the
    # document, so a section moved across the boundary is scoped correctly
    # without editing this test.
    #
    # What gets measured is the heading's own claim, not the section number
    # and title in front of it: the lead sentence starts where the template's
    # literal "N · Title" prefix ends, i.e. at the verdict slot's own text.
    scaffold = _in_heading_verdict_scaffold()
    headings = [(sid, _rendered_heading_verdict(inner, *scaffold[sid][:2]))
                for sid, inner in _SECTION_H2_RE.findall(basic) if sid in scaffold]
    assert headings, (
        "no basic-tier in-heading verdict found -- section 4 carries one, so either "
        "the heading scaffolding moved or the advanced-tier boundary did")
    for sid, text in headings:
        assert text, (
            f"basic-tier §{sid} uses the in-heading verdict mechanism but its rendered "
            "<h2> carries no conclusion after the section title")
        over.append(_over_the_density_cap(f"{sid} heading", text))

    over = [o for o in over if o]
    assert not over, (
        "basic-tier lead sentences over CLAUDE.md section 10's density cap "
        f"(35 words, 1 aside): {over}")
    return (f"all {len(lines)} basic-tier .verdict lines and {len(headings)} basic-tier "
            "in-heading verdict(s) lead in 35 words or fewer with at most one aside")


def case_the_two_structural_guards_reject_the_defects_they_exist_to_catch():
    """The two cases above pass on today's report whether or not their logic
    actually checks anything -- that is exactly how all three of the defects
    fixed here survived review. This drives their helpers with inputs that HAVE
    the defect, so reintroducing any of the three fails here on real output
    instead of waiting for a future report to be wrong in the right way."""

    # 1. An empty or stub conclusion element is not a conclusion. Crediting the
    #    opening TAG made every one of these pass.
    empty_p = '<h2 id="sX">X · Title</h2>\n<p class="verdict"></p>'
    stub_p = '<h2 id="sX">X · Title</h2>\n<p class="verdict"> <b>Yes.</b></p>'
    real_p = '<h2 id="sX">X · Title</h2>\n<p class="verdict">In one sentence: it works.</p>'
    assert _conclusion_mechanisms(empty_p, rendered=True)["sX"] == set(), \
        'an empty <p class="verdict"></p> still counts as a conclusion line'
    assert _conclusion_mechanisms(stub_p, rendered=True)["sX"] == set(), \
        f'a one-word .verdict counts as a conclusion (floor is {_MIN_CONCLUSION_WORDS} words)'
    assert _conclusion_mechanisms(real_p, rendered=True)["sX"] == {"verdict-line"}, \
        "a real .verdict line stopped counting as a conclusion"
    empty_t = '<summary><h2 id="sY">Y · Title</h2>\n<span class="teaser"></span>'
    real_t = ('<summary><h2 id="sY">Y · Title</h2>\n'
              '<span class="teaser">Cleaning does not pay for itself.</span>')
    assert _conclusion_mechanisms(empty_t, rendered=True)["sY"] == set(), \
        'an empty <span class="teaser"></span> still counts as a conclusion line'
    assert _conclusion_mechanisms(real_t, rendered=True)["sY"] == {"summary-teaser"}, \
        "a real .teaser stopped counting as a conclusion"
    # ... while in the TEMPLATE the unrendered slot is the conclusion, and in
    # the rendered report an unrendered slot is not text.
    slot_p = '<h2 id="sX">X · Title</h2>\n<p class="verdict">{{SX_VERDICT}}</p>'
    assert _conclusion_mechanisms(slot_p, rendered=False)["sX"] == {"verdict-line"}, \
        "the template's own {{...}} verdict slot stopped counting"
    assert _conclusion_mechanisms(slot_p, rendered=True)["sX"] == set(), \
        "an unrendered {{...}} token passes for a conclusion in the rendered report"

    # 2. The lead sentence ends at a period followed by a space, including one
    #    that lands on a figure. A digit LOOKBEHIND skipped that break and ran
    #    the count into the next sentence, over-counting a compliant lead.
    tail = " " + " ".join(["overrun"] * 60) + "."
    ends_on_a_figure = "In one sentence: the charging fix saves a modeled $1,221." + tail
    assert _lead_sentence(ends_on_a_figure).endswith("$1,221."), \
        "a lead sentence ending on a figure is not being broken at its own period"
    assert _over_the_density_cap(".verdict", ends_on_a_figure) is None, \
        "a 9-word lead ending on a figure is reported over the 35-word density cap"
    # The glued-to-digits exemption CLAUDE.md section 10 actually names survives.
    glued = "It cost $264.10 over 91.5% of the days" + tail
    assert _lead_sentence("It cost $264.10 today.") == "It cost $264.10 today.", \
        "a period between two digits is being treated as a sentence break"
    assert _over_the_density_cap(".verdict", glued) is not None, \
        "an overlong lead whose only earlier periods are glued to digits went unreported"

    # 3. An in-heading verdict has to agree with the token that owns it, and a
    #    token this machine cannot resolve is reported as uncompared, never as
    #    agreement.
    agreeing, diverged, unresolved = _heading_verdict_agreement(
        {"sA": "No — it strengthens it.",
         "sB": "the solar array has already paid for itself ",
         "sC": "not compared"},
        {"sA": "Yes — the battery widens the lead",
         "sB": "the solar array has already paid for itself"})
    assert diverged == {"sA"}, f"heading/token divergence not detected: {diverged}"
    assert agreeing == {"sB"}, f"heading/token agreement not detected: {agreeing}"
    assert unresolved == {"sC"}, f"an unresolvable token was compared anyway: {unresolved}"

    # 4. ... and "cannot resolve" means ONE thing: this checkout has no private
    #    archive. Reading report_tokens' general fail-closed SystemExit as that
    #    single cause let a genuinely broken token quietly leave the agreement
    #    check with nothing to compare, and the case still reported success.
    #    The four inputs below are the four combinations that matter. The
    #    loader-side ones are compiled under household.py's own path so they
    #    carry a real frame in that file, which is what the classifier reads.
    loader = str(ROOT / "analysis" / "household.py")
    raise_from_loader = compile("raise SystemExit('missing private/household.yaml')",
                                loader, "exec")
    try:
        exec(raise_from_loader, {})
    except SystemExit as e:
        direct = e
    try:
        try:
            exec(raise_from_loader, {})
        except SystemExit:
            raise SystemExit("report_tokens: failed to resolve token S4_VERDICT_SHORT")
    except SystemExit as e:
        wrapped = e
    try:
        raise SystemExit("report_tokens: unknown format spec for S4_VERDICT_SHORT")
    except SystemExit as e:
        unrelated = e
    assert _missing_archive_exit(direct, False, loader), \
        "the loader's own missing-archive exit is no longer recognised, so an archive-less " \
        "checkout (CI) would fail this case instead of reporting the token uncompared"
    assert _missing_archive_exit(wrapped, False, loader), \
        "report_tokens' wrapper hides the loader failure it chains from -- the exception " \
        "chain has to be walked, not just the exception that was caught"
    assert not _missing_archive_exit(wrapped, True, loader), \
        "a token failure on a checkout that HAS the private archive is being written off " \
        "as 'needs the archive'"
    assert not _missing_archive_exit(unrelated, False, loader), \
        "a token failure with nothing to do with the private archive is still being " \
        "swallowed as one, which is what makes the agreement check a silent no-op"
    return ("the conclusion-presence, density-cap and heading/token-agreement guards each "
            "reject the defect they exist to catch, and only a missing private archive "
            "can drop a token from the agreement check")


CASES = [
    case_periods_chart_matches_its_artifact,
    case_monthly_series_match_their_artifact,
    case_hourly_profiles_match_their_artifact,
    case_battery_chart_series_match_their_artifacts,
    case_carb_chart_matches_its_artifact,
    case_spread_chart_series_match_their_artifact,
    case_every_lazy_chart_id_resolves_to_a_unique_canvas,
    case_headline_figures_present_and_stale_ones_absent,
    case_chart_and_dispatch_agree_on_non_super_off_peak,
    case_no_retired_holiday_discrepancy_note,
    case_report_totals_match_the_artifact,
    case_cca_cheaper_period_count_matches_the_artifact,
    case_cca_verdict_annualized_figure_matches_the_artifact,
    case_sizing_curve_table_matches_the_artifact_row_by_row,
    case_sizing_curve_knee_direction_is_not_reversed,
    case_optimality_gap_table_matches_the_artifact,
    case_tou_structure_stress_table_matches_the_artifact,
    case_dsgs_prestaged_sensitivity_matches_the_artifact,
    case_heat_pump_conversion_section_matches_the_artifact,
    case_all_electric_paragraph_furnace_savings_matches_the_artifact,
    case_all_electric_endgame_section_matches_the_artifact,
    case_monte_carlo_paragraph_matches_uncertainty_results,
    case_backup_endurance_table_matches_the_artifact,
    case_battery_plan_matrix_table_matches_the_artifact,
    case_battery_hardware_sizing_table_matches_battery_sim_artifact,
    case_bill_decomposition_finding_matches_the_artifact,
    case_carbon_dispatch_tradeoff_paragraph_matches_the_artifact,
    case_tornado_battery_sensitivity_matches_extended_results,
    case_ev_fleet_fuel_cost_matches_extended_results,
    case_gas_hdd_decomposition_matches_extended_results,
    case_nbt_flat_credit_sensitivity_matches_the_artifacts,
    case_extra_results_cleaning_cadence_matches_the_artifact,
    case_extra_results_trueup_ledger_matches_the_artifact,
    case_extra_results_phantom_baseload_matches_the_artifact,
    case_away_days_finding_matches_the_artifact,
    case_gross_import_decomposition_section_matches_the_artifact,
    case_irreducible_bill_figures_match_the_artifact,
    case_lifetime_payback_recovered_figures_match_the_artifact,
    case_nem3_grandfathering_section_matches_the_artifact,
    case_reprice_by_vintage_note_matches_the_artifact,
    case_soiling_annual_economics_matches_the_artifact,
    case_weather_regression_paragraph_matches_the_artifact,
    case_every_h2_section_opens_with_exactly_one_conclusion_line,
    case_basic_tier_verdict_lines_stay_inside_the_density_cap,
    case_the_two_structural_guards_reject_the_defects_they_exist_to_catch,
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
