#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  Full Sensitivity Analysis Test Script (Paper 2)
#  Runs the two-cycle restorm workload varying one parameter at a time.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Running Test 1: Vary the cooldown window ==="
# Cooldown values of 30, 60, 90, 120, 150, 180 seconds.
# Test with a connection gap just shorter and just longer than the cooldown.
for CD in 30 60 90 120 150 180; do
  # Gap just shorter (CD - 10s) and just longer (CD + 10s)
  for GAP in $((CD - 10)) $((CD + 10)); do
    echo ">> Sweeping Cooldown: $CD, Gap: $GAP"
    bash scripts/run-sensitivity.sh --sweep cooldown --cooldown "$CD" --gap "$GAP" --reuse-cluster --keep-cluster
  done
done

echo "=== Running Test 2: Vary target connections per pod (T) ==="
# T values of 50, 100, 150, 200.
for T in 50 100 150 200; do
  echo ">> Sweeping Target (T): $T"
  bash scripts/run-sensitivity.sh --sweep target --target "$T" --reuse-cluster --keep-cluster
done

echo "=== Running Test 3: Vary the Prometheus scrape interval ==="
# Intervals of 15, 30, 60 seconds.
for S in 15 30 60; do
  echo ">> Sweeping Scrape Interval: $S"
  bash scripts/run-sensitivity.sh --sweep scrape --scrape "$S" --reuse-cluster --keep-cluster
done

echo "=== Running Test 4: Vary maxScaleDownStep ==="
# Values of 1, 2, 3, 4.
for STEP in 1 2 3 4; do
  echo ">> Sweeping maxScaleDownStep: $STEP"
  bash scripts/run-sensitivity.sh --sweep step --step "$STEP" --reuse-cluster --keep-cluster
done

echo "=== Tearing down cluster ==="
kind delete cluster --name stateful-sens 2>/dev/null || true

echo "=== Parsing and Aggregating Results ==="
# This will output four result tables (CSV)
python3 analysis/sensitivity/parse_sensitivity.py

echo "=== Generating Plots ==="
# This will output the boundary plot and sweep graphs
python3 analysis/sensitivity/plot_sensitivity.py

echo "=== Sensitivity Analysis Complete! ==="
