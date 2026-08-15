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
# the script stages only into a REGISTERED working tree of the checkout it
# lives in, only where that tree's own git will not let the archive be
# committed, and only into ordinary, singly-named files and directories inside
# it. See "DESTINATION GUARD" below for which TREE is accepted and why,
# "DESTINATION REGISTRATION GUARD" for why a tree that merely CLAIMS to be one
# is not enough, "DESTINATION IGNORE GUARD" for why belonging to this checkout
# is not the same as being unable to commit the archive, "DESTINATION PATH
# GUARD" for the paths INSIDE that tree (a symbolic link below the root would
# otherwise carry the archive back out of it, a hard link would rewrite a file
# outside it in place, and a FIFO or device node would hang the run or hand the
# archive to another process), and "ENVIRONMENT SANITIZING" for why the check
# reads the filesystem rather than the git variables an inherited environment
# can answer it with -- and why it resolves the destination PATH itself without
# the environment's help, since CDPATH decides what a bare relative name means.
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
#   CDPATH                            not a git variable at all, and it belongs
#                                     here for the reason this block exists:
#                                     the environment can change WHAT A PATH
#                                     MEANS, and every guard below decides its
#                                     verdict about whatever directory `cd`
#                                     picked. POSIX exempts only an operand
#                                     starting with `/`, `./` or `..` from the
#                                     CDPATH search, so `.` is safe and a bare
#                                     relative `mystery` is not. Reproduced: with
#                                     CDPATH set, `stage-private-data.sh SRC
#                                     mystery` run from a directory holding its
#                                     own empty ./mystery validated and staged
#                                     into a DIFFERENT registered worktree, nine
#                                     files, exit 0, while ./mystery stayed
#                                     empty. It cannot escape this checkout's
#                                     worktrees -- every guard still ran, just
#                                     against the wrong one of them -- so it is
#                                     not a cross-repo leak; it is the archive
#                                     somewhere other than the path on the
#                                     command line, silently, which is the same
#                                     sentence the 2026-08-13 incident wrote
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
#
# Announced SEPARATELY by kind, because the two reasons are different and a
# message that gave CDPATH the git-variables explanation would be telling the
# operator something untrue about their own environment.
# ---------------------------------------------------------------------------
_cleared=""
_cleared_path=""
for _v in CDPATH GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_CEILING_DIRECTORIES \
          GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_OBJECT_DIRECTORY \
          GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE GIT_NAMESPACE \
          ${!GIT_CONFIG*}; do
  if [ -n "${!_v+set}" ]; then
    case "$_v" in
      CDPATH) _cleared_path="$_cleared_path $_v" ;;
      *)      _cleared="$_cleared $_v" ;;
    esac
    unset "$_v"
  fi
done
if [ -n "$_cleared" ]; then
  echo "stage-private-data.sh: ignoring inherited git variable(s):$_cleared" >&2
  echo "  These can make git report any directory as part of any repository, so the" >&2
  echo "  destination check reads the filesystem instead. Nothing else was changed." >&2
fi
if [ -n "$_cleared_path" ]; then
  echo "stage-private-data.sh: ignoring inherited path variable(s):$_cleared_path" >&2
  echo "  cd searches CDPATH for a bare relative name, so the destination on the command" >&2
  echo "  line could resolve to a directory nobody named -- and every check below would" >&2
  echo "  then run against that one. Relative paths are resolved from the current" >&2
  echo "  directory only. Nothing else was changed." >&2
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
# It is the right identity and not, on its own, a sufficient one: see
# "DESTINATION REGISTRATION GUARD" below. A matching common dir plus a
# --show-toplevel that equals the destination says the directory CLAIMS to
# belong to this checkout, and a plain directory can make both claims with a
# one-line `.git` gitfile. Membership is decided against git's own register.
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

