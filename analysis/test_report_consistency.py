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
import csv
import datetime as dt
import hashlib
import html as htmlmod
import json
import pathlib
import re
import sys

import suite_runner

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
#
# TWO KINDS OF ENTRY LIVE HERE, and they are not equally permanent (issue #141
# review round 3, finding 5). Read the group comments before adding one, and
# say which kind the new entry is.
#
#   RETIRED BY METHOD. The first group below. A model changed -- a session
#   detector, a payback framing -- so the old figure is not something the
#   current pipeline can produce from any data. It cannot recur, and the
#   entry is permanent.
#
#   RETIRED BY REGENERATION. The second group. The method is unchanged; the
#   INPUTS were re-based, and every one of these figures is a value the
#   current script would print again given data that rounds there. So they
#   CAN recur, and this list is the one place in the suite where a correct
#   report can fail: `battery_alone_payback_evening_only_yr` has already
#   published 8.4, then 8.8, then 8.5, then 8.4 again (3ca0903, 298ad02,
#   58fb13c, 28daa97), so "8.5 yr" is one regeneration away from being both
#   the current figure and a banned string.
#
# WHAT TO DO IF THAT HAPPENS: the report is right and the list is wrong.
# Delete the entry -- it has stopped describing a stale figure -- rather than
# re-basing the prose to dodge it. What keeps the check honest in the
# meantime is not this list but the PRESENCE half of the same case, which
# derives each current figure from the artifact and requires it in the
# document; the absence half only catches the specific superseded string a
# sweep is known to have missed.
RETIRED_FIGURES = [
    # RETIRED BY METHOD (cannot recur).
    "560 charging sessions",   # pre-correction EV session count
    "$2,325",                  # pre-correction PW3 price-aware annual save
    "$3,438",                  # pre-correction MID package savings
    "$4,884",                  # pre-correction baseline bill at current rates
    "9.4-yr median",           # pre-correction Monte Carlo payback (now 6.0)
    "median 9.4 yr",           # same retired figure, its other prose form
    # RETIRED BY METHOD (issue #189). The stored-kWh cost priced the forgone
    # midday export at credit() (the surplus end) when every super-off-peak
    # bucket is net-import and so settles at energy(). The current pipeline
    # derives the figure from the run's own charging intervals and cannot
    # produce 8.4 again. The CENTS form only: "8.4 yr" is the CURRENT
    # evening-only battery payback and must stay in the document.
    "~8.4¢",                   # pre-correction stored-kWh cost from solar
    "8.4¢",                    # same figure, untilded form
    # RETIRED BY REGENERATION (can recur -- see the note above).
    # The §3 "vs. current" column and the §4 conclusion sentence, on the
    # plan_results.csv / battery_plan_matrix.json generation that preceded
    # 28daa97 ("regenerate on the corrected interval data and the confirmed
    # holiday rule"). Both files' LEVEL cells were re-based there; these
    # DIFFERENCES between them were not, so every one of them reconciled
    # perfectly against the superseded artifact and looked arbitrary against
    # the committed one. Listed as bare figures because that is the only form
    # they ever took, and none of these amounts is cited anywhere else.
    "$959",                    # EV-TOU-2 margin, no battery (now $961)
    "$1,472",                  # TOU-ELEC margin, no battery (now $1,474)
    "$1,516",                  # TOU-DR-P margin (now $1,518)
    "$1,982",                  # TOU-DR1 margin (now $1,990)
    "$2,329",                  # TOU-DR2 margin (now $2,338)
    "$1,609",                  # EV-TOU-2 margin, with battery (now $1,612)
    "$2,782",                  # TOU-ELEC margin, with battery (now $2,785)
    "8.5 yr",                  # evening-only battery payback (now 8.4 yr)
    # Issue #189. The grid top-up cost stopped being an asserted constant and
    # became a DERIVED figure, currently 14.023¢. It belongs in this group and
    # not the one above: the method that produces it is the current one, and it
    # sits one regeneration or one small rate change away from rounding back to
    # 13.9¢. If that happens the report is right and this entry is wrong --
    # delete it, per the group note above, rather than re-basing prose to dodge it.
    "13.9¢",                   # superseded grid top-up cost (now 14.0¢)
]

# THIS LIST IS SCOPED TO index.html ON PURPOSE, and the reason is measured
# rather than assumed (issue #141 review round 3, finding 3). TECHNICAL.md
# quotes the same three artifacts and was missed by an index.html-only sweep
# twice, so extending the absence check to it was tried against the file:
#
#   FALSE POSITIVES, 5 hits over 2 strings. "$2,329" appears four times in
#   sections 3.13/3.14 as the THEN-CURRENT greedy annual save -- a different
#   quantity from the retired TOU-DR2 plan margin that put the string on this
#   list -- inside sentences whose subject is the lineage itself ("moved to
#   $2,328/yr (from $2,329/yr)"), which cannot be edited away without
#   deleting the history TECHNICAL.md exists to hold. "8.5 yr" appears once
#   more as a rate-decline sensitivity endpoint. Naming superseded figures is
#   that document's job; index.html is forbidden it (CLAUDE.md section 9).
#
#   TRUE POSITIVES, 0, and 6 SILENT MISSES. Section 3.4 transcribes the
#   artifact's schema in BARE numbers -- 4884, 3438, 2325 -- so the "$4,884"
#   forms on this list test absent while the line is stale. Every stale
#   TECHNICAL.md figure this review found was either bare or a rounded
#   restatement ("~$4,880/yr"), which no exact-string blocklist reaches.
#
# So the gate is deliberately NOT extended: it would fire only falsely there.
# What that file needs is the POSITIVE check instead -- each current artifact
# value asserted present in the passage that claims to quote it -- which is
# what the presence half of case_headline_figures_present_and_stale_ones_absent
# does for index.html, and is a separate piece of work.

# The retired holiday-convention explanation; checked absent in
# case_no_retired_holiday_discrepancy_note. Both pipelines now share the
# canonical day-type rule, so any surviving copy of this note is false.
RETIRED_HOLIDAY_PHRASES = [
    "8,467",
    "treats seven weekday holidays as weekends",
    "holiday convention",
]


