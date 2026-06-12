#!/usr/bin/env bash
set -euo pipefail
# =============================================================================
#
#  run-failure-scenarios.sh — Run all 3 failure scenario experiments on a
#  single Kind cluster, one after the other.
#
#  Failure scenarios:
#    1. Metric Staleness     — Prometheus scrape_interval patched to 60 s.
#                              Controller sees stale metrics and reacts late.
#    2. Instant Spike        — Load generator uses RAMP_UP_DURATION=0 so all
#                              800 clients connect simultaneously (thundering
#                              herd).  Tests controller's maxScaleUpStep.
#    3. Prometheus Outage    — Prometheus pod killed mid-experiment for 120 s.
#                              Controller must survive with last-known metrics.
#
#  Results land in:
#    results/raw/websocket/failure-1-metric-staleness/
#    results/raw/websocket/failure-2-instant-spike/
#    results/raw/websocket/failure-3-prometheus-outage/
#
#  After all 3 scenarios, runs parse + plot scripts.
#
# =============================================================================

cleanup() {
  echo ""
  echo "[CLEANUP] Stopping background processes..."
  kill ${CPU_PID:-}          2>/dev/null || true
  kill ${REPLICA_PID:-}      2>/dev/null || true
  kill ${PROM_COL_PID:-}     2>/dev/null || true
  kill ${PROM_PID:-}         2>/dev/null || true
  kill ${POD_PID:-}          2>/dev/null || true
  wait                       2>/dev/null || true
  echo "[CLEANUP] Done."
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="exp-failure"
SCRAPE_INTERVAL=5   # seconds between metric samples in collectors

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
section() {
  echo ""
  echo "============================================================"
  echo "  $*"
  echo "============================================================"
}
log() { echo "[$(date '+%H:%M:%S')] $*"; }

stop_collectors() {
  kill ${CPU_PID:-}       2>/dev/null || true
  kill ${REPLICA_PID:-}   2>/dev/null || true
  kill ${PROM_COL_PID:-}  2>/dev/null || true
  kill ${POD_PID:-}       2>/dev/null || true
  wait ${CPU_PID:-} ${REPLICA_PID:-} ${PROM_COL_PID:-} ${POD_PID:-} 2>/dev/null || true
  CPU_PID="" REPLICA_PID="" PROM_COL_PID="" POD_PID=""
}

# ===========================================================================
#  PHASE 0  — Cluster bootstrap (done once)
# ===========================================================================
section "0. Bootstrap: creating kind cluster '$CLUSTER_NAME'"

kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$SCRIPT_DIR/kind.yml"
log "Cluster created."

# Metrics server
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
    "--cert-dir=/tmp","--secure-port=10250",
    "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
    "--kubelet-use-node-status-port","--metric-resolution=15s",
    "--kubelet-insecure-tls"]}]'
kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s

# Prometheus
kubectl apply -f monitoring/prometheus/namespace.yaml
kubectl apply -f monitoring/prometheus/rbac.yaml
kubectl apply -f monitoring/prometheus/configmap.yaml
kubectl apply -f monitoring/prometheus/deployment.yaml
kubectl apply -f monitoring/prometheus/service.yaml
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
log "Prometheus ready."

# Build + load images
section "Building Docker images"
cd "$PROJECT_ROOT/workloads/websocket/app-instrumented"
docker build -t websocket-server-instrumented:latest .
kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/controller"
make docker-build IMG=controller:latest
kind load docker-image controller:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT"
log "Images loaded."

# ===========================================================================
#  Helper: deploy fresh workload + controller, wait ready
# ===========================================================================
deploy_workload() {
  # Delete any lingering resources from previous scenario
  kubectl delete statefulautoscaler --all --ignore-not-found 2>/dev/null || true
  kubectl delete job websocket-loadgen --ignore-not-found 2>/dev/null || true
  kubectl delete deployment websocket-server --ignore-not-found 2>/dev/null || true
  kubectl delete svc websocket-service --ignore-not-found 2>/dev/null || true
  sleep 5

  kubectl apply -f workloads/websocket/k8s/deployment-experiment-c.yml
  kubectl apply -f workloads/websocket/k8s/service.yml
  kubectl rollout status deployment/websocket-server --timeout=180s
  # Deploy controller (install CRDs) and ensure it's running before creating CR
  cd "$PROJECT_ROOT/controller"
  make install
  make deploy IMG=controller:latest
  kubectl -n controller-system rollout status deployment/controller-controller-manager --timeout=300s
  cd "$PROJECT_ROOT"

  # StatefulAutoscaler CR (now CRDs should be present)
  cat <<EOF | kubectl apply -f -
apiVersion: autoscaling.star.local/v1alpha1
kind: StatefulAutoscaler
metadata:
  name: websocket-autoscaler
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: websocket-server
  minReplicas: 2
  maxReplicas: 15
  targetConnectionsPerPod: 100
  maxScaleUpStep: 3
  maxScaleDownStep: 2
  scaleUpCooldownSeconds: 10
  scaleDownCooldownSeconds: 120
  drain:
    enabled: false
    timeoutSeconds: 60
    maxConcurrentDrains: 1
EOF

  log "Workload + controller ready."
}

