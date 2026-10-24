#!/bin/bash
# Coverage gate for the analysis package: fails unless total coverage >= 90%.
#
# Runs every suite in a measured child. The real generator census uses guarded
# disposable snapshots seeded from private/verify and the raw archive, so this
# gate runs locally (like the section 9 regeneration gate). CI runs the suites and the
# synthetic tier but cannot reach the real-input paths, so the numeric floor is
# enforced here, on the machine where the data lives.
#
# Usage, from the repo root:  ./analysis/check_coverage.sh [--jobs 1|2]
# Requires: ./.venv with coverage installed (pip install coverage), the private
# archive staged per CLAUDE.md (private/verify/usage.csv etc.).
#
# One run at a time (issue #233). Lock the analysis/ DIRECTORY on fd 9 so
# unlinking a holder file cannot bypass a live run. The coordinator passes
# this descriptor through measured suites and census children, together with
# each private sandbox's marker. A surviving child keeps both locks held.
# Fresh measurements and full task logs live under private/verify-coverage-*.
# A run combines only its accepted measurements after all tasks and cleanup pass.
#
# "Is one already running?" Do not trust `ps aux | grep check_coverage`: most
# of the wall time is spent inside child `coverage run` processes, and the
# script's own name is easy to miss in that list. Ask instead:
#     cat .check_coverage.holder    # pid, start time and data file of the LAST run to start
#     lsof analysis                 # a `9r` row while a run (or a child of it) holds the lock
# The holder file is informational only; deleting it neither releases nor
# breaks the lock. The end of a run prints the data file it measured and the
# statement count it saw, so a truncated measurement is visible in the output
# itself.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
COV="$ROOT/.venv/bin/coverage"
PY="$ROOT/.venv/bin/python"
export COVERAGE_FILE="$ROOT/.coverage"
LOCK_DIR="$ROOT/analysis"
HOLDER="$ROOT/.check_coverage.holder"

[ -x "$COV" ] || { echo "coverage not installed: ./.venv/bin/pip install coverage"; exit 2; }
[ -x "$PY" ] || { echo "$PY missing -- create the venv first (CLAUDE.md)"; exit 2; }
[ -f private/verify/usage.csv ] || { echo "private/verify/usage.csv missing -- stage the sandbox first (CLAUDE.md)"; exit 2; }

# Take the lock on fd 9, which stays open (and so held) for the rest of this
# script and is inherited by every child it starts. The python child locks the
# inherited descriptor; a flock lives on the open file description, not on the
# process, so it outlives the child that took it. Only EAGAIN/EWOULDBLOCK means
# "held by another run"; any other error (a filesystem without flock, a broken
# venv) is reported as what it is instead of being blamed on a phantom run.
exec 9<"$LOCK_DIR"
lock_rc=0
"$PY" -c 'import errno, fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError as e:
    if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
        sys.exit(1)
    sys.stderr.write(f"check_coverage: could not take the lock on {sys.argv[1]}: {e}\n")
    sys.exit(2)' "$LOCK_DIR" || lock_rc=$?
if [ "$lock_rc" -ne 0 ]; then
  if [ "$lock_rc" -eq 1 ]; then
    holder="$( [ -f "$HOLDER" ] && tr '\n' ' ' < "$HOLDER" || echo "holder file $HOLDER missing" )"
    echo "check_coverage: another check_coverage.sh run holds the lock on $LOCK_DIR ($holder)" >&2
    echo "check_coverage: refusing to start -- two runs would race on checkout state; wait for it (lsof $LOCK_DIR) and re-run" >&2
  fi
  exit 2
fi
printf 'pid=%s started=%s data=%s\n' "$$" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$COVERAGE_FILE" > "$HOLDER"

# The coordinator gives every task a fresh private measurement and log file.
# fd 9 remains open through it, suites, and real census children.
export SDGE_COVERAGE_LOCK_FD=9
exec "$PY" "$ROOT/analysis/coverage_runner.py" "$@"
