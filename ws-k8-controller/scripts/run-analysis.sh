#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Generic Analysis Runner
# Usage:
#   bash scripts/run-analysis.sh websocket experiment-a-hpa
# ==========================================================

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <workload> <experiment>"
  exit 1
fi

WORKLOAD="$1"
EXPERIMENT="$2"

RAW_DIR="results/raw/${WORKLOAD}/${EXPERIMENT}"
PROCESSED_DIR="results/processed/${WORKLOAD}/${EXPERIMENT}"

ANALYSIS_DIR="analysis"

if [ ! -d "$RAW_DIR" ]; then
  echo "[✗] Raw directory not found: $RAW_DIR"
  exit 1
fi

mkdir -p "$PROCESSED_DIR"

echo "=============================================="
echo " Running Analysis"
echo " Workload:   $WORKLOAD"
echo " Experiment: $EXPERIMENT"
echo "=============================================="

export RAW_DIR
export PROCESSED_DIR

# ----------------------------------------------------------
# Choose Parser + Plotter Based on Experiment Type
# ----------------------------------------------------------

if [[ "$EXPERIMENT" == *"instrumented"* ]]; then
  PARSER="$ANALYSIS_DIR/parse_logs_instrumented.py"
  PLOTTER="$ANALYSIS_DIR/plot_experiment_instrumented.py"
  echo "[*] Detected instrumented experiment"
else
  PARSER="$ANALYSIS_DIR/parse_logs.py"
  PLOTTER="$ANALYSIS_DIR/plot_experiment.py"
fi

# ----------------------------------------------------------
# Parse logs
# ----------------------------------------------------------
if python3 "$PARSER"; then
  echo "[✓] Parsing complete"
else
  echo "[✗] Parsing failed"
  exit 1
fi

# ----------------------------------------------------------
# Plot results
# ----------------------------------------------------------
if python3 "$PLOTTER"; then
  echo "[✓] Plot generation complete"
else
  echo "[✗] Plot generation failed"
  exit 1
fi

echo "=============================================="
echo " Analysis Completed Successfully"
echo " Processed results:"
echo " $PROCESSED_DIR"
echo "=============================================="