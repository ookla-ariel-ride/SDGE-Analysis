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
#   GIT_LITERAL_PATHSPECS             (issue #194) not identity variables at
#   GIT_NOGLOB_PATHSPECS              all -- they change how git READS the
#   GIT_GLOB_PATHSPECS                paths this script hands it, which is the
#   GIT_ICASE_PATHSPECS               same lever one step over: the guard still
#                                     asks the right repository, about a
#                                     DIFFERENT path than the one named, or
#                                     about no path at all. Measured on git
#                                     2.50.1 in this checkout, each set alone:
#
#                                       ls-files -- './data/*.json'
#                                         plain    -> 3 tracked files
#                                         LITERAL  -> 0
#                                         NOGLOB   -> 0
#                                         GLOB     -> 3   (unchanged)
#                                         ICASE    -> 3   (unchanged)
#                                       check-ignore -q -- './private/foo'
#                                         plain    -> 0   (ignored)
#                                         LITERAL  -> 128 fatal: pathspec magic
#                                         NOGLOB   -> 128        not supported
#                                         GLOB     -> 128        by this command
#                                         ICASE    -> 128
#                                       ls-files -- './data/leak.json'
#                                         plain    -> nothing
#                                         ICASE    -> data/LEAK.json
#
#                                     So ALL FOUR were measured and all four
#                                     move an answer -- none is here on the
#                                     strength of the family name. NOGLOB and
#                                     LITERAL empty a glob-backed listing;
#                                     every one of the four turns check-ignore
#                                     fatal, which this script fails closed on
#                                     (rc 128 -> REFUSED) but as a denial of
#                                     service: no destination can be validated
#                                     at all, and a guard that refuses correct
#                                     callers is the kind that gets switched
#                                     off. ICASE additionally makes ls-files
#                                     answer about a differently-cased path,
#                                     naming a file the caller never asked
#                                     about in the refusal
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
# Announced SEPARATELY by kind, because the three reasons are different and a
# message that gave CDPATH the git-variables explanation would be telling the
# operator something untrue about their own environment.
# ---------------------------------------------------------------------------
_cleared=""
_cleared_path=""
_cleared_spec=""
for _v in CDPATH GIT_DIR GIT_COMMON_DIR GIT_WORK_TREE GIT_CEILING_DIRECTORIES \
          GIT_DISCOVERY_ACROSS_FILESYSTEM GIT_OBJECT_DIRECTORY \
          GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE GIT_NAMESPACE \
          GIT_LITERAL_PATHSPECS GIT_NOGLOB_PATHSPECS GIT_GLOB_PATHSPECS \
          GIT_ICASE_PATHSPECS \
          ${!GIT_CONFIG*}; do
  if [ -n "${!_v+set}" ]; then
    case "$_v" in
      CDPATH)             _cleared_path="$_cleared_path $_v" ;;
      GIT_*_PATHSPECS)    _cleared_spec="$_cleared_spec $_v" ;;
      *)                  _cleared="$_cleared $_v" ;;
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
if [ -n "$_cleared_spec" ]; then
  echo "stage-private-data.sh: ignoring inherited pathspec variable(s):$_cleared_spec" >&2
  echo "  These change how git READS the paths below, not which repository answers: a" >&2
  echo "  glob stops matching, or check-ignore refuses every path outright, so the checks" >&2
  echo "  would evaluate less than they say they do. Paths are read as written." >&2
fi

# ---------------------------------------------------------------------------
# CONFIGURATION ISOLATION (issue #193) -- the same rule from the other side.
#
# Clearing the inherited GIT_CONFIG* family above stops a caller REPLACING the
# configuration. It does nothing about the configuration that is simply THERE:
# `core.excludesFile` in $HOME/.gitconfig, in $XDG_CONFIG_HOME/git/config or in
# the system gitconfig is a supported, ordinary git mechanism, and a path the
# destination's own .gitignore does not cover can answer "ignored" because of
# it. Measured on git 2.50.1, on a fixture repository whose .gitignore holds
# `private/` and which does NOT ignore data/leak.json:
#
#   check-ignore -q -- './data/leak.json', excludesFile unset      -> 1
#   ... with a $HOME/.gitconfig naming an excludes file listing it  -> 0
#   ... with $XDG_CONFIG_HOME/git/config naming one                 -> 0
#   ... with $HOME/.config/git/ignore listing it (NO config key)    -> 0
#   ... with core.excludesFile set in the repo's own .git/config    -> 0
#
# and this script accepted the destination in every one of those. That is not
# primarily a forgery -- it is an ordinary-configuration correctness problem: a
# contributor with a global excludes file gets a different verdict about the
# same repository, and the verdict this guard needs is the one the REPOSITORY
# gives, because private data must be uncommittable for everyone rather than
# for whoever happened to run the script.
#
# HOME IS NOT CLEARED, and that is deliberate. HOME supplies a legitimate input
# to the real repository's answer rather than replacing which repository
# answers, and clearing it would break the ssh/credential machinery of any git
# command a later edit adds. The narrower instrument is to switch off the
# ambient configuration for git only -- and that is done TWICE, by two
# mechanisms of very different ages, because one of them is silently inert on an
# older git and the other is not.
#
# WHAT EACH MECHANISM IS, and when git learned it. Pinned by archaeology in
# git.git rather than by recall: each version below is the first release whose
# tag CONTAINS the introducing commit and whose predecessor does not.
#
#   -c core.excludesFile=/dev/null   git 1.7.2, Jul 2010 (commit 8b1fa778). A
#                                    top-level option, passed on EVERY git
#                                    command below by the _git wrapper. Highest
#                                    precedence there is: git-config(1) SCOPES
#                                    orders system < global < local < worktree <
#                                    GIT_CONFIG_COUNT/KEY/VALUE < -c, and says of
#                                    the variables that they "will be overridden
#                                    by any explicit options passed via git -c".
#                                    Measured here too: with both set to
#                                    different values, `config --show-origin
#                                    --get core.excludesFile` reports "command
#                                    line: <the -c value>"
#   GIT_CONFIG_NOSYSTEM=1            git 1.5.5, Apr 2008 (commit ab88c363;
#                                    undocumented until 1.8.1.1). Suppresses the
#                                    SYSTEM gitconfig only; says nothing about
#                                    global
#   GIT_CONFIG_COUNT / _KEY_<n> /    git 2.31, Mar 2021 (commit d8d77153)
#     _VALUE_<n>
#   GIT_CONFIG_GLOBAL / _SYSTEM      git 2.32, Jun 2021 (commit 4179b489)
#
# WHY BOTH, and why the -c is the load-bearing one. An older git does not reject
# a variable it does not know -- the name appears nowhere in its config.c, so
# there is no getenv to fail and nothing to report -- and git documents no rule
# either way, so this is an implementation fact and is treated as one: it is
# MEASURED, with a PATH shim whose `git` removes GIT_CONFIG_GLOBAL,
# GIT_CONFIG_SYSTEM, GIT_CONFIG_COUNT, GIT_CONFIG_KEY_0 and GIT_CONFIG_VALUE_0
# from the environment and then execs the real git. A process that never reads a
# variable and a process that never receives it cannot be told apart, so that is
# the old git exactly, not an approximation of one (`check-ignore -q --
# ./data/leak.json`; 0 = "ignored", the answer that lets a committable
# destination through):
#
#                                             no        the 6       -c
#   ambient route                        isolation  variables  excludesFile
#   $HOME/.gitconfig core.excludesFile           0          0          1
#   $XDG_CONFIG_HOME/git/config the same         0          0          1
#   $HOME/.config/git/ignore (no config key)     0          0          1
#   $HOME/.gitconfig include.path -> the key     0          0          1
#   $HOME/.gitconfig includeIf gitdir: -> it     0          0          1
#
# The middle column is the hole: on such a git every ambient route is still open
# and this script would stage the archive into a destination whose own .gitignore
# does not cover private/. The right-hand column is the same run with the -c,
# and it closes all five -- including the two INCLUDE routes, which matter
# because an include is how a global config reaches a key without naming it, and
# because precedence, not file-reading, is what -c wins on.
#
# The six variables are kept anyway, and they are not decoration: on a git that
# reads them they switch the ambient configuration off WHOLESALE rather than
# key by key, which covers the next core.* key somebody finds a use for. What
# they may no longer do is carry the property on their own.
#
# The value forced into core.excludesFile is /dev/null in both mechanisms: an
# empty excludes file, not an absent setting. It also overrides a
# core.excludesFile set in the destination's own .git/config, which is per-clone
# local configuration and no more part of the repository's shared ignore rules
# than the operator's ~/.gitconfig is (measured: local .git/config manufactures
# the "ignored" answer without either mechanism, and does not with either).
#
# WHAT SURVIVES, checked rather than hoped: the destination's own tracked
# .gitignore files and its .git/info/exclude, which is exactly the answer this
# guard wants. Measured under every column of the table above -- './private/foo'
# (covered by the fixture's .gitignore) still exits 0, and a path listed in
# .git/info/exclude still exits 0. The isolation must not throw away the answer
# it is protecting.
#
# WHAT ELSE COULD REACH THE VERDICT. The excludes file is not the only ambient
# input, and the sweep that said it was had a hole in its METHOD: it asked
# fixtures `git init` had just built, and git init writes the very keys it was
# testing into .git/config, where they outrank global. A local value that is
# PRESENT masks the ambient one. Re-run with each key's local value REMOVED --
# the state of any repository whose config was written on a case-sensitive
# filesystem, or by hand -- two keys move a verdict this script acts on, both in
# the ADMITTING direction. Fixture: .gitignore holds `Private/` and `café/`
# (NFC); the questions are './private/leak.json' and './café/leak.json' (NFD);
# old-git shim, -c core.excludesFile already in place:
#
#   ambient key (global)           local present     local removed
#   core.ignoreCase = true         1  not ignored    0  IGNORED
#   core.precomposeUnicode = true  1  not ignored    0  IGNORED
#
# Neither is an excludes file: they decide how the destination's OWN patterns
# match, so an ambient one widens the matching until a rule the destination does
# not have covers the path anyway. All five ambient routes deliver them.
#
# THEY ARE NOT FORCED OFF, they are forced FROM THE DESTINATION -- see
# _adopt_destination_config below. Forcing core.ignoreCase=false outright closes
# the hole and breaks correct callers: measured on a repository whose own
# .git/config says ignorecase=true (what git init writes on macOS and Windows)
# with .gitignore `private/`, './Private/leak.json' answers 0 unforced and 1
# forced-false -- the guard refusing a destination whose git really would refuse
# the path. A guard that refuses ordinary correct callers is one that gets
# switched off.
#
# WHAT WAS TESTED AND IS INERT, each with its own local value removed:
# core.worktree, core.symlinks, core.fileMode, core.quotePath,
# core.attributesFile, core.hooksPath, core.fsmonitor, core.sparseCheckout,
# core.protectHFS, core.autocrlf, core.longpaths, core.checkStat,
# core.untrackedCache, index.sparse, status.showUntrackedFiles,
# and aliases named after the commands run here (git does not let an alias
# shadow a builtin). safe.directory is inert IN THIS DIRECTION and is not inert
# in the other one -- see "WHAT THE ISOLATION MUST NOT TAKE AWAY" below, which
# is the question this list does not ask. core.bare needs its own sentence: an ambient bare=true does
# make `git worktree list` report a MAIN checkout as bare, which the register
# parse drops -- but only when the listing is made from inside a working tree.
# This script lists from $SELF_GIT, the common git dir, where bareness is
# decided by the cwd and by the repository's own core.bare (git init always
# writes it false), not by the ambient value: measured inert in that call shape.
# Refusing-direction in either case, so it is left alone.
#
# The remedy for a REFUSAL this causes is local and inside the destination: put
# the pattern in that working tree's .gitignore or .git/info/exclude, spelled as
# the path is spelled -- or state the matching the destination really wants,
# `git config core.ignoreCase true`, which is what git init writes on a
# case-insensitive filesystem. That is the property that separates all of this
# from clearing HOME, whose refusals the operator could not fix by editing
# anything in the repository.
#
# WHAT THE ISOLATION MUST NOT TAKE AWAY (issue #193, /review round three). The
# sweep above asks whether an ambient value MOVES a verdict in the admitting
# direction. The opposite question -- does switching the ambient configuration
# off REMOVE a value the probes NEED -- has its own answer, and it is
# `safe.directory`.
#
# git refuses to work in a repository owned by another user at all ("fatal:
# detected dubious ownership"), and the one way an operator lifts that is a
# safe.directory entry, which git honours ONLY from protected configuration
# (system, global, command line) -- precisely the scopes the three variables
# above empty. A worktree on an SMB or NFS share, in a container bind-mount, or
# created under sudo, that the operator declared safe long ago and uses every
# day, became unanswerable HERE and nowhere else. Measured on git 2.50.1 with
# the ownership check driven by GIT_TEST_ASSUME_DIFFERENT_OWNER=1 and
# safe.directory in ~/.gitconfig, on every probe this script runs:
#
#                                  ambient config   this script's isolation
#   rev-parse --git-common-dir          0                128  fatal
#   rev-parse --show-toplevel           0                128  fatal
#   worktree list --porcelain           0                128  fatal
#   ls-files -- <path>                  0                128  fatal
#   check-ignore -q -- <path>           0                128  fatal
#
# and the refusal that came out named none of it: _common_git_dir discards the
# fatal, so the run died with "REFUSED -- this script is not inside a git
# working tree" and a `git worktree add` remedy that cannot fix an ownership
# problem. A guard that refuses correct callers is one that gets switched off --
# this file's own argument, applied to itself.
#
# THE SWEEP in that second direction: 29 ambient keys, each set in the
# operator's global config and read by all five probes above with the isolation
# on and off. safe.directory is the ONLY one that turns an answer into a
# failure. protocol.file.allow=never moves them the other way (every probe fatal
# WITHOUT the isolation, answered with it), and the other 27 -- including
# safe.bareRepository and uploadpack.packObjectsHook, the only other two keys
# git reads from protected configuration alone -- are inert or admitting, which
# the paragraphs above already settle.
#
# THE REPAIR is _load_ambient_protected_config below, re-injected with -c, and
# it is narrow in four ways that together are why handing a protected-config key
# back does not undo the isolation: it happens only AFTER git has refused to
# answer at all (exit 128, and _git retries once), so it cannot change an answer
# git did give; safe.directory decides WHETHER git reads a repository, not what
# that repository's rules are (core.excludesFile) or how they match
# (core.ignoreCase, core.precomposeUnicode), so it reaches no verdict here; the
# values are taken from the `system` and `global` scopes ONLY, filtered on git's
# own `config --show-scope`, so a safe.directory in the DESTINATION's own config
# -- the one a forged destination could write -- is dropped rather than promoted
# to the command line, which is the attack git's protected-configuration rule
# exists to stop; and they are replayed RAW and in order, so `~/x`,
# `%(prefix)/x`, `*` and the empty reset entry keep their meaning and a
# directory nobody declared stays refused, now with git's own message and the
# working remedy it carries.
#
# AND IT IS PROVED, not assumed, once per run before the first write: see
# _require_isolation_proven below, which walks the keys THIS SCRIPT forces --
# not the re-injected one, whose value is the operator's and which there is
# nothing to prove about. Version numbers are what this comment can offer; what
# the guard acts on is the git in front of it.
#
# EXPORTED into this shell, like the clearing above and for the same reason:
# the verdict and everything it authorizes run in one environment. SET rather
# than merely cleared, so it is announced unconditionally -- it always applies,
# so a run that said nothing would be the silence the block above exists to
# fix.
# ---------------------------------------------------------------------------
_forced=""
for _kv in GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
           GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_COUNT=1 \
           GIT_CONFIG_KEY_0=core.excludesFile GIT_CONFIG_VALUE_0=/dev/null; do
  export "$_kv"
  _forced="$_forced ${_kv%%=*}"
done

# The keys this script FORCES, in ONE place. The wrapper below passes them and
# _require_isolation_proven reads back exactly this list, so a key added here is
# a key the proof checks -- which is the property the first version of that
# proof did not have: it restated `core.excludesFile=/dev/null` as a literal of
# its own, and a third forced key would have been passed and never verified,
# with the proof still passing. Nothing else in this file may name a forced -c.
_GIT_FORCED_CONFIG=(-c core.excludesFile=/dev/null)

# The destination-derived half of the -c list: the keys taken FROM the
# destination rather than switched off. EMPTY until _adopt_destination_config
# has read what the destination says, which is the same shape private_egress.py
# has -- its _git() defaults to the forced table alone and the two probes whose
# answer these keys move are handed the fuller list explicitly.
#
# It used to be seeded with git's own defaults, under a comment saying that a
# probe running before the adoption was hypothetical ("none does today"). SIX
# run: the two `rev-parse --git-common-dir` attempts in _common_git_dir and the
# third _git_error_at makes on the refusal path, `rev-parse --show-toplevel`,
# and both `worktree list --porcelain` listings. All six were carrying an
# override the python side does not put on the same probes, and the seed was
# measured inert for every one of them -- so the comment was false about the
# only thing it claimed. An empty array is the honest version of "inert", and it
# closes the divergence rather than documenting it.
_GIT_DEST_CONFIG=()

# ---------------------------------------------------------------------------
# THE ALIAS OVERRIDE (issue #204) -- the one value this script forces AGAINST
# what the destination says, and only for the second tracked question.
#
# `git ls-files -- <pathspec>` matches index entries BY THE BYTES, and on the
# filesystem this script runs on that is the wrong equivalence. Reproduced on
# macOS (APFS, git 2.50.1) in a scratch repository whose .gitignore holds
# `private/` and whose index holds `Private/household.yaml`:
#
#   write to private/household.yaml
#     git status                                    ->  M Private/household.yaml
#   git ls-files -- ./private/household.yaml        ->  nothing ("not tracked")
#   git check-ignore -q -- ./private/household.yaml ->  0       ("ignored")
#
# Both of the questions below answer in the ADMITTING direction while the write
# lands on a committed file.
#
# NOT core.ignoreCase, which is the plausible fix and is measurably not the
# mechanism: unforced, false and true were measured on that fixture and all
# three report the path as untracked, because core.ignoreCase governs how git
# matches working-tree paths against the index during status and checkout, not
# how a pathspec resolves against index entries.
#
# What does work, measured on the same fixture: the ':(icase)' pathspec magic
# for the case half, and core.precomposeUnicode=true for the unicode half (an
# index holding the NFC spelling answers an NFD pathspec only under it). Both
# folds are GIT'S OWN, which is what lets private_egress.py fold identically
# without a locale casefold or a unicode library this shell has no equivalent
# for.
#
# APPENDED to the -c list rather than replacing an entry in it: the last -c for
# a key is the one git uses, so this states the single value it changes and the
# adopted list stays the single statement of the rest. _require_isolation_proven
# reads it back under this array, not under the adopted one, or the readback
# would confirm the destination's value and prove nothing about the value the
# alias probe really runs with.
#
# WHAT THIS DOES NOT COVER, corrected here rather than left standing (issues
# #223, #224). Two sentences #204 left behind were wrong:
#
#   * "':(icase)' folds case." It folds ASCII case. Measured side by side in one
#     repository holding both spellings, on a filesystem that resolves each pair
#     to one file:
#         ls-files -- ':(icase)./private/household.yaml' -> private/HOUSEHOLD.yaml
#         ls-files -- ':(icase)./private/househöld.yaml' -> (nothing)
#     core.precomposeUnicode above is a different axis (composition, not case)
#     and closes none of it.
#   * "#193/#194 closed the ignore-side version of this." They closed the
#     CONFIGURATION half: an ambient core.ignoreCase or core.precomposeUnicode
#     can no longer widen the destination's own rules. The PATH half was still
#     open -- an untracked directory on disk under another case spelling made
#     check-ignore answer "ignored" about a path the write never reached -- and
#     `git check-ignore` takes no pathspec magic at all, so this remedy could not
#     be carried over to it.
#
# Both are closed FOR A PATH THAT EXISTS ON DISK by _ondisk_spelling below, which
# resolves the path against the filesystem before any question is asked about it.
# The ':(icase)' probe is kept ALONGSIDE it, not replaced: an index entry with no
# file in the working tree has no on-disk spelling for that walk to find, so only
# the pathspec fold sees it.
#
# AND THE TWO USED TO MISS TOGETHER in one combination: a tracked index entry
# with NO working-tree file that differs from the path only in NON-ASCII case --
# ':(icase)' folds ASCII only, and the walk has nothing to resolve for a leaf
# that does not exist. That one is answered by a THIRD question rather than by
# either of these (issue #230): _require_uncommittable enumerates the
# destination's index with `ls-files -z` and compares each entry against the
# candidate component by component, under the generated fold of _case_fold and the
# per-component answers in _FOLDS_VEC. The pair above still does the work
# wherever a name EXISTS on disk, where the filesystem itself is the oracle;
# the enumeration is what answers for a name that does not.
_GIT_ALIAS_OVERRIDE=(-c core.precomposeUnicode=true)

