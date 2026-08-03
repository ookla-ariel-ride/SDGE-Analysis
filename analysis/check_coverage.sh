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
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
COV="$ROOT/.venv/bin/coverage"
export COVERAGE_FILE="$ROOT/.coverage"

[ -x "$COV" ] || { echo "coverage not installed: ./.venv/bin/pip install coverage"; exit 2; }
[ -f private/verify/usage.csv ] || { echo "private/verify/usage.csv missing -- stage the sandbox first (CLAUDE.md)"; exit 2; }

rm -f "$COVERAGE_FILE" "$COVERAGE_FILE".*

# 1) the test suites, in-process
for t in test_rates test_report_consistency test_tou_audit test_parse_bills \
         test_carbon_fullyear test_carbon_dispatch_tradeoff test_household test_publish \
         test_service_headroom test_irreducible_bill test_privacy_tiers test_bill_decomposition \
         test_rates_history test_tou_spread test_scripts_runnable test_nem3_grandfathering \
         test_dsgs_vpp_backtest test_cca_rate_extraction test_cca_bundled_counterfactual \
         test_battery_sizing_curve test_perfect_foresight_dispatch test_tou_structure_stress \
         test_gross_import_decomposition test_reprice_by_vintage; do
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
         reprice_by_vintage; do
  "$COV" run --rcfile="$ROOT/.coveragerc" "$g.py" >/dev/null 2>&1 \
    && echo "gen    $g" || { echo "gen    $g FAILED"; exit 1; }
done
cd "$ROOT"

"$COV" combine >/dev/null
"$COV" report --rcfile=.coveragerc
"$COV" report --rcfile=.coveragerc --fail-under=90 >/dev/null \
  && echo "COVERAGE GATE: PASS (>= 90%)" \
  || { echo "COVERAGE GATE: FAIL (< 90%)"; exit 1; }
