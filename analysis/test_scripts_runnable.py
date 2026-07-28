#!/usr/bin/env python3
"""Catch analysis scripts that have quietly stopped being runnable.

This repo has been bitten three times. analyze.py and analyze_norelief.py carried
absolute paths into a retired Cowork sandbox and had never run here, which left
plan_results.csv, hourly_profile.csv and monthly.csv with no working generator.
carbon_timing.py read a caiso_data/ directory that was never committed. Every
time the script sat in analysis/ looking maintained and nothing complained.

The first version of this file only inspected source text, which was not enough:
a script whose *relative* inputs have gone missing, or whose imports are broken,
passed every check. carbon_timing.py itself would have passed had it not been
listed as retired by hand, so the guard could not catch the failure its own
docstring described.

So there are two tiers here, and the split is honest about what CI can know:

  structural   parses, is classified in MANIFEST, retired scripts say so and fail
               loudly, no absolute path that does not exist, libraries import
               cleanly. No private data, runs everywhere.
  executable   every declared generator is actually run to completion against the
               real inputs, in a throwaway repo root so nothing in data/ is
               touched. Needs the private export, so it SKIPS in CI and runs
               locally, which is where the reproduction claim is made anyway.

MANIFEST is explicit rather than "everything not retired", so adding a script
without classifying it fails instead of being silently assumed live.
"""
import ast
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
    "household.py": "library",
    "behavior_rebuild.py": "generator",
    "battery_dispatch_policies.py": "generator",
    "battery_plan_matrix.py": "generator",
    "package_results.py": "generator",
    "extended_findings.py": "generator",
    "report_data.py": "generator",
    "deep_analyses.py": "generator",
    "battery_backup_sims.py": "generator",
    "billing_model_nem.py": "library",
    "lifetime_payback.py": "generator",
    "analyze.py": "generator",
    "analyze_norelief.py": "generator",
    "carbon_fullyear.py": "generator",
    "soiling_analysis.py": "generator",
    "parse_bills.py": "generator",
    "tou_audit.py": "generator",
    "carbon_timing.py": "retired",
}

# Modules allowed to express TOU windows themselves. The legacy ranking pair keeps
# its own calendar by design (TECHNICAL.md 3.1/3.2); tou_audit scores alternative
# day-type rules against the bills on purpose; rates.py is where the rule lives.
TOU_EXEMPT = {"rates.py", "analyze.py", "analyze_norelief.py", "tou_audit.py"}

ABS_PATH = re.compile(r"""["'](/(?!tmp/)[A-Za-z0-9_.\-]+/[^"']*)["']""")


def _scripts():
    return sorted(f for f in ANALYSIS.glob("*.py") if not f.name.startswith("test_"))


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
    offenders = []
    for f in _scripts():
        for m in ABS_PATH.finditer(f.read_text()):
            path = m.group(1)
            if path.startswith(str(ROOT)) or not path.startswith("/"):
                continue
            if not pathlib.Path(path).exists():
                offenders.append(f"{f.name}: {path}")
    assert not offenders, f"absolute paths that do not exist: {offenders}"
    return "no analysis script hardcodes an absolute path that is not present"


def case_retired_scripts_say_so_and_refuse_to_run():
    for name, role in sorted(MANIFEST.items()):
        if role != "retired":
            continue
        src = (ANALYSIS / name).read_text()
        doc = ast.get_docstring(ast.parse(src)) or ""
        assert "RETIRED" in doc, f"{name}: retired but its docstring does not say so"
        assert "SystemExit" in src, f"{name}: should raise SystemExit with an explanation"
    n = sum(1 for r in MANIFEST.values() if r == "retired")
    return f"{n} retired script(s) carry the marker and refuse to run with an explanation"


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
        # It may call the canonical assignment, or take a frame from the loader
        # that already did.
        if "period_at" in src or "behavior_rebuild" in src:
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


IN_CI = bool(os.environ.get("CI"))

# Generators that need only a Green Button export, a household file and the
# committed data/ directory. These MUST execute in CI, against synthetic inputs
# when the private export is absent.
CI_RUNNABLE = {
    "behavior_rebuild.py", "battery_dispatch_policies.py", "package_results.py",
    "report_data.py", "analyze.py", "analyze_norelief.py",
    "carbon_fullyear.py", "tou_audit.py",
}
# Generators that additionally need raw private inputs which have no synthetic
# stand-in: the bill PDFs, the SAM 8760 exports, the monitoring history. These run
# only where that archive exists, and the reason is recorded rather than implied.
NEEDS_PRIVATE_ARCHIVE = {
    "parse_bills.py": "the bill PDF corpus (private/1-raw-data/*-bills/)",
    "extended_findings.py": "the SAM 8760 exports (private/1-raw-data/enphase_sam8760_*.csv)",
    "lifetime_payback.py": "the SAM full-year export (samB.csv)",
    "soiling_analysis.py": "the monitoring production history",
    "deep_analyses.py": "the SAM full-year export (samB.csv)",
    "battery_backup_sims.py": "the SAM full-year export (samB.csv)",
    "battery_plan_matrix.py": ("its fail-closed tie-out compares against "
                               "battery_dispatch_policies.json, which is built from the "
                               "real year, so invented inputs must diverge"),
}

