#!/usr/bin/env python3
"""Compose data/package_results.json from the integrated-pipeline artifacts.

This script makes the packages artifact reproducible (CLAUDE.md §9: every committed
artifact regenerable by a committed script). It computes nothing new: every figure is
read from the two upstream artifacts that the integrated pipeline writes —

  data/behavior_rebuild.json            (behavior scenarios a–d, baseline bill)
                                        — on a household whose intake says
                                        household.has_ev is false, scenarios a
                                        and b are explicit not-applicable stubs
                                        with no figure, and the LOW package is
                                        scenario c instead (see below)
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
  loudly rather than resolved in silence (see _resolve_artifact). Both artifacts
  must come from the SAME cohort, though: a current-run copy of only ONE of the
  two is refused rather than silently paired with the OTHER'S possibly-stale
  committed copy (see _check_cohort) — resolving each artifact's OWN staleness
  independently is not the same question as whether the two artifacts agree on
  which run they represent. KNOWN LIMITATION: _check_cohort only checks file
  CO-PRESENCE, not true provenance -- a private/verify/ sandbox reused across
  sessions (CLAUDE.md's own documented pattern) could hold a current-run
  battery_dispatch_policies.json from THIS session alongside a leftover
  behavior_rebuild.json from an EARLIER one; both would read as "present" and
  the cohort check would pass despite representing different runs. Closing
  that gap needs a shared run identifier written by behavior_rebuild.py and
  battery_dispatch_policies.py themselves (both outside this script's own
  file boundary) -- filed as a follow-up rather than expanded into here.
  CLAUDE.md's section 9 regeneration gate already runs behavior_rebuild.py
  and battery_dispatch_policies.py before this script.

ONE FREE FIX ACROSS ALL THREE PACKAGES. packages.LOW is one behavior scenario on
its own; packages.MID/HIGH are that scenario PLUS the battery, from
battery_dispatch_policies.py's post_behavior block. Which scenario depends on the
intake flag household.has_ev (a with an EV, c without), and both generators now
publish the one they used -- packages.LOW.free_fix_scenario here,
post_behavior.free_fix_scenario there. A disagreement is refused, not composed:
see the check beside `pb` below.

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


def _check_cohort(specs):
    """Both upstream artifacts must come from the SAME run, never one current-run
    copy blended with the OTHER's stale, committed copy.

    _resolve_artifact() picks a source per artifact independently. That is
    correct for staleness (this run's copy vs. the promoted one, same
    artifact), but composing a current-run behavior_rebuild.json with a
    committed battery_dispatch_policies.json left over from an EARLIER
    session (or vice versa) would silently blend two different runs into one
    package figure and still exit 0 — the disagreement notice in
    _resolve_artifact only ever compares an artifact against its OWN prior
    copy, never against the other artifact's provenance. Fail closed instead:
    if exactly one of the two has a current-run copy in the CWD, the cohort
    is mixed and this script refuses rather than guessing which run to
    trust.

    KNOWN LIMITATION: this checks CO-PRESENCE, not provenance. Two files
    that are both present could still be from different sessions in a
    persistent private/verify/ sandbox (one just regenerated, the other a
    leftover). Closing that fully needs a shared run identifier the two
    upstream generators don't currently write -- out of this script's own
    file boundary; see the module docstring."""
    cwd = pathlib.Path.cwd()
    present = {name: (cwd / name).exists() for name, _ in specs}
    if len(set(present.values())) > 1:
        have = [n for n, p in present.items() if p]
        missing = [n for n, p in present.items() if not p]
        gens = dict(specs)
        raise SystemExit(
            f"mixed-source upstream artifacts: a current-run copy of "
            f"{', '.join(have)} exists in {cwd}, but {', '.join(missing)} "
            "does not. Composing one current-run artifact with the OTHER's "
            "committed (possibly stale) copy would silently blend two "
            "different runs into one package figure. Run "
            f"{', '.join(gens[n] for n in missing)} in this working "
            "directory too before package_results.py (see the ordering "
            "contract above), or remove the stray current-run copy if it's "
            "left over from an unrelated session.")


def _is_not_applicable(node):
    """True when an upstream section is an explicit not-applicable STUB.

    behavior_rebuild.py publishes {"not_applicable": True, "reason": ...} for a
    section whose governing intake flag is false (its own _not_applicable()).
    That is not_applicable, NOT not_determined: the intake DID determine the
    answer, so the stub is a VALID artifact, never a broken one.

    Read the ARTIFACT, which is what this script consumes -- never the flag
    file, never charger.kw, and never a merely MISSING key. A missing key is a
    malformed artifact and must keep failing loudly; only this explicit marker
    means "the domain does not exist for this household".
    """
    return isinstance(node, dict) and node.get("not_applicable") is True


_check_cohort([(BEHAVIOR_JSON, "behavior_rebuild.py"), (DISPATCH_JSON, "battery_dispatch_policies.py")])
br = _resolve_artifact(BEHAVIOR_JSON, "behavior_rebuild.py")
bp = _resolve_artifact(DISPATCH_JSON, "battery_dispatch_policies.py")

base = round(br["baseline"]["model_bill"])           # modelled baseline at 6/1/2026 rates (see behavior_rebuild.json)
sc = br["scenarios"]
# Scenarios c and d (flexible house-load shifts) exist for EVERY household.
c, d = (round(sc[k]["saved"]) for k in ("c", "d"))
# Scenarios a and b are the EV-only rungs. On a household whose intake says
# household.has_ev is false, behavior_rebuild.py publishes them as explicit
# not-applicable stubs with no "saved" figure at all -- so they must not be
# computed here either. The LOW package is still a real, free behavior fix on
# such a household: it becomes scenario c, the pure 25% flexible house-load
# shift, so section 7 keeps its LOW/MID/HIGH structure.
ev_shift_applies = not (_is_not_applicable(sc.get("a")) or _is_not_applicable(sc.get("b")))
if ev_shift_applies:
    a, b = (round(sc[k]["saved"]) for k in ("a", "b"))
    low_scenario = "a"
    low_savings = a
    low_range = [b, c]
    low_note = (f"EV-only 100% compliance; 80% = ${b:,}; +25% flexible house "
                f"load = ${c:,}; stretch (50%) = ${d:,}")
else:
    low_scenario = "c"
    low_savings = c
    low_range = [c, d]
    low_note = ("household.has_ev is false (intake applicability flag), so there "
                "is no EV charging to reschedule; the free fix here is moving "
                "flexible on-peak house load off peak, into the super-off-peak "
                f"window: 25% of it = ${c:,}; stretch (50%) = ${d:,}")
pb = bp["post_behavior"]

# The two artifacts must be composing the SAME free fix.
#
# packages.MID and packages.HIGH are read out of post_behavior, which
# battery_dispatch_policies.py produces by applying one behavior scenario and
# THEN dispatching the battery, re-billed end to end. packages.LOW is that same
# behavior scenario on its own. If the two generators picked different scenarios,
# MID stops being "LOW plus the battery" and becomes a composite spliced from two
# different pipelines -- the failure CLAUDE.md §9's one-pipeline-per-package-
# figure rule exists to prevent, and the one the cohort check above already
# guards in its cross-RUN form. Both generators now publish the scenario they used, so this
# is a comparison of two recorded facts, never a re-derivation of the branch.
#
# A missing key on either side is a mismatch too: it means one artifact predates
# the field, so it was written by a generator that could not have made this
# choice deliberately.
_dispatch_scenario = pb.get("free_fix_scenario")
if _dispatch_scenario != low_scenario:
    raise SystemExit(
        "free-fix scenario mismatch between the two upstream artifacts: "
        f"{DISPATCH_JSON} (written by battery_dispatch_policies.py) says its "
        f"post_behavior block sits on top of behavior scenario "
        f"{_dispatch_scenario!r}, while {BEHAVIOR_JSON} (written by "
        f"behavior_rebuild.py) makes packages.LOW behavior scenario "
        f"{low_scenario!r}. Composing packages.MID/HIGH from a different "
        "behavior scenario than packages.LOW splices one package figure out of "
        "two pipelines (CLAUDE.md §9: one integrated simulation, re-billed "
        "end-to-end). Regenerate both from ONE run, in order: behavior_rebuild.py "
        "-> battery_dispatch_policies.py -> package_results.py, in the same "
        "working directory.")

# Which free fix precedes the battery in the MID/HIGH runs. Named in the artifact's
# own `basis` line and in the MID/HIGH notes, so each is true for an EV household
# and a no-EV one alike -- "EV shift" is a false description of a run that shifted
# flexible house load instead. EVERY place this artifact names the first stage of
# the integrated pipeline reads this one value; none of them spells the phrase out.
FREE_FIX_PHRASE = {"a": "EV shift", "c": "flexible house-load shift"}[low_scenario]

batt_alone = bp["pw3"]["greedy"]["save"]             # baseline battery marginal (see battery_dispatch_policies.json)
batt_post = pb["mid"]["battery_marginal"]            # battery marginal after the free fix
evening = bp["pw3"]["evening"]["save"]               # evening-only variant

out = {
    "basis": (f"integrated pipeline: behavior_rebuild.py {FREE_FIX_PHRASE} -> "
              "battery_dispatch_policies.py price-aware dispatch -> rates.bill_nem "
              "(canonical bill-derived rates, NBC on gross imports); baseline "
              f"${base:,} at 6/1/2026 rates; actual 365-day billed baseline $3,282 "
              "(2025-vintage tariffs)"),
    "model_baseline_current_rates": base,
    "packages": {
        "LOW": {
            "cost": 0,
            "savings_yr": low_savings,
            "savings_range": low_range,
            "note": low_note,
            # Which behavior scenario this LOW is: savings_yr IS
            # round(scenarios[free_fix_scenario].saved). Consumers
            # (report_tokens._free_fix_saving) run a derived-from-one-another
            # guard asserting exactly that, and they must read WHICH scenario
            # fed it from the artifact rather than re-deriving the branch --
            # otherwise the guard can check the wrong scenario and either pass
            # vacuously or fire on a household it was never about.
            "free_fix_scenario": low_scenario,
            "projected_bill_current_rates_yr": base - low_savings,
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
            "note": (f"single integrated run: {FREE_FIX_PHRASE} then price-aware "
                     "PW3 dispatch, re-billed end-to-end"),
        },
        "HIGH": {
            "cost": HIGH_COST,
            "savings_yr": pb["high"]["combined_save"],
            "marginal_vs_mid_yr": pb["high"]["combined_save"] - pb["mid"]["combined_save"],
            "projected_bill_current_rates_yr": pb["high"]["bill"],
            # The free fix is named here for the same reason it is named in MID:
            # "post-behavior" alone does not say WHICH behavior, and the two
            # households' answers differ.
            "note": (f"post-behavior ({FREE_FIX_PHRASE}) expansion marginal "
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