class SkipCase(Exception):
    """Typed skip signal, matching the convention 40 sibling suites already
    use. This file RAISED it without defining or importing it (issue #146), so
    the one branch that skips -- a household with no gas, where
    heat_pump_conversion.json is not applicable -- raised NameError instead.
    Since #236 main() catches Exception through suite_runner.CASE_FAILURES, so
    that surfaced as a FAILING consistency suite rather than the crashed run
    the issue predicted: a checkout whose household legitimately has no gas
    could not get a green suite at all, which is exactly the reproducer this
    repo is written for.
    """


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
    new-present AND old-absent catches every drift this branch actually had.

    TWO OF THE SIX PINS ARE EV FIGURES, AND ONE HOUSEHOLD IN N HAS NO EV
    (issue #147). behavior_rebuild.py writes `detection` and `scenarios.a` as
    explicit not-applicable stubs when the intake says household.has_ev is
    false: neither carries the field this case subscripts, so it raised
    KeyError -- not a FAIL naming a stale figure, an ERROR in a case that is
    otherwise entirely about non-EV artifacts, taking its four sound pins down
    with it. Those two are now gated on the artifact's own applicability flag
    and the other four run everywhere.

    THE EV HOUSEHOLD'S PINS ARE UNCHANGED. Where the artifact carries a real
    detection block, both figures are asserted exactly as before: a checkout
    whose report drops the session count or re-bases scenario a still fails
    here. The gate reads the artifact, never the report, so it cannot be
    satisfied by an index.html that simply stopped printing them."""
    BR = json.loads((ROOT / "data" / "behavior_rebuild.json").read_text())
    PK = json.loads((ROOT / "data" / "package_results.json").read_text())
    DP = json.loads((ROOT / "data" / "deep_results.json").read_text())
    has_ev = not (BR["detection"].get("not_applicable")
                  or BR["scenarios"]["a"].get("not_applicable"))
    current = [
        f"${DISPATCH['pw3']['greedy']['save']:,}",
        f"${PK['packages']['MID']['savings_yr']:,}",
        f"${DISPATCH['baseline_bill_current_rates']:,}",
        f"median payback {DP['monte_carlo']['payback_median']:.1f} yr",
    ]
    if has_ev:
        current += [
            f"{BR['detection']['sessions']} charging sessions",
            f"${BR['scenarios']['a']['saved']:,.0f}/yr",
        ]
    for new_form in current:
        assert new_form in HTML, f"current figure missing from the report: {new_form!r}"
    for old_form in RETIRED_FIGURES:
        assert old_form not in HTML, f"stale figure survives in the report: {old_form!r}"
    return (f"{len(current)} headline figures per artifact class are present and their "
            "stale forms absent"
            + ("" if has_ev else " (the two EV pins do not apply: "
                                 "household.has_ev is false)"))


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


def case_report_import_and_export_totals_match_the_artifact():
    """report_data.json's `totals` block holds five figures; this case covers
    the two meter flows the report actually prints, and says so in its name.

    Issue #131 review round 6: it was called case_report_totals_match_the_
    artifact and checked one of the five (`imp`), so its name claimed the
    whole block. `net`, `energy_cost` and `bsc` are not cited in index.html in
    any form this case could pin -- `bsc` would reduce to a bare "290"
    substring search -- so nothing here claims to cover them.

    The two that ARE cited sit in one §1 clause, and they are pinned as that
    ordered clause rather than as two document-wide substring searches: both
    numbers would still be present if the report swapped which one it called
    imported."""
    t = RD["totals"]
    clause = f"SDG&amp;E's meter ({t['imp']:,} kWh imported, {t['exp']:,} kWh exported)"
    assert clause in HTML, (
        f"the §1 meter-flow clause {clause!r} was not found in index.html -- the import "
        "and export totals may be stale, reversed, or the clause reworded")
    return "the report's annual import and export meter totals match report_data.json"


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

    # Issue #131 review round 6: these two messages named the §6 table while
    # searching the whole document, so a figure deleted from the table would
    # still have been found elsewhere in the report. Both are bounded to the
    # table block the loop above already parsed.
    knee_kwh = bsc["current_behavior"]["knee"]["kwh"]
    assert f"{knee_kwh} kWh — the knee" in table_html, (
        f"the artifact's knee ({knee_kwh} kWh) is not cited in the §6 table as expected")
    for scen_key in ("current_behavior", "post_behavior"):
        for prod in bsc[scen_key]["shipping_products_on_curve"]:
            payback = round(prod["payback_years"], 1)
            assert f"{payback} yr" in table_html, (
                f"{scen_key} {prod['name']} real-quote payback {payback} yr not found in "
                "the §6 sizing-curve table")

    return f"all {checked} rows of the §6 sizing-curve table match the live artifact exactly"


def case_sizing_curve_knee_direction_is_not_reversed():
    """Codex adversarial review caught the §6 prose describing the knee's OWN
    marginal kWh as one that 'still pays back... within 10 years' when the
    artifact's knee is, by its own pre-declared rule, the first capacity whose
    marginal kWh FAILS that payback -- the exact opposite claim, which could
    steer a reader toward buying capacity the report's own rule rejects. This
    locks the corrected direction against the live artifact so a future
    regeneration can't silently reintroduce the reversal (issue #12).

    What it can establish is the reversal's ABSENCE plus the presence of the
    number that contradicts it, which is what the summary line now says. Issue
    #131 review round 6: that line used to read "the report correctly states
    the knee's marginal kWh FAILS the 10-yr bar", a claim about what the prose
    says that no assertion here makes -- the prose could describe the knee any
    way at all, so long as it avoided one retired phrasing."""
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
    return ("the reversed knee-direction claim is absent, and both scenarios' knee "
            "marginal paybacks -- which fail the artifact's own 10-yr bar -- are cited")


def case_optimality_gap_table_matches_the_artifact():
    """The §6 'How good is the controller?' table and its verdict figures are
    hand-written, not templated -- lock every cited number against the live
    artifact so a regeneration can't silently drift them (issue #13).

    Issue #131 review round 6: the checks searched the whole document, while
    the summary line said the §6 table matched. Several of these values recur
    elsewhere in the report ("$2,328" appears twelve times), so the table
    could have lost a figure entirely and the search would still have found
    it in another section. Scoped to the subsection that owns them."""
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
    start = HTML.index("<h3>How good is the controller?")
    end = HTML.index("<h3", start + 4)
    section = HTML[start:end]
    for value in checks:
        assert value in section, (
            f"§6 controller-quality subsection: {value!r} not found in it "
            "(present elsewhere in the report doesn't count)")

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
    return ("the §6 controller-quality subsection's own figures match the live "
            "perfect-foresight artifact")


def case_tou_structure_stress_table_matches_the_artifact():
    """The §7 tariff-structure-risk table and its worst-scenario sentence are
    hand-written, not templated -- lock every cited number, and the precedent
    label sitting beside it, against the live artifact so a regeneration can't
    silently drift them (issue #14).

    Issue #131 review round 6: the precedent half of this case claimed more
    than it checked. Its own comment said "the summer-extension scenario
    specifically must be labeled hypothetical", and the assertion under it was
    `"hypothetical" in HTML` -- a word that appears seven times in this report
    for unrelated reasons (a "hypothetical Powerwall 3", "a real structural
    change, not a hypothetical one"), so it would have passed with the label
    stripped off the table entirely, or moved onto the wrong scenario's row.
    Each scenario is now matched to its OWN table row by its four delta cells
    and its precedent cell read off that row, which binds the label to the
    scenario without pinning either one's prose. Those four deltas are read
    from the row rather than searched for document-wide for the same reason."""
    tss_path = ROOT / "data" / "tou_structure_stress.json"
    assert tss_path.exists(), f"{tss_path} is committed public data and must exist"
    tss = json.loads(tss_path.read_text())

    def fmt(v):
        sign = "&minus;$" if v < 0 else "+$"
        return f"{sign}{abs(v):,.2f}"

    m = re.search(r'<div class="note" data-label="Risk">.*?</div>', HTML, re.S)
    assert m, "the §7 tariff-structure-risk note was not found in index.html"
    note = m.group(0)
    rows = []
    for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", note, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr.group(1), re.S)]
        if len(cells) == 6:                    # scenario, precedent, 4 deltas
            rows.append(cells)
    assert len(rows) == len(tss["scenarios"]), (
        f"the §7 table has {len(rows)} scenario rows but the artifact has "
        f"{len(tss['scenarios'])} scenarios")

    allowed = {"measured, in-corpus", "historically motivated", "hypothetical"}
    for key, spec in sorted(tss["scenarios"].items()):
        deltas = [fmt(spec["baseline_delta_usd"]), fmt(spec["behavior_save_delta_usd"]),
                  fmt(spec["battery_marginal_delta_usd"]),
                  fmt(spec["total_package_impact_usd"])]
        matched = [r for r in rows if r[2:] == deltas]
        assert len(matched) == 1, (
            f"§7 tariff-structure-risk table: the {key} scenario's four deltas {deltas} "
            f"match {len(matched)} rows of the table, not exactly one -- a cell has "
            "drifted from the artifact, or two scenarios' rows have been swapped")
        assert spec["precedent"] in allowed, (
            f"{key} carries precedent {spec['precedent']!r}, which is not one of the "
            f"labels this analysis allows ({sorted(allowed)}) -- a fabricated precedent")
        assert matched[0][1] == spec["precedent"], (
            f"§7 tariff-structure-risk table labels the {key} scenario "
            f"{matched[0][1]!r}, but the artifact's own precedent for it is "
            f"{spec['precedent']!r} -- the label and the scenario it sits beside "
            "must not drift apart")

    worst = tss["worst_scenario"]
    assert f"{worst['total_package_impact_usd']:.2f}" in note, (
        "the worst-scenario dollar figure is not cited in the §7 risk note's own prose")
    return ("the §7 tariff-structure-risk table matches the live tou_structure_stress "
            "artifact row by row, each scenario's precedent label included")


def _dsgs_prestaging_paragraph():
    """The consecutive <p>s holding the event-aware pre-staging disclosure
    (a lead paragraph plus its .small continuations, issue #255), isolated
    so every check below can only match INSIDE it -- several of its own
    figures ($139.95, $128.47, kWh totals) are also cited elsewhere in the
    report for the reactive baseline, and a match anywhere in the whole
    document would pass even if this specific paragraph never mentioned
    the figure at all."""
    m = re.search(r"<p><b>Event-aware pre-staging.*?</p>(?:\s*<p><span class=\"small\">.*?</p>)*", HTML, re.S)
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
    # SKIP, not assert (issue #146). The sibling case that reads this artifact
    # already skips when it is not applicable; this one asserted, so a household
    # with no gas -- for which "not applicable" is the correct and expected
    # answer -- got a FAILING consistency suite from the one document it cannot
    # produce. Two cases, one artifact, two different answers to the same
    # question was the actual reason a gasless checkout could not go green.
    if not hpc["applicable"]:
        raise SkipCase("household.has_gas is false")

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
    # SKIP, not assert (issue #146). all_electric_endgame.json is gated on the
    # same household.has_gas as heat_pump_conversion.json, so a gasless checkout
    # gets BOTH marked not applicable. Fixing only the heat-pump pair left this
    # one failing and the suite still red -- the third site of the same defect,
    # found by driving the scenario rather than by reading for it.
    if not aee["applicable"]:
        raise SkipCase("household.has_gas is false")

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


def _s3_plan_table_rows():
    """[(plan, [cell texts])] for §3's rate-plan table, header row dropped.

    The plan name is the leading run of tariff characters, so the first cell's
    reader-facing decoration comes off: "EV-TOU-5 ✓ current" and the footnoted
    "TOU-DR-P*" both name a plan the artifacts price."""
    start = HTML.index('<h2 id="s3">')
    table_end = HTML.index("</table>", start) + len("</table>")
    table_html = HTML[start:table_end]
    tag_re = re.compile(r"<[^>]+>")
    rows = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.S):
        cells = [htmlmod.unescape(tag_re.sub("", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]
        if cells:
            rows.append((re.match(r"[A-Za-z0-9-]+", cells[0]).group(0), cells))
    return rows


def case_plan_and_battery_margins_match_their_artifacts():
    """The report's plan MARGINS -- §3's "vs. current" column, §0's
    runner-up line and §4's conclusion sentence -- against the two artifacts
    that price them (issue #141).

    Every one of these is a DIFFERENCE the prose computes by hand between two
    artifact levels, and nothing checked them. The sibling cases above pin the
    LEVELS in both tables, so when 28daa97 re-based both artifacts the levels
    were updated and all seven differences were not: each still reconciled to
    the cent against the superseded generation, which is exactly why they read
    as deliberate rather than stale. Differences are what CLAUDE.md prefers to
    quote, so they are what this pins.

    Nothing here is hardcoded: the margins are computed from the artifacts and
    the base plan is read off the row the report itself marks class="win", so
    a household on another plan is measured against its own base.
    """
    plan_rows = list(csv.DictReader(
        (ROOT / "data" / "plan_results.csv").read_text().splitlines()))
    cea = {r["plan"]: float(r["total"]) for r in plan_rows if r["provider"] == "CEA"}
    bpm = json.loads((ROOT / "data" / "battery_plan_matrix.json").read_text())["plans"]

    # THE TWO ARTIFACTS' no-battery columns, cross-checked before either is
    # used to judge the prose. Case: SAME QUANTITY, INDEPENDENTLY COMPUTED --
    # not one derived from the other, and not two scenarios. Evidence:
    # analysis/battery_plan_matrix.py re-bills the year itself (bill_plan(),
    # its own published-rate-table engine) and then asserts its own result
    # against plan_results.csv at `abs(no_b - ref[plan]) < 1.0` (line ~215),
    # reading ref from the provider == "CEA" rows; it stores round(no_b).
    # So each column may sit up to $1.00 (the generator's own tie-out
    # tolerance) plus $0.50 (its rounding) from the CSV, and a DIFFERENCE of
    # two such cells up to $3.00. Anything past that is the two generators
    # actually disagreeing, not float noise, so it fails here rather than
    # being averaged over.
    base_plan = None
    for plan, cells in _s3_plan_table_rows():
        assert plan in cea, f"§3 prices {plan!r}, which is not in plan_results.csv"
        assert cells[1] == _fmt_usd(cea[plan]), (
            f"§3's {plan} level cell {cells[1]} is not plan_results.csv's "
            f"{_fmt_usd(cea[plan])}")
        if cells[3] == "—":
            assert base_plan is None, "§3 marks more than one plan as the current one"
            base_plan = plan
    assert base_plan, '§3 has no "—" row naming the household\'s current plan'

    for plan in bpm:
        assert plan in cea, f"battery_plan_matrix.json prices {plan!r}, absent from the CSV"
        if plan == base_plan:
            continue
        csv_margin = cea[plan] - cea[base_plan]
        bpm_margin = bpm[plan]["no_battery"] - bpm[base_plan]["no_battery"]
        assert abs(csv_margin - bpm_margin) <= 3.0, (
            f"plan_results.csv and battery_plan_matrix.json disagree about {plan}'s "
            f"no-battery margin over {base_plan}: ${csv_margin:,.2f} vs ${bpm_margin:,} "
            "-- past the $3.00 the generator's own $1.00 tie-out and two roundings allow")

    # §3's "vs. current" column, every row. Tolerance $1.00 and not exact
    # equality because the column may legitimately be written either as
    # round(a - b) or as the difference of the two rounded cells beside it,
    # which differ by at most a dollar; every stale value this case exists to
    # catch was $2-$9 out, well clear of that.
    checked = 0
    for plan, cells in _s3_plan_table_rows():
        if plan == base_plan:
            continue
        printed = float(cells[3].lstrip("+$").replace(",", ""))
        exact = cea[plan] - cea[base_plan]
        assert abs(printed - exact) <= 1.0, (
            f"§3's 'vs. current' cell for {plan} prints +${printed:,.0f} against "
            f"plan_results.csv's ${exact:,.2f}")
        checked += 1
    assert checked >= 5, f"only {checked} margin cells found in §3's table"

    # §4's conclusion sentence, which quotes the same margin at BOTH battery
    # states for every rival in the matrix. Bounded to that paragraph: these
    # are bare dollar figures, and searching the whole document would let the
    # sentence be deleted outright and still pass.
    s4 = HTML.index('<h2 id="s4">')
    concl_start = HTML.index("<p><b>Conclusion:</b>", s4)
    conclusion = HTML[concl_start:HTML.index("</p>", concl_start)]
    quoted = []
    s4_printed = {}
    for plan in bpm:
        if plan == base_plan:
            continue
        for column in ("no_battery", "with_battery"):
            margin = bpm[plan][column] - bpm[base_plan][column]
            assert _fmt_usd(margin) in conclusion, (
                f"§4's conclusion does not quote {plan}'s {column} margin over "
                f"{base_plan}, {_fmt_usd(margin)}: {conclusion}")
            quoted.append(_fmt_usd(margin))
        # The FIGURE §4 actually prints for this plan's no-battery margin --
        # the first dollar amount after the plan's name, which is the "from
        # ~$X/yr" of "over TOU-ELEC from ~$1,474 to ~$2,785". Read separately
        # from the presence checks above because those ask whether the
        # artifact's number is somewhere in the sentence; the comparison below
        # asks what the READER is shown.
        m = re.search(re.escape(plan) + r"[^$]*\$([\d,]+)", conclusion)
        assert m, (f"§4's conclusion names {plan} with no dollar figure after it, so its "
                   f"no-battery margin cannot be compared with §3's: {conclusion}")
        s4_printed[plan] = float(m.group(1).replace(",", ""))

    # THE TWO PUBLISHED MARGINS AGAINST EACH OTHER (issue #141 review round 3,
    # finding 4). Everything above pins each section to its OWN artifact --
    # §3 to plan_results.csv within $1.00, §4 to battery_plan_matrix.json by
    # exact string -- and separately allows the two artifacts to sit $3.00
    # apart. Those three tolerances compose: §3 could print +$961 and §4
    # ~$963 for the same EV-TOU-2 no-battery margin, in adjacent sections,
    # and every assertion above would pass. What the reader compares is the
    # two PRINTED figures, so they are compared here directly.
    #
    # $1.00, not $3.00: the generators' own tie-out allowance is a licence for
    # the two ARTIFACTS to differ, never a licence to print two different
    # numbers at one reader for one quantity (CLAUDE.md section 3). All that
    # is allowed between the two published figures is the dollar their two
    # roundings can each be out by. If a regeneration ever pushes the
    # artifacts far enough apart to break this, the remedy is to source both
    # sections from one artifact, not to widen the bound.
    s3_printed = {plan: float(cells[3].lstrip("+$").replace(",", ""))
                  for plan, cells in _s3_plan_table_rows() if cells[3] != "—"}
    agreed = []
    for plan, printed4 in sorted(s4_printed.items()):
        assert plan in s3_printed, (
            f"§4's conclusion quotes a margin for {plan}, which §3's table does not price "
            f"-- the two sections' margins cannot be reconciled")
        assert abs(s3_printed[plan] - printed4) <= 1.0, (
            f"§3 prints {plan}'s margin over {base_plan} as ${s3_printed[plan]:,.0f} and "
            f"§4 prints the same margin as ${printed4:,.0f}: two adjacent sections showing "
            f"one reader two different figures for one quantity")
        agreed.append(f"{plan} ${printed4:,.0f}")

    # §0's runner-up line, the same no-battery margin one section earlier.
    runner_up = min((p for p in bpm if p != base_plan),
                    key=lambda p: bpm[p]["no_battery"])
    s0 = HTML[HTML.index('<h2 id="s0">'):HTML.index('<h2 id="s1">')]
    margin0 = _fmt_usd(bpm[runner_up]["no_battery"] - bpm[base_plan]["no_battery"])
    assert margin0 in s0, (
        f"§0 does not quote the {margin0}/yr margin over the runner-up {runner_up}")

    return (f"§3's {checked} 'vs. current' cells, §4's conclusion ({', '.join(quoted)}) "
            f"and §0's {margin0} runner-up margin are all the margins their artifacts "
            f"price over {base_plan}, the two artifacts agree on the no-battery ones, and "
            f"§3 and §4 print the reader the same figure for each ({', '.join(agreed)})")


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
    # SKIP, not KeyError (issue #146). extended_findings.py publishes this
    # section as an explicit not_applicable stub when household.has_gas is
    # false, so a gasless checkout reaches here with no floor_therms_day to
    # index. Fourth case in this file gated on the same flag.
    if gd.get("not_applicable"):
        raise SkipCase("household.has_gas is false")

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


# ---------------------------------------------------------------------------
# THE ALWAYS-ON FLOOR, ONE ARTIFACT, THREE SECTIONS (issue #140).
#
# This report prices the household's overnight always-on load in three places
# -- the §0 bottom-line item, the §9 honesty note and the §13 decomposition --
# and the archive holds THREE artifacts that each carry a figure for it. Only
# data/quiet_night_floor.json can back a published one: extra_results.json's
# phantom key has no generator anywhere in this repo's history, and
# deep_results.json's is generated but states that load's energy and no price
# at all -- it used to price it at a hardcoded flat $0.20/kWh against an
# hour-weighted all-in import rate of about $0.375/kWh, and issue #172 deleted
# the field rather than reprice it. Both are labelled superseded workpapers in
# TECHNICAL.md §3.5/§3.11.
#
# The pair of cases below is what stops that split from reopening. The first
# pins §13's own figures to the artifact; the second pins the three sections to
# EACH OTHER through it, which is the property that actually failed before --
# every individual figure was traceable to some artifact, and the report still
# published two different costs for one load two sections apart.
#
# EVERY EXPECTED STRING IS BUILT FROM THE ARTIFACT. The version of this case
# that issue #140 replaced carried "44" inside its own locating regex, so the
# night count it claimed to check was the one thing it could not fail on.
# Nothing here is a literal from the data.
# ---------------------------------------------------------------------------
def _night_floor_artifact():
    path = ROOT / "data" / "quiet_night_floor.json"
    assert path.exists(), f"{path} is committed public data and must exist"
    return json.loads(path.read_text())


def _night_floor_published_figures():
    """The figures every section that prices this load must agree on, exactly
    as report_tokens.py's NIGHT_FLOOR_* formulas render them.

    The energy is floor_kw_priced x hours, not median_kw x hours: the priced
    floor is what both dollar figures were computed from (it is round(median,
    4) in the generator), so building the kWh off the other field would let
    the energy and the cost drift apart on a re-run."""
    doc = _night_floor_artifact()
    nf, pr = doc["night_floor"], doc["pricing"]
    return {
        "floor": f"{nf['median_kw']:,.2f} kW",
        "energy": f"{pr['floor_kw_priced'] * nf['nights_total'] * 24:,.0f} kWh/yr",
        "price map cost": f"${pr['method_a_price_map']['total_usd']:,.0f}",
        "re-bill cost": f"${pr['method_b_rebill']['total_usd']:,.0f}",
    }


def _report_span(what, start, end):
    """The document text from `start` up to `end`, or a named failure.

    Locating on the report's own headings means a renamed heading fails here
    rather than silently matching nothing -- a guard that cannot find its
    subject must say so, not pass."""
    m = re.search(start + r".*?(?=" + end + r")", HTML, re.S)
    assert m, f"{what} not found in index.html (looked for {start!r})"
    return m.group(0)


def case_night_floor_section_matches_the_artifact():
    """issue #112, re-based by issue #140: the §13 always-on-floor subsection
    is hand-written, not templated -- lock its sample, percentiles, cycling,
    seasonality and both pricings against data/quiet_night_floor.json."""
    doc = _night_floor_artifact()
    nf, pr = doc["night_floor"], doc["pricing"]
    section = _report_span("the §13 always-on-floor subsection",
                           r"<h3>Phantom baseload", r"<h3")
    monthly = {int(m): v for m, v in nf["monthly_median_kw"].items()}
    lo_m = min(monthly, key=lambda m: (monthly[m], m))
    hi_m = max(monthly, key=lambda m: (monthly[m], -m))
    checks = [
        # The sample AND the share it excludes: the floor is measured on the
        # quiet minority of nights, and the artifact's own selection_caveat
        # says to report the exclusion rate beside the figure rather than
        # treating the kept nights as the whole story.
        f"<b>{nf['quiet_nights']} of {nf['nights_total']} nights</b>",
        f"<b>{(1 - nf['quiet_nights'] / nf['nights_total']) * 100:.1f}%</b>",
        # A bare "{median} kW" check would still pass if the median were
        # swapped with one of the paragraph's own seasonal figures (also bare
        # "N kW"), so the median is joined to its own label and its own
        # percentile pair (issue #112 /review).
        f"median <b>{nf['median_kw']:,.2f} kW</b> ({nf['p10_kw']:,.2f} kW p10 to "
        f"{nf['p90_kw']:,.2f} kW p90)",
        f"within-night std {nf['cycling_within_night_std_kw_median']:,.2f} kW",
        f"{monthly[lo_m]:,.2f} kW in {calendar.month_name[lo_m]} against "
        f"{monthly[hi_m]:,.2f} kW in {calendar.month_name[hi_m]}",
        # Both pricings, and the gap between them: two live methods on one
        # load must be reconciled where they are published (CLAUDE.md §0).
        f"<b>${pr['method_a_price_map']['total_usd']:,.0f}/yr</b>",
        f"<b>${pr['method_b_rebill']['total_usd']:,.0f}/yr</b>",
        f"{abs(pr['reconciliation']['gap_pct']):.1f}% apart",
        # The qualifier the annual figures may not appear without, and the
        # direction of the error it introduces.
        "modeled, not measured",
        f"conservative by about ${pr['floor_assumption_violations']['usd_dropped_at_export_rate']:,.0f}",
        # What a household could act on, at the rate the sensitivity re-bill
        # measured rather than an opinion about how much is removable.
        f"about ${doc['sensitivity_per_100w']['usd_per_100w_at_current_floor']['value_usd']:,.0f}/yr "
        "for every 100 W removed",
    ]
    for value in checks:
        assert value in section, (
            f"§13 always-on-floor subsection: {value!r} not found in it")
    return ("the §13 always-on-floor subsection's sample, percentiles, cycling, "
            "seasonality, both pricings and their reconciliation match "
            "quiet_night_floor.json")


def case_all_three_sections_price_the_always_on_load_the_same_way():
    """issue #140, AC6. §0, §9 and §13 each state what the overnight always-on
    load is and what it costs. They must state the SAME figures, out of the
    same artifact -- the defect this replaces was not a wrong number in one
    place, it was three artifacts' worth of figures for one load spread across
    three sections, each individually traceable and collectively incoherent.

    Two halves, because either alone can pass while the split is open: every
    section carries the live figures, AND neither superseded workpaper's value
    survives anywhere in the document."""
    figures = _night_floor_published_figures()
    spans = {
        "§0": _report_span("the §0 always-on baseload item",
                           r"<li><b>Always-on baseload", r"</li>"),
        "§9": _report_span("the §9 always-on-load note",
                           r"<h3>Phantom / always-on load", r"</div>"),
        "§13": _report_span("the §13 always-on-floor subsection",
                            r"<h3>Phantom baseload", r"<h3"),
    }
    # The floor, its energy and its price-map cost are the three figures all
    # three sections state. The full re-bill is a §0/§13 reconciliation, so it
    # is required only where the two methods are set against each other.
    shared = ("floor", "energy", "price map cost")
    for sid, span in sorted(spans.items()):
        for name in shared:
            assert figures[name] in span, (
                f"{sid} does not state the {name} the artifact gives "
                f"({figures[name]!r}); one load may not carry different figures in "
                "different sections")
    for sid in ("§0", "§13"):
        assert figures["re-bill cost"] in spans[sid], (
            f"{sid} states one pricing of the load without the other "
            f"({figures['re-bill cost']!r}); two live methods are reconciled where they "
            "are published, not left to the reader (CLAUDE.md §0)")

    # The superseded workpapers, checked absent BY VALUE and read out of the
    # artifacts themselves, so the check tracks them rather than a literal.
    # A value that happens to equal a live one is skipped and named in the
    # summary: it cannot discriminate, and asserting it absent would fail on
    # the correct report.
    retired, indistinct = [], []
    xr = json.loads((ROOT / "data" / "extra_results.json").read_text())["phantom"]
    dp = json.loads((ROOT / "data" / "deep_results.json").read_text())["phantom"]
    for who, value in (("extra_results.json:phantom.annual_kwh_at_median",
                        f"{xr['annual_kwh_at_median']:,} kWh"),
                       ("extra_results.json:phantom.baseload_kw_median",
                        f"{xr['baseload_kw_median']:,} kW"),
                       ("deep_results.json:phantom.annual_kwh",
                        f"{dp['annual_kwh']:,} kWh"),
                       ("deep_results.json:phantom.baseload_kw",
                        f"{dp['baseload_kw']:,} kW")):
        if any(value in f for f in figures.values()) or any(
                f.startswith(value) for f in figures.values()):
            indistinct.append(who)
            continue
        retired.append((who, value))
    assert len(retired) >= 3, (
        "fewer than three superseded workpaper values are distinguishable from the live "
        f"ones, so this check can no longer tell them apart: {indistinct}")
    # deep_results.json:phantom's own COST is not on that list because issue
    # #172 removed it: it was the annual kWh times a hardcoded flat 0.20 $/kWh,
    # roughly half what rates.py implies for that energy, and it was deleted
    # rather than repriced so that one load keeps one published price. Checked
    # structurally instead -- a cost key back in that block is a fourth figure
    # for this load, whatever the report happens to say today.
    assert not [k for k in dp if re.search(r"cost|usd|price|blend", k, re.I)], (
        f"data/deep_results.json:phantom publishes a dollar figure again ({sorted(dp)}); "
        "this load is priced in data/quiet_night_floor.json, through rates.py, two ways")
    for who, value in retired:
        assert value not in HTML, (
            f"the report states {value!r}, which is {who} -- a superseded workpaper "
            "(TECHNICAL.md §3.5/§3.11), not the published figure for this load")
    return (f"§0, §9 and §13 all state the same {len(shared)} floor figures from "
            f"quiet_night_floor.json, §0 and §13 both reconcile the two pricings, and "
            f"{len(retired)} superseded workpaper values are absent from the report")


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


# The rate bracket the same paragraph closes on -- the figure SOILING_RATE_RANGE
# renders. Captured WITHOUT the leading "~", which is prose, not token output.
_SOILING_BRACKET_RE = re.compile(
    r"defensible range is the full bracket: <b>~([\d.]+–[\d.]+%/month)</b>")


def case_soiling_rate_bracket_matches_the_token_that_renders_it():
    """issue #137 AC3: §12 states the soiling rate bracket as a hand-written
    figure, and report_tokens.SOILING_RATE_RANGE derives the same bracket from
    data/soiling_results.json's two scenarios. They drifted: the token rendered
    both ends at one decimal, so scenario A's 0.449 %/month printed as "0.4"
    while index.html published 0.45 -- a lower soiling rate in the token than
    on the page, with nothing failing.

    This pins one against the other, so neither end can move alone. Rewriting
    the paragraph's bracket without regenerating the token fails here, and so
    does changing the token's precision or its scenario inputs without
    rewriting the page."""
    rt = _report_tokens()
    m = re.search(r"<p>An independent soiling study.*?</p>", HTML, re.S)
    assert m, "§12 soiling-range paragraph not found in index.html"
    para = m.group(0)
    b = _SOILING_BRACKET_RE.search(para)
    assert b, ("§12's 'defensible range is the full bracket: <b>~...%/month</b>' figure was "
               "not found in the soiling paragraph -- that bracket is what this case pins "
               "SOILING_RATE_RANGE to, so it cannot be checked at all if the page stops "
               "publishing it")
    published = b.group(1)
    rendered = rt.resolve_token("SOILING_RATE_RANGE")
    assert rendered == published, (
        f"§12 publishes a soiling rate bracket of ~{published}, but "
        f"SOILING_RATE_RANGE renders {rendered!r} from data/soiling_results.json's own "
        "scenario rates -- the page and the token that is supposed to fill it state "
        "different rates of soiling")
    return (f"§12's soiling rate bracket (~{published}) is the value SOILING_RATE_RANGE "
            "renders from soiling_results.json's two scenario rates")


# The §12 cleaning heading, and the two figures it can be filled from.
_CLEANING_HEADING_RE = re.compile(
    r"<h3>The [^<]*?cleaning \([^)]*\): ([-+]?[\d.]+%) production gain")
# The section's own conclusion, three lines below that heading.
_CLEANING_DIFF_IN_DIFF_RE = re.compile(r"Difference-in-differences: <b>([-+]?[\d.]+%)</b>")
# The cleaned year's row in the per-year windows table -- the OTHER statistic,
# each year's own raw post ÷ pre ratio.
_CLEANING_RAW_RATIO_RE = re.compile(
    r'<tr class="win"><td>\d{4} — cleaned [^<]*</td>(?:<td>[^<]*</td>){2}'
    r"<td><b>([\d.]+)</b></td>")


def case_cleaning_effect_heading_matches_the_sections_own_conclusion():
    """issue #138: §12's h3 states the cleaning's effect and the paragraph
    below the windows table concludes with the difference-in-differences
    estimate against the control years. They are the same claim, so they must
    be the same figure -- and CLEANING_EFFECT_PCT, the token that fills that
    heading, must BE it.

    It was not. The token computed the cleaned year's naive post/pre window
    ratio (+5.1%), the statistic the windows table publishes per year as
    CLEANED_RATIO, so a mechanical fill printed one figure directly above a
    paragraph stating the other. Both statistics are legitimate and both are
    kept; what is pinned here is which one heads the section.

    The token half resolves through report_tokens (defined below, called at
    run time), and the ONLY survivable failure is a checkout without the
    private archive -- exactly as in the heading-verdict agreement case."""
    sr_path = ROOT / "data" / "soiling_results.json"
    assert sr_path.exists(), f"{sr_path} is committed public data and must exist"
    sc = json.loads(sr_path.read_text())["sanity_check_2024_cleaning"]
    expected = f"{sc['known_cleaning_gain_pct']:+.1f}%"

    m = _CLEANING_HEADING_RE.search(HTML)
    assert m, "§12's cleaning h3 not found in index.html"
    heading = m.group(1)
    assert heading == expected, (
        f"§12's cleaning heading states {heading!r}, but soiling_results.json's "
        f"sanity_check_2024_cleaning.known_cleaning_gain_pct is {expected!r}")

    m = _CLEANING_DIFF_IN_DIFF_RE.search(HTML)
    assert m, "§12's difference-in-differences conclusion not found in index.html"
    assert m.group(1) == expected, (
        f"§12 concludes with a difference-in-differences of {m.group(1)!r} while its "
        f"own heading states {heading!r} -- the heading and the conclusion it heads "
        "state one figure")

    # The statistic the heading must NOT be: the cleaned year's own raw
    # post ÷ pre ratio, published one table up. It counts the seasonal decline
    # every control year also shows, so it reads several points lower.
    m = _CLEANING_RAW_RATIO_RE.search(HTML)
    assert m, "the cleaned year's row in §12's windows table not found in index.html"
    raw = f"{(float(m.group(1)) - 1) * 100:+.1f}%"
    assert heading != raw, (
        f"§12's heading states {heading!r}, which is the windows table's raw post ÷ pre "
        "ratio for the cleaned year, not the difference-in-differences estimate the "
        "section concludes with")

    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import household
    archive, loader = household.PATH.is_file(), household.__file__
    try:
        import report_tokens as rt
        resolved = rt.resolve_token("CLEANING_EFFECT_PCT")
    except SystemExit as e:                       # pragma: no cover - archive-dependent
        assert _missing_archive_exit(e, archive, loader), (
            f"CLEANING_EFFECT_PCT could not be resolved, and NOT because this checkout "
            f"lacks the private archive (present: {archive}): {e}")
        return ("§12's cleaning heading and its difference-in-differences conclusion "
                f"both state {expected}, the artifact's own gain, and neither is the "
                f"raw window ratio {raw}; CLEANING_EFFECT_PCT not compared ({e})")
    assert resolved == expected, (
        f"CLEANING_EFFECT_PCT resolves to {resolved!r}, but the §12 heading it fills "
        f"states {heading!r} and the section concludes with the same figure -- the "
        f"token must state the artifact's difference-in-differences gain {expected!r}"
        + (f", not the raw window ratio {raw!r}" if resolved == raw else ""))
    return ("§12's cleaning heading, its difference-in-differences conclusion and "
            f"CLEANING_EFFECT_PCT all state {expected} (soiling_results.json's "
            f"known_cleaning_gain_pct), distinct from the windows table's raw "
            f"post ÷ pre ratio {raw}")


_EXAMPLE_HOUSEHOLD = ROOT / "household.example.yaml"


def _report_tokens():
    """report_tokens, importable with no private archive.

    The cases below never read private/household.yaml -- they stand a synthetic
    cleaning history in for it (see _cleaning_history) -- so unlike the
    agreement cases above there is no archive-absent outcome to pardon here."""
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import report_tokens as rt
    return rt


class _cleaning_history:
    """The committed household.example.yaml standing in for private/
    household.yaml, with ONE cleaning_history written into it.

    §12's cleaning claim spans two files -- the dated event comes from
    cleaning_history, the measured gain from data/soiling_results.json -- and
    the defect these cases pin lives in how the two are matched. Driving it
    from the REAL history proves nothing: this household happens to record the
    measured cleaning first, so first-entry and measured-entry are the same
    record and every wrong rule passes. So the history is synthetic, the
    artifact is the committed one, and nothing is written to disk.

    Restores report_tokens' view of the household on the way out, including
    when the body raises -- the cases after these read the real archive."""

    def __init__(self, rt, entries):
        self.rt, self.entries = rt, entries

    def __enter__(self):
        import yaml
        node = yaml.safe_load(_EXAMPLE_HOUSEHOLD.read_text())
        node["cleaning_history"] = [dict(e) for e in self.entries]
        self.old_cache, self.old_path = self.rt.hh._cache, self.rt.hh.PATH
        self.rt.hh._cache, self.rt.hh.PATH = node, _EXAMPLE_HOUSEHOLD
        return node

    def __exit__(self, *exc):
        self.rt.hh._cache, self.rt.hh.PATH = self.old_cache, self.old_path
        return False


def _measured_cleaning_date():
    """The date soiling_analysis.py says it measured the gain for -- read off
    the artifact, never written here as a literal."""
    sc = json.loads((ROOT / "data" / "soiling_results.json").read_text())[
        "sanity_check_2024_cleaning"]
    return dt.date.fromisoformat(sc["cleaning_date"]), sc["known_cleaning_gain_pct"]


def _study_days():
    with open(ROOT / "data" / "cleaning_study_daily.csv", newline="") as f:
        return sorted((dt.datetime.strptime(r["date"], "%Y%m%d").date(),
                       float(r["generated_kwh"])) for r in csv.DictReader(f))


def _other_cleaning_date():
    """A date that is NOT the measured one but still has a 30-day window on
    both sides inside the cleaning study -- so a household whose history holds
    only this one still renders the windows table, and the only thing the
    mismatch can take away is the gain."""
    days = [d for d, _kwh in _study_days()]
    other = days[len(days) // 2]
    measured, _gain = _measured_cleaning_date()
    assert other != measured, (
        "the cleaning study's median day IS the measured cleaning date; pick another "
        "stand-in date for the non-matching cases")
    return other


def _window_ratio(clean_date):
    """The raw post ÷ pre median ratio around one date, recomputed here rather
    than through report_tokens' own helper."""
    parsed = _study_days()
    pre = sorted(kwh for d, kwh in parsed
                 if clean_date - dt.timedelta(days=30) <= d < clean_date)
    post = sorted(kwh for d, kwh in parsed
                  if clean_date < d <= clean_date + dt.timedelta(days=30))
    assert pre and post, f"the cleaning study does not bracket {clean_date}"

    def median(xs):
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    return median(pre), median(post)


def _resolution_failures(rt):
    """{token: what went wrong} for every non-gap token that will not render.

    Catches BaseException, not SystemExit: the point of the no-match case is
    that NOTHING escapes, and a token family that started raising KeyError or
    ValueError past resolve_token's own conversion would satisfy a
    SystemExit-only check while still taking the report down."""
    out = {}
    for name, spec in rt.TOKENS.items():
        if spec.get("kind") == "gap":
            continue
        try:
            rt.resolve_token(name, spec)
        except BaseException as e:                # noqa: BLE001 - that is the assertion
            out[name] = f"{type(e).__name__}: {e}"
    return out


def case_cleaning_heading_and_gain_describe_the_same_event():
    """issue #138: the §12 h3 names a cleaning ({{CLEANING_DATE}},
    {{CLEANING_PRICE}}) and states a measured gain ({{CLEANING_EFFECT_PCT}}) in
    one sentence, so both halves must come off the SAME record.

    The gain is soiling_analysis.py's difference-in-differences estimate for
    one dated event -- that module pins its own block to that record and says
    the figure "must never be attributed to any other event". The heading's
    date and price used to come off cleaning_history's FIRST entry instead,
    which the intake file guarantees nothing about. A household that records
    its cleanings newest-first, or has more than one, published a heading
    naming one cleaning and measuring another.

    Driven with the measured cleaning SECOND in the list, where a first-entry
    rule and a matched-entry rule disagree."""
    rt = _report_tokens()
    measured, gain = _measured_cleaning_date()
    other = _other_cleaning_date()
    entries = [{"date": other, "cost_usd": 90},
               {"date": measured, "cost_usd": 250},
               {"date": measured + dt.timedelta(days=400), "cost_usd": 310}]
    with _cleaning_history(rt, entries):
        rendered = {t: rt.resolve_token(t) for t in
                    ("CLEANING_EFFECT_PCT", "CLEANING_DATE", "CLEANING_DATE_SHORT",
                     "CLEANING_YEAR", "CLEANING_PRICE", "CLEANED_PRE_MEDIAN",
                     "CLEANED_POST_MEDIAN", "CLEANED_RATIO")}

    assert rendered["CLEANING_EFFECT_PCT"] == f"{gain:+.1f}%", (
        f"CLEANING_EFFECT_PCT rendered {rendered['CLEANING_EFFECT_PCT']!r}, not the "
        f"artifact's measured gain {gain:+.1f}%")
    expected = {
        "CLEANING_DATE": f"{calendar.month_name[measured.month]} {measured.day}, "
                         f"{measured.year}",
        "CLEANING_DATE_SHORT": f"{_MONTH_ABBR[measured.month]} {measured.day}",
        "CLEANING_YEAR": str(measured.year),
        "CLEANING_PRICE": "$250",
    }
    for token, want in expected.items():
        assert rendered[token] == want, (
            f"§12's heading states {rendered['CLEANING_EFFECT_PCT']}, the gain measured "
            f"for the {measured} cleaning, but {token} rendered {rendered[token]!r} "
            f"instead of {want!r} -- the heading names one cleaning and states another "
            "one's figure")

    # The windows table under that heading is the same event's evidence, so it
    # moves with it: its medians must bracket the measured date, not entry #1's.
    pre, post = _window_ratio(measured)
    assert (rendered["CLEANED_PRE_MEDIAN"], rendered["CLEANED_POST_MEDIAN"]) == \
        (f"{pre:,.1f}", f"{post:,.1f}"), (
            f"§12's windows row rendered {rendered['CLEANED_PRE_MEDIAN']}/"
            f"{rendered['CLEANED_POST_MEDIAN']} kWh/day, but the 30-day medians around "
            f"the {measured} cleaning are {pre:,.1f}/{post:,.1f}")
    assert rendered["CLEANED_RATIO"] == f"{round(post / pre, 3):,.2f}", (
        f"CLEANED_RATIO rendered {rendered['CLEANED_RATIO']!r} against a hand-computed "
        f"{round(post / pre, 3)} around the measured cleaning")
    return (f"with the measured cleaning second in cleaning_history, §12's heading names "
            f"{rendered['CLEANING_DATE']} ({rendered['CLEANING_PRICE']}) and states "
            f"{rendered['CLEANING_EFFECT_PCT']} -- one event")


def case_cleaning_gain_is_not_determined_when_no_entry_matches():
    """issue #138: a household whose cleaning_history does not contain the
    measured event gets NO gain -- and still gets a report.

    Both halves matter. Stating the figure beside a cleaning it was not
    measured on is the misattribution above; refusing the whole run is the
    failure mode CLAUDE.md and this repo's own history warn about twice over --
    an ordinary household, whose cleanings simply differ from this one's,
    losing every other section over one clause. So this asserts the token says
    "not determined" with its reason, and that NOTHING ELSE stops resolving:
    the set of tokens that fail is compared against the same set with the
    measured cleaning present, so an unrelated placeholder in
    household.example.yaml cannot pass the case either."""
    rt = _report_tokens()
    measured, gain = _measured_cleaning_date()
    other = _other_cleaning_date()
    with _cleaning_history(rt, [{"date": measured, "cost_usd": 250}]):
        baseline = _resolution_failures(rt)
        matched = rt.resolve_token("CLEANING_EFFECT_PCT")
    with _cleaning_history(rt, [{"date": other, "cost_usd": 90}]):
        variant = _resolution_failures(rt)
        try:
            unmatched = rt.resolve_token("CLEANING_EFFECT_PCT")
        except BaseException as e:                # noqa: BLE001 - that is the assertion
            raise AssertionError(
                f"a cleaning_history without the measured {measured} cleaning made "
                f"CLEANING_EFFECT_PCT raise {type(e).__name__}: {e} -- a household whose "
                "cleanings differ from this one's must still be able to generate a "
                "report")

    assert matched == f"{gain:+.1f}%", (
        f"the control run (measured cleaning present) rendered {matched!r}, so this case "
        "is not comparing what it claims to")
    assert unmatched.lower().startswith("not determined"), (
        f"CLEANING_EFFECT_PCT rendered {unmatched!r} for a household that never had the "
        f"{measured} cleaning -- that figure was measured for that event alone")
    assert str(measured) in unmatched, (
        f"CLEANING_EFFECT_PCT's refusal {unmatched!r} does not name the date the artifact "
        "measured, so a reader cannot tell what is missing")
    assert f"{gain:+.1f}" not in unmatched and f"{gain}" not in unmatched, (
        f"CLEANING_EFFECT_PCT's refusal {unmatched!r} still carries the {gain}% figure")
    assert variant == baseline, (
        "swapping cleaning_history to a household without the measured cleaning changed "
        "which tokens resolve at all: "
        + "; ".join(f"{t}: {variant.get(t, 'now resolves')}"
                    for t in sorted(set(variant) ^ set(baseline))))
    return (f"a history without the {measured} cleaning renders "
            f"{unmatched.split(' — ')[0]!r} for the gain and leaves the other "
            f"{len(rt.TOKENS) - len(variant)} resolvable tokens untouched")


def case_cleaning_gain_follows_the_artifacts_own_not_determined_status():
    """issue #138: soiling_analysis.py writes a `status` block INSTEAD of a
    gain for a household it could not measure one for, and the token reports
    that block's own reason rather than reaching past it for a figure that is
    not there.

    This is the shape every household that has never had a measured cleaning
    gets, so it is the one that decides whether they can publish a report at
    all -- and the reason belongs to the generator that made the call, not to a
    paraphrase written on this side.

    Issue #170 AC3: and CLEANING_EFFECT_PCT is not the only token that reads
    that block. Seven others in §12 reach into sanity_check_2024_cleaning for a
    figure the status block does not carry, and checking one token would let
    any of them start raising unnoticed -- the whole report, not one heading,
    is what a household in this shape loses. So this sweeps the FULL token set
    the way its no-match sibling does: the set of tokens that fail with the
    status block in place is compared against the same set with the real
    artifact, and the run with the real artifact is the positive control that
    the instrument reports a gain when there is one to report."""
    rt = _report_tokens()
    measured, gain = _measured_cleaning_date()
    status = ("not determined — cleaning_history has no entry for 2024-08-12, the only "
              "event with a measured diff-in-diff effect")
    real = rt._json("soiling_results.json")
    stubbed = dict(real, sanity_check_2024_cleaning={"status": status})
    try:
        with _cleaning_history(rt, [{"date": measured, "cost_usd": 250}]):
            baseline = _resolution_failures(rt)
            measured_gain = rt.resolve_token("CLEANING_EFFECT_PCT")
            rt._json_cache["soiling_results.json"] = stubbed
            try:
                rendered = rt.resolve_token("CLEANING_EFFECT_PCT")
            except BaseException as e:            # noqa: BLE001 - that is the assertion
                raise AssertionError(
                    f"an artifact carrying soiling_analysis.py's own not-determined "
                    f"status made CLEANING_EFFECT_PCT raise {type(e).__name__}: {e} -- "
                    "that block is the ordinary outcome for a household with no measured "
                    "cleaning, not a broken artifact")
            variant = _resolution_failures(rt)
    finally:
        rt._json_cache["soiling_results.json"] = real
    assert measured_gain == f"{gain:+.1f}%", (
        f"the control run (the real artifact, with its measured gain) rendered "
        f"{measured_gain!r}, so this case is not comparing what it claims to")
    assert rendered == status, (
        f"CLEANING_EFFECT_PCT rendered {rendered!r} instead of the artifact's own "
        f"{status!r} -- soiling_analysis.py owns the reason it measured nothing")
    differing = sorted(set(variant) ^ set(baseline)) or sorted(
        t for t in variant if variant[t] != baseline.get(t))
    assert variant == baseline, (
        "swapping soiling_results.json for one carrying soiling_analysis.py's own "
        "not-determined status changed which tokens resolve at all: "
        + "; ".join(f"{t}: {variant.get(t, 'now resolves')}" for t in differing))
    return ("an artifact carrying soiling_analysis.py's not-determined status renders "
            "that status through CLEANING_EFFECT_PCT, unparaphrased, and leaves the other "
            f"{len(rt.TOKENS) - len(variant)} resolvable tokens untouched")


def case_cleaning_gain_is_not_determined_when_two_entries_share_the_date():
    """issue #138: two cleanings recorded on the measured date is an ambiguity,
    and picking one of them silently would republish the same misattribution
    with a coin toss behind it -- the prices differ, and nothing in either file
    says which record the study measured."""
    rt = _report_tokens()
    measured, gain = _measured_cleaning_date()
    entries = [{"date": measured, "cost_usd": 90}, {"date": measured, "cost_usd": 250}]
    with _cleaning_history(rt, entries):
        try:
            rendered = rt.resolve_token("CLEANING_EFFECT_PCT")
        except BaseException as e:                # noqa: BLE001 - that is the assertion
            raise AssertionError(
                f"two cleanings on {measured} made CLEANING_EFFECT_PCT raise "
                f"{type(e).__name__}: {e} -- a duplicated intake entry must not cost the "
                "household its report")
    assert rendered.lower().startswith("not determined"), (
        f"CLEANING_EFFECT_PCT rendered {rendered!r} with two cleaning_history entries on "
        f"{measured} -- it cannot know which of them the study measured")
    assert f"{gain:+.1f}" not in rendered and f"{gain}" not in rendered, (
        f"CLEANING_EFFECT_PCT's refusal {rendered!r} still carries the {gain}% figure")
    assert "2 cleanings" in rendered and str(measured) in rendered, (
        f"CLEANING_EFFECT_PCT's refusal {rendered!r} does not say what is ambiguous")
    return (f"two cleaning_history entries on {measured} render "
            f"{rendered.split(',')[0]!r} rather than one of the two at random")


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
# Issue #143: two hand-written paragraphs -- §2's "key architectural fact" line
# and §8's "more panels" verdict -- state WHEN this array's exports leave and
# WHEN the EV draws. Both timing figures are derived here from the artifacts
# that measure them, and each paragraph's export-share figure is held to its
# own referent, since the two are different quantities that round near each
# other and have been confused for one another.
#
# PROVENANCE OF EVERY FIELD PAIRED BELOW, read out of the generator that writes
# both sides before pairing them (CLAUDE.md section 8's guard rule). Which of
# the four relationships each pair is:
#
#   report_data.json hourly_S/hourly_W.exp  vs  report_data.json totals.exp --
#   ONE QUANTITY, ONE PARTITIONED FROM THE OTHER. analysis/report_data.py's
#   build() computes both from the same frame in the same call: totals.exp is
#   `d.Generation.sum()`, and hourly_{s}.exp is that same Generation summed by
#   (season, hour-of-day) and divided by that season's own distinct-date count
#   (`sub.dt.dt.date.nunique()`). Re-multiplying by those day counts and summing
#   therefore MUST return totals.exp. The check below is an arithmetic
#   self-check on the season day counts THIS file derives -- not corroboration
#   of the midday share -- and its residual is the 3-decimal rounding build()
#   applies to each hourly mean.
#
#   data/hourly_profile.csv  vs  report_data.json's hourly arrays -- THE SAME
#   QUANTITY, INDEPENDENTLY COMPUTED. analysis/analyze_norelief.py writes
#   hourly_profile.csv from the same raw export by a different route: a plain
#   hour-of-day mean over every interval, with no season split and no day-count
#   division at all. It is the real corroboration of the midday share, and it
#   needs none of the season bookkeeping above.
#
#   What it does need is the BUCKET SIZES. Those cells are per-interval MEANS
#   (analyze_norelief.py line 185: `d.groupby(d.dt.dt.hour).agg(...,
#   exp=("Generation","mean"), ...)`), so an hour's share of the year's exports
#   is its mean times the number of 15-minute intervals its bucket holds -- and
#   the buckets are NOT all the same size once a window spans a DST Sunday.
#   rates.expected_day_hours() is explicit about it: 96 slots ordinarily, 92 on
#   the spring-forward Sunday (no 02:00-02:45), 100 on the fall-back Sunday
#   (01:00-01:45 twice). Over this window that makes hour 1 four intervals
#   larger and hour 2 four smaller than every other hour, so summing the means
#   as if they were commensurate does not reconstruct the annual distribution.
#   The share below is weighted by those counts, taken from
#   expected_day_hours() so this file cannot disagree with the tariff clock.
#   MEASURED on the committed data the correction is worth 0.000000 pp,
#   because exports at 01:00 and 02:00 are identically 0.000 -- the two hours
#   a DST Sunday resizes are the two the sun is down for. The weighting is
#   here so that a regeneration whose profile carries any nonzero night export
#   (a battery exporting overnight, a fork's meter, a re-based window) is
#   scored by arithmetic that is right rather than by one that happened to be
#   harmless, and so that a wrong route can never reject a correct report.
#
#   report_data.json totals.exp  vs  enphase_daily_production.csv's Total
#   footer -- TWO INSTRUMENTS MEASURING DIFFERENT QUANTITIES, combined into one
#   ratio: utility-meter exports over inverter-platform production. That ratio
#   is the export share both paragraphs state, built here from the same two
#   fields report_tokens.py's EXPORTED_SHARE builds it from. Neither side is
#   derived from, clamped to, or tuned against the other.
#
#   quiet_night_floor.json's ev_absence_by_window  vs  behavior_rebuild.json's
#   window -- SAME WINDOW, SAME FRAME: quiet_night_floor.py's main() loads
#   through `behavior_rebuild.load()`, so its eligible-night count and
#   behavior_rebuild.json's window.days count the same days. Inside that
#   artifact, issue_114_investigation() classifies each night with
#   `behavior_rebuild.detect_sessions()` and counts the nights holding ZERO
#   detected EV kWh in the window -- so `n` is the ABSENCE count and the paired
#   `n_eligible_nights` is that window's own denominator (365 here, but 364 for
#   the wrapped 21-6h window). Nights WITH charging is the difference, and the
#   two fields may only ever be read as a pair.
#
# NOT behavior_rebuild.json's detection.ev_kwh_sop_already, which is the field
# one reaches for first when asking when the EV charges. Its bucket is the
# whole super-off-peak PERIOD, which on EV-TOU-5 is the overnight run and the
# 10am-2pm midday run at once, so a 3am charge and a noon charge are
# indistinguishable inside it. It cannot answer the question and is
# deliberately not read here. report_tokens.py's own
# _overnight_ev_night_counts() carries the same warning for the same reason.
#
# WHO OWNS THESE TWO FIGURES. report_tokens.py's _midday_export_share() and
# _overnight_ev_night_counts() are the canonical derivations -- they feed
# S2_VERDICT, the token-rendered conclusion line §2 opens with, and they take
# their windows off the tariff (rates.period()) instead of naming hours. The
# two paragraphs pinned below are hand-written prose restating those same two
# measurements, so each case checks them BOTH ways: against the artifacts,
# recomputed here rather than by calling that module (the convention this file
# already follows in _expected_month_labels, so that a bug in the generator
# fails a case instead of being reproduced by it), and against the figure
# §2's own verdict line publishes. That second comparison is SAME QUANTITY,
# ONE COMPUTED AND ONE TYPED: the verdict line's share is
# _midday_export_share()'s output rendered into the page, the paragraphs' is a
# human copy of it, which is exactly why they can drift apart.
# ---------------------------------------------------------------------------
# THE MIDDAY WINDOW COMES OFF THE TARIFF, NOT OFF THIS HOUSEHOLD. The share is
# taken over the daytime super-off-peak run rates.period() itself defines,
# recomputed here from rates.period() rather than imported from
# report_tokens._cheap_run() -- the same recompute convention
# _expected_month_labels follows, so a bug in the generator's window logic
# fails a case instead of being reproduced by it.
#
# The prose's stated window ("10am-2pm") is then DERIVED from those hours
# (_MIDDAY_WINDOW_WORDS, below) and required to appear in the paragraphs. That
# is the pin the hard-coded range(10, 14) used to make -- the words on the page
# must name the window the figure was taken over -- except that it now adapts
# to the tariff instead of asserting this household's.
#
# FORK NOTE: the hours and the window words above follow your tariff on their
# own. Three things in this block do NOT, and are pinned to THIS report's
# dataset and wording. When reproducing the analysis for your own house:
#
#   * _EV_NIGHT_WINDOW is a KEY into data/quiet_night_floor.json's own
#     classification buckets, not a window that can be derived from anything --
#     pick the bucket your overnight prose actually names.
#   * the English substrings case_s0/case_s2/case_s8 check ("... go out in the
#     10am-2pm window", "the EV charged between midnight and 6am on N of the
#     year's M nights") are these sentences, not any household's. Rewrite them
#     to match your own prose; the figure pinned to an artifact is the point,
#     the sentence around it is not.
#   * _TIME_OF_DAY_WORDS is an English blocklist. A different window needs no
#     edit there; a different language needs a new list.
#
# A fork that skips these gets a failure naming a missing SENTENCE, which reads
# like a report defect and is a fork-adaptation step. That is the same trap the
# retired hard-coded window set, and the same reason this note exists.
# ---------------------------------------------------------------------------


def _weekday_tou_runs():
    """Every consecutive TOU run in a weekday as (start hour, end hour, label),
    sampled off rates.period() every 15 minutes -- the finest grid any window
    in this tariff moves on."""
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import rates as R
    slots = [R.period(i / 4, is_weekend=False) for i in range(96)]
    runs, start = [], 0
    for i in range(1, 97):
        if i == 96 or slots[i] != slots[start]:
            runs.append((start / 4, i / 4, slots[start]))
            start = i
    return runs


def _midday_export_hours():
    """The hour-of-day buckets the midday export share is taken over: the
    tariff's own DAYTIME super-off-peak run. The overnight super-off-peak run
    opens at midnight and is a different window -- that is the whole distinction
    the two paragraphs turn on, so it is made structurally here rather than by
    naming hours."""
    daytime = [r for r in _weekday_tou_runs() if r[2] == "sop" and r[0] > 0]
    assert len(daytime) == 1, (
        f"rates.period() gives a weekday {len(daytime)} daytime super-off-peak runs "
        f"({daytime}), so 'the midday window' is not one window on this tariff -- the "
        f"share these cases pin cannot be taken without first saying which run it means")
    lo, hi, _lab = daytime[0]
    assert lo == int(lo) and hi == int(hi), (
        f"the daytime super-off-peak run is {lo}-{hi}h, but data/hourly_profile.csv and "
        f"data/report_data.json carry WHOLE-HOUR buckets -- a share taken over them "
        f"would silently round the window and publish the result as this tariff's")
    return range(int(lo), int(hi))


_MIDDAY_EXPORT_HOURS = _midday_export_hours()
_EV_NIGHT_WINDOW = "0-6h"              # midnight-6am, the window they name


def _clock(hour):
    """`hour` written on the 12-hour clock the report states windows in."""
    return f"{hour % 12 or 12}{'am' if hour < 12 else 'pm'}"


# The WINDOW the measurement was actually taken over, in the words the
# paragraphs use. Only this names the midday figure's own provenance, so it is
# what the positive half of the guard demands -- "midday" alone would let the
# paragraph gesture at a time of day instead of stating the window the share
# was computed over.
#
# BUILT from _MIDDAY_EXPORT_HOURS rather than typed, so a fork whose daytime
# super-off-peak run is not 10-14 asks its own prose for its own window. Both
# dashes, because the report writes the en dash and a plain hyphen is the
# likelier typo, not a different claim.
_MIDDAY_WINDOW_WORDS = tuple(
    f"{_clock(_MIDDAY_EXPORT_HOURS[0])}{dash}{_clock(_MIDDAY_EXPORT_HOURS[-1] + 1)}"
    for dash in ("–", "-"))

# report_tokens._EXPORT_REBUILD_TOLERANCE's bound, and its reasoning: 3-decimal
# hourly profiles against whole-kWh totals can move the reconstruction ~0.05%
# on rounding alone, so the band is a publication-rounding allowance rather
# than a drift budget. Restating it here rather than tightening it -- a
# stricter private bound would fail a regeneration this repo's own generator
# considers sound.
_EXPORT_REBUILD_TOLERANCE = 0.01


def _dates_between(start, end):
    """Every calendar day in [start, end], both ends included."""
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def _analysis_window_dates():
    """Every calendar day in the analysis window.

    behavior_rebuild.json's `window` is where this file already reads the
    window from, and the sibling season-day derivation below reads it from the
    same place -- one source of truth, so the two cannot disagree about which
    year is being described."""
    window = BEHAVIOR["window"]
    return _dates_between(dt.date.fromisoformat(window["start"].split(" ")[0]),
                          dt.date.fromisoformat(window["end"].split(" ")[0]))


def _intervals_per_hour_of_day(days):
    """How many 15-minute intervals each hour-of-day bucket holds over `days`.

    Counted through rates.expected_day_hours(), which is the repo's canonical
    answer to "what slots does this calendar day carry" and the only place the
    two DST Sundays are written down. Deriving the weights from it rather than
    from `4 * len(days)` is what keeps this file from having its own opinion
    about the tariff clock."""
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import rates as R
    counts = {}
    for day in days:
        for slot in R.expected_day_hours(day):
            counts[int(slot)] = counts.get(int(slot), 0) + 1
    return counts


def _midday_share_of(by_hour, counts):
    """The 10am-2pm share of an hour-of-day MEAN profile.

    Each hour's mean is multiplied by the number of intervals its bucket
    actually holds before the share is taken -- see the provenance block above
    for why the buckets differ and by how much."""
    missing = sorted(set(by_hour) - set(counts))
    assert not missing, (
        f"hours {missing} have a profile value but no interval count -- the "
        f"profile and the analysis window describe different days")
    weighted = {h: by_hour[h] * counts[h] for h in by_hour}
    return (sum(weighted[h] for h in _MIDDAY_EXPORT_HOURS)
            / sum(weighted.values()))


def _midday_export_share_from_hourly_profile():
    """The 10am-2pm share of exports, straight off data/hourly_profile.csv.

    The file stores one per-interval MEAN per hour-of-day, and the hours do
    NOT all carry the same number of 15-minute intervals once the window spans
    a DST Sunday, so each mean is weighted by its own bucket's interval count
    over behavior_rebuild.json's window before the share is taken."""
    with (ROOT / "data" / "hourly_profile.csv").open() as fh:
        by_hour = {int(r["dt"]): float(r["exp"]) for r in csv.DictReader(fh)}
    assert set(by_hour) == set(range(24)), (
        f"data/hourly_profile.csv covers hours {sorted(by_hour)}, not all 24 -- "
        "an hour-of-day share cannot be taken from it")
    return _midday_share_of(by_hour, _intervals_per_hour_of_day(
        _analysis_window_dates()))


def _season_day_counts():
    """(summer days, winter days) in the analysis window -- the divisors
    report_data.py's build() used on its hourly means, recovered from
    behavior_rebuild.json's window and the tariff's own season months."""
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import rates as R          # SUMMER_MONTHS lives there and is never re-listed
    days = _analysis_window_dates()
    summer = sum(1 for d in days if d.month in R.SUMMER_MONTHS)
    return summer, len(days) - summer


def _midday_export_share_from_report_data():
    """The same share, rebuilt from report_data.json's two seasonal hour-of-day
    arrays weighted by their own season day counts.

    Returns (share, rebuild error vs totals.exp). The error is the self-check
    described in the provenance block above: the arrays are a partition of
    totals.exp, so a wrong season day count shows up here as a rebuilt total
    that misses the artifact's own, and the share it produced cannot be
    trusted."""
    n_summer, n_winter = _season_day_counts()
    weighted = {"S": (RD["hourly_S"]["exp"], n_summer),
                "W": (RD["hourly_W"]["exp"], n_winter)}
    total = sum(sum(exp) * n for exp, n in weighted.values())
    midday = sum(sum(exp[h] for h in _MIDDAY_EXPORT_HOURS) * n
                 for exp, n in weighted.values())
    return midday / total, abs(total - RD["totals"]["exp"]) / RD["totals"]["exp"]


# The span between "In one sentence: " and the percentage is read TAG AND ALL,
# stopping only at the paragraph's own close and never at a newline. A tag-free
# span ([^<]*?) was the first draft, and it made the locator a formatting
# detector: a <b>, an <a href="#s8"> or a <span class="pill"> written ahead of
# the figure -- all routine in this report -- broke the search, and the case
# then reported "§2's verdict line no longer states a 10am-2pm share of
# exports", which is a deleted CLAIM, not a rearranged one. The window words
# come from _MIDDAY_WINDOW_WORDS for the same reason the hours do.
_S2_VERDICT_MIDDAY_RE = re.compile(
    r'<p class="verdict">In one sentence: (?:(?!</p>)[^\n])*?(\d+)% of its exports '
    r"leave in the " + re.escape(_MIDDAY_WINDOW_WORDS[0]) + r" window")


def _midday_export_share_pct():
    """The published integer percentage, with every route to it required to
    agree: two artifacts written by different generators, and the figure §2's
    own token-rendered verdict line already states."""
    from_profile = _midday_export_share_from_hourly_profile()
    from_report_data, rebuild_err = _midday_export_share_from_report_data()
    assert rebuild_err < _EXPORT_REBUILD_TOLERANCE, (
        f"report_data.json's seasonal hour-of-day exports rebuild to a total "
        f"{rebuild_err * 100:.4f}% off its own totals.exp, past the "
        f"{_EXPORT_REBUILD_TOLERANCE:.0%} rebuild bound -- the profiles do not "
        f"describe this window, or the season day counts derived here are not the "
        f"ones report_data.py divided by")
    assert round(from_profile * 100) == round(from_report_data * 100), (
        f"the two artifacts disagree on the 10am-2pm share of exports: "
        f"hourly_profile.csv says {from_profile * 100:.2f}%, report_data.json's "
        f"season-weighted hours say {from_report_data * 100:.2f}%")
    pct = round(from_profile * 100)

    m = _S2_VERDICT_MIDDAY_RE.search(HTML)
    assert m, ("§2's verdict line no longer states a 10am–2pm share of exports -- "
               "it is the token-rendered original of the figure the two hand-written "
               "paragraphs restate, and nothing pins them without it")
    assert int(m.group(1)) == pct, (
        f"§2's verdict line publishes {m.group(1)}% of exports in the 10am–2pm "
        f"window (report_tokens._midday_export_share, through S2_VERDICT) but the "
        f"artifacts derive {pct}% -- the token and the artifacts have parted, so "
        f"neither figure may be pinned into the hand-written prose until they agree")
    return pct


def _export_share_pct():
    """Exports as a share of production -- the OTHER percentage, and the one
    the two paragraphs must not attach to a time of day."""
    with (ROOT / "data" / "enphase_daily_production.csv").open() as fh:
        footer = [r for r in csv.DictReader(fh) if r["Date/Time"] == "Total"]
    assert footer, ("data/enphase_daily_production.csv has no 'Total' footer row -- "
                    "annual production cannot be read from it")
    production = float(footer[0]["Energy Delivered (kWh)"].replace(",", ""))
    return round(RD["totals"]["exp"] / production * 100)


def _nights_with_ev_charging():
    """(nights the EV drew, eligible nights) in the midnight-6am window."""
    entry = json.loads((ROOT / "data" / "quiet_night_floor.json").read_text())[
        "night_floor"]["issue_114_investigation"]["ev_absence_by_window"][_EV_NIGHT_WINDOW]
    eligible = entry["n_eligible_nights"]
    assert eligible == BEHAVIOR["window"]["days"], (
        f"the {_EV_NIGHT_WINDOW} window was classified over {eligible} eligible "
        f"nights but the analysis window is {BEHAVIOR['window']['days']} days -- "
        f"the two no longer describe the same year")
    return eligible - entry["n"], eligible


# Everything that PLACES A CLAIM IN THE DAYTIME, which is the wider question
# the negative half asks. The two halves need different vocabularies: naming
# the window is a provenance requirement on one figure, while attaching ANY
# time of day to the export-over-production share is the referent error, and
# it does not stop being one because the writer paraphrased. The first draft
# of this guard listed only ("10am–2pm", "midday") and passed a paragraph
# reading "60% ... leaves as exports, and it leaves in the middle of the day"
# -- the same defect in different words. Matched case-insensitively, so a
# phrase at the start of a sentence is not a hole.
_TIME_OF_DAY_WORDS = _MIDDAY_WINDOW_WORDS + (
    "midday", "mid-day", "middle of the day", "middle of the afternoon",
    "midafternoon", "mid-afternoon", "afternoon", "daytime", "daylight hours",
    "solar noon", "noon", "while the sun is up", "in the sun",
)

# What _TIME_OF_DAY_WORDS must still carry, committed separately from the
# constant itself -- the same two-place-edit discipline as
# test_report_tokens._SEAM_VOCABULARY_FLOOR, and for the same reason: the
# regression case below generates one probe per member, so a member deleted
# from the constant takes its own probe with it and the case goes green by
# having stopped asking. A SUPERSET needs no edit here; a phrase DISAPPEARING
# does, in the same commit, where a reviewer reads why.
#
# The window member is the one entry that is DERIVED rather than typed: it is
# whatever _MIDDAY_WINDOW_WORDS built for this tariff, so the floor keeps
# asking "is the measured window still in the vocabulary" on a fork instead of
# demanding this household's hours from it.
_TIME_OF_DAY_VOCABULARY_FLOOR = (
    _MIDDAY_WINDOW_WORDS[0], "midday", "middle of the day",
    "middle of the afternoon", "daytime", "noon",
)


def _time_of_day_words_in(clause):
    """Which of _TIME_OF_DAY_WORDS place `clause` in the daytime."""
    lowered = clause.casefold()
    return [w for w in _TIME_OF_DAY_WORDS if w.casefold() in lowered]


# Where a clause ENDS, read outward from the percentage in either direction:
# at the neighbouring percentage -- so a claim belonging to another figure is
# never read as describing this one -- or at the end of the sentence. A period
# glued to a digit ($2.50, ~10.4¢) is not a sentence end, which is why the
# sentence pattern demands whitespace or a tag after the period rather than
# matching a bare one.
#
# The two directions carry the same two rules on purpose: a phrase must be
# caught at the same distance BEFORE a percentage as after it. Reading only
# forward was the hole -- "At midday, 60% of what the array makes leaves as
# exports" states the retired defect with the timing moved one clause to the
# left, and a tail-only cutter sees an innocent clause. The forward pattern
# alone may end at the string's end ($); backwards, that position is a period
# sitting flush against the percentage's own digits (".60%"), which is the
# glued-to-a-digit case and not a sentence end.
#
# Neither direction stops at a TAG boundary, so a clause can run out of the
# element the figure sits in -- the leading side now does that as the trailing
# side always did. Left that way on purpose: cutting at markup would narrow the
# clause and hand back a hiding place ("<b>At midday,</b> 60% of what the array
# makes ..."). It costs nothing on the three GUARDED passages: §0, §2 and §8
# all pass with the wide cutter. The two conflations this cutter could not
# reach were both in text report_tokens.py owns rather than text written into
# index.html, and both are now closed at the generator: §8's heading no longer
# characterises the all-hours share's worth at all (S8_VERDICT_SHORT), and what
# the year's exports are worth is the EXPORT_VALUE_SURPLUS_BOUND /
# EXPORT_VALUE_NETTING_BOUND range, the whole export profile priced through both
# NEM 2.0 settlement treatments instead of read off the midday cell. The
# paragraph that publishes it is pinned by
# case_s8_export_value_is_published_as_a_bounded_range below.
_CLAUSE_END_AFTER = (r"\d+(?:\.\d+)?%", r"\.(?:\s|<|$)")
_CLAUSE_END_BEFORE = (r"\d+(?:\.\d+)?%", r"\.(?:\s|<)")


def _clause_head(before):
    """The tail of `before` that still belongs to the percentage following it:
    everything after the LAST clause boundary in it, or all of it if the
    percentage opens its sentence."""
    start = 0
    for pattern in _CLAUSE_END_BEFORE:
        for m in re.finditer(pattern, before):
            start = max(start, m.end())
    return before[start:]


def _clause_tail(rest):
    """The head of `rest` that still belongs to the percentage preceding it:
    everything up to the FIRST clause boundary in it."""
    cut = len(rest)
    for pattern in _CLAUSE_END_AFTER:
        nxt = re.search(pattern, rest)
        if nxt:
            cut = min(cut, nxt.start())
    return rest[:cut]


# THE THIRD HOLE: a sentence end is where a CLAUSE stops, not where a REFERENT
# does. Both cutters above stop dead at a period, so the timing claim can move
# one sentence over and point back at the figure -- "60% ... leaves as exports.
# Nearly all of it leaves in the middle of the day." republishes the exact
# defect this guard retires, with a LISTED vocabulary member present, and
# passed. So did "It all goes out at midday." after it, and "Almost all of it
# at midday." written in front of it. A comma-to-period edit was the whole
# exploit.
#
# WHAT SEPARATES THAT FROM THE REPORT'S OWN PROSE, and it is not the pronoun:
# the offending sentence carries NO FIGURE OF ITS OWN. A sentence with a figure
# in it is making its own measured claim and is read on its own terms; a
# figure-free sentence sitting beside a measured one measures nothing, so a
# time of day in it can only be describing the measurement next to it. The
# clause therefore absorbs the RUN of figure-free sentences on either side of
# it, and stops at the first sentence that carries a digit.
#
# THE NARROWER RULE PROPOSED IN REVIEW WAS MEASURED FIRST AND DOES NOT HOLD.
# "Every sentence carrying a time-of-day phrase must also carry the midday
# window or the midday share" rejects §8's own "Since the 2026 TOU change those
# midday exports credit at only ~10.4¢/kWh." and its "it monetizes those midday
# kWh at 60-87¢ instead of 10¢" -- two correct sentences about the midday
# slice, neither of which restates the window or the share. The digit rule
# accepts both (each carries its own figures) and still rejects all four
# rewordings. case_the_referent_guard_reads_across_a_sentence_boundary pins
# both halves of that, including the counterexample, so the rule that was NOT
# adopted stays falsifiable instead of being remembered.
#
# The cost is stated: a figure-free sentence beside the export share may not
# carry a time of day at all, even innocently ("Solar is a daytime resource."
# next to it now fails). That is the ambiguity the guard exists to force out of
# the paragraph, and the fix is to put the figure in the sentence.
_SENTENCE_END_RE = re.compile(r"\.(?:\s|<|$)")
_FIGURE_RE = re.compile(r"\d")


def _carries_a_figure_of_its_own(sentence):
    """Whether `sentence` states a measurement rather than commenting on the
    one beside it.

    Two things are removed before the digits are counted, because neither is a
    measurement. MARKUP: a digit inside <a href="#s8"> is a link target.
    TIME-OF-DAY PHRASES: "10am-2pm" is the guard's own vocabulary, a window
    LABEL rather than a quantity taken over it, and counting its digits would
    hand the defect back its best hiding place -- "60% ... leaves as exports.
    Nearly all of it leaves in the 10am-2pm window." names the window and still
    predicates it of the whole share."""
    bare = re.sub(r"<[^>]*>", "", sentence)
    for word in _TIME_OF_DAY_WORDS:
        bare = re.sub(re.escape(word), "", bare, flags=re.I)
    return bool(_FIGURE_RE.search(bare))


def _sentence_spans(text):
    """(start, end) for every sentence in `text`, split on the same sentence
    end the clause cutters use so the two cannot disagree about where one
    stops. Text trailing the last period is a span too -- a paragraph does not
    have to end on one, and neither does the fragment handed in here."""
    spans, start = [], 0
    for m in _SENTENCE_END_RE.finditer(text):
        spans.append((start, m.start() + 1))
        start = m.start() + 1
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _figure_free_run_before(before):
    """The run of figure-free sentences `before` ENDS with.

    Self-limiting exactly where it must be: when the clause was cut at a
    neighbouring PERCENTAGE rather than at a sentence end, `before` ends on
    that percentage's own digits, its last span carries a figure, and nothing
    is absorbed -- another figure's clause is never read into this one."""
    cut = len(before)
    for start, end in reversed(_sentence_spans(before)):
        if _carries_a_figure_of_its_own(before[start:end]):
            break
        cut = start
    return before[cut:]


def _figure_free_run_after(rest):
    """The run of figure-free sentences `rest` BEGINS with -- the same rule as
    _figure_free_run_before, read the other way."""
    cut = 0
    for start, end in _sentence_spans(rest):
        if _carries_a_figure_of_its_own(rest[start:end]):
            break
        cut = end
    return rest[:cut]


def _clauses_about(para, pct):
    """Everything the paragraph says about one percentage: for each occurrence
    of that figure, the whole segment it sits in -- the text BEFORE it back to
    the previous clause boundary, the figure itself, and the text after it up
    to the next one -- plus the run of figure-free sentences on either side of
    that segment. Both sides are cut by the same rules (see _CLAUSE_END_AFTER
    and the figure-free run above), so a phrase reordered around the figure, or
    moved into a sentence of its own, is read the same way wherever the writer
    put it."""
    for m in re.finditer(rf"\b{pct}%", para):
        head = _clause_head(para[:m.start()])
        tail = _clause_tail(para[m.end():])
        yield (_figure_free_run_before(para[:m.start() - len(head)])
               + head + m.group(0) + tail
               + _figure_free_run_after(para[m.end() + len(tail):]))


def _assert_the_two_shares_stay_apart(para, where, export_pct, midday_pct):
    """The defect these cases exist to catch: the exports-over-production
    figure written as if it were the midday share.

    They are different quantities -- exports at ANY hour over production, and
    the 10am-2pm slice of those exports -- so the paragraph must state both,
    and only the time-of-day figure may carry the time-of-day words.

    Both halves read the whole segment a figure sits in, so either half can be
    satisfied -- or tripped -- by words on either side of the percentage. That
    cuts the way it should: "The exports concentrate in the 10am-2pm window:
    63% ..." names the measured window as squarely as the same words trailing
    the figure, and a time of day written ahead of the export share is the
    referent error written backwards."""
    assert export_pct != midday_pct, (
        f"{where}: the export share and the 10am-2pm share of exports both round "
        f"to {export_pct}%, so this guard cannot tell which figure a percentage "
        f"in the paragraph means -- pin them by hand until they part again")
    # A PERCENTAGE IS READ AS A REFERENT HERE, and the guard has no notion of
    # what any given one measures: it finds every "60%" in the paragraph and
    # judges the clause around it as if it were the export share. §8 already
    # carries "60-87¢" in a sentence about battery arbitrage, one keystroke
    # from a second "60%" that this guard would reject as a referent error
    # while naming the wrong figure in the message. So require each of the two
    # to occur exactly once and say so plainly -- an ambiguous paragraph fails
    # for the reason it is actually failing, not for one that misdirects the
    # writer to a sentence that was never the problem.
    for pct, what in ((export_pct, "the export share"),
                      (midday_pct, f"the {_MIDDAY_WINDOW_WORDS[0]} share of exports")):
        found = re.findall(rf"\b{pct}%", para)
        assert len(found) == 1, (
            f"{where}: {pct}% is {what}, and it appears {len(found)} times in this "
            f"paragraph. This guard reads a percentage as a referent and cannot tell "
            f"two occurrences apart, so every one of them would be judged as {what} -- "
            f"whichever occurrence means something else needs a different form (a "
            f"range, a ratio, words), or this guard needs to be taught to locate the "
            f"figure by its own phrase rather than by its digits")
    assert any(any(w.casefold() in c.casefold() for w in _MIDDAY_WINDOW_WORDS)
               for c in _clauses_about(para, midday_pct)), (
        f"{where}: {midday_pct}% is the 10am-2pm share of exports, but no clause "
        f"stating it names that window -- the timing claim is asserted rather than "
        f"stated as the measurement it is")
    for clause in _clauses_about(para, export_pct):
        offenders = _time_of_day_words_in(clause)
        assert not offenders, (
            f"{where}: {export_pct}% is exports over PRODUCTION, exported at any "
            f"hour, but its own clause says {clause!r} -- {offenders} describes the "
            f"midday share, which is {midday_pct}%, a different quantity")
        # A PRICE IS A TIME OF DAY WRITTEN IN CENTS. §0's bottom line read
        # "(60% exported at ~10¢)", and the super-off-peak export credit is
        # what the MIDDAY slice earns -- the rest of the share leaves in
        # off-peak and on-peak hours and credits at UDC+CEA, several times
        # higher (rates.py: 46.2¢ and 81.9¢ summer, against 7.6¢). Naming one
        # cell of the credit map beside the all-hours share says the same
        # wrong thing "at midday" says, so it fails in the same place rather
        # than in a second guard that could be extended to two paragraphs and
        # not the third.
        assert "¢" not in clause, (
            f"{where}: {export_pct}% is exports over PRODUCTION, exported at any hour, "
            f"but its own clause prices them: {clause!r}. No single cell of the export "
            f"credit map is that share's value -- the {midday_pct}% leaving in the "
            f"{_MIDDAY_WINDOW_WORDS[0]} window credits super-off-peak and the rest does "
            f"not, so the price belongs to whichever figure it was measured on")


def case_s2_key_architectural_fact_matches_the_artifacts():
    """issue #143: §2's closing .small line is hand-written, not templated
    (report-template.html carries only a TODO there) -- lock its export share,
    its 10am-2pm export share and its EV-charging night count to the artifacts
    that measure them, and hold each percentage to its own referent."""
    m = re.search(r'<p class="small">That last split is the key architectural fact'
                  r'.*?</p>', HTML, re.S)
    assert m, "§2's 'key architectural fact' paragraph not found in index.html"
    para = m.group(0)

    export_pct = _export_share_pct()
    midday_pct = _midday_export_share_pct()
    nights, eligible = _nights_with_ev_charging()

    checks = [
        f"{export_pct}% of what the array makes leaves as exports",
        f"{midday_pct}% of those exported kWh go out in the 10am–2pm window",
        f"the EV charged between midnight and 6am on {nights} of the year's {eligible} nights",
    ]
    for value in checks:
        assert value in para, (
            f"§2's 'key architectural fact' paragraph: {value!r} not found in it")
    _assert_the_two_shares_stay_apart(para, "§2's 'key architectural fact' paragraph",
                                      export_pct, midday_pct)
    return (f"§2's 'key architectural fact' line states {export_pct}% exported, "
            f"{midday_pct}% of those exports in the 10am–2pm window (hourly_profile.csv, "
            f"report_data.json's seasonal hours and §2's own verdict line all agree), "
            f"and charging on {nights} of {eligible} nights (quiet_night_floor.json)")


def case_s8_more_panels_timing_matches_the_artifacts():
    """issue #143: §8's 'more panels' verdict rests on a timing claim -- that
    the exports leave while the EV is not drawing -- so pin the two figures
    that measure it, and the export share they sit beside, to their artifacts.

    §8 is advanced-tier prose and exempt from the basic tier's density cap, so
    this case checks only the figures and their referents, not the sentence
    shape."""
    m = re.search(r"<p><b>More panels: .*?</p>", HTML, re.S)
    assert m, "§8's 'more panels' paragraph not found in index.html"
    para = m.group(0)

    export_pct = _export_share_pct()
    midday_pct = _midday_export_share_pct()
    nights, eligible = _nights_with_ev_charging()

    checks = [
        f"exports {export_pct}% of what it makes ({RD['totals']['exp']:,} kWh/yr)",
        f"{midday_pct}% of those exports leave in the 10am–2pm window",
        f"the EV charged between midnight and 6am on {nights} of {eligible} nights",
    ]
    for value in checks:
        assert value in para, f"§8's 'more panels' paragraph: {value!r} not found in it"
    _assert_the_two_shares_stay_apart(para, "§8's 'more panels' paragraph",
                                      export_pct, midday_pct)
    return (f"§8's 'more panels' paragraph states {export_pct}% of production exported "
            f"({RD['totals']['exp']:,} kWh/yr), {midday_pct}% of it in the 10am–2pm "
            f"window, against charging on {nights} of {eligible} nights")


# The two ends of what an exported kWh is worth, low first, each with the
# rates.py price map that produces it and the words §8 must use to name the
# settlement treatment behind it. rates.bill_nem_monthly() settles NEM 2.0 by
# MONTHLY PER-PERIOD NETTING: inside one month and period an export first
# cancels an import (rates.energy(), UDC+CEA+PCIA) and only the leftover is paid
# the surplus credit (rates.credit(), UDC+CEA). Two treatments, two prices, and
# no committed artifact resolves which one any individual month took -- so the
# report publishes both and neither is the value.
#
# THE HIGH END IS energy() AND NOT allin(). allin() = energy() + NBC is what a
# GROSS import costs; bill_nem_monthly() bills NBC on gross imports before any
# netting, so an export never avoids it. That is measured against the engine in
# test_report_tokens.case_the_two_export_bounds_are_the_two_settlement_
# treatments rather than argued from the constants here.
_EXPORT_BOUNDS = (
    ("EXPORT_VALUE_SURPLUS_BOUND", "credit", "surplus"),
    ("EXPORT_VALUE_NETTING_BOUND", "energy", "cancel"),
)


def _rates_module():
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import rates as R
    return R


def _report_tokens_module():
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import report_tokens as rt
    return rt


def _export_bound_from_report_data(rate_name):
    """One end of what an exported kWh is worth, $/kWh: a rates.py price map
    weighted by report_data.json's own hour-of-day export profiles.

    Neither end is what one more kW of panels would earn -- exports are the
    residual left after household load, not the shape of added production --
    and no case in this file may use either that way (issue #190).

    Rebuilt here rather than imported from report_tokens._export_value_bound
    -- the recompute convention this file already follows in
    _expected_month_labels and _midday_export_share_from_report_data, so a bug
    in the generator's weighting fails a case instead of being reproduced by
    it.

    The day counts are split by DAY TYPE as well as by season, through
    rates.off_peak_day(): the tariff bills a weekend or holiday morning
    super-off-peak and a weekday morning off-peak, and this window holds 111
    days of the former. The profiles are a mean day per SEASON, so that split
    is a modelled assumption both ends carry, and §8 says so.
    Returns (value, rebuild error against totals.exp)."""
    R = _rates_module()
    rate = getattr(R, rate_name)
    total = value = 0.0
    for d in _analysis_window_dates():
        seas = "S" if d.month in R.SUMMER_MONTHS else "W"
        off_day = R.off_peak_day(d)
        for hour, kwh in enumerate(RD[f"hourly_{seas}"]["exp"]):
            total += kwh
            value += kwh * rate(seas, R.period(hour, off_day))
    return value / total, abs(total - RD["totals"]["exp"]) / RD["totals"]["exp"]


def _price_map_cells(rate_name):
    R = _rates_module()
    rate = getattr(R, rate_name)
    return {(s, p): rate(s, p)
            for s in ("S", "W") for p in ("sop", "off", "on")}


def _s8_section():
    """§8's whole section, heading through the last paragraph before §9."""
    start = HTML.find('<h2 id="s8"')
    assert start > 0, "§8's heading not found in index.html"
    end = HTML.find('<details class="sec" id="sec9"', start)
    assert end > start, ("§9's <details> wrapper not found after §8, so §8's span "
                         "cannot be bounded -- every §8 pin below would silently "
                         "scan the rest of the document instead")
    return HTML[start:end]


# The published prices §8 may not attach to ADDED CAPACITY. Each pattern names
# a way of saying "one more kW/panel earns", and the case below rejects any
# sentence that carries one of them AND a price. Written as the shape of the
# claim rather than as the exact wording that shipped, so a paraphrase is
# caught too.
#
# EVERY ALTERNATIVE HERE NAMES ADDED CAPACITY, and that is the property that
# keeps the guard from refusing correct prose. A bare "to add" once sat in this
# list and did not: paired with _ANY_PRICE_RE, which matches any ¢ or $ figure
# anywhere in the sentence, it fails ordinary §8 sentences that price an
# installation rather than valuing a marginal kWh -- "≈$8,000 to add a second
# string", "the right time to add storage is after the panels are paid off, at
# ~$0.30/W". A guard that refuses correct input is worse than no guard here,
# because the remedy under pressure is to switch it off; so the infinitive has
# to carry the capacity noun with it. Both halves are pinned by
# case_the_added_capacity_guard_rejects_the_defect_and_accepts_a_priced_install.
_ADDED_CAPACITY_EARNS_RE = re.compile(
    r"marginal (?:new-)?panel|marginal kW|new-panel kWh|"
    r"added (?:panel|panels|capacity|kW|output)|an added kWh|one more kW|"
    r"per kW per year|kWh/kW/yr\s*[×x]|[\d.,]+ ?kW at |"
    r"to add (?:\d[\d.,]*\s*kW|(?:a |an |another |more |extra )?"
    r"(?:panels?|capacity|kW|array))\b|"
    r"expansion (?:earns|returns|pays|yields)", re.I)

# Any price at all: a cents figure, a dollar figure, or a $/W band.
_ANY_PRICE_RE = re.compile(r"[\d.,]+¢|\$[\d.,]+")

# A sentence SAYING WHAT AN EXPORTED kWh IS WORTH -- the claim that may never
# rest on one end of the range. Written as subject-plus-valuation-verb rather
# than as the shipped wording, so the point estimate cannot come back as a
# paraphrase. The subject is deliberately narrow: §8 also prices battery
# arbitrage and the midday cell, and neither of those is a claim about what the
# year's exports fetched.
_VALUATION_CLAIM_RE = re.compile(
    r"(?:an? exported kWh|the year's exports|those exports|its exports)"
    r"[^.]{0,120}?(?:is worth|are worth|earns?|fetch(?:es)?|settles? at)", re.I)

# The payback arithmetic that rested on pricing added capacity at the export
# credit. None of these forms may appear anywhere in §8.
_EXPANSION_PAYBACK_FORMS = (
    (r"kWh/kW/yr\s*[×x]", "the yield x export-credit multiplication"),
    (r"per kW per year", "a $/kW/yr expansion return"),
    (r"[\d.]+\s*–\s*[\d.]+ year payback", "an expansion payback band"),
    (r"[\d.]+\s*–\s*[\d.]+ yr payback", "an expansion payback band"),
    (r"retrofit pricing", "an assumed retrofit $/W price"),
    (r"\$[\d.]+\s*–\s*[\d.]+/W", "an assumed retrofit $/W band"),
)


def case_s8_export_value_is_published_as_a_bounded_range():
    """issue #182: §8 priced the year's exports at the MIDDAY cell of the price
    map, generalizing one cell of six to a whole year of exports; the
    profile-weighted figure that replaced it was then published as though the
    ALL-SURPLUS treatment were the settled answer. Both are the same failure --
    a derivation narrower than the sentence it is written into.

    WHAT THE ARTIFACTS SETTLE AND WHAT THEY DO NOT. rates.bill_nem_monthly()
    nets NEM 2.0 monthly and per period, so an exported kWh either cancels an
    import (rates.energy()) or is paid the surplus credit (rates.credit()).
    data/report_data.json:period_split shows imports above exports in all six
    season/period cells, so most exports net and the truth sits nearer the
    netting end -- but period_split is an ANNUAL total and the netting is
    MONTHLY, so nothing committed here says how any month settled. The range is
    therefore the answer, and a bound does not claim to be the value.

    WHAT ONE MORE kW WOULD EARN IS A DIFFERENT QUANTITY AGAIN, and keeping that
    apart is this case's other job. Exports are the residual left after
    household load, not the shape of added production: part of an added panel's
    output would displace an import rather than leave the meter. Pricing it
    needs a counterfactual re-billing at a larger array (issue #190), which
    nothing committed here runs, which is why EXPANSION_PAYBACK_YEARS is a
    report_tokens.KNOWN_GAPS token.

    Eight pins:

      1. BOTH bounds are published, each recomputed here from the artifacts;
      2. they appear in the valuation sentence low end first, as a range;
      3. neither is a single cell of its own price map -- the shape of the
         original defect, checked against rates.py rather than today's digits;
      4. NEITHER IS PUBLISHED AS THE VALUE. No sentence in §8 may make a
         valuation claim about an exported kWh while naming one bound and not
         the other. This is the pin that fails if the range is collapsed back
         to a point estimate, at either end;
      5. §8 states that the settlement falls between the two;
      6. §8 states the day-type assumption both bounds carry;
      7. no sentence in §8 attaches a price to added capacity, and §8 publishes
         none of the expansion payback arithmetic that rested on doing so;
      8. §8 says out loud that the marginal-panel value is not derived here, so
         a reader cannot take either bound as the answer by default.

    §8's own verdict is checked separately by
    case_s8_expansion_verdict_rests_on_the_cap_and_the_grandfathering, which
    holds the two artifact-backed figures the "no" actually rests on."""
    # SCOPED TO §8, not to the 'more panels' paragraph. The section's timing
    # figures and its export valuation are pinned by different cases and are
    # deliberately different paragraphs -- one paragraph carrying both would be
    # over this repo's own length rule. _s8_section() is bounded at §9's
    # wrapper, so "somewhere in §8" is still a bounded claim, and the
    # valuation sentence is located by its own words rather than by position.
    section = _s8_section()

    published = []
    for token, rate_name, treatment in _EXPORT_BOUNDS:
        value, rebuild_err = _export_bound_from_report_data(rate_name)
        assert rebuild_err < _EXPORT_REBUILD_TOLERANCE, (
            f"report_data.json's hour-of-day export profiles rebuild to a total "
            f"{rebuild_err * 100:.4f}% off its own totals.exp, past the "
            f"{_EXPORT_REBUILD_TOLERANCE:.0%} bound -- nothing priced off them "
            "describes this window")
        rendered = f"{value * 100:.1f}¢"
        assert rendered in section, (
            f"§8 does not state {rendered}, the "
            f"{token} end of what an exported kWh is worth (rates.{rate_name}() "
            "weighted by data/report_data.json's own hour-of-day export profiles). "
            "Both ends are published because the settlement lies between them; one "
            "of them alone reads as the value")
        for (seas, period), rate in _price_map_cells(rate_name).items():
            assert abs(value - rate) > 5e-4, (
                f"the {token} end is rates.{rate_name}({seas!r}, {period!r}) -- a "
                "single cell of a six-cell map, applied to a year of exports that do "
                "not all leave in one period")
        published.append((token, rendered, treatment))

    # THE SENTENCE THAT MAKES THE CLAIM, not merely the paragraph that contains
    # it. It must state a RANGE, low end first: that ordering is what tells a
    # reader the first figure is a floor rather than the answer.
    # The sentence end is a period followed by space, tag or end -- NOT any
    # period. "22.9¢" and "$2.50" carry periods glued to digits, and a bare
    # [^.]* locator cuts the clause at "22", which is how the first draft of
    # this pin reported "names no price at all" on a sentence that names two.
    # Same rule _CLAUSE_END_AFTER above uses, for the same reason.
    claim = re.search(r"an exported kWh on this profile.*?\.(?=\s|<|$)", section, re.S)
    assert claim, (
        "§8 no longer contains a sentence saying what an "
        "exported kWh on this profile is worth -- the valuation claim this case pins "
        "has been reworded, and the case cannot tell a rewording from a deletion")
    claim_text = claim.group(0)
    assert re.search(r"\bbetween\b", claim_text), (
        f"§8's valuation sentence no longer states a range: {claim_text!r}. What an "
        "exported kWh is worth depends on whether it nets against an import or is "
        "settled as surplus, and the committed artifacts do not resolve that per "
        "month -- so the sentence must publish both ends, not one of them")
    prices = re.findall(r"[\d.]+¢", claim_text)
    assert prices[:2] == [published[0][1], published[1][1]], (
        f"§8's valuation sentence names {prices[:2]} where the two bounds are "
        f"{[p[1] for p in published]}, low end first. The low end leading is what "
        f"makes it read as a floor rather than as the answer: {claim_text!r}")
    for _token, rendered, treatment in published:
        head = claim_text[:claim_text.index(rendered)]
        rest = claim_text[claim_text.index(rendered) + len(rendered):]
        window = head[-90:] + rest[:90]
        assert treatment in window.casefold(), (
            f"§8's valuation sentence states {rendered} without naming the settlement "
            f"treatment that produces it (looked for {treatment!r} beside it). An "
            f"unnamed bound is indistinguishable from a point estimate: {claim_text!r}")

    # 4. NEITHER END STANDS ALONE AS THE VALUE. Every sentence in §8 that makes
    #    a valuation claim about an exported kWh must carry both bounds or
    #    neither -- a sentence naming one of them and saying what an exported
    #    kWh "is worth" or "earns" has republished the point estimate with the
    #    other end deleted, which is the exact failure this issue closes.
    text = htmlmod.unescape(re.sub(r"<[^>]+>", " ", section))
    lo_fig, hi_fig = published[0][1], published[1][1]
    for sentence in re.split(r"\.(?=\s|$)", text):
        if not _VALUATION_CLAIM_RE.search(sentence):
            continue
        has = [f for f in (lo_fig, hi_fig) if f in sentence]
        assert len(has) != 1, (
            f"§8 states what an exported kWh is worth as the single figure {has[0]}: "
            f"{sentence.strip()!r}. That is one END of the range -- "
            f"{'the all-surplus floor' if has[0] == lo_fig else 'the all-netting ceiling'}"
            f" -- and the settlement lies between {lo_fig} and {hi_fig}. Publish both")

    # 5. The range is named as a range, in the section's own words.
    assert re.search(r"(?:sits|falls|lies|land\w*)\s+between\b|between the two\b", text), (
        "§8 never says the real settlement falls between the two bounds. Without it "
        "the pair reads as two competing answers rather than as a bracket around one")

    # 6. The assumption both bounds carry, stated rather than hidden.
    assert re.search(r"season-wide mean", text) and re.search(r"day type", text), (
        "§8 no longer states that the hour-of-day export profile behind both bounds "
        "is a season-wide mean that has already discarded day type, so the "
        "weekday/off-peak-day split is a modeled assumption rather than a "
        "reconstruction")

    # 7. No sentence prices ADDED CAPACITY. Sentence-split on the repo's own
    #    rule, because the defect this catches lives in one sentence: the
    #    paragraph legitimately contains both prices and the words "one more kW
    #    of panels", and only their appearing TOGETHER is the claim.
    for sentence in re.split(r"\.(?=\s|$)", text):
        capacity = _ADDED_CAPACITY_EARNS_RE.search(sentence)
        priced = _ANY_PRICE_RE.search(sentence)
        assert not (capacity and priced), (
            f"§8 prices added capacity: the sentence {sentence.strip()!r} carries both "
            f"{capacity.group(0)!r} and the price {priced.group(0)!r}. What one more kW "
            "would earn is not derived in this repo -- exports are the residual left "
            "after household load, not the shape of added production (issue #190) -- "
            f"and {lo_fig}-{hi_fig} brackets what an EXPORTED kWh is worth, not what an "
            "added one would be")

    # And none of the arithmetic that rested on it survives anywhere in §8.
    for pattern, what in _EXPANSION_PAYBACK_FORMS:
        hit = re.search(pattern, text)
        assert not hit, (
            f"§8 publishes {what} ({hit.group(0)!r}) -- an expansion payback needs a "
            "marginal-kW value this repo does not derive (issue #190) and a retrofit "
            "$/W price it does not collect, which is why EXPANSION_PAYBACK_YEARS is a "
            "KNOWN_GAPS token")

    # 8. The gap is stated, not left to inference.
    assert re.search(r"one more kW of panels[^<]{0,80}?"
                     r"(does not answer|not derived|no answer here)", text), (
        "§8 never says that what one more kW of panels would earn is undetermined. "
        "Without it a reader takes the export bounds beside it as the answer, which is "
        "the conflation this case exists to prevent")

    return (f"§8 publishes what an exported kWh is worth as the range {lo_fig}-{hi_fig} "
            f"-- the two NEM 2.0 settlement treatments of data/report_data.json's own "
            f"hour-of-day profiles, neither on any cell of its own price map, neither "
            f"standing alone in any valuation sentence -- says the settlement falls "
            f"between them, states the season-wide-mean day-type assumption both carry, "
            f"prices no added capacity in any of its "
            f"{len(_EXPANSION_PAYBACK_FORMS)} forbidden payback forms, and says the "
            "marginal-kW value is not derived here")


# The three TOU periods this window's exports leave in, each with a locator
# for the words §8 names it by. "off-peak" needs the lookbehind or it matches
# inside "super-off-peak" and reads the wrong share back.
_EXPORT_PERIOD_WORDS = (
    ("sop", "super-off-peak", r"super-off-peak"),
    ("off", "off-peak", r"(?<![-\w])off-peak"),
    ("on", "on-peak", r"(?<![-\w])on-peak"),
)

# The periods whose price band §8 must state. The sentence's whole point is
# that the cheapest cell is not the year, so the two dearer periods have to
# carry the prices that make that visible; super-off-peak is the cell being
# argued against and is priced elsewhere in the section.
_PERIODS_NEEDING_A_BAND = ("off", "on")


def _export_period_shares():
    """{period: share of the window's exported kWh that leaves in it}, and the
    profiles' rebuild error against totals.exp.

    Same reader, same weighting and same rebuild gate as
    _export_bound_from_report_data: report_data.json's per-season mean-day
    export profiles over the window's real days, with the period taken from
    rates.period() at each hour's own day type (rates.off_peak_day()). The day
    type is what moves the 6-10am band between off-peak and super-off-peak, so
    a share computed on a bare weekday schedule is a different number."""
    R = _rates_module()
    kwh = {"sop": 0.0, "off": 0.0, "on": 0.0}
    for d in _analysis_window_dates():
        seas = "S" if d.month in R.SUMMER_MONTHS else "W"
        off_day = R.off_peak_day(d)
        for hour, k in enumerate(RD[f"hourly_{seas}"]["exp"]):
            kwh[R.period(hour, off_day)] += k
    total = sum(kwh.values())
    return ({p: v / total for p, v in kwh.items()},
            abs(total - RD["totals"]["exp"]) / RD["totals"]["exp"])


def _export_period_price_band(period):
    """What an export leaving in `period` is credited, in whole cents, as the
    two ends the rest of §8 already publishes: the cheapest SURPLUS credit and
    the dearest NETTING value across the two seasons. Same pair of rates.py
    functions as the two published bounds, read cell by cell instead of
    profile-weighted, so the band a reader checks against the price map is
    derived from the map rather than typed beside it."""
    R = _rates_module()
    return (min(R.credit(s, period) for s in ("S", "W")),
            max(R.energy(s, period) for s in ("S", "W")))


def case_s8_export_period_split_matches_the_profiles():
    """issue #182 review: §8's "neither end rests on the cheapest cell"
    sentence carries five derived figures -- the three period shares of the
    year's exported kWh and the two price bands beside them -- typed into
    prose while the two bounds in the same sentence were pinned three ways.
    Re-run the pipeline on another window and the shares drift silently; they
    are shares OF A WINDOW, not constants of the tariff.

    WHY A CASE AND NOT FIVE TOKENS. Both were available, and the two export
    bounds in this same sentence went the other way, so the difference is worth
    stating. Those two are single self-formatting values ("22.9¢") that the
    TEMPLATE names in its own worked example, which is what makes a token pay:
    report_tokens.py resolves it, the seam guard renders it into its template
    line, and generate_report.py can write the sentence. These five are not one
    value each -- they are a partition and its price map, read only inside a
    human-authored paragraph of a block report_blocks.py classifies "human",
    which the generator never writes. A token for each would have to be given a
    live seam in a template that deliberately leaves this paragraph blank, and
    five self-formatting tokens strung through one sentence would render as
    five figures nothing checks the SUM of. Recomputing them here pins all five
    to the same artifact and the same weighting the bounds use, and pins the
    partition as a partition.

    RELATIONSHIP, these shares against the published bounds: SAME PROFILES,
    DIFFERENT QUESTION. The bounds are the profile weighted by a price; these
    are the profile weighted by nothing, split by the period rates.period()
    assigns each hour. A bound that moved without the split moving (or the
    reverse) means one of the two weightings stopped reading the artifact.

    Four pins:

      1. every share is the one the profiles produce, at the precision §8
         prints;
      2. the three of them partition the year -- a split whose parts do not sum
         to the whole is not a split;
      3. each price band §8 attaches to a period is that period's own two
         settlement treatments, derived from rates.py rather than typed;
      4. the split is published in ONE place. report_tokens.py's block comment
         above _export_value_bound used to re-type it, so drift had to be
         caught twice; it now points here instead."""
    section = _s8_section()
    shares, rebuild_err = _export_period_shares()
    assert rebuild_err < _EXPORT_REBUILD_TOLERANCE, (
        f"report_data.json's hour-of-day export profiles rebuild to a total "
        f"{rebuild_err * 100:.4f}% off its own totals.exp, past the "
        f"{_EXPORT_REBUILD_TOLERANCE:.0%} bound -- no share of them describes this "
        "window")

    text = htmlmod.unescape(re.sub(r"<[^>]+>", " ", section))
    # The sentence is located by naming all three periods, not by its own
    # wording, so a rewrite that keeps the claim keeps the pin. Split on the
    # repo's sentence rule: a bare [^.]* cuts inside "64.6" and "22.9¢".
    stated = [s for s in re.split(r"\.(?=\s|$)", text)
              if all(re.search(rx, s) for _p, _w, rx in _EXPORT_PERIOD_WORDS)
              and len(re.findall(r"[\d.]+%", s)) >= len(_EXPORT_PERIOD_WORDS)]
    assert len(stated) == 1, (
        f"§8 has {len(stated)} sentences putting a share of the year's exports in each "
        "of the three TOU periods, expected exactly one. With none, a reader has the "
        "two export bounds and no evidence that neither rests on the cheapest cell; "
        "with two, the split is published twice and the copies can drift")
    sentence = stated[0]

    published = []
    for period, words, rx in _EXPORT_PERIOD_WORDS:
        rendered = f"{shares[period] * 100:.1f}%"
        # The share BESIDE this period's name: [^%] cannot cross another
        # percentage, so the match is the nearest one ahead of the word.
        m = re.search(rf"([\d.]+)%[^%]{{0,80}}?{rx}", sentence)
        assert m, (
            f"§8's export-split sentence states no share for {words}: {sentence.strip()!r}")
        assert f"{m.group(1)}%" == rendered, (
            f"§8 puts {m.group(1)}% of the year's exports in {words} where "
            f"data/report_data.json's own hour-of-day profiles, weighted over the "
            f"window and split by rates.period() at each day's own day type, give "
            f"{rendered}")
        published.append((words, rendered))

        band = re.search(rf"{rx}(?: at ([\d.]+)\s*[–-]\s*([\d.]+)¢)?", sentence)
        lo, hi = _export_period_price_band(period)
        if band and band.group(1):
            assert (band.group(1), band.group(2)) == (f"{lo * 100:.0f}", f"{hi * 100:.0f}"), (
                f"§8 prices a {words} export at {band.group(1)}-{band.group(2)}¢ where "
                f"rates.py gives {lo * 100:.0f}-{hi * 100:.0f}¢ -- the cheapest surplus "
                f"credit and the dearest netting value across the two seasons, the same "
                f"two settlement treatments as the published bounds")
        else:
            assert period not in _PERIODS_NEEDING_A_BAND, (
                f"§8 states the {words} share without the price beside it, so the "
                "sentence no longer shows why the cheapest cell is not the year's "
                f"price: {sentence.strip()!r}")

    total = sum(shares.values())
    assert abs(total - 1) < 1e-9, (
        f"the three period shares sum to {total:.6f}, not 1 -- rates.period() is "
        "putting exported kWh somewhere this split does not name")
    printed = sum(float(r.rstrip('%')) for _w, r in published)
    assert abs(printed - 100) <= 0.15, (
        f"§8's three published shares sum to {printed}%, not 100% -- they are stated as "
        "a split of the year's exports and a split has to account for all of it")

    # 4. ONE COPY. report_tokens.py's block comment above _export_value_bound
    #    typed the same split; the drift it would cause is invisible from
    #    index.html, so the copy is gone and this keeps it gone.
    src = (ROOT / "analysis" / "report_tokens.py").read_text()
    head = src.find("# WHAT AN EXPORTED kWh IS WORTH IS A RANGE")
    tail = src.find("def _export_value_bound(", head)
    assert head > 0 and tail > head, (
        "report_tokens.py's export-value block comment was not found, so this pin "
        "cannot tell whether it re-types the split; re-point it at the renamed block")
    for _words, rendered in published:
        assert rendered not in src[head:tail], (
            f"report_tokens.py's export-value block comment types {rendered} again. "
            "That is a second copy of a figure that moves with the analysis window, "
            "and drift would then have to be caught in two places -- state it "
            "qualitatively there and let §8 publish the digits")

    return ("§8 splits the year's exports as "
            + ", ".join(f"{r} {w}" for w, r in published)
            + " -- recomputed from data/report_data.json's own hour-of-day profiles at "
              "each day's real day type, summing to the whole, with each stated price "
              "band derived from rates.py's credit/energy cells, and typed nowhere else")


def case_the_template_prices_no_added_capacity_and_needs_no_gap_token_in_s8():
    """issue #182 review, finding 1. index.html and report-template.html are
    two copies of §8's argument, and only one of them was corrected: the
    template's live paragraph went on saying "added capacity returns a
    {{EXPANSION_PAYBACK_YEARS}} payback" four lines under TODO text telling its
    author not to price added capacity at all.

    That is worse than a stale sentence. EXPANSION_PAYBACK_YEARS is a
    report_tokens.KNOWN_GAPS entry, and a gap token in LIVE markup is one
    generate_report.py demands a human-supplied override for -- so the
    documented reproduction route required the author to invent the single
    number this issue proved is not derivable here, and then printed the
    sentence.

    Two pins on the template's LIVE §8 markup (comments masked, since a TODO
    block naming the gap as a figure NOT to supply is exactly right and is
    where report_blocks.py's s8#1 scope now picks the token up):

      1. no KNOWN_GAPS token is referenced, so regenerating §8 asks nobody to
         invent a figure;
      2. no sentence pairs an added-capacity phrase with a price OR a token --
         a {{TOKEN}} is a figure that has not been substituted yet, and the
         retired sentence carried its valuation in one."""
    rt = _report_tokens_module()
    template = (ROOT / "report-template.html").read_text()
    live = re.sub(r"<!--.*?-->", " ", template, flags=re.S)
    start = live.find('<h2 id="s8"')
    end = live.find('<details class="sec" id="sec9"', start)
    assert start > 0 and end > start, (
        "§8's live span could not be bounded in report-template.html -- the heading or "
        "§9's wrapper has been renamed, and this pin would otherwise sweep the rest of "
        "the template")
    section = live[start:end]

    gaps = {n for n, spec in rt.TOKENS.items() if spec.get("kind") == "gap"}
    referenced = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", section))
    assert not (referenced & gaps), (
        f"report-template.html's live §8 markup references the KNOWN_GAPS token(s) "
        f"{sorted(referenced & gaps)}. A gap token in live markup makes "
        "generate_report.py demand a human-supplied override, so every regeneration of "
        "this section starts by asking someone to supply a figure this repo has "
        "declared it cannot derive")

    text = htmlmod.unescape(re.sub(r"<[^>]+>", " ", section))
    for sentence in re.split(r"\.(?=\s|$)", text):
        capacity = _ADDED_CAPACITY_EARNS_RE.search(sentence)
        priced = _ANY_PRICE_RE.search(sentence) or re.search(r"\{\{[A-Z0-9_]+\}\}",
                                                             sentence)
        assert not (capacity and priced), (
            f"report-template.html's live §8 markup prices added capacity: "
            f"{sentence.strip()!r} carries {capacity.group(0)!r} and "
            f"{priced.group(0)!r}. What one more kW would earn is not derived in this "
            "repo (issue #190), and the template is the route by which the sentence "
            "comes back")
    return ("report-template.html's live §8 markup references no KNOWN_GAPS token and "
            "attaches no price or unresolved token to added capacity")


def case_s0_expansion_cap_matches_the_s8_one():
    """RELATIONSHIP, §0's growth cap against §8's: SAME QUANTITY, TWO TYPED
    COPIES. Neither is recomputed -- the array's DC nameplate lives in
    private/household.yaml and this file runs without the private archive, so
    §8's copy is pinned to its tariff citation by
    case_s8_expansion_verdict_rests_on_the_cap_and_the_grandfathering and this
    pins §0's to §8's.

    §0's "more solar" bullet is now the sentence a reader acts on -- it says
    the cap is what settles the question -- so the cap figure in it is
    load-bearing, and it is the same drift class
    case_s8_specific_yield_matches_the_token_rendered_one exists for: two
    hand-typed copies of one figure with nothing tying them together."""
    m = re.search(r"<li><b>More solar\? No\.</b>.*?</li>", HTML, re.S)
    assert m, "§0's 'More solar? No.' bullet not found in index.html"
    s0 = re.search(r"caps? (?:the )?expansion (?:at|to) ~?([\d.]+) ?kW", m.group(0))
    assert s0, (
        "§0's 'more solar' bullet no longer states the NEM 2.0 growth cap, which is "
        f"what it says settles the question: {m.group(0)!r}")
    s8 = re.search(r"that's <b>~([\d.]+) kW</b>", _s8_section())
    assert s8, (
        "§8 no longer states the NEM 2.0 growth cap in kW, so §0's copy of it has "
        "nothing to be checked against")
    assert s0.group(1) == s8.group(1), (
        f"§0 caps expansion at ~{s0.group(1)} kW while §8 derives ~{s8.group(1)} kW "
        "from the tariff's 10%-or-1-kW rule against this array's nameplate. One of the "
        "two hand-typed copies has drifted")
    return (f"§0's 'more solar' bullet and §8 both cap expansion at ~{s0.group(1)} kW")


def case_the_added_capacity_guard_rejects_the_defect_and_accepts_a_priced_install():
    """_ADDED_CAPACITY_EARNS_RE has to fail on the sentences this issue retired
    and pass ordinary §8 prose. Only the first half was ever driven, and the
    second half is where the guard was wrong: a bare "to add" alternative,
    paired with _ANY_PRICE_RE matching any figure anywhere in the sentence,
    refuses an installed price for anything at all -- a string of panels, a
    battery -- while no marginal-capacity valuation is being made. A guard that
    fails on correct input gets switched off, so both directions are pinned.

    The rejected sentences are the ones index.html really published before
    issue #182, plus the paraphrase the narrowed infinitive still has to
    catch."""
    retired = (
        "A marginal new-panel kWh earns that 22.9¢, not the 10.4¢, because the "
        "daytime load is already covered.",
        "That gives 1,642 kWh/kW/yr × 22.9¢ = $376 per kW per year, and at retrofit "
        "pricing of ~$2.50–3.00/W, a 6.6–8.0 year payback.",
        "Set that against what the cap actually permits: 1.0 kW at the 22.9¢ derived "
        "above earns about $378/yr.",
        "At ~$2.50/W to add capacity, an exported kWh still only fetches 22.9¢.",
        "It costs about $3.00/W to add 1 kW, which earns 22.9¢ a kWh.",
    )
    benign = (
        "≈$8,000 to add a second string, and the roof has room for it.",
        "The right time to add storage is once the panels are paid off, at ~$0.30/W.",
        "Net recoverable value ≤ ~$35/yr against ~$8,000–11,000 to swap 30 "
        "microinverters: a non-starter.",
    )
    for sentence in retired:
        capacity = _ADDED_CAPACITY_EARNS_RE.search(sentence)
        assert capacity and _ANY_PRICE_RE.search(sentence), (
            "the added-capacity guard no longer rejects a sentence that prices a "
            f"marginal kW: {sentence!r}"
            + ("" if capacity else " -- no alternative in _ADDED_CAPACITY_EARNS_RE "
                                   "matches it any more"))
    for sentence in benign:
        capacity = _ADDED_CAPACITY_EARNS_RE.search(sentence)
        assert not (capacity and _ANY_PRICE_RE.search(sentence)), (
            f"the added-capacity guard refuses a correct sentence: {sentence!r} carries "
            f"{capacity.group(0)!r} and a price, but values no marginal kW. A guard "
            "that fails on correct input is worse than no guard: narrow the pattern so "
            "every alternative names added CAPACITY rather than an infinitive")
    return (f"the added-capacity guard rejects all {len(retired)} retired valuations "
            f"and accepts {len(benign)} priced sentences that value no marginal kW")


def case_s8_expansion_verdict_rests_on_the_cap_and_the_grandfathering():
    """issue #182: §8's "no" must stay legible on its own two artifact-backed
    figures -- the NEM 2.0 growth cap and what grandfathering is worth -- so a
    reader can see the recommendation does not depend on any marginal-panel
    valuation.

    RELATIONSHIP, the grandfathering range here against §13's: SAME QUANTITY,
    ONE ARTIFACT, TWO PLACES IN THE PAGE. Both are
    data/nem3_grandfathering.json's two scenario values; §13's subsection is
    pinned by case_nem3_grandfathering_section_matches_the_artifact, and this
    pins §8's copy of them to the same artifact so the two cannot drift.

    The ~1.0 kW cap is NOT recomputed from the nameplate: the array's DC
    nameplate lives in private/household.yaml and this file runs without the
    private archive. It is pinned as published, against the tariff citation
    that authorizes it, which is what a reader would check."""
    section = _s8_section()
    n3 = json.loads((ROOT / "data" / "nem3_grandfathering.json").read_text())
    low = n3["grandfathering_value_range_usd_per_yr"]["low"]
    high = n3["grandfathering_value_range_usd_per_yr"]["high"]
    published = f"{_fmt_usd2(low)}–{_fmt_usd2(high)}/yr"
    assert published in section, (
        f"§8 does not state the grandfathering at risk as {published}, the range "
        "data/nem3_grandfathering.json publishes -- it is half of what the expansion "
        "verdict rests on")

    assert "Special Condition 7(b)" in section, (
        "§8 no longer cites the NEM tariff provision that sets the growth cap, so the "
        "cap it publishes is unsourced")
    cap = re.search(r"that's <b>~([\d.]+) kW</b>", section)
    assert cap, (
        "§8 no longer states the NEM 2.0 growth cap in kW -- the other half of what "
        "the expansion verdict rests on")
    assert float(cap.group(1)) > 0, f"§8's growth cap is {cap.group(1)} kW"
    return (f"§8's verdict rests on the published ~{cap.group(1)} kW NEM 2.0 growth cap "
            f"(Special Condition 7(b)) and the {published} of grandfathering at risk, "
            "both artifact- or tariff-backed")


def case_s8_specific_yield_matches_the_token_rendered_one():
    """RELATIONSHIP, §8's repowering paragraph against §2's verdict line: SAME
    QUANTITY, ONE COMPUTED AND ONE TYPED. §2's is report_tokens' SPECIFIC_YIELD
    rendered into the page; §8's is a human copy of the same figure, which is
    exactly why they can drift apart. Neither is recomputed here, because the
    array nameplate it divides by lives in private/household.yaml and this file
    runs without the private archive."""
    m = re.search(r"<p><b>Repowering with higher-capacity panels: .*?</p>", HTML, re.S)
    assert m, "§8's repowering paragraph not found in index.html"
    typed = re.search(r"([\d,]+) kWh/kW/yr", m.group(0))
    assert typed, "§8's repowering paragraph no longer states the array's specific yield"
    verdict = re.search(r'<p class="verdict">In one sentence: '
                        r'(?:(?!</p>)[^\n])*?([\d,]+) kWh/kW', HTML)
    assert verdict, "§2's verdict line no longer states a specific yield to compare against"
    assert typed.group(1) == verdict.group(1), (
        f"§8's repowering paragraph uses {typed.group(1)} kWh/kW/yr while §2's verdict "
        f"line publishes {verdict.group(1)} kWh/kW from SPECIFIC_YIELD -- the human copy "
        "has drifted from the token-rendered figure")
    return (f"§8's typed {typed.group(1)} kWh/kW/yr specific yield matches the "
            "SPECIFIC_YIELD figure §2's verdict line renders")


def case_s0_more_solar_bullet_keeps_the_two_shares_apart():
    """issue #143 review: the §2/§8 sweep left a live instance of the same
    referent conflation in the BOTTOM LINE, where the report's most-read
    sentence sat outside the guard because the guard reads two named
    paragraphs.

    §0's "more solar" item read "(60% exported at ~10¢)", attaching the
    super-off-peak export credit -- which is what the 10am-2pm slice earns --
    to the all-hours export share. Only the midday slice of those exports
    credits there; the rest leave in off-peak and on-peak hours, which under
    NEM 2.0 credit at UDC+CEA, several times higher (rates.py: 46.2¢ summer
    off-peak, 81.9¢ summer on-peak, against 7.6¢ super-off-peak).

    §0 is basic tier, so the item states the two shares and nothing about the
    export price: what it needs to settle the question is already in the clause
    beside it (the growth cap and the grandfathering it puts at risk) and the
    derivation is §8's job. Same guard as §2 and §8, on the same two
    artifact-derived figures."""
    m = re.search(r"<li><b>More solar\? No\.</b>.*?</li>", HTML, re.S)
    assert m, "§0's 'More solar? No.' bullet not found in index.html"
    item = m.group(0)

    export_pct = _export_share_pct()
    midday_pct = _midday_export_share_pct()

    for value in (f"{export_pct}% exported",
                  f"{midday_pct}% of that in the {_MIDDAY_WINDOW_WORDS[0]} window"):
        assert value in item, f"§0's 'more solar' bullet: {value!r} not found in it"
    _assert_the_two_shares_stay_apart(item, "§0's 'more solar' bullet",
                                      export_pct, midday_pct)
    return (f"§0's 'more solar' bullet states {export_pct}% exported and "
            f"{midday_pct}% of that in the {_MIDDAY_WINDOW_WORDS[0]} window, with no "
            f"time of day and no export price attached to the all-hours share")


# Two-week windows CONSTRUCTED around a named DST Sunday, so the weighting
# guard below keeps testing the correction even if the committed analysis
# window is re-based onto a year that misses one, and a third with no
# transition in it as the control.
_SPRING_PROBE_WINDOW = (dt.date(2026, 3, 1), dt.date(2026, 3, 14))    # 2026-03-08
_FALL_PROBE_WINDOW = (dt.date(2025, 11, 1), dt.date(2025, 11, 14))    # 2025-11-02
_FLAT_PROBE_WINDOW = (dt.date(2026, 1, 1), dt.date(2026, 1, 14))      # no transition


def case_the_midday_share_weights_each_hour_by_its_real_interval_count():
    """issue #143 (Codex review): data/hourly_profile.csv's cells are
    per-interval MEANS, and a window spanning a DST Sunday holds unequal
    hour-of-day buckets, so the 10am-2pm share has to weight each mean by the
    number of intervals its own bucket holds. Summing the means as if the
    buckets matched is arithmetic that is wrong by a little always and could,
    near an integer-rounding boundary, reject a correctly regenerated report.

    Runs on its own constructed windows rather than on the committed one for
    two reasons: the committed window's transitions are an accident of the
    year it covers, and the committed profile exports exactly 0.000 at 01:00
    and 02:00 -- the two hours a DST Sunday resizes -- so on this household's
    data the two routes agree to the last digit and could not tell a correct
    weighting from a wrong one."""
    spring = _dates_between(*_SPRING_PROBE_WINDOW)
    fall = _dates_between(*_FALL_PROBE_WINDOW)
    flat_days = _dates_between(*_FLAT_PROBE_WINDOW)

    # 1. The bucket sizes themselves, straight off rates.expected_day_hours().
    for days, hour, delta, what in ((spring, 2, -4, "spring-forward"),
                                    (fall, 1, +4, "fall-back")):
        counts = _intervals_per_hour_of_day(days)
        flat = 4 * len(days)
        odd = sorted(h for h, n in counts.items() if n != flat)
        assert odd == [hour], (
            f"over a window containing the {what} Sunday the hours whose interval "
            f"count differs from {flat} are {odd}, not [{hour}] -- the weights are "
            f"not being read off rates.expected_day_hours()")
        assert counts[hour] == flat + delta, (
            f"the {what} Sunday should leave hour {hour} with {flat + delta} "
            f"intervals over this window, not {counts[hour]}")
    counts = _intervals_per_hour_of_day(flat_days)
    assert set(counts.values()) == {4 * len(flat_days)}, (
        f"a window with no DST transition should give every hour "
        f"{4 * len(flat_days)} intervals, got {sorted(set(counts.values()))} -- "
        f"the weighting is inventing a difference where the clock has none")

    # 2. The share those sizes produce, against hand arithmetic an
    #    equal-weight route cannot reach. The probe profile is flat at 1.0
    #    everywhere except the ONE hour the transition resizes, which carries
    #    3.0, so the whole disagreement between the two routes is attributable.
    for days, hour, delta, what in ((spring, 2, -4, "spring-forward"),
                                    (fall, 1, +4, "fall-back")):
        profile = dict.fromkeys(range(24), 1.0)
        profile[hour] = 3.0
        flat = 4 * len(days)
        # 4 midday hours at 1.0; 19 more flat hours at 1.0; the odd hour at 3.0.
        by_hand = (4 * flat) / (23 * flat + 3.0 * (flat + delta))
        equal_weighted = 4 / (23 + 3.0)
        assert abs(by_hand - equal_weighted) > 1e-4, (
            f"the {what} probe cannot distinguish the two routes -- it is not "
            f"evidence about the weighting")
        got = _midday_share_of(profile, _intervals_per_hour_of_day(days))
        assert abs(got - by_hand) < 1e-12, (
            f"over a window containing the {what} Sunday the 10am-2pm share of a "
            f"profile carrying 3.0 at hour {hour} is {by_hand:.9f}; "
            f"_midday_share_of returned {got:.9f}. Summing the means unweighted "
            f"would return {equal_weighted:.9f} -- an hour is being counted at a "
            f"bucket size it does not have")

    # 3. What the correction is worth on the committed data, and how much room
    #    the published integer has. Both are reported rather than asserted: the
    #    figure is pinned by the two cases above, and a bound on the delta
    #    would be a bound on the household's night exports, not on this file.
    with (ROOT / "data" / "hourly_profile.csv").open() as fh:
        live = {int(r["dt"]): float(r["exp"]) for r in csv.DictReader(fh)}
    weighted = _midday_share_of(live, _intervals_per_hour_of_day(
        _analysis_window_dates()))
    unweighted = sum(live[h] for h in _MIDDAY_EXPORT_HOURS) / sum(live.values())
    published = _midday_export_share_pct()
    assert round(weighted * 100) == published, (
        f"the weighted 10am-2pm export share is {weighted * 100:.6f}%, which does "
        f"not round to the {published}% §2 publishes")
    to_boundary = 0.5 - abs(weighted * 100 - round(weighted * 100))
    return (f"the 10am-2pm export share weights each hour by its own interval "
            f"count from rates.expected_day_hours() (hour 1 +4 on the fall-back "
            f"Sunday, hour 2 -4 on the spring-forward one, both inside the "
            f"committed window), reproducing hand arithmetic no unweighted sum "
            f"can reach; on the committed profile the correction moves the share "
            f"by {abs(weighted - unweighted) * 100:.6f} pp to {weighted * 100:.4f}%, "
            f"{to_boundary:.4f} pp from the nearest rounding boundary, and §2 "
            f"still publishes {published}%")


# Synthetic shares for the probes below -- the two figures the referent guard
# has to keep apart, held apart by construction so the probes stay readable
# and keep working when the artifacts move. The live figures are pinned by the
# two cases above; what is under test here is the guard itself.
_REFERENT_PROBE_EXPORT_PCT = 60      # exports over production, at ANY hour
_REFERENT_PROBE_MIDDAY_PCT = 63      # the 10am-2pm slice of those exports


# The two sides of a percentage a phrase can be written on. Every probe below
# runs on both, because a guard that reads one side is exactly the guard this
# case shipped with: the defect survives being reordered around the figure.
_PROBE_POSITIONS = ("before", "after")


def _referent_probe_paragraph(timing, attached, position):
    """§2's paragraph with `timing` either ATTACHED to the export share (the
    defect: a time of day predicated of exports-over-production) or sitting in
    the sentence that introduces the measured window (how the report states
    it, and what must stay clean) -- and, either way, written on the side of
    the figure `position` names.

    Written as prose the report could plausibly carry, not as a minimal
    string, so a probe that passes is evidence about the real shape. Some
    members read awkwardly in the slot ("it leaves noon", "In the noon,") --
    detection is what is being probed, not grammar."""
    assert position in _PROBE_POSITIONS, position
    export, midday = _REFERENT_PROBE_EXPORT_PCT, _REFERENT_PROBE_MIDDAY_PCT
    opening = '<p class="small">That last split is the key architectural fact of this '
    if attached:
        # The defect, on each side of the export share: the reordering the
        # tail-only cutter could not see is the "before" arm.
        claim = (f"house: In the {timing}, {export}% of what the array makes leaves "
                 f"as exports."
                 if position == "before" else
                 f"house: {export}% of what the array makes leaves as exports, and "
                 f"it leaves in the {timing}.")
        concentration = "The exports concentrate:"
        window = f"{midday}% of those exported kWh go out in the 10am–2pm window"
    else:
        # Legitimate: the timing sits with the figure that MEASURES it, again
        # on each side of that figure.
        claim = f"house: {export}% of what the array makes leaves as exports."
        concentration = (f"The exports concentrate in the {timing}:"
                         if position == "before" else "The exports concentrate:")
        window = (f"{midday}% of those exported kWh go out in the 10am–2pm window"
                  + ("" if position == "before" else f", in the {timing}"))
    return (f"{opening}{claim} {concentration} {window}, while the EV charged between "
            f"midnight and 6am on 323 of the year's 365 nights.</p>")


def _referent_guard_rejects(para):
    """The offenders _assert_the_two_shares_stay_apart names for `para`, or
    None if it accepts the paragraph."""
    try:
        _assert_the_two_shares_stay_apart(
            para, "probe", _REFERENT_PROBE_EXPORT_PCT, _REFERENT_PROBE_MIDDAY_PCT)
    except AssertionError as e:
        return str(e)
    return None


def case_the_referent_guard_rejects_every_paraphrase_of_the_timing_claim():
    """issue #143 review: the referent guard can only see what its vocabulary
    carries, and its first draft carried two phrases.

    That was enough to catch the defect as originally written and not enough
    to catch it reworded: "60% of what the array makes leaves as exports, and
    it leaves in the middle of the day" attaches a time of day to the
    exports-over-production share exactly as the retired wording did -- and it
    contradicts the next sentence, which measures 63% in the window -- yet the
    guard passed it. A referent rule that a paraphrase walks through is not a
    referent rule.

    So probe MEMBER BY MEMBER, generated off _TIME_OF_DAY_WORDS so a phrase
    added tomorrow is probed tomorrow with no edit here. Each member is probed
    in four shapes -- attached to the export share and in the concentration
    sentence, each written BEFORE the figure and AFTER it. Both axes matter and
    for different reasons. Attached must be rejected while the concentration
    sentence -- where the report legitimately says WHEN the exports leave,
    backed by the measured window -- must be accepted, or a guard could pass
    the first half by flagging everything. And each of those must hold on both
    SIDES of the figure, because the second review of this case found that
    reading only the text after a percentage let the identical defect through
    reordered: "At midday, 60% of what the array makes leaves as exports" says
    what the retired wording said, keeps every substring the live paragraph
    checks look for, and was reported clean.

    Deletion is the one weakening these probes cannot see (a phrase removed
    from the constant removes its own probe), which is what
    _TIME_OF_DAY_VOCABULARY_FLOOR is for."""
    gone = [w for w in _TIME_OF_DAY_VOCABULARY_FLOOR if w not in _TIME_OF_DAY_WORDS]
    assert not gone, (
        f"_TIME_OF_DAY_WORDS no longer carries {gone}, so a paragraph placing the "
        f"export share {gone} is invisible to this guard. Narrowing the vocabulary is "
        "allowed and hiding it is not: say why in _TIME_OF_DAY_VOCABULARY_FLOOR and "
        "drop it there too, in the same commit, where a reviewer reads it")

    for timing in _TIME_OF_DAY_WORDS:
        for position in _PROBE_POSITIONS:
            broken = _referent_probe_paragraph(timing, attached=True, position=position)
            reported = _referent_guard_rejects(broken)
            assert reported, (
                f"the guard accepts {timing!r} written {position} the "
                f"{_REFERENT_PROBE_EXPORT_PCT}% export share: {broken!r}")
            assert timing in reported, (
                f"the guard rejects {timing!r} written {position} the export share but "
                f"does not name it, so the failure does not say which words to move: "
                f"{reported}")
            clean = _referent_probe_paragraph(timing, attached=False, position=position)
            assert _referent_guard_rejects(clean) is None, (
                f"the guard also rejects {timing!r} written {position} the "
                f"{_REFERENT_PROBE_MIDDAY_PCT}% figure in the concentration sentence, "
                f"where the report states it legitimately -- it is flagging the phrase "
                f"rather than its referent: {_referent_guard_rejects(clean)}")

    # The two shapes above, spelled out once each against the wording the
    # report actually retired and the wording it now carries -- the generated
    # probes share a builder with them, and a builder that drifted would take
    # every probe with it.
    retired = ('<p class="small">That last split is the key architectural fact of this '
               "house: 60% of what the array makes leaves as exports, and it leaves in "
               "the middle of the day. 63% of those exported kWh go out in the 10am–2pm "
               "window, while the EV charged between midnight and 6am on 323 of the "
               "year's 365 nights.</p>")
    assert _referent_guard_rejects(retired), (
        "the wording this case exists to reject is accepted again: a midday claim "
        "predicated of the whole export share, contradicted by the 63% beside it")

    # The same defect with the timing moved AHEAD of the figure -- the review
    # finding that widened the cutter. Nothing else about it is unusual: it
    # carries, word for word, every substring §2's live case demands, so the
    # substring checks cannot see it and the referent guard is the only thing
    # standing between this sentence and publication. Asserted against
    # _assert_the_two_shares_stay_apart itself (through _referent_guard_rejects),
    # which is the function case_s2 and case_s8 run on the published paragraphs.
    reordered = ('<p class="small">That last split is the key architectural fact of this '
                 "house: At midday, 60% of what the array makes leaves as exports. 63% "
                 "of those exported kWh go out in the 10am–2pm window, while the EV "
                 "charged between midnight and 6am on 323 of the year's 365 nights.</p>")
    for value in ("60% of what the array makes leaves as exports",
                  "63% of those exported kWh go out in the 10am–2pm window",
                  "the EV charged between midnight and 6am on 323 of the year's 365 "
                  "nights"):
        assert value in reordered, (
            f"this probe no longer states {value!r}, so it is no longer the shape §2's "
            "live case would wave through -- rewrite it to keep every substring that "
            "case demands, or it proves nothing about the gap it exists to hold shut")
    leading = _referent_guard_rejects(reordered)
    assert leading and "midday" in leading, (
        "a time of day written BEFORE the export share is accepted: 'At midday, 60% of "
        "what the array makes leaves as exports' predicates the midday timing of the "
        "whole export share exactly as the retired wording did, and it passes every "
        f"substring §2 checks -- the guard reports {leading!r}")

    published = re.search(r'<p class="small">That last split is the key architectural '
                          r"fact.*?</p>", HTML, re.S)
    assert published, "§2's 'key architectural fact' paragraph not found in index.html"
    assert _referent_guard_rejects(published.group(0)) is None, (
        "§2's published paragraph attaches a time of day to the export share: "
        f"{_referent_guard_rejects(published.group(0))}")
    return (f"the export-share referent guard rejects all {len(_TIME_OF_DAY_WORDS)} of "
            f"its time-of-day phrases attached to the export share on either side of it "
            f"({len(_TIME_OF_DAY_WORDS) * len(_PROBE_POSITIONS)} probes), names the "
            f"offender in each, accepts every one of them on either side of the "
            f"{_REFERENT_PROBE_MIDDAY_PCT}% figure in the concentration sentence, "
            f"rejects the reordered wording that keeps every substring §2 checks, and "
            f"still carries all {len(_TIME_OF_DAY_VOCABULARY_FLOOR)} committed "
            "vocabulary members")


# The four rewordings the sentence-bounded cutter accepted, verbatim from the
# review that found them. The first is the one the branch already rejected and
# is here as the control: the other three are it with the comma changed to a
# period, which is the whole edit that used to republish the defect.
_SENTENCE_SPLIT_REWORDINGS = (
    ("comma (already rejected)",
     "60% of what the array makes leaves as exports, and it leaves in the middle "
     "of the day."),
    ("next sentence, quantified back-reference",
     "60% of what the array makes leaves as exports. Nearly all of it leaves in "
     "the middle of the day."),
    ("next sentence, bare pronoun",
     "60% of what the array makes leaves as exports. It all goes out at midday."),
    ("previous sentence",
     "Almost all of it at midday. 60% of what the array makes leaves as exports."),
)


def _sentence_split_probe(claim):
    """§2's paragraph with its opening claim replaced by `claim`, and every
    other substring §2's live case demands left in place -- so a probe that the
    referent guard accepts is a sentence the report could publish today."""
    return ('<p class="small">That last split is the key architectural fact of this '
            f"house: {claim} The exports concentrate: "
            f"{_REFERENT_PROBE_MIDDAY_PCT}% of those exported kWh go out in the "
            "10am–2pm window, while the EV charged between midnight and 6am on 323 "
            "of the year's 365 nights.</p>")


def case_the_referent_guard_reads_across_a_sentence_boundary():
    """issue #143 review round 3: the clause cutters both stopped at a period,
    so the timing claim only had to move one sentence over and point back.

    Three of the four wordings below were measured against the shipped guard
    and ACCEPTED, each with a listed vocabulary member present -- this is not
    the unlisted-paraphrase limit deferred to #180, it is the guard passing a
    phrase it knows. A comma-to-period edit republished the defect the branch
    exists to retire.

    The rule is that a clause absorbs the RUN of FIGURE-FREE sentences beside
    it: a sentence with no digit in it measures nothing of its own, so a time
    of day in it can only be describing the measurement next door.

    THE RULE THAT WAS NOT ADOPTED is pinned here too, because it is the
    obvious one and someone will propose it again. "Every sentence carrying a
    time-of-day phrase must also carry the midday window or the midday share"
    is measured below against §8's published paragraph, which carries sentences
    that state neither and are correct -- so that rule would fail the live
    report, and the counterexample is asserted rather than remembered."""
    for label, claim in _SENTENCE_SPLIT_REWORDINGS:
        para = _sentence_split_probe(claim)
        assert f"{_REFERENT_PROBE_EXPORT_PCT}% of what the array makes leaves as " \
               "exports" in para, (
            f"the {label} probe no longer states the export share in the words §2's "
            f"live case demands, so it is not the sentence that walked through")
        reported = _referent_guard_rejects(para)
        assert reported, f"the guard accepts the {label} reworking: {para!r}"
        assert "middle of the day" in reported or "midday" in reported, (
            f"the guard rejects the {label} reworking without naming the words to "
            f"move: {reported}")

    # Both shapes, over the whole vocabulary rather than the two phrases the
    # four literals happen to use -- generated so a phrase added tomorrow is
    # probed tomorrow, the same discipline the sibling case follows.
    for timing in _TIME_OF_DAY_WORDS:
        for label, claim in (
                ("after", f"{_REFERENT_PROBE_EXPORT_PCT}% of what the array makes "
                          f"leaves as exports. Nearly all of it leaves in the {timing}."),
                ("before", f"Nearly all of it in the {timing}. "
                           f"{_REFERENT_PROBE_EXPORT_PCT}% of what the array makes "
                           f"leaves as exports.")):
            reported = _referent_guard_rejects(_sentence_split_probe(claim))
            assert reported and timing in reported, (
                f"a bare sentence carrying {timing!r} written {label} the export "
                f"share is accepted, or rejected without naming it: {reported}")

    # THE BOUNDARY, from the other side: a neighbouring sentence that carries
    # its OWN figures is making its own claim and must still be accepted, or
    # the rule is just "no time of day anywhere near the share" and §8 cannot
    # be written. This is §8's real next sentence.
    priced = _sentence_split_probe(
        f"{_REFERENT_PROBE_EXPORT_PCT}% of what the array makes leaves as exports. "
        "Since the 2026 TOU change those midday exports credit at only ~10.4¢/kWh.")
    assert _referent_guard_rejects(priced) is None, (
        "a neighbouring sentence that states its own measured figures is being read "
        "as a back-reference to the export share: "
        f"{_referent_guard_rejects(priced)}")

    # The rule that was NOT adopted, measured on the live §8 paragraph.
    live = re.search(r"<p><b>More panels: .*?</p>", HTML, re.S)
    assert live, "§8's 'more panels' paragraph not found in index.html"
    s8 = live.group(0)
    midday_pct = _midday_export_share_pct()
    counterexamples = [
        s8[a:b] for a, b in _sentence_spans(s8)
        if _time_of_day_words_in(s8[a:b])
        and not any(w.casefold() in s8[a:b].casefold() for w in _MIDDAY_WINDOW_WORDS)
        and f"{midday_pct}%" not in s8[a:b]]
    assert counterexamples, (
        "§8 no longer carries a sentence that names a time of day without restating "
        "the window or the midday share, so the sentence-level rule this file "
        "rejected may now be available -- re-measure it before keeping the "
        "figure-free-run rule, and delete this assertion if it is adopted")

    for where, pattern in (("§2", r'<p class="small">That last split is the key '
                                  r"architectural fact.*?</p>"),
                           ("§8", r"<p><b>More panels: .*?</p>"),
                           ("§0", r"<li><b>More solar\? No\.</b>.*?</li>")):
        m = re.search(pattern, HTML, re.S)
        assert m, f"{where}'s guarded passage not found in index.html"
        assert _assert_the_two_shares_stay_apart(
            m.group(0), where, _export_share_pct(), midday_pct) is None
    return (f"the referent guard now reads the run of figure-free sentences beside a "
            f"figure, rejecting all {len(_SENTENCE_SPLIT_REWORDINGS)} sentence-split "
            f"rewordings and every one of {len(_TIME_OF_DAY_WORDS)} vocabulary phrases "
            f"moved into a sentence of its own on either side "
            f"({len(_TIME_OF_DAY_WORDS) * 2} probes), while still accepting a "
            f"neighbouring sentence that carries its own figures and all three "
            f"published passages; §8 carries {len(counterexamples)} sentence(s) that "
            f"rule out the narrower sentence-level rule")


def case_the_referent_guard_reads_a_percentage_only_when_it_is_unambiguous():
    """issue #143 review round 3: the guard matches every occurrence of a
    figure and has no idea what any of them measures.

    §8 already carries "60-87¢" in a sentence about battery arbitrage, one
    keystroke from a second "60%" that this guard would have read as the export
    share and rejected as a referent error, with a message pointing the writer
    at the arbitrage sentence. Unrelated figures that collide are not the
    guard's to adjudicate; failing accurately is."""
    ambiguous = _sentence_split_probe(
        f"{_REFERENT_PROBE_EXPORT_PCT}% of what the array makes leaves as exports. A "
        f"battery would monetize {_REFERENT_PROBE_EXPORT_PCT}% of the midday surplus "
        "instead.")
    reported = _referent_guard_rejects(ambiguous)
    assert reported, (
        "two occurrences of the export share in one paragraph are accepted, so the "
        "guard is judging clauses it cannot attribute")
    assert "appears 2 times" in reported, (
        f"a paragraph carrying the export share twice fails for the wrong reason: "
        f"{reported}. It should say the figure is ambiguous, not name a referent "
        f"error in a sentence that may be about something else entirely")
    assert "a different quantity" not in reported, (
        f"the ambiguous paragraph is reported as a referent error: {reported}")

    # And the same paragraph with the second occurrence written any other way
    # is fine -- what is banned is the collision, not the sentence.
    fine = _sentence_split_probe(
        f"{_REFERENT_PROBE_EXPORT_PCT}% of what the array makes leaves as exports. A "
        "battery would monetize three fifths of that surplus instead.")
    assert _referent_guard_rejects(fine) is None, (
        f"the uniqueness rule is rejecting more than a digit collision: "
        f"{_referent_guard_rejects(fine)}")
    return (f"the referent guard refuses a paragraph carrying "
            f"{_REFERENT_PROBE_EXPORT_PCT}% twice, naming the collision rather than "
            f"reporting a referent error against whichever sentence it landed in, and "
            f"accepts the same paragraph once the second figure is written in words")


def case_the_s2_verdict_locator_tolerates_inline_markup():
    """issue #143 review round 3: _S2_VERDICT_MIDDAY_RE required the span
    between "In one sentence: " and the percentage to be TAG-FREE.

    S2_VERDICT is a token-rendered sentence in a report whose prose carries
    <b>, <i>, <a href="#sN"> and <span class="pill"> everywhere. Any one of
    them written ahead of the figure broke the search, and the case then
    reported "§2's verdict line no longer states a 10am-2pm share of exports"
    -- a formatting change misdiagnosed as a deleted claim, which sends the
    reader to look for prose that is still there."""
    live = _S2_VERDICT_MIDDAY_RE.search(HTML)
    assert live, ("§2's verdict line no longer states a "
                  f"{_MIDDAY_WINDOW_WORDS[0]} share of exports")

    window = _MIDDAY_WINDOW_WORDS[0]
    for label, marked in (
            ("bold", f'<p class="verdict">In one sentence: the array produced <b>16,502 '
                     f"kWh</b>, but {live.group(1)}% of its exports leave in the "
                     f"{window} window.</p>"),
            ("section link", f'<p class="verdict">In one sentence: see <a href="#s8">§8'
                             f"</a> -- {live.group(1)}% of its exports leave in the "
                             f"{window} window.</p>"),
            ("evidence pill", f'<p class="verdict">In one sentence: <span class="pill">'
                              f"MODELED</span> {live.group(1)}% of its exports leave in "
                              f"the {window} window.</p>")):
        m = _S2_VERDICT_MIDDAY_RE.search(marked)
        assert m, (f"a {label} tag written between 'In one sentence: ' and the figure "
                   f"hides §2's verdict line from the locator: {marked!r}")
        assert int(m.group(1)) == int(live.group(1)), (
            f"the {label} probe resolves to {m.group(1)}%, not {live.group(1)}%")

    # It must still stop at the paragraph it is reading. A verdict line with no
    # midday claim, followed by one that has it, must not be spliced into a
    # match that spans both -- that would let a deleted claim be satisfied by
    # the next section's.
    spliced = ('<p class="verdict">In one sentence: the rate plan is right.</p>\n'
               f'<p class="verdict">Not a lead-in: {live.group(1)}% of its exports '
               f"leave in the {window} window.</p>")
    assert _S2_VERDICT_MIDDAY_RE.search(spliced) is None, (
        "the locator matched across two verdict paragraphs, so a §2 line that lost "
        "its midday claim can be satisfied by a later one")
    return (f"§2's verdict locator finds the {window} share through inline <b>, <a> "
            f"and <span> markup and still refuses to match across a paragraph "
            f"boundary")


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
    """{section id: set of the conclusion mechanisms a section OPENS with},
    parsed from the document rather than read off a hardcoded id list, so a
    section added later shows up here (with an empty set) instead of slipping
    through.

    This is a set of KINDS and it only ever looks at what immediately follows
    the <h2>, which is the "opens with" half of the invariant. It cannot count
    conclusion lines: three stacked <p class="verdict"> paragraphs collapse to
    {"verdict-line"} here, exactly like one. _conclusion_line_counts below is
    the half that counts.

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


# A conclusion LINE is an element, and "exactly one per section" is a count of
# ELEMENTS -- not a count of mechanism KINDS. Reading len() off the set
# _conclusion_mechanisms returns only ever caught a section MIXING two
# mechanisms: a section carrying the same mechanism twice, or carrying a
# second conclusion line further down where the opening-tag scan cannot see it
# at all, satisfied a guard whose own message said "exactly one per section".
# The two helpers below count the elements themselves over the whole span
# between one <h2> and the next.
#
# Matching is by CLASS rather than by the exact literal opening tag the
# mechanism helpers require, so a second conclusion line cannot duck the count
# by carrying an extra attribute.
_CONCLUSION_ELEMENT_RES = {
    "verdict-line": re.compile(r'<p\b[^>]*\bclass="[^"]*\bverdict\b[^"]*"'),
    "summary-teaser": re.compile(r'<span\b[^>]*\bclass="[^"]*\bteaser\b[^"]*"'),
}


def _conclusion_line_counts(doc):
    """{section id: {mechanism: how many conclusion-line ELEMENTS the section
    carries}}, counted over the whole span from each <h2> to the next.

    The in-heading mechanism contributes 1 to a section that uses it: a
    heading holds at most one verdict slot, which _in_heading_verdict_scaffold
    enforces against the template directly rather than assuming it."""
    scaffold = _in_heading_verdict_scaffold()
    heads = list(_SECTION_H2_RE.finditer(doc))
    counts = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(doc)
        span = doc[m.end():end]
        per_mech = {mech: len(rx.findall(span))
                    for mech, rx in _CONCLUSION_ELEMENT_RES.items()}
        per_mech["in-heading"] = 1 if m.group(1) in scaffold else 0
        counts[m.group(1)] = per_mech
    return counts


def _conclusion_lines_outside_sections(doc):
    """How many conclusion-line elements sit ahead of the first <h2>, where no
    section owns them -- the one region the per-section spans above cannot
    see, and so the one place a second conclusion line could be parked."""
    first = _SECTION_H2_RE.search(doc)
    head = doc if first is None else doc[:first.start()]
    return sum(len(rx.findall(head)) for rx in _CONCLUSION_ELEMENT_RES.values())


# Sections allowed to render an <h2> verdict that does NOT say what their own
# token says. EMPTY, and it stays empty: every in-heading verdict in the report
# now carries its token's text.
#
# It held s4 and s8 while issue #141 was open. The case asserts in BOTH
# directions, so an id parked here after its section starts agreeing fails just
# as loudly as a section drifting from its token -- which is how this set got
# emptied rather than forgotten.
_HEADING_VERDICT_TOKEN_DIVERGENCE = set()


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

    # Exactly one, in BOTH files, with no exceptions. Two assertions, because
    # the invariant is two claims: the section OPENS with a conclusion line
    # (the mechanism scan, which only reads what follows the <h2>), and the
    # section carries exactly ONE of them (the element count, over the whole
    # span to the next <h2>). Either alone is passable while the other is
    # violated -- a section whose only .verdict sits at the bottom counts 1
    # but opens with nothing, and a section opening with a .verdict followed
    # by two more opens correctly but carries three.
    for label, doc, mechanisms in (("index.html", HTML, index_mech),
                                   ("report-template.html", TEMPLATE_HTML, template_mech)):
        silent = sorted(sid for sid, m in mechanisms.items() if not m)
        assert not silent, (
            f"{label} sections with no conclusion line at all: {silent} -- every h2 needs "
            'an in-heading verdict, a <summary> .teaser, or a <p class="verdict">')
        line_counts = _conclusion_line_counts(doc)
        extra = {sid: {mech: n for mech, n in per_mech.items() if n}
                 for sid, per_mech in line_counts.items() if sum(per_mech.values()) > 1}
        assert not extra, (
            f"{label} sections carrying MORE than one conclusion line: {extra} -- exactly "
            "one per section (CLAUDE.md section 10). This counts ELEMENTS, not mechanism "
            'kinds: two <p class="verdict"> paragraphs in one section are two conclusion '
            "lines even though they share a mechanism")
        stray = _conclusion_lines_outside_sections(doc)
        assert stray == 0, (
            f"{label} carries {stray} conclusion-line element(s) ahead of its first <h2>, "
            "where no section owns them -- the per-section count above cannot see them, so "
            "a duplicate parked there would go unnoticed")

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
        "the divergence allowance names sections that no longer use the "
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
        f"§{', §'.join(healed)} AGREES with its token, so delete the id from "
        "_HEADING_VERDICT_TOKEN_DIVERGENCE (the set holds only sections that do NOT, "
        "and is empty)")

    counts = {}
    for mech in index_mech.values():
        counts[next(iter(mech))] = counts.get(next(iter(mech)), 0) + 1
    return (f"all {len(index_mech)} h2 sections in both files OPEN with a conclusion line "
            f"and carry exactly one conclusion-line element anywhere in the section, by "
            f"the same mechanism ({counts}); {len(agreeing)} in-heading verdict(s) "
            f"match their token, {len(diverged)} diverge from it, "
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
    # Issue #131 review round 6: the floor here used to be `len(lines) >= 8`, a
    # hardcoded count of ONE mechanism's elements. It fired first on any
    # rearrangement of the tier and so reported a .verdict shortfall for
    # defects that were nothing of the kind -- a section carrying its
    # conclusion a different legal way, or carrying none at all. The floor is
    # on the SECTIONS, which is what "the basic tier" means; the completeness
    # count below then ties one measured lead sentence to each of them.
    basic_sections = [sid for sid, _ in _SECTION_H2_RE.findall(basic)]
    assert len(basic_sections) >= 9, (
        f"only {len(basic_sections)} basic-tier <h2> sections parsed ({basic_sections}) -- "
        "sections 0-7 and the Monday appendix are all basic-tier, so either the parser or "
        "the advanced-tier boundary broke")
    lines = re.findall(r'<p class="verdict">(.*?)</p>', basic, re.S)
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

    # Issue #131 review round 6: the comment above says the tier boundary is
    # read off the document "so a section moved across it is scoped correctly
    # without editing this test", and this case measured only two of the three
    # conclusion mechanisms. A <summary> .teaser section moved into the basic
    # tier would have gone unmeasured while the summary line still reported the
    # tier covered. The third mechanism is measured too, and the count below
    # proves the coverage instead of assuming it: one lead sentence per
    # basic-tier <h2>, which the sibling case above has already established is
    # exactly one conclusion line per section.
    teasers = re.findall(r'<span class="teaser">(.*?)</span>', basic, re.S)
    for raw in teasers:
        text = htmlmod.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        over.append(_over_the_density_cap(".teaser", text))

    measured = len(lines) + len(headings) + len(teasers)
    assert measured == len(basic_sections), (
        f"the density cap measured {measured} lead sentences across {len(basic_sections)} "
        f"basic-tier sections ({basic_sections}) -- every basic-tier section must "
        "contribute exactly one, or a section's lead is going unchecked")

    over = [o for o in over if o]
    assert not over, (
        "basic-tier lead sentences over CLAUDE.md section 10's density cap "
        f"(35 words, 1 aside): {over}")
    return (f"every one of the {len(basic_sections)} basic-tier sections' lead sentences "
            f"({len(lines)} .verdict, {len(headings)} in-heading, {len(teasers)} .teaser) "
            "lead in 35 words or fewer with at most one aside")


def case_the_two_structural_guards_reject_the_defects_they_exist_to_catch():
    """The two cases above pass on today's report whether or not their logic
    actually checks anything -- that is exactly how every defect fixed in them
    survived review. This drives their helpers with inputs that HAVE the
    defect, so reintroducing one fails here on real output instead of waiting
    for a future report to be wrong in the right way.

    It covers the helpers, not the cases: an assertion the cases themselves
    make but no helper carries is invisible here, so it belongs in the case
    that makes it."""

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

    # 1b. "Exactly one conclusion line" is a count of ELEMENTS. Reading it off
    #     the mechanism SET only ever caught a section mixing two mechanisms:
    #     a section repeating one mechanism, or parking a second conclusion
    #     line further down where the opening-tag scan cannot see it, passed a
    #     guard whose message said "exactly one per section".
    one_line = '<h2 id="sX">X · Title</h2>\n<p class="verdict">In one sentence: it works.</p>'
    same_mech_twice = (one_line + '\n<p class="verdict">In one sentence: it also works.</p>')
    lower_down = (one_line + "\n<p>Body copy.</p>\n"
                  '<p class="verdict">In one sentence: a second conclusion.</p>')
    mixed_mechs = (one_line + '\n<span class="teaser">A second conclusion.</span>')
    attr_dodge = (one_line + '\n<p id="x" class="verdict small">A second conclusion.</p>')
    assert sum(_conclusion_line_counts(one_line)["sX"].values()) == 1, \
        "a section with one .verdict line no longer counts as carrying one conclusion line"
    for label, doc in (("the same mechanism twice", same_mech_twice),
                       ("a second .verdict lower down the section", lower_down),
                       ("two different mechanisms", mixed_mechs),
                       ("a second .verdict carrying extra attributes", attr_dodge)):
        assert _conclusion_mechanisms(doc, rendered=True)["sX"] == {"verdict-line"}, (
            f"{label}: the mechanism set is a set of KINDS and must stay one -- if it grew, "
            "the count assertion below is measuring something else")
        assert sum(_conclusion_line_counts(doc)["sX"].values()) == 2, (
            f"{label} is not counted as two conclusion lines, so the "
            '"exactly one per section" assertion cannot see it')
    # ... and a conclusion line parked ahead of every <h2> belongs to no
    # section's span, so it needs its own count or it is invisible.
    assert _conclusion_lines_outside_sections(
        '<p class="verdict">Orphan.</p>\n' + one_line) == 1, \
        "a conclusion line ahead of the first <h2> is not counted as unowned"
    assert _conclusion_lines_outside_sections(one_line) == 0, \
        "a section's own conclusion line is being counted as unowned"

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
    return ("the conclusion-presence, one-conclusion-line-per-section, density-cap and "
            "heading/token-agreement guards each reject the defect they exist to catch, "
            "and only a missing private archive can drop a token from the agreement check")


# ---------------------------------------------------------------------------
# Cross-document figure pins (issues #165, #166, #176).
#
# Each of these three ties a number index.html publishes to the artifact that
# derives it AND to the second document that restates it, so neither side can
# move alone. All three existed as live disagreements: a stale glossary ratio,
# two unnamed derivations of whole-home load, and a naive degradation band the
# report's own cited estimators fell outside of.
# ---------------------------------------------------------------------------

GLOSSARY = (ROOT / "GLOSSARY.md").read_text()

# "~2.5× cleaner" / "about 2.5× cleaner" -- the ratio, wherever it is stated.
_CARBON_RATIO_RE = re.compile(r"([\d.]+)× cleaner")
# The two whole-home-load figures §1 reconciles, and the gap it states.
_LOAD_BALANCE_RE = re.compile(r"Whole-home load is <b>([\d,]+) kWh/yr</b>")
_LOAD_CT_RE = re.compile(r"totals ([\d,]+) kWh: ([\d.]+)% below the energy balance")
# The naive degradation band, at both of the places §9 prints it.
_NAIVE_BAND_RES = (
    re.compile(r"naïve fit reads ~([\d.]+)–([\d.]+)%/yr"),
    re.compile(r"~([\d.]+)–([\d.]+)%/yr naïve band"),
)


def case_midday_vs_overnight_carbon_ratio_is_one_comparison_in_both_documents():
    """issue #176: GLOSSARY.md said midday grid power is ~2.2x cleaner than
    overnight while index.html §0 said ~2.5x, and nothing tied either to an
    artifact.

    They were the SAME comparison -- window mean against window mean -- on two
    generations of the carbon study: 279.0/125.1 = 2.2 on the retired 4-sample-
    day basis in data/carbon_results.json, 270.1/109.1 = 2.5 on the 364-day
    basis in data/carbon_fullyear_results.json. The glossary carried the stale
    one.

    The 2.5x figure also carried a wrong LABEL on the page: it was published as
    a point comparison ("cleaner at noon than at 3am"), which those same hourly
    means put at 2.6x (279.8 at 03h against 107.3 at 12h), not 2.5x. So this
    case pins three things at once -- the ratio in each document, the window
    means it comes from, and that each document names the two WINDOWS rather
    than two hours, since the number is a window average and the point
    comparison is a different (and differently-valued) statistic."""
    cf_path = ROOT / "data" / "carbon_fullyear_results.json"
    assert cf_path.exists(), f"{cf_path} is committed public data and must exist"
    wm = json.loads(cf_path.read_text())["intensity_kg_per_mwh"]["window_means_annual"]
    overnight, midday = wm["sop_overnight_00_06"], wm["solar_midday_10_14"]
    expected = f"{overnight / midday:.1f}"

    m = re.search(r"<li><b>Exploit the 10am–2pm weekday window.*?</li>", HTML, re.S)
    assert m, "§0's 10am-2pm window bullet not found in index.html"
    bullet = m.group(0)
    entry = next((ln for ln in GLOSSARY.splitlines()
                  if ln.startswith("**Grid carbon intensity**")), None)
    assert entry, "GLOSSARY.md has no 'Grid carbon intensity' entry to check"

    for where, text in (("index.html §0", bullet), ("GLOSSARY.md", entry)):
        hit = _CARBON_RATIO_RE.search(text)
        assert hit, (f"{where} no longer states a 'N× cleaner' midday/overnight ratio -- "
                     "that figure is what this case pins to "
                     "carbon_fullyear_results.json's window means, so it cannot be "
                     "checked at all if the document stops publishing it")
        assert hit.group(1) == expected, (
            f"{where} publishes {hit.group(1)}× cleaner, but "
            f"carbon_fullyear_results.json's window means give "
            f"{overnight}/{midday} = {expected}× -- the two documents and the artifact "
            "must state one ratio")
        for window in ("10am–2pm", "midnight–6am"):
            assert window in text or window.replace("–", "-") in text, (
                f"{where} states the ratio without naming the {window} window it "
                "averages over; the same artifact's hourly means make the noon-vs-3am "
                "POINT comparison a different number "
                f"({json.loads(cf_path.read_text())['intensity_kg_per_mwh']['annual_avg_by_hour'][3]}"
                " against "
                f"{json.loads(cf_path.read_text())['intensity_kg_per_mwh']['annual_avg_by_hour'][12]}"
                " kg/MWh), so which comparison this is has to be said")

    for value in (f"{midday}", f"{overnight}"):
        assert value in entry, (
            f"GLOSSARY.md's grid-carbon entry no longer cites the {value} kg CO₂/MWh "
            "window mean it takes the ratio from, so the ratio is back to hand arithmetic")
    return (f"index.html §0 and GLOSSARY.md both state the midday/overnight ratio as "
            f"{expected}×, over the named 10am–2pm and midnight–6am windows, from "
            f"carbon_fullyear_results.json's {overnight}/{midday} kg CO₂/MWh window means")


def case_whole_home_load_names_both_derivations_and_states_their_gap():
    """issue #166: ANNUAL_LOAD_KWH resolved to 29,914 while index.html §2
    published 29,857, and neither derivation was named.

    They are two independent measurements of one quantity. 29,914 is the
    utility-meter energy balance -- imports + (production − exports), every
    term from a committed artifact (data/report_data.json:totals and
    data/enphase_daily_production.csv). 29,857 is the Enphase consumption CT's
    own annual total, which no committed artifact carries; it lives in the
    gitignored SAM 8760 export. Both are right, and their 0.2% agreement is
    evidence, so the report publishes the artifact-backed one and states the
    other as corroboration.

    This pins the published figure to the token that derives it, the balance's
    three terms to their artifacts, and the stated gap to the two figures it
    is the gap between -- so re-basing either meter without rewriting the
    reconciliation fails here."""
    rt = _report_tokens()
    rendered = rt.resolve_token("ANNUAL_LOAD_KWH")

    balance = _LOAD_BALANCE_RE.search(HTML)
    assert balance, ("§1 no longer states 'Whole-home load is <b>N kWh/yr</b>' -- that "
                     "figure is what this case pins ANNUAL_LOAD_KWH to")
    assert balance.group(1) == rendered, (
        f"§1 publishes a whole-home load of {balance.group(1)} kWh/yr but "
        f"ANNUAL_LOAD_KWH renders {rendered} from data/report_data.json:totals and "
        "data/enphase_daily_production.csv -- the page and the token that derives it "
        "state different loads")
    assert f"{rendered} kWh/yr load" in HTML, (
        f"§2's 'covers N% of the home's ... kWh/yr load' bullet does not carry "
        f"{rendered}, the figure §1 and ANNUAL_LOAD_KWH agree on -- the two sections "
        "would publish two different whole-home loads")

    imp, exp = RD["totals"]["imp"], RD["totals"]["exp"]
    production = rt.resolve_token("ANNUAL_PRODUCTION_KWH")
    terms = f"({imp:,} + {production} − {exp:,})"
    assert terms in HTML, (
        f"§1 does not show the energy balance as {terms}; those three terms are what "
        "makes the published load traceable rather than asserted")

    ct = _LOAD_CT_RE.search(HTML)
    assert ct, ("§1 no longer states the consumption CT's own total and how far it sits "
                "from the energy balance -- that reconciliation is the point of "
                "publishing two derivations")
    ct_kwh = float(ct.group(1).replace(",", ""))
    balance_kwh = float(rendered.replace(",", ""))
    expected_gap = f"{abs(balance_kwh - ct_kwh) / balance_kwh * 100:.1f}"
    assert ct.group(2) == expected_gap, (
        f"§1 says the consumption CT reads {ct.group(2)}% below the energy balance, but "
        f"{ct.group(1)} against {rendered} is {expected_gap}% -- the stated agreement "
        "does not match the two figures it is between")
    return (f"§1 and §2 publish one whole-home load ({rendered} kWh/yr, the "
            f"ANNUAL_LOAD_KWH energy balance {terms}), with the consumption CT's "
            f"{ct.group(1)} kWh named as corroboration {expected_gap}% away")


def case_degradation_naive_band_contains_every_estimator_it_is_built_from():
    """issue #165: DEGRADATION_NAIVE_RANGE resolved to 1.3-1.8%/yr while §9
    published ~1.3-1.7%/yr -- and then asserted that OLS −1.77%/yr "lands
    inside" that band, which it does not.

    The band IS the span of gross_import_decomposition.json's three
    estimators, so containment is a property of the artifact, not a claim the
    prose gets to make independently. This case checks it arithmetically: both
    printed copies of the band must be the token's own endpoints, and every
    estimator the artifact carries must fall inside them, to within the half
    printed digit the endpoints are rounded to. The published −1.77 failed
    that (1.765 > 1.7 + 0.05); the artifact's own −1.76 does not."""
    gd_path = ROOT / "data" / "gross_import_decomposition.json"
    assert gd_path.exists(), f"{gd_path} is committed public data and must exist"
    deg = json.loads(gd_path.read_text())["degradation"]
    estimators = {"OLS": deg["ols_pct_per_yr"], "CAGR": deg["cagr_pct_per_yr"],
                  "Theil-Sen": deg["theil_sen_pct_per_yr"]}

    rt = _report_tokens()
    rendered = rt.resolve_token("DEGRADATION_NAIVE_RANGE")
    token_band = re.search(r"([\d.]+)–([\d.]+)%/yr", rendered)
    assert token_band, (
        f"DEGRADATION_NAIVE_RANGE renders {rendered!r}, which states no numeric band -- "
        "the estimators no longer agree on a direction, and §9's prose has to say so "
        "instead of printing a range this case can check")
    lo, hi = (float(token_band.group(1)), float(token_band.group(2)))

    for pattern in _NAIVE_BAND_RES:
        hit = pattern.search(HTML)
        assert hit, (f"§9 no longer prints the naive band in the form {pattern.pattern!r} "
                     "-- both copies of that band are what this case pins to "
                     "DEGRADATION_NAIVE_RANGE")
        assert (hit.group(1), hit.group(2)) == (token_band.group(1), token_band.group(2)), (
            f"§9 publishes a naive band of {hit.group(1)}–{hit.group(2)}%/yr but "
            f"DEGRADATION_NAIVE_RANGE renders {rendered!r} from "
            "gross_import_decomposition.json's three estimators")

    for name, value in estimators.items():
        assert lo - 0.05 <= abs(value) <= hi + 0.05, (
            f"§9 states the naive band as {lo}–{hi}%/yr and calls it the span of its "
            f"three estimators, but {name} is {value}%/yr, outside that band -- the "
            "published containment claim is arithmetically false")
        printed = f"{abs(value):.2f}%/yr"
        assert printed in HTML, (
            f"§9 no longer prints {name} as {printed}; the three estimator figures the "
            "band is built from have to be the artifact's own, not restated by hand")
    return (f"§9's naive band ({lo}–{hi}%/yr, both copies) is DEGRADATION_NAIVE_RANGE's "
            "own span, and each of "
            + ", ".join(f"{n} {v}%/yr" for n, v in estimators.items())
            + " falls inside it")


def case_glossary_figures_match_the_artifacts_that_derive_them():
    """issues #176 AC5/AC6 and #216: GLOSSARY.md is a public, linked document
    that no artifact-agreement case covered, so it is where stale figures
    accumulated -- #140 found a retired phantom figure surviving there after
    index.html, TECHNICAL.md and the tests had all been swept, and #176 found
    a carbon ratio two generations old.

    A one-off sweep only holds until the next regeneration, so this pins the
    glossary's artifact-derived figures the way the report's own are pinned:
    each entry names the artifact path or the token that derives it, and the
    string has to be present in that entry. Four were wrong when this was
    written -- the optimality gap ($217.24 against the artifact's $217.39),
    both electrification-dividend figures ($3,230/$4,440 against $3,191/
    $4,429), and the gas total (~342 against ~343 therms).

    Figures deliberately NOT pinned here, because no committed artifact
    carries them: the SAIDI/SAIFI outage-hours band, the $1.16/kWh Reduce-
    Your-Use surcharge, the $2/kWh ELRP rate, and the external constants
    (Climate Credit, ITC, SGIP, EV pack sizes, HPWH efficiency). Those are
    cited to public sources in the entries themselves and would need a
    generator before a test could mean anything.

    Seven of the pins are token-derived, and some of those tokens need
    private/household.yaml, which CI does not have. They are resolved up
    front rather than inline in the pins list, for three reasons: an
    unresolvable token drops its pin WHOLE (several interpolate the rendered
    string into the figure, so a None would be asserted as the literal
    "None" -- a wrong figure rather than an absent one); every pin that does
    NOT need the archive still runs; and the dropped token names go into the
    summary line, so a reader can see coverage shrank and by what."""
    rt = _report_tokens()
    if str(ROOT / "analysis") not in sys.path:
        sys.path.insert(0, str(ROOT / "analysis"))
    import household
    archive, loader = household.PATH.is_file(), household.__file__
    nem3 = json.loads((ROOT / "data" / "nem3_grandfathering.json").read_text())
    pfd = json.loads((ROOT / "data" / "perfect_foresight_dispatch.json").read_text())
    dsgs = json.loads((ROOT / "data" / "dsgs_vpp_backtest.json").read_text())
    curve = json.loads((ROOT / "data" / "battery_sizing_curve.json").read_text())
    ext = json.loads((ROOT / "data" / "extended_results.json").read_text())
    disp = DISPATCH
    gap = pfd["greedy_comparison"]
    div = ext["electrification_dividend"]
    therms = sum(float(row["therms"]) for row in
                 csv.DictReader((ROOT / "data" / "gas_monthly_therms.csv")
                                .read_text().splitlines()))

    # BaseException, not Exception: resolve_token's fail-closed signal is
    # SystemExit, which is NOT an Exception subclass. Building these inline in
    # the pins list let that SystemExit escape the runner -- which only catches
    # AssertionError -- and end the whole run at this case, with no FAIL line
    # and no tally, taking every later case with it. Catching Exception here
    # would reproduce exactly that. A skip is pardoned only when it is the
    # archive that is missing (_missing_archive_exit, same test the heading-
    # verdict resolver uses): with the archive present every token must still
    # resolve, so a genuinely broken token fails this case instead of quietly
    # deleting its own coverage.
    resolved, skipped = {}, set()
    for token in ("CAPACITY_FACTOR", "EXPORTED_SHARE", "SOILING_RATE_RANGE",
                  "SPECIFIC_YIELD", "DEGRADATION_NAIVE_RANGE",
                  "NIGHT_FLOOR_MEDIAN", "NIGHT_FLOOR_ANNUAL_COST"):
        try:
            resolved[token] = rt.resolve_token(token)
        except BaseException as e:                # noqa: BLE001 - that is the point
            assert _missing_archive_exit(e, archive, loader), (
                f"report_tokens could not resolve {token}, and NOT because this checkout "
                f"lacks the private archive (present: {archive}) -- the glossary figure "
                f"cannot be checked against a token that is itself broken: "
                f"{type(e).__name__}: {e}")
            skipped.add(token)

    # (glossary entry, figure as it must appear, what derives it)
    pins = [
        ("Grandfathering",
         f"${nem3['grandfathering_value_range_usd_per_yr']['low']:,.2f}–"
         f"${nem3['grandfathering_value_range_usd_per_yr']['high']:,.2f} per year",
         "nem3_grandfathering.json:grandfathering_value_range_usd_per_yr"),
        ("Therm", f"~{therms:,.0f} therms/yr", "data/gas_monthly_therms.csv, summed"),
        ("Dispatch policy",
         f"~${round(disp['pw3']['greedy']['save'] - disp['pw3']['evening']['save']):,}/yr more",
         "battery_dispatch_policies.json: greedy.save − evening.save"),
        ("DSGS",
         f"**${dsgs['per_aggregation_sensitivity']['net_usd_min']:,.0f}–"
         f"${dsgs['per_aggregation_sensitivity']['net_usd_max']:,.0f}**",
         "dsgs_vpp_backtest.json:per_aggregation_sensitivity"),
        ("DSGS", f"${dsgs['revenue']['reserve_20pct']['net_usd']:,.2f}",
         "dsgs_vpp_backtest.json:revenue.reserve_20pct.net_usd"),
        ("Knee (sizing curve)", f"lands at {curve['current_behavior']['knee']['kwh']:,.0f} kWh",
         "battery_sizing_curve.json:current_behavior.knee.kwh"),
        ("Optimality gap", f"${gap['optimality_gap_usd']:,.2f}/yr gap",
         "perfect_foresight_dispatch.json:greedy_comparison.optimality_gap_usd"),
        ("Optimality gap", f"({gap['optimality_gap_pct_of_greedy']:,.1f}% of the shipping",
         "perfect_foresight_dispatch.json:greedy_comparison.optimality_gap_pct_of_greedy"),
        ("Electrification dividend", f"about ${div['dividend_yr']:,}/yr here today",
         "extended_results.json:electrification_dividend.dividend_yr"),
        ("Electrification dividend", f"~${div['dividend_yr_post_fix']:,}/yr",
         "extended_results.json:electrification_dividend.dividend_yr_post_fix"),
    ]
    # The token-derived pins, each appended only when its token resolved, and
    # each built from the RESOLVED string -- so a pin whose figure interpolates
    # an unavailable token is skipped whole rather than compared against text
    # containing "None".
    for term, figure_of, token in (
        ("Capacity factor", lambda v: v, "CAPACITY_FACTOR"),
        ("Self-consumption vs export", lambda v: f"exports {v} of its production",
         "EXPORTED_SHARE"),
        # The token renders "0.45-2.4%/month"; the glossary spells the unit out.
        # Only the numeric range is pinned, so the prose stays free.
        ("Soiling", lambda v: f"{v.replace('%/month', '')}% lost per dry month",
         "SOILING_RATE_RANGE"),
        ("Specific yield", lambda v: f"{v} kWh/kW/yr", "SPECIFIC_YIELD"),
        ("Degradation", lambda v: v, "DEGRADATION_NAIVE_RANGE"),
        ("Phantom load", lambda v: f"median {v}", "NIGHT_FLOOR_MEDIAN"),
    ):
        if token in resolved:
            pins.append((term, figure_of(resolved[token]), token))
    # NIGHT_FLOOR_ANNUAL_COST renders both pricings in one sentence; the
    # glossary spreads them across its own. Pin the amounts, not the wording.
    if "NIGHT_FLOOR_ANNUAL_COST" in resolved:
        for amount in re.findall(r"\$[\d,]+", resolved["NIGHT_FLOOR_ANNUAL_COST"]):
            pins.append(("Phantom load", amount, "NIGHT_FLOOR_ANNUAL_COST"))

    entries = {}
    for line in GLOSSARY.splitlines():
        if line.startswith("**"):
            entries.setdefault(line.split("**")[1], []).append(line)

    for term, figure, source in pins:
        # Prefix match: several entries carry a parenthetical or an alias after
        # the term ("Dispatch policy (evening-only / ...)"), and renaming that
        # tail is a prose choice, not a figure moving.
        matched = [head for head in entries if head.startswith(term)]
        assert matched, (
            f"GLOSSARY.md no longer has a '{term}' entry, so the figure this case pins "
            f"to {source} cannot be checked at all")
        body = "\n".join(line for head in matched for line in entries[head])
        assert figure in body, (
            f"GLOSSARY.md's '{term}' entry does not carry {figure!r}, the value {source} "
            "derives -- the glossary and the artifact state different figures for the "
            "same quantity")
    note = (f"{len(skipped)} unresolvable without the private archive ({sorted(skipped)})"
            if skipped else "all resolved")
    return (f"{len(pins)} figures across {len(set(t for t, _, _ in pins))} GLOSSARY.md "
            f"entries match the artifacts and tokens that derive them ({note})")


# ---------------------------------------------------------------------------
# Issue #201: report-template.html's FIXED prose -- the live lines outside any
# {{TOKEN}} slot or <!-- TODO --> block -- is what a regeneration emits
# verbatim, so every substantial fixed-prose line in a template section must
# appear in the SAME section of index.html. Three divergences (#182, #196,
# #201) were each found by hand while proving byte-identity for something
# else; nothing compared the two files' fixed prose until this case.
#
# The check is ONE-directional: index.html legitimately carries more than the
# template (LLM-filled blocks, index-only paragraphs), so no index line is
# ever required to appear in the template. Comments never count as fixed
# prose: on the template side each <!-- ... --> becomes a wildcard hole (TODO
# blocks render as generated prose), on the index side they are dropped.
# {{TOKEN}} slots become non-greedy wildcards, which is what lets the same
# pattern also catch a token replaced by a WRONG literal: the fixed text
# around the hole still has to match, and a hand-edited
# <code>analysis/wrong.py</code> where index.html says
# <code>analysis/behavior_rebuild.py</code> is fixed text, not a hole.
#
# Only lines with at least _FIXED_PROSE_MIN_CHARS of real fixed content
# (tags, tokens and comment holes stripped) are checked -- below that the
# line is structural markup shared by construction. 30 was tuned on the fixed
# repo: 72 template lines qualify across 16 sections, and every known
# divergence line clears it comfortably. <script>/<style> bodies and the
# header/meta/day-band region ahead of the first <h2> are out of scope.
# ---------------------------------------------------------------------------
_FIXED_PROSE_MIN_CHARS = 30
_FIXED_PROSE_HOLE_SPLIT_RE = re.compile(r"\s*(?:\{\{[A-Z0-9_]+\}\}|\x00)\s*")
_FIXED_PROSE_TOKEN_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
_FIXED_PROSE_TAG_RE = re.compile(r"<[^>]*>")
_FIXED_PROSE_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_FIXED_PROSE_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

# KNOWN drift, pinned line by line (issue #201). Each entry names one template
# line -- (section id, a substring unique among that section's checked lines)
# -- whose fixed prose does NOT appear in index.html today, and maps it to
# (digest, reason): the first 12 hex chars of sha256 over the NORMALIZED
# template line exactly as _template_fixed_prose_drift reports it (whitespace
# collapsed, comments already a \x00 hole -- _fixed_prose_line_digest), plus
# the reason the line was not resolved in #201's sweep: resolving it needs a
# new token, a new TODO block, or an index.html edit, all outside that fix's
# scope. The case fails the moment an entry HEALS (delete the entry), stops
# matching any checked template line (the line changed -- re-decide it), OR
# the line drifts FURTHER while keeping its substring (the digest no longer
# matches -- re-decide the entry), so this list can only shrink truthfully.
# NEW drift is never allowed in by this list, and neither is a new edit to a
# line it already covers.
_FIXED_PROSE_DRIFT_ALLOWED = {
    ("s1", "Every 15-minute interval of the last"): ("50ada813f528",
        "index derives whole-home load in prose (29,914 kWh energy balance, CT-meter "
        "cross-check); no tokens exist for those figures"),
    ("s2", "kW DC nameplate"): ("f3f0614b3626",
        "index adds module arithmetic and mount/orientation facts no token renders"),
    ("s2", "<b>In service since:</b>"): ("848ed58632f5",
        "index words the NEM grandfathering span (~20 years) with no token for it"),
    ("s2", "<b>Production:</b>"): ("b2b724534b3c",
        "index names the concrete sources (CT meter; PVOutput) the template keeps "
        "monitoring-agnostic per CLAUDE.md section 7"),
    ("s2", "<b>Where it goes:</b>"): ("715f92125d33",
        "index adds MWh splits and share-of-production parentheticals with no tokens"),
    ("s5", "the behaviors behind them"): ("4c9586423c95",
        "index counts the behaviors (four); the count has no token"),
    ("s5", "Behaviors driving the current bill"): ("783be563930c",
        "index counts the behaviors (Four); the count has no token"),
    ("s6", "Price-aware (all non-super-off-peak imports)"): ("c0c52fc7d519",
        "index's expansion cell adds cycles/day; no token for pw3x cycles exists"),
    ("s8", "<b>More panels:"): ("2e409e20b9af",
        "deliberate divergence (#182): the template refuses to price added capacity "
        "(pinned by its own case); index still publishes the priced timing paragraph"),
    ("s9", "degradation trend</h3>"): ("8da8f6ba573b",
        "index's heading carries the measured span (6-year); no token owns it"),
    ("s9", "Inverter clipping"): ("f861755778ff",
        "index's heading bakes this household's verdict (none worth acting on)"),
    ("s9", "Phantom / always-on load"): ("d47f1f64d6aa",
        "index's heading bakes this household's verdict (identified, de-prioritized)"),
    ("s10", "detailed electric statements"): ("6f579ee69931",
        "index adds the corpus date range and a findings count; neither has a token"),
    ("s10", "Generation billed at"): ("6d1752357deb",
        "index names the CCA (CEA) in fixed prose the template keeps provider-neutral"),
    ("s10", "Model vs. actual"): ("67147baaddde",
        "index's note lead is a decomposition claim (six measured terms plus a "
        "residual) that only the filled TODO can truthfully state"),
    ("s11", "The install invoice shows"): ("1e3efa71c591",
        "index appends the blended-$/kWh derivation (rate-history scaling, CA State "
        "Auditor figures) with no tokens"),
    ("s12", "identical calendar windows in control years"): ("fefee6083c46",
        "template is deliberately AHEAD here (#212's raw-ratio wording); index also "
        "counts the control years (four) -- heals when index.html is regenerated"),
    ("s12", "Post ÷ pre (raw)</th>"): ("bd7fac4233e5",
        "same #212 template-side change ('(raw)' column header) awaiting an "
        "index.html regeneration"),
    ("s12", "How fast do the panels re-soil?"): ("31876a0af4f9",
        "index's heading adds a verdict clause (The evidence splits)"),
    ("s13", "Short workups pricing the context"): ("29756b16f69d",
        "index counts the workups (Six) and dates NEM expiry (2039); no tokens"),
    ("s13", "Annual rate escalation</th>"): ("be0c211b8e51",
        "index prefixes the utility name (SDG&E) the template keeps neutral"),
    ("s13", "Level escalation vs spread escalation"): ("8a43381191aa",
        "index's heading carries a verdict and an estimated pill reading '4 winter "
        "rate changes'; the template's SPREAD_OBSERVATION_COUNT token renders a "
        "different description (priced cells across periods) -- token drift, not "
        "just prose"),
    ("s14", "<b>Confidence labels:</b>"): ("df64676a5e46",
        "index adds report-specific evidence claims (example list, 'Sections 1-10 "
        "are measured/modeled throughout')"),
    ("s14", "not advice from any utility"): ("05e7dcb440cc",
        "index names vendors (SDG&E, CEA, Enphase, Tesla) the template keeps generic"),
}


def _fixed_prose_line_digest(line):
    """The 12-hex-char pin an allowance carries for its covered line: sha256
    over the line EXACTLY as _template_fixed_prose_drift reports it -- already
    whitespace-collapsed by _fixed_prose_lines, comments already \\x00 holes.
    One normalization, the checker's own; nothing here re-normalizes."""
    return hashlib.sha256(line.encode()).hexdigest()[:12]


def _fixed_prose_sections(doc, comment_replacement):
    """{section id: that section's text} with <script>/<style> bodies removed
    and every HTML comment replaced (template: a \\x00 hole; index: a space).
    Comments go first through re.S so a multi-line TODO block collapses onto
    one line and its surrounding markup stays a single checkable line."""
    doc = _FIXED_PROSE_SCRIPT_STYLE_RE.sub("", doc)
    doc = _FIXED_PROSE_COMMENT_RE.sub(comment_replacement, doc)
    hits = list(_SECTION_H2_RE_OPEN.finditer(doc))
    out = {}
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(doc)
        out[m.group(1)] = doc[m.start():end]
    return out


_SECTION_H2_RE_OPEN = re.compile(r'<h2 id="([^"]+)"')


def _fixed_prose_lines(section_text):
    return [re.sub(r"\s+", " ", ln).strip() for ln in section_text.splitlines()
            if ln.strip()]


def _fixed_prose_content(line):
    """The line's checkable fixed prose: comment holes, {{TOKEN}} slots, then
    tags removed. What is left is text a regeneration must carry verbatim."""
    return _FIXED_PROSE_TAG_RE.sub(
        "", _FIXED_PROSE_TOKEN_RE.sub("", line.replace("\x00", ""))).strip()


def _fixed_prose_pattern(line):
    """Regex a template line must fullmatch against some same-section index
    line: every hole ({{TOKEN}} or comment) becomes a non-greedy wildcard
    absorbing its neighboring spaces, every fixed fragment is escaped."""
    parts = _FIXED_PROSE_HOLE_SPLIT_RE.split(line)
    return re.compile(".*?".join(re.escape(p) for p in parts), re.S)


def _template_fixed_prose_drift(template_doc, index_doc):
    """(checked line count, [(section id, collapsed template line), ...]) --
    every checked template fixed-prose line that appears in NO line of the
    same index.html section. Pure function of the two documents so the guard
    case below can feed it mutated copies."""
    tmpl = _fixed_prose_sections(template_doc, "\x00")
    idx = _fixed_prose_sections(index_doc, " ")
    checked, drifted = 0, []
    for sid, ttext in tmpl.items():
        ilines = _fixed_prose_lines(idx.get(sid, ""))
        for line in _fixed_prose_lines(ttext):
            if len(_fixed_prose_content(line)) < _FIXED_PROSE_MIN_CHARS:
                continue
            checked += 1
            pat = _fixed_prose_pattern(line)
            if not any(pat.fullmatch(il) for il in ilines):
                drifted.append((sid, line))
    return checked, drifted


# THE ONE-PROSE-ELEMENT-PER-SOURCE-LINE CONVENTION, enforced (round-3 review).
# _template_fixed_prose_drift reads the template LINE BY LINE and skips any
# line under _FIXED_PROSE_MIN_CHARS of fixed content, so a prose element
# re-wrapped across short source lines becomes invisible to the whole gate: a
# duplicate of a covered paragraph split into 5-22-char lines passed with 72
# checked / 24 used. The line-by-line reading is only sound while the template
# keeps one prose element per source line, so that assumption is asserted
# structurally: a checked-prose element type OPENED on a source line without
# its matching closing tag on the same line fails, naming section, tag, line.
#
# THE TAG SET IS DERIVED FROM THE TEMPLATE AS IT IS, not aspirational. The 72
# checked lines open with b/div/h2/h3/li/p/span/thead/tr (plus one no-tag
# continuation of the exempted s14 paragraph below). Of those, div is also a
# structural container that legitimately spans lines (grid2/grid3/cards/pkg/
# rec wrappers), so divs are held to one-per-line only when a class token
# carries checked prose: card/note (checked lines open with them) and lbl/big
# (the prose cells nested inside card lines). <summary> spans lines by design
# (h2 + teaser inside) and no checked line opens with it, so it is NOT in the
# set -- its teaser prose rides in <span class="teaser">, and span IS held.
_FIXED_PROSE_ONE_LINE_TAGS = ("b", "h2", "h3", "li", "p", "span", "thead", "tr")
_FIXED_PROSE_ONE_LINE_DIV_CLASSES = frozenset({"card", "note", "lbl", "big"})

# The ONE deliberate multi-line prose element in today's template: the s14
# provenance paragraph (CLAUDE.md section 11) opens here and closes two source
# lines later. Its continuation lines each carry enough fixed prose to be
# checked individually, so nothing in it escapes the gate; anything else
# spanning lines must be listed here deliberately or fail.
_FIXED_PROSE_MULTILINE_ALLOWED = {
    ("s14", '<p class="small"><b>Data sources:</b>'):
        "the provenance paragraph is written across source lines with <br> "
        "breaks; every continuation line is long enough to be checked itself",
}


def _template_prose_multiline_violations(template_doc):
    """[(sid, tag, line)] for every checked-prose element type opened on a
    template source line without its closing tag on that line -- read through
    the same comment/script/style preprocessing the drift checker applies
    (_fixed_prose_sections/_fixed_prose_lines), so both see identical lines."""
    out = []
    for sid, ttext in _fixed_prose_sections(template_doc, "\x00").items():
        for line in _fixed_prose_lines(ttext):
            for tag in _FIXED_PROSE_ONE_LINE_TAGS:
                if (len(re.findall(rf"<{tag}(?=[\s>])", line))
                        > line.count(f"</{tag}>")):
                    out.append((sid, tag, line))
            div_opens = re.findall(r"<div\b([^>]*)>", line)
            if div_opens and len(div_opens) != line.count("</div>"):
                for attrs in div_opens:
                    m = re.search(r'class="([^"]*)"', attrs)
                    if m and (_FIXED_PROSE_ONE_LINE_DIV_CLASSES
                              & set(m.group(1).split())):
                        out.append((sid, "div", line))
                        break
    return out


def _fixed_prose_gate(template_doc, index_doc):
    """The WHOLE public gate as one reusable check: the one-element-per-line
    structural assumption first, then drift detection, the allowance routing
    (substring match AND digest pin, exactly one line each), the staleness
    sweeps. Returns the pass summary or raises AssertionError with the same
    messages the real case shows -- so the guard case below can prove, on a
    mutated template, that THIS logic (not a lookalike) rejects each shape."""
    unexempt, used_exemptions = [], set()
    for sid, tag, line in _template_prose_multiline_violations(template_doc):
        keys = [k for k in _FIXED_PROSE_MULTILINE_ALLOWED
                if k[0] == sid and k[1] in line]
        if keys:
            used_exemptions.update(keys)
        else:
            unexempt.append((sid, tag, line))
    assert not unexempt, (
        "template prose element OPENS on one source line without CLOSING on "
        "it -- the fixed-prose checker reads the template line by line and "
        f"skips lines under {_FIXED_PROSE_MIN_CHARS} fixed chars, so prose "
        "wrapped across short source lines silently leaves the gate's "
        "coverage entirely; keep one prose element per source line (or list "
        "a deliberate multi-line element in _FIXED_PROSE_MULTILINE_ALLOWED "
        "with its reason):\n" + "\n".join(
            f"  section {sid}: <{tag}> left open on: {line}"
            for sid, tag, line in unexempt))
    stale_ex = sorted(k for k in _FIXED_PROSE_MULTILINE_ALLOWED
                      if k not in used_exemptions)
    assert not stale_ex, (
        f"_FIXED_PROSE_MULTILINE_ALLOWED entries matching no open-without-"
        f"close template line: {stale_ex} -- the element was rejoined onto "
        "one line or edited away; delete or re-decide the exemption")

    checked, drifted = _template_fixed_prose_drift(template_doc, index_doc)
    assert checked >= 50, (
        f"only {checked} template fixed-prose lines qualified for checking -- the "
        "parser or the threshold probably broke (72 qualified when this was written)")

    # Route each drifted line through the allowance; an allowed line is known
    # drift, an unmatched one is NEW drift and fails with its own text.
    def _allowance_for(sid, line):
        keys = [k for k in _FIXED_PROSE_DRIFT_ALLOWED
                if k[0] == sid and k[1] in line]
        assert len(keys) <= 1, (
            f"ambiguous _FIXED_PROSE_DRIFT_ALLOWED entries for one section-{sid} "
            f"line: {keys} -- make each substring unique")
        return keys[0] if keys else None

    matched = {}
    new_drift = []
    drifted_further = []
    for sid, line in drifted:
        key = _allowance_for(sid, line)
        if key is None:
            new_drift.append((sid, line))
            continue
        pinned, _reason = _FIXED_PROSE_DRIFT_ALLOWED[key]
        if _fixed_prose_line_digest(line) != pinned:
            drifted_further.append((sid, line, key, pinned))
        else:
            matched.setdefault(key, []).append((sid, line))
    used = set(matched)
    # An allowance blesses EXACTLY ONE line. A second drifted line carrying
    # the same substring and digest -- a duplicate of the covered line -- is
    # NEW drift, not something the old allowance ever decided; without this
    # count a copy of a covered line rode in on the entry silently.
    multi = {k: v for k, v in matched.items() if len(v) > 1}
    assert not multi, (
        "one _FIXED_PROSE_DRIFT_ALLOWED entry matched MORE THAN ONE drifted "
        "line -- an allowance blesses exactly one line, so the extra copies "
        "are NEW drift (a duplicated template line) the entry cannot cover:\n"
        + "\n".join(
            f"  entry {k} matched {len(v)} lines:\n" + "\n".join(
                f"    section {sid}: {line}" for sid, line in v)
            for k, v in sorted(multi.items())))
    # An entry blesses ONE exact line, pinned by digest -- never future edits
    # that keep its substring. A covered line whose digest moved is NEW drift.
    assert not drifted_further, (
        "an ALLOWLISTED line drifted FURTHER: each line below still carries its "
        "_FIXED_PROSE_DRIFT_ALLOWED substring, but its content no longer matches "
        "the digest pinned when the allowance was decided, so the old reason no "
        "longer covers it -- re-decide the entry (and re-pin its digest via "
        "_fixed_prose_line_digest) rather than letting the allowance bless a new "
        "edit:\n" + "\n".join(
            f"  section {sid} (entry {key}, pinned {pinned}, line now "
            f"{_fixed_prose_line_digest(line)}): {line}"
            for sid, line, key, pinned in drifted_further))
    assert not new_drift, (
        "template fixed prose that appears nowhere in the same section of "
        "index.html -- the template is what a regeneration emits, so the published "
        "page cannot be reproduced from it (adopt the published wording, tokens "
        "preserved, per issue #201):\n" + "\n".join(
            f"  section {sid}: {line}" for sid, line in new_drift))

    stale = sorted(k for k in _FIXED_PROSE_DRIFT_ALLOWED if k not in used)
    # A stale entry is one of two lies: the line now MATCHES index.html
    # (healed -- delete the entry) or no checked template line carries the
    # substring any more (the line changed -- re-decide it). Either way the
    # allowance no longer describes real drift and must go.
    assert not stale, (
        f"_FIXED_PROSE_DRIFT_ALLOWED entries matching no drifted line: {stale} -- "
        "each names known drift; if the line healed or was edited, delete or "
        "re-decide the entry (the list only shrinks truthfully)")

    return (f"all {checked} substantial template fixed-prose lines appear in the "
            f"same section of index.html ({len(used)} known-drift lines allowed by "
            f"_FIXED_PROSE_DRIFT_ALLOWED, {len(_FIXED_PROSE_DRIFT_ALLOWED)} listed)")


def case_template_fixed_prose_lines_all_appear_in_the_published_page():
    return _fixed_prose_gate(TEMPLATE_HTML, HTML)


# The section-7 one-pipeline sentence, read OUT of the template instead of
# typed into the mutation table below.
#
# The sentence names the free fix that runs before the battery, and issue #147
# made that name a token ({{FREE_FIX_SHORT_NAME}}: "EV" here, "house-load" for
# a household with no EV), because the literal "EV" asserted a vehicle the
# household may not own. A mutation anchored on the literal sentence therefore
# stopped matching the moment the template was tokenized -- and the mutation's
# uniqueness assert would have reported that as "re-anchor it", which is what
# this pattern does once and for all: the fix's NAME is a wildcard, the rest of
# the sentence -- the claim the guard is about -- is matched exactly. A rename
# of the token, or a second household name for the fix, keeps the anchor; an
# edit to the pipeline claim itself still breaks it loudly, which is correct,
# because the mutation below exists to prove that exact claim is guarded.
_S7_PIPELINE_SENTENCE_RE = re.compile(
    r"behavior and battery are simulated in ONE integrated pipeline "
    r"\(\{\{[A-Z0-9_]+\}\} shift first, then battery, re-billed end-to-end\), "
    r"so nothing is double-counted\.")


def _s7_pipeline_sentence():
    """The live s7 one-pipeline sentence, exactly as the template spells it.
    Asserts it appears EXACTLY once, so the mutation built on it still edits
    one known line rather than silently zero or several."""
    hits = _S7_PIPELINE_SENTENCE_RE.findall(TEMPLATE_HTML)
    assert len(hits) == 1, (
        "the section-7 one-pipeline sentence no longer appears exactly once in "
        f"report-template.html ({len(hits)} matches for "
        f"{_S7_PIPELINE_SENTENCE_RE.pattern!r}) -- the fixed-prose guard's "
        "mutation is anchored on it; re-anchor the mutation, do not delete it")
    return hits[0]


# ---------------------------------------------------------------------------
# ISSUE #147: LIVE TEMPLATE MARKUP MAY NOT NAME A FREE FIX THIS HOUSEHOLD
# DOES NOT HAVE.
#
# Seven tokens were taught to render truthfully for a household whose intake
# says household.has_ev is false, and the page stayed false anyway, because the
# FIXED MARKUP around those tokens went on asserting an EV in its own voice: a
# <h3> reading "success metrics for the EV fix", a section-7 paragraph reading
# "EV shift first, then battery". A token cannot correct a sentence it does not
# appear in, so the property worth guarding is not the wording of any one
# sentence -- it is that live markup never spells a household-specific EV fact
# at all. Every such fact belongs to a token (resolved from the artifacts) or
# to a TODO block (written from the artifacts); neither is fixed markup.
#
# TWO HALVES, and they catch different defects:
#   (1) a VOCABULARY sweep over all live markup -- fixed prose may not contain
#       an EV-asserting word anywhere, so a new sentence added tomorrow is
#       covered without anyone adding a case for it;
#   (2) a SEMANTIC check on the one-pipeline claim -- the fix it names must be
#       token-owned AND the token's value must agree with what the committed
#       artifacts say the free fix moves. Half (1) alone would pass a template
#       that hard-codes "house-load shift first" over a household WITH an EV,
#       which is the same lie pointing the other way.
#
# WHAT IS DELIBERATELY NOT IN THE VOCABULARY: "charge", "recharge", "charge
# cap". Those are battery words in this report ("solar recharge", "continuous
# charge caps") and banning them would fire on prose that asserts nothing about
# a vehicle. "charger" and "plug-in" ARE banned -- in this report they name EV
# hardware and nothing else, so a live mention of either is the same claim as
# "EV" itself.
_EV_ASSERTING_LITERALS = (
    (re.compile(r"\bEVs?\b"), 'the abbreviation "EV"'),
    (re.compile(r"electric vehicles?", re.I), 'the phrase "electric vehicle"'),
    (re.compile(r"\bchargers?\b", re.I), 'EV hardware ("charger")'),
    (re.compile(r"\bplug-ins?\b", re.I), 'an EV habit ("plug-in")'),
)

# The one-pipeline claim with the FIX NAME left open, so the check reads what
# the template actually put there instead of assuming a token is there.
_S7_PIPELINE_FIX_RE = re.compile(
    r"integrated pipeline \(([^()]*?) shift first, then battery")

_TOKEN_REF_ONLY_RE = re.compile(r"^\{\{([A-Z0-9_]+)\}\}$")


def _blank_keeping_lines(m):
    """Replacement that deletes a span but keeps the file's line numbering, so
    a violation can be reported at the line a maintainer will open."""
    return "\n" * m.group(0).count("\n")


def _live_template_markup():
    """report-template.html as the PUBLISHED page carries it: <script>/<style>
    bodies dropped, HTML comments (every TODO block) dropped, and {{TOKEN}}
    references dropped -- a token's NAME is a reference to a value, never a
    claim the markup makes, so {{EV_FIX_SAVINGS_100}} must not read as one.
    Line numbers are preserved."""
    doc = _FIXED_PROSE_SCRIPT_STYLE_RE.sub(_blank_keeping_lines, TEMPLATE_HTML)
    doc = _FIXED_PROSE_COMMENT_RE.sub(_blank_keeping_lines, doc)
    return _FIXED_PROSE_TOKEN_RE.sub("", doc)


def _free_fix_moves_ev():
    """Whether the committed artifacts say the free behavior fix moves EV
    charging at all -- recomputed here from the two artifacts rather than by
    calling report_tokens, so a bug in that module's naming branch fails this
    case too instead of being confirmed by it.

    behavior_rebuild.py's scenario ladder: a and b shift EV sessions; c and d
    shift flexible house load, and carry the EV shift as well wherever an EV
    exists. Which rung the LOW package IS comes from the generator's own
    statement of it (packages.LOW.free_fix_scenario), never re-derived; whether
    an EV exists comes from the detector block, which behavior_rebuild.py
    replaces with a {"not_applicable": true} stub for a household whose intake
    says it has none."""
    pk = json.loads((ROOT / "data" / "package_results.json").read_text())
    scenario = pk["packages"]["LOW"].get("free_fix_scenario")
    assert scenario in ("a", "b", "c", "d"), (
        "data/package_results.json:packages.LOW.free_fix_scenario is "
        f"{scenario!r}, which is not one of behavior_rebuild.py's four shift "
        "scenarios -- nothing here can say what the free fix moves")
    has_ev = BEHAVIOR["detection"].get("not_applicable") is not True
    return scenario, (True if scenario in ("a", "b") else has_ev)


def _live_markup_ev_assertions(doc):
    """[(line number, label, line)] for every EV-asserting literal in live
    markup. A pure function of the document so the guard case below can feed it
    a mutated copy."""
    out = []
    for n, line in enumerate(doc.splitlines(), 1):
        for rx, label in _EV_ASSERTING_LITERALS:
            if rx.search(line):
                out.append((n, label, line.strip()))
    return out


def case_live_template_markup_never_names_a_free_fix_the_household_lacks():
    """No fixed markup in report-template.html asserts an EV, and the
    section-7 one-pipeline sentence names the same free fix the committed
    artifacts do (issue #147)."""
    doc = _live_template_markup()
    assertions = _live_markup_ev_assertions(doc)
    assert not assertions, (
        "live report-template.html markup asserts an EV in its own voice -- a "
        "household whose intake says household.has_ev is false gets a page that "
        "is false no matter how its tokens render. Move the fact into a token "
        "(resolved from the artifacts) or into a TODO block:\n"
        + "\n".join(f"  line {n}: {label} in: {line[:160]}"
                    for n, label, line in assertions))

    # (2) The one-pipeline claim: token-owned, and the token agrees with the
    # artifacts about what the free fix actually moves.
    named = _S7_PIPELINE_FIX_RE.findall(doc)
    assert len(named) == 1, (
        "the section-7 one-pipeline sentence ('... integrated pipeline (X shift "
        f"first, then battery ...') appears {len(named)} times in live template "
        "markup, expected exactly once -- CLAUDE.md section 9's one-pipeline "
        "claim is stated there and this case checks WHICH fix it names")
    # {{TOKEN}} references were stripped from `doc`, so a token-owned name
    # leaves an empty slot here; anything else is fixed markup naming the fix.
    assert named[0] == "", (
        f"the section-7 one-pipeline sentence names its free fix as {named[0]!r}, "
        "which is fixed markup, not a token -- whichever household the artifacts "
        "describe, a literal there is a claim the template makes on its own. Use "
        "the token the Monday appendix's success-metrics heading already uses")
    m = _S7_PIPELINE_FIX_RE.search(
        _FIXED_PROSE_COMMENT_RE.sub(_blank_keeping_lines, TEMPLATE_HTML))
    token_ref = _TOKEN_REF_ONLY_RE.match(m.group(1).strip())
    assert token_ref, (
        "the section-7 one-pipeline sentence's free-fix slot is not a single "
        f"{{{{TOKEN}}}} reference: {m.group(1)!r}")
    token = token_ref.group(1)

    import report_tokens as rt
    assert token in rt.TOKENS, (
        f"the section-7 one-pipeline sentence names its free fix with "
        f"{{{{{token}}}}}, which report_tokens.py does not declare")
    rendered = rt.resolve_token(token, rt.TOKENS[token])
    scenario, moves_ev = _free_fix_moves_ev()
    says_ev = bool(re.search(r"\bEV\b", str(rendered)))
    assert says_ev == moves_ev, (
        f"section 7 says the integrated pipeline runs the {rendered!r} shift "
        f"first, but the artifacts say the free fix "
        f"{'does' if moves_ev else 'does NOT'} move EV charging "
        f"(data/package_results.json:packages.LOW.free_fix_scenario = "
        f"{scenario!r}; data/behavior_rebuild.json:detection is "
        f"{'a not-applicable stub' if BEHAVIOR['detection'].get('not_applicable') is True else 'a real detector run'})")
    return (f"live template markup asserts no EV ({len(doc.splitlines())} lines "
            f"swept for {len(_EV_ASSERTING_LITERALS)} EV-asserting literals) and "
            f"section 7's one-pipeline sentence names the free fix through "
            f"{{{{{token}}}}} = {rendered!r}, which agrees with scenario "
            f"{scenario!r}")


def case_the_free_fix_naming_guard_rejects_the_claims_it_exists_to_catch():
    """The guard above, fed the two defect shapes issue #147 is about, in
    memory: (1) the EV literal put back into live markup, (2) the fix named by
    fixed markup rather than a token. A guard trusted on plausibility has
    already shipped a silent no-op in this file (tests-must-fail memory)."""
    clean = _live_template_markup()
    assert not _live_markup_ev_assertions(clean), (
        "positive control: the unmutated template must be clean before this "
        "case can prove the guard catches anything")

    # (1) the literal EV assertion, in the exact site issue #147 found.
    ev_literal = clean.replace(
        "integrated pipeline ( shift first",
        "integrated pipeline (EV shift first")
    assert ev_literal != clean, "mutation 1 was a no-op -- re-anchor it"
    caught = _live_markup_ev_assertions(ev_literal)
    assert caught and any('the abbreviation "EV"' == label for _n, label, _l in caught), (
        "the vocabulary sweep did NOT catch a live 'EV shift first' in section 7")

    # (1b) the same defect worded differently, to prove the sweep is a
    # vocabulary check and not a second pin on one sentence.
    charger = clean.replace(
        "Logged before the change",
        "Reprogram the charger, then logged before the change")
    assert charger != clean, "mutation 1b was a no-op -- re-anchor it"
    caught_b = _live_markup_ev_assertions(charger)
    assert caught_b and any('charger' in label for _n, label, _l in caught_b), (
        "the vocabulary sweep did NOT catch live markup naming an EV charger")

    # (2) the fix named by fixed markup instead of a token -- worded so the
    # vocabulary sweep CANNOT see it, which is why half (2) exists.
    literal_fix = clean.replace(
        "integrated pipeline ( shift first",
        "integrated pipeline (house-load shift first")
    assert literal_fix != clean, "mutation 2 was a no-op -- re-anchor it"
    assert not _live_markup_ev_assertions(literal_fix), (
        "mutation 2 must be invisible to the vocabulary sweep, or it does not "
        "prove the semantic half catches anything")
    named = _S7_PIPELINE_FIX_RE.findall(literal_fix)
    assert named == ["house-load"], (
        "the semantic half did NOT see a fixed-markup free-fix name in the "
        f"section-7 one-pipeline sentence: {named!r}")
    return ("the free-fix naming guard catches an EV literal, an EV-charger "
            "literal, and a fixed-markup fix name the vocabulary sweep cannot see")


def case_the_fixed_prose_guard_rejects_the_drift_it_exists_to_catch():
    """The three defect shapes issue #201 names, plus a fourth (an edit to an
    ALLOWLISTED line that keeps the entry's substring), reintroduced one at a
    time into an in-memory copy of the template, must each be caught and NAMED
    (section id + the offending line) -- a guard trusted on plausibility
    alone has already shipped silent no-ops here (tests-must-fail memory)."""
    baseline_checked, baseline_drift = _template_fixed_prose_drift(TEMPLATE_HTML, HTML)
    mutations = [
        ("s7 overlap-deduction wording resurrected",
         _s7_pipeline_sentence(),
         "behavior/battery interaction is modeled so nothing is double-counted "
         "(overlap: {{OVERLAP_DEDUCTION}}/yr).",
         "s7", "double-counted"),
        ("s0 NEM dropped from the netting-model name",
         "re-billing with the bill-validated NEM netting model",
         "re-billing with the bill-validated netting model",
         "s0", "netting model"),
        ("s7 script token replaced by a wrong literal",
         "the validated NEM netting model (<code>{{BEHAVIOR_MODEL_SCRIPT}}</code>)",
         "the validated NEM netting model (<code>analysis/wrong.py</code>)",
         "s7", "analysis/wrong.py"),
    ]
    for name, old, new, want_sid, want_frag in mutations:
        assert TEMPLATE_HTML.count(old) == 1, (
            f"mutation {name!r} no longer has a unique anchor in the template "
            f"({TEMPLATE_HTML.count(old)} occurrences of {old!r}) -- re-anchor it")
        mutated = TEMPLATE_HTML.replace(old, new)
        assert mutated != TEMPLATE_HTML, f"mutation {name!r} was a no-op"
        checked, drifted = _template_fixed_prose_drift(mutated, HTML)
        introduced = [d for d in drifted if d not in baseline_drift]
        assert introduced, (
            f"the guard did NOT catch mutation {name!r}: no new drifted line "
            f"beyond the {len(baseline_drift)} baseline entries")
        assert all(sid == want_sid for sid, _ in introduced) and any(
            want_frag in line for _, line in introduced), (
            f"mutation {name!r} was caught but misattributed: expected section "
            f"{want_sid} with a line containing {want_frag!r}, got {introduced}")

    # FOURTH defect shape (the adversarial-review finding): an ALLOWLISTED
    # line edited while its _FIXED_PROSE_DRIFT_ALLOWED substring stays intact.
    # The three mutations above introduce drift a substring matches nothing
    # for; this one hides INSIDE a covered line, so only the digest pin can
    # see it -- and only the PUBLIC gate applies the pin, which is why this
    # shape runs _fixed_prose_gate (the exact logic the real case calls),
    # not the bare drift function.
    old = "maps when the money leaves"
    new = "maps where the money leaves"
    name = "s5 allowlisted line edited around its intact substring"
    assert TEMPLATE_HTML.count(old) == 1, (
        f"mutation {name!r} no longer has a unique anchor in the template "
        f"({TEMPLATE_HTML.count(old)} occurrences of {old!r}) -- re-anchor it")
    mutated = TEMPLATE_HTML.replace(old, new)
    assert mutated != TEMPLATE_HTML, f"mutation {name!r} was a no-op"
    assert ("s5", "the behaviors behind them") in _FIXED_PROSE_DRIFT_ALLOWED, (
        "the fourth mutation targets the s5 'the behaviors behind them' "
        "allowance, which no longer exists -- re-anchor the mutation")
    try:
        _fixed_prose_gate(mutated, HTML)
    except AssertionError as e:
        msg = str(e)
        assert "ALLOWLISTED line drifted FURTHER" in msg, (
            f"mutation {name!r} was rejected, but not AS an allowlisted line "
            f"drifting further -- the developer would hunt a new line instead "
            f"of re-deciding the entry: {msg}")
        assert "section s5" in msg and new in msg, (
            f"mutation {name!r} was caught but the message does not name "
            f"section s5 and the offending line: {msg}")
        assert "the behaviors behind them" in msg, (
            f"mutation {name!r} was caught but the message does not name the "
            f"allowlist entry to re-decide: {msg}")
    else:
        raise AssertionError(
            f"the gate did NOT catch mutation {name!r}: an edit to an "
            "allowlisted line that keeps the entry's substring passed the "
            "public gate -- the digest pin is not being enforced")

    # FIFTH defect shape (review-the-fix finding): the allowlisted line
    # DUPLICATED whole. Both copies carry the substring AND the pinned
    # digest, so neither the substring routing nor the digest pin objects --
    # only the exactly-one count can see that one allowance is now blessing
    # two lines, the second of which is NEW drift it never decided.
    dup = ("<p>This section maps when the money leaves: your grid flows by "
           "hour, season, and month, and the behaviors behind them.</p>")
    name = "s5 allowlisted line duplicated whole"
    assert TEMPLATE_HTML.count(dup) == 1, (
        f"mutation {name!r} no longer has a unique anchor in the template "
        f"({TEMPLATE_HTML.count(dup)} occurrences of {dup!r}) -- re-anchor it")
    mutated = TEMPLATE_HTML.replace(dup, dup + "\n" + dup)
    assert mutated != TEMPLATE_HTML, f"mutation {name!r} was a no-op"
    assert ("s5", "the behaviors behind them") in _FIXED_PROSE_DRIFT_ALLOWED, (
        "the fifth mutation targets the s5 'the behaviors behind them' "
        "allowance, which no longer exists -- re-anchor the mutation")
    try:
        _fixed_prose_gate(mutated, HTML)
    except AssertionError as e:
        msg = str(e)
        assert "MORE THAN ONE drifted line" in msg, (
            f"mutation {name!r} was rejected, but not AS one allowance "
            f"blessing multiple lines: {msg}")
        assert ("('s5', 'the behaviors behind them')" in msg
                and "matched 2 lines" in msg
                and msg.count("maps when the money leaves") == 2), (
            f"mutation {name!r} was caught but the message does not name the "
            f"entry, the count, and both lines: {msg}")
    else:
        raise AssertionError(
            f"the gate did NOT catch mutation {name!r}: a duplicate of an "
            "allowlisted line passed the public gate -- the exactly-one "
            "count per allowance is not being enforced")

    # SIXTH defect shape (round-3 review): the allowlisted s5 paragraph
    # duplicated WRAPPED, every source line under the 30-fixed-char
    # threshold. Each wrapped line is too short to be checked, so the drift
    # detector, the digest pin, and the exactly-one count are all blind to
    # it -- only the one-element-per-source-line structural assert can see
    # the <p> opened without closing on its line. The rejection must name
    # the multi-line ELEMENT, not any allowance.
    wrapped = ("<p>This section\nmaps when the\nmoney leaves: your\n"
               "grid flows by hour,\nseason, and month,\nand the behaviors\n"
               "behind them.</p>")
    name = "s5 allowlisted paragraph duplicated wrapped across short lines"
    for wline in wrapped.splitlines():
        content = _fixed_prose_content(re.sub(r"\s+", " ", wline).strip())
        assert len(content) < _FIXED_PROSE_MIN_CHARS, (
            f"mutation {name!r} no longer exercises the under-threshold hole: "
            f"wrapped line {wline!r} has {len(content)} fixed chars, at or "
            f"over the {_FIXED_PROSE_MIN_CHARS}-char threshold -- re-wrap it")
    assert TEMPLATE_HTML.count(dup) == 1, (
        f"mutation {name!r} no longer has a unique anchor in the template "
        f"({TEMPLATE_HTML.count(dup)} occurrences of {dup!r}) -- re-anchor it")
    mutated = TEMPLATE_HTML.replace(dup, dup + "\n" + wrapped)
    assert mutated != TEMPLATE_HTML, f"mutation {name!r} was a no-op"
    try:
        _fixed_prose_gate(mutated, HTML)
    except AssertionError as e:
        msg = str(e)
        assert "OPENS on one source line without CLOSING" in msg, (
            f"mutation {name!r} was rejected, but not AS a multi-line prose "
            f"element -- the structural assert is not what fired: {msg}")
        assert ("section s5" in msg and "<p> left open on" in msg
                and "<p>This section" in msg), (
            f"mutation {name!r} was caught but the message does not name the "
            f"section, the tag, and the opening line: {msg}")
        assert ("drifted FURTHER" not in msg
                and "MORE THAN ONE drifted line" not in msg), (
            f"mutation {name!r} must be attributed to the multi-line element, "
            f"not to an allowance: {msg}")
    else:
        raise AssertionError(
            f"the gate did NOT catch mutation {name!r}: a wrapped duplicate "
            "whose every source line is under the fixed-char threshold "
            "passed the public gate -- the one-element-per-line structural "
            "assert is not being enforced")

    return (f"all {len(mutations)} reintroduced #201 defects plus the "
            f"allowlisted-line-edited-further, allowlisted-line-duplicated "
            f"and wrapped-duplication shapes are caught and attributed "
            f"({baseline_checked} lines checked, {len(baseline_drift)} "
            f"known-drift baseline)")



# ---------------------------------------------------------------------------
# Issue #189. An exported kWh is worth a BRACKET, not a figure: rates.credit()
# if its (month, season, TOU period) bucket settles as surplus, rates.energy()
# if it cancels an import inside that bucket. Publishing one end as "the" value
# is the defect #182 closed at section 8 and #189 closed at sections 0/5/6/15.
# The two cases below are the guard against it coming back, and they are
# deliberately SHAPE-based rather than string-based: #189 recorded that three
# consecutive issues here missed a live instance by searching for the text they
# had just changed.
# ---------------------------------------------------------------------------

# Wording that tells a reader WHICH treatment a figure is. A published export
# figure must sit near one of these; that is what makes it readable as one end
# of the bracket rather than as the value.
_NETTING_WORDS = ("cancel", "netted rate", "month and period", "nets against")
# NOT the bare word "surplus": this report uses it for PHYSICAL surplus energy
# ("solar surplus", "midday surplus") far more often than for the settlement
# treatment, so accepting it lets "solar surplus that would otherwise export for
# 7.6c" -- the exact sentence #189 corrected -- pass as if it named its end of
# the bracket. Verified by mutation: with the bare word in this list, that
# sentence passes; without it, it fails.
_SURPLUS_WORDS = ("surplus export", "settles as surplus", "settled as surplus",
                  "net-negative", "credit()")
_TREATMENT_WINDOW = 260


def _cents(x):
    """rates.py rate (dollars/kWh) as the one-decimal cents string the report prints."""
    return f"{x * 100:.1f}¢"


def case_every_published_export_figure_names_its_treatment():
    """Every occurrence of an export-bracket END in index.html sits within
    reach of wording that says which end it is.

    The values are computed from rates.py, not hard-coded here, so a rate
    change moves the strings this case looks for and the case keeps meaning
    what it says. A new passage that quotes 7.6¢ or 10.4¢ as "the" export value
    fails this, which is the whole point -- the failure mode is a sentence that
    is arithmetically right and tells the reader the wrong thing."""
    sys.path.insert(0, str(ROOT / "analysis"))
    import rates as R

    checks = [
        (_cents(R.credit("S", "sop")), _SURPLUS_WORDS, "credit() -- the surplus end"),
        (_cents(R.energy("S", "sop")), _NETTING_WORDS, "energy() -- the netting end"),
    ]
    seen = 0
    for figure, words, label in checks:
        for m in re.finditer(re.escape(figure), HTML):
            seen += 1
            lo = max(0, m.start() - _TREATMENT_WINDOW)
            window = HTML[lo:m.end() + _TREATMENT_WINDOW].lower()
            assert any(w in window for w in words), (
                f"{figure} ({label}) appears at index.html char {m.start()} with none "
                f"of {words} within {_TREATMENT_WINDOW} characters, so the reader "
                "cannot tell which end of the export bracket it is. Name the "
                "treatment or state the bracket.")
    assert seen >= 4, (
        f"only {seen} export-bracket figures found in index.html -- the report is "
        "expected to publish both ends in several places, so this case is probably "
        "no longer looking at the right strings")
    return (f"all {seen} published export-bracket figures name their treatment "
            f"({checks[0][0]} surplus / {checks[1][0]} netting)")


def case_stored_kwh_costs_match_the_dispatch_artifact():
    """Section 6's stored-kWh costs are the artifact's, to the digit.

    These are the figures #189 corrected: the midday cost was published as the
    forgone SURPLUS credit when every super-off-peak bucket is net-import and
    so settles at the netted rate. Pinning them here means the prose cannot
    drift back to a rate-card guess without this failing."""
    art = json.loads((ROOT / "data" / "battery_dispatch_policies.json").read_text())
    cost = art["stored_kwh_cost"]
    sop = cost["solar_surplus"]["by_period"]["sop"]

    # Anchored to the SENTENCE that makes each claim, not to mere presence
    # anywhere in the document. Verified by mutation: a presence-only check
    # passes when section 6's lead is corrupted, because the same figure also
    # appears in the caveat below it.
    for value, pattern, what in (
        (sop["cost_per_kwh_delivered"],
         r"A stored kWh costs <b>([\d.]+)¢</b> when it comes from midday solar surplus",
         "midday solar stored-kWh cost (section 6 lead)"),
        (cost["grid_topup"]["cost_per_kwh_delivered"],
         r"or <b>([\d.]+)¢</b> from a super-off-peak grid top-up",
         "grid top-up stored-kWh cost (section 6 lead)"),
        (cost["solar_surplus"]["cost_per_kwh_delivered"],
         r"averaged over everything the dispatch stores from the sun, a stored kWh "
         r"costs <b>([\d.]+)¢</b>",
         "blended solar stored-kWh cost (section 6 caveat)"),
    ):
        m = re.search(pattern, HTML)
        assert m, (
            f"the sentence publishing the {what} is not in index.html in the form this "
            f"case pins ({pattern!r}) -- either the prose was reworded or the figure was "
            "dropped; re-anchor this case rather than deleting it")
        printed, expected = m.group(1) + "¢", f"{value * 100:.1f}¢"
        assert printed == expected, (
            f"the {what} reads {printed} in index.html but "
            f"data/battery_dispatch_policies.json derives {expected}")

    # Section 6 calls this cell "midday", but rates.period_at() puts every
    # WEEKEND hour before 14:00 in sop, so that label is a claim about this
    # household's charging, not about the TOU window. The generator measures it;
    # this refuses the wording if the measurement ever stops supporting it.
    if "midday solar surplus" in HTML:
        inside = sop["share_inside_midday_window"]
        assert inside == 1.0, (
            f"index.html calls the super-off-peak stored-kWh cost a MIDDAY figure, but "
            f"only {inside * 100:.1f}% of the surplus charged in that period falls "
            "inside 10:00-14:00 -- the rest is weekend morning charging, which sop "
            "also covers. Either say super-off-peak, or compute the figure from the "
            "10:00-14:00 mask")

    share = sop["share_of_surplus_kwh"]
    assert f"{share * 100:.1f}%" in HTML, (
        f"the midday share of stored surplus is {share * 100:.1f}% in the artifact but "
        "index.html does not print it -- section 6 quotes the midday cost, so it must "
        "also say how much of the stored surplus that cost covers")
    assert share < 0.9, (
        f"midday is {share * 100:.1f}% of stored surplus; if it ever approaches all of "
        "it, section 6's separate blended figure stops being worth publishing and this "
        "case should be revisited rather than silenced")
    return ("section 6's three stored-kWh costs and the midday share all match "
            "data/battery_dispatch_policies.json")


# The HIGH package's claim, in each document that publishes it. SCOPED
# REGIONS, not whole files: TECHNICAL.md legitimately reports absent savings
# for panel cleaning, array upgrades and CCA repricing, so scanning it entire
# for "no savings" fails this case over prose that has nothing to do with the
# package, with a message blaming the package (Codex /review, issue #142).
def _high_card_regions():
    """{document: the passage that makes the HIGH package's claim}."""
    out = {}
    card = re.search(r'<div class="pkg">\s*<h3>HIGH\b.*?</div>', HTML, re.S)
    assert card, ("the HIGH package card is not in index.html in the form this case "
                  "pins (<div class=\"pkg\"> then <h3>HIGH); re-anchor it rather "
                  "than deleting it")
    out["index.html"] = card.group(0)

    tech = (ROOT / "TECHNICAL.md").read_text()
    bullet = re.search(r"- `packages\.HIGH`.*?(?=\n- `)", tech, re.S)
    assert bullet, "TECHNICAL.md no longer carries a `packages.HIGH` schema bullet"
    out["TECHNICAL.md"] = bullet.group(0)

    tpl = (ROOT / "report-template.html").read_text()
    todo = re.search(r"<li><!-- TODO: what the expansion increment really buys.*?--></li>",
                     tpl, re.S)
    assert todo, "report-template.html no longer carries the expansion-increment TODO"
    out["report-template.html"] = todo.group(0)
    return out


# One vocabulary for "this saves nothing", shared with the sibling guard in
# test_report_tokens.py (_ABSENT_SAVING_RE). Keeping two lists let the half that
# sees hand-authored prose drift weaker than the half that sees tokens: this one
# was missing "nothing", "never repays" and "saves no" entirely.
_ABSENT_SAVING_RE = re.compile(
    r"not savings|no savings|nothing|never repays|saves? no\b|zero saving|"
    r"rather than savings|rather than dollars", re.I)

# Claims that a positive marginal exists, for the branch where it does not.
_POSITIVE_SAVING_RE = re.compile(
    r"more than MID|does save money|saves more than", re.I)


def case_the_high_card_never_denies_the_saving_its_own_bullet_states():
    """Issue #142. The HIGH package card asserted "$216/yr more than MID" and
    then, in the very next bullet, "this package buys outage endurance, not
    savings". `packages.HIGH.marginal_vs_mid_yr` is positive, so the first
    bullet was the artifact's and the second contradicted it.

    Not a wording preference. A reader deciding whether to buy the expansion
    needs "it saves too little to earn back $5,900", not "it saves nothing" --
    only the second rules the pack out on its own terms, and someone whose own
    numbers differ would be led the wrong way.

    Two artifact fields, two different claims, checked separately:
    `savings_yr` is what the PACKAGE saves against the baseline;
    `marginal_vs_mid_yr` is what the INCREMENT adds over MID. A denial of the
    first is false whatever the second does.

    #131 added the equivalent guard for the token-owned section 7 verdict;
    this is the half that sees hand-authored prose."""
    art = json.loads((ROOT / "data" / "package_results.json").read_text())
    marginal = art["packages"]["HIGH"]["marginal_vs_mid_yr"]
    savings = art["packages"]["HIGH"]["savings_yr"]
    regions = _high_card_regions()

    # UNCONDITIONAL, whatever the sign: TECHNICAL.md hard-codes the figure in
    # its schema note, so the two can drift apart silently. Bare integer, which
    # is that document's own convention for these values -- a comma-grouped
    # pattern would demand **1,216** from a file that writes **1216**.
    assert f"`marginal_vs_mid_yr` **{marginal:.0f}**" in regions["TECHNICAL.md"], (
        f"TECHNICAL.md's packages.HIGH bullet does not carry marginal_vs_mid_yr as "
        f"{marginal:.0f}; the schema note has drifted from data/package_results.json")

    # The PACKAGE claim, independent of the increment: while savings_yr is
    # positive, no region may say the package saves nothing.
    if savings > 0:
        for doc, text in regions.items():
            m = _ABSENT_SAVING_RE.search(text)
            assert not (m and "package" in text.lower()[:m.end()].rsplit(".", 1)[-1]), (
                f"{doc} says {m.group(0)!r} of the PACKAGE, whose artifact reports "
                f"savings_yr of ${savings:,.0f}/yr. Only a claim scoped to the "
                "expansion's marginal saving could be true here")

    if marginal <= 0:
        # Not a free pass: a stale positive claim left by an earlier
        # regeneration must not survive the artifact moving under it.
        for doc, text in regions.items():
            m = _POSITIVE_SAVING_RE.search(text)
            assert not m, (
                f"{doc} still says {m.group(0)!r}, but "
                f"packages.HIGH.marginal_vs_mid_yr is now {marginal}. The prose is "
                "stale: a regeneration moved the artifact and left the purchase "
                "advice behind")
        return (f"packages.HIGH.marginal_vs_mid_yr is {marginal}, not positive; no "
                f"region claims a positive marginal, and TECHNICAL.md carries "
                f"{marginal:.0f}")

    printed = f"~${marginal:,.0f}/yr more than MID"
    assert printed in regions["index.html"], (
        f"the HIGH card does not state its own artifact's marginal saving "
        f"({printed!r} from packages.HIGH.marginal_vs_mid_yr)")

    # The INCREMENT claim: with a positive marginal, no region may deny it.
    # report-template.html is included on purpose -- the defect this issue
    # exists to fix lived in its TODO, where it instructed every generated
    # report to write the contradiction.
    for doc, text in regions.items():
        m = _ABSENT_SAVING_RE.search(text)
        assert not m, (
            f"{doc} says {m.group(0)!r} about an increment whose artifact reports a "
            f"POSITIVE marginal saving of ${marginal:,.0f}/yr. Say it saves too "
            "little to earn back the increment; do not say it does not save")
    return (f"the HIGH card states its artifact's ${marginal:,.0f}/yr marginal saving, "
            f"TECHNICAL.md carries the same figure, and no region denies it")



def case_a_household_with_no_gas_skips_that_case_and_still_exits_zero():
    """Issue #146. A gasless household gets every has_gas-gated artifact marked
    not applicable, and every case reading one must skip. This file raised
    SkipCase without defining or importing it, and two other cases asserted
    `applicable` outright, so such a checkout could not get a green suite.

    Runs in a COPY of the tracked tree, never in this checkout. Rewriting two
    committed artifacts in place -- the first version of this case -- fails on a
    read-only checkout, races a concurrent run, and leaves the tree modified if
    the process is killed before `finally`.

    Asserts every target case BY NAME, in both directions. This case
    itself skips in the child run, so a generic "some line says SKIP" check is
    satisfied by its own skip and would pass even if every target wrongly
    reported PASS."""
    import shutil
    import subprocess
    import tempfile

    gated = ("data/heat_pump_conversion.json", "data/all_electric_endgame.json")
    targets = ("case_heat_pump_conversion_section_matches_the_artifact",
               "case_all_electric_paragraph_furnace_savings_matches_the_artifact",
               "case_all_electric_endgame_section_matches_the_artifact",
               "case_gas_hdd_decomposition_matches_extended_results")

    live = {rel: json.loads((ROOT / rel).read_text()).get("applicable", True)
            for rel in gated}
    # extended_results.json is gated on the same flag but signals it differently:
    # its gas section is replaced by a not_applicable stub rather than carrying
    # an `applicable` key. Leaving it out of this check let exactly the mixed
    # state the assert exists to report pass silently.
    live["data/extended_results.json:gas_decomposition"] = not json.loads(
        (ROOT / "data" / "extended_results.json").read_text()
    )["gas_decomposition"].get("not_applicable", False)
    # household.has_gas gates both of these, so they can only ever agree. A
    # checkout where they disagree was produced by regenerating one and not the
    # other, and reporting that is more useful than skipping past it.
    assert len(set(live.values())) == 1, (
        f"these artifacts are gated on the same household.has_gas but disagree: "
        f"{live}. One was regenerated without the other")
    if not all(live.values()):
        raise SkipCase("this checkout already has no gas, so the fixture has "
                       "nothing to change")

    # -z and core.quotePath=false: a tracked path with a space would otherwise
    # split into fragments, and a non-ASCII one would come back octal-quoted.
    # Either way the file is silently skipped and the child dies at import with
    # a message about the wrong thing.
    tracked = [t for t in subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "-z"], cwd=str(ROOT),
        capture_output=True, text=True, check=True).stdout.split("\0") if t]
    with tempfile.TemporaryDirectory(prefix="sdge-nogas-") as td:
        sandbox = pathlib.Path(td)
        for rel in tracked:
            src, dst = ROOT / rel, sandbox / rel
            if not src.is_file():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            # copyfile, NOT copy2: copy2 preserves permission bits, so a
            # read-only checkout yields 0444 copies and the two artifacts this
            # fixture rewrites below die with PermissionError -- the very
            # scenario the docstring above gives as the reason for sandboxing.
            shutil.copyfile(src, dst)
        # THE PRODUCERS' OWN STUB, not the full payload with one flag flipped.
        # heat_pump_conversion.build() and all_electric_endgame.build() both
        # return {"applicable": False, "reason": ...} and nothing else, so
        # keeping the gas-only fields would let a case that ignores the marker
        # read figures a real no-gas checkout does not have -- passing here and
        # failing for a reproducer, which is the failure this whole issue is.
        for rel, gen in ((gated[0], "heat_pump_conversion.py"),
                         (gated[1], "all_electric_endgame.py")):
            gen_src = (ROOT / "analysis" / gen).read_text()
            assert '{"applicable": False, "reason": "household.has_gas is false"}' in gen_src, (
                f"{gen} no longer emits that no-gas stub; this fixture is out of date")
            (sandbox / rel).write_text(json.dumps(
                {"applicable": False, "reason": "household.has_gas is false"}, indent=1))
        # extended_results.json carries no top-level `applicable`: its gas
        # section is replaced wholesale by extended_findings._not_applicable
        # when household.has_gas is false. The stub is written out here rather
        # than imported, because importing that generator executes module-level
        # code that reads usage.csv from the private archive. The KEY is pinned
        # against the generator's source instead, so renaming it there fails
        # this fixture rather than leaving it describing a stub the pipeline no
        # longer emits.
        gen_src = (ROOT / "analysis" / "extended_findings.py").read_text()
        assert '"not_applicable": True' in gen_src, (
            "extended_findings.py no longer publishes a `not_applicable` stub in "
            "that shape; this fixture's no-gas artifact is out of date")
        er_path = sandbox / "data" / "extended_results.json"
        er = json.loads(er_path.read_text())
        er["gas_decomposition"] = {
            "not_applicable": True,
            "reason": "household.has_gas is false (fixture, issue #146)"}
        er_path.write_text(json.dumps(er, indent=1))

        # timeout: the child is the whole 78-case suite, and capture_output
        # means a hang would print nothing and block the parent indefinitely.
        r = subprocess.run([sys.executable, "analysis/test_report_consistency.py"],
                           cwd=str(sandbox), capture_output=True, text=True,
                           timeout=900)
        out = r.stdout + r.stderr

    assert "NameError" not in out, (
        "the not-applicable branch still raises NameError -- SkipCase is not "
        f"defined or imported in this file:\n{out[-600:]}")
    for name in targets:
        assert f"SKIP  {name} " in out, (
            f"{name} did not skip with its artifact not applicable:\n{out[-900:]}")
        # main() prints `PASS  {case()}` -- the RETURN STRING, never the case
        # name -- so an f"PASS  {name}" check could never fire. FAIL lines do
        # carry the name, and a gated case that stopped skipping fails there.
        assert f"FAIL  {name}" not in out, (
            f"{name} failed instead of skipping on data it cannot check:\n{out[-900:]}")
    assert r.returncode == 0, (
        "a household with no gas cannot get a green consistency suite; the run "
        f"exited {r.returncode}:\n{out[-900:]}")
    # Not `"skipped" in out`: this case skips in the child run too, so that is
    # satisfied by its own skip. The tally must account for at least the target
    # cases plus this one.
    tally = re.search(r"(\d+) skipped", out)
    assert tally and int(tally.group(1)) >= len(targets) + 1, (
        f"the tally reports {tally.group(1) if tally else 'no'} skips, fewer than the "
        f"{len(targets)} gated cases plus this one:\n{out[-900:]}")
    return (f"a no-gas checkout skips all {len(targets)} has_gas-gated cases by name "
            "and the suite still exits 0, driven in a copy of the tracked tree")


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
    case_report_import_and_export_totals_match_the_artifact,
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
    case_plan_and_battery_margins_match_their_artifacts,
    case_battery_hardware_sizing_table_matches_battery_sim_artifact,
    case_bill_decomposition_finding_matches_the_artifact,
    case_carbon_dispatch_tradeoff_paragraph_matches_the_artifact,
    case_tornado_battery_sensitivity_matches_extended_results,
    case_ev_fleet_fuel_cost_matches_extended_results,
    case_gas_hdd_decomposition_matches_extended_results,
    case_nbt_flat_credit_sensitivity_matches_the_artifacts,
    case_extra_results_cleaning_cadence_matches_the_artifact,
    case_extra_results_trueup_ledger_matches_the_artifact,
    case_night_floor_section_matches_the_artifact,
    case_all_three_sections_price_the_always_on_load_the_same_way,
    case_away_days_finding_matches_the_artifact,
    case_gross_import_decomposition_section_matches_the_artifact,
    case_irreducible_bill_figures_match_the_artifact,
    case_lifetime_payback_recovered_figures_match_the_artifact,
    case_nem3_grandfathering_section_matches_the_artifact,
    case_reprice_by_vintage_note_matches_the_artifact,
    case_soiling_annual_economics_matches_the_artifact,
    case_soiling_rate_bracket_matches_the_token_that_renders_it,
    case_cleaning_effect_heading_matches_the_sections_own_conclusion,
    case_cleaning_heading_and_gain_describe_the_same_event,
    case_cleaning_gain_is_not_determined_when_no_entry_matches,
    case_cleaning_gain_follows_the_artifacts_own_not_determined_status,
    case_cleaning_gain_is_not_determined_when_two_entries_share_the_date,
    case_weather_regression_paragraph_matches_the_artifact,
    case_s2_key_architectural_fact_matches_the_artifacts,
    case_s8_more_panels_timing_matches_the_artifacts,
    case_s8_export_value_is_published_as_a_bounded_range,
    case_s8_export_period_split_matches_the_profiles,
    case_the_template_prices_no_added_capacity_and_needs_no_gap_token_in_s8,
    case_s0_expansion_cap_matches_the_s8_one,
    case_the_added_capacity_guard_rejects_the_defect_and_accepts_a_priced_install,
    case_s8_expansion_verdict_rests_on_the_cap_and_the_grandfathering,
    case_s8_specific_yield_matches_the_token_rendered_one,
    case_s0_more_solar_bullet_keeps_the_two_shares_apart,
    case_the_midday_share_weights_each_hour_by_its_real_interval_count,
    case_the_referent_guard_rejects_every_paraphrase_of_the_timing_claim,
    case_the_referent_guard_reads_across_a_sentence_boundary,
    case_the_referent_guard_reads_a_percentage_only_when_it_is_unambiguous,
    case_the_s2_verdict_locator_tolerates_inline_markup,
    case_every_h2_section_opens_with_exactly_one_conclusion_line,
    case_basic_tier_verdict_lines_stay_inside_the_density_cap,
    case_the_two_structural_guards_reject_the_defects_they_exist_to_catch,
    case_midday_vs_overnight_carbon_ratio_is_one_comparison_in_both_documents,
    case_whole_home_load_names_both_derivations_and_states_their_gap,
    case_degradation_naive_band_contains_every_estimator_it_is_built_from,
    case_glossary_figures_match_the_artifacts_that_derive_them,
    case_template_fixed_prose_lines_all_appear_in_the_published_page,
    case_live_template_markup_never_names_a_free_fix_the_household_lacks,
    case_the_free_fix_naming_guard_rejects_the_claims_it_exists_to_catch,
    case_the_fixed_prose_guard_rejects_the_drift_it_exists_to_catch,
    case_every_published_export_figure_names_its_treatment,
    case_stored_kwh_costs_match_the_dispatch_artifact,
    case_the_high_card_never_denies_the_saving_its_own_bullet_states,
    case_a_household_with_no_gas_skips_that_case_and_still_exits_zero,
]



def main():
    ran = skipped = failures = 0
    skipped_names = []
    for case in CASES:
        try:
            print(f"PASS  {case()}")
            ran += 1
        # BEFORE suite_runner.CASE_FAILURES, which catches Exception: a skip is
        # not a failure, and this ordering is the whole fix.
        except SkipCase as e:
            print(f"SKIP  {case.__name__} ({e})")
            skipped_names.append(case.__name__)
            skipped += 1
        except suite_runner.CASE_FAILURES as e:  # noqa: BLE001
            suite_runner.report_case_failure(case, e)
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    # NAME WHAT WENT UNPROVEN. This suite turns content checks into skips when
    # the artifact they read does not apply, so a green line alone no longer
    # means the report was fully checked: a tree carrying the not-applicable
    # stubs -- a fork's regeneration, an older branch, a run against the wrong
    # household.yaml -- would silently stop checking those sections and still
    # exit 0. test_private_egress.py hit exactly that (#186) and answered it
    # with a banner; this is the same answer.
    if skipped_names:
        print(f"\nNOT CHECKED ({len(skipped_names)}), because the artifacts they "
              "read do not apply to this household:")
        for name in skipped_names:
            print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