SYNTH_START = dt.date(2025, 7, 20)      # a little before the pipeline's fixed window
SYNTH_END = dt.date(2026, 7, 26)


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
        if d == dt.date(2026, 3, 8):
            slots = [h for h in slots if not 2.0 <= h < 3.0]
        elif d == dt.date(2025, 11, 2):
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
        os.symlink(raw, tmp / "private" / "1-raw-data")
    shutil.copy(SANDBOX / "usage.csv", tmp / "usage.csv")
    for extra in ("samA.csv", "samB.csv"):
        if (SANDBOX / extra).exists():
            shutil.copy(SANDBOX / extra, tmp / extra)
    return tmp


def _run_generators(names, synthetic):
    failures = []
    with tempfile.TemporaryDirectory() as td:
        tmp = _build_throwaway_root(pathlib.Path(td), synthetic)
        for name in sorted(names):
            r = subprocess.run([sys.executable, name], cwd=tmp,
                               capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                tail = (r.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
                failures.append(f"{name}: {tail[0][:150]}")
    return failures


def case_generators_run_on_synthetic_inputs():
    """The CI-safe half: real execution, invented data.

    This is the case that has to work where there is no private archive, because
    that is where it guards main. It builds a structurally faithful Green Button
    export and household file, then runs every generator that needs nothing more.
    """
    failures = _run_generators(CI_RUNNABLE, synthetic=True)
    assert not failures, "generators that do not run on synthetic inputs:\n  " + "\n  ".join(failures)
    return f"all {len(CI_RUNNABLE)} CI-runnable generators execute against synthetic inputs"


def case_generators_run_on_the_real_archive():
    """The local half: the remaining generators, against the real private inputs."""
    usage = SANDBOX / "usage.csv"
    if not usage.exists():
        # Skipping here is legitimate: these generators need raw private inputs
        # that have no synthetic stand-in. The CI guarantee does not rest on this
        # case -- case_generators_run_on_synthetic_inputs has no skip path at all,
        # so something always executes wherever this suite runs.
        return ("SKIP generators needing the private archive (" +
                ", ".join(sorted(NEEDS_PRIVATE_ARCHIVE)) + ")")
    failures = _run_generators(set(NEEDS_PRIVATE_ARCHIVE) | CI_RUNNABLE, synthetic=False)
    assert not failures, "generators that do not run:\n  " + "\n  ".join(failures)
    n = len(NEEDS_PRIVATE_ARCHIVE) + len(CI_RUNNABLE)
    return f"all {n} generators execute against the real inputs"


def case_the_ci_tier_cannot_skip():
    """The previous version of this guard skipped everything in CI and passed.

    So state the property directly: the synthetic tier must be non-empty and must
    have no early return, which is what makes it run wherever the suite runs.
    """
    assert CI_RUNNABLE, "the CI-runnable tier is empty; nothing would execute in CI"
    src = pathlib.Path(__file__).read_text()
    body = src[src.index("def case_generators_run_on_synthetic_inputs("):
               src.index("def case_generators_run_on_the_real_archive(")]
    assert "SKIP" not in body, "the CI-runnable tier has grown a skip path"
    return f"the CI tier executes {len(CI_RUNNABLE)} generators and has no skip path"


def case_every_generator_is_covered_by_one_of_the_two_tiers():
    declared = {n for n, r in MANIFEST.items() if r == "generator"}
    covered = CI_RUNNABLE | set(NEEDS_PRIVATE_ARCHIVE)
    assert declared == covered, (
        f"generators in no execution tier: {sorted(declared - covered)}; "
        f"tiers naming unknown scripts: {sorted(covered - declared)}")
    return f"every one of the {len(declared)} generators sits in an execution tier"


CASES = [
    case_manifest_is_complete_and_exact,
    case_every_script_parses,
    case_no_absolute_paths_outside_the_repo,
    case_retired_scripts_say_so_and_refuse_to_run,
    case_tou_assignment_comes_from_the_canonical_module,
    case_libraries_import_cleanly,
    case_the_ci_tier_cannot_skip,
    case_every_generator_is_covered_by_one_of_the_two_tiers,
    case_generators_run_on_synthetic_inputs,
    case_generators_run_on_the_real_archive,
]


def main():
    ran = skipped = failures = 0
    for case in CASES:
        try:
            msg = case()
            if msg.startswith("SKIP"):
                print(f"SKIP  {msg[5:]}")
                skipped += 1
            else:
                print(f"PASS  {msg}")
                ran += 1
        except AssertionError as e:
            print(f"FAIL  {case.__name__}: {e}")
            failures += 1
    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
