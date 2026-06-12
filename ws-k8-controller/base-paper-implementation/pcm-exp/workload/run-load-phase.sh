#!/usr/bin/env bash
# run-load-phase.sh — HTTP-based workload generator for PCM experiments
# Uses 'hey' to send real HTTP requests to the cpu-app service.
# This generates both CPU load AND http_requests_total Prometheus metrics.
#
# Usage: ./run-load-phase.sh <output_dir> [num_cycles] [high_secs] [low_secs]
#
# Paper params: 100s high (~1800 req/s), 100s low (~600 req/s), 300s total

set -euo pipefail

OUTDIR="${1:-.}"
NUM_CYCLES="${2:-5}"
HIGH_SECS="${3:-100}"
LOW_SECS="${4:-100}"
PHASES_LOG="$OUTDIR/phases.log"

# Target URL — use the Kind node's address + NodePort
# Get the first worker node IP from Kind
NODE_IP="${NODE_IP:-$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')}"
APP_URL="${APP_URL:-http://${NODE_IP}:30080/}"

# Load rates (requests per second)
HIGH_RPS="${HIGH_RPS:-200}"
LOW_RPS="${LOW_RPS:-2}"

# Concurrency
HIGH_CONCURRENCY="${HIGH_CONCURRENCY:-50}"
LOW_CONCURRENCY="${LOW_CONCURRENCY:-2}"

mkdir -p "$OUTDIR"

# Check for hey
if ! command -v hey &>/dev/null; then
    echo "[!] 'hey' not found. Install with: go install github.com/rakyll/hey@latest"
    echo "    or: apt-get install hey / brew install hey"
    exit 1
fi

echo "timestamp,phase,action" > "$PHASES_LOG"

echo "=== PCM Workload Pattern ==="
echo "  Target: $APP_URL"
echo "  Cycles: $NUM_CYCLES × ${HIGH_SECS}s high (${HIGH_RPS} rps) / ${LOW_SECS}s low (${LOW_RPS} rps)"
echo ""

for i in $(seq 1 "$NUM_CYCLES"); do
    echo "--- Cycle $i/$NUM_CYCLES ---"

    # HIGH phase
    ts_high_start=$(date --iso-8601=seconds)
    echo "$ts_high_start,high,start" >> "$PHASES_LOG"
    echo "[$ts_high_start] HIGH phase start (cycle $i) — ${HIGH_RPS} rps"

    hey -z "${HIGH_SECS}s" -q "$HIGH_RPS" -c "$HIGH_CONCURRENCY" \
        "$APP_URL" > /dev/null 2>&1 &
    HEY_PID=$!

    sleep "$HIGH_SECS"
    kill "$HEY_PID" 2>/dev/null || true
    wait "$HEY_PID" 2>/dev/null || true

    ts_high_end=$(date --iso-8601=seconds)
    echo "$ts_high_end,high,end" >> "$PHASES_LOG"
    echo "[$ts_high_end] HIGH phase end (cycle $i)"

    # LOW phase
    ts_low_start=$(date --iso-8601=seconds)
    echo "$ts_low_start,low,start" >> "$PHASES_LOG"
    echo "[$ts_low_start] LOW phase start (cycle $i) — ${LOW_RPS} rps"

    hey -z "${LOW_SECS}s" -q "$LOW_RPS" -c "$LOW_CONCURRENCY" \
        "$APP_URL" > /dev/null 2>&1 &
    HEY_PID=$!

    sleep "$LOW_SECS"
    kill "$HEY_PID" 2>/dev/null || true
    wait "$HEY_PID" 2>/dev/null || true

    ts_low_end=$(date --iso-8601=seconds)
    echo "$ts_low_end,low,end" >> "$PHASES_LOG"
    echo "[$ts_low_end] LOW phase end (cycle $i)"
done

echo "=== Workload pattern complete ==="
