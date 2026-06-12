#!/usr/bin/env bash
# run-load-phase.sh — Runs N cycles of high/low CPU load, logs phase timestamps
# Usage: ./run-load-phase.sh <output_dir> [num_cycles] [high_secs] [low_secs]

set -euo pipefail

OUTDIR="${1:-.}"
NUM_CYCLES="${2:-5}"
HIGH_SECS="${3:-100}"
LOW_SECS="${4:-100}"
PHASES_LOG="$OUTDIR/phases.log"

mkdir -p "$OUTDIR"

# Write header
echo "timestamp,phase,action" > "$PHASES_LOG"

echo "=== Workload pattern: $NUM_CYCLES cycles, ${HIGH_SECS}s high / ${LOW_SECS}s low ==="

for i in $(seq 1 "$NUM_CYCLES"); do
    echo "--- Cycle $i/$NUM_CYCLES ---"

    # HIGH phase: exec busy-loop into ALL running cpu-app pods for parallel load
    ts_high_start=$(date --iso-8601=seconds)
    echo "$ts_high_start,high,start" >> "$PHASES_LOG"
    echo "[$ts_high_start] HIGH phase start (cycle $i)"

    # Get all pod names
    PODS=$(kubectl get pod -l app=cpu-app -o jsonpath='{.items[*].metadata.name}')
    LOAD_PIDS=()
    for pod in $PODS; do
        kubectl exec "$pod" -- /bin/sh -c "timeout $HIGH_SECS sh -c 'while :; do :; done'" &
        LOAD_PIDS+=($!)
    done

    # Wait for high phase duration
    sleep "$HIGH_SECS"

    # Kill any stragglers
    for pid in "${LOAD_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done

    ts_high_end=$(date --iso-8601=seconds)
    echo "$ts_high_end,high,end" >> "$PHASES_LOG"
    echo "[$ts_high_end] HIGH phase end (cycle $i)"

    # LOW phase: just wait
    ts_low_start=$(date --iso-8601=seconds)
    echo "$ts_low_start,low,start" >> "$PHASES_LOG"
    echo "[$ts_low_start] LOW phase start (cycle $i)"

    sleep "$LOW_SECS"

    ts_low_end=$(date --iso-8601=seconds)
    echo "$ts_low_end,low,end" >> "$PHASES_LOG"
    echo "[$ts_low_end] LOW phase end (cycle $i)"
done

echo "=== Workload pattern complete ==="
