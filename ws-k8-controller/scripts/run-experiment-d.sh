#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#
#  Experiment-D: HPA with Custom Connection-Count Metric (Baseline)
#
#  Tests whether standard HPA, given active_connections as a custom metric via
#  the Prometheus Adapter, can match the StatefulAutoscaler's performance.
#
#  Hypothesis: HPA with custom metrics will scale replicas correctly but will
#  still scale down during the 90-second dropout gap (if the stabilization
#  window does not hold metric-derived recommendations high), demonstrating
#  that metric selection alone is insufficient without connection-lifecycle-
#  aware scale-down policy.
#
#  Load pattern (same as Experiment C — 2-Cycle Restorm Simulation):
#    CYCLE 1    -> 800 clients ramp up                             (150s)
#    DROP 1     -> All clients deleted (90s gap)                   (90s)
#    CYCLE 2    -> 800 fresh clients, expect possible storm        (150s)
#    FINAL DROP -> Permanent disconnect                            (180s)
#
#  Key difference from C: autoscaler is HPA+Prometheus Adapter,
#  NOT StatefulAutoscaler CRD.
#
# ==============================================================================

cleanup() {
  echo ""
  echo "[CLEANUP] Stopping background processes..."
  kill ${REPLICA_PID:-}      2>/dev/null || true
  kill ${PROM_COLLECT_PID:-} 2>/dev/null || true
  kill ${POD_PID:-}          2>/dev/null || true
  kill ${PROM_PID:-}         2>/dev/null || true
  # Wait only for the PIDs we started — never bare 'wait' (hangs on port-forward etc.)
  for _pid in "${REPLICA_PID:-}" "${PROM_COLLECT_PID:-}" "${POD_PID:-}" "${PROM_PID:-}"; do
    [ -n "$_pid" ] && wait "$_pid" 2>/dev/null || true
  done
  echo "[CLEANUP] Done."
}
trap cleanup EXIT

# ----------------------------------------------------------
# Paths
# ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="exp-d-hpa-custom"
EXPERIMENT_NAME="experiment-d-hpa-custom-metric"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/$EXPERIMENT_NAME"
EXP_DIR="$PROJECT_ROOT/experiments/websocket/$EXPERIMENT_NAME"

# ----------------------------------------------------------
# Timing Parameters (same as Experiment C for comparability)
# ----------------------------------------------------------
CYCLE1_DURATION=150
DROP1_DURATION=90
CYCLE2_DURATION=150
FINAL_DROP_DURATION=180
SCRAPE_INTERVAL=5

section() {
  echo ""
  echo "============================================================"
  echo "  $1"
  echo "============================================================"
}

log() {
  echo "[$(date '+%H:%M:%S')] $1"
}

# ==============================================================
#  1. CLEAN PREVIOUS RESULTS
# ==============================================================
section "1. Cleaning Previous Results"

if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
  echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
else
  rm -rf "$RESULT_DIR"
  mkdir -p "$RESULT_DIR"
fi

# ==============================================================
#  2. CREATE FRESH KIND CLUSTER
# ==============================================================
section "2. Creating Fresh Kind Cluster"

kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"
log "Kind cluster '$CLUSTER_NAME' created."

# ==============================================================
#  3. INSTALL METRICS SERVER
# ==============================================================
section "3. Installing Metrics Server"

kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
    "--cert-dir=/tmp",
    "--secure-port=10250",
    "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
    "--kubelet-use-node-status-port",
    "--metric-resolution=15s",
    "--kubelet-insecure-tls"
  ]}
]'
kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
log "Metrics server ready."

# ==============================================================
#  4. DEPLOY PROMETHEUS
# ==============================================================
section "4. Deploying Prometheus"

kubectl apply -f monitoring/prometheus/namespace.yaml
kubectl apply -f monitoring/prometheus/rbac.yaml
kubectl apply -f monitoring/prometheus/configmap.yaml
kubectl apply -f monitoring/prometheus/deployment.yaml
kubectl apply -f monitoring/prometheus/service.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
log "Prometheus ready."

# ==============================================================
#  5. DEPLOY PROMETHEUS ADAPTER (custom metrics)
# ==============================================================
section "5. Deploying Prometheus Adapter"

# Deploy Prometheus Adapter base manifest (ServiceAccount, RBAC, APIService, Deployment)
# Delete any existing deployment first to avoid immutable selector errors
kubectl delete deployment prometheus-adapter -n monitoring --ignore-not-found=true
kubectl apply -f "$PROJECT_ROOT/base-paper-implementation/pcm-exp/manifests/prometheus-adapter.yaml"

