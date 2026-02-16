#!/usr/bin/env bash
# run-experiment.sh — Main orchestrator for KRM HPA experiment
# Loops over metric-resolution values, patches metrics-server, deploys workload,
# starts collectors, runs load phases, stops collectors, and archives data.
#
# Usage: ./run-experiment.sh [resolutions...]
# Default: ./run-experiment.sh 60s 30s 15s

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTS="$SCRIPT_DIR/manifests"
COLLECTORS="$SCRIPT_DIR/collect-scripts"
WORKLOAD="$SCRIPT_DIR/workload"
RESULTS_BASE="$SCRIPT_DIR/results"

RESOLUTIONS=("${@:-60s 30s 15s}")
if [ $# -eq 0 ]; then
    RESOLUTIONS=(60s 30s 15s)
fi

NUM_CYCLES=5
HIGH_SECS=100
LOW_SECS=100

# --- Helper functions --------------------------------------------------------

wait_for_metrics_server() {
    echo "[*] Waiting for metrics-server rollout..."
    kubectl -n kube-system rollout status deployment/metrics-server --timeout=120s
    echo "[*] Waiting for metrics to become available (up to 90s)..."
    for i in $(seq 1 18); do
        if kubectl top nodes &>/dev/null; then
            echo "[✓] kubectl top nodes works"
            return 0
        fi
        echo "    ...not ready yet (attempt $i/18)"
        sleep 5
    done
    echo "[!] WARNING: kubectl top nodes still not responding after 90s"
    return 1
}

start_collectors() {
    local outdir="$1"
    echo "[*] Starting collectors → $outdir"
    bash "$COLLECTORS/collect_pod_cpu.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    bash "$COLLECTORS/collect_hpa_status.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    bash "$COLLECTORS/collect_pod_count.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    bash "$COLLECTORS/collect_metrics_server_logs.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    echo "[✓] ${#COLLECTOR_PIDS[@]} collectors running (PIDs: ${COLLECTOR_PIDS[*]})"
}

stop_collectors() {
    echo "[*] Stopping collectors..."
    for pid in "${COLLECTOR_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    done
    COLLECTOR_PIDS=()
    echo "[✓] All collectors stopped"
}

# --- Main loop ---------------------------------------------------------------

echo "============================================================"
echo " KRM HPA Experiment"
echo " Resolutions: ${RESOLUTIONS[*]}"
echo " Cycles: $NUM_CYCLES × ${HIGH_SECS}s high / ${LOW_SECS}s low"
echo "============================================================"
echo ""

for resolution in "${RESOLUTIONS[@]}"; do
    echo ""
    echo "============================================================"
    echo " Experiment run: metric-resolution=$resolution"
    echo "============================================================"
    RUN_DIR="$RESULTS_BASE/$resolution"
    mkdir -p "$RUN_DIR"
    COLLECTOR_PIDS=()

    # 1. Patch metrics-server with this resolution and wait for readiness
    bash "$MANIFESTS/metrics-server-patch.sh" "$resolution"
    wait_for_metrics_server

    # 3. Deploy workload + HPA
    echo "[*] Deploying cpu-app and HPA..."
    kubectl apply -f "$MANIFESTS/cpu-app.yaml"
    kubectl apply -f "$MANIFESTS/hpa-krm.yaml"
    echo "[*] Waiting for pods to be ready..."
    kubectl wait --for=condition=ready pod -l app=cpu-app --timeout=120s

    # 4. Start collectors
    start_collectors "$RUN_DIR"

    # 5. Run workload pattern
    echo "[*] Running workload pattern..."
    bash "$WORKLOAD/run-load-phase.sh" "$RUN_DIR" "$NUM_CYCLES" "$HIGH_SECS" "$LOW_SECS"

    # 6. Stop collectors
    stop_collectors

    # 7. Snapshot final state
    echo "[*] Capturing final state..."
    kubectl get hpa cpu-hpa -o yaml > "$RUN_DIR/hpa_final.yaml" 2>/dev/null || true
    kubectl describe hpa cpu-hpa > "$RUN_DIR/hpa_describe.txt" 2>/dev/null || true
    kubectl get events --sort-by='.lastTimestamp' > "$RUN_DIR/final_events.log" 2>/dev/null || true

    # 8. Cleanup workload for next run
    echo "[*] Cleaning up workload..."
    kubectl delete -f "$MANIFESTS/hpa-krm.yaml" --ignore-not-found
    kubectl delete -f "$MANIFESTS/cpu-app.yaml" --ignore-not-found
    kubectl wait --for=delete pod -l app=cpu-app --timeout=60s 2>/dev/null || true

    echo "[✓] Run complete for $resolution → $RUN_DIR"
    echo ""
done

echo "============================================================"
echo " All experiment runs complete!"
echo " Results in: $RESULTS_BASE/"
echo "============================================================"

# Archive results
ARCHIVE_NAME="krm_experiment_results_$(date +%Y%m%d%H%M%S).tgz"
tar -czf "$SCRIPT_DIR/$ARCHIVE_NAME" -C "$RESULTS_BASE" .
echo "[✓] Archived to $SCRIPT_DIR/$ARCHIVE_NAME"
