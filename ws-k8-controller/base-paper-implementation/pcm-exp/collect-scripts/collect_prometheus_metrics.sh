#!/usr/bin/env bash
# collect_prometheus_metrics.sh — Query Prometheus API every 5s for raw metrics
# Usage: ./collect_prometheus_metrics.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/prometheus_metrics.csv"
mkdir -p "$OUTDIR"

# Prometheus URL via port-forward or service
PROM_URL="${PROM_URL:-http://localhost:9090}"

if [ ! -f "$OUTFILE" ]; then
    echo "timestamp,metric,pod,value" > "$OUTFILE"
fi

echo "[collector] prometheus_metrics → $OUTFILE (every 5s)"
echo "  Prometheus URL: $PROM_URL"

while true; do
    ts=$(date --iso-8601=seconds)

    # Query http_requests_total per pod
    result=$(curl -s "${PROM_URL}/api/v1/query?query=http_requests_total" 2>/dev/null || echo '{"data":{"result":[]}}')
    echo "$result" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data.get('data',{}).get('result',[]):
        pod = r['metric'].get('kubernetes_pod_name','unknown')
        val = r['value'][1]
        print(f'$ts,http_requests_total,{pod},{val}')
except: pass
" >> "$OUTFILE" 2>/dev/null

    # Query rate of http_requests_total (per second, 30s window — responsive to load changes)
    result=$(curl -s "${PROM_URL}/api/v1/query?query=sum(rate(http_requests_total%5B30s%5D))%20by%20(kubernetes_pod_name)" 2>/dev/null || echo '{"data":{"result":[]}}')
    echo "$result" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data.get('data',{}).get('result',[]):
        pod = r['metric'].get('kubernetes_pod_name','unknown')
        val = r['value'][1]
        print(f'$ts,http_requests_rate,{pod},{val}')
except: pass
" >> "$OUTFILE" 2>/dev/null

    sleep 5
done
