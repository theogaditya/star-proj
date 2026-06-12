#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#
#  Experiment-E: KEDA Baseline
#
#  Tests whether KEDA (Kubernetes Event-Driven Autoscaling) handles the
#  stateful WebSocket restorm scenario and how it compares to both:
#  - Experiment C (StatefulAutoscaler)
#  - Experiment D (HPA + custom metric)
#
#  KEDA uses a cooldownPeriod=120s (matching StatefulAutoscaler's
#  scaleDownCooldownSeconds=120) for a fair parametric comparison.
#
#  Expected finding: KEDA may hold pods through the 90-second DROP 1 gap
#  due to cooldownPeriod. The paper should report this honestly. Key
#  differentiators: no maxScaleDownStep, updates HPA minReplicas rather
#  than deployment directly, different convergence semantics.
#
#  Load pattern (same as Experiments C and D):
#    CYCLE 1    -> 800 clients ramp up                             (150s)
#    DROP 1     -> All clients deleted (90s gap)                   (90s)
#    CYCLE 2    -> 800 fresh clients                               (150s)
#    FINAL DROP -> Permanent disconnect                            (180s)
#
# ==============================================================================

cleanup() {
  echo ""
  echo "[CLEANUP] Stopping background processes..."
  # Kill any background collectors/port-forwards we started
  kill ${REPLICA_PID:-}      2>/dev/null || true
  kill ${PROM_COLLECT_PID:-} 2>/dev/null || true
  kill ${PROM_PID:-}         2>/dev/null || true
  kill ${KEDA_COLLECT_PID:-} 2>/dev/null || true
  # Give processes a moment to exit
  sleep 1

  echo "[CLEANUP] Removing KEDA resources (ScaledObject, managed HPA, operator)..."
  # Delete the ScaledObject we created
  kubectl delete scaledobject websocket-keda-scaler -n default --ignore-not-found=true || true
  # Attempt to delete any HPA created by KEDA for this ScaledObject
  kubectl delete hpa keda-hpa-websocket-keda-scaler -n default --ignore-not-found=true || true
  # Try to delete any HPAs that reference websocket-server (safe cleanup)
  for h in $(kubectl -n default get hpa -o name 2>/dev/null | grep websocket || true); do
    kubectl -n default delete "$h" --ignore-not-found=true || true
  done

  # Uninstall KEDA operator (Helm or manifest)
  if command -v helm >/dev/null 2>&1; then
    helm -n keda uninstall keda >/dev/null 2>&1 || true
    kubectl delete namespace keda --ignore-not-found=true || true
  else
    kubectl delete -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml" >/dev/null 2>&1 || true
    kubectl delete namespace keda --ignore-not-found=true || true
  fi

  echo "[CLEANUP] Removing Prometheus and monitoring stack..."
  # Delete monitoring namespace (prometheus deployment + service)
  kubectl delete namespace monitoring --ignore-not-found=true || true
  # Delete Metrics Server (best-effort)
  kubectl -n kube-system delete deployment metrics-server --ignore-not-found=true || true

  echo "[CLEANUP] Deleting Kubernetes artifacts created for the experiment..."
  # Delete websocket server deployment/service and loadgen job
  kubectl delete -f workloads/websocket/k8s/deployment-experiment-c.yml --ignore-not-found=true || true
  kubectl delete -f workloads/websocket/k8s/service.yml --ignore-not-found=true || true
  kubectl delete -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml" --ignore-not-found=true || true

  echo "[CLEANUP] Deleting kind cluster '$CLUSTER_NAME'..."
  kind delete cluster --name "$CLUSTER_NAME" || true

  echo "[CLEANUP] Finalizing local logfile handles and waiters..."
  # Wait only for the PIDs we started — avoid bare wait
  for _pid in "${REPLICA_PID:-}" "${PROM_COLLECT_PID:-}" "${PROM_PID:-}" "${KEDA_COLLECT_PID:-}"; do
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

CLUSTER_NAME="exp-e-keda"
EXPERIMENT_NAME="experiment-e-keda"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/$EXPERIMENT_NAME"
EXP_DIR="$PROJECT_ROOT/experiments/websocket/$EXPERIMENT_NAME"
KEDA_VERSION="2.13.0"

# ----------------------------------------------------------
# Timing Parameters (same as Experiment C/D for comparability)
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
#  5. INSTALL KEDA
# ==============================================================
section "5. Installing KEDA v${KEDA_VERSION}"

if command -v helm >/dev/null 2>&1; then
  log "Installing KEDA via Helm"
  helm repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
  helm repo update >/dev/null 2>&1 || true
  helm upgrade --install keda kedacore/keda --version ${KEDA_VERSION} --namespace keda --create-namespace
else
  log "Helm not found; applying KEDA release YAML (may fail on CRD annotations)."
  kubectl apply -f "https://github.com/kedacore/keda/releases/download/v${KEDA_VERSION}/keda-${KEDA_VERSION}.yaml"
fi

log "Waiting for KEDA operator to be ready..."
kubectl -n keda rollout status deployment/keda-operator --timeout=180s
kubectl -n keda rollout status deployment/keda-operator-metrics-apiserver --timeout=180s
log "KEDA ready."

# ==============================================================
#  6. BUILD AND LOAD DOCKER IMAGES
# ==============================================================
section "6. Building Docker Images"

log "Building websocket-server-instrumented image..."
cd "$PROJECT_ROOT/workloads/websocket/app-instrumented"
docker build -t websocket-server-instrumented:latest .
kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT"
log "Images loaded."

# ==============================================================
#  7. DEPLOY WEBSOCKET SERVER
# ==============================================================
section "7. Deploying WebSocket Server"

kubectl apply -f workloads/websocket/k8s/deployment-experiment-c.yml
kubectl apply -f workloads/websocket/k8s/service.yml
kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s
log "WebSocket server ready."

# ==============================================================
#  8. DEPLOY KEDA SCALEDOBJECT
# ==============================================================
section "8. Deploying KEDA ScaledObject"

kubectl apply -f "$EXP_DIR/manifests/keda-scaledobject.yaml"
log "ScaledObject deployed."
log "  cooldownPeriod=120s, pollingInterval=15s, threshold=100 connections/pod"
log "  minReplicas=2, maxReplicas=15"

sleep 10  # Give KEDA time to create the managed HPA
log "Managed HPA created by KEDA:"
kubectl get hpa

# ==============================================================
#  9. START DATA COLLECTION
# ==============================================================
section "9. Starting Data Collection"

REPLICA_LOG="$RESULT_DIR/replicas.log"
PROM_LOG="$RESULT_DIR/prometheus.log"
KEDA_LOG="$RESULT_DIR/keda-scaledobject.log"

# Collect replica counts + KEDA HPA status
(set +eu; set +o pipefail; while true; do
  TS=$(date +%s)
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.spec.replicas}' 2>/dev/null) || REPLICAS=0
  KEDA_HPA=$(kubectl get hpa keda-hpa-websocket-keda-scaler \
    -o jsonpath='{.status.currentReplicas},{.status.desiredReplicas}' \
    2>/dev/null) || KEDA_HPA=,
  echo "$TS,${REPLICAS:-0},${KEDA_HPA:-,}"
  sleep "$SCRAPE_INTERVAL"
done) >> "$REPLICA_LOG" &
REPLICA_PID=$!

