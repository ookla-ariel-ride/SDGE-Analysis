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
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

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
        f"baseline ${result['baseline']['bill_usd']:,.2f}, {result['baseline']['co2_kg']:,.0f} kg | "
        f"A(cost-min): ${a['bill_usd']:,.2f} save ${a['savings_vs_baseline_usd']:,.2f}, "
        f"{a['co2_kg']:,.0f} kg avoided {a['co2_avoided_vs_baseline_kg']:,.0f} | "
        f"B(carbon-min): ${b['bill_usd']:,.2f} save ${b['savings_vs_baseline_usd']:,.2f}, "
        f"{b['co2_kg']:,.0f} kg avoided {b['co2_avoided_vs_baseline_kg']:,.0f} | "
        f"C(union): ${c['bill_usd']:,.2f} save ${c['savings_vs_baseline_usd']:,.2f}, "
        f"{c['co2_kg']:,.0f} kg avoided {c['co2_avoided_vs_baseline_kg']:,.0f} | "
        f"cost penalty of clean ${t['cost_penalty_of_clean_policy_usd']:,.2f}/yr | "
        f"CO2 penalty of cheap {t['co2_penalty_of_cheap_policy_kg']:,.1f} kg/yr | "
        f"Run C meaningfully differs: {result['run_c_analysis']['meaningfully_differs_from_a_and_b']}")


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
        except Exception as exc:  # noqa: BLE001
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
