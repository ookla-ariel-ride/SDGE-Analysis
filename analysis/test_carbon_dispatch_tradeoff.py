#!/usr/bin/env python3
"""Behavioural + cross-check tests for carbon_dispatch_tradeoff.py (issue #8).

carbon_dispatch_tradeoff.py imports behavior_rebuild.py at module top level --
the same convention its two sibling generators, battery_dispatch_policies.py
and carbon_fullyear.py, already use -- and behavior_rebuild.py reads
private/household.yaml at ITS OWN module top level, failing closed (SystemExit)
if that file is absent. test_carbon_fullyear.py already solved this for the
identical problem: point household.PATH at a synthetic, invented household
BEFORE importing, so the whole chain (behavior_rebuild -> battery_dispatch_
policies / carbon_fullyear -> carbon_dispatch_tradeoff) imports cleanly on any
checkout, private/ or not. Applied here too, so all four modules import
unconditionally at the top of this file rather than per-case.

That only fixes IMPORTING the modules, not what each case can prove. Two
different needs follow from it:
  * The dispatch-LOGIC cases (EV-spillover exclusion, the A/B/C control-flow
    divergence, the fail-closed corrupt/insufficient-CSV cases) call pure
    functions on small hand-built synthetic frames or throwaway temp files --
    nothing about them depends on the real archive, so they run unconditionally
    in CI now (previously they were needlessly gated behind the same
    private-archive check the import problem forced on everything).
  * Cases that call br.load() for the real 35,040-row year, or need the real
    cross-check against battery_dispatch_policies.json / the real CAISO
    intensity data, still call _require_archive() and raise SkipCase (matching
    test_irreducible_bill.py's / test_bill_decomposition.py's own
    private-archive-SKIP convention) when that archive is absent -- there is no
    synthetic stand-in that would prove anything about those.

Run from the repo root:  ./.venv/bin/python analysis/test_carbon_dispatch_tradeoff.py
"""
import glob
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import suite_runner  # noqa: E402

# Same fix as test_carbon_fullyear.py, for the same reason: point the intake
# loader at a synthetic, invented household before the transitive import of
# behavior_rebuild fires, so this whole file (and every case in it) imports on
# a clean checkout with no private/ at all. Values are invented; nothing here
# depends on them except the cases that explicitly load the real archive below.
import household as _hh
_HH_DIR = tempfile.TemporaryDirectory()
_hh.PATH = pathlib.Path(_HH_DIR.name) / "household.yaml"
_hh.PATH.write_text(
    "household:\n  pto_date: 2019-12-01\nlocation:\n  lat: 33.0\n"
    "solar:\n  install_invoice_usd: 30000\n  install_paid_date: 2019-12-01\n"
    "charger:\n  kw: 11.5\ncleaning_history: []\n"
    "gas:\n  therm_allin_usd: 2.0\n"
    "misc:\n  miles_per_year: 12000\n  supercharge_kwh_yr: 500\n")
_hh._cache = None

import behavior_rebuild as br
import battery_dispatch_policies as bp
import carbon_fullyear as CF
import carbon_dispatch_tradeoff as CDT

