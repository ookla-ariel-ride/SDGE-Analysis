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
     .gitignore that was already correct.
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
import subprocess
import sys

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
# safe.directory, and aliases named after the three commands run here (git does
# not let an alias shadow a builtin). core.bare is the one that needs its own
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
# AND IT IS PROVED, not assumed: _require_isolation_proven() below asks the
# running git what each forced key actually reads back as, and refuses if any of
# them is not ours. Version numbers are what this comment can offer; what the
# guard acts on is the git in front of it.
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


def sanitized_env(env=None):
    """`env` (default os.environ) with every variable that can move git's answer
    -- or change what a relative path or a pathspec means -- removed, and the
    ambient git configuration switched off.

    Two operations, in this order: DROP what the caller (or the caller's shell)
    supplied, then WRITE the controlled values that make the probes answer from
    repository-local configuration alone. The drop comes first because the
    GIT_CONFIG* prefix covers the names the second half writes.
    """
    src = os.environ if env is None else env
    drop = (set(GIT_IDENTITY_VARS) | set(PATH_MEANING_VARS)
            | set(PATHSPEC_MEANING_VARS))
    out = {k: v for k, v in src.items()
           if k not in drop and not k.startswith(GIT_CONFIG_PREFIX)}
    out.update(GIT_CONFIG_ISOLATION)
    return out


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
    """
    opts = [tok for name, value in overrides
            for tok in ("-c", f"{name}={value}")]
    return subprocess.run(["git"] + opts + ["-C", str(cwd)] + list(args),
                          capture_output=True, text=True, env=sanitized_env(env))


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


def _physical(path):
    """The shell's `(cd -- "$1" && pwd -P)`: an absolute, symlink-resolved path,
    or None when it is not an existing directory."""
    try:
        if not os.path.isdir(path):
            return None
        return os.path.realpath(path)
    except OSError:
        return None


def common_git_dir(path, env=None):
    """`--git-common-dir` of `path`, absolute and symlink-resolved, or None.

    --path-format=absolute is load-bearing: without it rev-parse returns a
    RELATIVE ".git" from a repo root and an ABSOLUTE path from a linked
    worktree, so a naive comparison rejects a legitimate destination. git < 2.31
    has no such flag, so its (already cwd-relative) answer is normalized instead
    of being compared raw.
    """
    if not os.path.isdir(path):
        return None
    r = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path, env)
    if r.returncode != 0:
        r = _git(["rev-parse", "--git-common-dir"], path, env)
        if r.returncode != 0:
            return None
    out = r.stdout.strip()
    if not out:
        return None
    if not os.path.isabs(out):
        out = os.path.join(str(path), out)
    return _physical(out)


def self_common_git_dir(env=None):
    """The common dir of the checkout THIS FILE lives in, or None.

    The reference is this file's own resolved location, exactly as the shell
    takes ${BASH_SOURCE[0]} through its link chain first: which COPY is running
    decides which repository's worktrees are eligible, and a copy of this module
    on some other path must not authorize this checkout's destinations.
    """
    return common_git_dir(str(ROOT), env)


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
    now = common_git_dir(wt_real, env)
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
            "prune' removes it, because the .git it recorded is gone", wt_real)
    raise DestinationRefused(
        "different_repository",
        f"this checkout's register names this worktree, but the directory there "
        f"belongs to {now} NOW, not to {self_git}: the worktree was deleted "
        f"without 'git worktree remove' and an UNRELATED repository was created "
        f"at the same path. Not a missing directory and not a prunable entry -- "
        f"'git worktree prune' keeps it (the directory exists) and 'git worktree "
        f"remove --force' refuses it ('is not a .git file'). Remove or move that "
        f"directory and then run 'git worktree prune'", wt_real)


def _diagnose_outside(path, self_git, env):
    """Why `path` is in no registered worktree -- the shell's four distinct
    refusals, kept distinct because their remedies differ."""
    near = _nearest_existing(path)
    if near is None or _physical(near) is None:
        return "no_such_destination", "no existing directory on this path"
    dst_git = common_git_dir(_physical(near), env)
    if dst_git is None:
        return "not_a_worktree", f"{near} is not inside a git repository"
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
        self_git = self_common_git_dir(env)
        if not self_git:
            raise DestinationRefused(
                "self_unlocatable",
                f"{__file__} does not resolve into a git working tree, so which "
                "checkout's worktrees are eligible cannot be established", lit)

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
    for key, value in overrides:
        r = _git(["config", "--get", key], worktree, env, overrides)
        effective = r.stdout.strip()
        if effective == value:
            continue
        version = _git(["--version"], worktree, env).stdout.strip() or "unknown"
        raise DestinationRefused(
            "isolation_unproven",
            f"{key} reads back as "
            f"{effective or '<unset>'!r} in {worktree}, not "
            f"{value!r} ('git config --get' exited {r.returncode}; "
            f"{version}). The ignore question below would then be answered by the "
            "operator's global, XDG or system configuration rather than by the "
            "repository, and a destination that does NOT ignore private data could "
            "answer that it does. 'git -c' has existed since git 1.7.2 (2010), so a "
            "git that ignores it is older than anything this repository is developed "
            "on: upgrade git", worktree)
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
    did apply every one of them, handing back the list the two probes then run
    with. What is left is the destination's own .gitignore, its info/exclude and
    its index.

    This is the ONLY place in this module where a caller-supplied path becomes a
    git PATHSPEC (everything else hands git a -C directory), so it is the one
    place _pathspec() has to be applied -- see there for what a leading ':'
    otherwise does to both questions below.
    """
    overrides = _require_isolation_proven(worktree, env)
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
    r = _git(["check-ignore", "-q", "--", spec], worktree, env, overrides)
    if r.returncode == 0:
        return
    if r.returncode == 1:
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


if __name__ == "__main__":   # pragma: no cover - manual smoke check
    if len(sys.argv) != 3 or sys.argv[1] not in KINDS:
        raise SystemExit(f"usage: private_egress.py <{'|'.join(KINDS)}> <destination path>")
    try:
        d = check_destination(sys.argv[2], kind=sys.argv[1])
    except DestinationRefused as exc:
        print(exc)
        raise SystemExit(1)
    print(f"ACCEPTED {d}")
