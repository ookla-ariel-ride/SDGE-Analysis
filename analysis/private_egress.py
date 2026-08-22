#!/usr/bin/env python3
"""May private-derived files be written to THIS path? (issue #186)

Every privacy control in this repo guards the GIT boundary: the gitleaks
pre-commit hook, the CI history scan, privacy_tiers.py's staged-content scan.
On 2026-08-13 this household's whole raw archive was written into an unrelated
project's worktree -- a directory that does not gitignore private/ -- and the
run exited 0. Nothing in the repo watched the FILESYSTEM boundary, so nothing
noticed. stage-private-data.sh now enforces the rule in shell (issue #184);
this module is the same rule, callable from Python, for the argument-derived
destinations that are guarded by nothing (generate_report.py's --cache-dir and
--manifest-path, publish.promote_set's dest_dir, the dest_dir= family across
the pipeline scripts).

It is the FILESYSTEM counterpart of llm_providers.py's network gate, and it
deliberately borrows that gate's vocabulary: a single refusal exception,
raised with the first problem found, naming the path -- never a boolean, and
never a silent pass. The two do not share a class: llm_providers.EgressRefused
means "this must not be SENT"; DestinationRefused here means "this must not be
WRITTEN THERE", and a caller that catches one must not accidentally swallow
the other.

THE RULE, matching what stage-private-data.sh already enforces:

  1. the git environment is SANITIZED before any probe, and the ambient git
     CONFIGURATION is switched off for it. `git rev-parse` answers "which
     repository is this path in" from the environment first and the filesystem
     second, so GIT_DIR/GIT_COMMON_DIR/GIT_WORK_TREE (and CDPATH, which changes
     what a bare relative path even MEANS, and the four GIT_*_PATHSPECS, which
     change what a PATTERN means) turn every check below into an answer the
     caller supplied. Cleared, not rejected, for the reason the shell gives at
     length: GIT_DIR is exported by every git hook. Clearing is only half of it
     -- a core.excludesFile in the operator's own global config manufactures the
     "ignored" verdict with nothing forged at all -- so core.excludesFile is
     forced on the command line of every probe, and global, XDG and system
     configuration are additionally switched off on any git that reads the
     variables for it, while the destination's own .gitignore and
     .git/info/exclude stay fully in effect. Two keys are forced FROM the
     destination instead of off: core.ignoreCase and core.precomposeUnicode
     decide how the destination's own patterns MATCH, so an ambient one widens
     the matching until a rule the destination does not have covers the path
     anyway -- their values are read from that repository's own configuration
     and forced to what it says. The command-line half is what makes all of this
     version-independent, and it is verified against the running git before any
     answer is believed. See sanitized_env(), _git(), _destination_overrides()
     and _require_isolation_proven().
  2. the destination lies inside a REGISTERED worktree of THIS checkout, and
     that worktree still IS one. Registration is decided against `git worktree
     list`, resolved from the common dir of the checkout THIS FILE lives in --
     never from anything the destination said. A plain directory holding a
     one-line `.git` gitfile answers --git-common-dir and --show-toplevel
     exactly like a real worktree; a directory restored from a backup of a
     linked worktree does it by accident. Only git's own register tells them
     apart. But the register is a RECORD, not a question asked of the
     directory: git writes the entry when `git worktree add` succeeds and never
     revalidates one whose directory still exists. So the matched entry is
     asked, once, which repository it is in NOW -- see
     _confirm_register_entry().
  3. NO PATH COMPONENT AT OR BELOW THE WORKTREE ROOT IS A SYMLINK. Symlinks
     ABOVE the root are fine and must be (on macOS /tmp and /var are links, and
     a legitimate worktree reached through one has to keep working) -- they are
     resolved before the register is consulted. Below the root a link is a route
     back out of the tree that just passed every check, and every writer here
     follows it. The same walk requires every EXISTING component above the last
     to be a directory: a regular file, a FIFO or a device node there is a path
     the caller cannot create at all, so accepting it answers the module's one
     question wrongly.
  4. the path is GENUINELY IGNORED there: untracked, and reported ignored by
     that working tree's OWN git. Untracked is asked first and separately,
     because check-ignore consults the index and reports a tracked-though-
     ignored path as "not ignored" -- which would send the operator to edit a
     .gitignore that was already correct. Both questions are asked about the
     path THE FILESYSTEM SPELLS, not the one the caller typed: on a folding
     volume a write to `private/1-raw-data` lands in an existing
     `Private/1-raw-data`, which the tree may track and may not ignore, and git
     answers about the bytes it is handed. See _ondisk_relpath().
  5. the thing already at the path is the KIND the caller says it will write.
     A hard link, a FIFO, a device node and a directory-where-a-file-goes are
     each invisible to 1-4: they are inside the tree, they are ignored there,
     and they still send the bytes somewhere else (a second name on the inode,
     a reader on the other end of the pipe) or hang the run. Which checks apply
     is decided by the REQUIRED `kind` argument, never by a default -- see
     check_destination().

WHAT IT DOES NOT DO. It does not open, create or write anything, and it does
not wire itself into any caller: this is the predicate only. Wiring it into the
argument-derived writers is a separate change with its own review (see
analysis/test_private_egress.py's MOVERS registry for what that would touch).
A refusal is raised, never returned as a code the caller can forget to read.

RACE, stated rather than discovered later: the checks here are TOCTOU-shaped --
a link planted between this call and the caller's write is not caught, and
Python's stdlib copy calls have no O_NOFOLLOW to make it atomic. The hazard
this closes is a link planted BEFORE the run (a stale worktree, a leftover
`ln -sfn`), which is the shape the incident and every fixture in #184 had, not
an attacker racing the copy: anyone able to write inside the destination
mid-run already has the archive.

Run directly for a one-path verdict:
    ./.venv/bin/python analysis/private_egress.py <root|tree|dir|file> <path>
"""
import fnmatch
import os
import pathlib
import re
import shutil
import subprocess
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# The refusal vocabulary. Machine-readable codes, because the agreement table
# in test_private_egress.py compares this module's verdict against
# stage-private-data.sh's REFUSED headline for the same fixture, and comparing
# prose would either be brittle or vacuous. Every code below is reachable by at
# least one case in that table (asserted there, against a floor).
# ---------------------------------------------------------------------------
REASONS = {
    "self_unlocatable":     "this module is not inside a git working tree, so it cannot say "
                            "which checkout's worktrees are eligible",
    "register_unavailable": "this checkout's worktrees could not be listed",
    "unnormalized_path":    "a path is not a normalized name for one place: it contains a "
                            "'..' component, or -- where a worktree-relative declaration "
                            "belongs -- it is absolute",
    "no_such_destination":  "the destination does not exist, or is not a directory",
    "not_a_worktree":       "the destination is not inside a git working tree",
    "different_repository": "the destination belongs to a different repository",
    "no_worktree_of_its_own": "the destination is a bare repository or a git internal directory",
    "not_registered":       "the destination claims this checkout but appears in no entry of "
                            "its worktree register",
    "not_worktree_root":    "the destination is a subdirectory, not a worktree root",
    "worktree_root_itself": "the destination IS a worktree root, and no checkout ignores "
                            "its own root",
    "symlink_component":    "a path component at or below the worktree root is a symbolic link",
    "not_a_directory":      "a path that must be a directory exists and is not one",
    "special_file":         "a path where a REGULAR FILE goes exists and is not one: a "
                            "directory, a FIFO, a socket or a device node",
    "hard_link":            "a destination file has more than one name for its inode",
    "symlink_under":        "a directory that will be written into recursively contains a "
                            "symbolic link",
    "special_under":        "a directory that will be written into recursively contains a "
                            "special file",
    "hardlink_under":       "a directory that will be written into recursively contains a "
                            "hard-linked file",
    "scan_unreadable":      "a directory that will be written into recursively could not be "
                            "read, so it could not be cleared",
    "isolation_unproven":   "this git could not be isolated from the operator's own "
                            "configuration, so its answer about what is ignored is "
                            "about this machine rather than about the repository",
    "tracked_path":         "the destination's own git TRACKS this path",
    "not_ignored":          "the destination's own git does not ignore this path",
    "aliased_not_ignored":  "the destination ignores this path as it was SPELLED, and does "
                            "not ignore the different on-disk spelling the write would "
                            "actually land on",
    "spelling_unresolved":  "which path on disk the write would land on could not be "
                            "determined: a directory on the way to it could not be read",
    "ignore_unanswerable":  "the destination could not say whether it ignores this path",
    "tracked_unanswerable": "the destination could not be asked which paths it tracks",
    "glob_source_unlistable": "a leaf pattern's source directory could not be listed, so "
                              "the destinations that pattern names are unknown",
    "pattern_names_a_directory": "a glob pattern is used where a DIRECTORY is named, and "
                                 "this module expands leaf names only",
}


class DestinationRefused(Exception):
    """Raised for the first problem found. `reason` is a key of REASONS."""

    def __init__(self, reason, detail, path=None):
        assert reason in REASONS, f"unknown refusal reason {reason!r}"
        self.reason = reason
        self.detail = detail
        self.path = None if path is None else str(path)
        super().__init__(f"REFUSED [{reason}] {detail}"
                         + (f" -- path: {self.path}" if self.path else ""))


class Destination:
    """An accepted destination: where it is, and which worktree owns it."""

    def __init__(self, path, worktree, relpath):
        self.path = str(path)          # the literal absolute path, links unresolved
        self.worktree = str(worktree)  # the registered worktree root, physically resolved
        self.relpath = relpath         # posix, relative to that root

    def __repr__(self):
        return f"Destination({self.path!r}, worktree={self.worktree!r}, relpath={self.relpath!r})"


# ---------------------------------------------------------------------------
# Environment sanitizing. The same list stage-private-data.sh clears, in the
# same two kinds, and test_private_egress.py asserts the two lists are equal by
# parsing the shell's own loop -- a variable added to one side and not the other
# is a build failure, not a divergence somebody notices later.
#
# GIT_CONFIG* is matched by PREFIX because GIT_CONFIG_KEY_<n>/VALUE_<n> are
# unbounded, and config can carry core.worktree -- a working-tree location under
# another name.
#
# PATHSPEC_MEANING_VARS (issue #194) are not identity variables at all: they
# leave the repository alone and change how git READS the path handed to it,
# which is the same lever one step over -- the right repository, asked about a
# DIFFERENT path than the one named, or about no path at all. All four were
# measured on git 2.50.1 rather than taken on the family name, and all four move
# an answer this module depends on:
#
#   git ls-files -- './data/*.json'      plain 3 tracked | LITERAL 0 | NOGLOB 0
#                                        | GLOB 3 | ICASE 3
#   git check-ignore -q -- './private/foo'  plain 0 (ignored) | LITERAL,
#                                        NOGLOB, GLOB, ICASE all 128,
#                                        "pathspec magic not supported by this
#                                        command"
#   git ls-files -- './data/leak.json'   plain nothing | ICASE data/LEAK.json
#
# So LITERAL and NOGLOB empty the glob-backed listing _expand()'s zero-match
# branch relies on -- the leaf pattern is then evaluated against nothing while
# the check still returns success. All four turn check-ignore fatal, which
# _require_uncommittable() fails closed on (ignore_unanswerable) but as a denial
# of service: no destination can be validated at all. And ICASE makes ls-files
# answer about a differently-cased path, so a refusal names a file the caller
# never asked about. Cleared, like the rest, rather than rejected.
#
# Which is not the same as never folding case (issue #204). GIT_ICASE_PATHSPECS
# applies the fold to EVERY pathspec at once -- including check-ignore's, which
# it turns fatal -- from an environment this module does not control. The alias
# probe applies ':(icase)' to ONE ls-files pathspec, on purpose, where the
# filesystem was measured to fold, and only to ADD a refusal. See
# ALIAS_CONFIG_OVERRIDE.
#
# Deliberately kept: GIT_EXEC_PATH, GIT_SSH_COMMAND, GIT_TEMPLATE_DIR and the
# like. They say HOW git runs, not which repository it is looking at, and on a
# relocatable install clearing GIT_EXEC_PATH breaks git outright -- turning a
# legitimate call into a refusal for no gain.
#
# The CONFIGURATION half (issue #193) is the other one, and it is SET rather
# than cleared. Clearing the GIT_CONFIG* family stops a caller REPLACING the
# configuration; it does nothing about the configuration that is simply THERE.
# core.excludesFile in $HOME/.gitconfig, in $XDG_CONFIG_HOME/git/config or in
# the system gitconfig is ordinary, supported git, and it makes a path the
# destination's own .gitignore does not cover answer "ignored". Measured on git
# 2.50.1 against a fixture whose .gitignore holds `private/` and which does not
# ignore data/leak.json -- 'git check-ignore -q -- ./data/leak.json':
#
#   excludesFile unset                                       -> 1  not ignored
#   $HOME/.gitconfig naming an excludes file listing it       -> 0  IGNORED
#   $XDG_CONFIG_HOME/git/config naming one                    -> 0  IGNORED
#   $HOME/.config/git/ignore listing it (no config key at all) -> 0  IGNORED
#   $HOME/.gitconfig include.path -> a file naming one         -> 0  IGNORED
#   $HOME/.gitconfig includeIf gitdir: -> a file naming one    -> 0  IGNORED
#   core.excludesFile in the repository's own .git/config      -> 0  IGNORED
#
# and check_destination() accepted the path in every one of them. HOME is NOT
# cleared to fix that: it supplies a legitimate input to the real repository's
# answer rather than replacing which repository answers, and clearing it would
# break the credential and ssh machinery of any git command a later edit adds.
# The narrow instrument is to switch the ambient configuration off for git only,
# and it is applied TWICE, by two mechanisms of very different ages:
#
#   GIT_CONFIG_OVERRIDES     -c core.excludesFile=<devnull>, passed on EVERY git
#                            command _git() runs. `git -c` arrived in git 1.7.2
#                            (Jul 2010, commit 8b1fa778) and outranks everything
#                            else: git-config(1) SCOPES orders system < global <
#                            local < worktree < GIT_CONFIG_COUNT/KEY/VALUE < -c,
#                            and says of the variables that they "will be
#                            overridden by any explicit options passed via git
#                            -c". Measured here too -- with both set to different
#                            values, `config --show-origin --get
#                            core.excludesFile` reports "command line:" with the
#                            -c value
#   GIT_CONFIG_ISOLATION     the six environment variables, which switch the
#                            ambient configuration off WHOLESALE rather than one
#                            key at a time -- but only on a git that reads them:
#                            GIT_CONFIG_NOSYSTEM is git 1.5.5 (Apr 2008, commit
#                            ab88c363) and covers the SYSTEM file only,
#                            GIT_CONFIG_COUNT/KEY/VALUE are git 2.31 (Mar 2021,
#                            commit d8d77153) and GIT_CONFIG_GLOBAL/_SYSTEM are
#                            git 2.32 (Jun 2021, commit 4179b489)
#
# THE SECOND MECHANISM CANNOT CARRY THIS ON ITS OWN, and the reason is that a
# git which has never heard of a GIT_* variable does not reject it: the name
# appears nowhere in its config.c, so there is no getenv to fail and nothing to
# report, and an isolation built on the 2021 variables looks identical whether it
# is working or inert. Git documents no rule about unsupported variables either
# way, so that is an implementation fact and is treated as one -- MEASURED, with
# a PATH shim whose `git` removes GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM,
# GIT_CONFIG_COUNT, GIT_CONFIG_KEY_0 and GIT_CONFIG_VALUE_0 from the environment
# and execs the real git. A process that never reads a variable and a process
# that never receives it cannot be told apart, so that is the old git exactly:
#
#                                             no        the 6       -c
#   ambient route                        isolation  variables  excludesFile
#   $HOME/.gitconfig core.excludesFile           0          0          1
#   $XDG_CONFIG_HOME/git/config the same         0          0          1
#   $HOME/.config/git/ignore (no config key)     0          0          1
#   $HOME/.gitconfig include.path -> the key     0          0          1
#   $HOME/.gitconfig includeIf gitdir: -> it     0          0          1
#
# (0 = "ignored", the answer that lets a committable destination through.) The
# middle column is the hole; the right-hand column is the -c, which closes all
# five -- including the two INCLUDE routes, because precedence, not
# file-reading, is what a -c wins on. Both mechanisms are kept: the -c carries
# the property on every version, the six cover the next core.* key somebody
# finds a use for on the versions that read them.
#
# WHAT SURVIVES, measured under every column: './private/foo', covered by the
# fixture's own .gitignore, still exits 0, and a path listed in that fixture's
# .git/info/exclude still exits 0. The isolation must not throw away the answer
# it exists to protect. A refusal it does cause has a remedy inside the
# destination -- name the path in that working tree's .gitignore or
# .git/info/exclude -- which is the property that separates it from clearing
# HOME, whose refusals an operator could not fix by editing anything in the
# repository.
#
# WHAT ELSE COULD REACH THE VERDICT. The excludes file is not the only ambient
# input, and the sweep that said it was had a hole in its METHOD: it was run
# against fixtures `git init` had just built, and git init writes the very keys
# it was testing into .git/config, where they outrank global. A local value that
# is PRESENT masks the ambient one. Re-run with each key's local value REMOVED
# -- the state of any repository whose config was written on a case-sensitive
# filesystem, or by hand -- two keys move a verdict this module acts on, both in
# the ADMITTING direction. Fixture: .gitignore holds `Private/` and `café/`
# (NFC); the questions are './private/leak.json' and './café/leak.json' (NFD).
# Old-git shim, with the -c core.excludesFile already in place:
#
#   ambient key (global)          local value present    local value removed
#   core.ignoreCase = true        1  not ignored         0  IGNORED
#   core.precomposeUnicode = true 1  not ignored         0  IGNORED
#
# Neither is an excludes file: they decide how the destination's OWN patterns
# match, and both widen the match, so an ambient one makes a rule the
# destination does not have cover the path anyway. All five ambient routes
# deliver them -- $HOME/.gitconfig, $XDG_CONFIG_HOME/git/config,
# $HOME/.config/git/config, include.path and includeIf gitdir:.
#
# THEY ARE NOT FORCED OFF, they are forced FROM THE DESTINATION -- see
# _destination_overrides(). Forcing core.ignoreCase=false outright closes the
# hole and breaks correct callers: measured on a repository whose own
# .git/config says ignorecase=true (what git init writes on macOS and Windows)
# with .gitignore `private/`, the question './Private/leak.json' answers 0
# unforced and 1 forced-false -- the guard refusing a destination whose git
# really would refuse the path. A guard that refuses ordinary correct callers is
# one that gets switched off.
#
# WHAT WAS TESTED AND IS INERT, with each key's local value removed and the
# ambient one set: core.worktree, core.bare, core.symlinks, core.fileMode,
# core.quotePath, core.attributesFile, core.hooksPath, core.fsmonitor,
# core.sparseCheckout, core.protectHFS, core.autocrlf, core.longpaths,
# core.checkStat, core.untrackedCache, index.sparse, status.showUntrackedFiles,
# and aliases named after the three commands run here (git does not let an alias
# shadow a builtin). safe.directory is inert IN THIS DIRECTION and is not inert
# in the other one -- see "WHAT THE ISOLATION MUST NOT TAKE AWAY" below, which
# is the question this list does not ask. core.bare is the one that needs its own
# sentence: an ambient bare=true does make `git worktree list` report a MAIN
# worktree as bare -- which _parse_worktree_records() drops, refusing a
# destination inside it -- but only when the listing is made from inside a
# working tree. registered_worktrees() lists from the COMMON GIT DIR, and there
# the main worktree's bareness is decided by the cwd and by the repository's own
# core.bare (which git init always writes as false), not by the ambient value:
# measured inert in that call shape, with the local value present and removed.
# Refusing-direction in either case, so it is left alone rather than made a
# fourth forced key.
#
# AND WHAT THE ISOLATION MUST NOT TAKE AWAY (issue #193, /review round three).
# The sweep above asks one question -- does an ambient value MOVE a verdict in
# the admitting direction -- and the opposite question has its own answer: does
# switching the ambient configuration off REMOVE a value the probes NEED? It
# does, for exactly one key, and the key is `safe.directory`.
#
# git refuses to work in a repository owned by another user at all ("fatal:
# detected dubious ownership"), and the one way an operator lifts that is a
# `safe.directory` entry -- which git honours ONLY from protected configuration
# (system, global, command line), precisely the scopes GIT_CONFIG_GLOBAL,
# GIT_CONFIG_SYSTEM and GIT_CONFIG_NOSYSTEM empty. So a worktree on an SMB or
# NFS share, in a container bind-mount, or created under sudo -- one the
# operator has already declared safe and uses every day -- became unanswerable
# HERE and nowhere else. MEASURED on git 2.50.1, with the ownership check driven
# by GIT_TEST_ASSUME_DIFFERENT_OWNER=1 and safe.directory in the operator's
# ~/.gitconfig, against every probe this module runs:
#
#                                  ambient config   this branch's isolation
#   rev-parse --git-common-dir          0                128  fatal
#   rev-parse --show-toplevel           0                128  fatal
#   worktree list --porcelain           0                128  fatal
#   ls-files -- <path>                  0                128  fatal
#   check-ignore -q -- <path>           0                128  fatal
#
# and the refusal that came out named none of it: common_git_dir() returns None
# on a fatal, so a correct caller was told "not inside a git working tree" with
# a `git worktree add` remedy that cannot fix an ownership problem. A guard that
# refuses correct callers is one that gets switched off -- this file's own
# argument, applied to itself.
#
# THE SWEEP, in that second direction: 29 ambient keys, each set in the
# operator's global config and each read by all five probes above with the
# isolation on and off. safe.directory is the ONLY one that turns an answer into
# a failure. protocol.file.allow=never moves them the other way (every probe is
# fatal WITHOUT the isolation and answers with it), and the remaining 27 --
# safe.bareRepository, uploadpack.packObjectsHook (the other two keys git reads
# from protected configuration only), core.longpaths, core.protectHFS,
# core.protectNTFS, core.symlinks, core.fileMode, core.quotePath,
# core.attributesFile, core.hooksPath, core.untrackedCache, core.fsmonitor,
# core.autocrlf, core.bare, core.worktree, core.sparseCheckout, index.sparse,
# status.showUntrackedFiles, core.pager, core.checkStat, core.abbrev,
# init.defaultBranch, worktree.useRelativePaths, advice.detachedHead and the
# three keys this module forces -- are inert or move in the admitting direction,
# which the paragraphs above already settle.
#
# THE REPAIR is AMBIENT_PROTECTED_KEYS below, re-injected with -c, and it is
# narrow in four ways that together are why re-admitting a protected-config key
# does not undo the isolation:
#
#   WHEN. Only after git has already refused to answer -- _git() re-runs a
#         command that exited 128 (git's fatal status), once, and only then. A
#         value that arrives only where there was no answer cannot change an
#         answer, so no verdict this module acts on can be moved by it.
#   WHICH. safe.directory decides WHETHER git will read a repository, not what
#         that repository's rules are or how they match. It supplies no ignore
#         rules (that is core.excludesFile) and no matching semantics (that is
#         core.ignoreCase and core.precomposeUnicode), so there is no path by
#         which it reaches the ignored/not-ignored verdict.
#   FROM WHERE. Read from the `system` and `global` scopes only, filtered by
#         git's own `config --show-scope`, which is the same rule git applies to
#         the key. The DESTINATION's local and per-worktree config is dropped --
#         promoting a repository-local safe.directory to the command line would
#         let a forged destination declare itself trustworthy, which is the
#         attack git's protected-configuration rule exists to stop. The read
#         runs with the whole GIT_CONFIG* family dropped (_unisolated_env()), so
#         an inherited environment cannot supply one either.
#   HOW MUCH. The values are replayed RAW, in git's own order, so `~/x`,
#         `%(prefix)/x`, `*` and the empty reset entry keep their meaning and a
#         directory the operator never declared stays refused -- now with git's
#         own message, which carries the working remedy.
#
# On a git old enough to lack `config --show-scope` (2.26, Mar 2020) the read
# returns nothing and nothing is re-injected: safe.directory arrived in 2.35.2
# (Mar 2022), so such a git has no ownership check to lift.
#
# AND IT IS PROVED, not assumed: _require_isolation_proven() below asks the
# running git what each forced key actually reads back as, and refuses if any of
# them is not ours. The re-injected key is deliberately NOT in that list -- its
# value is the operator's, not this module's, and there is nothing to prove
# about a value we did not choose. Version numbers are what this comment can
# offer; what the guard acts on is the git in front of it.
#
# The ORDER matters and is asserted by construction below: the inherited
# GIT_CONFIG* family is dropped first, then the six are written in. An inherited
# GIT_CONFIG_COUNT=5 left in place beside GIT_CONFIG_KEY_0 would describe a
# configuration nobody meant.
# ---------------------------------------------------------------------------
GIT_IDENTITY_VARS = (
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE", "GIT_NAMESPACE",
)
PATH_MEANING_VARS = ("CDPATH",)
PATHSPEC_MEANING_VARS = (
    "GIT_LITERAL_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_GLOB_PATHSPECS",
    "GIT_ICASE_PATHSPECS",
)
GIT_CONFIG_PREFIX = "GIT_CONFIG"
# os.devnull, not the literal, for the reason test_scripts_runnable's
# case_no_absolute_paths_outside_the_repo gives: an absolute literal outside the
# repo is a hardcoded machine assumption. It is the POSIX null device on every
# platform that can run stage-private-data.sh, which is what makes the sides of
# case_the_environment_this_module_clears_matches_the_shell_script comparable at
# all -- the shell writes the literal, and on a platform where they differ the
# shell script does not run.
GIT_CONFIG_ISOLATION = (
    ("GIT_CONFIG_GLOBAL", os.devnull),
    ("GIT_CONFIG_SYSTEM", os.devnull),
    ("GIT_CONFIG_NOSYSTEM", "1"),
    ("GIT_CONFIG_COUNT", "1"),
    ("GIT_CONFIG_KEY_0", "core.excludesFile"),
    ("GIT_CONFIG_VALUE_0", os.devnull),
)
# The version-independent half: `git -c NAME=VALUE`, on every invocation. Kept
# as a table rather than written into _git()'s argument list so that
# case_the_environment_this_module_clears_matches_the_shell_script can compare it
# against the shell's own `command git -c ...` line, the same way the six above
# are compared against the shell's forcing loop.
GIT_CONFIG_OVERRIDES = (
    ("core.excludesFile", os.devnull),
)
# The key whose effective value _require_isolation_proven() reads back, and the
# value it demands. Derived from the table above rather than restated, so the
# proof cannot go on asserting a key the isolation has stopped forcing.
ISOLATION_PROOF_KEY, ISOLATION_PROOF_VALUE = GIT_CONFIG_OVERRIDES[0]

