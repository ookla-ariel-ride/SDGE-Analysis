#!/usr/bin/env python3
"""Keeps data/extra_results.json's `escalation` block from being an orphaned,
hand-typed figure with no committed generator (issue #34).

`data/extra_results.json` predates this repo's generator/OWNS convention --
most of its content is one-time, "in-session" computation (TECHNICAL.md 3.11
documents this for `phantom`; `ev_fleet` says so directly in its own `source`
field) with no other reproducible source anywhere in this repo. Six of its
seven top-level keys stay that way here: `phantom`, `price_map`, `nbt`,
`cleaning`, `trueup`, `ev_fleet` are copied through byte-for-byte from
whatever is already committed, since inventing a computation for them would
mean guessing at a methodology this repo has no record of (CLAUDE.md section
0). `price_map` in particular is independently, separately fail-closed
cross-checked against `rates.py` by `quiet_night_floor.py` -- that check
stays live and unaffected by this script.

`escalation` needed real investigation, not a quick patch. Issue #34's own
framing ("contradicts the live engine") assumed extra_results.json's ladder
and battery_dispatch_policies.json's own escalation_greedy_pw3_post_behavior
ladder were the SAME computation that had drifted apart. They are not:
running battery_dispatch_policies.escalation() (reimplemented below, see
_escalation_ladder) against each script's own base-saving figure reproduces
BOTH numbers exactly --
  escalation(1743) == extra_results.json's committed 7.8/7.3/6.8/6.2 yr
  escalation(2238) == battery_dispatch_policies.json's committed 6.2/5.9/5.5/5.2 yr
TECHNICAL.md section 3.11 already documents this precisely: extra_results.json's
ladder is a "RETIRED variant" seeded from the superseded $1,743/yr evening-only
base saving (section 3.8), while "the published ladder (report section 13)" is
battery_dispatch_policies.json's, rebased on the current $2,238/yr post-behavior
marginal. These are two different dispatch scenarios' escalation curves, not
one figure that drifted -- overwriting the retired figure with the published
one would erase a real historical comparison point and make the two files
redundant, which is a worse outcome than the orphaned-generator problem this
issue actually names.

$1,743 is ITSELF now stale as a live quantity. TECHNICAL.md section 3.8 traces
it precisely: it is an earlier value of behavior_rebuild.py's own "evening-only
dispatch variant, retired as the published basis" -- a 13.5 kWh/11.5 kW/90%-RTE
battery re-simulated on TOP OF the EV-behavior shift (scenario a), currently
$1,753/yr "after behavior" ($135/yr of double-counting avoided from the raw
$1,887/yr marginal). It is NOT battery_dispatch_policies.json's own
pw3.evening.save ($1,720) -- that is a DIFFERENT scenario (evening-only
WITHOUT the EV-behavior shift applied first) that only coincidentally sits
close in value; conflating the two once already produced a wrong provenance
claim here (adversarial review, issue #34, round 3). Recomputing escalation() from
today's $1,753 would silently change the committed numbers AND contradict
TECHNICAL.md's own still-published 7.8/7.3/6.8/6.2 figures, trading one
undocumented disagreement for another. So $1,743 is frozen here as a DATED
historical constant (RETIRED_EVENING_BASE_SAVE_USD below) -- the same "hardcode a
superseded figure with an explicit provenance comment" pattern
nem3_grandfathering.py's own `old_bracket` already uses for the SAME reason.
This generator changes NOTHING about the published numbers; it converts them
from orphaned/hand-typed to reproducible from a documented, dated constant,
and adds a `basis` field directly in the JSON (not just in TECHNICAL.md) so a
reader of the raw file alone sees why it disagrees with
battery_dispatch_policies.json instead of assuming a bug.

Run from the repo root:  ./.venv/bin/python analysis/extra_results.py
Writes data/extra_results.json atomically: a partial or failed run changes
nothing.
"""
import json
import os
import pathlib


def _repo_root():
    """Walk up for the repo root, so the script runs from any working
    directory (the private/verify sandbox pattern in CLAUDE.md relies on
    this) -- this script needs no private data, only committed data/*.json."""
    here = pathlib.Path(__file__).resolve()
    for parent in [here.parent] + list(here.parents):
        if (parent / "data").is_dir() and (parent / "analysis").is_dir():
            return parent
    raise SystemExit("extra_results.py: could not locate the repo root")


ROOT = _repo_root()
DATA = ROOT / "data"
OUT = DATA / "extra_results.json"

# The other six top-level keys, frozen: no other committed script or
# artifact reproduces them, so this generator copies them through unchanged
# rather than inventing a methodology this repo has no record of.
FROZEN_KEYS = ("phantom", "price_map", "nbt", "cleaning", "trueup", "ev_fleet")

