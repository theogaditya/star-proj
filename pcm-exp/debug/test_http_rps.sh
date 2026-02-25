#!/usr/bin/env bash
# test_http_rps.sh — Debug script to verify HTTP RPS collection works
# It deploys the app, port-forwards Prometheus, starts the collector,
# sends a short burst of load, and prints the resulting CSV.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$SCRIPT_DIR/.."
OUT_DIR="$SCRIPT_DIR/out"

echo "=== Debug: Testing HTTP RPS Collection ==="
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

echo "[1] Deploying cpu-app..."
kubectl apply -f "$BASE_DIR/manifests/cpu-app.yaml"
kubectl wait --for=condition=ready pod -l app=cpu-app --timeout=60s

echo "[2] Starting Prometheus port-forward..."
kubectl -n monitoring port-forward svc/prometheus 9090:9090 &>/dev/null &
PF_PID=$!
sleep 3 # Give it time to bind

echo "[3] Starting collect_http_rps.sh..."
# Run it a bit faster (every 2s) for debugging
PROM_URL="http://localhost:9090" COLLECT_INTERVAL=2 bash "$BASE_DIR/collect-scripts/collect_http_rps.sh" "$OUT_DIR" &
COLLECTOR_PID=$!

echo "[4] Generating load (1 cycle: 20s HIGH, 10s LOW)..."
bash "$BASE_DIR/workload/run-load-phase.sh" "$OUT_DIR" 1 20 10

echo "[5] Cleaning up..."
kill $COLLECTOR_PID 2>/dev/null || true
kill $PF_PID 2>/dev/null || true

echo "=== Collection Results ($OUT_DIR/http_rps.csv) ==="
cat "$OUT_DIR/http_rps.csv"

echo "=== Done ==="
echo "If you see non-empty total_rps values above, the metric collection is working!"