# The ambient keys the isolation must hand BACK when git refuses to answer, and
# the only two config scopes they may be taken from -- git's own rule for them.
# See "WHAT THE ISOLATION MUST NOT TAKE AWAY" above for the sweep that found
# this list and the four limits that keep it from undoing the isolation.
AMBIENT_PROTECTED_KEYS = ("safe.directory",)
PROTECTED_SCOPES = ("system", "global")
# git's own status for "I refused to do this at all", as opposed to 1 for a
# question answered "no". check-ignore's 1, `config --get`'s 1 and ls-files'
# empty success are answers; 128 is not, and it is the only status the
# re-injection above is allowed to react to.
GIT_FATAL = 128

# The keys whose value is taken FROM THE DESTINATION rather than switched off,
# with the value used when that repository states none. Boolean, and forced as
# git's own normalized spelling ("true"/"false") so the readback in
# _require_isolation_proven() compares like with like.
#
# WHY NOT OFF: core.excludesFile names an alternative FILE of rules, so an empty
# one leaves the destination's .gitignore and .git/info/exclude to answer alone
# -- exactly what the guard wants to ask. These two are not rules, they are the
# MATCHING SEMANTICS the destination's own rules are read with, and the right
# semantics are a property of the repository (and of the filesystem under it),
# not of the machine the staging happens to run on.
#
# WHY "false" WHEN ABSENT: it is the value the destination's own git already
# uses when nothing states one, so the guard reproduces what `git add` would do
# there with the operator's configuration out of the picture, which is the
# question this whole module asks. git-config(1) says it outright for
# core.ignoreCase ("The default is false, except git-clone or git-init will
# probe and set core.ignoreCase true if appropriate"); it states no default for
# core.precomposeUnicode, so that half is MEASURED rather than cited -- with
# neither a local nor an ambient value, the NFD question against the NFC rule
# answered "not ignored", which is what false does. It is also the narrow
# direction in both cases: false matches fewer paths, so it can only refuse
# where a wrong default would otherwise accept.
#
# WHAT ABSENT COSTS, stated because it is real: on a case-insensitive
# filesystem whose repository has no local core.ignoreCase, the destination's
# `Private/` rule stops covering `private/`, and a path already tracked as
# `Private/x` stops answering `ls-files private/x`. `git init` and `git clone`
# write core.ignoreCase=true on such a filesystem, so this is a repository whose
# config was hand-edited or carried over from a case-sensitive machine -- and
# the remedy is one command inside the destination, `git config core.ignoreCase
# true`, which is what git would have written.
DESTINATION_CONFIG_KEYS = (
    ("core.ignoreCase", "false"),
    ("core.precomposeUnicode", "false"),
)
# The repository-internal scopes the value above is read from, in git's own
# precedence order (highest first). BOTH, because `--local` does not see a
# per-worktree value: measured on a repository with extensions.worktreeConfig
# enabled and core.ignoreCase=true in config.worktree, `git config --local
# --get` exits 1 while check-ignore behaves as true, so a guard reading --local
# alone would force false over a value the destination really is using. Neither
# scope can be reached from the environment or from ambient config files, which
# is what makes the read safe to make with the ambient configuration still
# standing.
DESTINATION_CONFIG_SCOPES = ("--worktree", "--local")

# ---------------------------------------------------------------------------
# THE ALIAS PROBE (issue #204) -- the SECOND tracked question, asked under the
# filesystem's own idea of which two spellings are one file.
#
# The tracked question above is `git ls-files -- <pathspec>`, and a pathspec
# matches index entries BY THE BYTES. On a case-insensitive filesystem that is
# the wrong equivalence, and the gap is not theoretical -- reproduced on macOS
# (APFS, git 2.50.1) in a scratch repository whose .gitignore holds `private/`
# and whose index holds `Private/household.yaml`:
#
#   write to private/household.yaml
#     git status                        ->  M Private/household.yaml
#   git ls-files -- ./private/household.yaml     ->  nothing  ("not tracked")
#   git check-ignore -q -- ./private/household.yaml  ->  0     ("ignored")
#
# Both questions answer in the ADMITTING direction while the write lands on a
# committed file. The same happens for unicode composition: an index holding
# `private/houséhold.yaml` (NFC) is not matched by the NFD spelling of the
# same name, and on this filesystem the two are one file.
#
# NOT core.ignoreCase, which is the plausible fix and is measurably not the
# mechanism. All three values were measured on the fixture above and are
# IDENTICAL -- unforced, false and true all report the path as untracked --
# because core.ignoreCase governs how git matches working-tree paths against the
# index during status and checkout, not how a pathspec resolves against index
# entries. Forcing it would have closed nothing while reading as a fix.
#
# What does work, measured on the same fixture:
#
#   ls-files -- ':(icase)./private/household.yaml'   ->  Private/household.yaml
#   -c core.precomposeUnicode=true, NFD pathspec     ->  the NFC index entry
#
# So the fold is git's OWN, on both sides of the agreement table -- no locale
# casefold, no unicodedata call the shell has no equivalent for, and identical
# semantics in the two implementations by construction.
#
# ADDITIVE, AND THAT IS WHAT MAKES IT SAFE TO ASK SECOND: the alias probe can
# only turn ACCEPT into REFUSE. It is asked after the literal one, so a path
# that is tracked under its own name still reports the plain fact rather than a
# fold, and it raises the SAME reason (tracked_path) because it is the same
# question -- "is this write going to land on something committed here".
#
# ':(icase)./' rather than ':(icase,literal)./': `literal` would switch globbing
# off, and _expand() answers a zero-match leaf pattern with the pattern itself,
# which git evaluates as a glob against the index. Measured verdict-identical to
# the plain './' spelling on every magic-looking name in PATHSPEC_EQUIVALENT --
# ':(top)x', ':(exclude)x', '::x', ':!x', ':/x' -- because the './' this prefixes
# is still what the path itself starts with, so nothing after the magic is
# parsed as more magic.
#
# core.precomposeUnicode=true is FORCED here, and only here: it is the one value
# this module sets against the destination's own statement of it, so it is
# proved separately in _require_isolation_proven() rather than riding on the
# proof of the adopted list. Off macOS git is built without precompose support
# and the key is inert, which is the right behaviour rather than a limitation --
# a filesystem that does not normalize has no alias to find.
#
# WHAT THIS BLOCK DOES NOT COVER, corrected here rather than left standing
# (issues #223 and #224). Two sentences #204 left behind were wrong:
#
#   * "':(icase)' folds case."  It folds ASCII case. The magic is byte-oriented,
#     so `:(icase)./private/househöld.yaml` does NOT find a tracked
#     `private/HOUSEHÖLD.yaml` while `:(icase)./private/household.yaml` does find
#     `private/HOUSEHOLD.yaml` -- measured side by side in one repository, on a
#     filesystem that resolves both pairs to one file. core.precomposeUnicode
#     above is a different axis (composition, not case) and closes none of it.
#   * "#193/#194 closed the ignore-side version of this."  They closed the
#     CONFIGURATION half -- an ambient core.ignoreCase or core.precomposeUnicode
#     can no longer widen the destination's own rules. The PATH half was still
#     open: an untracked directory on disk under the other case spelling made
#     check-ignore answer "ignored" about a path the write never reached, and
#     `git check-ignore` takes no pathspec magic at all (`:(icase)…` exits 128),
#     so this block's remedy could not be carried over to it.
#
# Both are closed FOR A PATH THAT EXISTS ON DISK by _ondisk_relpath(), which
# resolves the candidate to the spelling the filesystem actually holds before any
# of the three questions is asked. That is filesystem truth rather than a model of
# folding, so it needs no Unicode rules in either implementation and works for
# check-ignore, which refuses magic. The ':(icase)' probe here is kept alongside
# it, not replaced: see _require_uncommittable() for the measured reason -- an
# index entry with no file in the working tree has no on-disk spelling for the
# walk to find.
#
# WHERE THE TWO USED TO MISS TOGETHER, and what closed it (issue #230). A tracked
# index entry with NO working-tree file, differing from the path only in NON-ASCII
# case, was seen by neither probe -- ':(icase)' folds ASCII only, and the walk has
# nothing to resolve for a leaf that does not exist. A THIRD question now covers
# it, and it is not a third probe of the same kind: _index_case_aliases()
# enumerates the destination's index and compares each entry against the candidate
# component by component, under the generated fold at CASE_FOLD_SED and gated by the
# per-component fold vector. The pair above still does the work wherever a name
# exists on disk, where the filesystem itself is the oracle; the enumeration is
# what answers for a name that does not.
ALIAS_CONFIG_OVERRIDE = (("core.precomposeUnicode", "true"),)
# The name the ROOT's half of the case measurement is taken on. `.git` is the one
# entry every worktree root is guaranteed to have -- a directory in the main
# checkout, a gitfile in a linked worktree -- and it has case-varying characters,
# so no directory listing is needed to find something to ask about. No directory
# BELOW the root has a name like that, so the directories the path descends into
# are measured on an entry of their own instead (_dir_folds_case), which is what
# makes the answer follow the path onto a volume mounted under the root.
CASE_PROBE_NAME = ".git"
CASE_PROBE_ALIAS = ".GIT"

# ---------------------------------------------------------------------------
# THE CASE FOLD (issues #230, #233) -- the one comparison in this module that
# MODELS what a folding filesystem does instead of measuring it, and the reason
# it has to exist.
#
# Everywhere else the filesystem itself answers "are these two names one file":
# _same_file() stats both and compares device and inode, which needs no Unicode
# rules and is right for whatever some later volume folds. That instrument needs
# at least one of the two names to EXIST. For an index entry that is tracked and
# has no working-tree file, and a candidate leaf that has not been created yet,
# NEITHER name exists -- and there is no write-free way to ask a filesystem
# whether it would collide two names that are not there. Measured rather than
# assumed: stat, lstat and realpath all answer ENOENT for both spellings, and
# every oracle that does answer (open(O_CREAT|O_EXCL), link, rename) writes
# inside a destination this module has not yet decided it may write to.
#
# So the pair is compared under a fold both implementations perform on the raw
# bytes, FROM ONE GENERATED TABLE. The table is python's own simple lowercase
# map -- str.lower() -- emitted as a sed script: CASE_FOLD_SED below, and the
# byte-identical literal in stage-private-data.sh's _CASE_FOLD_SED. The shell
# runs that text through `LC_ALL=C sed`; this module PARSES THE SAME TEXT and
# applies it with one regex. Neither side writes down a fold rule of its own, so
# there is no second derivation for the two to drift apart. Regenerate with
#
#     python3 analysis/private_egress.py --regenerate-case-fold
#
# run on the NEWEST interpreter available, which rewrites the marked block in
# BOTH files.
#
# THE DOMAIN IS PINNED, NOT RE-DERIVED PER RUN (issue #234). str.lower() is a
# property of the interpreter's Unicode version, not a constant: this table is
# 1392 pairs under Unicode 13 (python 3.9), 1432 under 15 (3.12) and 1459 under
# 16 (3.14), all measured. A committed table asserted EQUAL to what the running
# interpreter generates is therefore asserted against whichever python happens
# to run the suite -- it passed locally on 3.9 and failed on the 3.12 CI pins in
# the same commit, which is how this was found. So:
#
#   * the table is generated under the newest interpreter to hand, making it the
#     WIDEST fold available rather than the local one;
#   * CASE_FOLD_UNICODE below records the Unicode version it came from, written
#     by the generator rather than typed;
#   * the guard case asserts the committed table is a SUPERSET of the running
#     interpreter's str.lower(), and byte-equal only when that interpreter's
#     unicodedata.unidata_version is the recorded one.
#
# SUPERSET IS THE FAIL-CLOSED DIRECTION HERE, which is what lets the assertion
# be relaxed to it. A pair the table joins that the running python does not know
# is a pair some later Unicode assigned: the guard sees an alias the local
# interpreter cannot name, and the worst it costs is an over-refusal of a
# destination that is genuinely two files. A pair the running python knows and
# the table lacks is an alias NOBODY sees, which is the fail-open condition
# issue #230 exists to close -- so that is the direction that fails the suite,
# loudly, naming the missing pairs.
#
# WHAT HAPPENS ON ANOTHER INTERPRETER, both directions, because both happen:
#   * NEWER (a Unicode this table predates). The suite passes as long as every
#     pair that python knows is in the table, and fails the moment one is not --
#     which is a real staleness and not a version quarrel. The remedy is to
#     regenerate on that interpreter, which widens the table and moves
#     CASE_FOLD_UNICODE forward.
#   * OLDER. Regeneration REFUSES: regenerate_case_fold() compares the table it
#     would write against the committed one and will not drop a pair, so running
#     the command on 3.9 today reports what it would lose and writes nothing.
#     An older interpreter can still RUN the guard -- the superset holds
#     trivially -- it just cannot narrow the domain by regenerating.
#
# WHAT IT FOLDS, EXACTLY: every code point whose str.lower() is a SINGLE
# different code point, and nothing else. ASCII A-Z, the whole of the 2-byte
# UTF-8 range (Latin-1 supplement, Latin Extended-A and -B, IPA, Greek and
# Coptic, Cyrillic, Armenian), and the 3- and 4-byte code points str.lower()
# maps one-to-one (Georgian, Cherokee, Glagolitic, Greek Extended, the
# fullwidth Latin forms, Warang Citi, Medefaidrin, Adlam, Deseret). It does NOT
# fold two unrelated accents: 'í' and 'å' are not a case pair and stay apart.
#
# IT IS NOT A SUPERSET OF WHAT A FOLDING VOLUME DOES, and the table it replaces
# was described here as one. That claim was wrong in both directions, measured
# rather than argued (issue #233):
#
#   UNDER-MATCHING, which is the defect. The old table was three BYTE ranges
#   (A-Z, \200-\237, \300-\337, each with 0x20 set), so it folded a pair only
#   where the UTF-8 differs in the 0x20 bit of the FINAL byte. Of the 380 cased
#   pairs in the five blocks a filename realistically uses it folded 61 and
#   MISSED 319 -- all of Latin Extended-A and -B, most of Greek, most of
#   Cyrillic. A tracked-but-absent `private/РИСК.yaml` beside a candidate
#   `private/риск.yaml` therefore matched nothing and the guard ACCEPTED, which
#   is issue #230 still open across the majority of its own space. On this
#   checkout's APFS volume those two spellings really are one file (measured:
#   equal st_dev and st_ino after writing one of them), so the direction of that
#   residue was FAIL-OPEN. The word "fail-closed" is what made it invisible.
#
#   OVER-MATCHING, which was the claimed price and is not one worth paying. The
#   old table folded U+2010 and U+2030 together; they are not a case pair and
#   APFS keeps them apart (measured). The generated table folds neither more nor
#   less than str.lower() does.
#
# WHAT IT STILL DOES NOT FOLD, and the direction each residue errs. All three
# err FAIL-OPEN -- a pair this table does not join is an alias the guard does
# not see -- and the first two were MEASURED to fold on this checkout's APFS
# volume, so neither is theoretical:
#
#   * FULL-CASEFOLD equivalences simple lowercasing does not reach: the 297 code
#     points whose str.casefold() differs from their str.lower(), among them
#     U+03C2 final sigma / U+03C3 sigma, U+017F long s / 's', U+00B5 micro sign
#     / U+03BC. That count is SOURCE CODE POINTS, one per code point cp for
#     which chr(cp).casefold() != chr(cp).lower(), and it is the whole residue
#     rather than the reachable part of it: 194 of the 297 casefold to a single
#     code point and 103 to more than one (the 297 land on 178 distinct
#     casefolded strings). Counting only the single-code-point 194 understates
#     what simple lowercasing misses, and this residue is fail-open, so the
#     understating direction is the dangerous one. str.casefold() does not build
#     the table because it is many-to-one on LENGTH -- 'ß' casefolds to 'ss' --
#     and a per-code-point substitution cannot express that without changing
#     what a name's components are. All four counts are asserted against the
#     running interpreter by the guard case, not restated from memory; they are
#     identical under Unicode 13, 14, 15 and 16 (measured).
#   * U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE, whose str.lower() is two
#     code points, excluded for the same reason. This is the one residue that
#     is NOT reachable here: APFS was measured to keep U+0130 and 'i' apart.
#   * NORMALIZATION, which is a different axis and not this table's. NFC and NFD
#     spellings of one name are joined where git can do it -- core.
#     precomposeUnicode, forced true for the alias pathspec, see
#     ALIAS_CONFIG_OVERRIDE -- and are NOT joined for the tracked-but-absent
#     entry this table exists for.
#
# THE TABLE DOES NOT PRESERVE BYTE LENGTH, for 26 of its pairs: U+023A 'Ⱥ' ->
# U+2C65 'ⱥ' grows from two UTF-8 bytes to three, U+212A KELVIN SIGN -> 'k'
# shrinks from three to one. Nothing in this module ever depended on that;
# stage-private-data.sh's cheap pre-filter did, and no longer does -- see "A
# CHEAP GATE FIRST" there.
#
# Where either name DOES exist the filesystem is asked instead, by
# _ondisk_relpath(), and none of the above applies.
# --- BEGIN GENERATED CASE FOLD (private_egress.py --regenerate-case-fold) ---
CASE_FOLD_UNICODE = "16.0.0"
CASE_FOLD_SED = """\
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
"""
# --- END GENERATED CASE FOLD ---
CASE_FOLD_MARKERS = ("# --- BEGIN GENERATED CASE FOLD "
                     "(private_egress.py --regenerate-case-fold) ---",
                     "# --- END GENERATED CASE FOLD ---")
