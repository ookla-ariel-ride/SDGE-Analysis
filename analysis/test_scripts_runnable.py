#!/usr/bin/env python3
"""Catch analysis scripts that have quietly stopped being runnable or producing.

This repo has been bitten three times by scripts that sat in analysis/ looking
maintained while being unrunnable (analyze.py and analyze_norelief.py carried
absolute paths into a retired sandbox; carbon_timing.py read a directory that was
never committed), and once by a guard that skipped everywhere it mattered and by
assertions that checked exit codes while a generator could run and produce
nothing. Every failure mode was silence, so the tiers below are explicit about
what executes where and every execution is followed by an assertion on OUTPUT,
not just on the exit code.

  structural   parses, classified in MANIFEST, retired scripts refuse to run
               (verified by running them), no absolute paths outside the repo,
               libraries import cleanly. Runs everywhere.
  synthetic    the CI_RUNNABLE generators execute against an invented but
               structurally faithful Green Button fixture, and their outputs are
               checked for content (artifacts written, sessions detected, DST
               days seen). No skip path; this is what protects main in CI.
  real         every generator executes against the private archive, and each
               artifact it owns must reproduce the committed copy byte-for-byte
               -- the CLAUDE.md section 9 gate, folded into the suite. Skips
               only where the private archive is absent.

MANIFEST classifies every script; OWNS declares which committed artifacts each
generator writes and where, which both drives the byte-diff and forbids two
generators from claiming the same output file.
"""
import ast
import json
import datetime as dt
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parent.parent
ANALYSIS = ROOT / "analysis"
SANDBOX = ROOT / "private" / "verify"

# role: "generator" writes a committed artifact and must run; "library" is imported
# by others and must import cleanly with no side effects; "retired" is kept for
# provenance and must refuse to run.
MANIFEST = {
    "rates.py": "library",
    "rates_history.py": "generator",
    "household.py": "library",
    "privacy_tiers.py": "library",
    "publish.py": "library",
    "report_tokens.py": "library",
    "llm_providers.py": "library",
    "report_blocks.py": "library",
    "generate_report.py": "library",
    "prose_lint.py": "library",
    "behavior_rebuild.py": "generator",
    "battery_dispatch_policies.py": "generator",
    "battery_plan_matrix.py": "generator",
    "battery_sizing_curve.py": "generator",
    "perfect_foresight_dispatch.py": "generator",
    "tou_structure_stress.py": "generator",
    "package_results.py": "generator",
    "extended_findings.py": "generator",
    "report_data.py": "generator",
    "deep_analyses.py": "generator",
    "uncertainty_propagation.py": "generator",
    "battery_backup_sims.py": "generator",
    "billing_model_nem.py": "library",
    "lifetime_payback.py": "generator",
    "tou_spread.py": "generator",
    "analyze.py": "generator",
    "analyze_norelief.py": "generator",
    "carbon_fullyear.py": "generator",
    "carbon_dispatch_tradeoff.py": "generator",
    "soiling_analysis.py": "generator",
    "parse_bills.py": "generator",
    "bill_decomposition.py": "generator",
    "tou_audit.py": "generator",
    "service_headroom.py": "generator",
    "irreducible_bill.py": "generator",
    "nem3_grandfathering.py": "generator",
    "dsgs_vpp_backtest.py": "generator",
    "cca_rate_extraction.py": "generator",
    "cca_bundled_counterfactual.py": "generator",
    "gross_import_decomposition.py": "generator",
    "reprice_by_vintage.py": "generator",
    "quiet_night_floor.py": "generator",
    "threeway_production_validation.py": "generator",
    "heat_pump_conversion.py": "generator",
    "extra_results.py": "generator",
    "carbon_timing.py": "retired",
}

# Which committed artifacts each generator writes, and where it writes them:
# "cwd" = the sandbox convention (script writes into the working directory; the
# documented gate compares that copy against data/), "data" = written into
# ROOT/data directly. Drives the real-tier byte-diff and the no-two-owners check.
# Not listed: gitignored run products (stats.json, *_relief*) and parse_bills.py,
# whose six artifacts have their own transactional gate and test suite.
OWNS = {
    "rates_history.py":             [("data", "rate_vintages.csv"),
                                     ("data", "rate_rebilling_residuals.csv")],
    "behavior_rebuild.py":          [("cwd", "behavior_rebuild.json")],
    "battery_dispatch_policies.py": [("cwd", "battery_dispatch_policies.json")],
    "battery_plan_matrix.py":       [("data", "battery_plan_matrix.json")],
    "battery_sizing_curve.py":      [("data", "battery_sizing_curve.json")],
    "perfect_foresight_dispatch.py": [("data", "perfect_foresight_dispatch.json")],
    "tou_structure_stress.py":      [("data", "tou_structure_stress.json")],
    "package_results.py":           [("data", "package_results.json")],
    "extended_findings.py":         [("data", "extended_results.json")],
    "report_data.py":               [("data", "report_data.json")],
    "deep_analyses.py":             [("cwd", "deep_results.json")],
    "uncertainty_propagation.py":   [("cwd", "uncertainty_results.json")],
    "battery_backup_sims.py":       [("cwd", "battery_sim.json"),
                                     ("cwd", "backup_endurance.json")],
    "analyze_norelief.py":          [("data", "plan_results.csv"),
                                     ("data", "hourly_profile.csv"),
                                     ("data", "monthly.csv")],
    "carbon_fullyear.py":           [("data", "carbon_fullyear_results.json"),
                                     ("data", "caiso_hourly_intensity.csv")],
    "carbon_dispatch_tradeoff.py":  [("data", "carbon_dispatch_tradeoff.json")],
    "tou_audit.py":                 [("data", "tou_audit.csv"),
                                     ("data", "tou_audit_summary.json")],
    "lifetime_payback.py":          [("data", "lifetime_payback.json")],
    "bill_decomposition.py":        [("data", "bill_decomposition.json")],
    "tou_spread.py":                [("data", "tou_spread.json")],
    "service_headroom.py":          [("data", "service_headroom.json")],
    "irreducible_bill.py":          [("data", "irreducible_bill.json")],
    "nem3_grandfathering.py":       [("data", "nem3_grandfathering.json")],
    # Not listed: dsgs_event_calendar_2025.csv, which only build_calendar() writes,
    # gated behind --build-calendar (never passed by this generic runner) -- same
    # shape as nem3_grandfathering.py's RATE_CSV/--build-rates, above.
    "dsgs_vpp_backtest.py":         [("data", "dsgs_vpp_backtest.json")],
    "cca_rate_extraction.py":       [("data", "cca_generation_rates.csv")],
    "cca_bundled_counterfactual.py": [("data", "cca_bundled_counterfactual.json")],
    "gross_import_decomposition.py": [("cwd", "gross_import_decomposition.json")],
    # writes directly into ROOT/data (found via its own _repo_root() walk-up),
    # exactly like rates_history.py -- NOT the cwd-then-promote convention
    # gross_import_decomposition.py uses.
    "reprice_by_vintage.py":        [("data", "reprice_by_vintage.json")],
    # writes directly into ROOT/data via its own repo_root() walk-up, same
    # convention as tou_structure_stress.py/rates_history.py/reprice_by_vintage.py
    "quiet_night_floor.py":         [("data", "quiet_night_floor.json")],
    # writes directly into ROOT/data via its own repo_root() walk-up, same
    # convention as quiet_night_floor.py/rates_history.py
    "threeway_production_validation.py": [("data", "threeway_production_validation.csv")],
    # writes directly into ROOT/data via its own repo_root() walk-up, same
    # convention as threeway_production_validation.py/quiet_night_floor.py
    "heat_pump_conversion.py":      [("data", "heat_pump_conversion.json")],
    "extra_results.py":             [("data", "extra_results.json")],
}