# EMPTY except while an alias question is being asked. _git splices it after
# _GIT_DEST_CONFIG, so an ordinary probe carries exactly what it carried before.
_GIT_ALIAS_CONFIG=()

# The operator's own protected-scope values, handed back only where git refused
# to answer -- see "WHAT THE ISOLATION MUST NOT TAKE AWAY" above. Empty until a
# fatal makes _git ask for them.
_GIT_AMBIENT_CONFIG=()

# EVERY git invocation in this script goes through here, and none goes round it
# (asserted by test_stage_private_data.case_every_git_invocation_carries_the_
# configuration_override, which reads this file). A wrapper rather than the
# option repeated at seven call sites, for the reason the clearing above is done
# once in this shell: a probe a later edit adds inherits the isolation instead of
# having to remember it.
#
# `${a[@]+"${a[@]}"}` rather than `"${a[@]}"`: under `set -u`, bash 3.2 -- what
# macOS ships, and what this script runs on -- treats an EMPTY array's expansion
# as an unbound variable and aborts.
#
# THE RETRY is the second half of the ambient-safe.directory repair. `|| rc=$?`
# rather than a bare call, because `set -e` would otherwise abort the process
# substitution the register listing runs in before the retry could happen. Only
# 128, git's "I refused to do this at all": check-ignore's 1 and `config --get`'s
# 1 are ANSWERS and are never retried. On a fatal git writes its message to
# stderr and nothing to stdout, so the second attempt's output is the only
# output any caller parses (measured for all six probe shapes).
_git() {
  local rc=0 where="."
  [ "${1:-}" != "-C" ] || where=${2:-.}
  command git "${_GIT_FORCED_CONFIG[@]}" ${_GIT_DEST_CONFIG[@]+"${_GIT_DEST_CONFIG[@]}"} \
    ${_GIT_ALIAS_CONFIG[@]+"${_GIT_ALIAS_CONFIG[@]}"} "$@" || rc=$?
  if [ "$rc" -eq 128 ]; then
    _load_ambient_protected_config "$where"
    if [ ${#_GIT_AMBIENT_CONFIG[@]} -gt 0 ]; then
      rc=0
      command git "${_GIT_FORCED_CONFIG[@]}" \
        ${_GIT_DEST_CONFIG[@]+"${_GIT_DEST_CONFIG[@]}"} \
        ${_GIT_ALIAS_CONFIG[@]+"${_GIT_ALIAS_CONFIG[@]}"} \
        "${_GIT_AMBIENT_CONFIG[@]}" "$@" || rc=$?
    fi
  fi
  return "$rc"
}

# Read safe.directory from the operator's SYSTEM and GLOBAL configuration and
# nowhere else, as -c options.
#
# The scope filter is the safety argument, not a detail: it is git's own rule
# for this key, so a value in the DESTINATION's local or per-worktree config --
# the one a forged destination could write for itself -- is dropped here instead
# of being promoted to the command line, where git would honour it.
#
# `--show-scope` on an effective read, not `config --global`: measured on git
# 2.50.1, `config --global --get-all safe.directory` reports NOTHING for a value
# the same git reads and applies, because that spelling reads ~/.gitconfig alone
# -- not $XDG_CONFIG_HOME/git/config and not an include from either. The
# effective read with the scope printed beside each value returns both. A git
# without --show-scope (2.26, Mar 2020) prints nothing and nothing is
# re-injected, which is right rather than merely safe: safe.directory arrived in
# 2.35.2 (Mar 2022), so that git has no ownership refusal to lift.
#
# -z, and a NUL-delimited read, because a config value may contain a newline.
# The subshell unsets the isolation for this one read -- it has to see the files
# the isolation empties -- and unsets the whole inherited GIT_CONFIG* family with
# it, so an environment cannot supply a safe.directory that this read would then
# hand to the command line.
_load_ambient_protected_config() {
  local scope value
  _GIT_AMBIENT_CONFIG=()
  while IFS= read -r -d '' scope && IFS= read -r -d '' value; do
    case "$scope" in
      system|global) _GIT_AMBIENT_CONFIG+=(-c "safe.directory=$value") ;;
    esac
  done < <(unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM \
                 GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0
           command git -C "$1" config --show-scope -z --get-all safe.directory \
             2>/dev/null || true)
}

# Read those keys from the DESTINATION's own repository configuration and force
# what it says. Called from _require_isolation_proven, so every path is asked
# with a freshly read value and the proof below reads back the same list.
#
# --worktree then --local: git's own precedence order for the two scopes that
# live inside the repository, highest first. Both, because --local does not see
# a per-worktree value (measured on a repo with extensions.worktreeConfig and
# core.ignoreCase=true in config.worktree: `config --local --get` exits 1 while
# check-ignore behaves as true), and --worktree alone is fatal on a repository
# with several working trees and no such extension, which is the ordinary case.
# Neither scope can be reached from the environment or from an ambient config
# file, so the read cannot be contaminated by what it is about to override --
# measured with core.ignoreCase=true in $HOME/.gitconfig and -c
# core.ignoreCase=false on the command line: `config --local --bool --get` still
# printed the destination's own value. A scope is not optional here: an
# effective `config --get` reads the -c the wrapper has already added, so the
# read would confirm the value it is about to force and the destination would
# never be consulted at all (measured while testing exactly that mistake).
#
# --bool for the SPELLING: git accepts yes/on/1 and a valueless key, and the
# proof compares strings. A value git cannot read as a boolean falls back to the
# default here and costs nothing -- it makes every other git command in that
# repository fatal, so the probes refuse it as unanswerable.
_adopt_destination_config() {
  local key scope value opts=()
  for key in core.ignoreCase core.precomposeUnicode; do
    value=false
    for scope in --worktree --local; do
      if value=$(_git -C "$DST_REAL" config "$scope" --bool --get "$key" 2>/dev/null) \
         && [ -n "$value" ]; then
        break
      fi
      value=false
    done
    opts+=(-c "$key=$value")
  done
  _GIT_DEST_CONFIG=("${opts[@]}")
}

echo "stage-private-data.sh: asking git from repository-local configuration only" >&2
echo "  every git command below carries -c core.excludesFile=/dev/null; every one" >&2
echo "  that asks ABOUT the destination also carries core.ignoreCase and" >&2
echo "  core.precomposeUnicode as that repository's own configuration states them," >&2
echo "  and these are exported:$_forced" >&2
echo "  So a core.excludesFile in global, XDG or system config -- and git's default" >&2
echo "  global ignore file, which is a hardcoded path rather than a config setting --" >&2
echo "  cannot decide what this run treats as ignored, and neither can an ambient" >&2
echo "  core.ignoreCase or core.precomposeUnicode widen the destination's own rules;" >&2
echo "  the destination's own .gitignore and .git/info/exclude do, read with the" >&2
echo "  matching that repository itself asks for. The -c is what makes that" >&2
echo "  version-independent: all" >&2
echo "  but GIT_CONFIG_NOSYSTEM are read by git 2.31 and newer only, and an older git" >&2
echo "  ignores them in silence, while 'git -c' has worked since 1.7.2. Not assumed --" >&2
echo "  verified against the git in front of us before anything is written." >&2
echo "  One thing is NOT switched off: if git refuses a repository outright as" >&2
echo "  dubiously owned, that one command is retried with the safe.directory entries" >&2
echo "  your system and global config already state -- the only scopes git reads them" >&2
echo "  from, and the ones the isolation above would otherwise delete." >&2

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
  if ! out=$(_git -C "$d" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
    # git < 2.31 has no --path-format; its output is relative to $d (git -C
    # already chdir'd there), so normalize rather than compare it raw.
    out=$(_git -C "$d" rev-parse --git-common-dir 2>/dev/null) || return 1
  fi
  [ -n "$out" ] || return 1
  case "$out" in /*) ;; *) out="$d/$out" ;; esac
  _physical "$out"
}

# What git says about a directory it will not answer for -- stdout discarded,
# stderr kept -- or nothing when it answers fine.
#
# A swallowed fatal reads exactly like "there is no repository here", and both
# refusals below turn a failure of _common_git_dir into that sentence, with a
# `git worktree add` remedy: right for a missing worktree, no remedy at all for
# the two failures that are not about one -- dubious ownership, and a repository
# whose configuration or object store git cannot read. git's own message says
# which it is, and for ownership it carries the exact `git config --global --add
# safe.directory ...` command, so it is quoted verbatim rather than paraphrased.
#
# Asked HERE rather than recorded inside _common_git_dir, and that is not a
# style choice: every caller of that function reads it through `$(...)`, which
# is a subshell, so a variable it set there would be gone by the time the
# refusal printed it (measured -- the first version of this printed nothing).
# private_egress.py returns the two together instead, since a python function
# can; the two implementations agree on the message, not on the plumbing.
_git_error_at() {
  [ -d "$1" ] || return 0
  _git -C "$1" rev-parse --git-common-dir 2>&1 >/dev/null || true
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
  SELF_ERR=$(_git_error_at "$SELF_DIR")
  [ -z "$SELF_ERR" ] || echo "  git said:    $SELF_ERR" >&2
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
  DST_ERR=$(_git_error_at "$DST_REAL")
  [ -z "$DST_ERR" ] || echo "  git said:    $DST_ERR" >&2
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
if ! DST_TOP_RAW=$(_git -C "$DST_REAL" rev-parse --show-toplevel 2>/dev/null) \
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
done < <(_git -C "$SELF_GIT" worktree list --porcelain -z 2>/dev/null)
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
  if DST_WORKTREES=$(_git -C "$SELF_GIT" worktree list --porcelain 2>/dev/null) \
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
# Asked OF THE DESTINATION (`_git -C "$DST_REAL"`, paths relative to its root),
# because the answer comes from the destination's own .gitignore, its
# info/exclude and its index, not from this checkout's -- and asked in the
# sanitized shell above, because an inherited GIT_CONFIG could otherwise supply
# a core.excludesFile that manufactures the "ignored" answer, an ambient one
# (global, XDG or system config, or the default global ignore file) could
# manufacture it without anybody having forged anything, and an ambient
# core.ignoreCase or core.precomposeUnicode could widen the destination's own
# rules until they cover a path it never named. The first two are shut off by
# "CONFIGURATION ISOLATION" above and the third is taken from the destination
# itself; what is left is the destination's own .gitignore, its info/exclude and
# its index, read with the matching that repository asks for.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ... AND THE ISOLATION IS PROVED FIRST, on the git that is actually going to
# answer (issue #193, adversarial review).
#
# The block above can cite the version each mechanism arrived in; it cannot know
# which git is installed here, and a guard that reasons about versions it has not
# looked at is the shape of the defect this check exists to close -- an isolation
# that is silently inert reads exactly like an isolation that works. So the
# question is put to the running git directly, in the destination, where every
# configuration file that could reach the ignore verdict is already in the stack:
#
#   _git -C "$DST_REAL" config --get <key>   must print what this script forces
#
# and if it prints anything else, or nothing, the ambient configuration is still
# in charge of what counts as ignored and this script must not write. It is
# version-independent in a way the mechanisms it checks are not: `git config
# --get` is as old as git.
#
# EVERY KEY, which is what the first version of this check got wrong: it read
# back core.excludesFile alone, so the isolation could have stopped applying to
# anything else -- as it had, to the two keys the destination-derived half now
# forces -- while the proof went on passing. The list it walks is the list the
# probes are given, adopted immediately above it, so there is no second copy for
# the two to drift apart.
#
# It is the whole isolation that is on trial here, not one option. Whatever
# delivered the value -- the -c, the six variables, some later git's own
# rules -- the check asks for the RESULT, so it keeps holding if the mechanisms
# change, and it fails closed if a future git changes precedence under us.
#
# ASKED FOR EVERY PATH, not once and remembered, and the extra cost is a handful
# of read-only git commands per run. A configuration file is an ordinary file: it
# can be rewritten between the first probe and the last, and a guard that
# obtains a fact once and trusts it for the rest of the call is the shape of
# every other defect review has found in this script. It is called from the top
# of _require_uncommittable for that reason, and private_egress.py calls its own
# copy from the same place.
# ---------------------------------------------------------------------------
# One key, read back through whatever -c list is active right now. Factored out
# so the alias pass below proves its value the same way the adopted list does,
# with one refusal to keep in step rather than two.
#   $1 key   $2 the value this script forces   $3 "alias" for the alias pass
_prove_config_key() {
  local key=$1 want=$2 pass=${3:-} effective rc=0 said expected
  local -a remedy
  effective=$(_git -C "$DST_REAL" config --get "$key" 2>/dev/null) || rc=$?
  if [ "$effective" = "$want" ]; then return 0; fi
  # git's own words about the failure, so the message diagnoses itself rather
  # than reporting a silence and guessing at the cause.
  said=$(_git -C "$DST_REAL" config --get "$key" 2>&1 >/dev/null) || true
  [ -n "$said" ] || said="(nothing on stderr)"
  # The remedy is the one for THIS key, which the old message was not: it
  # ended in "upgrade git" whatever had happened, and that is right only for
  # the case it was written for -- the -c ignored. The two keys taken FROM the
  # destination reach this refusal by a route the -c mechanism is not on trial
  # in, and an operator sent to upgrade git for a config file that changed
  # underneath the run fixes nothing and learns to distrust the refusal.
  expected="$want, which is what this script forces"
  if [ "$pass" = "alias" ]; then
    expected="$want, which this script forces for the ALIAS question alone"
    remedy=("This is the one key forced AGAINST what the destination says, and"
            "only for the second tracked question (issue #204): the literal one"
            "runs with the destination's own value, and this one asks whether an"
            "index entry differing from the path only in unicode composition"
            "would be written over. A readback that disagrees means the -c never"
            "reached this git, so that question was answered with the wrong"
            "matching -- upgrade git if it said nothing above, and fix what it"
            "printed if it did.")
  else
    case "$key" in
      core.ignoreCase|core.precomposeUnicode)
        remedy=("This key is not switched off, it is taken FROM the destination:"
                "the value above was read from that repository's own"
                "--worktree/--local config a moment earlier. Either the file"
                "changed underneath the run or git could not read it -- see 'git"
                "said' above, re-run, and if it persists inspect the"
                "destination's own .git/config. Upgrading git is NOT the remedy"
                "here.") ;;
      *)
        remedy=("If git said nothing above, the -c option on the command line"
                "did not take effect: 'git -c' has existed since git 1.7.2"
                "(2010), so a git that ignores it is far older than anything this"
                "script has been run on -- upgrade git, and re-run. If git"
                "printed an error, that error is what to fix first: the isolation"
                "is unproven because the question could not be asked, not because"
                "the answer was wrong.") ;;
    esac
  fi
  _refuse "this git could not be isolated from the operator's own configuration" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "found:       $key reads back as '${effective:-<unset>}'" \
    "             ('git config --get $key' exited $rc)" \
    "git said:    $said" \
    "expected:    $expected -- with 'git -c'" \
    "             on every command, and, for core.excludesFile, with" \
    "             GIT_CONFIG_COUNT/KEY/VALUE as well" \
    "git version: $(_git --version 2>/dev/null || echo unknown)" \
    "The ignore check below asks the destination whether it would let this" \
    "household's archive be committed. If the operator's global, XDG or system" \
    "configuration can still supply core.excludesFile -- or widen the matching" \
    "with core.ignoreCase or core.precomposeUnicode -- that answer is about" \
    "this machine rather than about the repository, and a destination that" \
    "does NOT ignore private/ can answer that it does." \
    "${remedy[@]}" \
    "Nothing was written, and nothing about the destination is at fault."
}

_require_isolation_proven() {
  local kv
  _adopt_destination_config
  # DERIVED FROM THE WRAPPER, not restated. Both arrays are the ones _git puts
  # on every command line, so a key added to the forcing above is read back here
  # with no second edit -- the defect this loop had when it named
  # core.excludesFile itself. _GIT_AMBIENT_CONFIG is deliberately not walked:
  # its values are the operator's, multi-valued, and `config --get` of one prints
  # whichever entry came first, so there is nothing here to compare.
  for kv in "${_GIT_FORCED_CONFIG[@]}" ${_GIT_DEST_CONFIG[@]+"${_GIT_DEST_CONFIG[@]}"}; do
    if [ "$kv" = "-c" ]; then continue; fi   # the arrays hold their own option flags
    _prove_config_key "${kv%%=*}" "${kv#*=}"
  done
  # AND THE ALIAS LIST, under ITSELF (issue #204). Read back with
  # _GIT_ALIAS_CONFIG active, because a `config --get` run without it would
  # report the value the destination states -- confirming the adopted list a
  # second time and proving nothing about the list the alias probe runs with.
  # Only the keys that DIFFER are re-asked: the rest are the same -c options in
  # the same order, proved by the loop above, and re-asking would double every
  # readback for every path.
  _GIT_ALIAS_CONFIG=(${_GIT_ALIAS_OVERRIDE[@]+"${_GIT_ALIAS_OVERRIDE[@]}"})
  for kv in "${_GIT_ALIAS_OVERRIDE[@]}"; do
    if [ "$kv" = "-c" ]; then continue; fi
    _prove_config_key "${kv%%=*}" "${kv#*=}" alias
  done
  _GIT_ALIAS_CONFIG=()
}

# Does a write to $DST_REAL/$1 land on a filesystem that treats two spellings
# differing only in case as ONE file? MEASURED here, not inferred from `uname`,
# and measured at every directory the path crosses rather than at the root alone.
#
# THE ROOT IS NOT THE FILESYSTEM THE WRITE LANDS ON. This used to be one `-ef` at
# the worktree root, and a case-insensitive volume mounted BELOW that root was
# therefore measured as case-sensitive: ':(icase)' was left off, and an index
# entry differing from the path only in case -- with no file in the working tree,
# so _ondisk_spelling has nothing to resolve either -- was seen by neither alias
# probe. Reproduced on macOS 15 with two attached disk images, a case-sensitive
# APFS volume holding the worktree and a case-insensitive HFS+ volume mounted at
# <root>/private, over an index holding private/HOUSEHOLD.yaml written with
# `update-index --cacheinfo` so no working-tree file ever existed for it: both
# implementations measured "case is significant", accepted, and after the copy
# `git status` reported `AM private/HOUSEHOLD.yaml` -- the private bytes staged
# under the committed name by one `git add -A`.
#
# SO THE MEASUREMENT FOLLOWS THE PATH:
#
#   the ROOT keeps its own probe        $DST_REAL/.git  -ef  $DST_REAL/.GIT
#   every EXISTING directory below it   _dir_folds_case, on an entry of its own
#
# `-ef` is "same device and inode", so an equal answer means the filesystem
# resolved both spellings to one file, which is the whole of the question. A
# case-sensitive filesystem has no .GIT and answers no; one that really holds a
# separate .GIT answers no for the right reason. Where the root already folds the
# answer is the additive one and no directory is listed at all. Where it does
# not, the first directory below it that folds decides.
#
# THAT IS AN OR ALONG THE PATH, deliberately, and it is not the equivalence
# relation -- it is the CANDIDATE GENERATOR. ':(icase)' is applied to the whole
# pathspec by git, so this is the only question a pathspec can be asked, and a
# narrower answer here would leave index entries unfound. Being over-broad was,
# on its own, issue #231: a case-SENSITIVE volume mounted under a folding
# directory had every component below the folding one folded too, and a
# destination that should be accepted was refused. What closed it is not a
# narrower OR but a second measurement kept alongside -- _dest_folds_vector
# answers per component, and every entry the pathspec returns is filtered through
# _classify_alias before it may refuse anything.
#
# NOTHING IS WRITTEN, which is what settles the ordering this measurement would
# otherwise pose. The reliable way to detect case behaviour is usually to create
# a probe file and see whether the alias resolves -- inside a destination this
# script has not yet decided it may write to. Asking about an entry that is
# already there removes the ordering question instead of answering it: `-ef`
# stats, so it creates nothing and opens nothing (a FIFO cannot block it), and
# it is safe at any point in the guard. `.git` is the entry every worktree root
# is guaranteed to have -- a directory in the main checkout, a gitfile in a
# linked worktree -- and it has case-varying characters, so no directory listing
# is needed to find something to ask about; no directory below the root has a
# name like that, which is why those are measured on an entry of their own.
#
# NOT a mount-boundary test, though that was the other candidate. Detecting a
# device change would say WHERE to measure and still not say what that filesystem
# does, so it would need this probe anyway -- and `[ -ef ]` compares device and
# inode together, so the shell cannot isolate a device at all without `stat`,
# whose flags differ between BSD and GNU. Measuring behaviour directly needs no
# notion of a mount, and private_egress.py can do exactly the same thing.
#
# FAILS CLOSED TOWARD FOLDING: a `.git` that cannot be stat'd, a directory that
# cannot be listed, and a directory holding no entry whose ASCII case can be
# flipped all leave the question unanswered, and folding is the conservative
# answer because the alias probe is additive -- it can only make this script
# refuse more, and only for a destination that also holds a case-aliased entry in
# its index: the thing being guarded against wherever the two spellings really are
# one file, and a false alarm where they are not. An EMPTY case-sensitive volume
# is that false alarm and stays one -- nothing in it can be measured, so
# _dest_folds_vector reads it as folding too.
#
# Sets _FOLDS_WHERE, in the same words private_egress._fs_folds_case() records,
# so the refusal below names the directory the answer was taken from.

# $1 with every ASCII letter's case swapped. LC_ALL=C on `tr` so the ranges are
# bytes: python's _ascii_case_flip() folds exactly these pairs and no others, and
# a measurement that folded more on one side than the other would put the two
# implementations on different entries.
_ascii_case_flip() {   # $1 = a name
  printf '%s' "$1" | LC_ALL=C tr 'A-Za-z' 'a-zA-Z'
}

# Prints yes | no | unknown for ONE directory, measured on an entry it already
# holds. Entries come from one LC_ALL=C glob, so this and python's
# _dir_folds_case() pick the same entry to ask about. An entry that cannot be
# stat'd under its own name (a dangling symlink) is skipped rather than read as
# "does not fold": `-ef` is false for both spellings there, which is not a
# measurement. `unknown` is an unlistable directory or one with no ASCII letter
# in any entry -- an empty directory being the ordinary case.
#
# KNOWN IMPRECISION, AND WHY IT STAYS (issue #231, AC4). A case-sensitive
# directory holding two HARD LINKS to one inode under case-aliased names
# ('README' and 'readme') measures as folding, because device and inode are what
# folding looks like. It could be told apart -- on a folding filesystem only ONE
# of the two spellings is an entry of the directory, so seeing both in the glob
# proves it does not fold -- and it deliberately is not, because that same shape
# is the only instrument a test has: a case-aliased pair sharing an inode is how
# analysis/test_private_egress.py builds a folding directory on a case-sensitive
# machine, mounting being something a test suite may not leave behind. It errs
# toward folding, the additive direction, and it can no longer contaminate the
# rest of the path -- the answer is per component now (_dest_folds_vector), so a
# directory measuring folding for this reason folds only its OWN entries.
_dir_folds_case() {   # $1 = an existing directory
  local d=$1 entry name flip
  local LC_ALL=C
  local had_nullglob=0 had_dotglob=0
  local -a entries
  if [ ! -r "$d" ] || [ ! -x "$d" ]; then printf 'unknown'; return 0; fi
  if shopt -q nullglob; then had_nullglob=1; fi
  if shopt -q dotglob; then had_dotglob=1; fi
  shopt -s nullglob dotglob
  entries=("$d"/*)
  if [ "$had_nullglob" = 0 ]; then shopt -u nullglob; fi
  if [ "$had_dotglob" = 0 ]; then shopt -u dotglob; fi
  for entry in ${entries[@]+"${entries[@]}"}; do
    name=${entry##*/}
    flip=$(_ascii_case_flip "$name")
    # `$( )` strips TRAILING newlines, so a name ending in one comes back
    # shorter and this measurement would be taken on a path that is not the
    # entry. Python skips exactly these names for the same reason: an entry only
    # one implementation can ask about is one they can disagree on.
    if [ "${#flip}" != "${#name}" ]; then continue; fi
    if [ "$flip" = "$name" ]; then continue; fi
    if [ ! -e "$entry" ]; then continue; fi
    if [ "$entry" -ef "$d/$flip" ]; then printf 'yes'; else printf 'no'; fi
    return 0
  done
  printf 'unknown'
}