# One substitution of the generated table, read back out of a block that is
# about to be replaced -- deliberately tolerant, because it is asked of text
# regenerate_case_fold() has not validated yet and its only job is to see which
# pairs would be LOST. What the table must be, exactly, is _parse_case_fold().
_CASE_FOLD_RULE = re.compile(r"s/([^/;\n]+)/([^/;\n]+)/g")


def case_fold_pairs():
    """The (upper, lower) code-point pairs the generated table covers, taken
    from python's own str.lower() -- the DECLARED DOMAIN, in one place.

    A pair is in exactly when str.lower() maps the code point to a single
    different code point. What that leaves out, and which way each omission
    errs, is written out above; the point of deriving it here is that the
    boundary is a two-line rule a reader can check rather than a table somebody
    typed.
    """
    out = []
    for cp in range(ord("A"), sys.maxunicode + 1):
        ch = chr(cp)
        lo = ch.lower()
        if lo != ch and len(lo) == 1:
            out.append((ch, lo))
    return tuple(out)


def case_fold_sed_script(pairs=None, per_line=8):
    """The generated table, as the sed script BOTH implementations use.

    `s/<upper>/<lower>/g`, one command per pair, packed `per_line` to a line
    with ';' separators so the block is an eighth of the rule count in lines
    rather than one line per rule. Every source and target is a letter, so
    nothing in it is a sed metacharacter, a '/', a newline or a "'" -- which is
    what lets the same text be a shell single-quoted literal, a python
    triple-quoted literal and a sed script with no escaping anywhere. Asserted,
    not assumed, by _check_case_fold_pairs().

    `pairs` defaults to case_fold_pairs(), which is the RUNNING interpreter's
    domain and not necessarily the committed one: what is committed is pinned at
    CASE_FOLD_UNICODE, so this returns the committed text only on an interpreter
    carrying that Unicode version. regenerate_case_fold() passes its pairs in
    for that reason.
    """
    rules = [f"s/{a}/{b}/g" for a, b in _check_case_fold_pairs(
        case_fold_pairs() if pairs is None else pairs)]
    return "\n".join(";".join(rules[i:i + per_line])
                     for i in range(0, len(rules), per_line))


def _check_case_fold_pairs(pairs):
    """`pairs` back, having proved the four properties the two implementations'
    agreement rests on. Cheap, and run at generation time rather than believed.

      1. no source or target carries a character that would have to be escaped
         in a sed script, a shell single-quoted string or a python string
      2. no target is also a source, so no rule can rewrite another rule's
         output. This is what makes sed's rule-at-a-time pass and this module's
         single leftmost regex pass the same function -- str.lower() is
         idempotent, so it holds by construction, and it is checked because the
         equivalence is load-bearing rather than obvious
      3. no source is a prefix of another source, so no two rules compete for
         one position. UTF-8 lead bytes are never continuation bytes, which
         makes this true across lengths as well as within one
      4. the pairs are sorted by code point, so a regeneration produces the same
         bytes on any machine
    """
    forbidden = set("/;'\"\\\n\r")
    src = [a for a, _ in pairs]
    tgt = [b for _, b in pairs]
    bad = sorted(c for c in set(src) | set(tgt) if forbidden & set(c))
    assert not bad, f"case-fold pair characters need escaping: {bad!r}"
    clash = sorted(set(src) & set(tgt))
    assert not clash, f"case-fold targets that are also sources: {clash!r}"
    encoded = sorted(a.encode() for a in src)
    prefix = [(a, b) for a, b in zip(encoded, encoded[1:]) if b.startswith(a)]
    assert not prefix, f"case-fold sources that prefix another: {prefix!r}"
    assert src == sorted(src), "case-fold pairs are not in code-point order"
    return tuple(pairs)


def _parse_case_fold(script):
    """The (source bytes, target bytes) pairs of `script` -- the reader that
    makes THE SHELL'S OWN TEXT this module's fold rather than a copy of it.

    Deliberately strict: a command that is not exactly `s/<from>/<to>/g` is an
    assertion failure at import, not a rule quietly skipped. A fold that silently
    lost a rule would answer "these two names are different" and the guard would
    ACCEPT, which is the failure direction issue #233 was about.
    """
    pairs = []
    for command in script.replace("\n", ";").split(";"):
        if not command:
            continue
        assert command.startswith("s/") and command.endswith("/g"), \
            f"CASE_FOLD_SED holds a command that is not a substitution: {command!r}"
        source, sep, target = command[2:-2].partition("/")
        assert sep and source and target, \
            f"CASE_FOLD_SED holds a malformed substitution: {command!r}"
        pairs.append((source.encode(), target.encode()))
    assert pairs, "CASE_FOLD_SED is empty: nothing would fold at all"
    return tuple(pairs)


CASE_FOLD_PAIRS = _parse_case_fold(CASE_FOLD_SED)
_CASE_FOLD_MAP = dict(CASE_FOLD_PAIRS)
# One leftmost pass over the raw bytes, which is the same function as sed's
# rule-at-a-time pass for this table -- see _check_case_fold_pairs() properties
# 2 and 3 for why. Compiled once: the alternation has one branch per table pair.
_CASE_FOLD_RE = re.compile(b"|".join(re.escape(s) for s, _ in CASE_FOLD_PAIRS))


def _unisolated_env(env=None):
    """`env` (default os.environ) with every variable DROPPED that sanitized_env()
    drops, and nothing written in its place.

    The half of sanitized_env() that stops a caller REPLACING git's answer,
    without the half that switches the operator's own configuration off. Exactly
    one read is made in it -- _ambient_protected_config(), which has to see the
    operator's system and global files to read them at all -- and dropping the
    GIT_CONFIG* family first is what keeps an inherited environment from
    supplying a safe.directory that read would then hand to the command line.
    """
    src = os.environ if env is None else env
    drop = (set(GIT_IDENTITY_VARS) | set(PATH_MEANING_VARS)
            | set(PATHSPEC_MEANING_VARS))
    return {k: v for k, v in src.items()
            if k not in drop and not k.startswith(GIT_CONFIG_PREFIX)}


def sanitized_env(env=None):
    """`env` (default os.environ) with every variable that can move git's answer
    -- or change what a relative path or a pathspec means -- removed, and the
    ambient git configuration switched off.

    Two operations, in this order: DROP what the caller (or the caller's shell)
    supplied, then WRITE the controlled values that make the probes answer from
    repository-local configuration alone. The drop comes first because the
    GIT_CONFIG* prefix covers the names the second half writes.
    """
    out = _unisolated_env(env)
    out.update(GIT_CONFIG_ISOLATION)
    return out


def _ambient_protected_config(cwd, env=None):
    """The operator's own AMBIENT_PROTECTED_KEYS values, as (key, value) pairs,
    taken from the system and global scopes only.

    Read with `config --show-scope --get-all` and filtered on the scope git
    itself reports, which is the whole of the safety argument: it is git's own
    rule for these keys, so a value in the DESTINATION's local or per-worktree
    config -- the one a forged destination could write -- is dropped here rather
    than promoted to the command line, where it would count.

    --show-scope, not `config --global`: measured on git 2.50.1, `config
    --global --get-all safe.directory` reports NOTHING for a value the same git
    reads and applies, because that spelling reads ~/.gitconfig alone -- not
    $XDG_CONFIG_HOME/git/config, and not an `include.path` from either. The
    effective read with the scope printed beside each value returns both
    (measured: scope `global`, origins the XDG file and the included file), and
    a `--local` value is labelled `local` and dropped here.

    Values are returned RAW, exactly as git printed them, so `~/x`,
    `%(prefix)/x`, `*` and the empty entry that RESETS the list keep the meaning
    they have in the file, in the order git read them.

    A git without --show-scope (2.26, Mar 2020) exits non-zero and this returns
    nothing, which is correct rather than merely safe: safe.directory arrived in
    2.35.2 (Mar 2022), so that git has no ownership refusal to lift.
    """
    out = []
    for key in AMBIENT_PROTECTED_KEYS:
        r = subprocess.run(
            ["git", "-C", str(cwd), "config", "--show-scope", "-z",
             "--get-all", key],
            capture_output=True, text=True, env=_unisolated_env(env))
        if r.returncode != 0:
            continue
        fields = r.stdout.split("\0")
        for scope, value in zip(fields[0::2], fields[1::2]):
            if scope in PROTECTED_SCOPES:
                out.append((key, value))
    return tuple(out)


def _git(args, cwd, env=None, overrides=GIT_CONFIG_OVERRIDES):
    """Every git this module runs, with the isolation attached to the command
    line as well as to the environment.

    The -c options come first, before -C, so they are read as written whatever
    the working directory turns out to be. No caller assembles its own git
    argument vector -- asserted by
    test_private_egress.case_every_git_invocation_carries_the_configuration_
    override -- because an isolation a later probe can forget to apply is the
    defect this funnel exists to make impossible.

    `overrides` defaults to the constant table, which is every -c whose value is
    known without asking anything: it applies to identity and register probes,
    which no destination-derived key moves. The two probes whose ANSWER those
    keys move are handed the fuller list _require_isolation_proven() has just
    proved -- see _destination_overrides().

    AND ONE RETRY, on git's fatal status alone. Emptying the global and system
    configuration also empties the operator's `safe.directory` entries, which
    git honours from nowhere else, so a repository they have already declared
    safe answers every probe with `fatal: detected dubious ownership` -- a
    correct caller refused by the isolation itself. When that happens the
    command is re-run once with those entries, and only those, put back on the
    command line. It is deliberately reactive: a value that arrives only where
    git gave no answer at all cannot change an answer it did give, so the
    verdicts stay the isolation's. See "WHAT THE ISOLATION MUST NOT TAKE AWAY".
    """
    opts = [tok for name, value in overrides
            for tok in ("-c", f"{name}={value}")]

    def run(extra):
        # errors="surrogateescape", not the default "strict": `ls-files -z`
        # prints index paths RAW, and a destination whose history carries a
        # filename that is not valid UTF-8 would otherwise raise
        # UnicodeDecodeError out of the guard instead of producing a verdict.
        # Round-trips through os.fsencode(), which is what _case_fold() folds
        # with, so such a name is still compared on its real bytes.
        return subprocess.run(["git"] + opts + extra + ["-C", str(cwd)] + list(args),
                              capture_output=True, text=True,
                              errors="surrogateescape", env=sanitized_env(env))

    r = run([])
    if r.returncode != GIT_FATAL:
        return r
    ambient = [tok for name, value in _ambient_protected_config(cwd, env)
               for tok in ("-c", f"{name}={value}")]
    return run(ambient) if ambient else r


def _destination_config(worktree, key, default, env=None):
    """`key`'s value as the DESTINATION's own repository configuration states
    it, normalized to "true"/"false", or `default` when it states none.

    Read with `git config <scope> --bool --get`, and the scope is what keeps the
    read clean: --worktree and --local name one file each, inside the
    destination's own git directory, so neither the ambient configuration this
    is about to override nor the -c that overrides it can contaminate the
    answer. Measured, because a read that quietly saw the value it was about to
    force would prove nothing: with core.ignoreCase=true in $HOME/.gitconfig and
    -c core.ignoreCase=false on the command line, `config --local --bool --get`
    still printed the destination's own `true`, and printed nothing (exit 1) in
    a repository that states none. A scope is not optional: an effective `config
    --get` would read the -c this module has already put on the command line, so
    the read would confirm the value it is about to force and the destination
    would never be consulted at all.

    --bool for the spelling, not for the truth: git accepts `yes`, `on`, `1` and
    a valueless key, and all of them have to arrive at the readback in
    _require_isolation_proven() as the same word git would print back. A value
    git cannot read as boolean exits non-zero here and takes the default -- and
    costs nothing, because that same value makes every other git command in that
    repository fatal (measured: check-ignore, ls-files and rev-parse all exit
    128 on `ignorecase = bogus`, with the -c in place), so the probes below
    refuse it as unanswerable rather than believing the default.
    """
    for scope in DESTINATION_CONFIG_SCOPES:
        r = _git(["config", scope, "--bool", "--get", key], worktree, env)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return default


def _destination_overrides(worktree, env=None):
    """The full -c list for a probe asked ABOUT `worktree`: the constant
    overrides, plus each destination-derived key set to what that repository
    itself says (DESTINATION_CONFIG_KEYS).

    Derived per call rather than once per run, for the reason
    _require_isolation_proven() is: a config file is an ordinary file that can
    be rewritten between the first probe and the last.
    """
    derived = tuple((key, _destination_config(worktree, key, default, env))
                    for key, default in DESTINATION_CONFIG_KEYS)
    return tuple(GIT_CONFIG_OVERRIDES) + derived


def _alias_overrides(overrides):
    """The -c list for the ALIAS probe: the proved list, with
    ALIAS_CONFIG_OVERRIDE appended.

    APPENDED rather than substituted, and the last `-c` for a key is the one git
    uses, so this states the one value it changes instead of restating the whole
    list -- there is no second derivation for the two to drift apart, which is
    the property _require_isolation_proven() exists to keep.

    A pure function of the list already proved, so the caller cannot hand the
    alias probe a set of overrides that was never checked.
    """
    return tuple(overrides) + ALIAS_CONFIG_OVERRIDE


def _ascii_case_flip(name):
    """`name` with every ASCII letter's case swapped, and nothing else touched.

    ASCII AND ONLY ASCII, deliberately, because the shell measures with
    `LC_ALL=C tr 'A-Za-z' 'a-zA-Z'` and the two implementations have to fold the
    same bytes. str.swapcase() would fold 'É' as well, and a directory whose only
    case-varying entry is non-ASCII would then be measured by python and not by
    the shell -- the two would part company on a fixture nobody wrote.
    """
    return "".join(c.lower() if "A" <= c <= "Z"
                   else c.upper() if "a" <= c <= "z"
                   else c for c in name)


def _dir_folds_case(directory):
    """True / False / None: does `directory` itself resolve two case spellings of
    one of its OWN entries to one file, or could that not be measured?

    The generic form of the `.git`/`.GIT` probe below, for a directory that has
    no name every instance is guaranteed to have. The entries are listed, the
    first one whose ASCII case can be flipped is stat'd under both spellings,
    and equal (st_dev, st_ino) is the answer -- the same comparison, the same
    two-stat shape, and still nothing written.

    Entries are taken in sorted order, matching the shell's LC_ALL=C glob, so
    both implementations pick the SAME entry to measure. An entry that cannot be
    stat'd under its own name (a dangling symlink) is skipped rather than read as
    "does not fold": stat fails for both spellings there, which is not a
    measurement.

    None means UNMEASURABLE, and it has two causes with one remedy: the
    directory could not be listed, or it holds no entry with an ASCII letter in
    it (an empty directory is the ordinary case). The caller treats that as
    folding -- see _fs_folds_case().

    KNOWN IMPRECISION, AND WHY IT STAYS (issue #231, AC4). A case-sensitive
    directory holding two HARD LINKS to one inode under case-aliased names
    ('README' and 'readme') measures as folding, because device and inode are
    what folding looks like. It could be told apart -- on a folding filesystem
    only ONE of the two spellings is an entry of the directory, so seeing both in
    the listing proves the directory does not fold -- and it is deliberately not,
    because that same shape is the only instrument a test has: a case-aliased
    pair sharing an inode is how test_private_egress builds a folding directory
    on a case-sensitive machine, mounting being something a test suite may not
    leave behind. Refusing to model it would make the per-directory mechanism
    untestable everywhere except on a machine that folds anyway.

    It errs toward folding, the additive direction, and it takes a destination
    built to look like one. What it can no longer do is contaminate the rest of
    the path: the answer is per component now, so a directory that measures
    folding for this reason folds only its OWN entries.
    """
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in names:
        if name.endswith("\n"):
            # The shell flips case through `$( )`, which strips trailing
            # newlines, so it cannot carry this name to `tr` and back. Skipped on
            # BOTH sides rather than measured on one: an entry only python can
            # ask about is an entry the two implementations disagree on.
            continue
        flipped = _ascii_case_flip(name)
        if flipped == name:
            continue
        here = os.path.join(directory, name)
        try:
            os.stat(here)
        except OSError:
            continue                     # a dangling link measures nothing
        return _same_file(here, os.path.join(directory, flipped))
    return None


def _root_folds_case(worktree):
    """The worktree ROOT's own answer, from a name every worktree root has.

    Two stat() calls on a name that is already there:

        <worktree>/.git   and   <worktree>/.GIT

    equal (st_dev, st_ino) means the filesystem resolved both spellings to one
    file, which is the whole of the question. A case-sensitive filesystem
    answers ENOENT for the second, and one that really holds a separate `.GIT`
    answers with a different inode -- both correctly "no".

    NOTHING IS WRITTEN, which settles the ordering problem this measurement
    would otherwise pose. The reliable way to detect case behaviour is usually
    to create a probe file and see whether the alias resolves; here that would
    mean writing inside a destination the guard has not yet decided it may write
    to, so the measurement would have to precede -- and could not be conditioned
    on -- its own verdict. Using an entry the destination already has removes the
    ordering question rather than answering it: stat() creates nothing, opens
    nothing (so a FIFO cannot block it) and follows no final symlink into a
    write. It is safe at any point, and it is asked here, before the probe whose
    pathspec it decides.

    FAILS CLOSED TOWARD FOLDING. A `.git` that cannot be stat'd at all leaves the
    question unanswered, and the folding answer is the conservative one: the
    alias probe is additive, so folding can only make the guard refuse more.
    """
    try:
        here = os.stat(os.path.join(worktree, CASE_PROBE_NAME))
    except OSError:
        return True
    try:
        there = os.stat(os.path.join(worktree, CASE_PROBE_ALIAS))
    except OSError:
        return False
    return (here.st_dev, here.st_ino) == (there.st_dev, there.st_ino)