# Modules allowed to express TOU windows themselves. The legacy ranking pair keeps
# its own calendar by design (TECHNICAL.md 3.1/3.2); tou_audit scores alternative
# day-type rules against the bills on purpose; rates.py is where the rule lives.
# report_tokens.py never assigns a timestamp to a period -- it only reads the
# "on"/"off"/"sop" labels already assigned elsewhere (report_data.json's
# periods_chart.order, an already-computed artifact) and calls rates.py's own
# energy()/credit()/allin() with a period letter, so it trips this AST guard's
# literal-string check without doing the thing the guard exists to catch.
# report_blocks.py's §13 price-map row builder does exactly the same thing
# (iterates the same three period-letter constants and calls rates.py's own
# allin()/credit() with each) for the same reason -- same exemption rationale.
# generate_report.py's chart-data filler only ASSERTS that report_data.json's
# own already-computed periods_chart.order equals ["sop","off","on"] before
# trusting its positional indexing -- reading, not assigning, a label.
TOU_EXEMPT = {"rates.py", "analyze.py", "analyze_norelief.py", "tou_audit.py",
              "report_tokens.py", "report_blocks.py", "generate_report.py"}

ABS_PATH = re.compile(r"""["'](/[A-Za-z0-9_.\-]+/[^"']*)["']""")

CI_WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
COVERAGE_SCRIPT = ANALYSIS / "check_coverage.sh"


class SkipCase(Exception):
    """Typed skip signal (matching test_parse_bills.py's convention, issue #44
    AC4) -- a case raises this instead of returning a "SKIP ..."-prefixed
    string, so a case that legitimately returns a message starting with those
    five letters can never be silently miscounted as skipped."""


def _scripts():
    return sorted(f for f in ANALYSIS.glob("*.py") if not f.name.startswith("test_"))


def case_no_two_generators_own_the_same_artifact():
    """analyze.py and analyze_norelief.py used to collide on four filenames, with
    whichever ran last winning silently. Ownership must be exclusive."""
    seen = {}
    for name, artifacts in OWNS.items():
        for _, fname in artifacts:
            assert fname not in seen, (
                f"{fname} claimed by both {seen[fname]} and {name}")
            seen[fname] = name
    unknown = set(OWNS) - set(MANIFEST)
    assert not unknown, f"OWNS lists scripts missing from MANIFEST: {sorted(unknown)}"
    return f"{len(seen)} owned artifacts, each with exactly one generator"


def case_manifest_is_complete_and_exact():
    on_disk = {f.name for f in _scripts()}
    listed = set(MANIFEST)
    assert not on_disk - listed, f"scripts not classified in MANIFEST: {sorted(on_disk - listed)}"
    assert not listed - on_disk, f"MANIFEST lists missing scripts: {sorted(listed - on_disk)}"
    return f"all {len(on_disk)} analysis scripts are explicitly classified"


def case_every_script_parses():
    bad = []
    for f in _scripts():
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            bad.append(f"{f.name}: {e}")
    assert not bad, bad
    return f"all {len(_scripts())} analysis scripts parse"


def case_no_absolute_paths_outside_the_repo():
    """Existence-independent: a wrong-but-existing path (or one that exists only
    on the author's machine) is just as dead as a missing one. Any absolute
    literal outside the repo is flagged, everywhere, deterministically."""
    offenders = []
    for f in _scripts():
        for m in ABS_PATH.finditer(f.read_text()):
            path = m.group(1)
            if path.startswith(str(ROOT)):
                continue
            offenders.append(f"{f.name}: {path}")
    assert not offenders, f"absolute path literals outside the repo: {offenders}"
    return "no analysis script hardcodes an absolute path outside the repo"


def case_retired_scripts_say_so_and_refuse_to_run():
    """Behavioral: each retired script is actually RUN and must refuse.

    The previous version grepped the source for the string "SystemExit", which a
    comment satisfies -- checking for one spelling of a property instead of the
    property. Running the script is the property.
    """
    for name, role in sorted(MANIFEST.items()):
        if role != "retired":
            continue
        doc = ast.get_docstring(ast.parse((ANALYSIS / name).read_text())) or ""
        assert "RETIRED" in doc, f"{name}: retired but its docstring does not say so"
        r = subprocess.run([sys.executable, str(ANALYSIS / name)],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode != 0, f"{name}: retired but exits 0 when run"
        assert "RETIRED" in (r.stderr + r.stdout), (
            f"{name}: refuses to run but without naming its retirement: "
            f"{(r.stderr or r.stdout)[-120:]}")
    n = sum(1 for r in MANIFEST.values() if r == "retired")
    return f"{n} retired script(s) verified by execution to refuse with the notice"


def case_tou_assignment_comes_from_the_canonical_module():
    """AST-level: any module handling TOU period labels must defer to rates.

    The previous text-only check looked for `16 <= h < 21` and so missed
    battery_plan_matrix.py's vectorised `(h >= 16) & (h < 21)`, which is the same
    rule written differently. Detecting the period LABELS instead is far harder to
    evade by rewriting, because any implementation has to name its outputs.
    """
    offenders = []
    for f in _scripts():
        if f.name in TOU_EXEMPT:
            continue
        src = f.read_text()
        consts = {n.value for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        if not {"on", "off", "sop"} <= consts:
            continue
        # It may call the canonical assignment, or import the loader whose frame
        # already carries it. Both are checked in the AST, not as substrings: a
        # comment mentioning period_at must not exempt a module (that is the
        # battery_plan_matrix failure mode this case exists to prevent).
        tree = ast.parse(src)
        calls_period_at = any(
            isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Attribute) and n.func.attr == "period_at")
                or (isinstance(n.func, ast.Name) and n.func.id == "period_at"))
            for n in ast.walk(tree))
        imports_loader = any(
            (isinstance(n, ast.Import) and any(a.name == "behavior_rebuild" for a in n.names))
            or (isinstance(n, ast.ImportFrom) and n.module == "behavior_rebuild")
            for n in ast.walk(tree))
        if calls_period_at or imports_loader:
            continue
        offenders.append(f.name)
    assert not offenders, f"modules labelling TOU periods without rates.period_at: {offenders}"
    return "every non-exempt module gets its TOU labels from rates.period_at"


