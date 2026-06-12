#!/usr/bin/env bash
# collect_http_rps.sh — Query Prometheus every 5s for HTTP requests/second
# Writes a clean per-row CSV: timestamp, total_rps (sum across all pods)
#
# Uses irate(http_requests_total[2m]) which reacts quickly to changes
# (only uses the last two data points within the window).
#
# Usage: ./collect_http_rps.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/http_rps.csv"
mkdir -p "$OUTDIR"

PROM_URL="${PROM_URL:-http://localhost:9090}"
INTERVAL="${COLLECT_INTERVAL:-5}"

if [ ! -f "$OUTFILE" ]; then
    echo "timestamp,total_rps,per_pod_avg_rps" > "$OUTFILE"
fi

echo "[collector] http_rps → $OUTFILE (every ${INTERVAL}s)"
echo "  Prometheus URL: $PROM_URL"

while true; do
    ts=$(date --iso-8601=seconds)

    # sum of irate across all cpu-app pods → actual observed req/s cluster-wide
    total_rps=$(curl -s \
        "${PROM_URL}/api/v1/query?query=sum(irate(http_requests_total%5B2m%5D))" \
        2>/dev/null \
        | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    result = data.get('data', {}).get('result', [])
    if result:
        print(result[0]['value'][1])
    else:
        print('')
except:
    print('')
" 2>/dev/null || echo "")

    # per-pod average: avg(irate(...))
    per_pod_rps=$(curl -s \
        "${PROM_URL}/api/v1/query?query=avg(irate(http_requests_total%5B2m%5D))" \
        2>/dev/null \
        | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    result = data.get('data', {}).get('result', [])
    if result:
        print(result[0]['value'][1])
    else:
        print('')
except:
    print('')
" 2>/dev/null || echo "")

    echo "$ts,$total_rps,$per_pod_rps" >> "$OUTFILE"
    sleep "$INTERVAL"
done