# ===========================================================================
#  Helper: start Prometheus port-forward, wait ready
# ===========================================================================
start_prom_forward() {
  # Kill any previous port-forward
  kill ${PROM_PID:-} 2>/dev/null || true
  sleep 2
  kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
  PROM_PID=$!
  until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do sleep 2; done
  # Wait for active_connections to appear
  PROM_WAIT=0
  until curl -s "http://localhost:9090/api/v1/query?query=active_connections" | grep -q '"result":\[{'; do
    [ "$PROM_WAIT" -ge 120 ] && { log "WARNING: no active_connections metric after 120s"; break; }
    sleep 10; PROM_WAIT=$((PROM_WAIT+10))
  done
  log "Prometheus port-forward ready."
}

# ===========================================================================
#  Helper: start metric collectors writing into RESULT_DIR
# ===========================================================================
RESULT_DIR=""   # set before calling this
start_collectors() {
  (while true; do
    METRICS=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null || true)
    [ -n "$METRICS" ] && { echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"; echo "$METRICS" >> "$RESULT_DIR/cpu.log"; }
    sleep "$SCRAPE_INTERVAL"
  done) &
  CPU_PID=$!

  (while true; do
    REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    [ -z "$REPLICAS" ] && REPLICAS=0
    echo "$(date +%s),$REPLICAS" >> "$RESULT_DIR/replicas.log"
    sleep "$SCRAPE_INTERVAL"
  done) &
  REPLICA_PID=$!

  (while true; do
    echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
    kubectl get pods -l app=websocket-server -o wide >> "$RESULT_DIR/pods.log" 2>/dev/null || true
    sleep "$SCRAPE_INTERVAL"
  done) &
  POD_PID=$!

  echo "timestamp,active_connections" > "$RESULT_DIR/prometheus_dump.csv"
  (set +e; while true; do
    TS=$(date +%s)
    RAW=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)")
    VALUE=$(echo "$RAW" | jq -r '.data.result[0].value[1] // 0')
    echo "$TS,$VALUE" >> "$RESULT_DIR/prometheus_dump.csv"
    sleep "$SCRAPE_INTERVAL"
  done) &
  PROM_COL_PID=$!
  log "Collectors started."
}

# ===========================================================================
#  SCENARIO 1 — Metric Staleness
# ===========================================================================
section "SCENARIO 1 — Metric Staleness (scrape_interval=60s)"

RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/failure-1-metric-staleness"
if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
  echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
else
  rm -rf "$RESULT_DIR"; mkdir -p "$RESULT_DIR"
fi

# Patch Prometheus configmap to use 60s scrape interval
kubectl -n monitoring create configmap prometheus-config \
  --from-literal=prometheus.yml="$(cat monitoring/prometheus/configmap.yaml | \
    python3 -c "
import sys, yaml
obj = yaml.safe_load(sys.stdin.read().split('prometheus.yml: |',1)[1])
obj['global']['scrape_interval'] = '60s'
obj['global']['evaluation_interval'] = '60s'
print(yaml.dump(obj))
  ")" \
  --dry-run=client -o yaml | kubectl apply -f -

# Simpler approach: patch with kubectl
kubectl -n monitoring patch configmap prometheus-config --type=merge -p "$(python3 - <<'PYEOF'
import json
prometheus_yml = """
global:
  scrape_interval: 60s
  evaluation_interval: 60s
scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: 'true'
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)
      - source_labels: [__address__, __meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        regex: '([^:]+)(?::\d+)?;(\d+)'
        replacement: '$1:$2'
        target_label: __address__
      - source_labels: [__meta_kubernetes_pod_name]
        action: replace
        target_label: kubernetes_pod_name
      - source_labels: [__meta_kubernetes_namespace]
        action: replace
        target_label: kubernetes_namespace
"""
print(json.dumps({"data": {"prometheus.yml": prometheus_yml}}))
PYEOF
)"
kubectl -n monitoring rollout restart deployment/prometheus
kubectl -n monitoring rollout status deployment/prometheus --timeout=120s
log "Prometheus restarted with 60s scrape_interval."

deploy_workload
start_prom_forward
start_collectors
sleep 15

log "Running 800-client ramp load (CYCLE_1) for 150s..."
echo "$(date +%s),CYCLE_1" >> "$RESULT_DIR/phase.log"
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep 150

log "DROP_1: deleting load, waiting 90s..."
echo "$(date +%s),DROP_1" >> "$RESULT_DIR/phase.log"
kubectl delete job websocket-loadgen --ignore-not-found
sleep 90

log "CYCLE_2: re-deploying load for 150s..."
echo "$(date +%s),CYCLE_2" >> "$RESULT_DIR/phase.log"
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep 150