def _import_check(name):
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0,'{ANALYSIS}'); "
                        f"import {name[:-3]}"], capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stderr or "").strip().splitlines()[-1:]


def case_libraries_import_cleanly():
    bad = []
    for name, role in sorted(MANIFEST.items()):
        if role != "library":
            continue
        code, err = _import_check(name)
        if code != 0:
            bad.append(f"{name}: {err}")
    assert not bad, bad
    n = sum(1 for r in MANIFEST.values() if r == "library")
    return f"all {n} library modules import with no side effects"


# Generators that need only a Green Button export, a household file and the
# committed data/ directory. These MUST execute in CI, against synthetic inputs
# when the private export is absent.
CI_RUNNABLE = {
    "behavior_rebuild.py", "battery_dispatch_policies.py", "package_results.py",
    "report_data.py", "analyze.py", "analyze_norelief.py",
    "carbon_fullyear.py", "tou_audit.py",
    # reads only the two committed bill artifacts, so it runs anywhere
    "rates_history.py",
    # reads only data/bill_tou_detail.csv and data/battery_dispatch_policies.json,
    # both committed, so it runs anywhere too
    "tou_spread.py",
    # needs only usage.csv (via behavior_rebuild.load()), the committed PUBLIC
    # rate table data/nbt_export_rates_2026.csv (exhaustive over all 12 months x
    # 2 day-types x 24 hours, so any calendar-real synthetic export lands in a
    # covered bucket), and the committed data/extended_results.json (read-only
    # reconciliation reference) -- no raw MIDAS archive needed for the normal
    # run (only --build-rates needs that, and CI never passes that flag)
    "nem3_grandfathering.py",
    # needs only usage.csv (via behavior_rebuild.load()) and the committed PUBLIC
    # data/dsgs_event_calendar_2025.csv (CEC policy data, not household-specific)
    # -- no raw CEC xlsx needed for the normal run (only --build-calendar needs
    # that, and CI never passes that flag)
    "dsgs_vpp_backtest.py",
    # needs only usage.csv (via behavior_rebuild.load()) and household.yaml, same
    # shape as battery_dispatch_policies.py above; no tie-out assertion against
    # archive-derived data inside the generator itself (unlike battery_plan_matrix.py)
    "battery_sizing_curve.py",
    # needs only usage.csv (via behavior_rebuild.load()) and household.yaml; its
    # canonical-artifact cross-check (battery_dispatch_policies.json) is read
    # read-only and optional (skipped gracefully if absent), not a hard tie-out
    # assertion, so synthetic CI inputs run it cleanly
    "perfect_foresight_dispatch.py",
    # needs only usage.csv (via behavior_rebuild.load()) and household.yaml; it
    # recomputes its own CURRENT-structure figures fresh (no sibling artifact
    # read at all, unlike perfect_foresight_dispatch.py's optional cross-check)
    "tou_structure_stress.py",
    # reads only the committed data/extra_results.json (a documented, dated
    # historical constant stands in for what would otherwise need a private-
    # data-requiring import -- see the module's own docstring), so it runs
    # anywhere too (same shape as tou_spread.py above)
    "extra_results.py",
}
# Generators that additionally need raw private inputs which have no synthetic
# stand-in: the bill PDFs, the SAM 8760 exports, the monitoring history. These run
# only where that archive exists, and the reason is recorded rather than implied.
NEEDS_PRIVATE_ARCHIVE = {
    "parse_bills.py": "the bill PDF corpus (private/1-raw-data/*-bills/)",
    "bill_decomposition.py": ("the bill PDF corpus (the charged CEA per-TOU rates and "
                              "the billing-mode sentences are printed nowhere else) and "
                              "the billing-history export"),
    "extended_findings.py": "the SAM 8760 exports (private/1-raw-data/enphase_sam8760_*.csv)",
    "lifetime_payback.py": "the SAM full-year export (samB.csv)",
    "soiling_analysis.py": "the monitoring production history",
    "deep_analyses.py": "the SAM full-year export (samB.csv)",
    "battery_backup_sims.py": "the SAM full-year export (samB.csv)",
    "service_headroom.py": ("the SAM 8760 exports (the only independent gross-load "
                            "instrument) and the raw Green Button export, neither of "
                            "which has a stand-in carrying a real solar day"),
    "irreducible_bill.py": ("the bill PDF corpus (private/1-raw-data/electric-bills/*.pdf, "
                            "same dependency shape as parse_bills.py and "
                            "bill_decomposition.py) plus data/bill_periods_electric.csv and "
                            "data/bill_tou_detail.csv, which themselves derive from that "
                            "corpus"),
    "battery_plan_matrix.py": ("its fail-closed tie-out compares against "
                               "battery_dispatch_policies.json, which is built from the "
                               "real year, so invented inputs must diverge"),
    "carbon_dispatch_tradeoff.py": ("its cross-check compares its own freshly-computed "
                                    "Run A saving against the committed "
                                    "battery_dispatch_policies.json's pw3.greedy.save "
                                    "(built from the real year) within a $5 tolerance -- "
                                    "the same tie-out shape as battery_plan_matrix.py, "
                                    "so synthetic inputs must diverge and trip it"),
    "heat_pump_conversion.py": ("the raw gas Green Button export "
                               "(private/1-raw-data/gas.csv, daily therms) -- no "
                               "synthetic stand-in exists, and the isolation methods "
                               "need a real, physically plausible year of gas usage "
                               "to cross-check against real weather, not an invented one"),
    "cca_rate_extraction.py": ("the bill PDF corpus (every CCA-era statement's own "
                               "per-TOU generation-charge lines are printed nowhere "
                               "else)"),
    "cca_bundled_counterfactual.py": ("the bill PDF corpus (via data/cca_generation_rates.csv "
                                      "and data/bill_tou_detail.csv, which themselves derive "
                                      "from it) -- same dependency shape as irreducible_bill.py"),
    "uncertainty_propagation.py": ("its hard tie-out recomputes the real dispatch engine's "
                                   "pre-/post-behavior battery marginals and compares them "
                                   "against the committed battery_dispatch_policies.json "
                                   "(built from the real year) within a $1 tolerance, and "
                                   "separately reproduces data/deep_results.json's "
                                   "monte_carlo block exactly -- same tie-out shape as "
                                   "battery_plan_matrix.py, so synthetic inputs must diverge "
                                   "and trip it"),
    "gross_import_decomposition.py": ("both SAM 8760 exports (samA.csv/samB.csv, the only "
                                      "independent gross-load instrument -- same dependency "
                                      "as service_headroom.py) and the raw Green Button "
                                      "export, none of which has a stand-in carrying a real "
                                      "two-year-apart pair of billing periods"),
    "reprice_by_vintage.py": ("the raw Green Button export (usage.csv, via "
                              "billing_model_nem.load()) for the interval data it reconciles "
                              "against the 13-period bill corpus"),
    "quiet_night_floor.py": ("both SAM 8760 exports (samA.csv/samB.csv, the only "
                             "independent gross-load instrument that can see the "
                             "day-side floor -- same dependency as service_headroom.py "
                             "and gross_import_decomposition.py), none of which has a "
                             "synthetic stand-in carrying a real, continuous always-on "
                             "signature"),
    "threeway_production_validation.py": ("both SAM 8760 exports (samA.csv/samB.csv, "
                             "the only independent gross-load instrument -- same "
                             "dependency as service_headroom.py and gross_import_"
                             "decomposition.py) and the raw Green Button export, "
                             "none of which has a stand-in carrying a real solar year"),
}

