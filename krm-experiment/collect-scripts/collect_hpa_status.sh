#!/usr/bin/env bash
# collect_hpa_status.sh — Poll HPA status every 5s, append to CSV
# Usage: ./collect_hpa_status.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/hpa_log.csv"
mkdir -p "$OUTDIR"

# Write CSV header if file doesn't exist
if [ ! -f "$OUTFILE" ]; then
    echo "timestamp,currentReplicas,desiredReplicas,currentCPUUtilizationPercent" > "$OUTFILE"
fi

echo "[collector] hpa_status → $OUTFILE (every 5s)"
while true; do
    ts=$(date --iso-8601=seconds)
    current=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentReplicas}' 2>/dev/null || echo "")
    desired=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.desiredReplicas}' 2>/dev/null || echo "")
    cpuutil=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentMetrics[*].resource.current.averageUtilization}' 2>/dev/null || echo "")
    echo "$ts,$current,$desired,$cpuutil" >> "$OUTFILE"
    sleep 5
done