_FOLDS_WHERE=
_dest_folds_case() {   # $1 = a path this script writes, relative to $DST_REAL
  local rel=${1:-} cur=$DST_REAL comp r ans
  local LC_ALL=C
  if [ ! -e "$DST_REAL/.git" ]; then
    _FOLDS_WHERE="yes  (unmeasurable: $DST_REAL/.git could not be stat'd)"
    return 0
  fi
  if [ "$DST_REAL/.git" -ef "$DST_REAL/.GIT" ]; then
    _FOLDS_WHERE="yes  (measured: .git and .GIT are one file at the worktree root)"
    return 0
  fi
  r=$rel
  while [ -n "$r" ]; do
    comp=${r%%/*}
    if [ "$comp" = "$r" ]; then r=; else r=${r#*/}; fi
    if [ -z "$comp" ]; then continue; fi
    if [ ! -d "$cur/$comp" ]; then break; fi   # the walk has left the existing tree
    cur=$cur/$comp
    ans=$(_dir_folds_case "$cur")
    case "$ans" in
      yes)     _FOLDS_WHERE="yes  (measured: two case spellings are one file in $cur)"
               return 0 ;;
      unknown) _FOLDS_WHERE="yes  (unmeasurable: $cur holds no entry whose ASCII case can be flipped)"
               return 0 ;;
    esac
  done
  _FOLDS_WHERE="no   (measured at the worktree root and at every existing directory below it on this path)"
  return 1
}