# Generators listed above that nonetheless run END TO END IN CI -- just not
# through the shared Green-Button-only synthetic fixture below, which is not
# shaped for what they specifically need (a SAM-8760 pair, a promoted dispatch
# artifact, a monitoring production history, ...). Each maps to its own
# dedicated test_<name>.py AND the exact case name(s) inside it that must
# genuinely PASS (never skip) with no private archive present -- not just "a
# file exists with this name somewhere in tests.yml". That distinction is not
# decorative: an earlier version of this dict listed 15 entries by file name
# alone, and a clean-room review (git-archive checkout, no private/) found 9
# of them false -- every case in those files that actually invokes the real
# generator (subprocess.run, B.write(tmp), CX.write(...), ...) SKIPS without
# the archive; only leaf/unit checks on synthetic text passed. The self-check
# below (case_verified_elsewhere_mapping_is_real_and_wired_into_ci) now
# EXECUTES every entry's named case(s) in a freshly-built archive-free root
# and fails if any of them skips, specifically so this dict cannot silently
# regress to asserting something false again.
#
# Only 7 of the 16 NEEDS_PRIVATE_ARCHIVE generators are covered this way today
# (battery_plan_matrix.py added after its "genuinely cannot" claim was
# disproved by a working demonstration -- an independently computed
# reference, not the generator's own committed real-year artifact, can
# satisfy its fail-closed tie-outs for real). The other 9 (parse_bills.py,
# bill_decomposition.py, irreducible_bill.py, carbon_dispatch_tradeoff.py,
# cca_rate_extraction.py, cca_bundled_counterfactual.py,
# uncertainty_propagation.py, gross_import_decomposition.py,
# reprice_by_vintage.py) are NOT verified end to end anywhere in CI as of this
# commit -- see TECHNICAL.md 6.7 for the honest per-generator accounting of
# why each one is or is not yet covered, and which are believed tractable vs.
# disproportionate.
VERIFIED_ELSEWHERE_IN_CI = {
    "service_headroom.py": ("test_service_headroom.py",
                            ["case_build_runs_end_to_end_on_a_synthetic_house"]),
    "battery_backup_sims.py": ("test_battery_backup_sims.py",
                               ["case_arbitrage_sim_matches_hand_computation",
                                "case_backup_endurance_matches_hand_computation"]),
    "deep_analyses.py": ("test_deep_analyses.py",
                         ["case_deep_analyses_end_to_end_matches_hand_and_oracle_computations"]),
    "lifetime_payback.py": ("test_lifetime_payback.py",
                            ["case_derive_blended_matches_hand_computation"]),
    "soiling_analysis.py": ("test_soiling_analysis.py",
                            ["case_soiling_regression_recovers_the_injected_rate"]),
    "extended_findings.py": ("test_extended_findings.py",
                             ["case_extended_findings_end_to_end_on_a_synthetic_house"]),
    "battery_plan_matrix.py": ("test_battery_plan_matrix.py",
                               ["case_battery_plan_matrix_end_to_end_on_a_synthetic_house"]),
    "carbon_dispatch_tradeoff.py": ("test_carbon_dispatch_tradeoff.py",
                                    ["case_compute_runs_end_to_end_on_a_synthetic_house"]),
}

# The fixture window is DERIVED from the pipeline's anchor date so re-pointing
# the analysis year updates the fixture automatically. behavior_rebuild.py runs
# code at import (its module level reads private/household.yaml and fails closed
# without it -- correct for the generator, fatal for a clean CI checkout), so
# WINDOW_END is parsed out of the source text instead of imported.
_WINDOW_END_RE = re.compile(
    r"^WINDOW_END\s*=\s*dt\.datetime\((\d{4}),\s*(\d{1,2}),\s*(\d{1,2})\)", re.M)


def _pipeline_window_end():
    m = _WINDOW_END_RE.search((ANALYSIS / "behavior_rebuild.py").read_text())
    assert m, ("WINDOW_END not found in behavior_rebuild.py -- the synthetic "
               "fixture derives its date range from it and cannot anchor itself")
    return dt.date(*map(int, m.groups()))


WINDOW_END = _pipeline_window_end()
# Bracket the analysis year (the 365 days before WINDOW_END) with a few days of
# slack on each side so the loaders' boundary handling is exercised.
SYNTH_START = WINDOW_END - dt.timedelta(days=369)
SYNTH_END = WINDOW_END + dt.timedelta(days=2)

# The fixture's DST days are derived from the window too, with
# rates.dst_transition_sundays as the single authority on the US rule;
# hardcoded dates would silently fall out of a re-pointed window and gut the
# tou_audit calendar checks. Transitions are SETS, not one pair: consecutive
# US spring-forward Sundays can be as little as 364 days apart, so a legitimate
# WINDOW_END anchored near early March puts TWO spring transitions inside this
# 372-day fixture range. Every transition inside the range gets its 92/100-slot
# synthetic day, and the tou_audit expectation is derived from these same sets.
sys.path.insert(0, str(ANALYSIS))
import rates as _rates

_transitions = [_rates.dst_transition_sundays(y)
                for y in range(SYNTH_START.year, SYNTH_END.year + 1)]
DST_SPRINGS = frozenset(s for s, _ in _transitions if SYNTH_START <= s <= SYNTH_END)
DST_FALLS = frozenset(f for _, f in _transitions if SYNTH_START <= f <= SYNTH_END)
DST_DATES = DST_SPRINGS | DST_FALLS
# Consecutive same-type transitions sit at most 372 days apart, so a 372-day
# range always holds at least one of each; an empty set means the window
# arithmetic or the rule source broke, not a legitimate anchor.
assert DST_SPRINGS and DST_FALLS, (
    f"the synthetic window {SYNTH_START}..{SYNTH_END} holds {len(DST_SPRINGS)} "
    f"spring-forward and {len(DST_FALLS)} fall-back DST transition(s); a 372-day "
    "range must hold at least one of each -- check WINDOW_END in "
    "behavior_rebuild.py and rates.dst_transition_sundays")