# ---------------------------------------------------------------------------
# WHICH COPY IS RUNNING (issue #184, /review) -- the guard's whole reference is
# this file's own location, so that location has to be the REAL one.
#
# ${BASH_SOURCE[0]} is the path the caller typed, links and all. Invoked through
# a symlink -- ~/bin/stage-private-data.sh -> <checkout>/stage-private-data.sh,
# the ordinary way a script gets onto a PATH -- the unresolved dirname is
# ~/bin, and the guard then fixes its identity from THAT directory:
#
#   ~/bin is in no repository        -> the run dies with "this script is not
#                                       inside a git working tree", pointing at
#                                       the script rather than at the link
#   ~/bin is inside a versioned      -> SELF_GIT becomes the DOTFILES repo, and
#   dotfiles repo (the common case)     EVERY legitimate destination is refused
#                                       as "belongs to a DIFFERENT repository",
#                                       naming the destination when the
#                                       INVOCATION is what is at fault
#
# The second is the one that matters: a guard that refuses a correct caller,
# with a message that misnames the culprit, is the shape that gets guards
# disabled rather than fixed. So the link chain is followed to the real file
# before the dirname is taken, and both refusals now name the invoking path
# next to the resolved one, so a misdiagnosis is at least legible.
#
# Resolved BY HAND rather than with `readlink -f`: that flag is GNU/coreutils
# and BSD-recent, and macOS -- the platform this is developed on -- has no
# `readlink -f` in its default toolchain (nor `realpath`). The portable pieces
# are `readlink` reading ONE level (already used by _reject_link below), `cd`
# + `pwd -P` for the directory, and a loop for the chain. The hop counter is
# what turns a symlink CYCLE into a refusal instead of a hang -- the same
# reason the FIFO rule exists further down.
# ---------------------------------------------------------------------------
_self_dir() {   # $1 = ${BASH_SOURCE[0]}; prints the real directory it lives in
  local src=$1 dir hops=0
  while [ -L "$src" ]; do
    hops=$((hops + 1))
    [ "$hops" -le 40 ] || return 1        # a cycle, not a chain
    dir=$(_physical "$(dirname -- "$src")") || return 1
    [ -n "$dir" ] || return 1
    src=$(readlink -- "$src") || return 1
    case "$src" in /*) ;; *) src="$dir/$src" ;; esac
  done
  _physical "$(dirname -- "$src")"
}

SELF_PATH="${BASH_SOURCE[0]}"
if ! SELF_DIR=$(_self_dir "$SELF_PATH") || [ -z "$SELF_DIR" ]; then
  echo "stage-private-data.sh: REFUSED -- this script's own location could not be resolved" >&2
  echo "  invoked as:  $SELF_PATH" >&2
  echo "  found:       a symbolic link chain that does not end at a readable file," >&2
  echo "               or one that loops" >&2
  echo "  expected:    a path resolving to a real file inside a checkout of this" >&2
  echo "               repository, since that checkout is what decides which" >&2
  echo "               destinations may be staged into" >&2
  echo "  Nothing was written." >&2
  exit 1
fi
if ! SELF_GIT=$(_common_git_dir "$SELF_DIR"); then
  echo "stage-private-data.sh: REFUSED -- this script is not inside a git working tree" >&2
  echo "  invoked as:  $SELF_PATH" >&2
  echo "  really in:   $SELF_DIR  (symbolic links resolved)" >&2
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
  echo "  this script: $SELF_PATH" >&2
  echo "               (really in $SELF_DIR, which is what fixes the expected common dir)" >&2
  echo "  A different clone of the same remote is a different checkout and is refused too:" >&2
  echo "  sharing an origin does not make it the checkout you think it is. To stage into" >&2
  echo "  that clone, run ITS OWN copy of this script from inside it." >&2
  echo "  If the destination looks right, check the line above: the COPY that is running" >&2
  echo "  decides which repository is expected, and a symlink or a stray copy on your" >&2
  echo "  PATH can make that a repository you did not mean to name." >&2
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
# DESTINATION REGISTRATION GUARD (issue #184, adversarial review) -- the checks
# above establish what the destination SAYS it is; this one checks it against
# what git actually records. Still before the first byte.
#
# "Its common dir is mine, and its --show-toplevel is itself" is not proof of
# membership, because both answers come from a file the destination itself
# supplies. A plain directory holding a one-line `.git` GITFILE --
#
#     printf 'gitdir: %s\n' "<this checkout>/.git" > <unrelated>/.git
#
# -- answers both probes exactly the way a real worktree does while never
# having been registered. Reproduced against this script: rc 0, and the whole
# private archive written into an unrelated scratch directory. The environment
# hardening above does not reach it; the forgery is on disk, not in the
# environment, so no amount of clearing changes the answer.
#
# It is reachable by accident as much as by intent, which is what makes it
# worth a mechanism rather than a warning: a directory copied, rsynced or
# restored from a backup of a linked worktree carries that worktree's `.git`
# gitfile with it, still pointing at this checkout's common dir, and is now a
# plain directory that passes every check above.
#
# So membership is decided by git's own register instead: `git worktree list
# --porcelain`, resolved FROM THE VALIDATED COMMON DIR ($SELF_GIT, derived
# from this script's own location -- never from anything the destination
# said), and the destination must equal one of the entries. The main checkout
# is itself an entry in that listing, so staging into it stays supported.
#
# Both sides are compared PHYSICALLY (`pwd -P`), for the same reason
# --path-format=absolute is load-bearing above: the register holds the path a
# worktree was created with, and a legitimate one created through a symlinked
# parent (/tmp, /var/folders on macOS) would otherwise never match the
# destination the operator hands us. A registered entry whose directory no
# longer exists resolves to nothing and simply matches nothing -- a stale
# register entry cannot admit a destination.
#
# READ WITH -z, and the reason is a MEASURED one rather than the one review
# suggested. The claim under review was that porcelain C-quotes a path
# containing unusual characters, so a legitimate worktree would be wrongly
# refused. That is not what this git does: `git worktree list --porcelain`
# emits the worktree PATH raw -- spaces, tabs, quotes, backslashes, accented
# characters and NEWLINES all pass through unescaped, with core.quotePath at
# its default and when forced true. What IS quoted is the `locked` REASON, and
# git's own manual says so in as many words ("Unless -z is used any 'unusual'
# characters in the lock reason ... are escaped"). So the reported case does
# not exist here.
#
# A worse one does, and it was reproduced: a registered worktree whose path
# contains a NEWLINE is emitted raw, so a line-based parse splits one entry
# across two lines and recovers a TRUNCATED PREFIX of the real path. A
# genuinely registered worktree is then refused -- a guard failing on correct
# input, which is the failure mode that gets guards disabled. Worse than
# unrecoverable: the prefix is a path the register never contained, so a
# line-based parse does not merely lose the entry, it invents one. git's manual
# names this exact case as what `-z` is for ("This makes it possible to parse
# the output when a worktree path contains a newline character").
#
# `-z` changes the RECORD FORMAT, not only the separator, so it was read before
# this parse was written: each attribute record is NUL-terminated instead of
# newline-terminated, each entry still ends with an EMPTY record, and the
# label-space-value shape is unchanged -- but the `locked` reason arrives RAW
# rather than C-quoted. That last part does not reach this parse, which keys
# only on `worktree `, `bare` and the empty record, and is noted because a
# future edit that reads `locked` would be the first to care.
#
# It is read through a PROCESS SUBSTITUTION, never `$(...)`: bash cannot hold a
# NUL in a variable and drops them SILENTLY (measured on bash 3.2, the macOS
# system shell this runs under: `x=$(printf 'a\0b')` yields `ab`, no warning).
# Capturing -z output would therefore glue every record into one string and
# match nothing -- a guard that refuses everything. The `while` still runs in
# THIS shell, which is the property the here-string below was chosen for too.
#
# FALLING BACK rather than requiring a version. A git too old to know `-z`
# writes nothing to stdout, which this reads as "no entries" and retries the
# old line-based listing -- correct for every path without a newline, which is
# every path anyone actually has. Detect-and-fall-back is chosen over declaring
# a minimum version because the alternative fails a legitimate destination on
# an old git and calls it a refusal; a refusal a correct caller cannot fix is
# the thing being repaired here, so it is not worth reintroducing at the seam.
# When the fallback is what ran AND the destination did not match, the refusal
# says so, so an operator whose worktree path really does hold a newline reads
# why instead of guessing.
#
# FAIL CLOSED on anything short of a match: a listing that errors, an empty
# listing after BOTH attempts (git always reports at least the main worktree,
# so empty means the question was not answered), or a path that resolves to
# nothing -- each matches nothing rather than being waved through. `bare`
# entries are dropped before comparison: a bare repository has no working tree
# to stage into.
#
# This runs in the sanitized shell like every other probe here, so the
# environment cannot redirect the listing the way it could redirect rev-parse.
#
# THE ONE LEGITIMATE SHAPE THIS REFUSES, stated rather than discovered later: a
# working tree created with `--separate-git-dir`. Measured, not assumed -- for
# such a checkout git reports the EXTERNAL GITDIR as the main worktree's path
# and never the working tree itself, from either vantage (`git -C <gitdir>` and
# `git -C <a linked worktree>` return identical listings, and no core.worktree
# is set to recover it from). So git's own register does not know that
# directory, and to this guard it is byte-for-byte the forgery above: a plain
# directory whose only claim is a `.git` gitfile. Nothing available here tells
# the two apart, so both are refused, which is the fail-closed direction. The
# remedy is the message's: create the destination with `git worktree add`, or
# stage into an ordinary checkout. analysis/test_stage_private_data.py pins
# this so the limitation stays a decision rather than a surprise.
# ---------------------------------------------------------------------------
_dst_registered=0
_entries=0
_pending=""
_match_pending() {   # compare the entry just finished, then clear it
  local real
  [ -n "$_pending" ] || return 0
  real=$(_physical "$_pending" || true)
  if [ -n "$real" ] && [ "$real" = "$DST_REAL" ]; then _dst_registered=1; fi
  _pending=""
}
_scan_record() {   # $1 = one porcelain record, however it was delimited
  case "$1" in
    "worktree "*) _match_pending; _entries=$((_entries + 1)); _pending=${1#worktree } ;;
    "bare")       _pending="" ;;
    "")           _match_pending ;;
  esac
}

# Neither loop is on the right of a pipe: a `while` there runs in a subshell,
# where _dst_registered would be set and then thrown away -- and the failure
# mode of that mistake is a guard that refuses every destination, so it would be
# found, but the same shape with the sense inverted would not be. Process
# substitution and a here-string both keep the loop in THIS shell.
_rec=""
while IFS= read -r -d '' _rec || [ -n "$_rec" ]; do
  _scan_record "$_rec"
done < <(git -C "$SELF_GIT" worktree list --porcelain -z 2>/dev/null)
_match_pending

# Retry the line-based listing whenever the -z parse did not settle it -- not
# only when -z produced nothing. Both reasons it can produce nothing are real
# (this git predates `worktree list -z`, so it wrote usage to stderr and nothing
# to stdout; or the listing genuinely failed), and retrying on any non-match
# additionally covers a git that answers -z in some shape this parse does not
# recognize. The cost is one extra listing on a path that is about to refuse
# anyway; the benefit is that no unforeseen -z behaviour can turn a legitimate
# destination into a refusal, which is the failure this whole change is about.
_z_entries=$_entries
if [ "$_dst_registered" -ne 1 ]; then
  _pending=""
  _line=""
  if DST_WORKTREES=$(git -C "$SELF_GIT" worktree list --porcelain 2>/dev/null) \
     && [ -n "$DST_WORKTREES" ]; then
    while IFS= read -r _line || [ -n "$_line" ]; do
      _scan_record "$_line"
    done <<< "$DST_WORKTREES"
    _match_pending
  fi
fi

# The NOTE below is keyed on the -z listing having produced nothing, not on the
# fallback having run: only then is the fallback's inability to read a newline
# in a path the reason a registered destination could be sitting here refused.
_z_listing=1
[ "$_z_entries" -gt 0 ] || _z_listing=0

if [ "$_entries" -eq 0 ]; then
  echo "stage-private-data.sh: REFUSED -- this checkout's worktrees could not be listed (nothing was written)" >&2
  echo "  destination: $DST  (resolved: $DST_REAL)" >&2
  echo "  found:       'git worktree list' failed or reported nothing for $SELF_GIT," >&2
  echo "               with and without --porcelain -z" >&2
  echo "  expected:    a listing naming every registered worktree, so the destination" >&2
  echo "               can be checked against it -- without one, membership is unproven" >&2
  echo "               and this script does not stage on an unproven destination." >&2
  exit 1
fi

if [ "$_dst_registered" -ne 1 ]; then
  echo "stage-private-data.sh: REFUSED -- destination is not a REGISTERED worktree of this checkout (nothing was written)" >&2
  echo "  destination: $DST  (resolved: $DST_REAL)" >&2
  echo "  found:       a directory that reports git common dir $SELF_GIT but appears in" >&2
  echo "               no entry of 'git worktree list --porcelain' for it" >&2
  echo "  expected:    the main checkout, or a worktree created with 'git worktree add'" >&2
  echo "  A plain directory carrying a .git gitfile that points at this checkout answers" >&2
  echo "  every other check here exactly like a real worktree -- a copied or restored" >&2
  echo "  worktree directory does it by accident. Only git's own register settles it." >&2
  if [ "$_z_listing" -ne 1 ]; then
    echo "  NOTE: this git does not support 'git worktree list --porcelain -z', so the" >&2
    echo "  register was read line by line. git emits a worktree path RAW, so one" >&2
    echo "  containing a newline is split across lines and cannot be recovered from" >&2
    echo "  that form -- if this destination's path holds a newline it is registered" >&2
    echo "  and was refused anyway. Upgrade git, or move it to a path without one." >&2
  fi
  echo "  Create the destination properly:  git worktree add \"$DST\" -b <branch> origin/main" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# REFUSAL, shared by every check from here down. Defined ahead of anything that
# writes, so "REFUSED" appears in this file only before the first mkdir/cp --
# the ordering analysis/test_stage_private_data.py checks structurally.
# ---------------------------------------------------------------------------
_refuse() {   # $1 = headline, remaining args = detail lines.
  echo "stage-private-data.sh: REFUSED -- $1 (nothing was written)" >&2
  shift
  for _line in "$@"; do echo "  $_line" >&2; done
  exit 1
}

# ---------------------------------------------------------------------------
# DESTINATION IGNORE GUARD (issue #184, adversarial review) -- the guards above
# prove WHICH REPOSITORY the destination belongs to. This one proves the
# property that actually made the incident dangerous: that the paths about to
# be written cannot become committable there.
#
# On 2026-08-13 the archive landed in another project's worktree, and what made
# that dangerous is not only that it was the wrong repository -- it is that THAT
# REPOSITORY DID NOT GITIGNORE private/, so the archive sat one `git add -A`
# from a commit into an unrelated public repo. Until now every mention of
# gitignore in this file was PROSE: comments, and a closing message asserting
# that "nothing was written outside the gitignored tree" without ever asking
# git whether the tree was ignored. Reproduced against this script: a
# registered worktree of a repository with no .gitignore took the whole archive
# at exit 0, and `git status` then offered all nine staged files to `git add`.
#
# REACHABILITY, measured rather than inflated: every commit in THIS
# repository's history ignores private/, and the only file ever tracked beneath
# it is the committed private/README.md placeholder. So no revision reachable
# today gets past this check by needing it. It becomes reachable the moment a
# branch drops that rule or tracks a path under private/ -- and it is the check
# that would have caught the real event from the other side, which is why it is
# here regardless.
#
# WHICH PATHS ARE CHECKED, and why not simply "the three managed directories":
#
#   private/1-raw-data   checked AS A DIRECTORY. Everything this script puts
#   private/verify       there is created by this run, and the names come from
#                        source-side globs and `cp -R` descents, so no fixed
#                        list of leaves would cover them. A directory that
#                        check-ignore reports as ignored settles every path
#                        beneath it at once, including ones that do not exist
#                        yet: git cannot re-include a file whose parent
#                        directory is excluded.
#
#   private/household.yaml   checked AS A FILE, because its parent cannot be
#                        checked as a directory. Measured in this checkout:
#                        `git check-ignore private` exits 1 -- NOT ignored --
#                        precisely because the committed private/README.md is
#                        tracked inside it. private/ is deliberately a
#                        partially-tracked directory (CLAUDE.md's repo map), so
#                        demanding that it be ignored would refuse this
#                        repository's own normal shape. This script writes
#                        exactly one file directly into private/, so that one
#                        file is named.
#
# TWO QUESTIONS, not one, asked in that order. `git check-ignore` happens to
# answer both -- it consults the index, so a TRACKED path reports as
# not-ignored (measured: private/README.md exits 1 here, while the same path
# under --no-index exits 0) -- but that is an implicit behaviour to rest a
# privacy guard on, and the two failures want different messages and different
# remedies: "your .gitignore does not cover this" versus "this path is already
# committed here". So `git ls-files` is asked separately, and FIRST, or the
# tracked case would be reported as the ignore case; see the note in the
# function itself.
#
# FAIL CLOSED on anything that is not a clear "ignored AND untracked".
# check-ignore's status is three-way -- 0 ignored, 1 not ignored, anything else
# means the question was not answered -- and an unanswered question is refused
# rather than read as either verdict.
#
# Asked OF THE DESTINATION (`git -C "$DST_REAL"`, paths relative to its root),
# because the answer comes from the destination's own .gitignore, its
# info/exclude and its index, not from this checkout's -- and asked in the
# sanitized shell above, because an inherited GIT_CONFIG could otherwise supply
# a core.excludesFile that manufactures the "ignored" answer.
# ---------------------------------------------------------------------------
_require_uncommittable() {   # $1 = a path this script writes, relative to $DST_REAL
  local rel=$1 rc=0 tracked
  # TRACKED first, and the order is the whole point. check-ignore consults the
  # index, so a path that is tracked-though-ignored reports rc 1 -- measured on
  # a fixture whose .gitignore holds `private/` and which force-added
  # private/household.yaml and private/verify/usage.csv: check-ignore returned
  # 1 for both, and for the private/verify DIRECTORY around the tracked file,
  # while private/1-raw-data returned 0. Asking check-ignore first would
  # therefore answer "your .gitignore does not cover this" for a file whose
  # .gitignore covers it perfectly well and which is committed anyway, sending
  # the operator to edit the wrong file.
  if ! tracked=$(git -C "$DST_REAL" ls-files -- "$rel"); then
    _refuse "the destination could not be asked which paths it tracks" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $rel" \
      "found:       'git ls-files' failed" \
      "expected:    a listing, so a path that is already COMMITTED there can be" \
      "             told from one that is merely unignored"
  fi
  [ -z "$tracked" ] || _refuse "the destination TRACKS a path this script writes" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "path:        $rel" \
    "tracked:     $(echo $tracked)" \
    "expected:    an untracked path. A tracked file stays in the index whatever" \
    "             .gitignore says, so staging the archive over one puts it" \
    "             straight into the next commit's diff."
  git -C "$DST_REAL" check-ignore -q -- "$rel" || rc=$?
  case "$rc" in
    0) ;;
    1) _refuse "the destination does not gitignore a path this script writes" \
         "destination: $DST  (resolved: $DST_REAL)" \
         "path:        $rel" \
         "found:       'git check-ignore' reports it is NOT ignored there" \
         "expected:    a path that working tree's own git refuses to offer to" \
         "             'git add', so this household's archive cannot be" \
         "             committed from it" \
         "This is the half of the 2026-08-13 incident a repository check cannot" \
         "see: the destination that took the archive did not ignore private/," \
         "which is why the data sat one 'git add -A' from a public commit." \
         "Add the rule to that working tree's .gitignore and re-run." ;;
    *) _refuse "the destination could not say whether it ignores a path this script writes" \
         "destination: $DST  (resolved: $DST_REAL)" \
         "path:        $rel" \
         "found:       'git check-ignore' exited $rc -- neither 'ignored' (0)" \
         "             nor 'not ignored' (1), so the question went unanswered" \
         "expected:    an answerable question. 'Probably ignored' is not a" \
         "             property to write a private archive on." ;;
  esac
}

_require_uncommittable "private/1-raw-data"
_require_uncommittable "private/verify"
_require_uncommittable "private/household.yaml"

# ---------------------------------------------------------------------------
# SOURCE GUARD (issue #184, /review) -- everything the SOURCE has to satisfy,
# decided here, before the first byte, for the same reason every destination
# check is.
#
# These source-side questions used to be asked AFTER six cp invocations had already
# copied household.yaml, the Green Button and SAM globs, gas.csv, the whole of
# electric-bills/ and the billing-history export. So a source whose venv lives
# somewhere else, or a has_gas:true household whose gas-bills/ had simply not
# been pulled yet, exited 1 with a message about the SOURCE -- having already
# written this household's intake file and fifty-odd bill PDFs into the
# destination, with nothing in the output saying the destination was now
# half-staged. Every refusal in this file claims "nothing was written"; on
# those two paths the claim was false.
#
# Hoisting them is possible because EVERY predicate here reads only $SRC: the
# venv is $SRC/.venv/bin/python, gas-bills/ is $SRC/private/1-raw-data/, and
# has_gas comes from $SRC's own household.yaml read through $SRC's own
# interpreter. None consults $DST, so none needed the copies to have happened.
# (household.py resolves its repo root from the CWD before its own __file__ --
# unchanged by the move, since the CWD is the same on both sides of it.)
#
# The invariant this establishes is the whole-script one: EITHER THIS SCRIPT
# WRITES NOTHING, OR IT COMPLETES. Not "the guard block writes nothing".
#
# HAS_GAS is decided here and consumed twice below -- once to scope the
# path scan (a gas-bills/ this run will not write must not be scanned for
# links, or the scan refuses a caller over files it never touches) and once
# for the copy itself.
# ---------------------------------------------------------------------------
if [ ! -x "$SRC/.venv/bin/python" ]; then
  _refuse "the source has no virtualenv interpreter" \
    "source:      $SRC" \
    "path:        $SRC/.venv/bin/python" \
    "found:       no executable there" \
    "expected:    the venv the pipeline runs on, since this script reads the" \
    "             source's household.yaml with it" \
    "Set it up first (CLAUDE.md \"Commands\"):" \
    "  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
fi

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
if ! HAS_GAS=$(PYTHONPATH="$SRC/analysis" "$SRC/.venv/bin/python" -c \
  "import household as hh; print(hh.get('household.has_gas'))"); then
  _refuse "the source's household.yaml could not be read" \
    "source:      $SRC" \
    "path:        $SRC/private/household.yaml" \
    "found:       household.py exited non-zero reading household.has_gas" \
    "expected:    a readable intake file, since which bill directories this" \
    "             script stages depends on it (DATA-SOURCES-CHEATSHEET.md)"
fi
case "$HAS_GAS" in
  True)
    [ -d "$SRC/private/1-raw-data/gas-bills" ] || \
      _refuse "the source's household.has_gas is true but its gas bills are missing" \
        "source:      $SRC" \
        "path:        $SRC/private/1-raw-data/gas-bills" \
        "found:       no such directory" \
        "expected:    the detailed gas statements parse_bills.py requires of a" \
        "             has_gas household -- staging without them produces a" \
        "             destination that fails two steps later, inside the parser"
    ;;
  False)
    [ ! -d "$SRC/private/1-raw-data/gas-bills" ] || \
      _refuse "the source's household.has_gas is false but gas bills are present" \
        "source:      $SRC" \
        "path:        $SRC/private/1-raw-data/gas-bills" \
        "found:       a directory that a has_gas:false household should not have" \
        "expected:    no gas-bills/, or has_gas:true in household.yaml -- this is" \
        "             a real inconsistency in the source, not a staging detail"
    ;;
  *)
    _refuse "the source's household.has_gas is not a boolean" \
      "source:      $SRC" \
      "found:       ${HAS_GAS:-<empty>} from household.py" \
      "expected:    True or False -- see household.example.yaml"
    ;;
esac

# The CAISO day-cache is OPTIONAL, so its absence is not a refusal -- but the
# decision is a source-side one and is taken here, once, so the path scan below
# and the copy further down are driven by the same answer. Scanning a subtree
# this run will not write is precisely the defect this reorganization removes.
STAGE_CAISO=0
if [ -d "$SRC/private/1-raw-data/caiso_raw" ] && \
   ls "$SRC"/private/1-raw-data/caiso_raw/caiso_co2_*.csv >/dev/null 2>&1; then
  STAGE_CAISO=1
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
# HARD LINKS TOO, and they are the quieter half (issue #184, adversarial
# review). `[ -L ]` sees symbolic links only, so an existing output file that
# is a HARD link to a file outside this working tree passes every check above:
# `cp` opens the destination and truncates it, writing THROUGH the shared
# inode, so the outside file's contents become this household's private data
# while nothing here has followed a link and the closing message reports
# containment. Reproduced against this script at exit 0, three times over --
# a hard link planted at private/household.yaml, at private/verify/usage.csv
# and at private/1-raw-data/gas.csv each rewrote a file in an unrelated
# directory with the staged contents, and the run still printed "nothing was
# written outside the gitignored tree".
#
# REFUSE rather than replace atomically. Writing each output to a temp name in
# the same directory and `mv`-ing it over the target would also spare the
# existing inode, but only for the copies this file spells out: `cp -R` creates
# the entire electric-bills/, gas-bills/ and caiso_raw/ subtrees, and a shell
# `cp -R` offers no hook to route each of those files through a rename. That
# fix would be partial while looking total, precisely on the paths the archive
# is bulkiest. Refusing is also the same answer this guard already gives
# symbolic links one paragraph up, and it needs no re-staging semantics (the
# broader stale-destination question is issue #185, deliberately not touched
# here).
#
# THE COST, named so it is a decision and not a surprise: hard-linked backups
# are how this rule is most likely to fire on someone doing nothing wrong.
# `cp -al`, `rsync --link-dest` and the incremental backup tools built on them
# deduplicate by giving one inode a second name, so a private/1-raw-data
# restored from such a backup can arrive with every file at link count 2 --
# and the other name is exactly the outside file this refuses to rewrite, so
# firing there is correct, not collateral. A fresh stage never trips it: `cp`
# without -l always creates a new inode, so nothing this script writes has a
# second name afterwards. The remedy is the message's -- delete and re-run.
#
# SPECIAL FILES TOO, and they are the loudest half (issue #184, adversarial
# review). A FIFO or a device node at an output path is NEITHER a symbolic link
# nor a multiply-linked regular file, so `[ -L ]` says no and `find -links +1`
# never looks at it (`-type f` excludes it) -- it passes both guards above. `cp`
# then opens it, and what happens next is decided by whatever is on the other
# end: on a FIFO the open blocks until some process reads, so the run hangs with
# no message and no exit status, and if something IS reading, this household's
# archive is handed to it -- a process outside the worktree, with every
# containment check here still passing. Reproduced against this script on a
# genuine registered worktree, twice: a FIFO at private/verify/usage.csv and one
# at private/1-raw-data/gas.csv each hung the run indefinitely, and each had
# already written household.yaml and most of the raw archive into the
# destination before blocking -- so "nothing was written" was false as well.
#
# The rule is stated as a POSITIVE shape rather than a list of bad ones: an
# output leaf must be a REGULAR FILE or must not exist yet. That covers FIFOs,
# sockets, block and character devices, and a directory sitting where a file
# belongs (`cp file dir/` would silently deposit the archive one level down),
# without needing to enumerate the special kinds a future filesystem might add.
#
# Two shapes are deliberately NOT refused by it. A path that does not exist is
# the ORDINARY case -- this script creates every one of these -- so absence is
# the normal shape, not a special one. And a DIRECTORY where a directory is
# expected (private/, private/1-raw-data, private/verify) is likewise ordinary;
# those three slots have their own check, `_check_dir_slot`, which has always
# refused a non-directory sitting in a directory's place, so the special-file
# rule is only added to the FILE paths and to the recursive scan.
#
# The three checks therefore describe the SAME SURFACE, which is the property
# worth keeping: the recursive scan under private/1-raw-data (where `cp -R`
# writes names this script never spells out) and the four named leaves are each
# checked for symbolic links, for shared inodes, and for not being a regular
# file. If they ever diverge, the next reader cannot tell which paths are
# protected from what.
#
# HOW the link count is read, because this runs on a developer's Mac and in
# CI: `stat -f %l` is BSD-only and `stat -c %h` is GNU-only, and a script that
# guesses wrong does not fail -- it silently reads nothing and waves the file
# through. `find ... -type f -links +1` is POSIX (`-links` and `-type` both
# are), behaves identically under BSD find and GNU findutils, and needs no
# flavour detection. `-type f` is deliberate: a directory's link count is
# always at least 2 by construction (its own `.` plus each child's `..`), and
# directories cannot be hard-linked on the filesystems this runs on, so
# including them would refuse every destination. The special-file scan is the
# same tool with the complementary test -- `! -type d ! -type f ! -type l` --
# and `find` only lstats what it walks, so nothing here opens a FIFO to find out
# what it is. Neither does `test`: `-p`, `-S`, `-b`, `-c` and `-d` are all POSIX
# and all stat rather than open, which is what makes it safe to ASK whether a
# path is a FIFO on a script whose whole problem is that opening one hangs.
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

_reject_hardlink() {   # $1 = a file `cp` would truncate and write THROUGH
  local shared
  # Scoped by this test rather than by -maxdepth (which is in both find
  # flavours but in neither's POSIX core): a path that is not a plain file
  # cannot be an inode this run rewrites in place.
  [ -f "$1" ] || return 0
  if ! shared=$(find "$1" -type f -links +1 -print); then
    _refuse "a destination file's link count could not be read" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "expected:    a readable file, so its link count can decide whether cp" \
      "             would write through an inode shared with somewhere else"
  fi
  [ -z "$shared" ] || _refuse "a destination file is a HARD link" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "file:        $1" \
    "found:       more than one name for this inode" \
    "expected:    a file with exactly one name inside $DST_REAL" \
    "cp truncates the file it is handed and writes through it, so every other" \
    "name for this inode -- including one outside this working tree -- becomes" \
    "a copy of this household's private data, while every containment check" \
    "here still passes. Nothing on disk says where the other names are." \
    "Delete this file and re-run; this script writes it itself."
}

_reject_special() {   # $1 = a file `cp` would create or overwrite
  local what
  # Absence is the ordinary shape -- this script creates every one of these --
  # and a symbolic link belongs to _reject_link, which runs first and exits, so
  # repeating it here would only relabel that refusal. What is left is a name
  # that exists, is not a link, and is not a regular file.
  if [ ! -e "$1" ] || [ -L "$1" ] || [ -f "$1" ]; then return 0; fi
  if   [ -p "$1" ]; then what="a named pipe (FIFO)"
  elif [ -S "$1" ]; then what="a socket"
  elif [ -b "$1" ]; then what="a block device"
  elif [ -c "$1" ]; then what="a character device"
  elif [ -d "$1" ]; then what="a directory"
  else                   what="neither a regular file nor a directory"
  fi
  _refuse "a destination file is not a regular file" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "path:        $1" \
    "found:       $what" \
    "expected:    a regular file, or a name this script can create" \
    "cp opens whatever it is handed. On a FIFO that open blocks until some" \
    "other process reads, so this run would hang with no message -- and if" \
    "something is reading, this household's archive goes to it instead of to" \
    "a file, while every containment check here still passes." \
    "Remove this and re-run; this script writes the file itself."
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

_reject_special_under() {   # $1 = a directory `cp -R` may write anywhere beneath
  local special
  [ -d "$1" ] || return 0
  if ! special=$(find "$1" ! -type d ! -type f ! -type l -print); then
    _refuse "the destination could not be scanned for special files" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "expected:    a readable directory tree"
  fi
  [ -z "$special" ] || _refuse "the destination contains special file(s)" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "files:       $(echo $special)" \
    "expected:    a tree of ordinary files and directories" \
    "cp -R opens every destination name it lands on. A FIFO here blocks the" \
    "open until something reads, which hangs this run with no message and" \
    "hands the archive to whatever is on the other end; a device node sends" \
    "it somewhere stranger still. Neither is a link, so nothing above sees" \
    "them." \
    "Remove them and re-run; the copies below stage these files for you."
}

_reject_multilinked_under() {   # $1 = a directory `cp -R` may write anywhere beneath
  local shared
  [ -d "$1" ] || return 0
  if ! shared=$(find "$1" -type f -links +1 -print); then
    _refuse "the destination could not be scanned for hard links" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "expected:    a readable directory tree"
  fi
  [ -z "$shared" ] || _refuse "the destination contains hard-linked file(s)" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "files:       $(echo $shared)" \
    "expected:    a tree whose files each have exactly one name" \
    "cp -R truncates every file it lands on and writes through it, so a name" \
    "shared with a file outside this working tree makes that file a copy of" \
    "this household's private data -- with no link for the checks above to" \
    "see and nothing on disk saying where the other name is." \
    "Delete them and re-run; the copies below stage these files for you."
}

# Every path is CHECKED first and only then created, in two passes rather than
# one interleaved walk: a refusal on private/verify must not leave behind the
# private/1-raw-data a single pass would already have made. "Nothing was
# written" then means nothing at all, including empty directories.
_check_dir_slot "$DST_REAL/private"
_check_dir_slot "$DST_REAL/private/1-raw-data"
_check_dir_slot "$DST_REAL/private/verify"

# WHAT THE THREE SCANS COVER, and why it is not all of private/1-raw-data
# (issue #184, /review).
#
# The recursive scans used to walk the whole of private/1-raw-data. `cp -R`
# writes only three subtrees beneath it -- electric-bills/, gas-bills/ and
# caiso_raw/ -- plus the named and glob-named files at its top level. So a
# destination whose dsgs_events/ or sdge_nbt_export_rates/ had come back from a
# `cp -al` or `rsync --link-dest` backup (both deduplicate by giving one inode a
# second name) was refused with "the destination contains hard-linked file(s)"
# for files `cp` would never open -- and the remedy the message gave, delete
# them and re-run, was WRONG, because this script does not stage those two
# directories back. Deleting them on that advice loses them. A refusal a correct
# caller cannot act on is the shape that gets guards disabled; both directories
# are documented as never staged at the top of this file, and now the scans
# agree with that documentation instead of contradicting it.
#
# So each scanned subtree gets the full three-check treatment -- and the
# subtree's own slot is checked first, because `[ -d ]` is TRUE for a symbolic
# link to a directory: without _check_dir_slot on the subtree itself, a link at
# private/1-raw-data/electric-bills would be walked THROUGH rather than refused,
# and a FIFO there would be skipped by all three scans.
#
# The top-level leaves are named instead of scanned, and the two glob copies
# contribute their destination names from the SOURCE basenames -- which is
# exactly what `cp` will write, so the list is complete by construction rather
# than by hand. A pattern that matches nothing in the source stays literal and
# simply matches no destination path, which every check below treats as absent.
#
# private/verify is still NOT scanned in full: this script writes exactly three
# named files there, while the sandbox around them legitimately holds a venv
# whose bin/ entries are symlinks -- scanning it would refuse a normal re-stage
# over a working sandbox for links this script never touches. Same principle,
# applied consistently now.
#
# The hard-link scan and the special-file scan cover exactly the same ground as
# the symbolic-link one, for the same reason: those are the paths `cp` and
# `cp -R` write, and WHAT sits at one of them changes how the archive escapes,
# not whether it can -- a symbolic link redirects the write, a second name on
# the inode rewrites a file elsewhere in place, and a FIFO or device node hands
# the bytes to a process. Three checks, one surface; keep them in step.
# The list is built from the SAME two conditions the copies below use -- the
# validated HAS_GAS, and STAGE_CAISO decided in the source guard -- so a subtree
# this run will not write is not scanned for links it will never follow. A
# gas-bills/ left in a has_gas:false destination is stale, not a hazard.
_scanned_subtrees="electric-bills"
[ "$HAS_GAS" != True ] || _scanned_subtrees="$_scanned_subtrees gas-bills"
[ "$STAGE_CAISO" != 1 ] || _scanned_subtrees="$_scanned_subtrees caiso_raw"
for _sub in $_scanned_subtrees; do
  _check_dir_slot "$DST_REAL/private/1-raw-data/$_sub"
  _reject_links_under "$DST_REAL/private/1-raw-data/$_sub"
  _reject_special_under "$DST_REAL/private/1-raw-data/$_sub"
  _reject_multilinked_under "$DST_REAL/private/1-raw-data/$_sub"
done

_dst_leaves=("$DST_REAL/private/household.yaml"
             "$DST_REAL/private/verify/usage.csv"
             "$DST_REAL/private/verify/samA.csv"
             "$DST_REAL/private/verify/samB.csv"
             "$DST_REAL/private/1-raw-data/gas.csv"
             "$DST_REAL/private/1-raw-data/electric_billing_history_2024-2026.csv")
for _srcfile in "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv \
                "$SRC"/private/1-raw-data/enphase_sam8760_*.csv; do
  [ -e "$_srcfile" ] || continue
  _dst_leaves[${#_dst_leaves[@]}]="$DST_REAL/private/1-raw-data/$(basename -- "$_srcfile")"
done
for _leaf in "${_dst_leaves[@]}"; do
  _reject_link "$_leaf"
  _reject_special "$_leaf"
  _reject_hardlink "$_leaf"
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

# The gas statements, on the flag the SOURCE GUARD above already validated --
# both directions of it, so by here this is a copy and not a decision.
if [ "$HAS_GAS" = True ]; then
  cp -R "$SRC/private/1-raw-data/gas-bills" "$DST_REAL/private/1-raw-data/"
fi

cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$DST_REAL/private/verify/usage.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$DST_REAL/private/verify/samA.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$DST_REAL/private/verify/samB.csv"

if [ "$STAGE_CAISO" = 1 ]; then
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
echo "  (that tree's own git reported every path written here ignored and untracked"
echo "   before the first byte -- see DESTINATION IGNORE GUARD)"
