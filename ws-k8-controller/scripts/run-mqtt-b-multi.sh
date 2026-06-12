#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  MQTT-B statistical replication (Paper 2 revision: 5 independent runs).
#  Re-runs the existing experiment-b-stateful script RUNS times, preserves
#  each run's raw results, then aggregates mean/std and a multi-run figure.
#
#  Usage: RUNS=5 ./run-mqtt-b-multi.sh
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXP_DIR="$PROJECT_ROOT/experiments/mqtt/experiment-b-stateful"
RUNS="${RUNS:-5}"

set +u
source "$EXP_DIR/config.env"
set -u

if [ -z "${RESULT_DIR:-}" ]; then
  RESULT_DIR="$PROJECT_ROOT/results/raw/mqtt/experiment-b-stateful"
fi
BASE_DIR="${RESULT_DIR%/}"

echo "=== MQTT-B replication: $RUNS runs (base result dir: $BASE_DIR) ==="

for i in $(seq 1 "$RUNS"); do
  echo ""
  echo "================================================="
  echo "  MQTT-B run $i / $RUNS  --  $(date)"
  echo "================================================="
  if bash "$EXP_DIR/run.sh"; then
    DEST="${BASE_DIR}-run${i}"
    rm -rf "$DEST"
    cp -r "$RESULT_DIR" "$DEST"
    echo "[OK] Run $i raw results preserved at: $DEST"
  else
    echo "[FAIL] Run $i failed -- see output above. Continuing with next run."
  fi
done

echo ""
echo "=== Aggregating $RUNS runs (mean / std + multi-run figure) ==="
python3 "$PROJECT_ROOT/analysis/mqtt-multi/aggregate_mqtt_runs.py" --base "$BASE_DIR"
echo "Done. Output: results/processed/mqtt/experiment-b-multi/"