# REMAINING COUPLING the derived window cannot remove: tou_audit's CI run
# audits the COMMITTED bill artifacts (data/bill_periods_electric.csv) against
# the synthetic export, and tou_audit.py dies with "no billing period lies
# wholly inside the interval coverage" unless at least one committed billing
# period sits wholly inside [SYNTH_START, SYNTH_END]. The synthetic case
# asserts this up front (_assert_bill_periods_overlap_the_window) so a fork
# sees the real cause instead of that cryptic downstream failure.


def _assert_bill_periods_overlap_the_window():
    """Fail fast, with the cause named, when the committed billing periods and
    the synthetic fixture window have drifted apart."""
    lines = (ROOT / "data" / "bill_periods_electric.csv").read_text().splitlines()
    period_col = lines[0].split(",").index("period")

    def _d(s):
        m, d, y = map(int, s.strip().split("/"))
        return dt.date(2000 + y, m, d)

    inside = 0
    for line in lines[1:]:
        text = line.split(",")[period_col]
        start, end = (_d(part) for part in text.split(" - "))
        if SYNTH_START <= start and end <= SYNTH_END:
            inside += 1
    assert inside > 0, (
        "the synthetic fixture window no longer covers the committed billing "
        f"periods -- no period in data/bill_periods_electric.csv lies wholly "
        f"inside {SYNTH_START}..{SYNTH_END}. Regenerate the bill artifacts or "
        "adjust WINDOW_END in behavior_rebuild.py; otherwise tou_audit's CI "
        "run fails with 'no billing period lies wholly inside the interval "
        "coverage'.")


def _synthetic_usage(path):
    """A structurally faithful Green Button export with invented numbers.

    Real shape, no real data: the 13-line preamble the loaders skip, the same
    column header, 96 slots a day, and DST days at 92 and 100 so the audit's
    calendar checks see what they expect. Values are a crude solar-plus-EV shape,
    enough for every generator to exercise its real code path.
    """
    rows = []
    d = SYNTH_START
    while d <= SYNTH_END:
        slots = [i * 0.25 for i in range(96)]
        if d in DST_SPRINGS:
            slots = [h for h in slots if not 2.0 <= h < 3.0]
        elif d in DST_FALLS:
            slots = slots + [1.0, 1.25, 1.5, 1.75]
        for h in sorted(slots):
            solar = max(0.0, 3.2 * math.sin(math.pi * (h - 6.5) / 11.5)) if 6.5 < h < 18 else 0.0
            # an EV charges most nights at the charger's rated power, and on a few
            # days it starts in the on-peak window; the session detectors key on
            # exactly that signature, and several generators divide by what they find
            ev = 0.0
            if d.toordinal() % 3 != 0 and 0 <= h < 3:
                ev = 11.5
            elif d.toordinal() % 7 == 0 and 17 <= h < 19:
                ev = 11.5
            base = 0.9 + ev + (0.4 if 16 <= h < 21 else 0.0)
            imp = round(max(0.0, base - solar) * 0.25, 4)
            exp = round(max(0.0, solar - base) * 0.25, 4)
            ampm = "AM" if h < 12 else "PM"
            hh12 = int(h) % 12 or 12
            rows.append(f'"09999999","{d.month}/{d.day}/{d.year}",'
                        f'"{hh12}:{int(round((h % 1) * 60)):02d} {ampm}","15",'
                        f'"{imp:.4f}","{exp:.4f}","{imp - exp:.4f}"')
        d += dt.timedelta(days=1)
    head = ["Name,SYNTHETIC FIXTURE", "Address,SYNTHETIC", "Account Number,000000000",
            "Disclaimer,synthetic test fixture - no real data", "Title,CSV Export Electric Meter(s)",
            "Resource,Electric", "Meter Number,09999999", "Interval UOM,Minute(s)",
            f"Reading Start,{SYNTH_START.month}/{SYNTH_START.day}/{SYNTH_START.year} 00:00",
            f"Reading End,{SYNTH_END.month}/{SYNTH_END.day}/{SYNTH_END.year} 23:45",
            f"Total Duration,{(SYNTH_END - SYNTH_START).days + 1} Days", "Total Usage,0",
            "UOM,kWh",
            "Meter Number,Date,Start Time,Duration,Consumption,Generation,Net"]
    path.write_text("\n".join(head + rows) + "\n")


SYNTH_HOUSEHOLD = """# synthetic fixture - invented values, no real household data
household:
  pto_date: 2019-12-01
location:
  lat: 33.0
solar:
  install_invoice_usd: 30000
  install_paid_date: 2019-12-01
charger:
  kw: 11.5
cleaning_history: []
gas:
  therm_allin_usd: 2.0
misc:
  miles_per_year: 12000
  supercharge_kwh_yr: 500
"""


def _build_throwaway_root(tmp, synthetic):
    """A repo-shaped root so generators write here instead of into data/."""
    (tmp / "analysis").mkdir()
    (tmp / "data").mkdir()
    (tmp / "private").mkdir()
    for f in ANALYSIS.glob("*.py"):
        shutil.copy(f, tmp / "analysis" / f.name)
        shutil.copy(f, tmp / f.name)          # sandbox convention: run from the root
    for f in (ROOT / "data").glob("*"):
        if f.is_file():
            shutil.copy(f, tmp / "data" / f.name)
    for f in (ROOT / "data").glob("*.csv"):
        shutil.copy(f, tmp / f.name)
    if synthetic:
        _synthetic_usage(tmp / "usage.csv")
        (tmp / "private" / "household.yaml").write_text(SYNTH_HOUSEHOLD)
        return tmp
    hh = ROOT / "private" / "household.yaml"
    if hh.exists():
        shutil.copy(hh, tmp / "private" / "household.yaml")
    raw = ROOT / "private" / "1-raw-data"
    if raw.exists():
        # a COPY, not a symlink: a generator that ever writes under
        # private/1-raw-data must corrupt the throwaway copy, not the archive
        shutil.copytree(raw, tmp / "private" / "1-raw-data")
    shutil.copy(SANDBOX / "usage.csv", tmp / "usage.csv")
    for extra in ("samA.csv", "samB.csv"):
        if (SANDBOX / extra).exists():
            shutil.copy(SANDBOX / extra, tmp / extra)
    return tmp


def _owned_path(tmp, where, fname):
    return (tmp / fname) if where == "cwd" else (tmp / "data" / fname)


