#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#
#  Experiment-C: Connection-Based Autoscaling with Custom Controller
#
#  Demonstrates that the StatefulAutoscaler controller scales based on active
#  connections, NOT CPU utilization. Even when CPU is low, high active
#  connections keep replicas from scaling down. Replicas only decrease when
#  connections actually drop.
#
#  Load pattern (2-Cycle Restorm Simulation):
#    CYCLE 1    -> 800 clients smoothly ramp up, ping, then idle   (150s)
#    DROP 1     -> Clients deleted. Restorm gap <120s cooldown     (90s)
#    CYCLE 2    -> 800 clients redeploy, instaconnect to warm pods (150s)
#    FINAL DROP -> Clients deleted. Cooldown expires -> scale down (180s)
#
#  Proves: The StatefulAutoscaler correctly uses scale-down cooldown
#  to ride out transient connection drops without aggressively scaling
#  down -> saving pods for the inevitable reconnection storm.
#
# ==============================================================================

cleanup() {
  echo ""
  echo "[CLEANUP] Stopping background processes..."
  kill ${CPU_PID:-}             2>/dev/null || true
  kill ${REPLICA_PID:-}         2>/dev/null || true
  kill ${PROM_COLLECT_PID:-}    2>/dev/null || true
  kill ${PROM_PID:-}            2>/dev/null || true
  kill ${POD_PID:-}             2>/dev/null || true
  wait                          2>/dev/null || true
  echo "[CLEANUP] Done."
}
trap cleanup EXIT

# ----------------------------------------------------------
# Paths
# ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="stateful-exp"
EXPERIMENT_NAME="experiment-c-stateful"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/$EXPERIMENT_NAME"
CONTROLLER_DIR="$PROJECT_ROOT/controller"

# ----------------------------------------------------------
# Timing Parameters
# ----------------------------------------------------------
CYCLE1_DURATION=150
DROP1_DURATION=90
CYCLE2_DURATION=150
FINAL_DROP_DURATION=180
SCRAPE_INTERVAL=5

# ----------------------------------------------------------
# Logging helpers
# ----------------------------------------------------------
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

if [ -d "$RESULT_DIR" ]; then
  if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
    echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
  else
    rm -rf "$RESULT_DIR"
    log "Cleared previous raw results."
  fi
fi

mkdir -p "$RESULT_DIR"

# ==============================================================
#  2. CREATE FRESH KIND CLUSTER
# ==============================================================
section "2. Creating Fresh Kind Cluster"

kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
log "Old cluster deleted (if existed)."

kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"
log "Kind cluster '$CLUSTER_NAME' created successfully."

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
log "Metrics server deployed and patched."

log "Waiting for metrics API to become available..."
MAX_WAIT=180
WAITED=0
while ! kubectl top pods >/dev/null 2>&1; do
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    log "WARNING: Metrics API not ready after ${MAX_WAIT}s. Continuing."
    break
  fi
  sleep 5
  WAITED=$((WAITED + 5))
done
log "Metrics API ready (${WAITED}s)."

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
log "Prometheus deployed and ready."

# ==============================================================
#  5. BUILD AND LOAD DOCKER IMAGES
# ==============================================================
section "5. Building Docker Images"

log "Building websocket-server-instrumented image..."
cd "$PROJECT_ROOT/workloads/websocket/app-instrumented"
docker build -t websocket-server-instrumented:latest .
kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"
log "websocket-server-instrumented image loaded into cluster."

log "Building sophisticated b3 load generator image..."
cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"
log "websocket-loadgen loaded."

log "Building custom controller image..."
cd "$CONTROLLER_DIR"
make docker-build IMG=controller:latest
kind load docker-image controller:latest --name "$CLUSTER_NAME"
log "Controller image loaded into cluster."

cd "$PROJECT_ROOT"

# ==============================================================
#  6. DEPLOY WORKLOAD
# ==============================================================
section "6. Deploying WebSocket Workload (CPU_WORK=0)"

kubectl apply -f workloads/websocket/k8s/deployment-experiment-c.yml
kubectl apply -f workloads/websocket/k8s/service.yml

kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s
log "WebSocket server pods are ready."

# ==============================================================
#  7. DEPLOY CUSTOM CONTROLLER
# ==============================================================
section "7. Deploying Custom StatefulAutoscaler Controller"

cd "$CONTROLLER_DIR"

log "Installing CRDs..."
make install

log "Deploying controller manager..."
make deploy IMG=controller:latest

log "Waiting for controller manager to be ready..."
kubectl -n controller-system rollout status deployment/controller-controller-manager --timeout=300s
log "Controller manager is running."

