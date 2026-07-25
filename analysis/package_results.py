#!/usr/bin/env python3
"""Compose data/package_results.json from the integrated-pipeline artifacts.

This script makes the packages artifact reproducible (CLAUDE.md §9: every committed
artifact regenerable by a committed script). It computes nothing new: every figure is
read from the two upstream artifacts that the integrated pipeline writes —

  data/behavior_rebuild.json            (behavior scenarios a–d, baseline bill)
  data/battery_dispatch_policies.json   (price-aware battery, post_behavior block)

— and package costs, which are purchase-price constants from the battery research
(research/battery-research-notes.md: PW3 installed ~$14,500; +$5,900 expansion).
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

br = json.load(open(DATA / "behavior_rebuild.json"))
bp = json.load(open(DATA / "battery_dispatch_policies.json"))

base = round(br["baseline"]["model_bill"])           # 4884 at 6/1/2026 rates
sc = br["scenarios"]
a, b, c, d = (round(sc[k]["saved"]) for k in ("a", "b", "c", "d"))
pb = bp["post_behavior"]
batt_alone = bp["pw3"]["greedy"]["save"]             # 2325 baseline marginal
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
