#!/bin/bash
# stage-private-data.sh SOURCE_WORKING_COPY DEST_CLONE
# ---------------------------------------------------------------------------
# Copies the gitignored PRIVATE inputs a full pipeline run needs from an
# existing working copy into a fresh clone, so the documented private/verify
# reproduction flow (CLAUDE.md "Commands") can run there. Copies NO secrets
# (.env is never touched) and nothing this script stages is ever committed —
# every destination is inside the clone's gitignored private/ tree.
#
# The destination is CHECKED before the first byte is written: this copy of
# the script stages only into a working tree of the checkout it lives in.
# See "DESTINATION GUARD" below for what that refuses and why.
#
# What the pipeline needs and why:
#   private/household.yaml                    intake file (analysis/household.py)
#   private/1-raw-data/Electric_15_Minute_*.csv  Green Button 15-min interval data
#   private/1-raw-data/enphase_sam8760_*.csv  Enphase SAM hourly load, one file per
#                                             calendar year (glob-copied, so a future
#                                             year's export needs no script change)
#   private/1-raw-data/gas.csv                gas Green Button daily therms
#   private/1-raw-data/electric-bills/*.pdf   detailed electric statements
#                                             (bill_decomposition.py, parse_bills.py,
#                                             cca_bundled_counterfactual.py, cca_rate_extraction.py)
#   private/1-raw-data/electric_billing_history_2024-2026.csv  SDG&E's own billing-
#                                             history export (bill_decomposition.py)
#   private/1-raw-data/gas-bills/*.pdf        (has_gas households only) detailed gas
#                                             statements (parse_bills.py)
#   private/1-raw-data/caiso_raw/             (optional) CAISO day-cache; without it
#                                             carbon_fullyear.py rebuilds exactly from
#                                             the committed data/caiso_hourly_intensity.csv
#   private/verify/usage.csv, samA.csv, samB.csv  the sandbox copies the verify flow
#                                             expects (samA=partial year, samB=full year)
#
# Deliberately NOT staged: private/1-raw-data/dsgs_events/ and
# .../sdge_nbt_export_rates/ -- both are read only under a non-default flag
# (dsgs_vpp_backtest.py --build-calendar, nem3_grandfathering.py --build-rates)
# that no automated pipeline run ever passes. See
# analysis/test_stage_private_data.py's OPTIONAL_NOT_STAGED for the mechanical
# check that keeps this list honest as generators change.
# ---------------------------------------------------------------------------
set -euo pipefail
SRC="${1:?usage: stage-private-data.sh SOURCE_WORKING_COPY DEST_CLONE}"
DST="${2:?usage: stage-private-data.sh SOURCE_WORKING_COPY DEST_CLONE}"

# ---------------------------------------------------------------------------
# DESTINATION GUARD (issue #184) -- runs BEFORE the first write.
#
# On 2026-08-13 this script copied household.yaml and the whole raw archive
# into ANOTHER PROJECT'S git worktree. The caller's `git worktree add ... |
# tail -2` had failed, but a pipeline's exit status is the LAST command's, so
# `tail` returned 0, `set -e` saw success, and the staging step ran against a
# directory this repo does not own. That repo does not gitignore private/, so
# the archive sat one `git add -A` away from a commit into an unrelated public
# repository. Nothing leaked, and CLAUDE.md sec.4's .env precedent says why a
# briefing is not the fix: what backstops a rule people forget is a mechanism.
#
# The invariant this enforces: A COPY OF THIS SCRIPT ONLY EVER WRITES INTO A
# WORKING TREE OF THE CHECKOUT IT ITSELF LIVES IN. The reference is this
# file's own location, so the operator's choice of WHICH copy to run is the
# deliberate act -- staging into a fresh clone still works, but only by
# running that clone's own copy (README "Refreshing this analysis"), which
# means standing in the destination, the confirmation the incident lacked.
#
# `git rev-parse --git-common-dir` is the right identity: it is shared by a
# checkout and every linked worktree of it, and differs for every other
# clone -- including a clone of the same remote, which is why `remote -v` is
# the wrong test (it matches any clone sharing an origin, while the question
# that decides where a secret lands is "is this the checkout I think it is").
#
# --path-format=absolute is load-bearing, not decoration: without it
# rev-parse returns a RELATIVE ".git" from a repo root and an ABSOLUTE path
# from a linked worktree, so the naive comparison rejects a perfectly
# legitimate destination. Both sides are also resolved with `pwd -P`, since
# --show-toplevel reports the physical path while $DST may arrive through a
# symlink (/tmp, /var/folders on macOS) or with a trailing slash.
#
# A destination that does not exist is REFUSED, never created: it cannot be a
# working tree of anything, and "mkdir -p whatever I was handed" is precisely
# what turned a failed `git worktree add` into a copy of the archive.
# ---------------------------------------------------------------------------
_physical() { (cd -- "$1" >/dev/null 2>&1 && pwd -P); }

