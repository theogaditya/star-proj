#!/usr/bin/env bash
# collect_metrics_server_logs.sh — Stream metrics-server logs continuously
# Usage: ./collect_metrics_server_logs.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/metrics-server.log"
mkdir -p "$OUTDIR"

echo "[collector] metrics-server logs → $OUTFILE (continuous)"
kubectl -n kube-system logs deployment/metrics-server --follow >> "$OUTFILE" 2>&1
