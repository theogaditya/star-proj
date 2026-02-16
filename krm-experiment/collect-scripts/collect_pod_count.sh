#!/usr/bin/env bash
# collect_pod_count.sh — Poll pod count & events every 5s
# Usage: ./collect_pod_count.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
PODCOUNT_FILE="$OUTDIR/podcount.csv"
EVENTS_FILE="$OUTDIR/events_snapshot.log"
mkdir -p "$OUTDIR"

# Write CSV header if file doesn't exist
if [ ! -f "$PODCOUNT_FILE" ]; then
    echo "timestamp,pod_count" > "$PODCOUNT_FILE"
fi

echo "[collector] pod_count → $PODCOUNT_FILE (every 5s)"
while true; do
    ts=$(date --iso-8601=seconds)
    count=$(kubectl get pods -l app=cpu-app --no-headers 2>/dev/null | wc -l)
    echo "$ts,$count" >> "$PODCOUNT_FILE"
    kubectl get events --sort-by='.lastTimestamp' 2>/dev/null | tail -n 50 > "$EVENTS_FILE"
    sleep 5
done