_common_git_dir() {   # absolute, symlink-resolved --git-common-dir of $1, or fail
  local d=$1 out
  [ -d "$d" ] || return 1
  if ! out=$(git -C "$d" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
    # git < 2.31 has no --path-format; its output is relative to $d (git -C
    # already chdir'd there), so normalize rather than compare it raw.
    out=$(git -C "$d" rev-parse --git-common-dir 2>/dev/null) || return 1
  fi
  [ -n "$out" ] || return 1
  case "$out" in /*) ;; *) out="$d/$out" ;; esac
  _physical "$out"
}

SELF_DIR=$(dirname -- "${BASH_SOURCE[0]}")
if ! SELF_GIT=$(_common_git_dir "$SELF_DIR"); then
  echo "stage-private-data.sh: REFUSED -- this script is not inside a git working tree" >&2
  echo "  script:      ${BASH_SOURCE[0]}" >&2
  echo "  expected:    to be run from a checkout of this repository, whose worktrees are" >&2
  echo "               the only destinations it may stage private data into" >&2
  echo "  Nothing was written." >&2
  exit 1
fi

DST_REAL=$(_physical "$DST" || true)
if [ -z "$DST_REAL" ]; then
  if [ -e "$DST" ]; then FOUND="not a directory"; else FOUND="no such directory"; fi
  echo "stage-private-data.sh: REFUSED -- destination is not an existing directory (nothing was written)" >&2
  echo "  destination: $DST" >&2
  echo "  found:       $FOUND" >&2
  echo "  expected:    an EXISTING working tree of this checkout (git common dir $SELF_GIT)" >&2
  if [ ! -e "$DST" ]; then
    echo "  This script never creates the destination: if a preceding 'git worktree add'" >&2
    echo "  failed (check its exit status directly -- piping it hides the failure), creating" >&2
    echo "  the directory here is how private data lands somewhere nobody will look." >&2
    echo "  Create it first:  git worktree add \"$DST\" -b <branch> origin/main" >&2
  fi
  exit 1
fi

if ! DST_GIT=$(_common_git_dir "$DST_REAL"); then
  echo "stage-private-data.sh: REFUSED -- destination is not a git working tree (nothing was written)" >&2
  echo "  destination: $DST  (resolved: $DST_REAL)" >&2
  echo "  found:       not a git repository" >&2
  echo "  expected:    a working tree of this checkout (git common dir $SELF_GIT)" >&2
  echo "  This script stages one household's raw private archive and will only write it" >&2
  echo "  into a working tree of the checkout it lives in ($SELF_DIR)." >&2
  exit 1
fi

if [ "$DST_GIT" != "$SELF_GIT" ]; then
  echo "stage-private-data.sh: REFUSED -- destination belongs to a DIFFERENT repository (nothing was written)" >&2
  echo "  destination: $DST  (resolved: $DST_REAL)" >&2
  echo "  found:       git common dir $DST_GIT" >&2
  echo "  expected:    git common dir $SELF_GIT" >&2
  echo "  A different clone of the same remote is a different checkout and is refused too:" >&2
  echo "  sharing an origin does not make it the checkout you think it is. To stage into" >&2
  echo "  that clone, run ITS OWN copy of this script from inside it." >&2
  exit 1
fi

# A directory can share this checkout's common dir and still have no working
# tree of its own -- the .git directory itself is the reachable case. Handled
# explicitly so it fails with a message rather than dying on rev-parse's own
# exit status, which under `set -e` would be a silent 128.
if ! DST_TOP_RAW=$(git -C "$DST_REAL" rev-parse --show-toplevel 2>/dev/null) \
   || [ -z "$DST_TOP_RAW" ] || ! DST_TOP=$(_physical "$DST_TOP_RAW"); then
  echo "stage-private-data.sh: REFUSED -- destination has no working tree of its own (nothing was written)" >&2
  echo "  destination: $DST  (resolved: $DST_REAL)" >&2
  echo "  found:       a bare repository or a git internal directory" >&2
  echo "  expected:    the ROOT of a working tree of this checkout" >&2
  exit 1
fi
if [ "$DST_TOP" != "$DST_REAL" ]; then
  echo "stage-private-data.sh: REFUSED -- destination is a subdirectory, not a worktree root (nothing was written)" >&2
  echo "  destination: $DST  (resolved: $DST_REAL)" >&2
  echo "  expected:    the ROOT of a working tree of this checkout" >&2
  echo "  did you mean: $DST_TOP" >&2
  exit 1
fi

mkdir -p "$DST/private/1-raw-data" "$DST/private/verify"

cp "$SRC/private/household.yaml"                      "$DST/private/household.yaml"
cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST/private/1-raw-data/"
cp "$SRC"/private/1-raw-data/enphase_sam8760_*.csv    "$DST/private/1-raw-data/"
cp "$SRC/private/1-raw-data/gas.csv"                  "$DST/private/1-raw-data/"
cp -R "$SRC/private/1-raw-data/electric-bills"        "$DST/private/1-raw-data/"
cp "$SRC/private/1-raw-data/electric_billing_history_2024-2026.csv" \
   "$DST/private/1-raw-data/"

# Read the authoritative flag with the SAME YAML parser the pipeline itself
# uses (household.py's own get(), via yaml.safe_load) rather than a text
# scan (Codex review, issue #33, pass 3): a text-based grep either matches
# an unrelated earlier mention of "has_gas:" in a comment, or rejects a
# valid PyYAML boolean spelling household.py itself accepts (True/TRUE/yes,
# not just lowercase true) -- both would fail this script on a household
# configuration the real pipeline runs on without complaint. A missing
# gas-bills/ on a has_gas:true household must fail loudly here, not two
# steps later inside parse_bills.py against a destination this script
# already reported as fully staged; a stale gas-bills/ on a has_gas:false
# household is equally a real inconsistency worth stopping for. Mirrors
# parse_bills.py's own fail-closed has_gas invariant exactly.
if [ ! -x "$SRC/.venv/bin/python" ]; then
  echo "stage-private-data.sh: $SRC/.venv/bin/python not found -- set up the venv first (CLAUDE.md Commands)" >&2
  exit 1
fi
HAS_GAS=$(PYTHONPATH="$SRC/analysis" "$SRC/.venv/bin/python" -c \
  "import household as hh; print(hh.get('household.has_gas'))")
case "$HAS_GAS" in
  True)
    if [ ! -d "$SRC/private/1-raw-data/gas-bills" ]; then
      echo "stage-private-data.sh: household.has_gas is true but $SRC/private/1-raw-data/gas-bills is missing" >&2
      exit 1
    fi
    cp -R "$SRC/private/1-raw-data/gas-bills" "$DST/private/1-raw-data/"
    ;;
  False)
    if [ -d "$SRC/private/1-raw-data/gas-bills" ]; then
      echo "stage-private-data.sh: household.has_gas is false but $SRC/private/1-raw-data/gas-bills exists (stale?)" >&2
      exit 1
    fi
    ;;
  *)
    echo "stage-private-data.sh: unexpected household.has_gas value from household.py: ${HAS_GAS:-empty}" >&2
    exit 1
    ;;
esac

cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST/private/verify/usage.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$DST/private/verify/samA.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$DST/private/verify/samB.csv"

if [ -d "$SRC/private/1-raw-data/caiso_raw" ] && \
   ls "$SRC"/private/1-raw-data/caiso_raw/caiso_co2_*.csv >/dev/null 2>&1; then
  cp -R "$SRC/private/1-raw-data/caiso_raw" "$DST/private/1-raw-data/"
fi

echo "staged into $DST/private/ — nothing outside the gitignored tree was written"
