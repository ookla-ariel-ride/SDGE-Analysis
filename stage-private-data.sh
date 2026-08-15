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
# the script stages only into a working tree of the checkout it lives in, and
# only into real directories inside it. See "DESTINATION GUARD" below for
# which TREE is accepted and why, "DESTINATION PATH GUARD" for the paths
# INSIDE that tree (a symbolic link below the root would otherwise carry the
# archive back out of it), and "ENVIRONMENT SANITIZING" for why the check
# reads the filesystem rather than the git variables an inherited environment
# can answer it with.
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
# ENVIRONMENT SANITIZING (issue #184, adversarial review) -- runs before the
# guard below, because the guard is only as trustworthy as the question it
# asks git, and git answers "which repository is this path in" from the
# ENVIRONMENT first and the filesystem second.
#
# Left alone that makes the guard advisory rather than binding:
#
#   GIT_DIR=<this checkout>/.git GIT_WORK_TREE=<anywhere> \
#     git -C <anywhere> rev-parse --git-common-dir   -> this checkout's .git
#
# and the guard waves <anywhere> through. Verified, not theorized: it staged
# the archive into an unrelated scratch directory and exited 0. GIT_COMMON_DIR
# is worse -- it needs no GIT_DIR, it IS the value this guard compares, and it
# answers BOTH probes at once, so the comparison passes whatever the two
# directories really are. That defeats even the two destinations the test
# suite checks by name: an unrelated repository and a different clone of the
# same remote.
#
# So every input to the identity question has to come from the filesystem.
# What is cleared, and why each earns its place:
#
#   GIT_DIR                           replaces repository discovery outright
#   GIT_COMMON_DIR                    IS the identity this guard compares
#   GIT_WORK_TREE                     supplies --show-toplevel, the root check
#   GIT_CEILING_DIRECTORIES           truncates the upward walk
#   GIT_DISCOVERY_ACROSS_FILESYSTEM   extends the walk across mount points,
#                                     which can turn "not a repository" into
#                                     "a repository" -- the direction that
#                                     turns a refusal into an acceptance
#   GIT_CONFIG*  (the whole family,   config can carry core.worktree, which is
#   matched by prefix because the     a working-tree location under another
#   GIT_CONFIG_KEY_<n>/VALUE_<n>      name; GIT_CONFIG_GLOBAL holding one was
#   pairs are unbounded)              observed moving --show-toplevel
#   GIT_OBJECT_DIRECTORY,             these select CONTENT inside an already
#   GIT_ALTERNATE_OBJECT_DIRECTORIES, chosen repository. None of them moved
#   GIT_INDEX_FILE, GIT_NAMESPACE     either probe when tested, so they are
#                                     defence in depth and not part of the
#                                     fix -- listed because they cost nothing
#                                     and keep a later git-reading step honest
#
# Deliberately NOT cleared: GIT_EXEC_PATH, GIT_SSH_COMMAND, GIT_TEMPLATE_DIR
# and the like. They tell git HOW to run, not which repository it is looking
# at, and on a relocatable git install unsetting GIT_EXEC_PATH breaks git
# outright -- converting a legitimate run into a refusal for no gain.
#
# CLEAR, not REJECT. Refusing outright when these are set would be louder, but
# GIT_DIR is exported by every git hook (this repo installs its own via
# core.hooksPath) and by `git rebase --exec` and `git bisect run`, so a reject
# would fail ordinary invocations in which GIT_DIR names THIS checkout and
# clearing reaches the identical, correct verdict. The operator's remedy for
# such a refusal would be to re-run under `env -u GIT_DIR ...` -- to perform
# this clear by hand -- so it would cost a step, add no information, and
# create exactly the pressure to reach for a bypass flag that the guard below
# exists to avoid. Silence is the part actually worth fixing, so anything
# cleared is ANNOUNCED on stderr: the environment is never trusted, and never
# ignored quietly either.
#
# Cleared in THIS shell rather than around each probe, so the guard's verdict
# and everything it authorizes -- the copies, the household.py child that
# reads has_gas, and any git command a later edit adds -- run in one
# environment. A guard that sanitizes only its own probes and then hands the
# untouched environment to the work it approved has checked one repository and
# written into another.
# ---------------------------------------------------------------------------
_cleared=""
for _v in GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_CEILING_DIRECTORIES \
          GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_OBJECT_DIRECTORY \
          GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE GIT_NAMESPACE \
          ${!GIT_CONFIG*}; do
  if [ -n "${!_v+set}" ]; then
    _cleared="$_cleared $_v"
    unset "$_v"
  fi
done
if [ -n "$_cleared" ]; then
  echo "stage-private-data.sh: ignoring inherited git variable(s):$_cleared" >&2
  echo "  These can make git report any directory as part of any repository, so the" >&2
  echo "  destination check reads the filesystem instead. Nothing else was changed." >&2
fi

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