def _run_generators(names, synthetic, inspect=None):
    """Run each generator in a throwaway root; then run `inspect(tmp)` INSIDE the
    tempdir context (it is deleted on exit) and fold its findings into the
    failure list. Exit code alone is not success: a generator can run to
    completion and produce nothing, which is the founding failure of the
    section 9 gate."""
    failures = []
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_throwaway_root(pathlib.Path(td), synthetic)
        for name in sorted(names):
            before = {}
            for where, fname in OWNS.get(name, ()):
                path = _owned_path(tmp, where, fname)
                before[fname] = path.stat().st_mtime_ns if path.exists() else None
            try:
                r = subprocess.run([sys.executable, name], cwd=tmp,
                                   capture_output=True, text=True, timeout=900)
            except subprocess.TimeoutExpired:
                failures.append(f"{name}: timed out after 900s")
                continue
            if r.returncode != 0:
                err = (r.stderr or "").strip() or (r.stdout or "").strip()
                tail = err.splitlines()[-1:] or ["(no output)"]
                failures.append(f"{name}: {tail[0][:150]}")
                continue
            # the generator must have WRITTEN every artifact it owns -- exiting 0
            # while never touching the pre-staged copy is a silent no-op. mtime,
            # not content: several generators legitimately reproduce the committed
            # bytes (their inputs ARE the committed artifacts), but a write always
            # moves the timestamp through the atomic replace.
            for where, fname in OWNS.get(name, ()):
                path = _owned_path(tmp, where, fname)
                if not path.exists():
                    failures.append(f"{name}: exited 0 without writing {fname}")
                elif before[fname] is not None and \
                        path.stat().st_mtime_ns == before[fname]:
                    failures.append(f"{name}: exited 0 without rewriting {fname}")
        if inspect is not None and not failures:
            failures.extend(inspect(tmp))
    return failures


def case_generators_run_on_synthetic_inputs():
    """The CI-safe half: real execution, invented data.

    This is the case that has to work where there is no private archive, because
    that is where it guards main. It builds a structurally faithful Green Button
    export and household file, then runs every generator that needs nothing more.
    """
    _assert_bill_periods_overlap_the_window()

    def inspect(tmp):
        out = []
        br = json.loads((tmp / "behavior_rebuild.json").read_text())
        if br["detection"]["sessions"] <= 0:
            out.append("behavior_rebuild found no EV sessions in a fixture built "
                       "around the session signature")
        if br["detection"]["ev_kwh_onpeak"] <= 0:
            out.append("behavior_rebuild found no on-peak EV energy despite the "
                       "fixture's on-peak charge starts")
        ta = json.loads((tmp / "data" / "tou_audit_summary.json").read_text())
        dst = {(d["date"], d["intervals"]) for d in ta["dst_days"]}
        expected = ({(d.isoformat(), 92) for d in DST_SPRINGS}
                    | {(d.isoformat(), 100) for d in DST_FALLS})
        if dst != expected:
            out.append(f"tou_audit did not see the fixture's DST days: "
                       f"{sorted(dst)} != expected {sorted(expected)}")
        if ta["integrity_skips"]:
            out.append(f"tou_audit reports integrity skips on a complete fixture: "
                       f"{ta['integrity_skips']}")
        return out

    failures = _run_generators(CI_RUNNABLE, synthetic=True, inspect=inspect)
    assert not failures, ("generators failing on synthetic inputs:\n  "
                          + "\n  ".join(failures))
    return (f"all {len(CI_RUNNABLE)} CI-runnable generators execute against "
            "synthetic inputs and their outputs check out")


def case_generators_run_on_the_real_archive():
    """The local half: the remaining generators, against the real private inputs."""
    usage = SANDBOX / "usage.csv"
    if not usage.exists():
        # Skipping here is legitimate: these generators need raw private inputs
        # that have no synthetic stand-in FOR THIS SUITE'S shared fixture. The CI
        # guarantee does not rest on this one case -- case_generators_run_on_
        # synthetic_inputs has no skip path at all, so something always executes
        # wherever this suite runs -- but issue #44 AC1 asks for more than that:
        # every one of these generators must be named as either verified by a
        # DIFFERENT CI job (VERIFIED_ELSEWHERE_IN_CI), or genuinely unverified
        # anywhere in CI, with why, stated per generator rather than lumped into
        # one bare name list.
        lines = []
        for name in sorted(NEEDS_PRIVATE_ARCHIVE):
            reason = NEEDS_PRIVATE_ARCHIVE[name]
            elsewhere = VERIFIED_ELSEWHERE_IN_CI.get(name)
            if elsewhere:
                test_file, _cases = elsewhere
                lines.append(f"  {name}: verified end to end in CI by {test_file} "
                            f"instead (this case's own real-archive run needs {reason})")
            else:
                lines.append(f"  {name}: NOT verified end to end anywhere in CI "
                            f"-- needs {reason}")
        raise SkipCase("generators needing the private archive, and why "
                       "(none of these run in THIS case in CI):\n" + "\n".join(lines))
    def inspect(tmp):
        """The section 9 gate, folded in: on the real inputs every owned artifact
        must reproduce the committed copy byte-for-byte."""
        out = []
        for name in sorted(set(NEEDS_PRIVATE_ARCHIVE) | CI_RUNNABLE):
            for where, fname in OWNS.get(name, ()):
                got = _owned_path(tmp, where, fname)
                want = ROOT / "data" / fname
                if not want.exists():
                    continue
                if got.read_bytes() != want.read_bytes():
                    out.append(f"{name}: {fname} does not reproduce the committed "
                               "artifact byte-for-byte")
        return out

    failures = _run_generators(set(NEEDS_PRIVATE_ARCHIVE) | CI_RUNNABLE,
                               synthetic=False, inspect=inspect)
    assert not failures, "generators that do not run:\n  " + "\n  ".join(failures)
    n = len(NEEDS_PRIVATE_ARCHIVE) + len(CI_RUNNABLE)
    return (f"all {n} generators execute against the real inputs and every owned "
            "artifact reproduces the committed copy byte-for-byte")


def _ci_wired_test_files(workflow_src):
    """Test files with a REAL, enabled `run: python analysis/X.py` step
    somewhere in jobs.*.steps -- parsed as actual YAML structure, not a bare
    substring or a line-shaped regex, so a commented-out line, an unrelated
    mention of the filename in prose, or a step disabled with `if: false`
    cannot satisfy this. (A bare-substring predecessor of this function is
    what let VERIFIED_ELSEWHERE_IN_CI assert 15 covered generators when only
    6 actually were -- the file existed and had SOME line mentioning it, but
    that is not the same claim as "a job actually runs it".)"""
    import yaml
    doc = yaml.safe_load(workflow_src) or {}
    found = set()
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            cond = step.get("if")
            if cond is False or (isinstance(cond, str) and cond.strip().lower() == "false"):
                continue
            run = step.get("run")
            if not isinstance(run, str):
                continue
            m = re.search(r"^\s*python analysis/(\S+)\s*$", run, re.M)
            if m:
                found.add(m.group(1))
    return found


def _archive_free_root(tmp):
    """A repo-shaped root with the analysis/ package and the real, COMMITTED
    data/ tree, but NO private/ anywhere -- exactly what a CI runner checks
    out (private/ is gitignored and never pushed). Deliberately NOT built by
    copying test_scripts_runnable.py's own _build_throwaway_root, which
    always writes a private/household.yaml (synthetic or real): the whole
    point here is to prove a claimed case survives with private/ ABSENT."""
    shutil.copytree(ANALYSIS, tmp / "analysis")
    shutil.copytree(ROOT / "data", tmp / "data")
    return tmp


