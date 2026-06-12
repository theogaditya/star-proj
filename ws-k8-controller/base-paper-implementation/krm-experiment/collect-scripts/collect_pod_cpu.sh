#!/usr/bin/env bash
# collect_pod_cpu.sh — Poll pod CPU & memory every 5s, append to CSV
# Usage: ./collect_pod_cpu.sh <output_dir>

set -euo pipefail
OUTDIR="${1:-.}"
OUTFILE="$OUTDIR/pod_cpu.csv"
mkdir -p "$OUTDIR"

# Write CSV header if file doesn't exist
if [ ! -f "$OUTFILE" ]; then
    echo "timestamp,pod,cpu,memory" > "$OUTFILE"
fi

echo "[collector] pod_cpu → $OUTFILE (every 5s)"
while true; do
    ts=$(date --iso-8601=seconds)
    # kubectl top pods output has variable whitespace; awk handles it natively
    kubectl top pods -l app=cpu-app --no-headers 2>/dev/null | while read -r pod cpu mem; do
        echo "$ts,$pod,$cpu,$mem" >> "$OUTFILE"
    done
    sleep 5
done