# ---------------------------------------------------------------------------
# DESTINATION PATH GUARD (issue #184, adversarial review) -- the guard above
# decides WHICH TREE may be written to; this one decides which PATHS INSIDE it
# are real. Both run before the first byte.
#
# The root check alone is not enough, because `mkdir -p` and `cp` follow a
# symbolic link at any path BELOW the root they were handed. Reproduced on a
# genuine linked worktree of this checkout with private/1-raw-data pre-made as
# a link to an unrelated directory: every repository check passed, the run
# exited 0, the whole archive landed outside the worktree -- and the success
# line at the bottom of this script announced that nothing outside the
# gitignored tree had been written. A false reassurance is worse than none,
# which is why that line is now derived from the checks below.
#
# The reachable paths are exactly the GITIGNORED ones: private/1-raw-data and
# private/verify do not exist in a fresh checkout, so nothing pre-empts a link
# planted there and `git status` will never mention it. private/ itself is a
# different shape -- the committed private/README.md means a checkout already
# has it as a real directory -- so it is checked, not assumed.
#
# REJECT a link, rather than resolve it and check containment. Resolving would
# permit a link that stays inside the tree, and this script would then be
# placing one household's raw archive through an indirection it cannot see
# through at the moment it writes: a link is re-pointable, and the file that
# decides where the PII lands would no longer be the one the guard read. The
# cost of the strict rule is one error message on a legitimate in-tree link;
# the cost of the permissive one is a copy of the archive somewhere nobody
# looks. Nothing in the documented flow (README "Refreshing this analysis")
# links these paths, and analysis/dry_run.py already refuses to symlink
# private/ into its sandbox for the same reason -- a link there is "a writable
# path from the sandbox back into the raw archive, and a stray write there is
# unrecoverable". The pre-#33 staging recipe DID link the bill directories in
# by hand, so a worktree staged that way is refused now; the message says so,
# and the remedy is to let this script copy them.
#
# CREATE without following: `mkdir -p` is satisfied by an existing link that
# resolves to a directory and writes straight through it, so it cannot be used
# here. Each component is created with a plain `mkdir`, one level at a time,
# which never follows and fails outright if the name is already a link. That
# is also what makes the check binding rather than advisory: the directories
# this run writes into were created by this run, non-following, after the
# check -- there is no window in which a checked path is swapped for a link
# without the mkdir failing.
#
# ONCE, UP FRONT, not before each write: every copy below goes through the
# three directories checked here, in one uninterrupted block, and a shell `cp`
# has no O_NOFOLLOW that would make a per-write check atomic anyway -- a
# per-write test would add noise and still not be a guarantee. The hazard this
# closes is a link planted BEFORE the run (a stale worktree, a leftover
# `ln -sfn`), not an attacker racing the copies: anyone able to write inside
# the destination mid-run already has the archive. The up-front decision is
# then RE-VERIFIED after the writes, so the closing message reports something
# that was measured rather than assumed.
# ---------------------------------------------------------------------------
_refuse() {   # $1 = headline, remaining args = detail lines.
              # Defined ahead of anything that writes, so "REFUSED" appears in
              # this file only before the first mkdir/cp -- the ordering
              # analysis/test_stage_private_data.py checks structurally.
  echo "stage-private-data.sh: REFUSED -- $1 (nothing was written)" >&2
  shift
  for _line in "$@"; do echo "  $_line" >&2; done
  exit 1
}

_reject_link() {   # $1 = a path this script writes to, or writes through
  [ -L "$1" ] || return 0
  _refuse "a destination path is a symbolic link" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "link:        $1" \
    "points to:   $(readlink "$1" 2>/dev/null || echo '<unreadable>')" \
    "expected:    a real file or directory inside $DST_REAL" \
    "cp follows a link, so the archive would land at that target instead --" \
    "outside the working tree this script just validated. Replace the link" \
    "with a real directory and re-run; this script copies the bill" \
    "directories itself, so nothing here needs to be linked in by hand."
}

_check_dir_slot() {   # $1 = a directory this script needs; writes NOTHING
  _reject_link "$1"
  if [ -e "$1" ]; then
    [ -d "$1" ] || _refuse "a destination path exists and is not a directory" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "expected:    a directory, or a name this script can create"
  fi
}

_ensure_contained_dir() {   # $1 = absolute path; create it without following
  local real
  _check_dir_slot "$1"
  if [ ! -e "$1" ]; then
    mkdir "$1" || _refuse "could not create a destination directory" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "expected:    a creatable path -- a plain mkdir is used deliberately," \
      "             so it fails rather than write through a link planted here"
  fi
  # `pwd -P` differs from the literal path if ANY component of it is a link,
  # so this one comparison proves containment for the whole path at once --
  # including components above the one just created.
  real=$(_physical "$1" || true)
  [ "$real" = "$1" ] || _refuse "a destination directory does not resolve to itself" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "path:        $1" \
    "resolves to: ${real:-<unresolvable>}" \
    "expected:    a path whose every component is a real directory inside" \
    "             $DST_REAL"
}

