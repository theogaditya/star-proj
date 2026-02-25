#!/usr/bin/env bash
# test_full_experiment.sh — Short debug run of the entire experiment pipeline
# Runs all scenarios (pcm-cpu, pcm-h, pcm-ch) with:
#   - 1 cycle only
#   - 10 seconds high load / 10 seconds low load
# This proves the entire pipeline (including analysis.py) works end-to-end.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR/.."
DEBUG_RESULTS="$SCRIPT_DIR/results"

echo "============================================================"
echo " Running FULL EXPERIMENT DEBUG (1 cycle, 10s phases)"
echo " Output: $DEBUG_RESULTS"
echo "============================================================"

# Clean previous debug results
rm -rf "$DEBUG_RESULTS"
mkdir -p "$DEBUG_RESULTS"

cd "$BASE_DIR"

# Temporarily override the load parameters for the run-experiment.sh script
# We do this by exporting the variables that run-experiment.sh can use,
# or we can just create a wrapper that overrides the default variables inside it.

# Since run-experiment.sh hardcodes NUM_CYCLES=5, HIGH_SECS=100, LOW_SECS=100
# and RESULTS_BASE inside itself, the safest way for a clean debug is to
# run the helper functions directly, OR we can inject sed replacements for this debug run.
# Injecting is easiest to ensure we test the EXACT same logic:

echo "[*] Creating temporary modified run script for fast debug..."
cp run-experiment.sh debug/run-debug.sh

# Modify the hardcoded variables in the copied script for a super fast run
sed -i 's/^NUM_CYCLES=5/NUM_CYCLES=1/' debug/run-debug.sh
sed -i 's/^HIGH_SECS=100/HIGH_SECS=25/' debug/run-debug.sh
sed -i 's/^LOW_SECS=100/LOW_SECS=25/' debug/run-debug.sh
# Also fast-forward the scrape intervals so we don't wait 60s per run
sed -i 's/PCM_CPU_SCRAPE_INTERVALS=(60s 30s 15s)/PCM_CPU_SCRAPE_INTERVALS=(15s)/' debug/run-debug.sh

# Override the RESULTS_BASE to point to our debug folder
sed -i 's|^RESULTS_BASE="$SCRIPT_DIR/results"|RESULTS_BASE="'"$DEBUG_RESULTS"'"|' debug/run-debug.sh

# Fix SCRIPT_DIR since the script was copied into the debug folder
sed -i 's|^SCRIPT_DIR=.*|SCRIPT_DIR="'"$BASE_DIR"'"|' debug/run-debug.sh

chmod +x debug/run-debug.sh

# Run the modified script
echo "[*] Starting fast debug run..."
./debug/run-debug.sh

echo "============================================================"
echo " DEBUG RUN COMPLETE"
echo " Check $DEBUG_RESULTS for CSVs and Plots!"
echo "============================================================"
