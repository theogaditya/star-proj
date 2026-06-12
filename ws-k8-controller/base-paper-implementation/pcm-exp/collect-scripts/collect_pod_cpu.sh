#!/usr/bin/env bash
# collect_pod_cpu.sh — Poll pod CPU & memory every 5s, append to CSV
# Usage: ./collect_pod_cpu.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/pod_cpu.csv"
mkdir -p "$OUTDIR"

if [ ! -f "$OUTFILE" ]; then
    echo "timestamp,pod,cpu,memory" > "$OUTFILE"
fi

# Wait for metrics-server to be ready before polling
echo "[collector] pod_cpu: waiting for kubectl top to become available..."
for attempt in $(seq 1 30); do
    if kubectl top pods -l app=cpu-app --no-headers 2>/dev/null | head -1 | grep -q .; then
        echo "[collector] pod_cpu: kubectl top is ready (attempt $attempt)"
        break
    fi
    if [ "$attempt" -eq 30 ]; then
        echo "[collector] pod_cpu: WARNING — kubectl top still not ready after 150s, starting anyway"
    fi
    sleep 5
done

echo "[collector] pod_cpu → $OUTFILE (every 5s)"
while true; do
    ts=$(date --iso-8601=seconds)
    # Capture output; skip if kubectl top fails
    if output=$(kubectl top pods -l app=cpu-app --no-headers 2>/dev/null); then
        echo "$output" | while read -r pod cpu mem; do
            [ -n "$pod" ] && echo "$ts,$pod,$cpu,$mem" >> "$OUTFILE"
        done
    fi
    sleep 5
done