def _fs_folds_case(worktree, relpath=None, evidence=None):
    """Does a write to `<worktree>/<relpath>` land on a filesystem that treats two
    spellings differing only in case as ONE file? MEASURED at every directory the
    path crosses, not inferred from sys.platform and not taken at the root alone.

    THE ROOT IS NOT THE FILESYSTEM THE WRITE LANDS ON. The probe used to be one
    pair of stats at the worktree root, and a case-insensitive volume mounted
    BELOW that root was therefore measured as case-sensitive: `:(icase)` was left
    off, and an index entry differing only in case -- with no file in the working
    tree, so the on-disk walk has nothing to resolve either -- was found by
    neither of the two alias probes. Reproduced on macOS 15 with two attached
    disk images, a case-sensitive APFS volume holding the worktree and a
    case-insensitive HFS+ volume mounted at `<root>/private`, an index holding
    `private/HOUSEHOLD.yaml` written with `update-index --cacheinfo` so no file
    ever existed for it:

        _fs_folds_case(root)                     -> False   (right about the root)
        _ondisk_relpath(private/household.yaml)  -> unchanged (nothing to resolve)
        check-ignore ./private/household.yaml    -> 0, ignored
        the guard ACCEPTED, the copy wrote private/household.yaml, and then
        git status              ->  AM private/HOUSEHOLD.yaml
        git add -A; git diff --cached  ->  the private bytes, under the tracked name

    So the measurement follows the path. The root keeps its own probe -- `.git`
    is the one entry a worktree root is guaranteed to have, and asking about it
    costs no directory listing -- and where that says "folds" the answer is
    already the additive one and the walk is skipped. Where it says "case is
    significant", every EXISTING directory the path descends into is measured in
    turn with _dir_folds_case(), and the FIRST one that folds decides.

    THAT IS AN OR ALONG THE PATH, deliberately, and it is not the equivalence
    relation -- it is the CANDIDATE GENERATOR. ':(icase)' is applied to the whole
    pathspec by git, so this is the only question a pathspec can be asked, and a
    narrower answer here would leave index entries unfound. Being over-broad is
    therefore the correct behaviour of THIS function and was, on its own, issue
    #231: a case-SENSITIVE volume mounted under a folding directory had every
    component below the folding one folded too, and a destination that should be
    accepted was refused. What closed it is not a narrower OR but a second
    measurement kept alongside -- _fold_vector() answers per component, and every
    entry the pathspec returns is filtered against it in _classify_alias() before
    it may refuse anything.

    AN UNMEASURABLE DIRECTORY COUNTS AS FOLDING, by the same argument the root
    probe already used for an unstattable `.git`: the alias probe is additive, so
    folding can only make the guard refuse more, and refusing more is what an
    unanswered question deserves. It costs a refusal only for a destination that
    ALSO holds a case-aliased entry in its index -- which is the thing being
    guarded against wherever the two spellings really are one file, and a false
    alarm where they are not. An EMPTY case-sensitive volume is that false alarm
    and stays one: nothing in it can be measured, so the fold vector reads it as
    folding too.

    NOT a device-change test, though that was the other candidate. Comparing
    st_dev at each component would say WHERE to measure and still not say what
    that filesystem does, so it would need this probe anyway; and the shell has
    no portable st_dev -- `[ -ef ]` compares device and inode together and cannot
    separate them, so the two implementations would have had to detect mount
    boundaries by different means (st_dev against `df -P`) and could disagree on
    a bind mount. Measuring behaviour directly needs no notion of a mount at all,
    and answers for a filesystem this module has never heard of.

    `evidence`, when a list is passed, gets one line saying where the answer came
    from, for the refusal message. The shell sets _FOLDS_WHERE for the same
    reason and in the same words.
    """
    if _root_folds_case(worktree):
        if evidence is not None:
            evidence.append(
                "yes  (measured: .git and .GIT are one file at the worktree root)"
                if os.path.exists(os.path.join(worktree, CASE_PROBE_NAME))
                else f"yes  (unmeasurable: {os.path.join(worktree, CASE_PROBE_NAME)} "
                     "could not be stat'd)")
        return True
    cur = worktree
    for part in (p for p in (relpath or "").split("/") if p):
        below = os.path.join(cur, part)
        if not os.path.isdir(below):
            break                        # the walk has left the existing tree
        cur = below
        folds = _dir_folds_case(cur)
        if folds is True:
            if evidence is not None:
                evidence.append("yes  (measured: two case spellings are one file "
                                f"in {cur})")
            return True
        if folds is None:
            if evidence is not None:
                evidence.append(f"yes  (unmeasurable: {cur} holds no entry whose "
                                "ASCII case can be flipped)")
            return True
    if evidence is not None:
        evidence.append("no   (measured at the worktree root and at every existing "
                        "directory below it on this path)")
    return False


def _case_fold(name):
    """`name`'s bytes under the generated table at CASE_FOLD_SED -- the same
    text the shell's `_case_fold` runs through one `LC_ALL=C sed`.

    On BYTES, and os.fsencode() rather than str.encode(), because a destination
    whose history carries a filename that is not valid UTF-8 still has to be
    compared: _git() reads index paths with errors="surrogateescape", and this
    round-trips those surrogates back to the original bytes. A rule can only
    match at a UTF-8 lead byte, so an invalid run folds nowhere and is compared
    as it stands -- the same thing sed does with it.
    """
    return _CASE_FOLD_RE.sub(lambda m: _CASE_FOLD_MAP[m.group(0)],
                             os.fsencode(name))


def _fold_vector(worktree, relpath):
    """Which of `relpath`'s components may fold case, ONE ANSWER PER COMPONENT
    (issue #231) -- `folds[i]` is what the directory that CONTAINS component i
    does, never what some other directory on the path does.

    _fs_folds_case() above answers a different question and keeps answering it:
    "is a fold possible ANYWHERE on this path", which is the only question git's
    pathspec can be asked, because ':(icase)' applies path-wide. That OR is the
    right CANDIDATE GENERATOR -- a narrower one would leave index entries
    unfound -- and the wrong equivalence relation. This vector is what the
    candidates are then filtered against, so a folding ANCESTOR no longer makes
    a component below it fold: with a case-sensitive volume mounted at
    `private/sensitive/`, an index entry `private/sensitive/FOO` is a genuinely
    different file from `private/sensitive/foo`, and the pathspec matches it
    anyway. Reproduced on three real nested mounts before this existed.

    folds[i] is measured at the directory `<worktree>/<parts[0..i-1]>`:

      i == 0                the worktree root, by _root_folds_case()
      the directory EXISTS  _dir_folds_case(); UNMEASURABLE counts as folding,
                            by the argument the root probe already uses -- an
                            unanswered question deserves the refusing answer
      it does NOT exist     the answer of the nearest existing ancestor, and
                            that is a deduction rather than a default: a
                            directory the caller is about to CREATE is created
                            inside its parent, on its parent's filesystem, and
                            no volume can be mounted at a path that is not there

    The cost is one listdir per EXISTING directory on the path -- the worktree
    root, `private/`, and for a glob-expanded leaf `private/1-raw-data`. The
    walk does not descend past the path it was given.
    """
    parts = [p for p in relpath.split("/") if p]
    out = []
    cur = worktree
    here = _root_folds_case(worktree)
    for part in parts:
        out.append(here)
        below = os.path.join(cur, part)
        if os.path.isdir(below):
            cur = below
            measured = _dir_folds_case(cur)
            here = True if measured is None else measured
        # else: absent, or a non-directory. Whatever the caller creates there
        # lands on the filesystem `cur` is already on, so `here` carries over.
    return tuple(out)


ALIAS_CLASSES = ("spurious", "match", "keep")


def _classify_alias(relpath, entry, folds):
    """How an index `entry` relates to `relpath`, judged COMPONENT BY COMPONENT
    against `folds` -- one of ALIAS_CLASSES, and the shell prints the same three
    words from the same comparison.

      "spurious"  some component differs ONLY BY CASE where that component's own
                  parent directory does not fold. git's ':(icase)' is applied
                  path-wide, so a folding ANCESTOR matched two files that really
                  are two files. Dropped -- this is issue #231.
      "match"     every component is equal, or fold-equal in a directory that
                  folds. A write to `relpath` lands on this entry -- issue #230
                  when the pathspec could not see it.
      "keep"      neither: it differs some other way. Unicode composition, which
                  core.precomposeUnicode folds on an axis of its own, and a glob
                  match both land here, so nothing about the case axis may drop
                  them.

    An entry DEEPER than the candidate is judged on the components they share:
    writing into `private/verify` lands on `private/verify/usage.csv` too. An
    entry SHORTER than the candidate shares no such relationship, and is kept
    rather than dropped -- the fail-closed direction for a shape neither probe
    produces.
    """
    cand = [p for p in relpath.split("/") if p]
    got = [p for p in entry.split("/") if p]
    if len(got) < len(cand):
        return "keep"
    out = "match"
    for i, (c, e) in enumerate(zip(cand, got)):
        if c == e:
            continue
        if _case_fold(c) == _case_fold(e):
            if not (i < len(folds) and folds[i]):
                return "spurious"
        else:
            out = "keep"
    return out


def _index_case_aliases(worktree, relpath, folds, overrides, env=None):
    """Index entries that are this same path under the filesystem's own case
    folding, found by ENUMERATING THE INDEX rather than by handing git a
    pathspec (issue #230).

    THE HOLE THIS CLOSES. The two probes that were here fold different things
    and both stop short of the same combination: ':(icase)' is byte-oriented, so
    it folds ASCII case pairs and no others, and the on-disk walk folds whatever
    the filesystem folds but resolves a path only AS FAR AS IT EXISTS. An index
    entry that is tracked, has no working-tree file, and differs from the
    candidate only in NON-ASCII case was therefore seen by neither. Reproduced
    on macOS 15 (APFS, case-insensitive, git 2.50.1) with an index holding
    `private/HOUSEHÖLD.yaml` written by `update-index --cacheinfo` and no file on
    disk for it: the guard ACCEPTED `private/househöld.yaml`, the write created
    the file, and `git add -A` then staged the private bytes under the committed
    name --

        git status --porcelain   ->  AM "private/HOUSEH\\303\\226LD.yaml"
        git diff --cached        ->  the private bytes, under the tracked path

    WHY THE ANSWER HAS TO COME FROM THE INDEX. Extending the on-disk walk past
    the last existing component means asking the filesystem about a name that is
    not there, and it has no answer to give: for an absent leaf, `lexists` being
    false IS the statement that the directory holds no entry the filesystem
    would call the same name. So the directory is enumerated on the side that
    does hold something -- git's index -- and the candidate is compared against
    those entries component by component, under the fold vector for the case
    axis and the generated fold at CASE_FOLD_SED for the names themselves.

    THE FOLD VECTOR IS WHAT KEEPS IT NARROW. A component may fold only where its
    own parent directory was measured to fold, so on a case-sensitive
    destination this enumeration matches nothing at all, and on a folding one it
    matches exactly the entries a write would land on. It is skipped outright
    where no component folds.

    COST: one `git ls-files -z` per checked path, on top of the two pathspec
    questions already asked. It reads the index and stats nothing, and the whole
    index is read rather than a directory prefix so that a fold in a component
    ABOVE the leaf is covered by the same pass -- the leaf is where issue #230
    put it, but nothing about the mechanism is special to the last component.
    """
    if not any(folds):
        return []
    r = _git(["ls-files", "-z"], worktree, env, overrides)
    if r.returncode != 0:
        raise DestinationRefused(
            "tracked_unanswerable",
            f"'git ls-files' could not list the index of {worktree}, so the "
            "guard cannot say whether this destination already tracks a path "
            "its filesystem would treat as this one: "
            f"{(r.stderr or '').strip()[:200]}",
            os.path.join(worktree, relpath))
    return [e for e in r.stdout.split("\0") if e
            and _classify_alias(relpath, e, folds) == "match"]


def _same_file(a, b):
    """The shell's `[ a -ef b ]`, spelled the same way it is: stat(2) on both,
    equal device and inode, and FALSE when either cannot be stat'd.

    stat and not lstat, because `-ef` follows links and the shell has no
    lstat-shaped test. Keeping the two implementations on one predicate matters
    more here than the choice itself: the only place the difference shows is a
    directory holding two case-aliased SYMLINKS to one target, where lstat would
    pick the entry and stat picks whichever comes first -- and a symbolic link on
    a walked component is refused by both implementations anyway. A dangling
    link is stat-unreadable, so it matches nothing and _ondisk_relpath() below
    fails closed on it rather than guessing.
    """
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


def _ondisk_relpath(worktree, relpath):
    """`relpath` as the destination's filesystem ACTUALLY SPELLS IT (issues #223,
    #224) -- the path a write to `relpath` would land on, not the path the
    caller typed.

    WHY THIS EXISTS AT ALL. Every question this module puts to git is a question
    about a path, and git answers it about the bytes it was handed. The
    filesystem does not: on a case-folding volume `private/1-raw-data` and
    `private/1-RAW-DATA` are one directory, and a `.gitignore` that says
    `private/1-raw-data` covers only one of the two spellings once
    core.ignoreCase is false -- which is the value #193 forces when the
    destination states none. Measured, in a worktree of this checkout whose
    ignore rules name the leaves:

        on disk:   private/1-RAW-DATA        (untracked, created by hand)
        asked:     private/1-raw-data
        git check-ignore ./private/1-raw-data   -> 0   IGNORED
        git check-ignore ./private/1-RAW-DATA   -> 1   NOT ignored
        both implementations ACCEPTED, the copies ran, and afterwards
        git status reported:  ?? private/1-RAW-DATA/

    which is the 2026-08-13 incident shape exactly: the whole archive in a tree
    that does not ignore it, one `git add -A` from a commit.

    #204's remedy cannot be reused for it. That one re-asks `ls-files` with git's
    own `:(icase)` pathspec magic, and `git check-ignore` REJECTS pathspec magic
    outright -- measured on git 2.50.1, `:(icase)./private/1-raw-data` exits 128,
    "pathspec magic not supported by this command". Nor is `:(icase)` the whole
    answer even where it is accepted: it is BYTE-oriented, so it folds ASCII case
    pairs and no others. Measured in a scratch repository holding both spellings,
    with an ASCII control alongside so the contrast cannot be the fixture:

        git ls-files -- ':(icase)./private/household.yaml'  -> private/HOUSEHOLD.yaml
        git ls-files -- ':(icase)./private/househöld.yaml'  -> (nothing)
        ... while the filesystem resolves BOTH pairs to one file

    core.precomposeUnicode, which _alias_overrides() forces, normalizes
    COMPOSITION and does nothing about case; the two are independent axes.

    So neither implementation models the fold. This walks the path instead, one
    component at a time, and asks the filesystem which entry each component
    names -- ASCII, non-ASCII, normalization and anything a future volume folds,
    without a line of Unicode in either language, FOR THE COMPONENTS THAT EXIST.

    THE PATH USUALLY DOES NOT EXIST YET, which is the point of the module: the
    walk resolves as far as the path really goes and takes the REST exactly as
    asked. `private/1-raw-data` in a tree that holds only `Private/` resolves to
    `Private/1-raw-data`. An absent LEAF is therefore asked about as typed, and
    that is not a gap so much as the limit of what a filesystem can be asked:
    `lexists` being false for the leaf IS the statement that the directory holds
    no entry the filesystem would call the same name. The tracked question for
    such a leaf is answered from the other side instead, by enumerating the index
    -- see _index_case_aliases() (issue #230).

    HOW A COMPONENT'S REAL SPELLING IS FOUND, and what it costs. The parent is
    listed and the component is looked for BY NAME first; only when it is not
    there under its own name -- and the path exists anyway, which is the fold --
    are the parent's entries compared against it with _same_file(). The
    by-name test is what makes the ordinary case one listdir and no stat, and it
    is also correctness rather than speed: two hard-linked entries share an
    inode, so an inode-first search could rename a component that is on disk
    under exactly the name it was asked about. The cost is ONE listdir per
    EXISTING component, and it is bounded by nothing but the size of those
    directories: the walk here is `<root>/private/<leaf>`, so the directories
    listed are the worktree root, `private/`, and (for the glob-expanded leaves)
    `private/1-raw-data` -- tens of entries, not a tree walk. Entries are sorted
    so that a tie between several inode-equal entries resolves the same way here
    and in the shell, whose glob is sorted too.

    FAILS CLOSED, with its own reason, on a component that cannot be read
    (`spelling_unresolved`). That is a new failure mode this walk creates and it
    gets its own code rather than borrowing `not_ignored`: an unreadable
    directory mid-path means the guard cannot say WHICH path the write lands on,
    which is not the same statement as "that path is committable", and the two
    have different remedies. It covers a directory with no read or no search
    permission, and a component that exists but matches no entry of its own
    parent -- a case-aliased dangling symlink, or an entry removed underneath the
    walk.

    TWO mechanisms guard it, and both are needed -- measured, not doubled for
    comfort. The explicit os.access() mirrors the shell's `[ -r ]`/`[ -x ]`,
    which the shell cannot do without: a glob over an unreadable directory
    expands to NOTHING rather than failing, so the shell would read "cannot list"
    as "empty". The try/except catches what access() cannot promise -- it answers
    for the real uid, and a directory can stop being listable between the two
    calls. They also cover different modes: at mode 0444 os.listdir SUCCEEDS
    while the directory is not searchable, so without access() python would walk
    on where the shell refuses, and the two implementations would part company on
    a fixture nobody had written.

    NOT fail-closed, deliberately: a component that is absent, and a component
    whose parent is not a directory at all. Neither is unanswerable -- the first
    is the ordinary "the caller will create it", and the second is refused by
    name (`not_a_directory`) by the component walk in _check_destination(). Both
    take the rest of the path as asked, which is what those checks then report.
    """
    parts = [p for p in relpath.split("/") if p]
    cur = worktree
    out = []
    for i, part in enumerate(parts):
        if not os.path.isdir(cur):
            out.extend(parts[i:])       # nothing below a non-directory to resolve
            break
        if not os.access(cur, os.R_OK | os.X_OK):
            raise DestinationRefused(
                "spelling_unresolved",
                f"{cur} cannot be read and searched, so the entry it holds for "
                f"{part!r} cannot be found. On a filesystem that folds case or "
                "normalization the name a write lands on is not the name it was "
                "given, and this guard asks git about the name it lands on -- "
                "with that directory unreadable there is no such name to ask "
                "about. Fix the permissions on it, or name a destination whose "
                "path this run can read",
                os.path.join(worktree, relpath))
        try:
            names = sorted(os.listdir(cur))
        except OSError as e:
            raise DestinationRefused(
                "spelling_unresolved",
                f"{cur} could not be listed ({e.__class__.__name__}: {e}), so "
                f"the entry it holds for {part!r} -- and therefore the path a "
                "write would actually land on -- is unknown",
                os.path.join(worktree, relpath))
        if part in names:
            real = part
        elif os.path.lexists(os.path.join(cur, part)):
            # It resolves, under a spelling that is not the one asked for: the
            # filesystem folded it. Which entry did it fold to?
            real = next((n for n in names
                         if _same_file(os.path.join(cur, n), os.path.join(cur, part))),
                        None)
            if real is None:
                raise DestinationRefused(
                    "spelling_unresolved",
                    f"{os.path.join(cur, part)} resolves to something, and no "
                    f"entry of {cur} is that same file -- so the guard cannot "
                    "name the path a write would land on. A symbolic link with "
                    "no target, or a directory changing underneath this run, "
                    "both look like this. 'Probably somewhere' is not a property "
                    "to write a private archive on",
                    os.path.join(worktree, relpath))
        else:
            out.extend(parts[i:])       # not there yet; the caller creates it
            break
        out.append(real)
        cur = os.path.join(cur, real)
    return "/".join(out)


def _physical(path):
    """The shell's `(cd -- "$1" && pwd -P)`: an absolute, symlink-resolved path,
    or None when it is not an existing directory."""
    try:
        if not os.path.isdir(path):
            return None
        return os.path.realpath(path)
    except OSError:
        return None


def _locate_common_git_dir(path, env=None):
    """(the `--git-common-dir` of `path` -- absolute and symlink-resolved -- or
    None, and what git said when it would not answer).

    --path-format=absolute is load-bearing: without it rev-parse returns a
    RELATIVE ".git" from a repo root and an ABSOLUTE path from a linked
    worktree, so a naive comparison rejects a legitimate destination. git < 2.31
    has no such flag, so its (already cwd-relative) answer is normalized instead
    of being compared raw.

    THE SECOND HALF is the refusal's evidence, and it exists because a swallowed
    fatal reads exactly like "no repository here". Every caller of this turns a
    None into prose -- "not inside a git working tree", with a `git worktree
    add` remedy -- and for the two failures that are not about a missing
    repository at all (dubious ownership; a repository whose config or object
    store git cannot read) that prose names the wrong cause and offers a remedy
    that cannot work. git's own message says which it is, and for ownership it
    carries the exact `git config --global --add safe.directory ...` command, so
    it is passed through verbatim rather than paraphrased.
    """
    if not os.path.isdir(path):
        return None, ""
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path, env)
    if r.returncode != 0:
        r = _git(["rev-parse", "--git-common-dir"], path, env)
        if r.returncode != 0:
            return None, (r.stderr or "").strip()
    out = r.stdout.strip()
    if not out:
        return None, (r.stderr or "").strip()
    return _physical(out if os.path.isabs(out) else os.path.join(str(path), out)), ""


