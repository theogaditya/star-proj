#!/usr/bin/env bash
# collect_prometheus_logs.sh — Stream Prometheus logs continuously
# Usage: ./collect_prometheus_logs.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/prometheus.log"
mkdir -p "$OUTDIR"

echo "[collector] prometheus logs → $OUTFILE (continuous)"
kubectl -n monitoring logs deployment/prometheus --follow >> "$OUTFILE" 2>&1