# NOW apply the experiment-D configmap AFTER the base manifest so it overwrites the
# default http_requests rules with the active_connections_per_pod rule.
# The configmap name (prometheus-adapter-config) matches the volume mount in the Deployment.
kubectl apply -f "$EXP_DIR/manifests/prometheus-adapter-configmap.yaml"

# Restart the adapter so it picks up the new configmap immediately
kubectl rollout restart deployment/prometheus-adapter -n monitoring
log "Waiting for prometheus-adapter deployment to roll out..."
kubectl -n monitoring rollout status deployment/prometheus-adapter --timeout=180s || {
  log "WARNING: prometheus-adapter rollout timed out. Check pod logs with:"
  log "  kubectl logs -n monitoring -l app=prometheus-adapter"
}

log "Waiting for custom metrics API to become available..."
MAX_WAIT=120
WAITED=0
while ! kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1" >/dev/null 2>&1; do
  sleep 5
  WAITED=$((WAITED+5))
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    log "WARNING: Custom metrics API not ready after ${MAX_WAIT}s. HPA may not react correctly."
    break
  fi
done
log "Custom metrics API ready (${WAITED}s)."

# ==============================================================
#  6. BUILD AND LOAD DOCKER IMAGES
# ==============================================================
section "6. Building Docker Images"

log "Building websocket-server-instrumented image..."
cd "$PROJECT_ROOT/workloads/websocket/app-instrumented"
docker build -t websocket-server-instrumented:latest .
kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"
log "Image loaded."

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"
log "Load generator image loaded."

cd "$PROJECT_ROOT"

# ==============================================================
#  7. DEPLOY WEBSOCKET SERVER
# ==============================================================
section "7. Deploying WebSocket Server"

kubectl apply -f workloads/websocket/k8s/deployment-experiment-c.yml
kubectl apply -f workloads/websocket/k8s/service.yml
kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s
log "WebSocket server ready."