def common_git_dir(path, env=None):
    """`--git-common-dir` of `path`, absolute and symlink-resolved, or None."""
    return _locate_common_git_dir(path, env)[0]


def self_common_git_dir(env=None):
    """The common dir of the checkout THIS FILE lives in, or None.

    The reference is this file's own resolved location, exactly as the shell
    takes ${BASH_SOURCE[0]} through its link chain first: which COPY is running
    decides which repository's worktrees are eligible, and a copy of this module
    on some other path must not authorize this checkout's destinations.
    """
    return _locate_common_git_dir(str(ROOT), env)[0]


def _parse_worktree_records(text, sep):
    """`git worktree list --porcelain` output -> worktree paths, bare ones dropped.

    Mirrors the shell's _scan_record: `worktree <path>` opens an entry, `bare`
    voids it, an empty record closes it. Read with -z where possible because git
    emits a worktree PATH RAW -- newlines included -- so a line-based parse of a
    path containing one recovers a TRUNCATED PREFIX: not merely a lost entry, an
    INVENTED one, and a genuinely registered destination refused.
    """
    paths, pending, entries = [], None, 0
    def close():
        nonlocal pending
        if pending is not None:
            paths.append(pending)
            pending = None
    for rec in text.split(sep):
        if rec.startswith("worktree "):
            close()
            entries += 1
            pending = rec[len("worktree "):]
        elif rec == "bare":
            pending = None
        elif rec == "":
            close()
    close()
    return paths, entries


def _resolve_register(paths):
    """A list of worktree paths -> the same list physically resolved, deduped,
    with everything that is not an existing directory dropped.

    ONE normalizer for both registers -- the one read from git and the one an
    internal caller supplies -- because _locate_worktree() compares against the
    RESOLVED ancestors of the destination. Resolving one side and not the other
    made membership depend on the spelling the caller happened to use: on macOS a
    tempdir is /var/folders/... whose realpath is /private/var/folders/..., so
    the same directory matched when passed as a realpath and did not when passed
    literally.

    An entry whose directory no longer EXISTS resolves to nothing and is dropped
    here, which settles exactly one of the two ways an entry goes stale -- the
    prunable one. The other, a directory that exists and belongs to somebody
    else now, survives this function by construction (it resolves perfectly
    well) and is settled by _confirm_register_entry() against the matched entry.
    Neither is decided here on its own.
    """
    out = []
    for p in paths:
        real = _physical(p)
        if real and real not in out:
            out.append(real)
    return out


def registered_worktrees(common_dir=None, env=None):
    """Every registered worktree of this checkout, physically resolved.

    Returns [] when the register could not be read at all -- callers treat that
    as a refusal, never as "no restrictions". A registered entry whose directory
    no longer exists resolves to nothing and is dropped; an entry whose
    directory exists is returned AS GIT RECORDED IT and is not believed on that
    alone -- _confirm_register_entry() re-asks the matched one.

    Falls back to the line-based listing whenever -z yields nothing (a git that
    predates `worktree list -z` writes usage to stderr and nothing to stdout),
    rather than declaring a minimum git version: a refusal a correct caller
    cannot fix is the failure mode this whole module exists to repair.
    """
    if common_dir is None:
        common_dir = self_common_git_dir(env)
    if not common_dir:
        return []
    r = _git(["worktree", "list", "--porcelain", "-z"], common_dir, env)
    paths, entries = ([], 0) if r.returncode != 0 else _parse_worktree_records(r.stdout, "\0")
    if entries == 0:
        r = _git(["worktree", "list", "--porcelain"], common_dir, env)
        if r.returncode == 0:
            paths, entries = _parse_worktree_records(r.stdout, "\n")
    return _resolve_register(paths)


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------
def _ancestors(path):
    """`path` and every ancestor, longest first."""
    p = pathlib.PurePath(path)
    out = [str(p)]
    while str(p.parent) != str(p):
        p = p.parent
        out.append(str(p))
    return out


def _nearest_existing(path):
    for a in _ancestors(path):
        if os.path.lexists(a):
            return a
    return None


def _locate_worktree(path, worktrees):
    """(literal prefix, resolved worktree root) of the registered worktree that
    contains `path`, or (None, None).

    Matched by resolving each LITERAL ancestor and comparing against the
    register -- so symlinks above the root are resolved (they must be) while the
    literal prefix is kept, which is what makes the symlink walk below able to
    see a link BELOW the root. The longest match wins, so a worktree nested
    inside another is attributed to the inner one.

    `worktrees` must ALREADY be resolved -- both sides of this comparison are
    physical paths or neither is. Every caller gets it from _resolve_register(),
    which is the one place that guarantees it.
    """
    for a in _ancestors(path):          # longest first
        real = _physical(a)
        if real and real in worktrees:
            return a, real
    return None, None


def _confirm_register_entry(wt_real, self_git, env=None):
    """The matched register entry must STILL be a worktree of this checkout.

    `git worktree list` is a RECORD OF A DIRECTORY, not a question asked of it.
    git writes the entry when `git worktree add` succeeds and revalidates it
    only for a directory that is GONE (`git worktree prune`); a directory that
    exists keeps its entry whatever is now in it. Measured on git 2.50.1, not
    assumed -- and each of the three outcomes below is one of those
    measurements:

      * directory deleted, path re-created and `git init`-ed by another project
        -- the shape `~/limits-wt/` invites, where paths are reused across
        projects and worktrees are abandoned rather than removed. The entry
        SURVIVES `git worktree prune` (the directory exists) and `git worktree
        remove --force` REFUSES it ("is not a .git file"). It is the dangerous
        one: it makes an unrelated repository a registered worktree of this
        checkout, and if that repository gitignores private/ -- as many do --
        every check below then passes, asked of the WRONG REPOSITORY. That is
        the 2026-08-13 incident with the one detail that made a human notice
        (`?? private/` in the receiving repo's `git status`) removed.
      * directory deleted and replaced by a plain directory: still listed until
        somebody prunes, so it too can be matched here.
      * directory gone: still listed until pruned, but _resolve_register()
        drops it because it resolves to nothing, so it never reaches this
        function. The branch below exists for the window between that
        resolution and this call, and it says PRUNABLE rather than hijacked --
        the two have different remedies and must not be reported as each other.

    Asked of the MATCHED ENTRY only, not of every entry in the register. It is
    cheaper (one `git rev-parse`, not one per registered worktree), and it is
    more precise: a hijacked entry somewhere else in the register is not a fact
    about the destination in hand, and refusing this destination for it would be
    a refusal the caller cannot act on -- which is how guards get switched off.

    The comparison is the one this module already makes; what is new is asking
    the DIRECTORY rather than the register. `self_git` is this checkout's common
    dir, resolved from the copy of this module that is running.
    """
    now, why = _locate_common_git_dir(wt_real, env)
    if now == self_git:
        return
    if not os.path.isdir(wt_real):
        raise DestinationRefused(
            "no_such_destination",
            "this checkout's register names this worktree, but its directory is "
            "GONE. The entry is stale and PRUNABLE -- 'git worktree prune' "
            "removes it. Nothing was written", wt_real)
    if now is None:
        raise DestinationRefused(
            "not_a_worktree",
            "this checkout's register names this worktree, but the directory "
            "there is in no git repository NOW: the worktree was deleted "
            "without 'git worktree remove' and a plain directory was put back "
            "at the same path. STALE ENTRY, not a missing one -- 'git worktree "
            "prune' removes it, because the .git it recorded is gone"
            + _git_said(why), wt_real)
    raise DestinationRefused(
        "different_repository",
        f"this checkout's register names this worktree, but the directory there "
        f"belongs to {now} NOW, not to {self_git}: the worktree was deleted "
        f"without 'git worktree remove' and an UNRELATED repository was created "
        f"at the same path. Not a missing directory and not a prunable entry -- "
        f"'git worktree prune' keeps it (the directory exists) and 'git worktree "
        f"remove --force' refuses it ('is not a .git file'). Remove or move that "
        f"directory and then run 'git worktree prune'", wt_real)


def _git_said(stderr):
    """git's own words about a probe it refused to answer, ready to append to a
    refusal -- or nothing, when it said nothing.

    One helper for all three sites, so a fatal cannot go on being reported as
    "no repository here" at whichever exit a later edit forgets. Truncated
    because a refusal is read by a person: the first line of a git fatal carries
    the cause, and for dubious ownership the remedy comes with it.
    """
    said = " ".join((stderr or "").split())
    return f" -- git said: {said[:300]}" if said else ""


def _diagnose_outside(path, self_git, env):
    """Why `path` is in no registered worktree -- the shell's four distinct
    refusals, kept distinct because their remedies differ."""
    near = _nearest_existing(path)
    if near is None or _physical(near) is None:
        return "no_such_destination", "no existing directory on this path"
    dst_git, why = _locate_common_git_dir(_physical(near), env)
    if dst_git is None:
        return "not_a_worktree", f"{near} is not inside a git repository" + _git_said(why)
    if dst_git != self_git:
        return ("different_repository",
                f"git common dir {dst_git}, expected {self_git}")
    r = _git(["rev-parse", "--show-toplevel"], _physical(near), env)
    if r.returncode != 0 or not r.stdout.strip() or _physical(r.stdout.strip()) is None:
        return ("no_worktree_of_its_own",
                "a bare repository or a git internal directory")
    return ("not_registered",
            "reports this checkout's common dir but appears in no entry of "
            "'git worktree list' for it -- a .git gitfile can claim membership "
            "a copied or restored directory never had")


def _self_git_or_refuse(lit, env):
    """This checkout's common dir, or the self_unlocatable refusal -- carrying
    what git said when it would not answer.

    A separate function because the refusal has to quote git and the ordinary
    lookup does not: "this file is not in a working tree" is the right sentence
    for a module outside a checkout and the wrong one for a checkout git will
    not read (dubious ownership, an unreadable config), and those two arrive
    here identically as None.
    """
    self_git, why = _locate_common_git_dir(str(ROOT), env)
    if not self_git:
        raise DestinationRefused(
            "self_unlocatable",
            f"{__file__} does not resolve into a git working tree, so which "
            "checkout's worktrees are eligible cannot be established"
            + _git_said(why), lit)
    return self_git


KINDS = ("root", "tree", "dir", "file")


def check_destination(path, *, kind):
    """Raise DestinationRefused unless private-derived files may be written to
    `path`. Returns a Destination on success. Writes nothing, creates nothing.

    `path` need not exist -- writers create their own directories -- but every
    component of it that DOES exist is checked, and the containing worktree must
    exist and be registered.

    `kind` IS REQUIRED, and it has no default on purpose. It says what the
    caller will do with the path, and every check that depends on what is
    already there hangs off it:

      "root"  stage a whole archive INTO this registered worktree root. It must
              BE a root, and the ignore question is not asked of it (no checkout
              ignores its own root); the caller then passes the paths it will
              really write to check_write_set(). stage-private-data.sh's
              question, and the one the agreement table asks both
              implementations.
      "tree"  a directory a RECURSIVE copy will descend. Everything "dir"
              checks, plus a scan of what is already inside it for the three
              ways an existing entry redirects a write.
      "dir"   a directory the caller creates and then writes named leaves into,
              checking each leaf itself (what check_write_set does). It does NOT
              look inside: if the caller will copy a tree in, it must say
              "tree".
      "file"  a regular file the caller creates or overwrites.

    A kind was chosen over an optional `check_leaf=` flag because a flag that
    defaults to the weaker check is a hole a caller falls into silently -- which
    is exactly how the FIFO and hard-link cases reached this API unchecked while
    check_write_set() refused them. An unknown kind is a ValueError, not a
    refusal: a caller that cannot name what it is writing has a bug in itself,
    and must not be able to spell that bug as "accepted".

    NOTHING ON THIS SIGNATURE WEAKENS A CHECK. `path` and `kind` DESCRIBE the
    destination and what the caller will write there; neither can turn a check
    off, and neither has a default that is weaker than the other value. The
    three parameters that DO weaken a check -- require_ignored=, worktrees= and
    env= -- are parameters of the private _check_destination() below and NOT of
    this signature, so a caller outside this module gets

        TypeError: check_destination() got an unexpected keyword argument 'env'

    rather than a weaker check. Each is legitimate for exactly one internal
    caller and each is documented there with the acceptance it can manufacture.
    test_private_egress.PUBLIC_PARAMS holds every public parameter as an
    ALLOWLIST with what it DESCRIBES written beside it: a fourth weakener cannot
    reach a public door without being added there, and adding it means writing a
    description of it that is false, in review, next to entries that are true.
    No test can decide that question; the table puts it where a human does.

    kind="root" skips the ignore question, the leaf check and the component
    walk, and it is still not a waiver: it pays for them by REQUIRING the path
    to BE a registered worktree root, and the caller then declares the paths it
    will really write to check_write_set(). See the kind list above and
    test_private_egress.API_REACH.
    """
    return _check_destination(path, kind=kind, require_ignored=True,
                              worktrees=None, env=None)


