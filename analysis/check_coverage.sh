#!/bin/bash
# Coverage gate for the analysis package: fails unless total coverage >= 90%.
#
# Runs every test suite in-process and every generator against the REAL inputs
# in the private/verify sandbox, so this gate needs the private archive and runs
# locally (like the section 9 regeneration gate). CI runs the suites and the
# synthetic tier but cannot reach the real-input paths, so the numeric floor is
# enforced here, on the machine where the data lives.
#
# Usage, from the repo root:  ./analysis/check_coverage.sh
# Requires: ./.venv with coverage installed (pip install coverage), the private
# archive staged per CLAUDE.md (private/verify/usage.csv etc.).
#
# One run at a time (issue #233). Every suite and generator accumulates into the
# single data file $ROOT/.coverage, which a run erases at startup, so a second
# run started while the first is still going (the suite takes ~10 minutes)
# destroys the first run's measurement and both then print a percentage from
# whatever survived. The run therefore takes an exclusive kernel lock (flock)
# on $ROOT/.coverage.lock before it touches the data file, and a second run
# refuses to start while the lock is held. The kernel drops the lock the
# instant the holder exits, crash or kill included, so a lock file left behind
# by a dead run never blocks anyone and never needs clearing by hand.
#
# "Is one already running?" Do not trust `ps aux | grep check_coverage`: most
# of the wall time is spent inside child `coverage run` processes, and the
# script's own name is easy to miss in that list. Ask the lock instead:
#     cat .coverage.lock            # pid, start time and data file of the LAST holder
#     lsof .coverage.lock           # non-empty while a run (or its children) holds it
# The end of a run prints the data file it measured and the statement count it
# saw, so a truncated measurement is visible in the output itself.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
COV="$ROOT/.venv/bin/coverage"
PY="$ROOT/.venv/bin/python"
export COVERAGE_FILE="$ROOT/.coverage"
LOCK="$COVERAGE_FILE.lock"

[ -x "$COV" ] || { echo "coverage not installed: ./.venv/bin/pip install coverage"; exit 2; }
[ -x "$PY" ] || { echo "$PY missing -- create the venv first (CLAUDE.md)"; exit 2; }
[ -f private/verify/usage.csv ] || { echo "private/verify/usage.csv missing -- stage the sandbox first (CLAUDE.md)"; exit 2; }

# Take the lock on fd 9, which stays open (and so held) for the rest of this
# script and is inherited by every child it starts. Opened for APPEND so that
# opening never truncates a live holder's pid line; the python child locks the
# inherited descriptor, and a flock lives on the open file, not on the process,
# so it outlives the child that took it.
exec 9>>"$LOCK"
if ! "$PY" -c 'import fcntl, sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)'; then
  echo "check_coverage: another check_coverage.sh run holds $LOCK ($(tr '\n' ' ' < "$LOCK"))" >&2
  echo "check_coverage: refusing to start -- two runs share one data file and would destroy each other's measurement; wait for it (lsof $LOCK) and re-run" >&2
  exit 2
fi
: > "$LOCK"
printf 'pid=%s started=%s data=%s\n' "$$" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$COVERAGE_FILE" >&9

# Erase the previous run's data (but never the lock file this run is holding).
for f in "$COVERAGE_FILE" "$COVERAGE_FILE".*; do
  [ "$f" = "$LOCK" ] || rm -f "$f"
done

# 1) the test suites, in-process
for t in test_rates test_report_consistency test_tou_audit test_parse_bills \
         test_carbon_fullyear test_carbon_dispatch_tradeoff test_household test_publish \
         test_service_headroom test_irreducible_bill test_privacy_tiers test_bill_decomposition \
         test_rates_history test_tou_spread test_scripts_runnable test_nem3_grandfathering \
         test_dsgs_vpp_backtest test_cca_rate_extraction test_cca_bundled_counterfactual \
         test_battery_sizing_curve test_battery_dispatch_policies \
         test_perfect_foresight_dispatch test_tou_structure_stress \
         test_gross_import_decomposition test_reprice_by_vintage test_uncertainty_propagation \
         test_battery_backup_sims test_deep_analyses test_lifetime_payback test_soiling_analysis \
         test_extended_findings test_battery_plan_matrix test_package_results test_report_tokens \
         test_llm_providers test_egress_preflight test_report_blocks test_prose_blocks test_prose_lint \
         test_prose_rhythm test_stamp_report_version \
         test_generate_report test_quiet_night_floor test_heat_pump_conversion \
         test_stage_private_data test_extra_results test_all_electric_endgame \
         test_dry_run test_private_egress test_suite_runner; do
  "$COV" run --rcfile="$ROOT/.coveragerc" "analysis/$t.py" >/dev/null
  echo "suite  $t"
done

# 2) every generator, on the real inputs, in the sandbox
cd private/verify
cp "$ROOT"/analysis/*.py .
cp "$ROOT"/data/pvoutput_daily.csv "$ROOT"/data/enphase_daily_production.csv .
for g in behavior_rebuild battery_dispatch_policies battery_plan_matrix \
         package_results extended_findings report_data deep_analyses \
         battery_backup_sims analyze analyze_norelief carbon_fullyear \
         carbon_dispatch_tradeoff tou_audit lifetime_payback soiling_analysis \
         parse_bills billing_model_nem service_headroom rates_history tou_spread \
         bill_decomposition irreducible_bill nem3_grandfathering dsgs_vpp_backtest \
         cca_rate_extraction cca_bundled_counterfactual battery_sizing_curve \
         perfect_foresight_dispatch tou_structure_stress gross_import_decomposition \
         reprice_by_vintage quiet_night_floor uncertainty_propagation \
         heat_pump_conversion extra_results all_electric_endgame; do
  "$COV" run --rcfile="$ROOT/.coveragerc" "$g.py" >/dev/null 2>&1 \
    && echo "gen    $g" || { echo "gen    $g FAILED"; exit 1; }
done
cd "$ROOT"

"$COV" combine >/dev/null
report=$("$COV" report --rcfile=.coveragerc)
printf '%s\n' "$report"
# The TOTAL row's Stmts column: how much the run actually measured. A number
# far below the usual (~14,500 in 2026-09) means a truncated measurement, not
# a coverage change.
stmts=$(printf '%s\n' "$report" | awk '$1 == "TOTAL" { print $2 }')
[ -n "$stmts" ] || { echo "check_coverage: no TOTAL row in the coverage report -- nothing was measured"; exit 2; }
echo "measured $stmts statements from $COVERAGE_FILE"
"$COV" report --rcfile=.coveragerc --fail-under=90 >/dev/null \
  && echo "COVERAGE GATE: PASS (>= 90%)" \
  || { echo "COVERAGE GATE: FAIL (< 90%)"; exit 1; }