ROOT = pathlib.Path(__file__).resolve().parent.parent
USAGE_GLOB = str(ROOT / "private" / "1-raw-data" / "Electric_15_Minute_*.csv")
HOUSEHOLD_YAML = ROOT / "private" / "household.yaml"

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet (no
    private Green Button archive / household.yaml staged). Counted as neither
    pass nor fail."""


def _require_archive():
    """Only for cases that need the REAL archive (br.load() on the actual
    35,040-row year, or a cross-check against a committed artifact built from
    it) -- the module import above already succeeded unconditionally using the
    synthetic household, so this gates DATA, not importability."""
    files = sorted(glob.glob(USAGE_GLOB))
    if not files or not HOUSEHOLD_YAML.is_file():
        raise SkipCase(f"needs the private archive ({USAGE_GLOB}) and "
                       f"{HOUSEHOLD_YAML}, neither of which this checkout has")
    return files[0]


def _load_modules():
    """Points behavior_rebuild.CSV at the real archive file (its default is
    the bare relative "usage.csv", the private/verify sandbox convention) so
    this suite runs straight from the repo root with no sandbox copy step.
    Raises SkipCase (via _require_archive) if the real archive is absent."""
    usage = _require_archive()
    br.CSV = usage
    return br, bp, CF, CDT


_CACHE = {}


def _result():
    """The real compute() result, memoized across cases in this process (each
    case still calls _require_archive() itself first via _load_modules(), so
    the memoization never hides a missing-archive skip)."""
    if "result" not in _CACHE:
        _, _, _, CDT_ = _load_modules()
        _CACHE["result"] = CDT_.compute()
        _CACHE["CDT"] = CDT_
    return _CACHE["result"], _CACHE["CDT"]


EPS = 1e-6


# ---------------------------------------------------------------------------
# Threshold derivation: does it actually hit the intended split, and does the
# brief's claimed 53.3%/46.7% TOU split hold up against the real data?
# ---------------------------------------------------------------------------
@case
def case_threshold_achieves_the_intended_split():
    """Issue's own framing: size Run B's discharge window to the SAME fraction
    of the year as Run A's non-sop discharge window. Assert the ACHIEVED split
    is close to the TARGET split (not just that some number was picked), and
    separately confirm the brief's stated 53.3%/46.7% figures against the real
    d.p distribution rather than trusting them blindly."""
    result, _ = _result()
    th = result["threshold"]
    assert abs(th["achieved_dirty_frac"] - th["target_dirty_frac"]) < 0.01, th
    assert abs(th["achieved_clean_frac"] - th["target_clean_frac"]) < 0.01, th
    counts = th["tou_interval_counts"]
    total = counts["total"]
    nonsop_frac = (total - counts["sop"]) / total
    sop_frac = counts["sop"] / total
    # the brief's own claimed figures, checked against this run's real data
    assert abs(nonsop_frac - 0.533) < 0.01, (nonsop_frac, "brief claimed 0.533")
    assert abs(sop_frac - 0.467) < 0.01, (sop_frac, "brief claimed 0.467")
    return (f"threshold {th['kg_per_mwh']} kg/MWh achieves "
           f"{th['achieved_dirty_frac']*100:.2f}% dirty / "
           f"{th['achieved_clean_frac']*100:.2f}% clean vs target "
           f"{th['target_dirty_frac']*100:.2f}%/{th['target_clean_frac']*100:.2f}% "
           f"(real TOU split: sop={counts['sop']}, off={counts['off']}, "
           f"on={counts['on']} of {total} -- brief's claimed 53.3%/46.7% holds)")


# ---------------------------------------------------------------------------
# Run A cross-check: the SAME policy the committed artifact already reports.
# ---------------------------------------------------------------------------
@case
def case_run_a_matches_committed_battery_dispatch_policies_json():
    """Run A calls battery_dispatch_policies.run_batt() directly -- the exact
    function/policy/capacity data/battery_dispatch_policies.json's pw3.greedy
    figure comes from -- so the two savings figures are the SAME quantity
    computed twice and must agree within rounding, a real cross-check."""
    result, _ = _result()
    cc = result["cross_check"]
    assert abs(cc["run_a_computed_save_usd"]
               - cc["battery_dispatch_policies_json_pw3_greedy_save_usd"]) <= cc["tolerance_usd"], cc
    return (f"Run A save ${cc['run_a_computed_save_usd']} matches committed "
           f"pw3.greedy.save ${cc['battery_dispatch_policies_json_pw3_greedy_save_usd']} "
           f"within ${cc['tolerance_usd']}")


@case
def case_discharge_threshold_guarantees_no_net_negative_cycle():
    """An adversarial review finding: using ONE threshold for both charge and
    discharge lets a near-threshold pair straddle the round-trip-efficiency
    line (e.g. charge at 184, discharge at 186 with a 185 threshold and 0.9
    RTE -- 186 < 184/0.9=204.4 -- increases net emissions). The fix is a
    SEPARATE, higher discharge threshold (charge threshold / ETA**2). Prove
    the invariant this guarantees directly from the arithmetic, not just by
    trusting the code: for ANY charge at intensity <= threshold and ANY
    discharge at intensity > discharge_threshold, I_discharge is always
    > I_charge / ETA**2, so no allowed combination can be net-negative --
    without needing to track which specific charged kWh a pooled (non-FIFO)
    battery later delivers at which specific discharge. No real archive
    needed: this is a property of carbon_threshold()'s own output plus exact
    arithmetic, checked on a hand-built intensity array with values crossing
    both thresholds."""
    d = pd.DataFrame({"p": ["sop"] * 8})
    inten = np.array([50.0, 100.0, 150.0, 184.0, 185.0, 186.0, 250.0, 300.0])
    th, threshold, _dirty = CDT.carbon_threshold(d, inten)
    disch_threshold = th["discharge_kg_per_mwh"]
    eta = CDT.ETA
    # th's fields are rounded to 2 decimals for the artifact; threshold (the
    # function's second return value) is not, so allow for that rounding.
    assert abs(disch_threshold - threshold / (eta ** 2)) < 0.01, (
        disch_threshold, threshold, eta)
    assert disch_threshold > threshold, (
        "discharge threshold must sit strictly above the charge threshold")
    # worst-case charge (right at the threshold) vs worst-case discharge
    # (right above the discharge threshold): the round-trip-adjusted benefit
    # must still be non-negative
    # Use the UNROUNDED threshold on both sides (th's fields are rounded to 2
    # decimals for the artifact, which would otherwise leak a false positive
    # residual into this exact-arithmetic check).
    worst_charge_intensity = threshold
    worst_discharge_intensity = threshold / (eta ** 2)  # allowed discharge is > this
    net_kg_per_kwh_charged = (
        worst_charge_intensity - (eta ** 2) * worst_discharge_intensity)
    assert net_kg_per_kwh_charged <= 1e-9, (
        "even the worst allowed charge/discharge pairing is net-positive "
        f"for emissions: {net_kg_per_kwh_charged} kg/kWh charged")
    return (f"charge threshold {threshold:.1f}, discharge threshold "
           f"{disch_threshold:.1f} kg/MWh (= charge / ETA**2): worst-case "
           f"paired cycle nets {net_kg_per_kwh_charged:.4f} kg/kWh charged "
           "(<=0, i.e. never carbon-negative)")


# ---------------------------------------------------------------------------
# Dispatch-logic cases: small synthetic frames, exact expected outcomes.
# ---------------------------------------------------------------------------
@case
def case_ev_spillover_excluded_in_run_b_the_same_way_as_run_a():
    """Four hand-picked intervals, cap huge (100 kWh) so SOC never binds and
    PWRQ (2.875 kW*0.25h) is the only throughput constraint -- makes every
    row's outcome independently predictable regardless of processing order.

      row0: off-peak, high-kW (12 kW) import, DIRTY hour   -> excluded by BOTH
            A and B (kW gate fires for both; neither has an on-peak override
            here since hour=7 is not in 16-21).
      row1: off-peak, low-kW (1.6 kW) import, DIRTY hour    -> served by BOTH.
      row2: super-off-peak, CLEAN hour                       -> grid-charged by
            BOTH (A because p==sop; B because clean).
      row3: ON-PEAK (unconditional discharge in run_batt regardless of kW),
            high-kW (20 kW) import, CLEAN hour (inten below threshold) ->
            Run A DISCHARGES it anyway (the on-peak override run_batt always
            has); Run B does NOT discharge it (no on-peak override, and the
            EV-spillover gate excludes the high kW) -- B instead grid-charges
            further, since the hour reads as carbon-clean. This is exactly
            the documented, deliberate simplification in this module's
            docstring ("Run B" section) captured as an executable assertion,
            not left as an unverified claim.
    No _load_modules()/_require_archive() call: the module import at the top
    of this file already succeeded unconditionally (synthetic household), and
    this case's frame is entirely hand-built, so it needs no real archive.
    """
    d = pd.DataFrame({
        "p": ["off", "off", "sop", "on"],
        "hour": [7.0, 7.25, 2.0, 17.0],
    })
    imp0 = np.array([3.0, 0.4, 1.0, 5.0])
    gen0 = np.zeros(4)
    inten = np.array([300.0, 300.0, 50.0, 50.0])
    threshold = 100.0
    cap = 100.0

    iA, eA, servedA, thruA = CDT.bp.run_batt(d, imp0, gen0, cap, "greedy")
    iB, eB, servedB, thruB = CDT.run_batt_carbon(d, imp0, gen0, cap, inten, threshold)

    # row0: excluded by both (off-peak/dirty, high kW)
    assert iA[0] == imp0[0], ("Run A served the excluded high-kW off-peak row", iA[0])
    assert iB[0] == imp0[0], ("Run B served the excluded high-kW dirty row", iB[0])
    # row1: served by both (off-peak/dirty, low kW)
    assert iA[1] < imp0[1], "Run A did not serve the low-kW dirty row"
    assert iB[1] < imp0[1], "Run B did not serve the low-kW dirty row"
    # row2: grid-charged by both (sop / clean)
    assert iA[2] > imp0[2], "Run A did not grid-charge the sop row"
    assert iB[2] > imp0[2], "Run B did not grid-charge the clean row"
    # row3: A's on-peak override discharges despite high kW; B does not
    assert iA[3] < imp0[3], "Run A's on-peak override did not discharge row3"
    assert iB[3] > imp0[3], ("Run B unexpectedly did not grid-charge the "
                             "clean-but-high-kW on-peak row (expected no "
                             "discharge, since it lacks A's on-peak override)")
    assert servedA > 0 and servedB > 0 and thruA > 0 and thruB > 0
    return (f"row0 excluded by both (A {iA[0]}, B {iB[0]} == baseline {imp0[0]}); "
           f"row1 served by both (A {iA[1]:.2f}, B {iB[1]:.2f} < {imp0[1]}); "
           f"row2 charged by both (A {iA[2]:.2f}, B {iB[2]:.2f} > {imp0[2]}); "
           f"row3 diverges as documented: A discharges to {iA[3]:.2f}, B charges "
           f"further to {iB[3]:.2f}")


@case
def case_run_c_discharges_on_either_condition_and_charges_only_on_both():
    """Run C's OR-for-discharge / AND-for-charge design, on the same synthetic
    frame as the exclusion case above: row3 (Run A discharges for cost, Run B
    would rather charge for carbon -- a genuinely conflicting hour) must come
    out matching RUN A under the union rule, since disch_win is an OR and
    Run A's on-peak condition alone already satisfies it. row2 (both agree
    it's a good charging hour) must still charge. No _load_modules() call
    needed: hand-built frame, no real archive required."""
    d = pd.DataFrame({
        "p": ["off", "off", "sop", "on"],
        "hour": [7.0, 7.25, 2.0, 17.0],
    })
    imp0 = np.array([3.0, 0.4, 1.0, 5.0])
    gen0 = np.zeros(4)
    inten = np.array([300.0, 300.0, 50.0, 50.0])
    threshold = 100.0
    cap = 100.0

    iA, _, _, _ = CDT.bp.run_batt(d, imp0, gen0, cap, "greedy")
    iC, _, servedC, thruC = CDT.run_batt_union(d, imp0, gen0, cap, inten, threshold)

    assert iC[0] == imp0[0], "Run C served the row excluded by both A and B"
    assert iC[1] < imp0[1], "Run C did not serve the win-win discharge row"
    assert iC[2] > imp0[2], "Run C did not charge the win-win clean/sop row"
    # the conflicting row: Run C should match Run A (A's condition alone is
    # enough to satisfy the OR), not Run B's charge-more behavior
    assert abs(iC[3] - iA[3]) < EPS, (iC[3], iA[3])
    assert servedC > 0 and thruC > 0
    return (f"Run C matches A on the win-win rows and on the conflicting "
           f"on-peak row ({iC[3]:.4f} vs A's {iA[3]:.4f}), resolving the "
           "conflict in favor of discharge (OR), while still charging the "
           f"win-win clean/sop row ({iC[2]:.2f} > baseline {imp0[2]})")


# ---------------------------------------------------------------------------
# Energy conservation (a bound, not a strict identity, given RTE loss).
# ---------------------------------------------------------------------------
@case
def case_energy_conservation_bounds_hold_for_all_three_runs():
    """soc_final = cap/2 + thru - served/ETA (every charge step adds equally
    to soc and thru; every discharge step subtracts dd/ETA from soc and adds
    dd to served) and 0 <= soc_final <= cap, so:
        ETA*(thru - cap/2) <= served <= ETA*(thru + cap/2)
    is an exact, derivable bound (not a guess) for any run of this dispatch
    shape, regardless of policy. Recomputed here directly from the dispatch
    functions (not the rounded artifact figures) so rounding does not mask a
    real violation.
    """
    _, bp, _, CDT = _load_modules()
    import behavior_rebuild as br
    d = br.load()
    imp0 = d.Consumption.values.astype(float)
    gen0 = d.Generation.values.astype(float)
    inten_map, _, _ = CDT.build_intensity_map()
    inten = CDT.household_intensity(d, inten_map)
    _, threshold, _ = CDT.carbon_threshold(d, inten)
    cap = CDT.CAP
    eta = CDT.ETA

    runs = {
        "A": bp.run_batt(d, imp0, gen0, cap, "greedy")[2:4],
        "B": CDT.run_batt_carbon(d, imp0, gen0, cap, inten, threshold)[2:4],
        "C": CDT.run_batt_union(d, imp0, gen0, cap, inten, threshold)[2:4],
    }
    details = []
    for name, (served, thru) in runs.items():
        lower = eta * (thru - cap / 2)
        upper = eta * (thru + cap / 2)
        assert lower - 1e-6 <= served <= upper + 1e-6, (name, served, lower, upper)
        details.append(f"{name}: served={served:.1f} in [{lower:.1f}, {upper:.1f}]")
    return "energy conservation bound holds for all three runs: " + "; ".join(details)


# ---------------------------------------------------------------------------
# Fail-closed behaviour.
# ---------------------------------------------------------------------------
@case
def case_fails_closed_on_a_corrupt_intensity_csv():
    """A truncated/malformed committed CSV must abort build_intensity_map(),
    not silently produce a degraded map. No real archive needed: this writes
    its own throwaway CSV."""
    with tempfile.TemporaryDirectory() as td:
        bad = pathlib.Path(td) / "bad.csv"
        pd.DataFrame({"wrong": [1, 2, 3]}).to_csv(bad, index=False)
        old = CF.HOURLY_CSV
        CF.HOURLY_CSV = bad
        try:
            CDT.build_intensity_map()
            raise AssertionError("build_intensity_map accepted a malformed CSV")
        except SystemExit as e:
            assert "unexpected schema" in str(e), e
        finally:
            CF.HOURLY_CSV = old
    return "build_intensity_map fails closed (SystemExit) on a malformed committed CSV"


@case
def case_fails_closed_on_insufficient_coverage():
    """A committed CSV with real schema but too few covered days (< carbon_
    fullyear.COVERAGE_MIN) must abort rather than silently interpolate almost
    the whole year. No real archive needed: this writes its own throwaway
    CSV."""
    with tempfile.TemporaryDirectory() as td:
        thin = pathlib.Path(td) / "thin.csv"
        rows = [("2026-01-15", h, 200.0) for h in range(24)]
        pd.DataFrame(rows, columns=["date", "hour", "kgco2_per_mwh"]).to_csv(thin, index=False)
        old = CF.HOURLY_CSV
        CF.HOURLY_CSV = thin
        try:
            CDT.build_intensity_map()
            raise AssertionError("build_intensity_map accepted 1 covered day")
        except SystemExit as e:
            assert "COVERAGE_MIN" in str(e) or str(CF.COVERAGE_MIN) in str(e), e
        finally:
            CF.HOURLY_CSV = old
    return "build_intensity_map fails closed (SystemExit) on insufficient coverage"


@case
def case_fails_closed_when_bill_nem_raises():
    """If the billing engine itself fails, compute() must propagate the
    exception rather than catching it and publishing a zeroed/partial result.
    Monkeypatch battery_dispatch_policies.billed (which carbon_dispatch_
    tradeoff.py calls for every run) to raise, and confirm main() never
    writes an artifact."""
    _, bp, _, CDT = _load_modules()
    old = bp.billed

    def _boom(*a, **kw):
        raise RuntimeError("synthetic billing failure")

    bp.billed = _boom
    try:
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "would_not_be_written.json"
            try:
                CDT.main(out_path=out)
                raise AssertionError("main() swallowed a billing failure")
            except RuntimeError as e:
                assert "synthetic billing failure" in str(e)
            assert not out.exists(), "an artifact was written despite the billing failure"
    finally:
        bp.billed = old
    return "compute()/main() propagate a rates.bill_nem-path failure and write nothing"


@case
def case_fails_closed_on_intensity_length_mismatch():
    """household_intensity() must refuse a map that doesn't cover the
    household's actual analysis window rather than silently truncating or
    padding."""
    _, _, _, CDT = _load_modules()
    import behavior_rebuild as br
    d = br.load()
    empty_map = {}
    try:
        CDT.household_intensity(d, empty_map)
        raise AssertionError("household_intensity accepted an empty intensity map")
    except SystemExit as e:
        assert "intensity map" in str(e), e
    return "household_intensity fails closed (SystemExit) when the map does not cover the data"


# ---------------------------------------------------------------------------
# Byte-identical regeneration (CLAUDE.md section 9).
# ---------------------------------------------------------------------------
@case
def case_byte_identical_regeneration():
    _, _, _, CDT = _load_modules()
    with tempfile.TemporaryDirectory() as td:
        out1 = pathlib.Path(td) / "run1.json"
        out2 = pathlib.Path(td) / "run2.json"
        CDT.main(out_path=out1)
        CDT.main(out_path=out2)
        b1, b2 = out1.read_bytes(), out2.read_bytes()
        assert b1 == b2, "two regenerations produced different bytes"
    return "two independent regenerations produce byte-identical JSON"


# ---------------------------------------------------------------------------
# Direct numeric report of the issue's headline question (printed, not just
# asserted, so a reader of the test output sees the actual computed answer).
# ---------------------------------------------------------------------------
@case
def case_reports_the_tradeoff_numbers():
    """Not a tight assertion -- a readable printout of the actual answer to
    'does dispatching for money fight dispatching for carbon' plus the
    minimal sanity bounds (both penalties must have the expected SIGN: the
    carbon-min policy should not be cheaper than the cost-min one, and the
    cost-min policy should not have lower CO2 than the carbon-min one --
    otherwise the two objectives would not be in tension at all, which would
    itself be a surprising finding worth flagging, not silently passing)."""
    result, _ = _result()
    t = result["tradeoff"]
    a = result["policies"]["A_cost_min"]
    b = result["policies"]["B_carbon_min"]
    c = result["policies"]["C_union"]
    assert t["cost_penalty_of_clean_policy_usd"] >= 0, \
        "the carbon-min policy came out CHEAPER than the cost-min one -- surprising, verify"
    assert t["co2_penalty_of_cheap_policy_kg"] >= 0, \
        "the cost-min policy came out CLEANER than the carbon-min one -- surprising, verify"
    return (
        f"baseline ${result['baseline']['bill_usd']:,.2f}, {result['baseline']['net_co2_kg']:,.0f} kg net | "
        f"A(cost-min): ${a['bill_usd']:,.2f} save ${a['savings_vs_baseline_usd']:,.2f}, "
        f"{a['net_co2_kg']:,.0f} kg net avoided {a['co2_avoided_vs_baseline_kg']:,.0f} | "
        f"B(carbon-min): ${b['bill_usd']:,.2f} save ${b['savings_vs_baseline_usd']:,.2f}, "
        f"{b['net_co2_kg']:,.0f} kg net avoided {b['co2_avoided_vs_baseline_kg']:,.0f} | "
        f"C(union): ${c['bill_usd']:,.2f} save ${c['savings_vs_baseline_usd']:,.2f}, "
        f"{c['net_co2_kg']:,.0f} kg net avoided {c['co2_avoided_vs_baseline_kg']:,.0f} | "
        f"cost penalty of clean ${t['cost_penalty_of_clean_policy_usd']:,.2f}/yr | "
        f"CO2 penalty of cheap {t['co2_penalty_of_cheap_policy_kg']:,.1f} kg/yr (net) | "
        f"Run C meaningfully differs: {result['run_c_analysis']['meaningfully_differs_from_a_and_b']}")


# ---------------------------------------------------------------------------
# issue #44 follow-up review: compute() itself (not just its leaf functions)
# sat behind _require_archive() with no synthetic path at all, so a defect in
# the top-level assembly -- CO2 unit conversion, result-dict wiring, the
# baseline/Run-A/B/C aggregation -- could reach main invisibly to CI. This
# case runs the REAL compute() end to end on the already-proven synthetic
# Green Button fixture (test_scripts_runnable's), monkeypatching the SAME
# three module globals test_carbon_fullyear.py already established this
# pattern for (br.CSV, CF.HOURLY_CSV, CDT.DATA) rather than a throwaway-root
# subprocess, since this file already tests in-process throughout.
#
# The intensity source is the REAL, PUBLIC, committed data/caiso_hourly_
# intensity.csv (aggregate CAISO grid data, not household-specific -- no
# privacy concern, and its date range 2025-07-24..2026-07-23 already matches
# WINDOW_END, so the synthetic house's calendar dates land inside its
# coverage with no extra fixture work). The battery_dispatch_policies.json
# tie-out is promoted from THIS run's own bp.run_batt/bp.billed computation
# (the same call compute() itself makes for Run A), so it is satisfied for
# real, not neutered -- and because it is the same underlying computation,
# this specific tie-out mostly proves internal consistency; the independent
# value here is the hand-computed baseline CO2 check and the Run B/C
# DIRECTIONAL checks below, which a defect in compute()'s own assembly (as
# opposed to bp's dispatch engine, already covered elsewhere) would trip.
# ---------------------------------------------------------------------------
@case
def case_compute_runs_end_to_end_on_a_synthetic_house():
    import test_scripts_runnable as TSR

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "data").mkdir()
        usage = tmp / "usage.csv"
        TSR._synthetic_usage(usage)
        real_csv = ROOT / "data" / "caiso_hourly_intensity.csv"
        shutil.copy(real_csv, tmp / "data" / "caiso_hourly_intensity.csv")

        saved_csv, saved_hourly, saved_data = br.CSV, CF.HOURLY_CSV, CDT.DATA
        try:
            br.CSV = str(usage)
            CF.HOURLY_CSV = tmp / "data" / "caiso_hourly_intensity.csv"
            CDT.DATA = tmp / "data"

            d = br.load()
            imp0 = d.Consumption.values.astype(float)
            gen0 = d.Generation.values.astype(float)
            base = bp.billed(d, imp0, gen0)
            iA, eA, _, _ = bp.run_batt(d, imp0, gen0, CDT.CAP, "greedy", charge_kw=CDT.CHARGE_KW)
            billA = bp.billed(d, iA, eA)
            (tmp / "data" / "battery_dispatch_policies.json").write_text(json.dumps({
                "pw3": {"greedy": {"save": round(base - billA)}}}))

            # independent baseline-CO2 check: reuse household_intensity() as a
            # trusted building block (covered by this file's OTHER, already
            # archive-free cases), but do the FINAL aggregation with this
            # test's OWN formula, so a defect in compute()'s own KG-conversion
            # or aggregation wiring -- not in household_intensity() itself --
            # is what this specific check is sensitive to.
            inten_map, n_covered, _missing = CDT.build_intensity_map()
            assert n_covered >= CF.COVERAGE_MIN, (n_covered, "real committed CSV should be measured")
            inten = CDT.household_intensity(d, inten_map)
            exp_base_co2 = float((imp0 * inten).sum() * CDT.KG)
            exp_base_export_avoided = float((gen0 * inten).sum() * CDT.KG)
            exp_base_net_co2 = exp_base_co2 - exp_base_export_avoided

            result = CDT.compute()
        finally:
            br.CSV, CF.HOURLY_CSV, CDT.DATA = saved_csv, saved_hourly, saved_data

    assert abs(result["baseline"]["bill_usd"] - round(base, 2)) < 0.01, result["baseline"]
    assert abs(result["baseline"]["net_co2_kg"] - exp_base_net_co2) < 1.0, (
        result["baseline"], exp_base_net_co2)
    a, b, c = (result["policies"][k] for k in ("A_cost_min", "B_carbon_min", "C_union"))
    # directional sanity a defect in Run B/C's own logic would plausibly
    # break: all three battery policies save money (each policy's own stated
    # objective is a floor every one of them must clear), and the
    # CARBON-oriented policies (B, C -- the ones actually optimizing for it)
    # avoid net CO2 vs. no battery. Run A is cost-minimizing ONLY -- it is
    # NOT asserted to avoid CO2, because the whole thesis this generator
    # exists to test is that cost- and carbon-optimal dispatch can conflict
    # (confirmed on the real committed year: A's co2_avoided_vs_baseline_kg
    # is actually negative there too -- asserting it positive here would
    # assert the wrong physics, not guard against a defect).
    for name, p in (("A", a), ("B", b), ("C", c)):
        assert p["savings_vs_baseline_usd"] > 0, (name, p)
    for name, p in (("B", b), ("C", c)):
        assert p["co2_avoided_vs_baseline_kg"] > 0, (name, p)
    t = result["tradeoff"]
    assert t["cost_penalty_of_clean_policy_usd"] >= 0, t
    assert t["co2_penalty_of_cheap_policy_kg"] >= 0, t
    assert json.dumps(result), "compute() result is not JSON-serializable"
    return ("compute() runs end to end on a synthetic house against the real "
            "committed CAISO intensity data; baseline bill and net CO2 match "
            "hand computation, every policy saves money, the two "
            "carbon-oriented policies avoid net CO2, and the tradeoff signs "
            "hold")


# ---------------------------------------------------------------------------
# issue #40 -- charge and discharge power are now DISTINCT, optional
# parameters on run_batt_carbon/run_batt_union, not one shared PWRQ serving
# both directions.
# ---------------------------------------------------------------------------
@case
def case_run_batt_carbon_and_union_thread_a_distinct_charge_kw():
    """AC5: charge_kw must be a SEPARATE parameter from the module's
    discharge-direction PWRQ, actually wired to the charging branches (not
    silently dropped), on both run_batt_carbon and run_batt_union. A single
    interval with a large solar surplus and an empty battery isolates the
    charging RATE (see test_battery_sizing_curve.py's identical technique
    and its docstring for why a multi-interval fixture would mask this)."""
    import inspect
    assert "charge_kw" in inspect.signature(CDT.run_batt_carbon).parameters
    assert "charge_kw" in inspect.signature(CDT.run_batt_union).parameters

    d = pd.DataFrame({"p": ["off"], "hour": [12.0]})
    imp0 = np.array([0.0])
    gen0 = np.array([5.0])
    inten = np.array([50.0])
    threshold = 100.0  # this hour reads as chargeable (clean) for both functions
    cap = 13.5

    for fn, name in [(CDT.run_batt_carbon, "run_batt_carbon"),
                     (CDT.run_batt_union, "run_batt_union")]:
        _, _, _, thru_sym = fn(d, imp0, gen0, cap, inten, threshold)
        _, _, _, thru_asym = fn(d, imp0, gen0, cap, inten, threshold, charge_kw=5.0)
        assert thru_asym < thru_sym - EPS, (
            f"{name}: a tighter charge_kw=5.0 must reduce charging throughput "
            f"below the symmetric default ({thru_asym} vs {thru_sym})")
    assert CDT.PWRQ * 4 != 5.0, "the module's discharge PWRQ and this test's charge_kw must differ"
    return "run_batt_carbon and run_batt_union thread a distinct, independently-effective charge_kw"


# ---------------------------------------------------------------------------
# The EV-spillover exclusion is gated on the intake flag (issue #246). Runs B
# and C carry their own copy of run_batt's >= 2.5 kW rule; on a household
# whose intake says household.has_ev is false there is no spillover, and the
# rule would withhold ordinary house load from the battery. br.EV_ANALYSIS is
# that flag, read at call time, so each case sets it, runs, and restores it.
# ---------------------------------------------------------------------------
def _one_dirty_offpeak_spike():
    """One off-peak, dirty-hour interval importing 12 kW (3.0 kWh) and nothing
    else. cap huge so SOC never binds: the only cap on service is PWRQ, so the
    served energy is exactly PWRQ (2.875 kWh) when the rule lets it through."""
    d = pd.DataFrame({"p": ["off"], "hour": [7.0]})
    return d, np.array([3.0]), np.zeros(1), np.array([300.0]), 100.0, 100.0


def _served_by(fn, has_ev):
    d, imp0, gen0, inten, threshold, cap = _one_dirty_offpeak_spike()
    was = br.EV_ANALYSIS
    br.EV_ANALYSIS = has_ev
    try:
        imp, _exp, served, _thru = fn(d, imp0, gen0, cap, inten, threshold)
    finally:
        br.EV_ANALYSIS = was
    return served, float(imp0[0] - imp[0])


@case
def case_no_ev_household_run_b_serves_a_high_power_offpeak_import():
    served, delta = _served_by(CDT.run_batt_carbon, has_ev=False)
    assert abs(served - CDT.PWRQ) < EPS and abs(delta - CDT.PWRQ) < EPS, (
        f"Run B on a no-EV household did not serve the 12 kW dirty off-peak "
        f"import: served {served} kWh, expected {CDT.PWRQ}")
    return f"household.has_ev false: Run B serves {served:.3f} kWh of a 12 kW off-peak import"


@case
def case_ev_household_run_b_still_excludes_that_import():
    """Positive control for Run B: the same interval stays unserved with an EV,
    so the case above cannot pass against a build that deleted the rule."""
    served, delta = _served_by(CDT.run_batt_carbon, has_ev=True)
    assert served < EPS and delta < EPS, (
        f"Run B on an EV household served the 12 kW off-peak import: {served} kWh")
    return "household.has_ev true: Run B serves 0 kWh of the same interval"


@case
def case_no_ev_household_run_c_serves_a_high_power_offpeak_import():
    served, delta = _served_by(CDT.run_batt_union, has_ev=False)
    assert abs(served - CDT.PWRQ) < EPS and abs(delta - CDT.PWRQ) < EPS, (
        f"Run C on a no-EV household did not serve the 12 kW dirty off-peak "
        f"import: served {served} kWh, expected {CDT.PWRQ}")
    return f"household.has_ev false: Run C serves {served:.3f} kWh of a 12 kW off-peak import"


@case
def case_ev_household_run_c_still_excludes_that_import():
    """Positive control for Run C, both of whose discharge conditions carry
    the rule."""
    served, delta = _served_by(CDT.run_batt_union, has_ev=True)
    assert served < EPS and delta < EPS, (
        f"Run C on an EV household served the 12 kW off-peak import: {served} kWh")
    return "household.has_ev true: Run C serves 0 kWh of the same interval"


@case
def case_runs_b_and_c_gate_on_the_intake_flag_not_the_detector():
    """The gate is br.EV_ANALYSIS, the declared flag, in both policies. A
    detector that found no sessions is not the same fact."""
    import inspect
    for fn in (CDT.run_batt_carbon, CDT.run_batt_union):
        src = inspect.getsource(fn)
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        assert "br.EV_ANALYSIS" in code, f"{fn.__name__} no longer gates on br.EV_ANALYSIS"
        assert "detect_sessions" not in code, f"{fn.__name__} reads the EV detector"
    return "run_batt_carbon and run_batt_union both read br.EV_ANALYSIS and never the detector"


def main():
    listed = [c.__name__ for c in CASES]
    assert len(listed) == len(set(listed)), \
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}"
    ran = skipped = failures = 0
    for fn in CASES:
        try:
            detail = fn()
        except SkipCase as e:
            print(f"SKIP {fn.__name__} ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            failures += 1
        except suite_runner.CASE_FAILURES as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            failures += 1
        else:
            print(f"ok   {fn.__name__} -- {detail}")
            ran += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