def _check_destination(path, *, kind, require_ignored, worktrees, env):
    """check_destination()'s body, carrying the three parameters that are
    deliberately absent from its signature.

    Each is a real internal need, each is handed its STRONG value as a literal
    by check_destination(), and each -- measured on this checkout, not asserted
    -- can manufacture an acceptance that the module exists to refuse:

      require_ignored=False   skips _require_uncommittable entirely, so
                              data/leak.json and even the TRACKED index.html are
                              accepted. Legitimate in exactly one place:
                              _check_write_set(), which asks that question
                              itself -- once per declared path, and for a leaf
                              through a declared path that covers it -- before
                              handing the paths below the root here.
      worktrees=[...]         replaces the register. self_common_git_dir() is
                              never probed, so nothing checks that the answer
                              came from THIS checkout: with a list naming an
                              unrelated repository, a gitignored path inside
                              THAT repository is accepted -- the 2026-08-13
                              shape, through a keyword. It exists so
                              _check_write_set() can ask about a dozen paths in
                              one ALREADY-VERIFIED worktree without a git
                              subprocess each, and it passes exactly the root
                              this module just accepted. Entries are normalized
                              by _resolve_register() before use, the same way
                              the git-read register is, so membership does not
                              depend on whether the caller spelled the path as a
                              realpath.
      env={...}               is sanitized of the git IDENTITY variables, the
                              pathspec variables and the whole GIT_CONFIG*
                              family, and has the ambient configuration switched
                              off (see sanitized_env), which is what stops a
                              caller supplying the answer to "which repository
                              is this" or "what does this path mean". It used to
                              be weaker still: HOME survives sanitizing, so an
                              env whose HOME held a .gitconfig with
                              core.excludesFile manufactured the "ignored"
                              verdict and data/leak.json was accepted. That is
                              closed -- not by clearing HOME, which supplies a
                              legitimate input to the real repository's answer,
                              but by GIT_CONFIG_ISOLATION, which switches the
                              global, XDG and system configuration (and the
                              default global ignore file) off for the probes
                              while leaving the destination's own .gitignore and
                              info/exclude in force.
                              WHAT STILL MAKES IT A WEAKENER: `env` replaces the
                              WHOLE environment the probes run in, PATH
                              included, and PATH decides WHICH `git` answers.
                              Measured, not asserted: with a shim named `git`
                              ahead of the real one on PATH, exiting 0 for
                              check-ignore and passing everything else through,
                              check_destination(ROOT/"data/leak.json",
                              kind="file", env=...) returns ACCEPTED where the
                              same call without it is REFUSED [not_ignored]. No
                              sanitizer closes that -- an env with no usable PATH
                              cannot run git at all -- so the lever taken is the
                              REACH of the parameter: it is not on a public door.
                              See test_private_egress.case_no_public_entry_
                              point_can_weaken_a_check, which records the
                              decision and reproduces the shim.

    Why signatures and not a convention: this module's argument is that a check
    which CAN be waived will be waived. It exists because stage-private-data.sh
    printed a success message while writing an archive into a repository this
    checkout does not own; `kind` was made required rather than defaulted for
    the same reason; and the `recursive` hole in check_write_set() was
    require_ignored being passed without the phase that was supposed to justify
    it. Python enforces the function name and nothing else -- reaching for a
    leading underscore is still possible, exactly as it is for _check_leaf --
    but none of the three can any more be reached by a caller who never left the
    documented API.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    given = pathlib.PurePath(str(path))
    if ".." in given.parts:
        raise DestinationRefused(
            "unnormalized_path",
            "'..' cannot be normalized without following symlinks, so this "
            "module refuses it rather than guess which directory it names",
            path)
    lit = os.path.abspath(str(path))

    # Probed lazily: with `worktrees` supplied (check_write_set asks about a
    # dozen paths in one worktree) the identity question is already settled, and
    # a git subprocess per path buys nothing. It is still asked before any
    # refusal that needs it -- an unlocatable self must never read as "accepted".
    self_git = None
    if worktrees is None:
        self_git = _self_git_or_refuse(lit, env)

    if kind == "root" and not os.path.isdir(lit):
        # Refused, never created: a directory that does not exist cannot be a
        # working tree of anything, and "mkdir -p whatever I was handed" is
        # precisely what turned a failed `git worktree add` into a copy of the
        # archive.
        raise DestinationRefused(
            "no_such_destination",
            "not an existing directory" if not os.path.lexists(lit)
            else "exists but is not a directory", lit)

    # Normalized either way. registered_worktrees() resolves what it reads from
    # git; a supplied register goes through the SAME normalizer rather than
    # being trusted as written, because _locate_worktree compares it against
    # resolved ancestors and a half-resolved comparison answers "not a member"
    # for a directory that is one.
    supplied = worktrees is not None
    worktrees = (_resolve_register(worktrees) if supplied
                 else registered_worktrees(self_git, env))
    if not worktrees:
        raise DestinationRefused(
            "register_unavailable",
            ("the register handed to this call named no existing directory"
             if supplied else
             f"'git worktree list' reported nothing for "
             f"{self_git or 'this checkout'}, with and without -z")
            + "; membership is unproven, and unproven is refused", lit)

    wt_lit, wt_real = _locate_worktree(lit, worktrees)
    if wt_real is None:
        if self_git is None:
            self_git = self_common_git_dir(env)
        reason, detail = _diagnose_outside(lit, self_git, env)
        raise DestinationRefused(reason, detail, lit)

    # ... and the entry that matched is asked whether it is still one of ours,
    # BEFORE any question is asked of the destination -- because every question
    # below is asked of THAT WORKTREE'S OWN GIT, and a hijacked entry answers
    # them all about a repository this checkout does not own.
    #
    # Only when the register was read from git. A SUPPLIED register replaces the
    # register outright (see this function's docstring: nothing checks that it
    # came from this checkout, which is why it is not on a public signature), so
    # there is no self_git to compare against -- it is not probed on that path.
    # The one internal caller entitled to it, _check_write_set(), passes exactly
    # the root that the kind="root" call in the same invocation just put through
    # the confirmation below, so re-confirming it once per declared path would
    # buy a git subprocess per path and no fact.
    if not supplied:
        _confirm_register_entry(wt_real, self_git, env)

    if kind == "root" and _physical(lit) != wt_real:
        raise DestinationRefused(
            "not_worktree_root", f"the worktree root here is {wt_real}", lit)

    # Component walk, from the worktree root DOWN. `wt_lit` is the literal prefix
    # whose resolution is the root, so everything after it is checked as written
    # rather than as resolved: this is what sees a link planted at
    # <worktree>/private, which realpath would silently follow.
    #
    # An INTERMEDIATE component is a directory slot whatever `kind` says -- the
    # caller's own mkdir -p walks through it -- so an existing one that is not a
    # directory is refused here, with the same code _check_leaf() gives for the
    # same fact in a directory slot. It used to be walked past: a regular file
    # (or a FIFO, or a device node) at <worktree>/private made
    # check_destination(<worktree>/private/blocker/leaf.json, kind="file")
    # ACCEPTED, and the caller that believed it then could not create the path
    # at all (FileExistsError/ENOTDIR). No data escapes through that shape, but
    # the answer to the only question this module is asked -- may private-derived
    # files be written HERE -- was wrong, and a guard that is wrong where nothing
    # breaks is a guard nobody checks where something does.
    #
    # The FINAL component is exempt here and belongs to _check_leaf(), which
    # knows from `kind` whether a directory or a regular file is what the caller
    # will write. Requiring a directory of it here would refuse every file
    # destination in the module.
    walk = lit[len(wt_lit):].strip(os.sep)
    parts = [] if not walk else walk.split(os.sep)
    cur = wt_lit
    for i, part in enumerate(parts):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise DestinationRefused(
                "symlink_component",
                f"{cur} -> {os.readlink(cur)}; a copy follows it, so the data "
                "would land outside the working tree that just passed every "
                "check", lit)
        if not os.path.lexists(cur):
            break                       # the rest is created by the caller
        if i < len(parts) - 1 and not os.path.isdir(cur):
            raise DestinationRefused(
                "not_a_directory",
                f"{cur} exists and is not a directory, so the path below it "
                "cannot be created at all -- the caller's own mkdir gets "
                "ENOTDIR or FileExistsError. Every component above the last is "
                "a directory slot, whatever kind the leaf is", lit)

    relpath = os.path.relpath(lit, wt_lit).replace(os.sep, "/")
    if relpath == ".":
        relpath = ""
    # The worktree ROOT itself is never asked the ignore question: no checkout
    # ignores its own root, so demanding it would refuse every destination.
    # kind="root" callers are asking a different question anyway, and pass the
    # paths they intend to write to check_write_set().
    #
    # Which is why an ORDINARY call naming the root is REFUSED here rather than
    # allowed to skip the same check. An empty relpath used to fall through the
    # `and relpath` guard below and return accepted, so
    # check_destination(<checkout>) -- an argument-derived cache or manifest
    # directory pointed at the checkout root -- said yes to writing
    # private-derived files at a path every one of those files is committable
    # from. It has its own reason because its remedy is its own: name a path
    # INSIDE the tree that the tree ignores.
    if kind != "root" and not relpath:
        raise DestinationRefused(
            "worktree_root_itself",
            "a worktree root is not a place to write private-derived files: its "
            "own git ignores nothing about it, so anything written here is one "
            "'git add -A' from a commit. Name a path inside it that it ignores "
            "-- or, to stage a whole archive into it, ask with kind='root' and "
            "declare the paths to check_write_set()", lit)
    if require_ignored and relpath:
        _require_uncommittable(wt_lit, relpath, env)

    # The leaf, last, and part of the PUBLIC contract rather than of
    # check_write_set() alone. Skipped only for kind="root": the root is
    # resolved before the register is consulted (exactly as the shell resolves
    # DST to DST_REAL), so a symbolic link that NAMES a registered worktree is a
    # legitimate way to say where it is -- links BELOW the root are what the
    # walk above refuses -- and the isdir check above has already settled that
    # it is a directory.
    if kind != "root":
        _check_leaf(lit, "dir" if kind in ("tree", "dir") else "file")
        if kind == "tree":
            _scan_tree(lit)
    return Destination(lit, wt_real, relpath)


def _pathspec(relpath):
    """A destination-relative path -> the PATHSPEC that asks git about THAT path.

    git reads a leading ':' as pathspec MAGIC, so a destination spelled
    ':(top)private/foo', ':/private/foo', '::private/foo', ':!data/x' or
    ':(exclude)data/x' makes git answer about a DIFFERENT path than the one on
    disk. Each of those is a real, creatable name inside the tree -- the ignore
    rule is 'private/', not a directory literally called ':(top)private' -- so
    'ignored' was being reported for a path that is one 'git add' from a commit.
    ':(exclude)' is the worse shape: it turns 'git ls-files' into a filter that
    evaluates the tracked question against everything EXCEPT the declared path.
    These arrive through --cache-dir / dest_dir= arguments, where a leading colon
    is a typo or a hostile argument and never a real destination.

    The transform is './' + relpath, and it is the one form that works for BOTH
    commands. Measured on this checkout (git 2.50.1), not assumed:

      * 'git check-ignore' rejects pathspec magic OUTRIGHT -- ':(literal)x' exits
        128, "pathspec magic not supported by this command". The global escapes
        do not help: '--literal-pathspecs' and GIT_LITERAL_PATHSPECS=1 apply the
        'literal' magic to EVERY pathspec, so under them even a plain
        'private/foo' exits 128. '--stdin' does not help either -- it parses
        magic exactly the same way. So there is no way to ask check-ignore for a
        literal interpretation, and a refusal would have been the only other
        answer.
      * 'git ls-files' does accept ':(literal)', but forcing it would silently
        weaken the zero-match pattern check _expand() relies on: it lists every
        tracked file a pattern matches when the pattern is read as a glob and
        none of them when it is read as a literal, so a tracked file matching a
        declared pattern would stop being found.

    './x' starts with '.', so no magic is parsed at all, while both commands
    still resolve it as the path x relative to the cwd -- literal names stay
    literal and glob patterns keep globbing.

    Which is why the four GIT_*_PATHSPECS are in PATHSPEC_MEANING_VARS (issue
    #194): they apply the magic this prefix exists to avoid, from the
    environment, to every pathspec at once. An inherited GIT_NOGLOB_PATHSPECS
    would leave './d/*.csv' matching nothing while the check still returned
    success, and any of the four would make check-ignore exit 128 for every
    path. Cleared in sanitized_env(), so the sentence above stays true whatever
    the caller's shell holds.

    Asserted rather than described, in
    test_private_egress.case_a_destination_that_looks_like_pathspec_magic_is_
    answered_about_itself: verdict-identical to the bare spelling for every path
    in its PATHSPEC_EQUIVALENT list (tracked files, tracked directories, ignored
    paths, absent paths, '*' and '[' patterns) across both commands, still
    matching tracked files through a glob, and neutralizing every magic spelling
    above.
    """
    return "./" + relpath


def _alias_pathspec(relpath, fold_case):
    """The same path, as the pathspec that asks git about the index entries
    differing from it in ASCII CASE, or in unicode COMPOSITION (issue #204).

    That is the coverage, stated as narrowly as it really is, and it is NOT "every
    index entry the filesystem would treat as this one": ':(icase)' folds ASCII
    case pairs and no others, and core.precomposeUnicode -- forced alongside it in
    _alias_overrides() -- folds composition. A non-ASCII case alias is reached by
    the OTHER probe, the on-disk walk, and only where that walk has something on
    disk to resolve. The last paragraph names the one combination neither reaches.

    `fold_case` comes from _fs_folds_case(), which measures the worktree root AND
    every existing directory the path descends into, so the ':(icase)' magic
    follows the path onto a case-insensitive volume mounted below a case-sensitive
    root rather than being decided at the root alone. It is an OR ALONG THE PATH,
    and ':(icase)' then applies path-wide, so this pathspec deliberately asks for
    MORE index entries than the filesystem would really collide. That is what a
    pathspec can express, and no more: _require_uncommittable() filters what comes
    back through _classify_alias(), which uses the per-component fold vector, so a
    folding ANCESTOR no longer makes a case-sensitive descendant fold (issue #231).
    Adding the magic unconditionally would be a different matter and is still
    wrong: on a case-sensitive filesystem `Private/x` and `private/x` are two
    files, and asking git for entries no measurement supports would put the
    filter's own answer beyond what it can justify.

    Off that path this is _pathspec() exactly, so the alias probe still asks
    about the same path with the same magic neutralized -- the unicode half of
    the fold rides on core.precomposeUnicode, not on the spelling, and applies
    either way.

    ':(icase)' FOLDS ASCII CASE, and only that (issue #224). It is a byte
    comparison: `:(icase)./private/househöld.yaml` does not match a tracked
    `private/HOUSEHÖLD.yaml`. This is not the whole of the tracked question for
    that reason -- _require_uncommittable() hands the same call a second
    pathspec, the one _ondisk_relpath() resolved, which folds whatever the
    filesystem folds FOR A PATH THAT EXISTS ON DISK. Where the path does not exist
    there is nothing to resolve, and the two blind spots used to OVERLAP in exactly
    one combination: an index entry that is tracked, has no working-tree file, and
    differs from the path only in NON-ASCII case. That one is answered by
    _index_case_aliases(), which enumerates the index instead of handing git a
    pathspec (issue #230).
    """
    return (":(icase)" if fold_case else "") + _pathspec(relpath)


def _isolation_remedy(key):
    """What to actually do about a key that did not read back, which is not the
    same sentence for every key that can reach this refusal.

    The message here used to say "upgrade git" whatever had happened. That is
    right for the case it was written for -- the -c option ignored, which only a
    git predating 1.7.2 (2010) does -- and wrong for the others that reach it:
    a `config --get` that failed for some other reason, a destination whose
    configuration changed between the scope read and the readback, and, for the
    two keys taken FROM the destination, a mechanism that is not on trial at
    all. An operator sent to upgrade git for a stale config file fixes nothing
    and learns to distrust the refusal.
    """
    if key in dict(DESTINATION_CONFIG_KEYS):
        return (f"{key} is not switched off, it is taken FROM this destination: "
                "the value above was read from its own --worktree/--local config "
                "moments ago and forced with 'git -c'. A readback that disagrees "
                "means that file changed underneath the run, or git could not read "
                "it -- see what git said above, re-run, and if it persists inspect "
                f"the destination's own .git/config. Upgrading git is NOT the "
                "remedy here.")
    return ("If git said nothing above, the -c option on the command line did not "
            "take effect: 'git -c' has existed since git 1.7.2 (2010), so a git "
            "that ignores it is older than anything this repository is developed "
            "on -- upgrade git. If git printed an error, that error is the thing "
            "to fix first: the isolation is unproven because the question could "
            "not be asked, not because the answer was wrong.")


def _alias_isolation_remedy(key):
    """The same sentence for the ALIAS override, which reaches the readback by a
    different route and has a different remedy.

    _isolation_remedy() would answer for core.precomposeUnicode with the
    destination-derived text -- "it is taken FROM this destination" -- and that
    is exactly wrong here: this is the one value this module sets AGAINST what
    the destination says, so an operator sent to inspect the destination's
    .git/config would find the value it states and no fault in it.
    """
    return (f"{key} is the one key this guard forces against the destination's "
            "own statement of it, for the ALIAS question alone (issue #204): the "
            "literal tracked question runs with the value the destination states, "
            "and this second one asks whether an index entry that differs from "
            "the path only in unicode composition would be written over. A "
            "readback that disagrees means the -c did not reach this git, so the "
            "alias question was answered with the wrong matching -- upgrade git "
            "if it said nothing, and fix what it printed if it did.")


def _require_isolation_proven(worktree, env=None):
    """The running git must really be answering from repository-local
    configuration -- refuses with 'isolation_unproven' when it is not.

    The block on GIT_CONFIG_OVERRIDES above can cite the git version each
    mechanism arrived in. It cannot know which git is installed here, and that
    gap is the defect this check closes: a git that has never heard of
    GIT_CONFIG_GLOBAL does not complain about it, so an isolation that is
    entirely inert reads exactly like one that works. The question is put to the
    git that is going to answer, in the destination, where every configuration
    file that could reach the ignore verdict is already in the stack:

        git config --get <key>   must read back as ours, for EVERY key forced

    EVERY key, and that word is the finding this round: the proof read back
    core.excludesFile alone, so the isolation could have stopped applying
    anywhere else and the proof would still have passed. It now walks the same
    list the probes are given, which is why it RETURNS that list -- the caller
    runs its probes with the overrides this function has just proved, and there
    is no second derivation for the two to disagree about.

    EVERY key THIS MODULE CHOOSES, which is what leaves AMBIENT_PROTECTED_KEYS
    out of the loop: _git() hands git the operator's own safe.directory entries
    back when git has refused to answer at all, and their value is the
    operator's rather than ours. There is nothing to prove about a value we did
    not choose, and nothing to compare it against -- it is multi-valued, and a
    `config --get` of it prints whichever entry came first.

    It is the RESULT that is checked, not the mechanism, so this keeps holding if
    the mechanisms change and it fails closed if some later git reorders
    precedence underneath them. `git config --get` is as old as git, which is
    what makes the proof itself version-independent.

    Called from _require_uncommittable() for EVERY path rather than once and
    remembered: a configuration file is an ordinary file that can be rewritten
    between the first probe and the last, and a fact obtained once and trusted
    for the rest of the call is the shape of most defects review has found here.
    """
    overrides = _destination_overrides(worktree, env)
    # TWO LISTS, because two probes run with two lists (issue #204). The second
    # is the first with ALIAS_CONFIG_OVERRIDE appended, and it is read back under
    # ITSELF -- reading core.precomposeUnicode back under the adopted list would
    # confirm the destination's value and prove nothing about the one the alias
    # probe actually runs with. Only the keys that DIFFER are re-asked: the rest
    # are the same -c options in the same order, already proved above, and asking
    # again would double every readback for every path.
    for pairs, probe, remedy in ((overrides, overrides, _isolation_remedy),
                                 (ALIAS_CONFIG_OVERRIDE, _alias_overrides(overrides),
                                  _alias_isolation_remedy)):
        for key, value in pairs:
            r = _git(["config", "--get", key], worktree, env, probe)
            effective = r.stdout.strip()
            if effective == value:
                continue
            version = _git(["--version"], worktree, env).stdout.strip() or "unknown"
            raise DestinationRefused(
                "isolation_unproven",
                f"{key} reads back as "
                f"{effective or '<unset>'!r} in {worktree}, not "
                f"{value!r} ('git config --get {key}' exited {r.returncode}"
                + (_git_said(r.stderr) or " -- nothing on stderr")
                + f"; {version}). The ignore question below would then be answered by the "
                "operator's global, XDG or system configuration rather than by the "
                "repository, and a destination that does NOT ignore private data could "
                "answer that it does. " + remedy(key), worktree)
    return overrides


def _require_uncommittable(worktree, relpath, env=None):
    """`relpath` must be untracked AND ignored in `worktree`.

    TRACKED first, and the order is the whole point: check-ignore consults the
    index, so a tracked-though-ignored path reports "not ignored" -- answering
    "your .gitignore does not cover this" for a file whose .gitignore covers it
    perfectly well and which is committed anyway, sending the operator to edit
    the wrong file.

    Asked OF THE DESTINATION, because the answer comes from its own .gitignore,
    its info/exclude and its index -- and under the isolation, because an
    inherited GIT_CONFIG could otherwise supply a core.excludesFile that
    manufactures the "ignored" answer, an AMBIENT one (the operator's global,
    XDG or system config, or the default global ignore file) could manufacture
    it with nothing forged at all, an ambient core.ignoreCase or
    core.precomposeUnicode could widen the destination's OWN rules until they
    cover a path the destination never named, and an inherited GIT_*_PATHSPECS
    could make both questions below answer about a different path or refuse
    every path outright. The first and last are settled in sanitized_env(), the
    middle two on the command line in _git() -- switched off, and taken from the
    destination's own configuration, respectively -- and
    _require_isolation_proven() above checks that the git in front of us really
    did apply every one of them, handing back the list the probes then run
    with. What is left is the destination's own .gitignore, its info/exclude and
    its index.

    FOUR QUESTIONS, not two: the tracked one is asked three ways -- by the bytes,
    by the aliases git's own pathspec folds (issue #204), and by enumerating the
    index and comparing component by component (issue #230) -- because on a
    case-insensitive or normalizing filesystem a write to `private/household.yaml`
    lands on a tracked `Private/household.yaml` while the literal question reports
    it untracked. See ALIAS_CONFIG_OVERRIDE.

    AND EVERY ONE OF THEM IS ASKED ABOUT THE PATH ON DISK (issues #223, #224).
    `relpath` is what the caller typed; _ondisk_relpath() says what the
    filesystem spells it as, and those differ exactly when a write to the one
    lands on the other. Both spellings are put to git, because the index can hold
    both at once and each question refuses for its own reason:

      * TRACKED -- the on-disk spelling joins the alias pathspec. This is what
        catches the NON-ASCII fold that `:(icase)` cannot see (#224), FOR A PATH
        THAT EXISTS ON DISK: git's pathspec fold is ASCII-only and misses
        `HOUSEHÖLD.yaml` while the filesystem resolves it.
      * IGNORED -- asked of the on-disk spelling FIRST, because that is the path
        the bytes go to. `aliased_not_ignored` when only it is unignored (#223);
        plain `not_ignored` when the spellings are the same, or when the typed
        spelling is the unignored one, which is what that code has always meant.

    The `:(icase)` probe STAYS rather than being replaced by the walk, and the
    reason is measurable: an index entry whose working-tree file is ABSENT --
    tracked, deleted in the tree, not committed -- has no on-disk spelling to
    resolve, so only the pathspec fold finds it, while a file present under a
    non-ASCII cased name is invisible to the pathspec fold and only the walk
    finds it. Neither subsumes the other; asserted in
    test_private_egress.case_the_two_alias_probes_each_catch_what_the_other_misses.

    AND THEIR BLIND SPOTS OVERLAPPED, which "neither subsumes the other" does not
    say: an index entry that is BOTH absent from the working tree AND differs from
    the path only in NON-ASCII case was seen by neither. That combination is issue
    #230, and it is answered by the third question -- _index_case_aliases()
    enumerates the destination's index and compares each entry against the
    candidate component by component, because the side that holds something to
    compare against is the index rather than the filesystem when the name does not
    exist yet.

    AND EVERY ENTRY ANY OF THEM RETURNS IS JUDGED PER COMPONENT (issue #231).
    ':(icase)' is applied path-wide by git, so a folding ANCESTOR would otherwise
    make a case-SENSITIVE descendant fold as well and the guard would refuse a
    destination it should accept -- reproduced on three real nested mounts. The
    fold vector answers for each component's own parent directory, and
    _classify_alias() drops a match whose only difference sits in a directory that
    does not fold.

    This is the ONLY place in this module where a caller-supplied path becomes a
    git PATHSPEC (everything else hands git a -C directory), so it is the one
    place _pathspec() and _alias_pathspec() have to be applied -- see there for
    what a leading ':' otherwise does to the questions below.
    """
    overrides = _require_isolation_proven(worktree, env)
    ondisk = _ondisk_relpath(worktree, relpath)
    spec = _pathspec(relpath)
    r = _git(["ls-files", "--", spec], worktree, env, overrides)
    if r.returncode != 0:
        raise DestinationRefused(
            "tracked_unanswerable",
            f"'git ls-files' failed in {worktree}: {(r.stderr or '').strip()[:200]}",
            os.path.join(worktree, relpath))
    if r.stdout.strip():
        raise DestinationRefused(
            "tracked_path",
            "a tracked file stays in the index whatever .gitignore says, so "
            "writing private data over one puts it straight into the next "
            "commit's diff", os.path.join(worktree, relpath))
    # AND AGAIN UNDER THE FILESYSTEM'S OWN EQUIVALENCE (issue #204). The
    # question above is answered BY THE BYTES; this one is answered by which
    # spellings the filesystem holding the destination resolves to one file. See
    # ALIAS_CONFIG_OVERRIDE for the measurement and for what the plausible fix
    # (forcing core.ignoreCase) was measured to change, which is nothing.
    fold_evidence = []
    fold_case = _fs_folds_case(worktree, relpath, fold_evidence)
    folds = _fold_vector(worktree, relpath)
    spellings = [relpath] + ([ondisk] if ondisk != relpath else [])
    aliases = [_alias_pathspec(relpath, fold_case)]
    if ondisk != relpath:
        # The spelling the filesystem really holds, which the byte-oriented fold
        # above cannot reach for a non-ASCII cased name (issue #224). One call,
        # two pathspecs: `ls-files` unions them and prints whichever matched.
        aliases.append(_alias_pathspec(ondisk, fold_case))
    r = _git(["ls-files", "-z", "--"] + aliases, worktree, env,
             _alias_overrides(overrides))
    if r.returncode != 0:
        raise DestinationRefused(
            "tracked_unanswerable",
            f"'git ls-files' could not be asked about the paths {relpath!r} "
            f"aliases in {worktree}: {(r.stderr or '').strip()[:200]}",
            os.path.join(worktree, relpath))
    # COMPONENT-LOCAL, which the pathspec cannot be (issue #231). ':(icase)' is
    # applied path-wide, so a folding ancestor turns the fold on for every
    # component below it; each entry git returned is kept only if some spelling
    # it was asked about really does alias it AT EVERY COMPONENT, judged by that
    # component's own parent directory. -z because these names are split on '/'
    # here, and git quotes a non-ASCII path in its default output.
    matched = [e for e in r.stdout.split("\0") if e
               and any(_classify_alias(s, e, folds) != "spurious" for s in spellings)]
    # AND THE INDEX ITSELF, for the fold neither pathspec can express (issue
    # #230): a tracked entry with no working-tree file, differing from the path
    # only in NON-ASCII case, is invisible to ':(icase)' and has nothing on disk
    # for the walk to resolve.
    for spelling in spellings:
        for hit in _index_case_aliases(worktree, spelling, folds,
                                       _alias_overrides(overrides), env):
            if hit not in matched:
                matched.append(hit)
    if matched:
        raise DestinationRefused(
            "tracked_path",
            "nothing is tracked under this exact name, but the destination's "
            "filesystem resolves it to a path that is: "
            f"{' '.join(matched)[:200]}. Case folds: "
            + (fold_evidence[0] if fold_evidence else str(fold_case))
            + " (per component: "
            + ", ".join(f"{p}={'yes' if f else 'no'}"
                        for p, f in zip([p for p in relpath.split('/') if p], folds))
            + "), and unicode composition folds wherever git was built to "
            "precompose"
            + (f"; on disk this path is spelled {ondisk!r}"
               if ondisk != relpath else "")
            + ", so the write would land on a committed file under a "
            "spelling the guard was not asked about",
            os.path.join(worktree, relpath))
    # THE IGNORE QUESTION, asked of the path the bytes actually reach. When the
    # two spellings are the same -- every ordinary destination -- `ondisk_spec`
    # IS `spec`, so this is the one call it has always been and the second one
    # below is skipped.
    ondisk_spec = _pathspec(ondisk)
    r = _git(["check-ignore", "-q", "--", ondisk_spec], worktree, env, overrides)
    if r.returncode not in (0, 1):
        raise DestinationRefused(
            "ignore_unanswerable",
            f"'git check-ignore' exited {r.returncode} -- neither 'ignored' (0) nor "
            "'not ignored' (1). 'Probably ignored' is not a property to write a "
            "private archive on", os.path.join(worktree, relpath))
    if r.returncode == 1:
        if ondisk != relpath:
            raise DestinationRefused(
                "aliased_not_ignored",
                f"the destination ignores {relpath!r} as spelled, and the write "
                f"does not go there: its filesystem already holds this path as "
                f"{ondisk!r}, and that spelling is one 'git add -A' from a "
                "commit. The rule and the directory disagree about case or about "
                "unicode composition, and core.ignoreCase is what the DESTINATION "
                "states (git's default, false, when it states none), so the rule "
                "covers only the spelling it is written in. This is the "
                "2026-08-13 incident shape -- the whole archive in a tree that "
                "does not ignore it. Two remedies, both inside the destination: "
                f"give its .gitignore a rule for {ondisk!r}, or rename that "
                f"directory to {relpath!r} so the rule it already has covers it",
                os.path.join(worktree, relpath))
        raise DestinationRefused(
            "not_ignored",
            "that working tree's own git would offer this path to 'git add' -- "
            "the half of the 2026-08-13 incident a repository check cannot see. "
            "Asked with global, XDG and system configuration off, so a global "
            "excludes file does not count, and with core.ignoreCase and "
            "core.precomposeUnicode as the DESTINATION's own config states them "
            "(git's default, false, when it states neither), so a rule whose "
            "spelling differs from the path's only in case or in unicode "
            "composition does not count either: the remedy is inside the "
            "destination, in its .gitignore or its .git/info/exclude",
            os.path.join(worktree, relpath))
    if ondisk == relpath:
        return
    # The on-disk spelling is ignored. The TYPED one is asked as well, and only
    # here: it costs a second call exactly on the paths whose two spellings
    # differ, and it keeps this from being the one change that makes the guard
    # ACCEPT something it refused before. A destination whose rule covers the
    # on-disk name and not the typed one is still told so, under the code that
    # has always meant it.
    r = _git(["check-ignore", "-q", "--", spec], worktree, env, overrides)
    if r.returncode == 0:
        return
    if r.returncode == 1:
        raise DestinationRefused(
            "not_ignored",
            f"the destination ignores the on-disk spelling {ondisk!r} but not "
            f"{relpath!r} as it was asked about. Both are refused rather than "
            "the difference being resolved silently: the two spellings are one "
            "file here, and a rule that covers only one of them is a rule that "
            "stops covering this archive the moment the directory is recreated "
            "under the other. Add the missing spelling to that working tree's "
            ".gitignore or .git/info/exclude",
            os.path.join(worktree, relpath))
    raise DestinationRefused(
        "ignore_unanswerable",
        f"'git check-ignore' exited {r.returncode} -- neither 'ignored' (0) nor "
        "'not ignored' (1). 'Probably ignored' is not a property to write a "
        "private archive on", os.path.join(worktree, relpath))


# ---------------------------------------------------------------------------
# What is already AT the path
#
# The two below cover the three ways a destination path redirects a write
# without being outside the worktree: a symbolic link sends it elsewhere, a
# second name on the inode rewrites a file elsewhere in place, and a FIFO or
# device node hands the bytes to a process (and blocks the open until something
# reads, so the run hangs with no message). One surface, three checks; keep them
# in step.
#
# Both are reached from check_destination(), driven by its `kind`, so the
# single-path API and check_write_set() ask the same questions of the same path.
# They stay private because `kind` is the way to ask for them: a caller reaching
# past the public entry point gets the walk from the worktree root skipped,
# which is what sees a link planted ABOVE the leaf.
# ---------------------------------------------------------------------------
def _check_leaf(dest, kind):
    """`dest` is a path a caller will create or overwrite. `kind` here is the
    SLOT -- "dir" or "file" -- which check_destination() derives from its own
    four-valued public `kind` ("tree" wants a directory slot too, and then a
    scan). Absence is fine -- the caller creates it."""
    if os.path.islink(dest):
        raise DestinationRefused("symlink_component",
                                 f"{dest} -> {os.readlink(dest)}", dest)
    if not os.path.lexists(dest):
        return
    st = os.lstat(dest)
    if kind == "dir":
        if not os.path.isdir(dest):
            raise DestinationRefused("not_a_directory",
                                     "expected a directory, or a creatable name", dest)
        return
    if os.path.isdir(dest):
        raise DestinationRefused("special_file", "expected a regular file, found a directory", dest)
    if not os.path.isfile(dest):
        raise DestinationRefused(
            "special_file",
            "a FIFO, socket or device node: a copy opens whatever it is handed, "
            "so this run either blocks forever or hands the data to a process", dest)
    if st.st_nlink > 1:
        raise DestinationRefused(
            "hard_link",
            "more than one name for this inode -- a copy truncates and writes "
            "THROUGH it, so every other name, including one outside this "
            "working tree, becomes a copy of this household's private data", dest)


def _scan_tree(root):
    """Refuse a symlink, a special file or a hard-linked file anywhere beneath
    `root` -- the paths a recursive copy writes through. Never follows a link;
    the link itself is the refusal.

    EVERY filesystem probe is inside the OSError handler, not just the listing.
    os.scandir() only reads the directory; the per-entry questions below go back
    to the kernel, and each of them can fail for a reason that is a fact about
    the destination and not a bug here: e.stat() raises PermissionError in a
    directory that is readable but not searchable (mode r-- on a parent), and
    FileNotFoundError for an entry that is unlinked between the listing and the
    stat. Both used to escape this function raw, out through both public APIs --
    a caller holding the documented `except DestinationRefused` saw an unhandled
    PermissionError instead of a refusal it could act on. Reproduced before the
    fix: a subdirectory chmod'ed 0o400 with one file in it took
    check_destination(<tree>, kind="tree") and check_write_set(recursive=...)
    both out through a raw PermissionError.

    The probes are lifted out of the refusal branches so the handler covers the
    KERNEL CALLS ONLY. os.readlink() is in there too -- it is the one probe that
    used to sit inside a raise. A DestinationRefused raised below is not an
    OSError and so passes through untouched, and neither is a TypeError or an
    AttributeError: widening this to `except Exception` would let a bug in this
    module report itself as a fact about the destination, which is the mistake
    the scan_unreadable/symlink_under split exists to avoid in the first place.
    """
    if not os.path.isdir(root) or os.path.islink(root):
        return
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError as e:
            # Its own reason, not one of the three below: "I could not look" and
            # "I looked and found a link" are different facts, and a guard that
            # reports the second for the first sends the reader hunting for a
            # link that is not there.
            raise DestinationRefused(
                "scan_unreadable", f"could not scan {cur}: {e}", root) from None
        for e in entries:
            try:
                link = e.is_symlink()
                target = os.readlink(e.path) if link else None
                st = None if link else e.stat(follow_symlinks=False)
                isdir = not link and e.is_dir(follow_symlinks=False)
                isfile = not link and e.is_file(follow_symlinks=False)
            except OSError as err:
                raise DestinationRefused(
                    "scan_unreadable",
                    f"could not inspect {e.path} while scanning {cur} ({err}), so "
                    "whether it is a link, a special file or a second name for "
                    "some other inode is unknown", root) from None
            if link:
                raise DestinationRefused(
                    "symlink_under",
                    f"{e.path} -> {target}; a recursive copy writes "
                    "THROUGH it, and one pointing at the source archive makes the "
                    "run overwrite the originals", root)
            if isdir:
                stack.append(e.path)
                continue
            if not isfile:
                raise DestinationRefused(
                    "special_under",
                    f"{e.path} is neither a directory nor a regular file", root)
            if st.st_nlink > 1:
                raise DestinationRefused(
                    "hardlink_under",
                    f"{e.path} has more than one name for its inode", root)


def check_write_set(root, *, dirs=(), leaves=(), recursive=(), glob_source=None):
    """The whole question for a caller that stages a set of paths into `root`.

    `root` must be a registered worktree root (stage-private-data.sh's
    question). `dirs` and `leaves` are worktree-relative paths that must be
    ignored-and-untracked there and must not be a link, a special file or a
    hard link; `recursive` names directories a recursive copy descends, whose
    existing contents are scanned for all three. A `leaves` entry may be a glob
    pattern, expanded against `glob_source` (the SOURCE tree, whose basenames
    are what a copy will actually write) when given, else against `root`. Only a
    LEAF may be a pattern: a pattern in `dirs` or `recursive` is refused, because
    a directory's check is a scan of what is inside it and no literal stands in
    for that.

    Ordered checks first, writes never: this returns a Destination or raises.

    `dirs`, `leaves` and `recursive` are SEQUENCES of paths, and each is
    materialized exactly once, at entry, so a generator or any other one-shot
    iterable is checked in full rather than being consumed by the first phase and
    read as empty by the rest. A pathlib.Path entry is accepted (os.fspath); a
    bare string or Path passed where the sequence belongs is a TypeError, because
    iterating it would check its characters instead of the path it names. See
    _paths().

    Every parameter here DESCRIBES what will be written and where: four name
    paths, and `glob_source` says which tree a leaf pattern is expanded against.
    A WRONG `glob_source` names fewer leaves than the copy will really write, and
    that is the one thing it can do -- it can no longer take a declared pattern
    out of the check entirely, which is what an empty expansion used to do: a
    source that cannot be listed is refused (glob_source_unlistable), and a
    pattern that matches nothing is checked as the literal it is, so every
    declared pattern is evaluated as SOMETHING. See _expand() for what the
    literal does and does not settle. `env=` is not here, for the reason given at
    _check_destination(): it can manufacture the "ignored" verdict through HOME,
    so it is a parameter of the private _check_write_set() below and an ordinary
    caller gets a TypeError.
    """
    return _check_write_set(root, dirs=dirs, leaves=leaves, recursive=recursive,
                            glob_source=glob_source, env=None)


def _check_write_set(root, *, dirs, leaves, recursive, glob_source, env):
    """check_write_set()'s body, carrying the `env` its signature omits.

    Every path it asks about goes through the same checker the single-path API
    is a one-line delegation to -- the root with nothing turned off, the paths
    below it with the committability exemption this function has earned by
    asking that question itself. Same body either way, so a destination cannot
    pass for one API and fail for the other. That was not true before: the leaf
    checks lived here, and the single-path API accepted a FIFO and a hard link.

    EVERY declared path is asked the committability question, `recursive` and
    `leaves` no less than `dirs`. The one exemption is a path that an
    ALREADY-ANSWERED path contains, because git answers both halves for a whole
    subtree: an
    excluded directory cannot have a child re-included, and `git ls-files -- d`
    lists everything tracked beneath d. Nothing else is exempt. That is written
    as one loop over the union rather than as one loop per argument, because the
    per-argument version is what left `recursive` unchecked: its entries went
    straight to the checker with require_ignored=False -- the phase
    below hands every path that flag on the strength of THIS phase having asked
    -- so check_write_set(root, recursive=("data",)) accepted a tracked,
    committable destination that check_write_set(root, dirs=("data",)) refused.
    A caller using `recursive` without redundantly naming its parent in `dirs`
    could copy the archive into a path one `git add` from a commit, through this
    module's own API.

    THE SEQUENCES ARE MATERIALIZED ONCE, AT ENTRY, and that line is load-bearing
    rather than tidy. `dirs` and `recursive` used to be tuple()-ed twice -- once
    for the pattern check, once for the committability loop -- and a ONE-SHOT
    iterable is empty by the second conversion, so

        check_write_set(ROOT, dirs=(x for x in ["data"]))   -> ACCEPTED
        check_write_set(ROOT, dirs=("data",))               -> REFUSED [tracked_path]

    on the same tracked, committable directory: the later phases iterated an
    empty sequence and the call returned a Destination having scanned nothing.
    Materializing once makes that shape structurally impossible rather than
    fixed at one site, which matters because it is the THIRD appearance of one
    defect -- a public entry point returning success having evaluated nothing,
    because an input was empty, unexpanded or consumed rather than absent. See
    _require_every_declaration_planned() for the other half: the accounting that
    catches the shapes materializing cannot.
    """
    dirs = _paths("dirs", dirs)
    recursive = _paths("recursive", recursive)
    leaves = _paths("leaves", leaves)

    # Nothing turned off: require_ignored is the literal True the public door
    # passes, and the register is the real one. kind="root" never reaches the
    # ignore question anyway (a root's relpath is empty), so the root call needs
    # no exemption and does not take one.
    dest = _check_destination(root, kind="root", require_ignored=True,
                              worktrees=None, env=env)
    wts = [dest.worktree]

    def one(rel, kind):
        # The checker re-walks every component from the worktree root
        # down, which is what sees a link planted ABOVE the leaf: a link at
        # <root>/private is invisible to an lstat of <root>/private/1-raw-data.
        # require_ignored=False because the ignore question is asked once per
        # DECLARED path in the phase above -- which is why that phase must cover
        # every argument that reaches here, and why the loop below is over their
        # union rather than over `dirs` alone. It goes through the PRIVATE
        # _check_destination() because that exemption is not on the public
        # signature: this module is the only caller entitled to it, and the
        # single-path API a writer calls must not be able to spell it. Same
        # function body either way -- check_destination() is a one-line
        # delegation to it, so the two cannot drift. `wts` is the root this call
        # just accepted, already physically resolved.
        p = os.path.join(dest.path, rel)
        _check_destination(p, kind=kind, require_ignored=False, worktrees=wts, env=env)
        return p

    # In PHASES, not path by path, because the phases answer different questions
    # and a caller reading a refusal wants the first REASON, not the first path:
    # "this tree could commit what you are about to write" is settled for the
    # whole set before any of it is inspected for links. It is also the order
    # stage-private-data.sh checks in, which is what lets the two be compared.
    #
    # The ignore/tracked question is asked of EVERY declared path -- every
    # directory, every recursively-copied subtree, every leaf -- and skipped
    # only where a path already asked covers it: check-ignore and ls-files both
    # answer for a whole subtree, so asking again underneath would add a second
    # verdict on the same fact and a second way to drift.
    #
    # Covered-not-asked is also what keeps the two implementations comparable.
    # stage-private-data.sh asks about three managed paths and copies its
    # subtrees beneath one of them; asking again for private/1-raw-data/
    # electric-bills would make the python side refuse ignore_unanswerable
    # (check-ignore exits 128 for a path beyond a symbolic link) on a fixture
    # where the shell reaches the symlink scan and says symlink_component.
    # Same rule, same answers -- but only because "covered" means covered by a
    # path that was really asked, never by a path that was merely declared.
    _require_literal_directories(dirs + recursive)
    expanded = _expand_leaves(leaves, root, glob_source)

    # THE PLAN: every declaration turned into the concrete destinations it names,
    # built once and then iterated by both phases. Each entry CARRIES THE
    # DECLARATION IT CAME FROM, which is what lets the accounting below be per
    # declaration rather than a total -- see
    # _require_every_declaration_planned(). Every loop from here on runs over
    # `plan`, so a phase cannot silently iterate a different (or emptied)
    # sequence than the one that was counted.
    plan = ([(rel, "dir", rel) for rel in dirs]
            + [(rel, "tree", rel) for rel in recursive]
            + [(name, "file", decl) for decl, names in expanded for name in names])
    _require_every_declaration_planned(dirs + recursive + leaves, plan)

    covered = []
    for rel, kind, _decl in plan:
        if kind == "file":
            continue                    # leaves are asked after the directories
        if not _covered_by(rel, covered):
            _require_uncommittable(dest.path, rel, env)
        covered.append(rel)
    for rel, kind, _decl in plan:
        if kind == "file" and not _covered_by(rel, covered):
            _require_uncommittable(dest.path, rel, env)
    for rel, kind, _decl in plan:
        one(rel, kind)
    return dest


def _paths(name, value):
    """A caller's path SEQUENCE -> a tuple of strings, materialized exactly once.

    Three jobs, each of them a shape that has already shipped or that this module
    would otherwise mis-evaluate:

      * MATERIALIZE. A generator, a map object, an iterator -- anything one-shot
        -- is exhausted by whoever converts it first, and every later phase then
        iterates nothing. Converting here, at entry, and never again is what
        makes that impossible; see _check_write_set's docstring for the
        reproduction.
      * NORMALIZE. os.fspath() turns a pathlib.Path into the str the rest of this
        module needs. Without it a Path entry never even reached a check:
        _is_pattern() does `ch in rel`, which is a TypeError on a Path, so
        dirs=(Path("data"),) crashed instead of being refused.
      * REJECT A BARE PATH. A str, bytes or os.PathLike passed where a SEQUENCE
        of paths belongs is iterated character by character, so dirs="data"
        declares four one-character destinations and evaluates those instead of
        the one the caller named. That is the "declared X, evaluated Y" shape
        with no way for the accounting below to see it, because the count is
        right. A TypeError, not a refusal: like an unknown `kind`, a caller who
        cannot say what it is writing has a bug in itself and must not be able to
        spell that bug as a verdict about a path.
      * REQUIRE EACH ENTRY TO NAME ONE PLACE, RELATIVE TO THE ROOT. Every entry
        here becomes a git PATHSPEC through _pathspec(), which prefixes './' --
        so an ABSOLUTE entry becomes './' + itself, which git resolves inside
        the worktree and answers about <root> + that entry, a different path
        entirely. Measured before this check existed: a leaves= entry naming the
        system password file by absolute path was refused `not_ignored`, with
        the refusal naming a path the caller never asked about, which sends the
        operator to fix the wrong file. It did not ACCEPT, and the second check
        that stopped it was measured too: the os.path.join in _check_write_set's
        one() leaves such an entry absolute, so it lands outside the register and
        _diagnose_outside refuses it `no_such_destination`. A second check
        covering for a wrong answer from the first is the same shape as asking
        check-ignore before ls-files -- correct today, by an accident of which
        check runs next.
        A '..' entry is the same fact one spelling over -- it cannot be
        normalized without following symlinks -- and gets the same refusal
        _check_destination gives a '..' path, because the remedy is identical:
        name a normalized path relative to the worktree root. It is a
        DestinationRefused rather than a TypeError because, unlike a bare string
        or an unknown kind, the caller HAS named one definite path; it is just
        not one this API can be asked about.
    """
    if isinstance(value, (str, bytes, os.PathLike)):
        raise TypeError(
            f"{name}= takes a sequence of worktree-relative paths, not a single "
            f"path ({value!r}); a bare string is iterated one character at a "
            "time, so the paths checked would not be the path you named")
    out = tuple(os.fspath(v) for v in value)
    for rel in out:
        if os.path.isabs(rel) or ".." in pathlib.PurePath(rel).parts:
            raise DestinationRefused(
                "unnormalized_path",
                f"{name}= entries are paths relative to the destination root, and "
                "each becomes a git pathspec asked of that root. An absolute or "
                "'..'-bearing entry makes git answer about a DIFFERENT path than "
                "the one named, so the verdict -- and the path in it -- would "
                "describe somewhere the caller never asked about", rel)
    return out


def _require_every_declaration_planned(declared, plan):
    """INVARIANT: EVERY declared destination must have planned at least one.

    NOT a refusal and not about caller input -- it is the standing guard against
    one defect that has now been found in THREE consecutive review rounds, each
    time in the argument the previous round did not test:

      1. `recursive` entries skipped the committability phase entirely;
      2. an unlistable `glob_source` (and a zero-match pattern) expanded a
         declared leaf to nothing, so its destination was never evaluated;
      3. a one-shot `dirs`/`recursive` was consumed by the first pass, so the
         later phases iterated an empty sequence.

    One shape underneath all three: a public entry point returns SUCCESS having
    evaluated nothing, because an input was empty, unexpanded or consumed rather
    than absent. Two things close it structurally. _paths() materializes each
    sequence exactly once, which kills (3) by construction. This ATTRIBUTES what
    came out the other end back to what went in, so it catches (1) and (2) and
    the one shape they share -- a declaration that reaches the checks with no
    destination of its own, whatever emptied it: a new argument that forgets to
    extend `plan`, an expander that starts returning [], a filter that drops
    entries.

    THE ACCOUNTING IS PER DECLARATION, NOT A TOTAL, and that is the whole of
    what it can claim. Every plan entry carries the declared path it came from
    (`dirs` and `recursive` name themselves; a leaf names the declaration that
    expanded to it), and each declared path must own at least one entry. A
    length comparison -- which is what this was -- holds while a declaration
    vanishes, because a neighbour that yielded plenty pays for it:

        leaves=("a-*.json", "b-*.json") where the first expands to two names and
        the second to none plans TWO entries for TWO declarations, so
        len(plan) >= len(declared) is satisfied and 'b-*.json' is never
        evaluated

    -- which is exactly the regression this guard names below (an expander that
    starts returning [], a filter that drops entries), arriving in the mixed
    shape instead of the all-empty one. Counted by attribution, the empty
    neighbour is named in the message.

    What it does NOT check: that a declaration planned the RIGHT destinations. A
    plan entry attributed to a declaration proves that declaration was evaluated,
    not that the expansion was correct -- an expander returning one wrong name
    per declaration passes this. That residue belongs to _expand() and to the
    `glob_source` the caller supplies, and is written down there.

    Raised as an AssertionError, deliberately. Every state it describes is
    unreachable through the public API as this module stands, so it is a fact
    about THIS CODE rather than about the destination, and giving it a
    DestinationRefused code would put a verdict in the refusal vocabulary that
    no caller can provoke and no test can honestly exercise. It is an explicit
    raise and not the `assert` statement so that `python -O` cannot switch off
    the one check whose whole job is to notice that a check stopped running.
    """
    accounted = {decl for _, _, decl in plan}
    unplanned = [d for d in declared if d not in accounted]
    if unplanned:
        raise AssertionError(
            f"private_egress: of {len(declared)} declared destination(s), "
            f"{unplanned!r} planned none, so each of those would be accepted "
            "without being evaluated. Refusing to return a Destination. "
            f"declared={list(declared)!r} planned={[p for p, _, _ in plan]!r}")


def _covered_by(rel, prefixes):
    """Is `rel` inside a path already answered for? `prefixes` holds the paths
    whose committability has been settled -- asked directly, or contained in one
    that was, which is the same fact one level up. Matched on a PATH BOUNDARY,
    never on characters: "private/verify2" is not covered by "private/verify"."""
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


# The characters that make a leaf a PATTERN rather than a name. fnmatch's own
# three, which are `sh`'s three, because the copy this describes is a shell glob:
# a set expression is what `cp private/x[0-9].csv` expands, so reading it as a
# literal filename would understate the set for a caller who wrote down what
# their copy really does.
GLOB_METACHARS = "*?["


def _is_pattern(rel):
    return any(ch in rel for ch in GLOB_METACHARS)


def _expand_leaves(leaves, root, glob_source):
    """`leaves` -> [(declaration, [the paths the copies will really write])].

    ONE PAIR PER DECLARED LEAF, in order, and the pairing is the point rather
    than a convenience: _require_every_declaration_planned() accounts per
    declaration, so it needs to know which names came from which pattern. A flat
    list of names cannot answer that, and a flat list is what let a declaration
    that expanded to nothing hide behind a neighbour that expanded to two.

    THE SOURCE IS PROVEN BEFORE IT IS BELIEVED. `glob_source` names a tree that
    is not the destination and that no other check in this module looks at, so an
    unlistable one -- stale, mistyped, on a volume that is not mounted -- used to
    expand to nothing and take its declared destinations out of the write set
    with it: no committability question, no leaf check, and check_write_set()
    returned a Destination anyway. That is acceptance without evaluation, which
    is a waiver whatever the caller meant by it, and worse than an ordinary
    waiver because the copy the caller then runs reads its REAL source and writes
    names this module never saw.

    So the source is listed once, before any pattern is expanded, and a source
    that cannot be listed is a refusal. Listed lazily -- only when some leaf
    really is a pattern -- because a `glob_source` nothing expands against has
    understated nothing, and refusing correct input is how guards get turned off.
    """
    out = []
    proven = False
    for rel in leaves:
        if not _is_pattern(rel):
            out.append((rel, [rel]))
            continue
        if not proven:
            _require_listable_source(root, glob_source)
            proven = True
        out.append((rel, _expand(rel, root, glob_source)))
    return out


def _require_literal_directories(rels):
    """`dirs` and `recursive` NAME directories; they are not expanded.

    The sibling of the empty expansion, found by looking for the same shape in
    the arguments beside the one the finding named. A pattern here was taken as a
    literal path, and a literal path that does not exist is exactly what every
    check treats as absent: the component walk stops at the glob, _check_leaf()
    returns because there is nothing there, and _scan_tree() returns because the
    path is not a directory. So

        check_write_set(ROOT, recursive=("private/1-raw-data/*",))

    ACCEPTED, having scanned none of the directories the copy really descends
    for the links and hard links that scan exists to find -- acceptance with the
    tree check evaluating nothing, in a different argument.

    Refused rather than expanded, and the two are not symmetrical with `leaves`:
    a zero-match leaf pattern can be answered by its literal, because the facts a
    leaf needs (its component chain, its committability) hold for every name the
    pattern could yield. A directory needs the facts of what is INSIDE it, and no
    literal stands in for that. Name the directories.
    """
    for rel in rels:
        if _is_pattern(rel):
            raise DestinationRefused(
                "pattern_names_a_directory",
                "a directory argument is a path, not a pattern: as written it "
                "names a path that does not exist, so the walk stops at the glob "
                "and the recursive scan of what is really there never runs. Name "
                "the directories, or expand them before declaring them", rel)


def _require_listable_source(root, glob_source):
    """The tree leaf patterns are expanded against must exist and be readable."""
    src = str(root if glob_source is None else glob_source)
    try:
        os.listdir(src)
    except OSError as e:
        raise DestinationRefused(
            "glob_source_unlistable",
            f"the tree leaf patterns are expanded against could not be listed "
            f"({e}). Every pattern would name nothing, so the destinations they "
            "declare would go unchecked while the copy still writes them",
            src) from None


def _expand(rel, root, glob_source):
    """One leaf pattern -> the destination-relative paths a copy will write.

    A pattern that matches NOTHING returns the pattern itself, and that is a
    decision rather than a fallback. `cp <src>/enphase_sam8760_*.csv <dst>/`
    copies nothing when the household has no SAM export, so "no matches" is not
    an error and refusing it would refuse a correct caller. But the guard may not
    report success on a destination it never looked at, so the literal pattern is
    checked in the expansion's place -- and that is not a token check. The
    component walk runs from the worktree root down to the pattern's own
    directory, so a link planted at private/1-raw-data is seen exactly as it is
    for a named leaf; and git answers the committability question for the pattern
    itself -- `git ls-files -- 'd/*.csv'` reads it as a pathspec and lists every
    tracked file it matches, `git check-ignore` answers for the whole excluded
    directory, and an excluded directory cannot have a child re-included. What
    the literal cannot answer is what sits AT a name the expansion did not yield:
    a FIFO or a hard link at one particular leaf. That residue belongs to a
    `glob_source` that does not name the copy's real source, and it is written
    down here rather than hidden inside an empty list.

    A pattern in a DIRECTORY component is refused instead. No literal stands in
    for it -- the components below the glob are exactly the ones a walk would
    have to check -- so its destinations are genuinely unknown.
    """
    if not _is_pattern(rel):
        return [rel]
    base = os.path.dirname(rel)
    pat = os.path.basename(rel)
    if _is_pattern(base):
        raise DestinationRefused(
            "pattern_names_a_directory",
            "this module expands a pattern in the last component only, so the "
            "directories this one names -- and everything a walk would check "
            "about them -- are unknown. Name the directory literally", rel)
    src = os.path.join(str(root if glob_source is None else glob_source), base)
    try:
        names = sorted(n for n in os.listdir(src) if fnmatch.fnmatch(n, pat))
    except FileNotFoundError:
        # The source tree is there (proven above) and this subdirectory of it is
        # not: the same fact as a pattern matching nothing -- the copy writes
        # nothing -- so it gets the same answer, the literal.
        names = []
    except OSError as e:
        raise DestinationRefused(
            "glob_source_unlistable",
            f"could not list {src} ({e}), so the destinations this pattern "
            "names are unknown", rel) from None
    if not names:
        return [rel]
    return [f"{base}/{n}" if base else n for n in names]


def refusal(path, *, kind, **kw):
    """Non-raising form: the refusal reason code, or None if accepted.

    `kind` is required here too. A convenience wrapper that quietly supplied one
    would be the optional-flag hole again, one layer out. `**kw` widens nothing:
    it can only carry what check_destination() accepts, so require_ignored=,
    worktrees= and env= each raise TypeError through this door as well -- not
    caught here, because a keyword that does not exist is a bug in the caller
    and not a verdict about the path.
    """
    try:
        check_destination(path, kind=kind, **kw)
        return None
    except DestinationRefused as e:
        return e.reason


def _backup_beside(path, bak):
    """A backup a revert can restore EXACTLY, not approximately.

    os.link keeps the ORIGINAL INODE alive under a second name, so putting it
    back with os.replace() restores the mode, owner, group, flags and extended
    attributes the file actually had. shutil.copy2 carries the permission bits
    and little else -- enough for the execute bit that a lost one breaks
    stage-private-data.sh, not enough to promise the file comes back unchanged.
    The copy is the fallback for filesystems that refuse hard links, and it is
    the weaker guarantee of the two, which is why it is second.
    """
    try:
        os.link(str(path), str(bak))
    except (OSError, AttributeError, NotImplementedError):
        shutil.copy2(str(path), str(bak))


def _revert_published(published):
    """Put every backed-up file back, INDEPENDENTLY, and report what would not go.

    Each restore is attempted whatever the ones before it did. A rollback that
    stops at its first failure leaves the rest of the set on the new table,
    which is the divergence the rollback exists to undo; and a rollback that
    raises replaces the original failure with its own, so the operator is told
    about the symptom instead of the cause. Returns the files it could not
    restore -- their .bak is still on disk as the recovery copy.
    """
    unrestored = []
    for path, bak in reversed(published):
        try:
            os.replace(str(bak), str(path))
        except OSError as exc:
            unrestored.append(f"{path.name} (from {bak.name}: {exc})")
            continue
        # AND THE BACKUP IS GONE AFTERWARDS, which os.replace does NOT
        # guarantee here. _backup_beside() prefers a hard link, so when the
        # file's own replace never happened the two names are links to ONE
        # inode -- and POSIX rename() between two links to the same inode
        # succeeds while doing nothing at all, leaving the .bak in a directory
        # the guard walks. Restoring is what the replace is for; removing the
        # copy afterwards has to be asked for separately.
        try:
            os.unlink(str(bak))
        except OSError:
            pass                      # already consumed by the replace
    return unrestored


def regenerate_case_fold(root=ROOT):
    """Rewrite the marked block in BOTH files from python's own str.lower(),
    and report which of them changed.

    The regeneration path the generated table needs, and the reason the table
    may be committed at all: nobody edits those lines by hand, and a reader who
    wants to know what they say runs case_fold_pairs(). The two blocks carry the
    SAME table text in two quotings -- a python triple-quoted string here, a
    shell single-quoted string there -- which is safe because
    _check_case_fold_pairs() has proved no pair needs escaping in either.

    THE WHOLE POINT OF THIS ENTRY POINT IS THAT THE TWO FILES STAY IDENTICAL,
    so it may not leave them divergent (issue #234). Reading, validating and
    writing each file in turn did exactly that: this file's block was rewritten
    before the shell's markers were so much as looked at, so a shell script with
    a mangled or missing marker got the python half updated and an assertion --
    the two folds different, from the command that exists to keep them the same.
    Both files are now read and validated and both replacement texts built
    BEFORE either is written, and each write goes to a temporary in the target's
    own directory and lands with os.replace(), so a file is never observed
    half-written and a failure part-way through leaves the file it has not
    reached untouched. Two replaces are still two syscalls, so a file that has
    already landed is kept as a .bak and PUT BACK if a later replace fails: the
    set advances or reverts together, and an exception part-way through
    publication can no longer leave the python guard folding pairs the shell
    guard does not -- the narrower side being the fail-open one.
    carbon_fullyear.py and parse_bills.py publish their artifact sets the same
    way. Three details carry that promise, each of them a way it was not kept:

      * the backup is REGISTERED before its replace is attempted, not after,
        because an asynchronous KeyboardInterrupt between the replace returning
        and the bookkeeping would leave a landed file with no rollback record;
      * every restore is attempted INDEPENDENTLY and the original failure is
        preserved, because a rollback that stops at its first problem leaves the
        rest of the set on the new table, and one that raises tells the operator
        about the symptom instead of the cause;
      * the backup is a HARD LINK where the filesystem allows one, so putting it
        back restores the file's real mode, owner and attributes rather than the
        subset a copy carries.

    A hard kill still lands between two syscalls. What it leaves is the .bak
    beside the file as the recovery copy -- and the NEXT run refuses while that
    copy is there, rather than quietly overwriting the only remaining evidence
    of what the file held. The guard case asserting both files record ONE
    Unicode version fails throughout, so the state is loud.

    NEVER NARROWS THE DOMAIN. str.lower() grows with the interpreter's Unicode
    version, so regenerating on an older python than the table was built with
    would silently DELETE pairs -- aliases the guard would stop seeing, which is
    fail-open. A run that would drop any committed pair refuses and writes
    nothing, naming what it would have lost. See the domain note at
    CASE_FOLD_UNICODE.

    The checks here raise AssertionError rather than using `assert`, because
    `python -O` strips the statement and this function writes files.
    """
    begin, end = CASE_FOLD_MARKERS
    version = unicodedata.unidata_version
    pairs = case_fold_pairs()
    body = case_fold_sed_script(pairs)

    fresh = set(pairs)

    # PHASE 1 -- read and validate BOTH files, and build BOTH outputs. Nothing
    # is written while anything here can still fail.
    planned = []
    for path, opening, closing in (
            (root / "analysis" / "private_egress.py",
             f'CASE_FOLD_UNICODE = "{version}"\nCASE_FOLD_SED = """\\', '"""'),
            (root / "stage-private-data.sh",
             f"# generated from python's str.lower() under Unicode {version}\n"
             "_CASE_FOLD_SED='", "'")):
        # A RECOVERY COPY FROM AN INTERRUPTED RUN STOPS THIS ONE. Asked in
        # phase 1, so a refusal writes nothing, and asked of EVERY planned file
        # rather than only the ones whose text moved -- the file that kept a
        # .bak is the one whose rollback could not put it back, which is
        # precisely the file a later run would find already carrying the new
        # table and skip as a no-op. Matched by glob and not by name: the
        # interrupted run had a different pid, so its copy is never the one this
        # run would have created.
        stale = sorted(q.name for q in path.parent.glob(f"{path.name}.bak*"))
        if stale:
            raise AssertionError(
                f"{', '.join(stale)} is beside {path.name}, which is the "
                f"recovery copy an interrupted regeneration leaves behind. "
                f"Nothing was written. The two implementations may be carrying "
                f"different fold tables right now. Compare the copy against "
                f"{path.name}, keep whichever is right, and remove it by hand "
                f"-- a run that deletes a recovery copy to get past it is how "
                f"the interrupted state stops being recoverable.")
        text = path.read_text()
        head, mark, rest = text.partition(begin + "\n")
        if not mark:
            raise AssertionError(
                f"{path} has no generated-case-fold block to rewrite, so "
                f"nothing was written to either file")
        was, mark, tail = rest.partition(end + "\n")
        if not mark:
            raise AssertionError(
                f"{path}'s generated-case-fold block has no end marker, so "
                f"nothing was written to either file")
        # The table THIS FILE already carries, read out of the block about to be
        # replaced rather than off this module's own import, so the answer is
        # about the file being rewritten.
        held = set(_CASE_FOLD_RULE.findall(was))
        dropped = sorted(held - fresh)
        if dropped:
            raise AssertionError(
                f"this interpreter's str.lower() (Unicode {version}) would drop "
                f"{len(dropped)} of the {len(held)} pairs {path.name} already "
                f"carries, so nothing was written to either file: {dropped[:5]}. "
                f"Regenerate on an interpreter whose Unicode is at least the one "
                f"that block records, since a narrower fold is an alias the "
                f"guard stops seeing (fail-open).")
        block = f"{begin}\n{opening}\n{body}\n{closing}\n{end}\n"
        planned.append((path, text, head + block + tail))

    # PHASE 2 -- publish. Only files whose text really moved are touched, so a
    # no-op run leaves both mtimes alone.
    #
    # AND PUBLISH THEM AS A SET. Building both texts before writing either closes
    # a VALIDATION failure splitting the pair, but two os.replace calls are two
    # syscalls, and an exception, a full disk or a permission change on the
    # second one would still leave this file on the new table and the shell on
    # the old. That is the divergence the whole entry point exists to prevent,
    # and it is fail-open in the shell's direction: the stale side folds fewer
    # pairs, so it sees fewer aliases. Each file that has already landed is
    # copied aside first and restored if a later one fails.
    changed = []
    published = []                    # (path, bak) for every backup TAKEN
    try:
        for path, old, new in planned:
            if new == old:
                continue
            tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
            bak = path.with_name(f"{path.name}.bak{os.getpid()}")
            try:
                tmp.write_text(new)
                # os.replace hands the DESTINATION the temporary's inode, and with
                # it the temporary's mode: without this, stage-private-data.sh comes
                # back without its execute bit.
                os.chmod(str(tmp), os.stat(str(path)).st_mode & 0o7777)
                _backup_beside(path, bak)
            except BaseException:
                for leftover in (tmp, bak):
                    try:
                        os.unlink(str(leftover))
                    except OSError:
                        pass          # nothing to clean up is not a failure
                raise
            # REGISTERED BEFORE THE REPLACE, NOT AFTER. Between the replace
            # returning and this line, python can still deliver an asynchronous
            # KeyboardInterrupt -- and a file that has landed with no rollback
            # record is exactly the split this block exists to prevent. The
            # other order is safe because reverting a file whose replace never
            # happened restores identical bytes: a wasted syscall, not a wrong
            # one.
            published.append((path, bak))
            try:
                os.replace(str(tmp), str(path))
            except BaseException:
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass              # nothing to clean up is not a failure
                raise
            changed.append(path.name)
    except BaseException as cause:
        # Back to the pair we started with. os.replace consumes the .bak, and
        # reversed() puts the files back in the order they were taken.
        unrestored = _revert_published(published)
        if unrestored:
            raise AssertionError(
                f"publication failed AND the rollback could not put "
                f"{len(unrestored)} file(s) back: {'; '.join(unrestored)}. The "
                f"two implementations may now carry DIFFERENT fold tables, and "
                f"the shell's would be the narrower, fail-open one. The .bak "
                f"files named are the recovery copies -- reconcile them by hand "
                f"before staging anything. Original failure: {cause!r}") from cause
        raise
    for _, bak in published:
        os.unlink(str(bak))
    return changed


if __name__ == "__main__":   # pragma: no cover - manual smoke check
    if sys.argv[1:2] == ["--regenerate-case-fold"]:
        try:
            moved = regenerate_case_fold()
        except AssertionError as exc:      # a refusal, not a crash: it is the
            raise SystemExit(f"case fold: {exc}")   # answer the operator asked for
        print(f"case fold: {len(case_fold_pairs())} pairs from Unicode "
              f"{unicodedata.unidata_version}; "
              + (f"rewrote {', '.join(moved)}" if moved else "both copies were current"))
        raise SystemExit(0)
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        raise SystemExit(f"usage: private_egress.py <{'|'.join(KINDS)}> <destination path>")
    try:
        d = check_destination(sys.argv[2], kind=sys.argv[1])
    except DestinationRefused as exc:
        print(exc)
        raise SystemExit(1)
    print(f"ACCEPTED {d}")
