#!/usr/bin/env bash
# collect_hpa_status.sh — Poll HPA status every 5s (PCM-aware: captures custom metrics)
# Usage: ./collect_hpa_status.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/hpa_log.csv"
mkdir -p "$OUTDIR"

if [ ! -f "$OUTFILE" ]; then
    echo "timestamp,currentReplicas,desiredReplicas,currentCPUUtilizationPercent,httpRequestsPerSecond" > "$OUTFILE"
fi

echo "[collector] hpa_status → $OUTFILE (every 5s)"
while true; do
    ts=$(date --iso-8601=seconds)
    current=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentReplicas}' 2>/dev/null || echo "")
    desired=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.desiredReplicas}' 2>/dev/null || echo "")

    # CPU utilization (Resource metric — may not exist in PCM-H scenario)
    cpuutil=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentMetrics[?(@.resource.name=="cpu")].resource.current.averageUtilization}' 2>/dev/null || echo "")

    # HTTP requests per second (Pods custom metric — may not exist in PCM-CPU-only)
    httprps=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentMetrics[?(@.pods.metric.name=="http_requests_per_second")].pods.current.averageValue}' 2>/dev/null || echo "")

    echo "$ts,$current,$desired,$cpuutil,$httprps" >> "$OUTFILE"
    sleep 5
done