# Collect Prometheus metrics
PROM_URL="http://localhost:9090"
kubectl -n monitoring port-forward svc/prometheus 9090:9090 &>/dev/null &
PROM_PID=$!
sleep 3

(set +eu; set +o pipefail; while true; do
  TS=$(date +%s)
  CONNS=$(curl -s "$PROM_URL/api/v1/query?query=sum(active_connections)" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['result'][0]['value'][1] if d['data']['result'] else '0')" \
    2>/dev/null) || CONNS=0
  RECONNS=$(curl -s "$PROM_URL/api/v1/query?query=sum(rate(new_connections_total%5B15s%5D))" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data']['result'][0]['value'][1] if d['data']['result'] else '0')" \
    2>/dev/null) || RECONNS=0
  echo "$TS,${CONNS:-0},${RECONNS:-0}"
  sleep "$SCRAPE_INTERVAL"
done) >> "$PROM_LOG" &
PROM_COLLECT_PID=$!

# Collect KEDA ScaledObject status periodically
# Collect KEDA ScaledObject status periodically
(set +eu; set +o pipefail; while true; do
  TS=$(date +%s)
  STATUS=$(kubectl get scaledobject websocket-keda-scaler \
    -o jsonpath='{.status.conditions[?(@.type=="Active")].status}' \
    2>/dev/null) || STATUS=Unknown
  echo "$TS,${STATUS:-Unknown}"
  sleep "$SCRAPE_INTERVAL"
done) >> "$KEDA_LOG" &
KEDA_COLLECT_PID=$!

# ==============================================================
#  10. EXPERIMENT EXECUTION
# ==============================================================
section "10. Running Experiment E (2-Cycle Restorm with KEDA)"

MAIN_START=$(date +%s)
log "Experiment started at $(date)"

# CYCLE 1
log "--- CYCLE 1: Ramping 800 clients (${CYCLE1_DURATION}s) ---"
kubectl delete job websocket-loadgen --ignore-not-found=true
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep "$CYCLE1_DURATION"

# DROP 1 (90-second gap)
log "--- DROP 1: Deleting clients (${DROP1_DURATION}s gap) ---"
log "KEY TEST: Will KEDA cooldownPeriod=120s hold replicas through ${DROP1_DURATION}s gap?"
kubectl delete job websocket-loadgen --ignore-not-found=true
DROP1_START=$(date +%s)
sleep "$DROP1_DURATION"
DROP1_END=$(date +%s)
log "DROP 1 complete. Gap: $((DROP1_END - DROP1_START))s (cooldown: 120s)"

# CYCLE 2
log "--- CYCLE 2: 800 fresh clients (${CYCLE2_DURATION}s) ---"
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

kubectl get scaledobject websocket-keda-scaler -o yaml > "$RESULT_DIR/scaledobject-final.yaml"
kubectl get hpa -o yaml > "$RESULT_DIR/hpa-final.yaml"
kubectl get deployment websocket-server -o yaml > "$RESULT_DIR/deployment-final.yaml"
kubectl get events --sort-by=.metadata.creationTimestamp > "$RESULT_DIR/events.log"
kubectl -n keda logs deployment/keda-operator --tail=100 > "$RESULT_DIR/keda-operator.log" 2>/dev/null || true
log "Final state collected."

# ==============================================================
#  12. SUMMARY
# ==============================================================
section "12. Results Summary"

echo ""
echo "Result files in: $RESULT_DIR"
echo "  replicas.log             - Replica count + KEDA HPA status over time"
echo "  prometheus.log           - active_connections and reconnection rate"
echo "  keda-scaledobject.log    - KEDA ScaledObject active status"
echo "  keda-operator.log        - KEDA operator logs (scaling decisions)"
echo "  scaledobject-final.yaml  - Final ScaledObject state"
echo "  events.log               - Kubernetes events"
echo ""
echo "Next: run analysis/parse_logs_experiment_e.py to generate plots"
echo "Compare with Experiment C (StatefulAutoscaler) and D (HPA custom metric)."

# ==============================================================
#  13. PROCESS RESULTS (automatically run)
# ==============================================================
section "13. Processing Results"

log "Running parser: analysis/experiment-e/parse_logs_experiment_e.py"
if ! python3 analysis/experiment-e/parse_logs_experiment_e.py >> "$RESULT_DIR/parse_logs.out" 2>&1; then
  log "ERROR: Parsing failed. See $RESULT_DIR/parse_logs.out"
else
  log "Parsing completed successfully."
fi

log "Running plotter: analysis/experiment-e/plot_experiment_e.py"
if ! python3 analysis/experiment-e/plot_experiment_e.py >> "$RESULT_DIR/plot_experiment.out" 2>&1; then
  log "ERROR: Plotting failed. See $RESULT_DIR/plot_experiment.out"
else
  log "Plotting completed successfully. Plots in $RESULT_DIR/plots"
fi