# ---------------------------------------------------------------------------
# PER-COMPONENT CASE BEHAVIOUR (issue #231), and THE BYTE FOLD (issue #230).
#
# _dest_folds_case above answers "is a fold possible ANYWHERE on this path",
# because that is the only question git's pathspec can be asked: ':(icase)' is
# applied path-wide. That OR is the right CANDIDATE GENERATOR and the wrong
# equivalence relation -- with a case-sensitive volume mounted at
# private/sensitive/ under a case-insensitive private/, an index entry
# private/sensitive/FOO is a genuinely DIFFERENT file from private/sensitive/foo
# and the pathspec matches it anyway. Reproduced on three real nested mounts (a
# case-sensitive APFS image holding the worktree, a case-insensitive one mounted
# at <root>/private, a case-sensitive one at <root>/private/sensitive) before
# this existed: the guard refused a destination it should accept.
#
# So the answer is kept PER COMPONENT in _FOLDS_VEC, and every index entry git
# returns is filtered against it -- a component may fold only where its OWN
# parent directory folds. _FOLDS_VEC[i] is measured at the directory containing
# component i:
#
#   i = 0                  the worktree root, the same .git/.GIT pair
#   the directory EXISTS   _dir_folds_case; UNMEASURABLE counts as folding, by
#                          the argument the root probe already uses
#   it does NOT exist      the answer of the nearest existing ancestor, which is
#                          a deduction and not a default: a directory the copies
#                          are about to CREATE is created inside its parent, on
#                          its parent's filesystem, and nothing can be mounted at
#                          a path that is not there
#
# THE CASE FOLD is the other half, and it is the one comparison here that MODELS
# what a folding filesystem does instead of measuring it. Everywhere else `-ef`
# asks the filesystem itself, and that needs one of the two names to EXIST. For
# a tracked index entry with no working-tree file, compared against a leaf the
# copies have not created yet, NEITHER name exists, and there is no write-free
# way to ask a filesystem whether it would collide two names that are not there
# (issue #230). So the two are folded on the raw bytes instead, FROM ONE
# GENERATED TABLE: _CASE_FOLD_SED below is python's own simple lowercase map,
# str.lower(), emitted as a sed script. private_egress.py carries the
# byte-identical text as CASE_FOLD_SED and PARSES IT to build its own fold, so
# neither side writes down a rule of its own and there is no second derivation
# for the two to drift apart. Regenerate BOTH copies with
#
#   python3 analysis/private_egress.py --regenerate-case-fold
#
# run on the NEWEST interpreter available. str.lower() is a property of that
# interpreter's Unicode version rather than a constant -- 1392 pairs under
# Unicode 13, 1432 under 15, 1459 under 16, measured -- so the table is PINNED
# at the version named in the generated block below, and generated from the
# widest fold to hand rather than from whichever python is installed where the
# tests run (issue #234). The guard case asks that the committed table be a
# SUPERSET of the running interpreter's str.lower(), not equal to it: a pair
# the table joins that this python does not know costs at most an over-refusal,
# while a pair this python knows and the table lacks is an alias nobody sees,
# which is the fail-open condition issue #230 exists to close. Regeneration on
# an OLDER interpreter refuses rather than narrowing the table.
#
# WHAT IT FOLDS, EXACTLY: every code point whose str.lower() is a SINGLE
# different code point, and nothing else -- ASCII A-Z, the whole of the 2-byte
# UTF-8 range (Latin-1 supplement, Latin Extended-A and -B, IPA, Greek and
# Coptic, Cyrillic, Armenian) and the 3- and 4-byte code points str.lower()
# maps one-to-one (Georgian, Cherokee, Glagolitic, Greek Extended, the
# fullwidth Latin forms, Warang Citi, Medefaidrin, Adlam, Deseret). Two
# unrelated accents stay apart: 'i-acute' and 'a-ring' are not a case pair.
#
# IT IS NOT A SUPERSET of what a folding volume does, and the table it replaces
# was called one here. That was wrong in both directions, measured (issue #233).
# The old table was three BYTE ranges (A-Z, \200-\237, \300-\337, each with 0x20
# set), so it folded a pair only where the UTF-8 differs in the 0x20 bit of the
# FINAL byte: of the 380 cased pairs in the five blocks a filename realistically
# uses it folded 61 and MISSED 319, including all of Latin Extended-A and -B and
# most of Greek and Cyrillic. A tracked-but-absent private/<Cyrillic RISK>.yaml
# beside a candidate spelled in lower case matched nothing and the copies were
# ALLOWED -- fail-OPEN, on a volume measured to resolve the two to one file. In
# the other direction it folded U+2010 and U+2030, which are not a case pair and
# which APFS keeps apart. The generated table does neither.
#
# WHAT IT STILL DOES NOT FOLD, all erring FAIL-OPEN and stated rather than
# implied: the 297 code points whose full casefolding differs from their simple
# lowercasing (final sigma vs sigma, long s vs 's', micro sign vs mu), which a
# per-code-point substitution cannot carry because 'sharp s' casefolds to two
# letters -- that is a count of SOURCE code points, 194 of which casefold to a
# single code point and 103 to more than one; U+0130 (capital I with dot
# above), whose lowercase is two code points; and NFC/NFD normalization, which
# is a different axis, is joined by git's core.precomposeUnicode for the
# pathspec questions above, and is NOT joined for the tracked-but-absent entry
# this table exists for.
#
# The trailing '.' is not decoration: `$( )` strips TRAILING newlines, so a name
# ending in one would come back shorter than it is and two different names could
# fold alike. It is a constant on both sides of every comparison, so it cancels.
#
# COST: one `sed` per fold, the same process count the `tr` it replaces had, at
# roughly 5ms rather than 2ms on this machine because the script carries one
# substitution per table pair. The gate in the alias loop below is what keeps
# the call count down.
# --- BEGIN GENERATED CASE FOLD (private_egress.py --regenerate-case-fold) ---
# generated from python's str.lower() under Unicode 16.0.0
_CASE_FOLD_SED='
s/A/a/g;s/B/b/g;s/C/c/g;s/D/d/g;s/E/e/g;s/F/f/g;s/G/g/g;s/H/h/g
s/I/i/g;s/J/j/g;s/K/k/g;s/L/l/g;s/M/m/g;s/N/n/g;s/O/o/g;s/P/p/g
s/Q/q/g;s/R/r/g;s/S/s/g;s/T/t/g;s/U/u/g;s/V/v/g;s/W/w/g;s/X/x/g
s/Y/y/g;s/Z/z/g;s/À/à/g;s/Á/á/g;s/Â/â/g;s/Ã/ã/g;s/Ä/ä/g;s/Å/å/g
s/Æ/æ/g;s/Ç/ç/g;s/È/è/g;s/É/é/g;s/Ê/ê/g;s/Ë/ë/g;s/Ì/ì/g;s/Í/í/g
s/Î/î/g;s/Ï/ï/g;s/Ð/ð/g;s/Ñ/ñ/g;s/Ò/ò/g;s/Ó/ó/g;s/Ô/ô/g;s/Õ/õ/g
s/Ö/ö/g;s/Ø/ø/g;s/Ù/ù/g;s/Ú/ú/g;s/Û/û/g;s/Ü/ü/g;s/Ý/ý/g;s/Þ/þ/g
s/Ā/ā/g;s/Ă/ă/g;s/Ą/ą/g;s/Ć/ć/g;s/Ĉ/ĉ/g;s/Ċ/ċ/g;s/Č/č/g;s/Ď/ď/g
s/Đ/đ/g;s/Ē/ē/g;s/Ĕ/ĕ/g;s/Ė/ė/g;s/Ę/ę/g;s/Ě/ě/g;s/Ĝ/ĝ/g;s/Ğ/ğ/g
s/Ġ/ġ/g;s/Ģ/ģ/g;s/Ĥ/ĥ/g;s/Ħ/ħ/g;s/Ĩ/ĩ/g;s/Ī/ī/g;s/Ĭ/ĭ/g;s/Į/į/g
s/Ĳ/ĳ/g;s/Ĵ/ĵ/g;s/Ķ/ķ/g;s/Ĺ/ĺ/g;s/Ļ/ļ/g;s/Ľ/ľ/g;s/Ŀ/ŀ/g;s/Ł/ł/g
s/Ń/ń/g;s/Ņ/ņ/g;s/Ň/ň/g;s/Ŋ/ŋ/g;s/Ō/ō/g;s/Ŏ/ŏ/g;s/Ő/ő/g;s/Œ/œ/g
s/Ŕ/ŕ/g;s/Ŗ/ŗ/g;s/Ř/ř/g;s/Ś/ś/g;s/Ŝ/ŝ/g;s/Ş/ş/g;s/Š/š/g;s/Ţ/ţ/g
s/Ť/ť/g;s/Ŧ/ŧ/g;s/Ũ/ũ/g;s/Ū/ū/g;s/Ŭ/ŭ/g;s/Ů/ů/g;s/Ű/ű/g;s/Ų/ų/g
s/Ŵ/ŵ/g;s/Ŷ/ŷ/g;s/Ÿ/ÿ/g;s/Ź/ź/g;s/Ż/ż/g;s/Ž/ž/g;s/Ɓ/ɓ/g;s/Ƃ/ƃ/g
s/Ƅ/ƅ/g;s/Ɔ/ɔ/g;s/Ƈ/ƈ/g;s/Ɖ/ɖ/g;s/Ɗ/ɗ/g;s/Ƌ/ƌ/g;s/Ǝ/ǝ/g;s/Ə/ə/g
s/Ɛ/ɛ/g;s/Ƒ/ƒ/g;s/Ɠ/ɠ/g;s/Ɣ/ɣ/g;s/Ɩ/ɩ/g;s/Ɨ/ɨ/g;s/Ƙ/ƙ/g;s/Ɯ/ɯ/g
s/Ɲ/ɲ/g;s/Ɵ/ɵ/g;s/Ơ/ơ/g;s/Ƣ/ƣ/g;s/Ƥ/ƥ/g;s/Ʀ/ʀ/g;s/Ƨ/ƨ/g;s/Ʃ/ʃ/g
s/Ƭ/ƭ/g;s/Ʈ/ʈ/g;s/Ư/ư/g;s/Ʊ/ʊ/g;s/Ʋ/ʋ/g;s/Ƴ/ƴ/g;s/Ƶ/ƶ/g;s/Ʒ/ʒ/g
s/Ƹ/ƹ/g;s/Ƽ/ƽ/g;s/Ǆ/ǆ/g;s/ǅ/ǆ/g;s/Ǉ/ǉ/g;s/ǈ/ǉ/g;s/Ǌ/ǌ/g;s/ǋ/ǌ/g
s/Ǎ/ǎ/g;s/Ǐ/ǐ/g;s/Ǒ/ǒ/g;s/Ǔ/ǔ/g;s/Ǖ/ǖ/g;s/Ǘ/ǘ/g;s/Ǚ/ǚ/g;s/Ǜ/ǜ/g
s/Ǟ/ǟ/g;s/Ǡ/ǡ/g;s/Ǣ/ǣ/g;s/Ǥ/ǥ/g;s/Ǧ/ǧ/g;s/Ǩ/ǩ/g;s/Ǫ/ǫ/g;s/Ǭ/ǭ/g
s/Ǯ/ǯ/g;s/Ǳ/ǳ/g;s/ǲ/ǳ/g;s/Ǵ/ǵ/g;s/Ƕ/ƕ/g;s/Ƿ/ƿ/g;s/Ǹ/ǹ/g;s/Ǻ/ǻ/g
s/Ǽ/ǽ/g;s/Ǿ/ǿ/g;s/Ȁ/ȁ/g;s/Ȃ/ȃ/g;s/Ȅ/ȅ/g;s/Ȇ/ȇ/g;s/Ȉ/ȉ/g;s/Ȋ/ȋ/g
s/Ȍ/ȍ/g;s/Ȏ/ȏ/g;s/Ȑ/ȑ/g;s/Ȓ/ȓ/g;s/Ȕ/ȕ/g;s/Ȗ/ȗ/g;s/Ș/ș/g;s/Ț/ț/g
s/Ȝ/ȝ/g;s/Ȟ/ȟ/g;s/Ƞ/ƞ/g;s/Ȣ/ȣ/g;s/Ȥ/ȥ/g;s/Ȧ/ȧ/g;s/Ȩ/ȩ/g;s/Ȫ/ȫ/g
s/Ȭ/ȭ/g;s/Ȯ/ȯ/g;s/Ȱ/ȱ/g;s/Ȳ/ȳ/g;s/Ⱥ/ⱥ/g;s/Ȼ/ȼ/g;s/Ƚ/ƚ/g;s/Ⱦ/ⱦ/g
s/Ɂ/ɂ/g;s/Ƀ/ƀ/g;s/Ʉ/ʉ/g;s/Ʌ/ʌ/g;s/Ɇ/ɇ/g;s/Ɉ/ɉ/g;s/Ɋ/ɋ/g;s/Ɍ/ɍ/g
s/Ɏ/ɏ/g;s/Ͱ/ͱ/g;s/Ͳ/ͳ/g;s/Ͷ/ͷ/g;s/Ϳ/ϳ/g;s/Ά/ά/g;s/Έ/έ/g;s/Ή/ή/g
s/Ί/ί/g;s/Ό/ό/g;s/Ύ/ύ/g;s/Ώ/ώ/g;s/Α/α/g;s/Β/β/g;s/Γ/γ/g;s/Δ/δ/g
s/Ε/ε/g;s/Ζ/ζ/g;s/Η/η/g;s/Θ/θ/g;s/Ι/ι/g;s/Κ/κ/g;s/Λ/λ/g;s/Μ/μ/g
s/Ν/ν/g;s/Ξ/ξ/g;s/Ο/ο/g;s/Π/π/g;s/Ρ/ρ/g;s/Σ/σ/g;s/Τ/τ/g;s/Υ/υ/g
s/Φ/φ/g;s/Χ/χ/g;s/Ψ/ψ/g;s/Ω/ω/g;s/Ϊ/ϊ/g;s/Ϋ/ϋ/g;s/Ϗ/ϗ/g;s/Ϙ/ϙ/g
s/Ϛ/ϛ/g;s/Ϝ/ϝ/g;s/Ϟ/ϟ/g;s/Ϡ/ϡ/g;s/Ϣ/ϣ/g;s/Ϥ/ϥ/g;s/Ϧ/ϧ/g;s/Ϩ/ϩ/g
s/Ϫ/ϫ/g;s/Ϭ/ϭ/g;s/Ϯ/ϯ/g;s/ϴ/θ/g;s/Ϸ/ϸ/g;s/Ϲ/ϲ/g;s/Ϻ/ϻ/g;s/Ͻ/ͻ/g
s/Ͼ/ͼ/g;s/Ͽ/ͽ/g;s/Ѐ/ѐ/g;s/Ё/ё/g;s/Ђ/ђ/g;s/Ѓ/ѓ/g;s/Є/є/g;s/Ѕ/ѕ/g
s/І/і/g;s/Ї/ї/g;s/Ј/ј/g;s/Љ/љ/g;s/Њ/њ/g;s/Ћ/ћ/g;s/Ќ/ќ/g;s/Ѝ/ѝ/g
s/Ў/ў/g;s/Џ/џ/g;s/А/а/g;s/Б/б/g;s/В/в/g;s/Г/г/g;s/Д/д/g;s/Е/е/g
s/Ж/ж/g;s/З/з/g;s/И/и/g;s/Й/й/g;s/К/к/g;s/Л/л/g;s/М/м/g;s/Н/н/g
s/О/о/g;s/П/п/g;s/Р/р/g;s/С/с/g;s/Т/т/g;s/У/у/g;s/Ф/ф/g;s/Х/х/g
s/Ц/ц/g;s/Ч/ч/g;s/Ш/ш/g;s/Щ/щ/g;s/Ъ/ъ/g;s/Ы/ы/g;s/Ь/ь/g;s/Э/э/g
s/Ю/ю/g;s/Я/я/g;s/Ѡ/ѡ/g;s/Ѣ/ѣ/g;s/Ѥ/ѥ/g;s/Ѧ/ѧ/g;s/Ѩ/ѩ/g;s/Ѫ/ѫ/g
s/Ѭ/ѭ/g;s/Ѯ/ѯ/g;s/Ѱ/ѱ/g;s/Ѳ/ѳ/g;s/Ѵ/ѵ/g;s/Ѷ/ѷ/g;s/Ѹ/ѹ/g;s/Ѻ/ѻ/g
s/Ѽ/ѽ/g;s/Ѿ/ѿ/g;s/Ҁ/ҁ/g;s/Ҋ/ҋ/g;s/Ҍ/ҍ/g;s/Ҏ/ҏ/g;s/Ґ/ґ/g;s/Ғ/ғ/g
s/Ҕ/ҕ/g;s/Җ/җ/g;s/Ҙ/ҙ/g;s/Қ/қ/g;s/Ҝ/ҝ/g;s/Ҟ/ҟ/g;s/Ҡ/ҡ/g;s/Ң/ң/g
s/Ҥ/ҥ/g;s/Ҧ/ҧ/g;s/Ҩ/ҩ/g;s/Ҫ/ҫ/g;s/Ҭ/ҭ/g;s/Ү/ү/g;s/Ұ/ұ/g;s/Ҳ/ҳ/g
s/Ҵ/ҵ/g;s/Ҷ/ҷ/g;s/Ҹ/ҹ/g;s/Һ/һ/g;s/Ҽ/ҽ/g;s/Ҿ/ҿ/g;s/Ӏ/ӏ/g;s/Ӂ/ӂ/g
s/Ӄ/ӄ/g;s/Ӆ/ӆ/g;s/Ӈ/ӈ/g;s/Ӊ/ӊ/g;s/Ӌ/ӌ/g;s/Ӎ/ӎ/g;s/Ӑ/ӑ/g;s/Ӓ/ӓ/g
s/Ӕ/ӕ/g;s/Ӗ/ӗ/g;s/Ә/ә/g;s/Ӛ/ӛ/g;s/Ӝ/ӝ/g;s/Ӟ/ӟ/g;s/Ӡ/ӡ/g;s/Ӣ/ӣ/g
s/Ӥ/ӥ/g;s/Ӧ/ӧ/g;s/Ө/ө/g;s/Ӫ/ӫ/g;s/Ӭ/ӭ/g;s/Ӯ/ӯ/g;s/Ӱ/ӱ/g;s/Ӳ/ӳ/g
s/Ӵ/ӵ/g;s/Ӷ/ӷ/g;s/Ӹ/ӹ/g;s/Ӻ/ӻ/g;s/Ӽ/ӽ/g;s/Ӿ/ӿ/g;s/Ԁ/ԁ/g;s/Ԃ/ԃ/g
s/Ԅ/ԅ/g;s/Ԇ/ԇ/g;s/Ԉ/ԉ/g;s/Ԋ/ԋ/g;s/Ԍ/ԍ/g;s/Ԏ/ԏ/g;s/Ԑ/ԑ/g;s/Ԓ/ԓ/g
s/Ԕ/ԕ/g;s/Ԗ/ԗ/g;s/Ԙ/ԙ/g;s/Ԛ/ԛ/g;s/Ԝ/ԝ/g;s/Ԟ/ԟ/g;s/Ԡ/ԡ/g;s/Ԣ/ԣ/g
s/Ԥ/ԥ/g;s/Ԧ/ԧ/g;s/Ԩ/ԩ/g;s/Ԫ/ԫ/g;s/Ԭ/ԭ/g;s/Ԯ/ԯ/g;s/Ա/ա/g;s/Բ/բ/g
s/Գ/գ/g;s/Դ/դ/g;s/Ե/ե/g;s/Զ/զ/g;s/Է/է/g;s/Ը/ը/g;s/Թ/թ/g;s/Ժ/ժ/g
s/Ի/ի/g;s/Լ/լ/g;s/Խ/խ/g;s/Ծ/ծ/g;s/Կ/կ/g;s/Հ/հ/g;s/Ձ/ձ/g;s/Ղ/ղ/g
s/Ճ/ճ/g;s/Մ/մ/g;s/Յ/յ/g;s/Ն/ն/g;s/Շ/շ/g;s/Ո/ո/g;s/Չ/չ/g;s/Պ/պ/g
s/Ջ/ջ/g;s/Ռ/ռ/g;s/Ս/ս/g;s/Վ/վ/g;s/Տ/տ/g;s/Ր/ր/g;s/Ց/ց/g;s/Ւ/ւ/g
s/Փ/փ/g;s/Ք/ք/g;s/Օ/օ/g;s/Ֆ/ֆ/g;s/Ⴀ/ⴀ/g;s/Ⴁ/ⴁ/g;s/Ⴂ/ⴂ/g;s/Ⴃ/ⴃ/g
s/Ⴄ/ⴄ/g;s/Ⴅ/ⴅ/g;s/Ⴆ/ⴆ/g;s/Ⴇ/ⴇ/g;s/Ⴈ/ⴈ/g;s/Ⴉ/ⴉ/g;s/Ⴊ/ⴊ/g;s/Ⴋ/ⴋ/g
s/Ⴌ/ⴌ/g;s/Ⴍ/ⴍ/g;s/Ⴎ/ⴎ/g;s/Ⴏ/ⴏ/g;s/Ⴐ/ⴐ/g;s/Ⴑ/ⴑ/g;s/Ⴒ/ⴒ/g;s/Ⴓ/ⴓ/g
s/Ⴔ/ⴔ/g;s/Ⴕ/ⴕ/g;s/Ⴖ/ⴖ/g;s/Ⴗ/ⴗ/g;s/Ⴘ/ⴘ/g;s/Ⴙ/ⴙ/g;s/Ⴚ/ⴚ/g;s/Ⴛ/ⴛ/g
s/Ⴜ/ⴜ/g;s/Ⴝ/ⴝ/g;s/Ⴞ/ⴞ/g;s/Ⴟ/ⴟ/g;s/Ⴠ/ⴠ/g;s/Ⴡ/ⴡ/g;s/Ⴢ/ⴢ/g;s/Ⴣ/ⴣ/g
s/Ⴤ/ⴤ/g;s/Ⴥ/ⴥ/g;s/Ⴧ/ⴧ/g;s/Ⴭ/ⴭ/g;s/Ꭰ/ꭰ/g;s/Ꭱ/ꭱ/g;s/Ꭲ/ꭲ/g;s/Ꭳ/ꭳ/g
s/Ꭴ/ꭴ/g;s/Ꭵ/ꭵ/g;s/Ꭶ/ꭶ/g;s/Ꭷ/ꭷ/g;s/Ꭸ/ꭸ/g;s/Ꭹ/ꭹ/g;s/Ꭺ/ꭺ/g;s/Ꭻ/ꭻ/g
s/Ꭼ/ꭼ/g;s/Ꭽ/ꭽ/g;s/Ꭾ/ꭾ/g;s/Ꭿ/ꭿ/g;s/Ꮀ/ꮀ/g;s/Ꮁ/ꮁ/g;s/Ꮂ/ꮂ/g;s/Ꮃ/ꮃ/g
s/Ꮄ/ꮄ/g;s/Ꮅ/ꮅ/g;s/Ꮆ/ꮆ/g;s/Ꮇ/ꮇ/g;s/Ꮈ/ꮈ/g;s/Ꮉ/ꮉ/g;s/Ꮊ/ꮊ/g;s/Ꮋ/ꮋ/g
s/Ꮌ/ꮌ/g;s/Ꮍ/ꮍ/g;s/Ꮎ/ꮎ/g;s/Ꮏ/ꮏ/g;s/Ꮐ/ꮐ/g;s/Ꮑ/ꮑ/g;s/Ꮒ/ꮒ/g;s/Ꮓ/ꮓ/g
s/Ꮔ/ꮔ/g;s/Ꮕ/ꮕ/g;s/Ꮖ/ꮖ/g;s/Ꮗ/ꮗ/g;s/Ꮘ/ꮘ/g;s/Ꮙ/ꮙ/g;s/Ꮚ/ꮚ/g;s/Ꮛ/ꮛ/g
s/Ꮜ/ꮜ/g;s/Ꮝ/ꮝ/g;s/Ꮞ/ꮞ/g;s/Ꮟ/ꮟ/g;s/Ꮠ/ꮠ/g;s/Ꮡ/ꮡ/g;s/Ꮢ/ꮢ/g;s/Ꮣ/ꮣ/g
s/Ꮤ/ꮤ/g;s/Ꮥ/ꮥ/g;s/Ꮦ/ꮦ/g;s/Ꮧ/ꮧ/g;s/Ꮨ/ꮨ/g;s/Ꮩ/ꮩ/g;s/Ꮪ/ꮪ/g;s/Ꮫ/ꮫ/g
s/Ꮬ/ꮬ/g;s/Ꮭ/ꮭ/g;s/Ꮮ/ꮮ/g;s/Ꮯ/ꮯ/g;s/Ꮰ/ꮰ/g;s/Ꮱ/ꮱ/g;s/Ꮲ/ꮲ/g;s/Ꮳ/ꮳ/g
s/Ꮴ/ꮴ/g;s/Ꮵ/ꮵ/g;s/Ꮶ/ꮶ/g;s/Ꮷ/ꮷ/g;s/Ꮸ/ꮸ/g;s/Ꮹ/ꮹ/g;s/Ꮺ/ꮺ/g;s/Ꮻ/ꮻ/g
s/Ꮼ/ꮼ/g;s/Ꮽ/ꮽ/g;s/Ꮾ/ꮾ/g;s/Ꮿ/ꮿ/g;s/Ᏸ/ᏸ/g;s/Ᏹ/ᏹ/g;s/Ᏺ/ᏺ/g;s/Ᏻ/ᏻ/g
s/Ᏼ/ᏼ/g;s/Ᏽ/ᏽ/g;s/Ᲊ/ᲊ/g;s/Ა/ა/g;s/Ბ/ბ/g;s/Გ/გ/g;s/Დ/დ/g;s/Ე/ე/g
s/Ვ/ვ/g;s/Ზ/ზ/g;s/Თ/თ/g;s/Ი/ი/g;s/Კ/კ/g;s/Ლ/ლ/g;s/Მ/მ/g;s/Ნ/ნ/g
s/Ო/ო/g;s/Პ/პ/g;s/Ჟ/ჟ/g;s/Რ/რ/g;s/Ს/ს/g;s/Ტ/ტ/g;s/Უ/უ/g;s/Ფ/ფ/g
s/Ქ/ქ/g;s/Ღ/ღ/g;s/Ყ/ყ/g;s/Შ/შ/g;s/Ჩ/ჩ/g;s/Ც/ც/g;s/Ძ/ძ/g;s/Წ/წ/g
s/Ჭ/ჭ/g;s/Ხ/ხ/g;s/Ჯ/ჯ/g;s/Ჰ/ჰ/g;s/Ჱ/ჱ/g;s/Ჲ/ჲ/g;s/Ჳ/ჳ/g;s/Ჴ/ჴ/g
s/Ჵ/ჵ/g;s/Ჶ/ჶ/g;s/Ჷ/ჷ/g;s/Ჸ/ჸ/g;s/Ჹ/ჹ/g;s/Ჺ/ჺ/g;s/Ჽ/ჽ/g;s/Ჾ/ჾ/g
s/Ჿ/ჿ/g;s/Ḁ/ḁ/g;s/Ḃ/ḃ/g;s/Ḅ/ḅ/g;s/Ḇ/ḇ/g;s/Ḉ/ḉ/g;s/Ḋ/ḋ/g;s/Ḍ/ḍ/g
s/Ḏ/ḏ/g;s/Ḑ/ḑ/g;s/Ḓ/ḓ/g;s/Ḕ/ḕ/g;s/Ḗ/ḗ/g;s/Ḙ/ḙ/g;s/Ḛ/ḛ/g;s/Ḝ/ḝ/g
s/Ḟ/ḟ/g;s/Ḡ/ḡ/g;s/Ḣ/ḣ/g;s/Ḥ/ḥ/g;s/Ḧ/ḧ/g;s/Ḩ/ḩ/g;s/Ḫ/ḫ/g;s/Ḭ/ḭ/g
s/Ḯ/ḯ/g;s/Ḱ/ḱ/g;s/Ḳ/ḳ/g;s/Ḵ/ḵ/g;s/Ḷ/ḷ/g;s/Ḹ/ḹ/g;s/Ḻ/ḻ/g;s/Ḽ/ḽ/g
s/Ḿ/ḿ/g;s/Ṁ/ṁ/g;s/Ṃ/ṃ/g;s/Ṅ/ṅ/g;s/Ṇ/ṇ/g;s/Ṉ/ṉ/g;s/Ṋ/ṋ/g;s/Ṍ/ṍ/g
s/Ṏ/ṏ/g;s/Ṑ/ṑ/g;s/Ṓ/ṓ/g;s/Ṕ/ṕ/g;s/Ṗ/ṗ/g;s/Ṙ/ṙ/g;s/Ṛ/ṛ/g;s/Ṝ/ṝ/g
s/Ṟ/ṟ/g;s/Ṡ/ṡ/g;s/Ṣ/ṣ/g;s/Ṥ/ṥ/g;s/Ṧ/ṧ/g;s/Ṩ/ṩ/g;s/Ṫ/ṫ/g;s/Ṭ/ṭ/g
s/Ṯ/ṯ/g;s/Ṱ/ṱ/g;s/Ṳ/ṳ/g;s/Ṵ/ṵ/g;s/Ṷ/ṷ/g;s/Ṹ/ṹ/g;s/Ṻ/ṻ/g;s/Ṽ/ṽ/g
s/Ṿ/ṿ/g;s/Ẁ/ẁ/g;s/Ẃ/ẃ/g;s/Ẅ/ẅ/g;s/Ẇ/ẇ/g;s/Ẉ/ẉ/g;s/Ẋ/ẋ/g;s/Ẍ/ẍ/g
s/Ẏ/ẏ/g;s/Ẑ/ẑ/g;s/Ẓ/ẓ/g;s/Ẕ/ẕ/g;s/ẞ/ß/g;s/Ạ/ạ/g;s/Ả/ả/g;s/Ấ/ấ/g
s/Ầ/ầ/g;s/Ẩ/ẩ/g;s/Ẫ/ẫ/g;s/Ậ/ậ/g;s/Ắ/ắ/g;s/Ằ/ằ/g;s/Ẳ/ẳ/g;s/Ẵ/ẵ/g
s/Ặ/ặ/g;s/Ẹ/ẹ/g;s/Ẻ/ẻ/g;s/Ẽ/ẽ/g;s/Ế/ế/g;s/Ề/ề/g;s/Ể/ể/g;s/Ễ/ễ/g
s/Ệ/ệ/g;s/Ỉ/ỉ/g;s/Ị/ị/g;s/Ọ/ọ/g;s/Ỏ/ỏ/g;s/Ố/ố/g;s/Ồ/ồ/g;s/Ổ/ổ/g
s/Ỗ/ỗ/g;s/Ộ/ộ/g;s/Ớ/ớ/g;s/Ờ/ờ/g;s/Ở/ở/g;s/Ỡ/ỡ/g;s/Ợ/ợ/g;s/Ụ/ụ/g
s/Ủ/ủ/g;s/Ứ/ứ/g;s/Ừ/ừ/g;s/Ử/ử/g;s/Ữ/ữ/g;s/Ự/ự/g;s/Ỳ/ỳ/g;s/Ỵ/ỵ/g
s/Ỷ/ỷ/g;s/Ỹ/ỹ/g;s/Ỻ/ỻ/g;s/Ỽ/ỽ/g;s/Ỿ/ỿ/g;s/Ἀ/ἀ/g;s/Ἁ/ἁ/g;s/Ἂ/ἂ/g
s/Ἃ/ἃ/g;s/Ἄ/ἄ/g;s/Ἅ/ἅ/g;s/Ἆ/ἆ/g;s/Ἇ/ἇ/g;s/Ἐ/ἐ/g;s/Ἑ/ἑ/g;s/Ἒ/ἒ/g
s/Ἓ/ἓ/g;s/Ἔ/ἔ/g;s/Ἕ/ἕ/g;s/Ἠ/ἠ/g;s/Ἡ/ἡ/g;s/Ἢ/ἢ/g;s/Ἣ/ἣ/g;s/Ἤ/ἤ/g
s/Ἥ/ἥ/g;s/Ἦ/ἦ/g;s/Ἧ/ἧ/g;s/Ἰ/ἰ/g;s/Ἱ/ἱ/g;s/Ἲ/ἲ/g;s/Ἳ/ἳ/g;s/Ἴ/ἴ/g
s/Ἵ/ἵ/g;s/Ἶ/ἶ/g;s/Ἷ/ἷ/g;s/Ὀ/ὀ/g;s/Ὁ/ὁ/g;s/Ὂ/ὂ/g;s/Ὃ/ὃ/g;s/Ὄ/ὄ/g
s/Ὅ/ὅ/g;s/Ὑ/ὑ/g;s/Ὓ/ὓ/g;s/Ὕ/ὕ/g;s/Ὗ/ὗ/g;s/Ὠ/ὠ/g;s/Ὡ/ὡ/g;s/Ὢ/ὢ/g
s/Ὣ/ὣ/g;s/Ὤ/ὤ/g;s/Ὥ/ὥ/g;s/Ὦ/ὦ/g;s/Ὧ/ὧ/g;s/ᾈ/ᾀ/g;s/ᾉ/ᾁ/g;s/ᾊ/ᾂ/g
s/ᾋ/ᾃ/g;s/ᾌ/ᾄ/g;s/ᾍ/ᾅ/g;s/ᾎ/ᾆ/g;s/ᾏ/ᾇ/g;s/ᾘ/ᾐ/g;s/ᾙ/ᾑ/g;s/ᾚ/ᾒ/g
s/ᾛ/ᾓ/g;s/ᾜ/ᾔ/g;s/ᾝ/ᾕ/g;s/ᾞ/ᾖ/g;s/ᾟ/ᾗ/g;s/ᾨ/ᾠ/g;s/ᾩ/ᾡ/g;s/ᾪ/ᾢ/g
s/ᾫ/ᾣ/g;s/ᾬ/ᾤ/g;s/ᾭ/ᾥ/g;s/ᾮ/ᾦ/g;s/ᾯ/ᾧ/g;s/Ᾰ/ᾰ/g;s/Ᾱ/ᾱ/g;s/Ὰ/ὰ/g
s/Ά/ά/g;s/ᾼ/ᾳ/g;s/Ὲ/ὲ/g;s/Έ/έ/g;s/Ὴ/ὴ/g;s/Ή/ή/g;s/ῌ/ῃ/g;s/Ῐ/ῐ/g
s/Ῑ/ῑ/g;s/Ὶ/ὶ/g;s/Ί/ί/g;s/Ῠ/ῠ/g;s/Ῡ/ῡ/g;s/Ὺ/ὺ/g;s/Ύ/ύ/g;s/Ῥ/ῥ/g
s/Ὸ/ὸ/g;s/Ό/ό/g;s/Ὼ/ὼ/g;s/Ώ/ώ/g;s/ῼ/ῳ/g;s/Ω/ω/g;s/K/k/g;s/Å/å/g
s/Ⅎ/ⅎ/g;s/Ⅰ/ⅰ/g;s/Ⅱ/ⅱ/g;s/Ⅲ/ⅲ/g;s/Ⅳ/ⅳ/g;s/Ⅴ/ⅴ/g;s/Ⅵ/ⅵ/g;s/Ⅶ/ⅶ/g
s/Ⅷ/ⅷ/g;s/Ⅸ/ⅸ/g;s/Ⅹ/ⅹ/g;s/Ⅺ/ⅺ/g;s/Ⅻ/ⅻ/g;s/Ⅼ/ⅼ/g;s/Ⅽ/ⅽ/g;s/Ⅾ/ⅾ/g
s/Ⅿ/ⅿ/g;s/Ↄ/ↄ/g;s/Ⓐ/ⓐ/g;s/Ⓑ/ⓑ/g;s/Ⓒ/ⓒ/g;s/Ⓓ/ⓓ/g;s/Ⓔ/ⓔ/g;s/Ⓕ/ⓕ/g
s/Ⓖ/ⓖ/g;s/Ⓗ/ⓗ/g;s/Ⓘ/ⓘ/g;s/Ⓙ/ⓙ/g;s/Ⓚ/ⓚ/g;s/Ⓛ/ⓛ/g;s/Ⓜ/ⓜ/g;s/Ⓝ/ⓝ/g
s/Ⓞ/ⓞ/g;s/Ⓟ/ⓟ/g;s/Ⓠ/ⓠ/g;s/Ⓡ/ⓡ/g;s/Ⓢ/ⓢ/g;s/Ⓣ/ⓣ/g;s/Ⓤ/ⓤ/g;s/Ⓥ/ⓥ/g
s/Ⓦ/ⓦ/g;s/Ⓧ/ⓧ/g;s/Ⓨ/ⓨ/g;s/Ⓩ/ⓩ/g;s/Ⰰ/ⰰ/g;s/Ⰱ/ⰱ/g;s/Ⰲ/ⰲ/g;s/Ⰳ/ⰳ/g
s/Ⰴ/ⰴ/g;s/Ⰵ/ⰵ/g;s/Ⰶ/ⰶ/g;s/Ⰷ/ⰷ/g;s/Ⰸ/ⰸ/g;s/Ⰹ/ⰹ/g;s/Ⰺ/ⰺ/g;s/Ⰻ/ⰻ/g
s/Ⰼ/ⰼ/g;s/Ⰽ/ⰽ/g;s/Ⰾ/ⰾ/g;s/Ⰿ/ⰿ/g;s/Ⱀ/ⱀ/g;s/Ⱁ/ⱁ/g;s/Ⱂ/ⱂ/g;s/Ⱃ/ⱃ/g
s/Ⱄ/ⱄ/g;s/Ⱅ/ⱅ/g;s/Ⱆ/ⱆ/g;s/Ⱇ/ⱇ/g;s/Ⱈ/ⱈ/g;s/Ⱉ/ⱉ/g;s/Ⱊ/ⱊ/g;s/Ⱋ/ⱋ/g
s/Ⱌ/ⱌ/g;s/Ⱍ/ⱍ/g;s/Ⱎ/ⱎ/g;s/Ⱏ/ⱏ/g;s/Ⱐ/ⱐ/g;s/Ⱑ/ⱑ/g;s/Ⱒ/ⱒ/g;s/Ⱓ/ⱓ/g
s/Ⱔ/ⱔ/g;s/Ⱕ/ⱕ/g;s/Ⱖ/ⱖ/g;s/Ⱗ/ⱗ/g;s/Ⱘ/ⱘ/g;s/Ⱙ/ⱙ/g;s/Ⱚ/ⱚ/g;s/Ⱛ/ⱛ/g
s/Ⱜ/ⱜ/g;s/Ⱝ/ⱝ/g;s/Ⱞ/ⱞ/g;s/Ⱟ/ⱟ/g;s/Ⱡ/ⱡ/g;s/Ɫ/ɫ/g;s/Ᵽ/ᵽ/g;s/Ɽ/ɽ/g
s/Ⱨ/ⱨ/g;s/Ⱪ/ⱪ/g;s/Ⱬ/ⱬ/g;s/Ɑ/ɑ/g;s/Ɱ/ɱ/g;s/Ɐ/ɐ/g;s/Ɒ/ɒ/g;s/Ⱳ/ⱳ/g
s/Ⱶ/ⱶ/g;s/Ȿ/ȿ/g;s/Ɀ/ɀ/g;s/Ⲁ/ⲁ/g;s/Ⲃ/ⲃ/g;s/Ⲅ/ⲅ/g;s/Ⲇ/ⲇ/g;s/Ⲉ/ⲉ/g
s/Ⲋ/ⲋ/g;s/Ⲍ/ⲍ/g;s/Ⲏ/ⲏ/g;s/Ⲑ/ⲑ/g;s/Ⲓ/ⲓ/g;s/Ⲕ/ⲕ/g;s/Ⲗ/ⲗ/g;s/Ⲙ/ⲙ/g
s/Ⲛ/ⲛ/g;s/Ⲝ/ⲝ/g;s/Ⲟ/ⲟ/g;s/Ⲡ/ⲡ/g;s/Ⲣ/ⲣ/g;s/Ⲥ/ⲥ/g;s/Ⲧ/ⲧ/g;s/Ⲩ/ⲩ/g
s/Ⲫ/ⲫ/g;s/Ⲭ/ⲭ/g;s/Ⲯ/ⲯ/g;s/Ⲱ/ⲱ/g;s/Ⲳ/ⲳ/g;s/Ⲵ/ⲵ/g;s/Ⲷ/ⲷ/g;s/Ⲹ/ⲹ/g
s/Ⲻ/ⲻ/g;s/Ⲽ/ⲽ/g;s/Ⲿ/ⲿ/g;s/Ⳁ/ⳁ/g;s/Ⳃ/ⳃ/g;s/Ⳅ/ⳅ/g;s/Ⳇ/ⳇ/g;s/Ⳉ/ⳉ/g
s/Ⳋ/ⳋ/g;s/Ⳍ/ⳍ/g;s/Ⳏ/ⳏ/g;s/Ⳑ/ⳑ/g;s/Ⳓ/ⳓ/g;s/Ⳕ/ⳕ/g;s/Ⳗ/ⳗ/g;s/Ⳙ/ⳙ/g
s/Ⳛ/ⳛ/g;s/Ⳝ/ⳝ/g;s/Ⳟ/ⳟ/g;s/Ⳡ/ⳡ/g;s/Ⳣ/ⳣ/g;s/Ⳬ/ⳬ/g;s/Ⳮ/ⳮ/g;s/Ⳳ/ⳳ/g
s/Ꙁ/ꙁ/g;s/Ꙃ/ꙃ/g;s/Ꙅ/ꙅ/g;s/Ꙇ/ꙇ/g;s/Ꙉ/ꙉ/g;s/Ꙋ/ꙋ/g;s/Ꙍ/ꙍ/g;s/Ꙏ/ꙏ/g
s/Ꙑ/ꙑ/g;s/Ꙓ/ꙓ/g;s/Ꙕ/ꙕ/g;s/Ꙗ/ꙗ/g;s/Ꙙ/ꙙ/g;s/Ꙛ/ꙛ/g;s/Ꙝ/ꙝ/g;s/Ꙟ/ꙟ/g
s/Ꙡ/ꙡ/g;s/Ꙣ/ꙣ/g;s/Ꙥ/ꙥ/g;s/Ꙧ/ꙧ/g;s/Ꙩ/ꙩ/g;s/Ꙫ/ꙫ/g;s/Ꙭ/ꙭ/g;s/Ꚁ/ꚁ/g
s/Ꚃ/ꚃ/g;s/Ꚅ/ꚅ/g;s/Ꚇ/ꚇ/g;s/Ꚉ/ꚉ/g;s/Ꚋ/ꚋ/g;s/Ꚍ/ꚍ/g;s/Ꚏ/ꚏ/g;s/Ꚑ/ꚑ/g
s/Ꚓ/ꚓ/g;s/Ꚕ/ꚕ/g;s/Ꚗ/ꚗ/g;s/Ꚙ/ꚙ/g;s/Ꚛ/ꚛ/g;s/Ꜣ/ꜣ/g;s/Ꜥ/ꜥ/g;s/Ꜧ/ꜧ/g
s/Ꜩ/ꜩ/g;s/Ꜫ/ꜫ/g;s/Ꜭ/ꜭ/g;s/Ꜯ/ꜯ/g;s/Ꜳ/ꜳ/g;s/Ꜵ/ꜵ/g;s/Ꜷ/ꜷ/g;s/Ꜹ/ꜹ/g
s/Ꜻ/ꜻ/g;s/Ꜽ/ꜽ/g;s/Ꜿ/ꜿ/g;s/Ꝁ/ꝁ/g;s/Ꝃ/ꝃ/g;s/Ꝅ/ꝅ/g;s/Ꝇ/ꝇ/g;s/Ꝉ/ꝉ/g
s/Ꝋ/ꝋ/g;s/Ꝍ/ꝍ/g;s/Ꝏ/ꝏ/g;s/Ꝑ/ꝑ/g;s/Ꝓ/ꝓ/g;s/Ꝕ/ꝕ/g;s/Ꝗ/ꝗ/g;s/Ꝙ/ꝙ/g
s/Ꝛ/ꝛ/g;s/Ꝝ/ꝝ/g;s/Ꝟ/ꝟ/g;s/Ꝡ/ꝡ/g;s/Ꝣ/ꝣ/g;s/Ꝥ/ꝥ/g;s/Ꝧ/ꝧ/g;s/Ꝩ/ꝩ/g
s/Ꝫ/ꝫ/g;s/Ꝭ/ꝭ/g;s/Ꝯ/ꝯ/g;s/Ꝺ/ꝺ/g;s/Ꝼ/ꝼ/g;s/Ᵹ/ᵹ/g;s/Ꝿ/ꝿ/g;s/Ꞁ/ꞁ/g
s/Ꞃ/ꞃ/g;s/Ꞅ/ꞅ/g;s/Ꞇ/ꞇ/g;s/Ꞌ/ꞌ/g;s/Ɥ/ɥ/g;s/Ꞑ/ꞑ/g;s/Ꞓ/ꞓ/g;s/Ꞗ/ꞗ/g
s/Ꞙ/ꞙ/g;s/Ꞛ/ꞛ/g;s/Ꞝ/ꞝ/g;s/Ꞟ/ꞟ/g;s/Ꞡ/ꞡ/g;s/Ꞣ/ꞣ/g;s/Ꞥ/ꞥ/g;s/Ꞧ/ꞧ/g
s/Ꞩ/ꞩ/g;s/Ɦ/ɦ/g;s/Ɜ/ɜ/g;s/Ɡ/ɡ/g;s/Ɬ/ɬ/g;s/Ɪ/ɪ/g;s/Ʞ/ʞ/g;s/Ʇ/ʇ/g
s/Ʝ/ʝ/g;s/Ꭓ/ꭓ/g;s/Ꞵ/ꞵ/g;s/Ꞷ/ꞷ/g;s/Ꞹ/ꞹ/g;s/Ꞻ/ꞻ/g;s/Ꞽ/ꞽ/g;s/Ꞿ/ꞿ/g
s/Ꟁ/ꟁ/g;s/Ꟃ/ꟃ/g;s/Ꞔ/ꞔ/g;s/Ʂ/ʂ/g;s/Ᶎ/ᶎ/g;s/Ꟈ/ꟈ/g;s/Ꟊ/ꟊ/g;s/Ɤ/ɤ/g
s/Ꟍ/ꟍ/g;s/Ꟑ/ꟑ/g;s/Ꟗ/ꟗ/g;s/Ꟙ/ꟙ/g;s/Ꟛ/ꟛ/g;s/Ƛ/ƛ/g;s/Ꟶ/ꟶ/g;s/Ａ/ａ/g
s/Ｂ/ｂ/g;s/Ｃ/ｃ/g;s/Ｄ/ｄ/g;s/Ｅ/ｅ/g;s/Ｆ/ｆ/g;s/Ｇ/ｇ/g;s/Ｈ/ｈ/g;s/Ｉ/ｉ/g
s/Ｊ/ｊ/g;s/Ｋ/ｋ/g;s/Ｌ/ｌ/g;s/Ｍ/ｍ/g;s/Ｎ/ｎ/g;s/Ｏ/ｏ/g;s/Ｐ/ｐ/g;s/Ｑ/ｑ/g
s/Ｒ/ｒ/g;s/Ｓ/ｓ/g;s/Ｔ/ｔ/g;s/Ｕ/ｕ/g;s/Ｖ/ｖ/g;s/Ｗ/ｗ/g;s/Ｘ/ｘ/g;s/Ｙ/ｙ/g
s/Ｚ/ｚ/g;s/𐐀/𐐨/g;s/𐐁/𐐩/g;s/𐐂/𐐪/g;s/𐐃/𐐫/g;s/𐐄/𐐬/g;s/𐐅/𐐭/g;s/𐐆/𐐮/g
s/𐐇/𐐯/g;s/𐐈/𐐰/g;s/𐐉/𐐱/g;s/𐐊/𐐲/g;s/𐐋/𐐳/g;s/𐐌/𐐴/g;s/𐐍/𐐵/g;s/𐐎/𐐶/g
s/𐐏/𐐷/g;s/𐐐/𐐸/g;s/𐐑/𐐹/g;s/𐐒/𐐺/g;s/𐐓/𐐻/g;s/𐐔/𐐼/g;s/𐐕/𐐽/g;s/𐐖/𐐾/g
s/𐐗/𐐿/g;s/𐐘/𐑀/g;s/𐐙/𐑁/g;s/𐐚/𐑂/g;s/𐐛/𐑃/g;s/𐐜/𐑄/g;s/𐐝/𐑅/g;s/𐐞/𐑆/g
s/𐐟/𐑇/g;s/𐐠/𐑈/g;s/𐐡/𐑉/g;s/𐐢/𐑊/g;s/𐐣/𐑋/g;s/𐐤/𐑌/g;s/𐐥/𐑍/g;s/𐐦/𐑎/g
s/𐐧/𐑏/g;s/𐒰/𐓘/g;s/𐒱/𐓙/g;s/𐒲/𐓚/g;s/𐒳/𐓛/g;s/𐒴/𐓜/g;s/𐒵/𐓝/g;s/𐒶/𐓞/g
s/𐒷/𐓟/g;s/𐒸/𐓠/g;s/𐒹/𐓡/g;s/𐒺/𐓢/g;s/𐒻/𐓣/g;s/𐒼/𐓤/g;s/𐒽/𐓥/g;s/𐒾/𐓦/g
s/𐒿/𐓧/g;s/𐓀/𐓨/g;s/𐓁/𐓩/g;s/𐓂/𐓪/g;s/𐓃/𐓫/g;s/𐓄/𐓬/g;s/𐓅/𐓭/g;s/𐓆/𐓮/g
s/𐓇/𐓯/g;s/𐓈/𐓰/g;s/𐓉/𐓱/g;s/𐓊/𐓲/g;s/𐓋/𐓳/g;s/𐓌/𐓴/g;s/𐓍/𐓵/g;s/𐓎/𐓶/g
s/𐓏/𐓷/g;s/𐓐/𐓸/g;s/𐓑/𐓹/g;s/𐓒/𐓺/g;s/𐓓/𐓻/g;s/𐕰/𐖗/g;s/𐕱/𐖘/g;s/𐕲/𐖙/g
s/𐕳/𐖚/g;s/𐕴/𐖛/g;s/𐕵/𐖜/g;s/𐕶/𐖝/g;s/𐕷/𐖞/g;s/𐕸/𐖟/g;s/𐕹/𐖠/g;s/𐕺/𐖡/g
s/𐕼/𐖣/g;s/𐕽/𐖤/g;s/𐕾/𐖥/g;s/𐕿/𐖦/g;s/𐖀/𐖧/g;s/𐖁/𐖨/g;s/𐖂/𐖩/g;s/𐖃/𐖪/g
s/𐖄/𐖫/g;s/𐖅/𐖬/g;s/𐖆/𐖭/g;s/𐖇/𐖮/g;s/𐖈/𐖯/g;s/𐖉/𐖰/g;s/𐖊/𐖱/g;s/𐖌/𐖳/g
s/𐖍/𐖴/g;s/𐖎/𐖵/g;s/𐖏/𐖶/g;s/𐖐/𐖷/g;s/𐖑/𐖸/g;s/𐖒/𐖹/g;s/𐖔/𐖻/g;s/𐖕/𐖼/g
s/𐲀/𐳀/g;s/𐲁/𐳁/g;s/𐲂/𐳂/g;s/𐲃/𐳃/g;s/𐲄/𐳄/g;s/𐲅/𐳅/g;s/𐲆/𐳆/g;s/𐲇/𐳇/g
s/𐲈/𐳈/g;s/𐲉/𐳉/g;s/𐲊/𐳊/g;s/𐲋/𐳋/g;s/𐲌/𐳌/g;s/𐲍/𐳍/g;s/𐲎/𐳎/g;s/𐲏/𐳏/g
s/𐲐/𐳐/g;s/𐲑/𐳑/g;s/𐲒/𐳒/g;s/𐲓/𐳓/g;s/𐲔/𐳔/g;s/𐲕/𐳕/g;s/𐲖/𐳖/g;s/𐲗/𐳗/g
s/𐲘/𐳘/g;s/𐲙/𐳙/g;s/𐲚/𐳚/g;s/𐲛/𐳛/g;s/𐲜/𐳜/g;s/𐲝/𐳝/g;s/𐲞/𐳞/g;s/𐲟/𐳟/g
s/𐲠/𐳠/g;s/𐲡/𐳡/g;s/𐲢/𐳢/g;s/𐲣/𐳣/g;s/𐲤/𐳤/g;s/𐲥/𐳥/g;s/𐲦/𐳦/g;s/𐲧/𐳧/g
s/𐲨/𐳨/g;s/𐲩/𐳩/g;s/𐲪/𐳪/g;s/𐲫/𐳫/g;s/𐲬/𐳬/g;s/𐲭/𐳭/g;s/𐲮/𐳮/g;s/𐲯/𐳯/g
s/𐲰/𐳰/g;s/𐲱/𐳱/g;s/𐲲/𐳲/g;s/𐵐/𐵰/g;s/𐵑/𐵱/g;s/𐵒/𐵲/g;s/𐵓/𐵳/g;s/𐵔/𐵴/g
s/𐵕/𐵵/g;s/𐵖/𐵶/g;s/𐵗/𐵷/g;s/𐵘/𐵸/g;s/𐵙/𐵹/g;s/𐵚/𐵺/g;s/𐵛/𐵻/g;s/𐵜/𐵼/g
s/𐵝/𐵽/g;s/𐵞/𐵾/g;s/𐵟/𐵿/g;s/𐵠/𐶀/g;s/𐵡/𐶁/g;s/𐵢/𐶂/g;s/𐵣/𐶃/g;s/𐵤/𐶄/g
s/𐵥/𐶅/g;s/𑢠/𑣀/g;s/𑢡/𑣁/g;s/𑢢/𑣂/g;s/𑢣/𑣃/g;s/𑢤/𑣄/g;s/𑢥/𑣅/g;s/𑢦/𑣆/g
s/𑢧/𑣇/g;s/𑢨/𑣈/g;s/𑢩/𑣉/g;s/𑢪/𑣊/g;s/𑢫/𑣋/g;s/𑢬/𑣌/g;s/𑢭/𑣍/g;s/𑢮/𑣎/g
s/𑢯/𑣏/g;s/𑢰/𑣐/g;s/𑢱/𑣑/g;s/𑢲/𑣒/g;s/𑢳/𑣓/g;s/𑢴/𑣔/g;s/𑢵/𑣕/g;s/𑢶/𑣖/g
s/𑢷/𑣗/g;s/𑢸/𑣘/g;s/𑢹/𑣙/g;s/𑢺/𑣚/g;s/𑢻/𑣛/g;s/𑢼/𑣜/g;s/𑢽/𑣝/g;s/𑢾/𑣞/g
s/𑢿/𑣟/g;s/𖹀/𖹠/g;s/𖹁/𖹡/g;s/𖹂/𖹢/g;s/𖹃/𖹣/g;s/𖹄/𖹤/g;s/𖹅/𖹥/g;s/𖹆/𖹦/g
s/𖹇/𖹧/g;s/𖹈/𖹨/g;s/𖹉/𖹩/g;s/𖹊/𖹪/g;s/𖹋/𖹫/g;s/𖹌/𖹬/g;s/𖹍/𖹭/g;s/𖹎/𖹮/g
s/𖹏/𖹯/g;s/𖹐/𖹰/g;s/𖹑/𖹱/g;s/𖹒/𖹲/g;s/𖹓/𖹳/g;s/𖹔/𖹴/g;s/𖹕/𖹵/g;s/𖹖/𖹶/g
s/𖹗/𖹷/g;s/𖹘/𖹸/g;s/𖹙/𖹹/g;s/𖹚/𖹺/g;s/𖹛/𖹻/g;s/𖹜/𖹼/g;s/𖹝/𖹽/g;s/𖹞/𖹾/g
s/𖹟/𖹿/g;s/𞤀/𞤢/g;s/𞤁/𞤣/g;s/𞤂/𞤤/g;s/𞤃/𞤥/g;s/𞤄/𞤦/g;s/𞤅/𞤧/g;s/𞤆/𞤨/g
s/𞤇/𞤩/g;s/𞤈/𞤪/g;s/𞤉/𞤫/g;s/𞤊/𞤬/g;s/𞤋/𞤭/g;s/𞤌/𞤮/g;s/𞤍/𞤯/g;s/𞤎/𞤰/g
s/𞤏/𞤱/g;s/𞤐/𞤲/g;s/𞤑/𞤳/g;s/𞤒/𞤴/g;s/𞤓/𞤵/g;s/𞤔/𞤶/g;s/𞤕/𞤷/g;s/𞤖/𞤸/g
s/𞤗/𞤹/g;s/𞤘/𞤺/g;s/𞤙/𞤻/g;s/𞤚/𞤼/g;s/𞤛/𞤽/g;s/𞤜/𞤾/g;s/𞤝/𞤿/g;s/𞤞/𞥀/g
s/𞤟/𞥁/g;s/𞤠/𞥂/g;s/𞤡/𞥃/g
'
# --- END GENERATED CASE FOLD ---
_case_fold() {   # $1 = a name
  printf '%s.' "$1" | LC_ALL=C sed -e "$_CASE_FOLD_SED"
}

