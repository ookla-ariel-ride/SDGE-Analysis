#!/usr/bin/env python3
"""Compose data/package_results.json from the integrated-pipeline artifacts.

This script makes the packages artifact reproducible (CLAUDE.md §9: every committed
artifact regenerable by a committed script). It computes nothing new: every figure is
read from the two upstream artifacts that the integrated pipeline writes —

  data/behavior_rebuild.json            (behavior scenarios a–d, baseline bill)
  data/battery_dispatch_policies.json   (price-aware battery, post_behavior block)

— and package costs, which are purchase-price constants from the battery research
(research/battery-research-notes.md: PW3 installed ~$14,500; +$5,900 expansion).

ORDERING CONTRACT (this script runs THIRD):
  behavior_rebuild.py, battery_dispatch_policies.py  ->  package_results.py, in
  the SAME working directory. Both upstream generators write their JSON into the
  WORKING DIRECTORY; data/behavior_rebuild.json and data/battery_dispatch_policies.json
  are only the last PROMOTED run. Since this script composes every figure it
  reports from those two artifacts (see above) rather than computing anything
  itself, each one is read whole from this run's copy when one is there, from
  the committed copy otherwise, and a disagreement between the two is announced
  loudly rather than resolved in silence (see _resolve_artifact). CLAUDE.md's
  section 9 regeneration gate already runs behavior_rebuild.py and
  battery_dispatch_policies.py before this script.

Run AFTER behavior_rebuild.py and battery_dispatch_policies.py.
"""
import json, pathlib

def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("repo root not found: no ancestor of the CWD or of this "
                     "script contains both analysis/ and data/")

DATA = _repo_root() / "data"
PW3_COST = 14500          # installed, research/battery-research-notes.md
EXPANSION_COST = 5900     # PW3 expansion increment
HIGH_COST = PW3_COST + EXPANSION_COST

BEHAVIOR_JSON = "behavior_rebuild.json"            # written to the CWD by behavior_rebuild.py
DISPATCH_JSON = "battery_dispatch_policies.json"   # written to the CWD by battery_dispatch_policies.py


def _read_json(path):
    """Parse one artifact whole.

    Fail-closed: a malformed or unreadable copy is an ERROR, never a licence
    to fall back to the other one — falling back past a broken artifact is
    how a stale figure gets published under a citation that looks current.
    """
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(
            f"{path}: cannot parse artifact ({type(e).__name__}: {e}). "
            "Regenerate it with its own generator; this script will not "
            "fall back past a broken artifact.")


def _resolve_artifact(name, generator):
    """Read one upstream JSON artifact whole, from THIS run's copy when present.

    package_results.py composes EVERY figure it reports out of its two
    upstream artifacts (see module docstring) rather than citing one number,
    so unlike carbon_fullyear.py's _scenario_a_saved / deep_analyses.py's
    _base_save (each resolving a single scalar) this resolves the WHOLE
    document; "disagreement" means the two parsed documents are not equal.
    Which copy, though? generator writes NAME into the WORKING DIRECTORY (the
    documented private/verify sandbox); data/NAME changes only when the
    operator promotes that run. Reading data/ unconditionally would quote the
    PREVIOUS run's figures while claiming to cite the artifact this run just
    produced — the same drift carbon_fullyear.py and deep_analyses.py were
    fixed for (PR #26), one level down.

    Resolution order:
      1. current-run copy in the CWD (the upstream generator's product) —
         used when present;
      2. committed data/ copy — used only when there is no current-run copy,
         with a NOTICE saying so;
      3. both present and DISAGREEING — this run's copy wins, and the
         mismatch is announced loudly: the committed copy is stale relative
         to this run, and the section 9 regeneration gate will fail until it
         is promoted.
    """
    run = pathlib.Path.cwd() / name
    committed = DATA / name
    if not run.exists():
        if not committed.exists():
            raise SystemExit(
                f"no {name} artifact: neither a current-run {run} nor the "
                f"committed {committed} exists. Run {generator} in this "
                "working directory first (see the ordering contract above).")
        v = _read_json(committed)
        print(f"NOTICE: no current-run {name} in {pathlib.Path.cwd()}; reading "
              f"the committed {committed}. If this run's household inputs or "
              f"upstream inputs changed, run {generator} here FIRST.")
        return v
    v = _read_json(run)
    if committed.exists() and run.samefile(committed):
        print(f"NOTICE: {name} read from {run} (the working directory IS the "
              "committed data/ directory).")
        return v
    if not committed.exists():
        print(f"NOTICE: {name} read from this run's {run} (no committed "
              f"{committed} to compare against).")
        return v
    c = _read_json(committed)
    if c == v:
        print(f"NOTICE: {name} read from this run's {run} (agrees with the "
              f"committed {committed}).")
        return v
    bar = "!" * 72
    print(bar)
    print(f"NOTICE -- STALE COMMITTED ARTIFACT: this run's {name} differs "
          f"from the committed {committed}.")
    print(f"  Using THIS RUN's {run}. The committed copy has not been "
          "promoted; CLAUDE.md's section 9 gate will fail until it is.")
    print(bar)
    return v