def case_verified_elsewhere_mapping_is_real_and_wired_into_ci():
    """VERIFIED_ELSEWHERE_IN_CI is a claim about what actually runs in CI --
    check it by actually running it, not by trusting the dict.

    Issue #44 follow-up review: a prior version of this case only checked that
    each named test file existed and appeared somewhere in tests.yml, which
    passed even though 9 of 15 claimed entries were false -- the file existed
    and had a CI step, but the specific case that invokes the real generator
    SKIPS without the private archive, and only leaf/unit checks passed. That
    is precisely "a guard that reports success without checking anything",
    reproduced inside the fix for it. This version builds a fresh
    archive-free root (analysis/ + the real committed data/, no private/ at
    all) and RUNS every named test file there, then fails unless every
    case named in VERIFIED_ELSEWHERE_IN_CI actually reports PASS -- not SKIP,
    not silently absent from the output -- in that run.
    """
    unknown = set(VERIFIED_ELSEWHERE_IN_CI) - set(NEEDS_PRIVATE_ARCHIVE)
    assert not unknown, f"VERIFIED_ELSEWHERE_IN_CI names non-archive generators: {unknown}"
    assert CI_WORKFLOW.is_file(), f"{CI_WORKFLOW} not found"
    wired = _ci_wired_test_files(CI_WORKFLOW.read_text())
    missing_file, missing_step = [], []
    for name, (test_file, cases) in VERIFIED_ELSEWHERE_IN_CI.items():
        assert cases, f"{name}: VERIFIED_ELSEWHERE_IN_CI lists no required-pass case"
        if not (ANALYSIS / test_file).is_file():
            missing_file.append(test_file)
        if test_file not in wired:
            missing_step.append(test_file)
    assert not missing_file, f"VERIFIED_ELSEWHERE_IN_CI names test files that don't exist: {missing_file}"
    assert not missing_step, (
        f"VERIFIED_ELSEWHERE_IN_CI names test files with no `run: python "
        f"analysis/X.py` step in {CI_WORKFLOW.name}: {missing_step}")

    wrong = []
    with tempfile.TemporaryDirectory() as td:
        free = _archive_free_root(pathlib.Path(td))
        assert not (free / "private").exists(), "archive-free root must have no private/ at all"
        for name, (test_file, cases) in sorted(VERIFIED_ELSEWHERE_IN_CI.items()):
            src = (ANALYSIS / test_file).read_text()
            for c in cases:
                # static: the case must actually be a defined function referenced
                # in this file's own CASES list -- catches a rename/removal that
                # would otherwise leave the case silently absent from every run
                # (neither PASS, SKIP nor FAIL: main()'s PASS branch never prints
                # a case's __name__, only its returned message, so "present in
                # this file at all" has to be checked statically, not by grepping
                # subprocess output for a name that a passing case never prints).
                assert f"def {c}(" in src, (
                    f"{name}: {test_file} has no function named {c} -- "
                    "VERIFIED_ELSEWHERE_IN_CI is stale")
                # registered either via an explicit CASES = [...] list (name
                # appears again outside its own def line) or the `@case`
                # decorator convention (test_carbon_dispatch_tradeoff.py /
                # test_uncertainty_propagation.py): the decorator line
                # immediately precedes `def NAME(`, so the name need not repeat.
                registered = src.count(c) >= 2 or f"@case\ndef {c}(" in src
                assert registered, (
                    f"{name}: {test_file}'s {c} is defined but not registered "
                    "(not in CASES, and not @case-decorated) -- "
                    "VERIFIED_ELSEWHERE_IN_CI is stale")
            try:
                r = subprocess.run([sys.executable, test_file], cwd=free / "analysis",
                                   capture_output=True, text=True, timeout=900)
            except subprocess.TimeoutExpired:
                wrong.append(f"{name}: {test_file} timed out in the archive-free root")
                continue
            out = r.stdout + r.stderr
            skipped = set(re.findall(r"SKIP\s+(case_\w+)", out))
            failed = set(re.findall(r"FAIL\s+(case_\w+)", out))
            for c in cases:
                if c in skipped:
                    wrong.append(f"{name}: {test_file}'s {c} SKIPPED in an "
                                "archive-free root -- claimed coverage is false")
                elif c in failed:
                    wrong.append(f"{name}: {test_file}'s {c} FAILED in an "
                                f"archive-free root: {out[-300:]}")
            if r.returncode != 0 and not skipped and not failed:
                wrong.append(f"{name}: {test_file} exited {r.returncode} in an "
                            f"archive-free root for an unexplained reason: {out[-300:]}")
    assert not wrong, ("VERIFIED_ELSEWHERE_IN_CI claims false coverage:\n  "
                       + "\n  ".join(wrong))

    undeclared = set(NEEDS_PRIVATE_ARCHIVE) - set(VERIFIED_ELSEWHERE_IN_CI)
    return (f"{len(VERIFIED_ELSEWHERE_IN_CI)} of {len(NEEDS_PRIVATE_ARCHIVE)} "
            "NEEDS_PRIVATE_ARCHIVE generators are verified end to end -- each "
            "claimed case was actually RUN in a fresh archive-free root and "
            f"confirmed to pass, not skip; {len(undeclared)} "
            f"({', '.join(sorted(undeclared))}) are documented as not yet "
            "covered in CI")


def case_the_ci_tier_cannot_skip():
    """The previous version of this guard skipped everything in CI and passed.

    So state the property directly: the synthetic tier must be non-empty and must
    have no early return, which is what makes it run wherever the suite runs.
    """
    assert CI_RUNNABLE, "the CI-runnable tier is empty; nothing would execute in CI"
    src = pathlib.Path(__file__).read_text()
    starts = sorted([src.index("def case_generators_run_on_synthetic_inputs(")])
    end_candidates = [i for i in
                      (src.find("\ndef ", starts[0] + 1),) if i != -1]
    body = src[starts[0]:end_candidates[0]] if end_candidates else src[starts[0]:]
    assert len(body) > 100, "could not isolate the synthetic case body"
    assert "SKIP" not in body, "the CI-runnable tier has grown a skip path"
    return f"the CI tier executes {len(CI_RUNNABLE)} generators and has no skip path"


def case_every_generator_is_covered_by_one_of_the_two_tiers():
    declared = {n for n, r in MANIFEST.items() if r == "generator"}
    covered = CI_RUNNABLE | set(NEEDS_PRIVATE_ARCHIVE)
    assert declared == covered, (
        f"generators in no execution tier: {sorted(declared - covered)}; "
        f"tiers naming unknown scripts: {sorted(covered - declared)}")
    return f"every one of the {len(declared)} generators sits in an execution tier"