cd "$PROJECT_ROOT"

# ==============================================================
#  8. APPLY STATEFULAUTOSCALER CR
# ==============================================================
section "8. Applying StatefulAutoscaler Custom Resource"

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

log "StatefulAutoscaler CR applied."
log "  targetConnectionsPerPod: 100"
log "  With 600 connections -> expected ~6 replicas"

# ==============================================================
#  9. WAIT FOR PROMETHEUS TO SCRAPE WEBSOCKET PODS
# ==============================================================
section "9. Waiting for Prometheus Readiness"

log "Starting Prometheus port-forward..."
kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PID=$!

log "Waiting for Prometheus API..."
until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do
  sleep 2
done
log "Prometheus API is ready."

log "Waiting for Prometheus to scrape websocket pod metrics..."
PROM_WAIT=0
PROM_MAX=120
until curl -s "http://localhost:9090/api/v1/query?query=active_connections" | grep -q '"result":\[{'; do
  if [ "$PROM_WAIT" -ge "$PROM_MAX" ]; then
    log "WARNING: Prometheus has not scraped websocket metrics after ${PROM_MAX}s."
    log "Check Prometheus targets at http://localhost:9090/targets"
    break
  fi
  log "Waiting for active_connections metric... (${PROM_WAIT}s)"
  sleep 10
  PROM_WAIT=$((PROM_WAIT + 10))
done
log "Prometheus is scraping websocket pods."

# ==============================================================
#  10. START METRIC COLLECTORS
# ==============================================================
section "10. Starting Metric Collectors"

# --- CPU collector ---
(
  while true; do
    METRICS=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null || true)
    if [ -n "$METRICS" ]; then
      echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"
      echo "$METRICS" >> "$RESULT_DIR/cpu.log"
    fi
    sleep "$SCRAPE_INTERVAL"
  done
) &
CPU_PID=$!
log "CPU collector started (PID $CPU_PID)."

# --- Replica count collector (from deployment, not HPA) ---
(
  while true; do
    REPLICAS=$(kubectl get deployment websocket-server \
      -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    [ -z "$REPLICAS" ] && REPLICAS=0
    echo "$(date +%s),$REPLICAS" >> "$RESULT_DIR/replicas.log"
    sleep "$SCRAPE_INTERVAL"
  done
) &
REPLICA_PID=$!
log "Replica collector started (PID $REPLICA_PID)."

# --- Pod lifecycle collector ---
(
  while true; do
    echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
    kubectl get pods -l app=websocket-server -o wide >> "$RESULT_DIR/pods.log" 2>/dev/null || true
    sleep "$SCRAPE_INTERVAL"
  done
) &
POD_PID=$!
log "Pod lifecycle collector started (PID $POD_PID)."

# --- Prometheus active_connections collector ---
echo "timestamp,active_connections" > "$RESULT_DIR/prometheus_dump.csv"

(
  set +e
  while true; do
    TS=$(date +%s)
    RAW=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)")
    VALUE=$(echo "$RAW" | jq -r '.data.result[0].value[1] // 0')
    echo "$TS,$VALUE" >> "$RESULT_DIR/prometheus_dump.csv"
    sleep "$SCRAPE_INTERVAL"
  done
) &
PROM_COLLECT_PID=$!
log "Prometheus collector started (PID $PROM_COLLECT_PID)."

# Small delay to collect baseline metrics before load
sleep 15

# ==============================================================
#  11. LOAD GENERATION
# ==============================================================
section "11. Running Load Phases"

# --- CYCLE 1: CONNECT & IDLE ---
log "PHASE: CYCLE_1 -- Deploying 800 clients (90s ramp, 120s ping, then idle)"
echo "$(date +%s),CYCLE_1" >> "$RESULT_DIR/phase.log"

kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"

ELAPSED=0
while [ "$ELAPSED" -lt "$CYCLE1_DURATION" ]; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "?")
  CPU_TOTAL=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null | awk '{gsub("m","",$2); sum+=$2} END{print sum}' || echo "?")
  CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
  log "  [CYCLE1 +${ELAPSED}s] replicas=$REPLICAS cpu=${CPU_TOTAL}m connections=$CONNS"
  sleep 15
  ELAPSED=$((ELAPSED + 15))
done

# --- DROP 1: THE RESTORM GAP ---
log "PHASE: DROP_1 -- Deleting clients. Connections drop to 0. Controller should hold replicas due to 120s cooldown."
echo "$(date +%s),DROP_1" >> "$RESULT_DIR/phase.log"

kubectl delete job websocket-loadgen --ignore-not-found
log "Job deleted. Waiting 90s (less than 120s cooldown)..."