# Now wait for Prometheus to scrape the pods and the adapter to relist the metric.
# This MUST happen after the websocket-server pods are Running (they are the metric source).
log "Waiting for active_connections_per_pod metric to appear in custom metrics API..."
MAX_WAIT=120
WAITED=0
while ! kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/default/pods/*/active_connections_per_pod" >/dev/null 2>&1; do
  sleep 5
  WAITED=$((WAITED+5))
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    log "WARNING: active_connections_per_pod not visible after ${MAX_WAIT}s."
    log "  Adapter logs: kubectl logs -n monitoring deployment/prometheus-adapter"
    log "  Prometheus check: curl on port-forward :9090/api/v1/label/__name__/values | grep active"
    break
  fi
done
log "active_connections_per_pod metric confirmed in API (${WAITED}s)."

# ==============================================================
#  8. DEPLOY HPA WITH CUSTOM METRIC
# ==============================================================
section "8. Deploying HPA (custom connection-count metric)"

kubectl apply -f "$EXP_DIR/manifests/hpa-custom-metric.yaml"
log "HPA with active_connections_per_pod metric deployed."
log "Target: 100 connections/pod, min=2, max=15, scaleDown stabilization=120s"
kubectl get hpa websocket-hpa-custom-metric

# ==============================================================
#  9. START DATA COLLECTION
# ==============================================================
section "9. Starting Data Collection"

REPLICA_LOG="$RESULT_DIR/replicas.log"
PROM_LOG="$RESULT_DIR/prometheus.log"
POD_LOG="$RESULT_DIR/pods.log"

# Collect replica counts
(set +eu; set +o pipefail; while true; do
  TS=$(date +%s)
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.spec.replicas}' 2>/dev/null) || REPLICAS=0
  HPA_RAW=$(kubectl get hpa websocket-hpa-custom-metric \
    -o jsonpath='{.status.currentReplicas},{.status.desiredReplicas},{.status.currentMetrics[0].pods.current.averageValue}' \
    2>/dev/null) || HPA_RAW=,,
  echo "$TS,${REPLICAS:-0},${HPA_RAW:-,,}"
  sleep "$SCRAPE_INTERVAL"
done) >> "$REPLICA_LOG" &
REPLICA_PID=$!

# Collect Prometheus metrics
PROM_URL="http://localhost:9090"
kubectl -n monitoring port-forward svc/prometheus 9090:9090 &>/dev/null &
PROM_PID=$!
sleep 3
PROM_ERRORS="$RESULT_DIR/prometheus-collect-errors.log"

# Wait for Prometheus to become ready before starting collection (timeout 30s)
WAIT=0
until curl -s -f "$PROM_URL/-/ready" >/dev/null 2>&1 || [ "$WAIT" -ge 30 ]; do
  sleep 1
  WAIT=$((WAIT+1))
done

(set +eu; set +o pipefail; while true; do
  TS=$(date +%s)
  CONNS=$(curl --max-time 5 -s "$PROM_URL/api/v1/query?query=sum(active_connections)" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['result'][0]['value'][1] if d['data']['result'] else '0')" \
    2>>"$PROM_ERRORS") || CONNS=0
  RECONNS=$(curl --max-time 5 -s "$PROM_URL/api/v1/query?query=sum(rate(new_connections_total%5B15s%5D))" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['result'][0]['value'][1] if d['data']['result'] else '0')" \
    2>>"$PROM_ERRORS") || RECONNS=0
  echo "$TS,${CONNS:-0},${RECONNS:-0}"
  sleep "$SCRAPE_INTERVAL"
done) >> "$PROM_LOG" 2>>"$PROM_ERRORS" &
PROM_COLLECT_PID=$!

# ==============================================================
#  10. EXPERIMENT EXECUTION
# ==============================================================
section "10. Running Experiment D (2-Cycle Restorm)"

MAIN_START=$(date +%s)
log "Experiment started at $(date)"

# CYCLE 1
log "--- CYCLE 1: Ramping 800 clients (~120s job + ${CYCLE1_DURATION}s observation) ---"
kubectl delete job websocket-loadgen --ignore-not-found=true
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep "$CYCLE1_DURATION"

# DROP 1 (90-second gap)
log "--- DROP 1: Deleting load gen job (${DROP1_DURATION}s gap) ---"
log "KEY TEST: Will HPA's stabilization window hold 8 replicas through this ${DROP1_DURATION}s gap?"
kubectl delete job websocket-loadgen --ignore-not-found=true
DROP1_START=$(date +%s)
sleep "$DROP1_DURATION"
DROP1_END=$(date +%s)
log "DROP 1 complete. Elapsed: $((DROP1_END - DROP1_START))s"

# CYCLE 2
log "--- CYCLE 2: 800 fresh clients (~120s job + ${CYCLE2_DURATION}s observation) ---"
log "Observing: reconnection storm if replicas dropped, instant connect if pods were held warm"
kubectl delete job websocket-loadgen --ignore-not-found=true
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep "$CYCLE2_DURATION"

# FINAL DROP
log "--- FINAL DROP: Permanent disconnect (${FINAL_DROP_DURATION}s) ---"
kubectl delete job websocket-loadgen --ignore-not-found=true
sleep "$FINAL_DROP_DURATION"

MAIN_END=$(date +%s)
log "Experiment complete. Total: $((MAIN_END - MAIN_START))s"

# ==============================================================
#  11. COLLECT FINAL STATE
# ==============================================================
section "11. Final State Collection"

kubectl get hpa websocket-hpa-custom-metric -o yaml > "$RESULT_DIR/hpa-final.yaml"
kubectl get deployment websocket-server -o yaml > "$RESULT_DIR/deployment-final.yaml"
kubectl get events --sort-by=.metadata.creationTimestamp > "$RESULT_DIR/events.log"
log "Final state collected."

# ==============================================================
#  12. SUMMARY
# ==============================================================
section "12. Results Summary"

echo ""
echo "Result files in: $RESULT_DIR"
echo "  replicas.log    - Replica count over time (with HPA desired vs current)"
echo "  prometheus.log  - active_connections and reconnection rate over time"
echo "  hpa-final.yaml  - Final HPA status"
echo "  events.log      - Kubernetes events (scale decisions)"
echo ""
echo "Next: run analysis/parse_logs_experiment_d.py to generate plots"
echo "Compare with Experiment C (StatefulAutoscaler) results."

# ==============================================================
#  13. PROCESS RESULTS (automatically run)
# ==============================================================
section "13. Processing Results"

log "Running parser: analysis/experiment-d/parse_logs_experiment_d.py"
if ! python3 analysis/experiment-d/parse_logs_experiment_d.py >> "$RESULT_DIR/parse_logs.out" 2>&1; then
  log "ERROR: Parsing failed. See $RESULT_DIR/parse_logs.out and $RESULT_DIR/prometheus-collect-errors.log"
else
  log "Parsing completed successfully."
fi

log "Running plotter: analysis/experiment-d/plot_experiment_d.py"
if ! python3 analysis/experiment-d/plot_experiment_d.py >> "$RESULT_DIR/plot_experiment.out" 2>&1; then
  log "ERROR: Plotting failed. See $RESULT_DIR/plot_experiment.out"
else
  log "Plotting completed successfully. Plots in $RESULT_DIR/plots"
fi