def case_missing_day_fails_the_chart_generator():
    """Behavioral regression for the whole-day blind spot: delete one calendar
    day from an otherwise complete fixture and report_data must refuse, naming
    the coverage problem, instead of drawing charts with a quietly shrunken
    month."""
    # The probe is derived from the window (mid-analysis-year, advanced to a
    # Wednesday, never a DST day whose interval count is legitimately not 96)
    # so re-pointing WINDOW_END keeps it inside the fixture automatically.
    gone_date = WINDOW_END - dt.timedelta(days=180)
    while gone_date.weekday() != 2 or gone_date in DST_DATES:
        gone_date += dt.timedelta(days=1)
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_throwaway_root(pathlib.Path(td), synthetic=True)
        usage = tmp / "usage.csv"
        gone = f"{gone_date.month}/{gone_date.day}/{gone_date.year}"
        kept = [l for l in usage.read_text().splitlines()
                if f'"{gone}"' not in l]
        usage.write_text("\n".join(kept) + "\n")
        r = subprocess.run([sys.executable, "report_data.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=900)
        assert r.returncode != 0, "report_data accepted a frame missing a whole day"
        err = r.stderr + r.stdout
        assert gone_date.isoformat() in err and "missing" in err, err[-200:]
    return "report_data refuses a wholly missing day and names it"


def case_small_charger_refuses_ev_discrimination():
    """behavior_rebuild derives its session-peak gate from charger.kw. When
    0.7 * charger.kw sits within 1 kW of the 2.5 kW candidate threshold the
    gate cannot tell a charger from HVAC/oven load running 2-4 kW above
    baseline, so the script must refuse up front (fail closed) instead of
    classifying house load as EV charging and corrupting every downstream
    consumer."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_throwaway_root(pathlib.Path(td), synthetic=True)
        hh = tmp / "private" / "household.yaml"
        hh.write_text(SYNTH_HOUSEHOLD.replace("kw: 11.5", "kw: 3.3"))
        assert "kw: 3.3" in hh.read_text(), "fixture household edit did not take"
        r = subprocess.run([sys.executable, "behavior_rebuild.py"], cwd=tmp,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode != 0, (
            "behavior_rebuild ran with charger.kw = 3.3, whose derived peak gate "
            "(0.7 * 3.3 = 2.31 kW) collapses into the appliance-noise test")
        err = r.stderr + r.stdout
        assert "cannot discriminate" in err and "charger.kw" in err, (
            f"refused, but without naming the discrimination failure: {err[-300:]}")
    return ("behavior_rebuild refuses a charger too small to discriminate from "
            "house load, naming the cause")


def case_publication_failure_leaves_artifacts_untouched():
    """Failure injection for the staged-write publication: with data/ made
    read-only, the run must fail without truncating or partially replacing any
    committed artifact -- no mixed-generation output, no .tmp junk promoted."""
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_throwaway_root(pathlib.Path(td), synthetic=True)
        data = tmp / "data"
        before = {f.name: f.read_bytes() for f in data.iterdir() if f.is_file()}
        os.chmod(data, 0o555)
        try:
            r = subprocess.run([sys.executable, "analyze_norelief.py"], cwd=tmp,
                               capture_output=True, text=True, timeout=900)
        finally:
            os.chmod(data, 0o755)
        assert r.returncode != 0, "expected the run to fail with data/ read-only"
        after = {f.name: f.read_bytes() for f in data.iterdir() if f.is_file()}
        assert after == before, (
            "artifacts changed despite the publication failure: "
            + str(sorted(set(after) ^ set(before)
                         | {k for k in after if after.get(k) != before.get(k)}))[:200])
    return "a publication failure leaves every committed artifact byte-untouched"


def _coverage_suite_files(coverage_src):
    """Test files (with `.py`) named in check_coverage.sh's own SUITE loop
    (`for t in test_a test_b ... ; do`), parsed from the actual bash `for`
    statement rather than assumed from prose, so a reordered or reformatted
    list cannot silently desync this check from what the script really
    runs. The list spans multiple lines with bash `\` line-continuations,
    which must be stripped before splitting on whitespace -- otherwise a
    bare `\` (sitting on its own between two names) survives as a spurious
    token."""
    m = re.search(r"for t in(.*?);\s*do", coverage_src, re.S)
    assert m, f"{COVERAGE_SCRIPT.name}: no 'for t in ... ; do' SUITE loop found"
    names = m.group(1).replace("\\", " ").split()
    assert names, f"{COVERAGE_SCRIPT.name}: the SUITE loop parsed to zero names"
    return {f"{name}.py" for name in names}


def case_every_check_coverage_suite_is_wired_into_ci():
    """Issue #102: check_coverage.sh's SUITE list is real, already-passing
    local test coverage -- but a file can sit there for months with no
    corresponding `run:` step in tests.yml, so a regression in it reaches
    main with every CI check reporting green (found for test_extra_results.py
    during issue #34's review, then confirmed pre-existing for eight more
    files). Checked mechanically here, not by re-reading both lists by eye
    on every future addition: every SUITE-list file must have a real,
    enabled `run: python analysis/X.py` step somewhere in tests.yml (reusing
    _ci_wired_test_files(), the same parser case_verified_elsewhere_mapping_
    is_real_and_wired_into_ci already trusts for the same class of claim)."""
    assert COVERAGE_SCRIPT.is_file(), f"{COVERAGE_SCRIPT} not found"
    assert CI_WORKFLOW.is_file(), f"{CI_WORKFLOW} not found"
    suite_files = _coverage_suite_files(COVERAGE_SCRIPT.read_text())
    assert len(suite_files) > 30, (
        f"only found {len(suite_files)} SUITE files -- the 'for t in ... ; do' "
        "parse likely broke against a reformatted check_coverage.sh")
    wired = _ci_wired_test_files(CI_WORKFLOW.read_text())
    missing = sorted(f for f in suite_files if f not in wired)
    assert not missing, (
        f"{len(missing)} check_coverage.sh SUITE file(s) have no `run: python "
        f"analysis/X.py` step in {CI_WORKFLOW.name}, so a regression in them "
        f"reaches main with CI reporting green: {missing}")
    return (f"all {len(suite_files)} check_coverage.sh SUITE files have a real "
           f"CI step in {CI_WORKFLOW.name}")


CASES = [
    case_manifest_is_complete_and_exact,
    case_no_two_generators_own_the_same_artifact,
    case_every_script_parses,
    case_no_absolute_paths_outside_the_repo,
    case_retired_scripts_say_so_and_refuse_to_run,
    case_tou_assignment_comes_from_the_canonical_module,
    case_libraries_import_cleanly,
    case_the_ci_tier_cannot_skip,
    case_every_generator_is_covered_by_one_of_the_two_tiers,
    case_generators_run_on_synthetic_inputs,
    case_missing_day_fails_the_chart_generator,
    case_small_charger_refuses_ev_discrimination,
    case_publication_failure_leaves_artifacts_untouched,
    case_verified_elsewhere_mapping_is_real_and_wired_into_ci,
    case_every_check_coverage_suite_is_wired_into_ci,
    case_generators_run_on_the_real_archive,
]


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