# $1 split on '/' into the array named by $2. One splitter for both sides of
# every comparison below, so a candidate and an index entry are cut up the same
# way.
#
# The accumulator is `_sc_parts` and not `parts`, and the name is load-bearing:
# `local` is DYNAMICALLY scoped, so a local here whose name matches the array
# the caller asked to be filled shadows the caller's -- the eval writes to this
# function's own copy, the caller reads an empty array, and every component then
# defaults to "folds". Which is silent: the guard goes back to the path-wide
# answer and refuses exactly what it did before, so no test of the VERDICT can
# see it. Caught by the agreement table, which compares the vector itself.
_split_components() {   # $1 = a relative path   $2 = the array name to set
  local r=$1 _sc_comp
  local -a _sc_parts
  _sc_parts=()
  while [ -n "$r" ]; do
    _sc_comp=${r%%/*}
    if [ "$_sc_comp" = "$r" ]; then r=; else r=${r#*/}; fi
    if [ -n "$_sc_comp" ]; then _sc_parts+=("$_sc_comp"); fi
  done
  eval "$2=(\${_sc_parts[@]+\"\${_sc_parts[@]}\"})"
}

_FOLDS_VEC=()
_dest_folds_vector() {   # $1 = a path this script writes, relative to $DST_REAL
  local rel=${1:-} cur=$DST_REAL comp r here ans i n
  local LC_ALL=C
  local -a parts
  _FOLDS_VEC=()
  here=no
  if [ ! -e "$DST_REAL/.git" ] || [ "$DST_REAL/.git" -ef "$DST_REAL/.GIT" ]; then
    here=yes
  fi
  _split_components "$rel" parts
  n=${#parts[@]}
  i=0
  while [ "$i" -lt "$n" ]; do
    _FOLDS_VEC+=("$here")
    comp=${parts[$i]}
    if [ -d "$cur/$comp" ]; then
      cur=$cur/$comp
      ans=$(_dir_folds_case "$cur")
      case "$ans" in
        yes|unknown) here=yes ;;
        *)           here=no ;;
      esac
    fi
    # else: absent, or not a directory. Whatever the copies create there lands
    # on the filesystem $cur is already on, so $here carries over.
    i=$((i+1))
  done
}