_reject_links_under() {   # $1 = a directory `cp -R` may write anywhere beneath
  local links
  [ -d "$1" ] || return 0
  if ! links=$(find "$1" -type l -print); then
    _refuse "the destination could not be scanned for symbolic links" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "expected:    a readable directory tree"
  fi
  [ -z "$links" ] || _refuse "the destination contains symbolic link(s)" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "links:       $(echo $links)" \
    "expected:    a tree of real files and directories -- cp -R writes" \
    "             through any link it finds here, and one pointing back at" \
    "             the source archive makes this run overwrite the originals" \
    "This is what the pre-#33 recipe's hand-linked electric-bills/gas-bills" \
    "look like. Replace them with real directories; the copies below stage" \
    "them for you."
}

# Every path is CHECKED first and only then created, in two passes rather than
# one interleaved walk: a refusal on private/verify must not leave behind the
# private/1-raw-data a single pass would already have made. "Nothing was
# written" then means nothing at all, including empty directories.
_check_dir_slot "$DST_REAL/private"
_check_dir_slot "$DST_REAL/private/1-raw-data"
_check_dir_slot "$DST_REAL/private/verify"

# 1-raw-data is scanned in full, because `cp -R` below writes anywhere beneath
# it (electric-bills/, gas-bills/, caiso_raw/) and because the glob copies pick
# their destination names from the source, so no fixed list of leaves would
# cover them. private/verify is NOT scanned in full: this script writes exactly
# three named files there, while the sandbox around them legitimately holds a
# venv whose bin/ entries are symlinks -- scanning it would refuse a normal
# re-stage over a working sandbox for links this script never touches.
_reject_links_under "$DST_REAL/private/1-raw-data"
for _leaf in "$DST_REAL/private/household.yaml" \
             "$DST_REAL/private/verify/usage.csv" \
             "$DST_REAL/private/verify/samA.csv" \
             "$DST_REAL/private/verify/samB.csv"; do
  _reject_link "$_leaf"
done

_ensure_contained_dir "$DST_REAL/private"
_ensure_contained_dir "$DST_REAL/private/1-raw-data"
_ensure_contained_dir "$DST_REAL/private/verify"

cp "$SRC/private/household.yaml"                      "$DST_REAL/private/household.yaml"
cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST_REAL/private/1-raw-data/"
cp "$SRC"/private/1-raw-data/enphase_sam8760_*.csv    "$DST_REAL/private/1-raw-data/"
cp "$SRC/private/1-raw-data/gas.csv"                  "$DST_REAL/private/1-raw-data/"
cp -R "$SRC/private/1-raw-data/electric-bills"        "$DST_REAL/private/1-raw-data/"
cp "$SRC/private/1-raw-data/electric_billing_history_2024-2026.csv" \
   "$DST_REAL/private/1-raw-data/"

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
    cp -R "$SRC/private/1-raw-data/gas-bills" "$DST_REAL/private/1-raw-data/"
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

cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST_REAL/private/verify/usage.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$DST_REAL/private/verify/samA.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$DST_REAL/private/verify/samB.csv"

if [ -d "$SRC/private/1-raw-data/caiso_raw" ] && \
   ls "$SRC"/private/1-raw-data/caiso_raw/caiso_co2_*.csv >/dev/null 2>&1; then
  cp -R "$SRC/private/1-raw-data/caiso_raw" "$DST_REAL/private/1-raw-data/"
fi

# ---------------------------------------------------------------------------
# POST-WRITE VERIFICATION -- what the closing line is allowed to claim.
#
# The line this replaces asserted "nothing outside the gitignored tree was
# written" without checking anything, and printed it verbatim on the run that
# copied the archive outside the tree. So the claim is now measured: every
# directory the copies above ran through is re-resolved, and only if each one
# still IS its own literal path -- no component turned into a link, none of
# them was a link all along -- does the message say the writes stayed inside.
# The count is read off the destination afterwards rather than assumed from
# the list of cp lines.
#
# Not a REFUSAL: by here the files are already written, so there is nothing
# left to refuse. It is a failed verification, and it says that instead of
# claiming a clean refusal it cannot make.
# ---------------------------------------------------------------------------
for _dir in "$DST_REAL/private" "$DST_REAL/private/1-raw-data" "$DST_REAL/private/verify"; do
  if [ -L "$_dir" ] || [ "$(_physical "$_dir" || true)" != "$_dir" ]; then
    echo "stage-private-data.sh: FAILED -- $_dir does not resolve inside the destination" >&2
    echo "  The files just written cannot be confirmed to be inside $DST_REAL." >&2
    echo "  Check that path before treating this run as complete." >&2
    exit 1
  fi
done
if ! _staged=$(find "$DST_REAL/private" -type f -print); then
  echo "stage-private-data.sh: FAILED -- could not enumerate $DST_REAL/private after staging" >&2
  exit 1
fi
_n=0
while IFS= read -r _line; do
  if [ -n "$_line" ]; then _n=$((_n + 1)); fi
done <<< "$_staged"

echo "staged into $DST_REAL/private/ ($_n files now under it) — verified after writing:"
echo "  every directory written through is a real directory inside that working tree, so"
echo "  nothing was written outside the gitignored tree"
