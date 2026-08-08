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
        assert fmt_hours(t1["median_h"]) in cells[1], (
            f"{label} essentials cell {cells[1]!r} does not contain the artifact's "
            f"median ({fmt_hours(t1['median_h'])})")
        assert cells[2] == fmt_hours(t2["median_h"]), (
            f"{label} house-minus-EV cell {cells[2]!r} != {fmt_hours(t2['median_h'])!r}")

    # PW3's own worst-case (p10) essentials-window hours are called out specifically
    pw3_p10 = be["PW3|t1"]["p10_h"]
    assert f"({pw3_p10} h worst-case)" in table_html, (
        f"PW3's p10_h ({pw3_p10}) worst-case annotation not found in the table")
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

    assert _fmt_usd2(base["current_charges"]) in section, "base bill total not found"
    assert _fmt_usd2(current["current_charges"]) in section, "current bill total not found"

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
    checks = [
        ("comes out", f"{abs(A['co2_avoided_vs_baseline_kg']):.1f} kg/yr"),
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
        f"({tb['levers']['install_cost']['swing_yr']} yr)",
        f"({tb['levers']['escalation_5yr_avg']['swing_yr']})",
        f"({tb['levers']['post_behavior']['swing_yr']})",
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
        f"~{_fmt_usd(ed['dividend_yr'])}/yr",
        f"{_fmt_usd(ed['home_ev_cost_if_all_sop'])}/yr",
        f"~{_fmt_usd(ed['dividend_yr_post_fix'])}/yr",
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
    checks = [
        f"{gd['floor_therms_day']} therms/day",
        f"{gd['annual_floor_therms']} therms/yr",
        f"{gd['slope_therms_per_hdd']} therms/HDD",
        f"{gd['annual_heating_therms']} therms/yr",
        f"~{_fmt_usd(gd['heating_gas_cost_yr'])}/yr",
        f"~{_fmt_usd(gd['hpwh_saving_yr'])}/yr",
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
        f"{_fmt_usd2(real_hourly['battery_marginal_usd_yr'])}/yr",
        f"${nbt['battery_marginal_under_nem2']:,}/yr",
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
        f"{ph['baseload_kw_median']} kW",
        f"(p10 {ph['baseload_kw_p10']}, p90 {ph['baseload_kw_p90']})",
    ]
    for value in checks:
        assert value in para, f"§13 phantom-baseload paragraph: {value!r} not found in it"
    return "the §13 phantom-baseload paragraph's nights/kW figures match extra_results.json's phantom"


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
        f"{bgt['period_2024']['gross_kwh']:,.0f} kWh",
        f"{bgt['period_2026']['gross_kwh']:,.0f} kWh",
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
        f"{round(pf['LOW']['combined_fraction_of_projected_bill'] * 100, 1)}%",
        f"{round(pf['MID']['combined_fraction_of_projected_bill'] * 100, 1)}%",
        f"{round(pf['HIGH']['combined_fraction_of_projected_bill'] * 100, 1)}%",
    ]
    for value in checks:
        assert value in section, f"§7 irreducible-bill paragraphs: {value!r} not found in it"

    m2 = re.search(r'<p class="small"><b>That \$4,904/yr already includes the floor.*?</p>', HTML, re.S)
    assert m2, "§7 baseline-floor recap sentence not found in index.html"
    recap = m2.group(0)
    recap_checks = [
        f"{_fmt_usd2(base['strictly_irreducible_usd'])}/yr ({round(base['strictly_irreducible_fraction_of_projected_bill'] * 100, 1)}%)",
        f"{_fmt_usd2(base['non_bypassable_usd'])}/yr ({round(base['non_bypassable_fraction_of_projected_bill'] * 100, 1)}%)",
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
        f"${lp['blended_new_tou']:.2f}/kWh",
        f"${lp['blended_old_tou']:.2f}/kWh",
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

    checks = [
        _fmt_usd2(nem2),
        _fmt_usd2(nbt["annual_bill_usd"]),
        f"{_fmt_usd2(nbt['grandfathering_value_usd'])}/yr",
        f"{_fmt_usd2(alt['grandfathering_value_usd'])}/yr",
    ]
    for value in checks:
        assert value in section, f"§13 NEM-2.0-grandfathering subsection: {value!r} not found in it"
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
        f"${round10(wr['precool_shift_value'])}/yr",
        f"${round10(wr['setpoint_value'])}/yr",
    ]
    for value in checks:
        assert value in para, f"§9 weather-regression paragraph: {value!r} not found in it"
    return "the §9 weather-regression paragraph matches weather_results.json"


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
    case_gross_import_decomposition_section_matches_the_artifact,
    case_irreducible_bill_figures_match_the_artifact,
    case_lifetime_payback_recovered_figures_match_the_artifact,
    case_nem3_grandfathering_section_matches_the_artifact,
    case_reprice_by_vintage_note_matches_the_artifact,
    case_soiling_annual_economics_matches_the_artifact,
    case_weather_regression_paragraph_matches_the_artifact,
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