br = _resolve_artifact(BEHAVIOR_JSON, "behavior_rebuild.py")
bp = _resolve_artifact(DISPATCH_JSON, "battery_dispatch_policies.py")

base = round(br["baseline"]["model_bill"])           # modelled baseline at 6/1/2026 rates (see behavior_rebuild.json)
sc = br["scenarios"]
a, b, c, d = (round(sc[k]["saved"]) for k in ("a", "b", "c", "d"))
pb = bp["post_behavior"]
batt_alone = bp["pw3"]["greedy"]["save"]             # baseline battery marginal (see battery_dispatch_policies.json)
batt_post = pb["mid"]["battery_marginal"]            # 2245 post-EV-fix marginal
evening = bp["pw3"]["evening"]["save"]               # evening-only variant

out = {
    "basis": ("integrated pipeline: behavior_rebuild.py EV shift -> "
              "battery_dispatch_policies.py price-aware dispatch -> rates.bill_nem "
              "(canonical bill-derived rates, NBC on gross imports); baseline "
              f"${base:,} at 6/1/2026 rates; actual 365-day billed baseline $3,282 "
              "(2025-vintage tariffs)"),
    "model_baseline_current_rates": base,
    "packages": {
        "LOW": {
            "cost": 0,
            "savings_yr": a,
            "savings_range": [b, c],
            "note": (f"EV-only 100% compliance; 80% = ${b:,}; +25% flexible house "
                     f"load = ${c:,}; stretch (50%) = ${d:,}"),
            "projected_bill_current_rates_yr": base - a,
        },
        "MID": {
            "cost": PW3_COST,
            "savings_yr": pb["mid"]["combined_save"],
            "battery_alone_yr": batt_alone,
            "battery_alone_post_ev_fix_yr": batt_post,
            "battery_alone_payback_yr": round(PW3_COST / batt_alone, 1),
            "battery_alone_payback_post_fix_yr": round(PW3_COST / batt_post, 1),
            "battery_alone_payback_evening_only_yr": round(PW3_COST / evening, 1),
            "projected_bill_current_rates_yr": pb["mid"]["bill"],
            "note": ("single integrated run: EV shift then price-aware PW3 dispatch, "
                     "re-billed end-to-end"),
        },
        "HIGH": {
            "cost": HIGH_COST,
            "savings_yr": pb["high"]["combined_save"],
            "marginal_vs_mid_yr": pb["high"]["combined_save"] - pb["mid"]["combined_save"],
            "projected_bill_current_rates_yr": pb["high"]["bill"],
            "note": (f"post-behavior expansion marginal "
                     f"${pb['high']['combined_save'] - pb['mid']['combined_save']}/yr "
                     f"(~{round(EXPANSION_COST / (pb['high']['combined_save'] - pb['mid']['combined_save']))}"
                     f"-yr marginal payback on ${EXPANSION_COST:,}) — outage "
                     "endurance is the reason to buy it"),
        },
    },
    "superseded": ("additive cross-model splicing (behavior + battery from different "
                   "engines) replaced by the integrated pipeline; package payback "
                   "framings that credit free behavior to hardware remain invalid"),
}

with open(DATA / "package_results.json", "w") as f:
    json.dump(out, f, indent=1)
print("wrote", DATA / "package_results.json")
