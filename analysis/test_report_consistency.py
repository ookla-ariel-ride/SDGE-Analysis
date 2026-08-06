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
        f"{pb['central_3.5']['standalone']['payback_years']} years",
        f"{pb['central_3.5']['marginal_over_ac_replacement']['payback_years']} years",
        f"{pb['high_4.2']['marginal_over_ac_replacement']['payback_years']} years",
        f"${hpc['install_cost']['standalone_usd']:,}",
        f"${hpc['install_cost']['baseline_ac_and_furnace_replacement_usd']:,}",
        f"${hpc['install_cost']['marginal_over_ac_replacement_usd']:,}",
    ]
    for value in checks:
        assert value in section, f"§10 heat-pump-conversion section: {value!r} not found in it"

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
    # for the two COPs the report names a number for
    for cop_key in ("central_3.5", "high_4.2"):
        npv = pb[cop_key]["marginal_over_ac_replacement"]["npv"]
        for rate_key in ("4pct", "5pct"):
            v = npv[rate_key]
            assert f"{'+' if v >= 0 else '−'}${abs(v):,}" in section, (cop_key, rate_key, v)
    return "the §10 heat-pump-conversion section matches the live artifact, including the full sign-flip bracket"


CASES = [
    case_periods_chart_matches_its_artifact,
    case_monthly_series_match_their_artifact,
    case_hourly_profiles_match_their_artifact,
    case_battery_chart_series_match_their_artifacts,
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