# How an index entry ($2) relates to a path this script writes ($1), judged
# component by component against _FOLDS_VEC. Prints exactly one of:
#
#   spurious   some component differs ONLY by case where that component's own
#              parent does NOT fold -- git's path-wide ':(icase)' matched two
#              files that are two files (issue #231). Dropped.
#   match      every component is equal, or fold-equal in a directory that
#              folds -- the write lands on this entry (issue #230). Refused.
#   keep       neither: it differs some other way (unicode composition, which
#              core.precomposeUnicode folds on an axis of its own, or a glob),
#              so nothing here may drop it.
#
# An entry DEEPER than the candidate is judged on the components they share --
# writing into private/verify lands on private/verify/usage.csv too. An entry
# SHORTER than the candidate shares no such relationship and is kept.
# May the alias loop skip index entry $2 without calling _classify_alias on it?
# True (exit 0) = skip. The loop below runs over the WHOLE index of the
# destination and _classify_alias costs two forks per entry (`$( )` around it,
# `$( )` around each _case_fold), so this is what keeps that arithmetic down --
# fine for a repository this size, and the thing to look at first for a large
# one. Both skips are pure parameter expansion, and NEITHER MAY DROP AN ENTRY
# _classify_alias WOULD HAVE CALLED A MATCH, which is the whole contract:
#
#   DEPTH   a "match" needs the entry to be at least as deep as the path. The
#           fold never touches '/', so this holds whatever the table says.
#   LENGTH  fold-equal components have the same BYTE LENGTH -- but only while
#           every pair in the table encodes to the same length on both sides,
#           and the generated table has 26 pairs that do not (issue #233:
#           U+023A grows from two UTF-8 bytes to three, U+212A shrinks from
#           three to one). So the length skip is taken only where it is SOUND,
#           which is where BOTH first components are pure ASCII: no ASCII byte
#           appears in any multi-byte pair, so an all-ASCII component's folded
#           length is its own length. A component carrying any byte >= \200
#           falls through to the full comparison and pays the forks. In this
#           repository's own index that is no entries at all.
#
# A function rather than two `if`s inline so it can be lifted and exercised the
# way _classify_alias and _case_fold are -- the unsound version of the length
# skip is invisible to every fixture whose paths are ASCII, which is all of
# them. Called for its EXIT STATUS, so it costs no fork.
_gate_skips_entry() {   # $1 = a path relative to $DST_REAL   $2 = an index entry
  local rel=$1 entry=$2 reldepth entdepth relfirst entfirst
  # LC_ALL=C, because ${#var} counts CHARACTERS in a UTF-8 locale and BYTES in
  # this one, and the caller (_require_uncommittable) sets neither. The length
  # skip was sound under UTF-8 for a reason nobody wrote down -- one code point
  # folds to one code point, so a character count is fold-invariant -- and
  # unsound under C, where the 26 length-changing pairs move the count. Pinned
  # so the skip has ONE meaning and one argument for it, rather than two that
  # depend on the environment the staging happens to run in.
  local LC_ALL=C
  reldepth=${rel//[!\/]/}
  entdepth=${entry//[!\/]/}
  [ "${#entdepth}" -ge "${#reldepth}" ] || return 0
  relfirst=${rel%%/*}
  entfirst=${entry%%/*}
  if [ "${#entfirst}" -ne "${#relfirst}" ]; then
    case "$relfirst$entfirst" in
      *[$'\200'-$'\377']*) ;;
      *) return 0 ;;
    esac
  fi
  return 1
}

_classify_alias() {   # $1 = a path relative to $DST_REAL   $2 = an index entry
  local i=0 n c e cf ef out=match
  local LC_ALL=C
  local -a cp ep
  _split_components "$1" cp
  _split_components "$2" ep
  n=${#cp[@]}
  if [ "${#ep[@]}" -lt "$n" ]; then printf 'keep'; return 0; fi
  while [ "$i" -lt "$n" ]; do
    c=${cp[$i]}
    e=${ep[$i]}
    if [ "$c" != "$e" ]; then
      cf=$(_case_fold "$c")
      ef=$(_case_fold "$e")
      if [ "$cf" = "$ef" ]; then
        if [ "${_FOLDS_VEC[$i]:-yes}" != yes ]; then printf 'spurious'; return 0; fi
      else
        out=keep
      fi
    fi
    i=$((i+1))
  done
  printf '%s' "$out"
}

# ---------------------------------------------------------------------------
# THE ON-DISK SPELLING (issues #223, #224) -- which path a write to $1 actually
# reaches, resolved against the destination's filesystem instead of assumed from
# the bytes this script typed.
#
# WHY. Every question below is put to git, and git answers about the bytes it is
# handed; the filesystem does not. On a case-folding volume `private/1-raw-data`
# and `private/1-RAW-DATA` are ONE directory, and a rule naming one of them
# covers only that one once core.ignoreCase is false -- which is git's default,
# and therefore what "CONFIGURATION ISOLATION" above adopts from a destination
# that states nothing. Reproduced in a scratch repository whose .gitignore holds
# `private/` and which holds an UNTRACKED `Private/` on disk:
#
#   git check-ignore -q -- private/1-raw-data   ->  0   IGNORED
#   git check-ignore -q -- Private/1-raw-data   ->  1   NOT ignored
#   ls-files finds nothing under either spelling -- correctly, nothing is tracked
#   ... so the guard ACCEPTED, the copies ran, and git status then reported:
#       ?? Private/
#
# which is the 2026-08-13 incident exactly: the whole archive in a tree that does
# not ignore it, one `git add -A` from a commit.
#
# WHY NOT THE #204 REMEDY. That one re-asks `ls-files` with git's own ':(icase)'
# magic. `git check-ignore` REJECTS pathspec magic outright -- measured on git
# 2.50.1, ':(icase)private/1-raw-data' exits 128, "pathspec magic not supported
# by this command" -- so there is nothing to carry over. And ':(icase)' is
# BYTE-oriented even where it is accepted: it folds ASCII case pairs and no
# others, so a tracked `private/HOUSEHÖLD.yaml` goes unseen while
# `private/HOUSEHOLD.yaml` is found (measured side by side in one repository).
# core.precomposeUnicode is a different axis -- composition, not case.
#
# WHY NOT core.ignoreCase=true. It does make a destination answer the ASCII case
# correctly, and issue #193 decided deliberately to force `false` when the
# repository states none, so that an AMBIENT value cannot widen the
# destination's own rules. Turning it on here would reverse that decision
# sideways. It would also not be enough: with core.ignoreCase=true, and measured,
# `check-ignore private/vérify` exits 0 while `check-ignore private/VÉRIFY` exits
# 1 on a filesystem that resolves the two to one directory -- git's fold is ASCII
# too.
#
# SO THE FILESYSTEM IS ASKED INSTEAD, one component at a time. For the components
# that EXIST that folds exactly what the filesystem folds -- ASCII, non-ASCII,
# normalization, and whatever some later volume folds -- with no Unicode rules in
# this script or in private_egress.py, and it works for check-ignore, which takes
# no magic.
#
# THE PATH USUALLY DOES NOT EXIST YET, which is the point: the walk resolves as
# far as the path really goes and takes the REST exactly as asked, so
# `private/1-raw-data` in a tree holding only `Private/` resolves to
# `Private/1-raw-data`. An absent LEAF is asked about as typed, and that is the
# limit of what a filesystem can be asked rather than a gap: `[ -e ]` being false
# for the leaf IS the statement that the directory holds no entry this filesystem
# would call the same name. The tracked question for such a leaf is answered from
# the other side, by enumerating the index -- see the alias block above (issue
# #230).
#
# A COMPONENT'S REAL SPELLING is looked for BY NAME first and only then by
# `-ef` -- same device and inode, the shell's own stat comparison. By name first
# is correctness, not speed: two hard-linked entries share an inode, so an
# inode-first search could rename a component that is on disk under exactly the
# name it was asked about. Entries come from one glob per EXISTING component
# (LC_ALL=C so the order matches the sorted listing private_egress.py compares
# against), and the directories globbed here are the worktree root, private/,
# and nothing else -- tens of entries, not a tree walk.
#
# FAILS CLOSED, with its own refusal, when a component cannot be read: a
# directory with no read or no search permission, and a path that resolves while
# matching no entry of its own parent (a case-aliased dangling symlink, or a
# directory changing underneath the run). "Somewhere" is not a path to check.
# An ABSENT component is not that case -- it is the ordinary "this script will
# create it" -- and neither is a non-directory mid-path, which the DESTINATION
# PATH GUARD below refuses by name.
#
# Sets _ONDISK_REL rather than echoing, and that is load-bearing: _refuse exits,
# and an exit inside `$( )` kills only the subshell -- the caller would carry on
# with an empty answer and no refusal printed at all.
_ONDISK_REL=
_ondisk_spelling() {   # $1 = a path this script writes, relative to $DST_REAL
  local rel=$1 cur=$DST_REAL out= comp real entry name r i j n
  local LC_ALL=C
  local had_nullglob=0 had_dotglob=0
  local -a parts entries
  parts=()
  r=$rel
  while [ -n "$r" ]; do
    comp=${r%%/*}
    if [ "$comp" = "$r" ]; then r=; else r=${r#*/}; fi
    if [ -n "$comp" ]; then parts+=("$comp"); fi
  done
  if shopt -q nullglob; then had_nullglob=1; fi
  if shopt -q dotglob; then had_dotglob=1; fi
  shopt -s nullglob dotglob
  n=${#parts[@]}
  i=0
  while [ "$i" -lt "$n" ]; do
    if [ ! -d "$cur" ]; then break; fi          # nothing below a non-directory
    if [ ! -r "$cur" ] || [ ! -x "$cur" ]; then
      _refuse "the destination's on-disk spelling of a path this script writes could not be resolved" \
        "destination: $DST  (resolved: $DST_REAL)" \
        "path:        $rel" \
        "found:       $cur cannot be read and searched, so the entry it holds" \
        "             for '${parts[$i]}' cannot be found" \
        "expected:    a readable directory. On a filesystem that folds case or" \
        "             unicode composition the name a write lands on is not the" \
        "             name it was given, and the checks below ask git about the" \
        "             name it LANDS ON -- with that directory unreadable there" \
        "             is no such name to ask about." \
        "Fix the permissions on it, or name a destination this run can read."
    fi
    comp=${parts[$i]}
    entries=("$cur"/*)
    real=
    for entry in ${entries[@]+"${entries[@]}"}; do
      name=${entry##*/}
      if [ "$name" = "$comp" ]; then real=$comp; break; fi
    done
    if [ -z "$real" ]; then
      if [ -e "$cur/$comp" ] || [ -L "$cur/$comp" ]; then
        # It resolves under a spelling that is not the one asked for: the
        # filesystem folded it. Which entry did it fold to?
        for entry in ${entries[@]+"${entries[@]}"}; do
          if [ "$entry" -ef "$cur/$comp" ]; then real=${entry##*/}; break; fi
        done
        if [ -z "$real" ]; then
          _refuse "the destination's on-disk spelling of a path this script writes could not be resolved" \
            "destination: $DST  (resolved: $DST_REAL)" \
            "path:        $rel" \
            "found:       $cur/$comp resolves to something, and no entry of" \
            "             $cur is that same file" \
            "expected:    a component this script can name. A symbolic link with" \
            "             no target, or a directory changing underneath the run," \
            "             both look like this, and neither leaves a path the" \
            "             ignore check below could be asked about." \
            "'Probably somewhere' is not a property to write a private archive on."
        fi
      else
        break                                   # not there yet; the copies create it
      fi
    fi
    out=${out:+$out/}$real
    cur=$cur/$real
    i=$((i+1))
  done
  # Whatever the walk did not reach is taken exactly as asked.
  j=$i
  while [ "$j" -lt "$n" ]; do
    out=${out:+$out/}${parts[$j]}
    j=$((j+1))
  done
  if [ "$had_nullglob" = 0 ]; then shopt -u nullglob; fi
  if [ "$had_dotglob" = 0 ]; then shopt -u dotglob; fi
  _ONDISK_REL=$out
}

_require_uncommittable() {   # $1 = a path this script writes, relative to $DST_REAL
  local rel=$1 rc=0 arc=0 orc=0 irc=0 tracked aliased aliasspec ondiskspec folds ondisk
  local entry vecfolds
  local -a raw idx
  raw=()
  idx=()
  _require_isolation_proven
  # WHICH PATH THE WRITE ACTUALLY REACHES, before any of the three questions is
  # asked about it (issues #223, #224). Identical to $rel on every ordinary
  # destination; different exactly where the filesystem folds a spelling the
  # destination's own rules do not.
  _ondisk_spelling "$rel"
  ondisk=$_ONDISK_REL
  # TRACKED first, and the order is the whole point. check-ignore consults the
  # index, so a path that is tracked-though-ignored reports rc 1 -- measured on
  # a fixture whose .gitignore holds `private/` and which force-added
  # private/household.yaml and private/verify/usage.csv: check-ignore returned
  # 1 for both, and for the private/verify DIRECTORY around the tracked file,
  # while private/1-raw-data returned 0. Asking check-ignore first would
  # therefore answer "your .gitignore does not cover this" for a file whose
  # .gitignore covers it perfectly well and which is committed anyway, sending
  # the operator to edit the wrong file.
  if ! tracked=$(_git -C "$DST_REAL" ls-files -- "$rel"); then
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
  # AND AGAIN UNDER THE FILESYSTEM'S OWN EQUIVALENCE (issue #204). The question
  # above is answered BY THE BYTES; this one asks which index entries the
  # filesystem holding the destination resolves to this same file: ASCII case
  # and unicode composition through the two pathspecs below, whatever the
  # filesystem folds for a name that EXISTS through the on-disk walk, and -- for
  # a name that does not exist under either spelling -- the index enumeration
  # further down (issue #230). ':(icase)'
  # only where case really folds there -- see _dest_folds_case, and see
  # _GIT_ALIAS_OVERRIDE for the measurement and for what forcing core.ignoreCase
  # was measured to change, which is nothing. ADDITIVE, and asked second, so a
  # path tracked under its own name still reports the plain fact.
  ondiskspec=
  if _dest_folds_case "$rel"; then
    folds=$_FOLDS_WHERE
    aliasspec=":(icase)$rel"
    if [ "$ondisk" != "$rel" ]; then ondiskspec=":(icase)$ondisk"; fi
  else
    folds=$_FOLDS_WHERE
    aliasspec="$rel"
    if [ "$ondisk" != "$rel" ]; then ondiskspec="$ondisk"; fi
  fi
  _GIT_ALIAS_CONFIG=(${_GIT_ALIAS_OVERRIDE[@]+"${_GIT_ALIAS_OVERRIDE[@]}"})
  arc=0
  # AND THE ANSWER PER COMPONENT (issue #231), which the pathspec cannot carry:
  # ':(icase)' is applied path-wide, so a folding ANCESTOR turns the fold on for
  # every component below it. Each entry git returns is filtered against
  # _FOLDS_VEC before it is allowed to refuse anything.
  _dest_folds_vector "$rel"
  # TWO PATHSPECS where the filesystem spells this path differently (issue
  # #224), because ':(icase)' folds ASCII case and the filesystem folds more
  # than that: the on-disk name is asked about literally, which is what catches
  # a tracked entry whose name carries a non-ASCII cased character. One call --
  # `ls-files` unions the pathspecs and prints whichever matched. -z, because
  # these names are split on '/' below and git QUOTES a non-ASCII path in its
  # default output; the rc is carried back through the same stream, since `$( )`
  # drops NUL bytes and a pipeline would run the loop in a subshell where
  # _refuse could not exit the script.
  raw=()
  if [ -z "$ondiskspec" ]; then
    while IFS= read -r -d '' entry; do raw+=("$entry"); done \
      < <(_git -C "$DST_REAL" ls-files -z -- "$aliasspec"; printf 'rc=%s\0' "$?")
  else
    while IFS= read -r -d '' entry; do raw+=("$entry"); done \
      < <(_git -C "$DST_REAL" ls-files -z -- "$aliasspec" "$ondiskspec"; printf 'rc=%s\0' "$?")
  fi
  # The trailer is always written, so an EMPTY array means the redirection
  # itself never ran -- unanswered, which is refused rather than read as "the
  # index holds nothing".
  if [ "${#raw[@]}" -eq 0 ]; then arc=127; else
    arc=${raw[$((${#raw[@]}-1))]#rc=}
    unset "raw[$((${#raw[@]}-1))]"
  fi
  _GIT_ALIAS_CONFIG=()
  if [ "$arc" -ne 0 ]; then
    _refuse "the destination could not be asked which paths it tracks" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $rel" \
      "asked as:    $aliasspec" \
      "found:       'git ls-files' failed on the ALIAS question" \
      "expected:    a listing, so a path already COMMITTED there under a" \
      "             spelling this filesystem treats as the same file can be" \
      "             told from one that is genuinely absent"
  fi
  # KEEP an entry only where some spelling this script asked about really does
  # alias it AT EVERY COMPONENT (issue #231) ...
  aliased=
  for entry in ${raw[@]+"${raw[@]}"}; do
    if [ "$(_classify_alias "$rel" "$entry")" != spurious ]; then
      aliased=${aliased:+$aliased }$entry
      continue
    fi
    if [ -n "$ondiskspec" ] && [ "$(_classify_alias "$ondisk" "$entry")" != spurious ]; then
      aliased=${aliased:+$aliased }$entry
    fi
  done
  # ... AND ASK THE INDEX ITSELF for the fold neither pathspec can express
  # (issue #230): a tracked entry with no working-tree file, differing from the
  # path only in NON-ASCII case, is invisible to ':(icase)' -- which folds ASCII
  # bytes -- and has nothing on disk for _ondisk_spelling to resolve. The index
  # is the one side that does hold something to compare against, so it is
  # enumerated and compared under the generated fold, gated per component. Skipped
  # outright where no component of this path can fold at all.
  vecfolds=no
  for entry in ${_FOLDS_VEC[@]+"${_FOLDS_VEC[@]}"}; do
    if [ "$entry" = yes ]; then vecfolds=yes; fi
  done
  if [ "$vecfolds" = yes ]; then
    irc=0
    idx=()
    while IFS= read -r -d '' entry; do idx+=("$entry"); done \
      < <(_git -C "$DST_REAL" ls-files -z; printf 'rc=%s\0' "$?")
    if [ "${#idx[@]}" -eq 0 ]; then irc=127; else
      irc=${idx[$((${#idx[@]}-1))]#rc=}
      unset "idx[$((${#idx[@]}-1))]"
    fi
    if [ "$irc" -ne 0 ]; then
      _refuse "the destination could not be asked which paths it tracks" \
        "destination: $DST  (resolved: $DST_REAL)" \
        "path:        $rel" \
        "found:       'git ls-files' could not list the index" \
        "expected:    a listing, so a path already COMMITTED there under a name" \
        "             this filesystem folds to the one about to be created can" \
        "             be told from one that is genuinely absent"
    fi
    # A CHEAP GATE FIRST -- see _gate_skips_entry above for what it may skip
    # and why each skip is sound.
    for entry in ${idx[@]+"${idx[@]}"}; do
      if _gate_skips_entry "$rel" "$entry"; then continue; fi
      # Bounded on BOTH sides, so an entry that is a suffix of one already
      # listed ('README.md' beside 'private/README.md') is not read as a
      # duplicate and dropped from the refusal's evidence.
      case " $aliased " in *" $entry "*) continue ;; esac
      if [ "$(_classify_alias "$rel" "$entry")" = match ]; then
        aliased=${aliased:+$aliased }$entry
      elif [ -n "$ondiskspec" ] && [ "$(_classify_alias "$ondisk" "$entry")" = match ]; then
        aliased=${aliased:+$aliased }$entry
      fi
    done
  fi
  [ -z "$aliased" ] || _refuse "the destination TRACKS a path this script writes" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "path:        $rel" \
    "tracked:     $(echo $aliased)" \
    "asked as:    $aliasspec${ondiskspec:+ $ondiskspec}  (with core.precomposeUnicode=true)" \
    "on disk:     $ondisk" \
    "case folds:  $folds" \
    "expected:    an untracked path. Nothing is committed under this exact" \
    "             name, but this filesystem resolves the name above to one" \
    "             that is: the copy would land on a committed file under a" \
    "             spelling neither question before this one was asked about."
  # THE IGNORE QUESTION, asked of the path the bytes actually reach (issue
  # #223). On every ordinary destination $ondisk IS $rel and this is the single
  # call it has always been.
  _git -C "$DST_REAL" check-ignore -q -- "$ondisk" || orc=$?
  case "$orc" in
    0) ;;
    1) if [ "$ondisk" != "$rel" ]; then
         _refuse "the destination does not gitignore the path a write to it actually reaches" \
           "destination: $DST  (resolved: $DST_REAL)" \
           "path:        $rel" \
           "on disk:     $ondisk" \
           "found:       the rule covers '$rel' as spelled, and the write does" \
           "             not go there: this filesystem already holds the path" \
           "             as '$ondisk', and 'git check-ignore' reports THAT" \
           "             spelling is NOT ignored" \
           "expected:    a rule covering the name the copies land on. core.ignoreCase" \
           "             is what the DESTINATION states (git's default, false, when" \
           "             it states none) and git's own fold is ASCII-only, so a rule" \
           "             covers only the spelling it is written in." \
           "This is the 2026-08-13 incident shape: the whole archive in a tree" \
           "that does not ignore it, one 'git add -A' from a public commit." \
           "Two remedies, both inside the destination: give its .gitignore or" \
           "its .git/info/exclude a rule for '$ondisk', or rename that" \
           "directory to '$rel' so the rule it already has covers it."
       fi
       _refuse "the destination does not gitignore a path this script writes" \
         "destination: $DST  (resolved: $DST_REAL)" \
         "path:        $rel" \
         "found:       'git check-ignore' reports it is NOT ignored there" \
         "expected:    a path that working tree's own git refuses to offer to" \
         "             'git add', so this household's archive cannot be" \
         "             committed from it" \
         "This is the half of the 2026-08-13 incident a repository check cannot" \
         "see: the destination that took the archive did not ignore private/," \
         "which is why the data sat one 'git add -A' from a public commit." \
         "Add the rule to that working tree's .gitignore and re-run. A GLOBAL" \
         "excludes file does not count and is not consulted here (issue #193):" \
         "the question is whether the REPOSITORY refuses the path, not whether" \
         "this machine happens to. .gitignore or .git/info/exclude, in the" \
         "destination itself, are the two places that answer it." ;;
    *) _refuse "the destination could not say whether it ignores a path this script writes" \
         "destination: $DST  (resolved: $DST_REAL)" \
         "path:        $ondisk" \
         "found:       'git check-ignore' exited $orc -- neither 'ignored' (0)" \
         "             nor 'not ignored' (1), so the question went unanswered" \
         "expected:    an answerable question. 'Probably ignored' is not a" \
         "             property to write a private archive on." ;;
  esac
  # The on-disk spelling is ignored. Where it is not the spelling this script
  # asked about, the TYPED one is asked as well -- one extra call, only on the
  # paths whose two spellings differ, and it is what keeps this change from
  # being the one that makes the guard ACCEPT something it used to refuse.
  if [ "$ondisk" != "$rel" ]; then
    _git -C "$DST_REAL" check-ignore -q -- "$rel" || rc=$?
    case "$rc" in
      0) ;;
      1) _refuse "the destination does not gitignore a path this script writes" \
           "destination: $DST  (resolved: $DST_REAL)" \
           "path:        $rel" \
           "on disk:     $ondisk" \
           "found:       the on-disk spelling is ignored and '$rel' is not." \
           "             Both are refused rather than the difference being" \
           "             resolved silently: the two are one file here, and a" \
           "             rule covering only one of them stops covering this" \
           "             archive the moment the directory is recreated under" \
           "             the other." \
           "expected:    a rule covering both spellings. Add the missing one to" \
           "             that working tree's .gitignore or .git/info/exclude." ;;
      *) _refuse "the destination could not say whether it ignores a path this script writes" \
           "destination: $DST  (resolved: $DST_REAL)" \
           "path:        $rel" \
           "found:       'git check-ignore' exited $rc -- neither 'ignored' (0)" \
           "             nor 'not ignored' (1), so the question went unanswered" \
           "expected:    an answerable question. 'Probably ignored' is not a" \
           "             property to write a private archive on." ;;
    esac
  fi
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
#
# AND NONE CONSULTS THE CWD EITHER (issue #192), which is a separate property
# and was the one this block got wrong. household.py resolves its repo root
# from Path.cwd() BEFORE its own __file__, so `import household` inside the
# child below answered with whichever checkout the OPERATOR happened to be
# standing in. Reproduced both ways: a correct has_gas:false source run from
# inside this has_gas:true checkout was refused for gas bills it is right not
# to have, and a has_gas:true source whose gas-bills/ had not been pulled was
# ACCEPTED and staged incomplete when the operator stood in a has_gas:false
# checkout. The first is a guard failing on correct input -- the shape that
# gets guards bypassed -- and the second is the guard not running at all.
#
# The remedy is local to this caller: household.py's CWD-first order is
# deliberate (dry_run.py documents dropping the CWD probe on purpose, and the
# private/verify sandbox depends on it), so it is not changed. Instead the
# child is POINTED at the source root explicitly, below.
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
#
# WHICH household.yaml (issue #192). The module's own repo-root walk is not
# asked -- it answers from the CWD first, which is the operator's checkout and
# not the source. hh.PATH, the single global hh._load() reads the file from, is
# reassigned to the source named on the command line before get() is called, so
# the answer is a property of $SRC and of nothing else. hh.ROOT goes with it so
# the module stays self-consistent for anything a later household.py derives
# from it. Import-time resolution still runs and may land anywhere; it decides
# nothing here, because the only thing read afterwards is the reassigned PATH.
# A missing source household.yaml still raises household.py's own SystemExit --
# a non-zero exit into the refusal below -- so the fail-closed path is the
# module's, unchanged.
#
# $SRC reaches the child through the ENVIRONMENT rather than being interpolated
# into the -c program: a source path containing a quote, a backslash or a
# newline would otherwise be read as Python source instead of as data.
if ! HAS_GAS=$(PYTHONPATH="$SRC/analysis" STAGE_SRC_ROOT="$SRC" \
  "$SRC/.venv/bin/python" -c \
  "import os, pathlib, household as hh
_src = pathlib.Path(os.environ['STAGE_SRC_ROOT'])
hh.ROOT = _src
hh.PATH = _src / 'private' / 'household.yaml'
print(hh.get('household.has_gas'))"); then
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

# ---------------------------------------------------------------------------
# SOURCE COMPLETENESS (issue #185) -- every input the copies READ, asked for
# here, before the first byte.
#
# The whole-script invariant is "either this script writes nothing, or it
# completes". The two source checks above hold up their end; the copies did
# not. `set -e` aborts on the first `cp` that cannot find its source, so a
# source missing gas.csv exited 1 having ALREADY written household.yaml and the
# interval export -- a half-updated archive with nothing in the output saying
# so, which is the same failure the hoist above was for, arriving one step
# later. A missing input is the ordinary way a run fails part-way (an archive
# still being assembled, a pull that stopped early), and it is decidable
# up front, so it is decided up front.
#
# THE COUNT MATTERS for the interval export, not just the presence: the
# private/verify/usage.csv copy takes exactly ONE file, so two matching exports
# in the source make `cp` fail with "target is not a directory" -- after six
# earlier copies have landed. A source holding last year's export beside this
# year's is the shape that produces it, and this run cannot pick between them.
#
# NOT covered here: an I/O error, a full filesystem or a revoked permission
# DURING a copy. Those are not decidable up front, so they are handled where
# they happen -- see STAGED COPY below, which copies beside the target and
# renames into place only after every copy has landed.
# ---------------------------------------------------------------------------
_missing_src=()
for _need in "private/1-raw-data/gas.csv" \
             "private/1-raw-data/electric_billing_history_2024-2026.csv" \
             "private/1-raw-data/electric-bills" \
             "private/1-raw-data/enphase_sam8760_2025.csv" \
             "private/1-raw-data/enphase_sam8760_2026.csv"; do
  if [ ! -e "$SRC/$_need" ]; then
    _missing_src[${#_missing_src[@]}]="  $_need"
  fi
done
_n_interval=0
for _f in "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv; do
  if [ -e "$_f" ]; then _n_interval=$((_n_interval + 1)); fi
done
if [ "$_n_interval" -eq 0 ]; then
  _missing_src[${#_missing_src[@]}]="  private/1-raw-data/Electric_15_Minute_*.csv"
fi
if [ ${#_missing_src[@]} -ne 0 ]; then
  _refuse "the source is missing inputs the copies read" \
    "source:      $SRC" \
    "missing:     ${#_missing_src[@]} path(s), relative to the source root:" \
    "${_missing_src[@]}" \
    "expected:    every input listed at the top of this file -- a copy that" \
    "             cannot find its source aborts the run part-way, leaving the" \
    "             destination half-updated and saying nothing about it"
fi
if [ "$_n_interval" -gt 1 ]; then
  _refuse "the source holds more than one Green Button interval export" \
    "source:      $SRC/private/1-raw-data" \
    "found:       $_n_interval files matching Electric_15_Minute_*.csv" \
    "expected:    exactly one -- private/verify/usage.csv is a copy of THE" \
    "             interval export, and this script cannot choose between two." \
    "Move the superseded export out of private/1-raw-data (the archive keeps" \
    "them under superseded/) and re-run."
fi

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
# REFUSE, and do not rely on the rename below to make it moot. Since issue
# #214 every copy lands in a staging directory beside its target and is
# renamed into place (STAGED COPY below), so `cp` never opens a destination
# inode at all and a shared one is not written through. The guard is kept as
# it was: it is the check that makes the claim, the rename is the mechanism
# that happens to honour it, and a later edit that copies straight to the
# target again would reopen the hazard with the guard the only thing left
# standing. Refusing is also the same answer this guard already gives
# symbolic links one paragraph up.
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

# ---------------------------------------------------------------------------
# DESTINATION STALENESS GUARD (issue #185) -- the guards above decide whether
# the destination may be written to. This one decides whether what is ALREADY
# there is this source's.
#
# Every copy in this file OVERLAYS. `cp` replaces the names it is handed and
# leaves every other name alone, and `cp -R dir dest/` merges into an existing
# dir/ rather than replacing it. So a destination staged from one archive and
# re-staged from another ends up holding the UNION of both, at exit 0, with the
# closing message counting the union as staged. Reproduced: two synthetic
# households, the second lacking four of the first's files, and afterwards the
# destination held all of them -- the first household's interval export, its
# two bill PDFs and its CAISO day-cache sitting beside the second's.
#
# WHY THAT IS NOT MERELY UNTIDY. Downstream reads by GLOB --
# private/1-raw-data/Electric_15_Minute_*.csv, enphase_sam8760_*.csv,
# electric-bills/*.pdf, the CAISO day-cache -- so a leftover is CONSUMED, not
# ignored. The pipeline notices nothing, and the §9 regeneration gate
# reproduces the resulting artifact byte-identically, because the inputs are
# consistent with themselves. And this repo is meant to be forked: someone
# staging their own archive over a directory that once held another
# household's keeps whatever their own source lacks, while the success line
# tells them everything is staged. That is the reverse of the exposure the
# destination guards were written for -- not our data written into someone
# else's tree, but our data left in a tree someone else is now using.
#
# WHAT COUNTS AS STALE, stated so the check is decidable rather than
# atmospheric: a destination path this script's copies WOULD write into the
# neighbourhood of, which THIS SOURCE does not supply. Three subtrees compared
# entry by entry against the source's (electric-bills/, gas-bills/,
# caiso_raw/), and the two globbed top-level names compared by basename. The
# named single files -- household.yaml, gas.csv, the billing-history export,
# usage.csv, samA.csv, samB.csv -- are overwritten by every run and so can
# never be stale.
#
# ALL THREE SUBTREES ARE COMPARED, including the ones this run will not write.
# That is the opposite scoping from the link scans above, and deliberately: a
# gas-bills/ in a destination being re-staged from a has_gas:false source is
# not a subtree this run touches, it is the PREVIOUS household's gas bills, and
# it is the single clearest instance of what this guard is for. The link scans
# skip an unwritten subtree because `cp` never opens it; this one reads it
# because nothing will ever replace it.
#
# IT REFUSES; IT DOES NOT DELETE. The alternative shape -- clear the managed
# directories first, or swap whole directories out -- puts an rm -rf of a
# destination directory in this file, and the destination this script accepts
# is not always a scratch worktree: a main checkout is a registered worktree
# too, and staging into one is a documented flow (README, "Refreshing this
# analysis"). On that destination private/1-raw-data IS the irreplaceable raw
# archive, and a bug in the enumeration below would delete bill PDFs that exist
# nowhere else. A wrong refusal costs a re-run; a wrong deletion costs the
# archive. So the destructive step stays OUT of this script and with the
# operator, who gets the exact list of paths and can delete them, or stage into
# a fresh clone instead. The trade this accepts, named so it is a decision: a
# legitimate re-stage after the source's file names change costs one manual
# step.
#
# AND IT IS WHAT LETS THE COPY BE ATOMIC WITHOUT ONE (issue #214). Once this
# guard has passed, every name in the destination's managed paths is a name
# this source supplies, so the source's exact set is reached by REPLACING names
# one by one -- never by removing any. STAGED COPY below copies into a
# directory beside each target and renames each copy over its target; a rename
# replaces exactly the entry `cp` used to overwrite, and no other. The one
# removal this script performs is of its own staging directory: created by
# this run with a bare mkdir that fails if the name exists (_create_staging_dir,
# which records the name only after that mkdir succeeded), so nothing that
# was in the destination before the run can be under it, and fenced to that
# name and nothing else in _discard_staging. Nothing that existed before the
# run started is ever deleted by it, on any path.
#
# The list is printed IN FULL rather than counted, because "delete these and
# re-run" is only actionable if the operator can see what "these" are.
# ---------------------------------------------------------------------------
_stale_paths=()

_note_stale() { _stale_paths[${#_stale_paths[@]}]="$1"; }

_collect_stale_under() {   # $1 = a subtree name under private/1-raw-data/
  local sub="$1" dstdir srcdir listing rel
  dstdir="$DST_REAL/private/1-raw-data/$sub"
  srcdir="$SRC/private/1-raw-data/$sub"
  # A LINK here is stale whole. The written subtrees never reach this: a link
  # at one of them is refused by _check_dir_slot above. An UNwritten one does,
  # and `find` does not descend a symbolic link given on its command line, so
  # without this the leftover would be invisible to the walk below.
  if [ -L "$dstdir" ]; then
    _note_stale "private/1-raw-data/$sub  (a symbolic link)"
    return 0
  fi
  [ -d "$dstdir" ] || return 0
  # A subtree the source does not have AT ALL is reported whole, rather than
  # file by file: "delete private/1-raw-data/gas-bills" is a remedy the
  # operator can carry out in one step, and it leaves no empty directory
  # behind for the next run to wonder about. This is the previous household's
  # gas bills, in a destination now being staged from a has_gas:false source.
  if [ ! -d "$srcdir" ]; then
    _note_stale "private/1-raw-data/$sub  (the whole directory -- this source has none)"
    return 0
  fi
  if ! listing=$(find "$dstdir" -print); then
    _refuse "the destination could not be scanned for stale files" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $dstdir" \
      "expected:    a readable directory tree, so this run can tell what in it" \
      "             came from this source and what did not"
  fi
  while IFS= read -r _p; do
    [ -n "$_p" ] || continue
    [ "$_p" != "$dstdir" ] || continue
    # A name containing a newline arrives here as two records, neither of which
    # names a path that exists. Refuse rather than guess: a fragment tested
    # against the source would report a file as stale that is not, and skipping
    # it would report a tree as clean that this walk could not read.
    if [ ! -e "$_p" ] && [ ! -L "$_p" ]; then
      _refuse "a destination path could not be read back from the scan" \
        "destination: $DST  (resolved: $DST_REAL)" \
        "path:        $dstdir" \
        "found:       a listing entry naming nothing on disk, which is what a" \
        "             file name containing a newline looks like from here" \
        "expected:    file names this scan can compare against the source one" \
        "             per line, so a stale file cannot hide in a split record"
    fi
    rel=${_p#"$dstdir/"}
    if [ ! -e "$srcdir/$rel" ] && [ ! -L "$srcdir/$rel" ]; then
      _note_stale "private/1-raw-data/$sub/$rel"
    fi
  done <<< "$listing"
}

_collect_stale_glob() {   # $1 = a glob the copies write into private/1-raw-data/
  local pat="$1" base
  for _g in "$DST_REAL/private/1-raw-data/"$pat; do
    if [ -e "$_g" ] || [ -L "$_g" ]; then
      base=$(basename -- "$_g")
      if [ ! -e "$SRC/private/1-raw-data/$base" ] && \
         [ ! -L "$SRC/private/1-raw-data/$base" ]; then
        _note_stale "private/1-raw-data/$base"
      fi
    fi
  done
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

# The staleness comparison (issue #185), LAST of the destination checks and
# still before the first write. Last because the three scans above describe
# ways the archive escapes the tree, this one describes a tree holding the
# wrong household's files, and when a destination is both the operator should
# be told about the escape route first.
for _sub in electric-bills gas-bills caiso_raw; do
  _collect_stale_under "$_sub"
done
_collect_stale_glob "Electric_15_Minute_*.csv"
_collect_stale_glob "enphase_sam8760_*.csv"
if [ ${#_stale_paths[@]} -ne 0 ]; then
  _stale_lines=()
  for _s in "${_stale_paths[@]}"; do
    _stale_lines[${#_stale_lines[@]}]="  $_s"
  done
  _refuse "the destination holds staged files this source does not supply" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "found:       ${#_stale_paths[@]} path(s) left by an earlier stage, relative" \
    "             to the destination root:" \
    "${_stale_lines[@]}" \
    "expected:    a destination holding only what this source supplies. Every" \
    "             copy below overlays -- it replaces the names it writes and" \
    "             leaves the rest -- so these would survive this run and be" \
    "             read by the pipeline's globs afterwards, beside the files" \
    "             that replace them. If they came from another household, they" \
    "             would also still be here after that household was gone." \
    "This script deletes nothing it did not create in the same run: the destination" \
    "may be a main checkout whose private/1-raw-data is the only copy of the raw" \
    "archive, and a deletion there is not recoverable by re-running. Delete the" \
    "paths listed above yourself and re-run, or stage into a fresh clone."
fi

# ---------------------------------------------------------------------------
# STAGED COPY (issue #214) -- the copies land beside their targets and are
# renamed into place only after every one of them has landed.
#
# Every guard above is decided before the first byte, and SOURCE COMPLETENESS
# settles the ordinary way a run fails part-way. What none of them can decide
# is a failure DURING a copy: an I/O error, a disk that fills, a permission
# revoked mid-run. With the copies written straight to their targets, `set -e`
# aborted on the failing `cp` and whatever had landed before it stayed --
# reproduced with the fourth copy failing: household.yaml, the interval export
# and both SAM files already in the destination, gas.csv truncated, exit 1,
# and no line saying which half was new. The whole-script invariant this file
# states for itself -- either it writes nothing, or it completes -- did not
# hold for that class.
#
# THE SHAPE: a .staging-<pid> directory INSIDE each of the three directories
# the copies write (private/, private/1-raw-data/, private/verify/), every
# copy made into it, and then each staged entry renamed over its target.
# Beside the target rather than in one place, so each rename stays inside the
# directory tree it lands in: rename(2) is atomic on one filesystem and `mv`
# falls back to a copy across two, and a volume mounted at one of the three
# directories is a shape this file already measures for (issue #231).
#
# WHAT THIS BUYS. A failure in the COPY phase leaves the archive byte-identical:
# nothing in it has been touched, and the staging directory is removed. A
# failure in the RENAME phase is narrower than a copy failure -- each rename
# moves a whole, finished file, so no file is ever truncated in place -- but a
# rename can still fail, and then the run says exactly which paths already
# hold the new copy and that every other path is as it was. Never "nothing
# happened" when something did (issue #214's second criterion).
#
# WHY NO DELETE IS NEEDED, and what the one removal is. The DESTINATION
# STALENESS GUARD has already established that every name under the managed
# paths is one this source supplies, so replacing names one by one reaches the
# source's exact set; a rename over an existing file replaces precisely the
# entry the old `cp` overwrote. A directory the destination lacks is renamed
# in whole; one it already has is merged entry by entry, recursively, and the
# emptied staged directory is rmdir'd -- which can remove nothing that holds
# data. The only recursive removal in this file is _discard_staging, of the
# staging directory this run created: _create_staging_dir's bare mkdir fails
# if the name exists, and records the name only after it succeeded, so nothing
# pre-existing can be under it; and the removal is fenced to those three
# names. A run killed outright (SIGKILL, power loss)
# cannot run it, so the next run refuses on a leftover .staging-* and names
# it, above, rather than deleting what it did not create.
#
# STAYS INSIDE THE GUARDS: the staging directories are asked the same three
# ignore questions as the managed paths (see the loop after the declared
# _require_uncommittable calls), are created with the same non-following
# _ensure_contained_dir, and hold nothing that is not on its way into a path
# every guard above already accepted.
# ---------------------------------------------------------------------------
_STAGING_NAME=".staging-$$"
_STAGING_SLOTS=("$DST_REAL/private" "$DST_REAL/private/1-raw-data" "$DST_REAL/private/verify")

# The staging directories are asked the same three ignore questions as the
# declared managed paths (DESTINATION IGNORE GUARD). Two of them sit under a
# directory that verdict already settles whole; the third,
# private/.staging-<pid>, does not -- private/ holds the committed README and
# is never asked about as a directory -- and it is where the intake file waits
# before its rename. All three are asked, because a temporary copy of the
# archive is the archive. Asked HERE, after _check_dir_slot has refused a link
# at any of the three slots: git will not answer for a path beyond a symbolic
# link, and the refusal for a planted link should name the link. Derived names
# rather than declared managed paths, which is why they are asked in a loop:
# the three column-0 declarations are what analysis/test_private_egress.py
# reads as the write set.
for _slot in private private/1-raw-data private/verify; do
  _require_uncommittable "$_slot/$_STAGING_NAME"
done

# A staging directory left by a run this script could not finish cleaning up
# is refused rather than reused or removed: it holds a partial copy of some
# household's archive, and this script deletes nothing it did not create in
# the same run. Before the first write, like every refusal.
_leftovers=()
for _slot in "${_STAGING_SLOTS[@]}"; do
  for _left in "$_slot"/.staging-*; do
    if [ -e "$_left" ] || [ -L "$_left" ]; then
      _leftovers[${#_leftovers[@]}]="  ${_left#"$DST_REAL/"}"
    fi
  done
done
if [ ${#_leftovers[@]} -ne 0 ]; then
  _refuse "the destination holds a staging directory left by an interrupted run" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "found:       ${#_leftovers[@]} path(s), relative to the destination root:" \
    "${_leftovers[@]}" \
    "expected:    none. This script copies into a .staging-<pid> directory beside" \
    "             each target and renames the copies into place; a run that was" \
    "             killed could not remove its own, and what is in there is a" \
    "             partial copy of some household's archive." \
    "This script deletes nothing it did not create in the same run. Inspect and" \
    "delete the paths listed above yourself, then re-run."
fi

# After the first write nothing is a refusal: the message says what happened.
_fail() {   # $1 = headline, remaining args = detail lines.
  echo "stage-private-data.sh: FAILED -- $1" >&2
  shift
  for _line in "$@"; do echo "  $_line" >&2; done
  exit 1
}

# The staging directories THIS run created, appended only after their mkdir
# succeeded; the one list _discard_staging will remove from.
_STAGING_CREATED=()
_STAGING_REPORT=()
_discard_staging() {   # remove this run's own staging directories; fills _STAGING_REPORT
  local d
  _STAGING_REPORT=()
  for d in ${_STAGING_CREATED[@]+"${_STAGING_CREATED[@]}"}; do
    if [ ! -e "$d" ] && [ ! -L "$d" ]; then continue; fi
    # Fenced to the three names this run stages under, whatever the list holds:
    # this is the only recursive removal in the file, and it can name nothing
    # else.
    case "$d" in
      "$DST_REAL/private/$_STAGING_NAME"|"$DST_REAL/private/1-raw-data/$_STAGING_NAME"|"$DST_REAL/private/verify/$_STAGING_NAME") ;;
      *) _STAGING_REPORT+=("NOT removed: $d -- not a staging directory of this run"); continue ;;
    esac
    if [ -L "$d" ] || [ ! -d "$d" ] || [ "$(_physical "$d" || true)" != "$d" ]; then
      _STAGING_REPORT+=("NOT removed: ${d#"$DST_REAL/"} -- no longer the directory this run created")
      continue
    fi
    if rm -rf -- "$d" 2>/dev/null && [ ! -e "$d" ]; then
      _STAGING_REPORT+=("removed:     ${d#"$DST_REAL/"}  (this run's staging copy)")
    else
      _STAGING_REPORT+=("NOT removed: ${d#"$DST_REAL/"} -- it holds a partial copy; delete it yourself")
    fi
  done
  _STAGING_CREATED=()
  # And the managed directories this run itself created, if the failure left
  # them empty. rmdir removes nothing that holds data, so a directory the
  # rename phase already filled stays, as the report above says it does.
  # Deepest first, so private/ can go after its children have.
  local i=${#_SLOTS_CREATED[@]}
  while [ "$i" -gt 0 ]; do
    i=$((i - 1))
    d=${_SLOTS_CREATED[$i]}
    if [ -d "$d" ] && [ ! -L "$d" ]; then rmdir -- "$d" 2>/dev/null || true; fi
  done
  _SLOTS_CREATED=()
}

_copy_failed() {   # $1 = what was being copied, relative to the source root
  _discard_staging
  _fail "a copy into the staging directory failed" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "copying:     $1" \
    "found:       cp exited non-zero; its own message is above" \
    "The archive is unchanged: every copy lands in a $_STAGING_NAME directory" \
    "beside its target, and nothing is renamed into place until all of them" \
    "have landed." \
    ${_STAGING_REPORT[@]+"${_STAGING_REPORT[@]}"} \
    "Fix what cp reported (a full disk, a permission, an unreadable source" \
    "file) and re-run."
}

_swapped=()
_swap_failed() {   # $1 = what was being renamed, $2 = onto what, $3 = what mv said
  local -a lines
  local p
  lines=()
  for p in ${_swapped[@]+"${_swapped[@]}"}; do
    lines[${#lines[@]}]="  ${p#"$DST_REAL/"}"
  done
  _discard_staging
  _fail "a staged copy could not be renamed into place" \
    "destination: $DST  (resolved: $DST_REAL)" \
    "renaming:    $1" \
    "onto:        $2" \
    "mv said:     ${3:-(nothing)}" \
    "in place:    ${#_swapped[@]} path(s) now hold this source's copy, whole -- new" \
    "             where the destination had none, replaced where it had one --" \
    "             relative to the destination root:" \
    ${lines[@]+"${lines[@]}"} \
    "every other path this script writes still holds what it held before this run." \
    ${_STAGING_REPORT[@]+"${_STAGING_REPORT[@]}"} \
    "Re-run once the cause is fixed: a re-stage from the same source replaces" \
    "every path again, and the staleness guard accepts it."
}

# Rename one staged entry over its target. A target that is absent takes the
# whole entry in one rename, file or directory alike; a target that is an
# existing directory is merged into, entry by entry (below); a target that is
# an existing file is replaced in one rename. Kinds that differ are a failure,
# as they were for `cp -R`. Checked in that order because `mv` of a file onto
# an existing DIRECTORY would move it inside, one level down.
_swap_entry() {   # $1 = a staged path, $2 = its final path
  local from=$1 to=$2 said
  if [ -L "$to" ]; then
    _swap_failed "$from" "$to" "the target is a symbolic link, which no guard above saw there"
  fi
  if [ -d "$from" ] && [ ! -L "$from" ] && [ -d "$to" ]; then
    _swap_dir "$from" "$to"
    return 0
  fi
  if [ -e "$to" ] && { [ -d "$to" ] || { [ -d "$from" ] && [ ! -L "$from" ]; }; }; then
    _swap_failed "$from" "$to" "a file and a directory carry the same name"
  fi
  if ! said=$(mv -f -- "$from" "$to" 2>&1); then
    _swap_failed "$from" "$to" "$said"
  fi
  _swapped[${#_swapped[@]}]="$to"
}

# Merge a staged directory into an existing one and remove the emptied shell.
# The glob is taken under LC_ALL=C with nullglob and dotglob, the same way
# _dir_folds_case lists a directory, so the order is fixed and no entry hides
# behind a leading dot.
_swap_dir() {   # $1 = a staged directory, $2 = an existing final directory
  local from=$1 to=$2 entry said
  local LC_ALL=C
  local had_nullglob=0 had_dotglob=0
  local -a entries
  if shopt -q nullglob; then had_nullglob=1; fi
  if shopt -q dotglob; then had_dotglob=1; fi
  shopt -s nullglob dotglob
  entries=("$from"/*)
  if [ "$had_nullglob" = 0 ]; then shopt -u nullglob; fi
  if [ "$had_dotglob" = 0 ]; then shopt -u dotglob; fi
  for entry in ${entries[@]+"${entries[@]}"}; do
    _swap_entry "$entry" "$to/${entry##*/}"
  done
  if ! said=$(rmdir -- "$from" 2>&1); then
    _swap_failed "$from" "(removing the emptied staging directory)" "$said"
  fi
}

# A backstop for an exit this block did not handle itself -- `set -e` on a
# command outside the wrapped copies and renames. Installed only once a
# staging directory exists, and inert after the swap has cleared the list.
_on_exit() {
  local rc=$? p
  if [ "$rc" -eq 0 ]; then return 0; fi
  if [ ${#_STAGING_CREATED[@]} -eq 0 ] && [ ${#_SLOTS_CREATED[@]} -eq 0 ]; then return 0; fi
  _discard_staging
  echo "stage-private-data.sh: FAILED -- the run stopped (exit $rc) with its staging copy still in place" >&2
  echo "  in place:    ${#_swapped[@]} path(s) now hold this source's copy (new where the destination had none," >&2
  echo "               replaced where it had one), relative to the destination root:" >&2
  for p in ${_swapped[@]+"${_swapped[@]}"}; do echo "    ${p#"$DST_REAL/"}" >&2; done
  echo "  every other path this script writes still holds what it held before this run." >&2
  for p in ${_STAGING_REPORT[@]+"${_STAGING_REPORT[@]}"}; do echo "  $p" >&2; done
}

# The three directories the copies write into, noting which of them this run
# created: a failure below removes those again if they are still empty, so the
# archive is left as it was found -- no empty directory included, which is the
# same standard every refusal above holds itself to.
# Create ONE staging directory and record it as this run's. A BARE mkdir --
# no -p, and no existence test ahead of it -- so a name that is already there
# fails the mkdir instead of being adopted: _ensure_contained_dir accepts an
# existing directory, which is right for the three managed slots and wrong
# here, because this list is the one _discard_staging removes from, and an
# entry planted between the leftover check above and this mkdir (or a
# leftover from a run whose pid this one reuses) must never be on it. The
# recorded name is appended only after the mkdir has succeeded, so the list
# holds nothing this run did not create -- which is the whole claim the
# removal rests on. The containment check is the one _ensure_contained_dir
# makes, for the same reason: a component above turned into a link would make
# the literal path and the real one differ.
_create_staging_dir() {   # $1 = absolute path of a staging directory to create
  local said real
  if ! said=$(mkdir -- "$1" 2>&1); then
    _discard_staging
    _refuse "a staging directory could not be created" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "mkdir said:  ${said:-(nothing)}" \
      "expected:    a name this run can create -- only a directory it created" \
      "             may hold its staging copy or be removed afterwards. If the" \
      "             path already exists, it appeared after this run's check for" \
      "             leftovers: inspect and delete it yourself, then re-run."
  fi
  _STAGING_CREATED[${#_STAGING_CREATED[@]}]="$1"
  real=$(_physical "$1" || true)
  if [ "$real" != "$1" ]; then
    _discard_staging
    _refuse "a staging directory does not resolve to itself" \
      "destination: $DST  (resolved: $DST_REAL)" \
      "path:        $1" \
      "resolves to: ${real:-<unresolvable>}" \
      "expected:    a path whose every component is a real directory inside" \
      "             $DST_REAL"
  fi
}

_SLOTS_CREATED=()
for _d in "${_STAGING_SLOTS[@]}"; do
  if [ ! -e "$_d" ] && [ ! -L "$_d" ]; then
    _ensure_contained_dir "$_d"
    _SLOTS_CREATED[${#_SLOTS_CREATED[@]}]="$_d"
  else
    _ensure_contained_dir "$_d"
  fi
done

_STAGE_HH="$DST_REAL/private/$_STAGING_NAME"
_STAGE_RAW="$DST_REAL/private/1-raw-data/$_STAGING_NAME"
_STAGE_VERIFY="$DST_REAL/private/verify/$_STAGING_NAME"
trap _on_exit EXIT
for _d in "$_STAGE_HH" "$_STAGE_RAW" "$_STAGE_VERIFY"; do
  _create_staging_dir "$_d"
done

# THE COPY PHASE. `|| _copy_failed` rather than `set -e`'s silent abort: the
# handler removes the staging copy and says what the archive holds, which is
# what it held.
cp "$SRC/private/household.yaml"                      "$_STAGE_HH/household.yaml" \
  || _copy_failed "private/household.yaml"
cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$_STAGE_RAW/" \
  || _copy_failed "private/1-raw-data/Electric_15_Minute_*.csv"
cp "$SRC"/private/1-raw-data/enphase_sam8760_*.csv    "$_STAGE_RAW/" \
  || _copy_failed "private/1-raw-data/enphase_sam8760_*.csv"
cp "$SRC/private/1-raw-data/gas.csv"                  "$_STAGE_RAW/" \
  || _copy_failed "private/1-raw-data/gas.csv"
cp -R "$SRC/private/1-raw-data/electric-bills"        "$_STAGE_RAW/" \
  || _copy_failed "private/1-raw-data/electric-bills"
cp "$SRC/private/1-raw-data/electric_billing_history_2024-2026.csv" "$_STAGE_RAW/" \
  || _copy_failed "private/1-raw-data/electric_billing_history_2024-2026.csv"

# The gas statements, on the flag the SOURCE GUARD above already validated --
# both directions of it, so by here this is a copy and not a decision.
if [ "$HAS_GAS" = True ]; then
  cp -R "$SRC/private/1-raw-data/gas-bills" "$_STAGE_RAW/" \
    || _copy_failed "private/1-raw-data/gas-bills"
fi

cp "$SRC"/private/1-raw-data/Electric_15_Minute_*.csv "$_STAGE_VERIFY/usage.csv" \
  || _copy_failed "private/1-raw-data/Electric_15_Minute_*.csv -> private/verify/usage.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2026.csv" "$_STAGE_VERIFY/samA.csv" \
  || _copy_failed "private/1-raw-data/enphase_sam8760_2026.csv -> private/verify/samA.csv"
cp "$SRC/private/1-raw-data/enphase_sam8760_2025.csv" "$_STAGE_VERIFY/samB.csv" \
  || _copy_failed "private/1-raw-data/enphase_sam8760_2025.csv -> private/verify/samB.csv"

if [ "$STAGE_CAISO" = 1 ]; then
  cp -R "$SRC/private/1-raw-data/caiso_raw" "$_STAGE_RAW/" \
    || _copy_failed "private/1-raw-data/caiso_raw"
fi

# THE RENAME PHASE. Every copy has landed; from here each step moves a whole
# file. The intake file first, then the archive, then the verify sandbox --
# the order the old copies wrote in, so a failure report reads the same way.
_swap_dir "$_STAGE_HH"     "$DST_REAL/private"
_swap_dir "$_STAGE_RAW"    "$DST_REAL/private/1-raw-data"
_swap_dir "$_STAGE_VERIFY" "$DST_REAL/private/verify"
_STAGING_CREATED=()
_SLOTS_CREATED=()
trap - EXIT

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
echo "  each file was copied into a staging directory beside its target and renamed"
echo "  into place only after every copy had landed -- see STAGED COPY"