# The RETIRED evening-only-dispatch base saving this ladder was seeded from
# (TECHNICAL.md section 3.11, section 3.8) -- an earlier value of
# behavior_rebuild.py's own evening-only-after-EV-behavior-shift marginal,
# now $1,753, NOT battery_dispatch_policies.json's own, different,
# pw3.evening.save scenario (see RETIRED_EVENING_BASE_SAVE_SOURCE below for
# the full distinction). No longer reproducible from the live pipeline, so
# this is a dated historical constant, not a live read, same as
# nem3_grandfathering.py's own old_bracket constant and for the same reason:
# recomputing from today's pipeline would silently change a number TECHNICAL.md
# still quotes verbatim, trading one undocumented disagreement for another.
RETIRED_EVENING_BASE_SAVE_USD = 1743
RETIRED_EVENING_BASE_SAVE_SOURCE = (
    "TECHNICAL.md section 3.8/3.11: an earlier value of behavior_rebuild.py's "
    "own evening-only-dispatch-after-EV-behavior-shift marginal (currently "
    "$1,753/yr, not $1,743 -- NOT battery_dispatch_policies.json's own, "
    "different, pw3.evening.save scenario, which has no EV-behavior shift "
    "applied first). This figure predates whatever later change moved "
    "$1,743 to $1,753 and is frozen, not re-derived, so the committed "
    "ladder below never silently moves)")


def _escalation_ladder(save1, cost=14500, fade=0.01, disc=0.05):
    """Reimplements battery_dispatch_policies.escalation() locally rather
    than importing it -- that module reads private/household.yaml at import
    time (via behavior_rebuild), which would make this script need the
    private archive for a small, self-contained, already-published formula
    it otherwise has no other dependency on (same reasoning tou_spread.py's
    own _season_blocks reimplementation uses for rates_history.py's
    version)."""
    out = {}
    for esc in (0.03, 0.05, 0.08, 0.12):
        cum = 0; pay = None; npv = -cost
        for y in range(1, 16):
            sv = save1 * ((1 + esc) ** (y - 1)) * ((1 - fade) ** (y - 1)); cum += sv
            if pay is None and cum >= cost: pay = y - 1 + (cost - (cum - sv)) / sv
            if y <= 10: npv += sv / ((1 + disc) ** y)
        out[f"{int(esc*100)}%"] = {"payback_yr": round(pay, 1), "npv10": round(npv)}
    return out


def build():
    if not OUT.exists():
        raise SystemExit(f"extra_results.py: {OUT} does not exist -- this "
                         "script patches an existing committed file, it "
                         "does not create one from scratch (see the module "
                         "docstring: most of the file has no other source)")
    existing = json.loads(OUT.read_text())
    expected = set(FROZEN_KEYS) | {"escalation"}
    actual = set(existing)
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        raise SystemExit(
            f"extra_results.py: committed {OUT}'s top-level keys no longer "
            f"match what this script expects -- missing: {sorted(missing) or 'none'}, "
            f"unexpected new keys: {sorted(extra) or 'none'}. A NEW key must be "
            f"investigated and either added to FROZEN_KEYS (if it is another "
            f"one-time measurement with no other source, like the other six) "
            f"or given its own real generator -- it cannot be silently passed "
            f"through unvalidated (Codex review, issue #34, pass 2).")

    escalation = _escalation_ladder(RETIRED_EVENING_BASE_SAVE_USD)
    escalation["basis"] = (
        f"RETIRED variant: $14,500 installed, the superseded "
        f"${RETIRED_EVENING_BASE_SAVE_USD:,}/yr evening-only base saving "
        f"({RETIRED_EVENING_BASE_SAVE_SOURCE}), 1%/yr capacity fade, 5% "
        f"discount. The CURRENT published ladder (report section 13) is "
        f"data/battery_dispatch_policies.json -> "
        f"escalation_greedy_pw3_post_behavior, rebased on the post-behavior "
        f"$2,238/yr marginal -- the two ladders disagree by design, not by "
        f"drift; see TECHNICAL.md section 3.11.")

    out = {}
    for key in existing:
        out[key] = escalation if key == "escalation" else existing[key]
    return out


def main():
    out = build()
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1) + "\n")
    os.replace(tmp, OUT)
    print("wrote data/extra_results.json")
    esc = {k: v for k, v in out["escalation"].items() if k != "basis"}
    print("escalation (retired evening-only variant, dated historical basis): " +
         ", ".join(f"{k} {v['payback_yr']}yr" for k, v in esc.items()))


if __name__ == "__main__":
    main()