log "FINAL_DROP: deleting load, waiting 180s for scale-down..."
echo "$(date +%s),FINAL_DROP" >> "$RESULT_DIR/phase.log"
kubectl delete job websocket-loadgen --ignore-not-found
sleep 180

stop_collectors

# Restore normal Prometheus configmap and restart
kubectl apply -f monitoring/prometheus/configmap.yaml
kubectl -n monitoring rollout restart deployment/prometheus
kubectl -n monitoring rollout status deployment/prometheus --timeout=120s
log "Prometheus restored to normal scrape_interval."

log "Scenario 1 complete → $RESULT_DIR"

# ===========================================================================
#  SCENARIO 2 — Instant Spike
# ===========================================================================
section "SCENARIO 2 — Instant Spike (RAMP_UP_DURATION=0)"

RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/failure-2-instant-spike"
if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
  echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
else
  rm -rf "$RESULT_DIR"; mkdir -p "$RESULT_DIR"
fi

deploy_workload
start_prom_forward
start_collectors
sleep 15

log "CYCLE_1: instant 800-client spike..."
echo "$(date +%s),CYCLE_1" >> "$RESULT_DIR/phase.log"
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job-instant.yaml"
sleep 150

log "DROP_1: deleting load, waiting 90s..."
echo "$(date +%s),DROP_1" >> "$RESULT_DIR/phase.log"
kubectl delete job websocket-loadgen --ignore-not-found
sleep 90

log "CYCLE_2: instant spike again..."
echo "$(date +%s),CYCLE_2" >> "$RESULT_DIR/phase.log"
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job-instant.yaml"
sleep 150

log "FINAL_DROP: deleting load, waiting 180s for scale-down..."
echo "$(date +%s),FINAL_DROP" >> "$RESULT_DIR/phase.log"
kubectl delete job websocket-loadgen --ignore-not-found
sleep 180

stop_collectors
log "Scenario 2 complete → $RESULT_DIR"

# ===========================================================================
#  SCENARIO 3 — Prometheus Outage
# ===========================================================================
section "SCENARIO 3 — Prometheus Outage (killed at t=75s into CYCLE_1, restored at t=195s)"

RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/failure-3-prometheus-outage"
if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
  echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
else
  rm -rf "$RESULT_DIR"; mkdir -p "$RESULT_DIR"
fi

deploy_workload
start_prom_forward
start_collectors
sleep 15

log "CYCLE_1: deploying load..."
echo "$(date +%s),CYCLE_1" >> "$RESULT_DIR/phase.log"
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"

log "Waiting 75s then killing Prometheus pod..."
sleep 75
echo "$(date +%s),PROM_KILL" >> "$RESULT_DIR/phase.log"
kubectl -n monitoring delete pod -l app=prometheus --grace-period=0 --force 2>/dev/null || true
log "Prometheus pod deleted (k8s will restart it). Waiting 120s..."

sleep 120
echo "$(date +%s),PROM_RESTORE" >> "$RESULT_DIR/phase.log"
# Wait for Prometheus to come back
kubectl -n monitoring rollout status deployment/prometheus --timeout=120s || true
kill ${PROM_PID:-} 2>/dev/null || true; sleep 2
kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PID=$!
until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do sleep 2; done
log "Prometheus back online."

# Continue CYCLE_1 for remaining time (CYCLE_1_DURATION=150s, already 75+120=195s elapsed)
# Drop the load now
log "DROP_1: deleting load, waiting 90s..."
echo "$(date +%s),DROP_1" >> "$RESULT_DIR/phase.log"
kubectl delete job websocket-loadgen --ignore-not-found
sleep 90

log "CYCLE_2: re-deploying for 150s..."
echo "$(date +%s),CYCLE_2" >> "$RESULT_DIR/phase.log"
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep 150

log "FINAL_DROP: deleting load, waiting 180s..."
echo "$(date +%s),FINAL_DROP" >> "$RESULT_DIR/phase.log"
kubectl delete job websocket-loadgen --ignore-not-found
sleep 180

stop_collectors
log "Scenario 3 complete → $RESULT_DIR"

# ===========================================================================
#  ANALYSIS
# ===========================================================================
section "Running failure scenario analysis"
mkdir -p "$PROJECT_ROOT/results/processed/websocket"

python3 "$PROJECT_ROOT/analysis/failure-scenarios/parse_failure_scenarios.py"
python3 "$PROJECT_ROOT/analysis/failure-scenarios/plot_failure_scenarios.py"

# ===========================================================================
#  CLEANUP
# ===========================================================================
section "Deleting kind cluster '$CLUSTER_NAME'"
kind delete cluster --name "$CLUSTER_NAME"

echo ""
echo "All failure scenarios complete."
echo "  Raw:       $PROJECT_ROOT/results/raw/websocket/failure-{1,2,3}*/"
echo "  Processed: $PROJECT_ROOT/results/processed/websocket/failure-{1,2,3}*/"
