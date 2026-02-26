#!/usr/bin/env bash
# run-experiment.sh — Main orchestrator for PCM HPA experiments
# Runs 3 experiment scenarios from the paper:
#   1. PCM-CPU:  HPA with Prometheus custom metric (HTTP request rate = CPU proxy)
#   2. PCM-H:    HPA with only HTTP request rate
#   3. PCM-CH:   HPA with both CPU (Resource) + HTTP request rate (Custom)
#
# For PCM-CPU, also varies scraping periods: 60s, 30s, 15s
#
# Usage: ./run-experiment.sh [scenarios...]
# Default: ./run-experiment.sh pcm-cpu pcm-h pcm-ch

set -euo pipefail

# Add hey to PATH
export PATH=$PATH:/home/abhas/.local/share/mise/installs/go/1.25.5/bin

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTS="$SCRIPT_DIR/manifests"
COLLECTORS="$SCRIPT_DIR/collect-scripts"
WORKLOAD="$SCRIPT_DIR/workload"
RESULTS_BASE="$SCRIPT_DIR/results"

SCENARIOS=("${@:-pcm-cpu pcm-h pcm-ch}")
if [ $# -eq 0 ]; then
    SCENARIOS=(pcm-cpu pcm-h pcm-ch)
fi

NUM_CYCLES=5
HIGH_SECS=100
LOW_SECS=100

# Scraping periods to test for PCM-CPU scenario
PCM_CPU_SCRAPE_INTERVALS=(60s 30s 15s)

# --- Helper functions --------------------------------------------------------

setup_port_forward() {
    echo "[*] Setting up Prometheus port-forward..."
    kubectl -n monitoring port-forward svc/prometheus 9090:9090 &>/dev/null &
    PF_PID=$!
    sleep 3
    echo "[✓] Port-forward active (PID: $PF_PID)"
}

teardown_port_forward() {
    if [ -n "${PF_PID:-}" ]; then
        kill "$PF_PID" 2>/dev/null || true
        wait "$PF_PID" 2>/dev/null || true
        echo "[✓] Port-forward stopped"
    fi
}

start_collectors() {
    local outdir="$1"
    echo "[*] Starting collectors → $outdir"
    COLLECTOR_PIDS=()
    bash "$COLLECTORS/collect_pod_cpu.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    bash "$COLLECTORS/collect_hpa_status.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    bash "$COLLECTORS/collect_pod_count.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)
    bash "$COLLECTORS/collect_prometheus_logs.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)

    # Start prometheus metrics collector (needs port-forward)
    PROM_URL="http://localhost:9090" bash "$COLLECTORS/collect_prometheus_metrics.sh" "$outdir" &
    COLLECTOR_PIDS+=($!)

    # Start dedicated HTTP req/s collector (needs port-forward)
    PROM_URL="http://localhost:9090" bash "$COLLECTORS/collect_http_rps.sh" "$outdir" &
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

deploy_app() {
    echo "[*] Deploying cpu-app and service..."
    kubectl apply -f "$MANIFESTS/cpu-app.yaml"
    echo "[*] Waiting for pods to be ready..."
    kubectl wait --for=condition=ready pod -l app=cpu-app --timeout=120s
    echo "[✓] cpu-app deployed and ready"
}

cleanup_app() {
    echo "[*] Cleaning up workload..."
    kubectl delete hpa cpu-hpa --ignore-not-found 2>/dev/null || true
    kubectl delete -f "$MANIFESTS/cpu-app.yaml" --ignore-not-found 2>/dev/null || true
    kubectl wait --for=delete pod -l app=cpu-app --timeout=60s 2>/dev/null || true
    echo "[✓] Workload cleaned up"
}

snapshot_state() {
    local outdir="$1"
    echo "[*] Capturing final state..."
    kubectl get hpa cpu-hpa -o yaml > "$outdir/hpa_final.yaml" 2>/dev/null || true
    kubectl describe hpa cpu-hpa > "$outdir/hpa_describe.txt" 2>/dev/null || true
    kubectl get events --sort-by='.lastTimestamp' > "$outdir/final_events.log" 2>/dev/null || true
}

wait_for_custom_metrics() {
    echo "[*] Waiting for custom metrics to become available (up to 120s)..."
    for i in $(seq 1 24); do
        if kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/default/pods/*/http_requests_per_second" &>/dev/null; then
            echo "[✓] Custom metric http_requests_per_second is available"
            return 0
        fi
        echo "    ...not ready yet (attempt $i/24)"
        sleep 5
    done
    echo "[!] WARNING: Custom metrics still not available after 120s"
    return 1
}

run_single_experiment() {
    local scenario="$1"
    local hpa_file="$2"
    local run_dir="$3"
    local scrape_interval="${4:-60s}"

    mkdir -p "$run_dir"

    echo ""
    echo "------------------------------------------------------------"
    echo " Running: $scenario (scrape_interval=$scrape_interval)"
    echo " Output:  $run_dir"
    echo "------------------------------------------------------------"

    # Install / update Prometheus with the desired scrape interval
    bash "$MANIFESTS/install-prometheus.sh" "$scrape_interval"

    # Deploy app
    deploy_app

    # Wait for Prometheus to discover pods and for custom metrics to appear
    echo "[*] Waiting for Prometheus to discover pods (30s warm-up)..."
    sleep 30
    wait_for_custom_metrics || true

    # Apply HPA
    echo "[*] Applying HPA: $hpa_file"
    kubectl apply -f "$hpa_file"
    kubectl get hpa cpu-hpa -o wide
    sleep 10

    # Setup port-forward for Prometheus metrics collector
    setup_port_forward

    # Start collectors
    COLLECTOR_PIDS=()
    start_collectors "$run_dir"

    # Run workload
    echo "[*] Running workload pattern..."
    bash "$WORKLOAD/run-load-phase.sh" "$run_dir" "$NUM_CYCLES" "$HIGH_SECS" "$LOW_SECS"

    # Stop collectors
    stop_collectors

    # Teardown port-forward
    teardown_port_forward

    # Snapshot
    snapshot_state "$run_dir"

    # Cleanup
    cleanup_app

    echo "[✓] Experiment complete: $scenario → $run_dir"
}

# --- Main loop ---------------------------------------------------------------

echo "============================================================"
echo " PCM HPA Experiment"
echo " Scenarios: ${SCENARIOS[*]}"
echo " Cycles: $NUM_CYCLES × ${HIGH_SECS}s high / ${LOW_SECS}s low"
echo "============================================================"
echo ""

for scenario in "${SCENARIOS[@]}"; do
    case "$scenario" in
        pcm-cpu)
            # Run with different scraping periods (like the paper's Section 5.2.3/5.2.4)
            for interval in "${PCM_CPU_SCRAPE_INTERVALS[@]}"; do
                run_single_experiment \
                    "pcm-cpu-${interval}" \
                    "$MANIFESTS/hpa-pcm-cpu.yaml" \
                    "$RESULTS_BASE/pcm-cpu/$interval" \
                    "$interval"
            done
            ;;
        pcm-h)
            # PCM-H: HTTP request rate only (paper Section 5.2.6)
            run_single_experiment \
                "pcm-h" \
                "$MANIFESTS/hpa-pcm-http.yaml" \
                "$RESULTS_BASE/pcm-h" \
                "60s"
            ;;
        pcm-ch)
            # PCM-CH: CPU + HTTP combined (paper Section 5.2.6)
            run_single_experiment \
                "pcm-ch" \
                "$MANIFESTS/hpa-pcm-cpu-http.yaml" \
                "$RESULTS_BASE/pcm-ch" \
                "60s"
            ;;
        *)
            echo "[!] Unknown scenario: $scenario (expected: pcm-cpu, pcm-h, pcm-ch)"
            ;;
    esac
done

echo ""
echo "============================================================"
echo " All PCM experiments complete!"
echo " Results in: $RESULTS_BASE/"
echo "============================================================"

# Run analysis
echo "[*] Running analysis and generating plots..."
if python3 "$SCRIPT_DIR/analysis/analysis.py" "$RESULTS_BASE"; then
    echo "[✓] Plots generated in $RESULTS_BASE/plots"
else
    echo "[!] Analysis failed"
fi

# Archive results
mkdir -p "$SCRIPT_DIR/tgz"
ARCHIVE_NAME="pcm_experiment_results_$(date +%Y%m%d%H%M%S).tgz"
tar -czf "$SCRIPT_DIR/tgz/$ARCHIVE_NAME" -C "$RESULTS_BASE" .
echo "[✓] Archived to $SCRIPT_DIR/tgz/$ARCHIVE_NAME"