ELAPSED=0
while [ "$ELAPSED" -lt "$DROP1_DURATION" ]; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "?")
  CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
  log "  [DROP1 +${ELAPSED}s] replicas=$REPLICAS connections=$CONNS"
  sleep 15
  ELAPSED=$((ELAPSED + 15))
done

# --- CYCLE 2: RESTORM ---
log "PHASE: CYCLE_2 -- Redeploying 800 clients. They should instantly land on remaining pods."
echo "$(date +%s),CYCLE_2" >> "$RESULT_DIR/phase.log"

kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"

ELAPSED=0
while [ "$ELAPSED" -lt "$CYCLE2_DURATION" ]; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "?")
  CPU_TOTAL=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null | awk '{gsub("m","",$2); sum+=$2} END{print sum}' || echo "?")
  CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
  log "  [CYCLE2 +${ELAPSED}s] replicas=$REPLICAS cpu=${CPU_TOTAL}m connections=$CONNS"
  sleep 15
  ELAPSED=$((ELAPSED + 15))
done

# --- FINAL DROP ---
log "PHASE: FINAL_DROP -- Deleting clients permanently. Waiting for cooldown to expire and controller to scale down."
echo "$(date +%s),FINAL_DROP" >> "$RESULT_DIR/phase.log"

kubectl delete job websocket-loadgen --ignore-not-found

ELAPSED=0
while [ "$ELAPSED" -lt "$FINAL_DROP_DURATION" ]; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "?")
  CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
  log "  [FINAL_DROP +${ELAPSED}s] replicas=$REPLICAS connections=$CONNS"

  if [ "$REPLICAS" = "2" ] && [ "$ELAPSED" -ge 120 ]; then
    log "  Controller successfully scaled down to 2 replicas after cooldown expired!"
    sleep 30
    break
  fi

  sleep 15
  ELAPSED=$((ELAPSED + 15))
done

# ==============================================================
#  12. STOP COLLECTORS
# ==============================================================
section "12. Stopping Collectors"

kill $CPU_PID          2>/dev/null || true
kill $REPLICA_PID      2>/dev/null || true
kill $PROM_COLLECT_PID 2>/dev/null || true
kill $POD_PID          2>/dev/null || true
kill $PROM_PID         2>/dev/null || true
wait                   2>/dev/null || true
log "All collectors stopped."

# ==============================================================
#  13. RUN ANALYSIS
# ==============================================================
section "13. Running Analysis Pipeline"

PROCESSED_DIR="$PROJECT_ROOT/results/processed/websocket/$EXPERIMENT_NAME"
mkdir -p "$PROCESSED_DIR"

export RAW_DIR="$RESULT_DIR"
export PROCESSED_DIR

log "Parsing raw logs..."
if python3 "$PROJECT_ROOT/analysis/experiment-c/parse_logs_experiment_c.py"; then
  log "Log parsing complete."
else
  log "ERROR: Log parsing failed."
fi

log "Generating plots..."
if python3 "$PROJECT_ROOT/analysis/experiment-c/plot_experiment_c.py"; then
  log "Plot generation complete."
else
  log "ERROR: Plot generation failed."
fi

# ==============================================================
#  14. ARCHIVE RESULTS
# ==============================================================
section "14. Archiving Results"

ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
mkdir -p "$ARCHIVE_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FINAL_ARCHIVE="${EXPERIMENT_NAME}_final_${TIMESTAMP}.tgz"
tar -czf "$ARCHIVE_DIR/$FINAL_ARCHIVE" \
  -C "$PROJECT_ROOT/results/raw/websocket" "$EXPERIMENT_NAME" \
  -C "$PROJECT_ROOT/results/processed/websocket" "$EXPERIMENT_NAME" 2>/dev/null || true
log "Results archived -> $FINAL_ARCHIVE"

# ==============================================================
#  15. CLEANUP
# ==============================================================
section "15. Deleting Kind Cluster"

kind delete cluster --name "$CLUSTER_NAME"
log "Cluster deleted."

# ==============================================================
#  DONE
# ==============================================================
section "Experiment-C Complete"

echo ""
echo "  Raw results:       $RESULT_DIR"
echo "  Processed results: $PROCESSED_DIR"
echo "  Plots:             $PROCESSED_DIR/plots/"
echo ""
echo "  Key plots to inspect:"
echo "    - cpu.png          : CPU rises and falls, decoupled from scaling."
echo "    - connections.png  : Connections go 800 -> 0 -> 800 -> 0."
echo "    - replicas.png     : Replicas stay perfectly flat at 15 through the first restorm gap!"
echo "    - combined.png     : Overlay proving scale-down optimization."
echo ""
