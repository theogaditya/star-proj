#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#
#  Experiment-B3: CPU-Based HPA with Active Load → Premature Scale-Down
#                 (Direct Counterpart to Experiment-C)
#
#  Narrative:
#    1. CONNECT phase (t=0 to 120s): 800 clients smoothly ramp up their connections
#       over 90 seconds. As initial pings spike CPU, HPA scales up. Because
#       the ramp is gradual, new connections naturally load balance across
#       all newly scaled pods flawlessly.
#
#    2. IDLE phase (t>120s): The clients are programmed to STOP sending pings
#       after 120s, but they keep the connections open indefinitely.
#       CPU drops to ~0% because the 800 connections are now completely idle.
#       HPA sees low CPU and begins scale-down (60s window) — despite 800
#       live connections still existing on those pods.
#
#    3. PERMANENT CONNECTION DROP: As HPA scales down, pods holding the idle
#       connections are terminated. Clients hard-disconnect and are programmed
#       to NEVER reconnect. This explicitly proves connections are lost ONLY
#       because of scaling, leaving a clear step-down graph.
#
#  Key demonstration:
#    HPA's scale-down is purely CPU-driven. It blindly terminates pods
#    holding live connections if CPU is low.
#
#  Compare with Experiment-C:
#    Same cluster, same workload, same load volume —
#    Custom controller holds replicas proportional to connections no matter what.
#
# ==============================================================================

cleanup() {
  echo ""
  echo "[CLEANUP] Stopping background processes..."
  kill ${HPA_PID:-}             2>/dev/null || true
  kill ${CPU_PID:-}             2>/dev/null || true
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
EXPERIMENT_NAME="experiment-b3-hpa-idle-connections"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/$EXPERIMENT_NAME"

# ----------------------------------------------------------
# Timing Parameters
# ----------------------------------------------------------
CONNECT_DURATION=120      # Active ping phase: CPU spikes, HPA scales up
IDLE_MAX_DURATION=240     # Clients stop pinging, connections stay flat, HPA config scales down
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
#  1. ARCHIVE PREVIOUS RESULTS
# ==============================================================
section "1. Archiving Previous Results"

if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR" 2>/dev/null)" ]; then
  ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
  mkdir -p "$ARCHIVE_DIR"
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  ARCHIVE_NAME="${EXPERIMENT_NAME}_${TIMESTAMP}.tgz"
  tar -czf "$ARCHIVE_DIR/$ARCHIVE_NAME" -C "$RESULT_DIR" .
  if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
    echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
  else
    rm -rf "$RESULT_DIR"
    log "Archived previous results -> $ARCHIVE_NAME"
  fi
else
  if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
    echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
  else
    rm -rf "$RESULT_DIR"
    log "No previous results to archive."
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

log "Waiting for metrics API..."
MAX_WAIT=180
WAITED=0
while ! kubectl top pods >/dev/null 2>&1; do
  [ "$WAITED" -ge "$MAX_WAIT" ] && { log "WARNING: Metrics API timeout. Continuing."; break; }
  sleep 5; WAITED=$((WAITED + 5))
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
log "Prometheus ready."

# ==============================================================
#  5. BUILD AND LOAD IMAGES
# ==============================================================
section "5. Building Docker Images"

log "Building instrumented websocket-server (CPU_WORK=1)..."
cd "$PROJECT_ROOT/workloads/websocket/app-instrumented"
docker build -t websocket-server-instrumented:latest .
kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"
log "websocket-server-instrumented loaded."

log "Building active load generator..."
cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"
log "websocket-loadgen loaded."

cd "$PROJECT_ROOT"

# ==============================================================
#  6. DEPLOY WORKLOAD with CPU_WORK=1
# ==============================================================
section "6. Deploying WebSocket Workload (CPU_WORK=1)"

# Use the instrumented deployment — CPU_WORK defaults to 1 in the image
# which means every received message triggers a CPU-intensive loop.
kubectl apply -f workloads/websocket/k8s/deployment-instrumented.yml
kubectl apply -f workloads/websocket/k8s/service.yml

kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s
log "WebSocket server pods ready (CPU_WORK=1)."

# ==============================================================
#  7. APPLY CPU-BASED HPA (60s scaleDown stabilization)
# ==============================================================
section "7. Applying CPU-Based HPA"

cat <<EOF | kubectl apply -f -
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: websocket-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: websocket-server
  minReplicas: 2
  maxReplicas: 15
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 0
      policies:
        - type: Pods
          value: 4
          periodSeconds: 15
    scaleDown:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 4
          periodSeconds: 60
EOF

log "HPA applied."
log "  targetCPU:              60%"
log "  scaleUp stabilization:  0s  (fast scale-up)"
log "  scaleDown stabilization: 60s (aggressive scale-down)"

# ==============================================================
#  8. WAIT FOR HPA METRICS
# ==============================================================
section "8. Waiting for HPA Metrics"

log "Waiting for HPA to report valid CPU metrics..."
MAX_WAIT=180
WAITED=0
while true; do
  TOP=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null || echo "")
  if [ -n "$TOP" ]; then
    log "Metrics API ready for HPA."
    break
  fi
  [ "$WAITED" -ge "$MAX_WAIT" ] && { log "WARNING: HPA metrics timeout. Continuing."; break; }
  log "Waiting... (${WAITED}s)"
  sleep 10; WAITED=$((WAITED + 10))
done

# ==============================================================
#  9. PROMETHEUS PORT-FORWARD
# ==============================================================
section "9. Prometheus Port-Forward"

kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PID=$!

until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do sleep 2; done
log "Prometheus API ready."

# Wait for websocket metrics to appear
PROM_WAIT=0
until curl -s "http://localhost:9090/api/v1/query?query=active_connections" | grep -q '"result":\[{'; do
  [ "$PROM_WAIT" -ge 120 ] && { log "WARNING: active_connections not yet visible."; break; }
  log "Waiting for Prometheus to scrape websocket pods... (${PROM_WAIT}s)"
  sleep 10; PROM_WAIT=$((PROM_WAIT + 10))
done
log "Prometheus scraping websocket pods."

# ==============================================================
#  10. START METRIC COLLECTORS
# ==============================================================
section "10. Starting Metric Collectors"

# HPA state
(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/hpa.log"
  kubectl get hpa websocket-hpa >> "$RESULT_DIR/hpa.log" 2>/dev/null || true
  sleep "$SCRAPE_INTERVAL"
done
) &
HPA_PID=$!
log "HPA collector started (PID $HPA_PID)."

# CPU per pod
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

# Pod lifecycle
(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
  kubectl get pods -l app=websocket-server -o wide >> "$RESULT_DIR/pods.log" 2>/dev/null || true
  sleep "$SCRAPE_INTERVAL"
done
) &
POD_PID=$!
log "Pod lifecycle collector started (PID $POD_PID)."

# Prometheus metrics
echo "timestamp,active_connections,reconnect_rate" > "$RESULT_DIR/prometheus_dump.csv"
(
set +e
while true; do
  TS=$(date +%s)

  RAW_ACTIVE=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)")
  ACTIVE_VALUE=$(echo "$RAW_ACTIVE" | jq -r '.data.result[0].value[1] // 0')

  RAW_RECONNECT=$(curl -s "http://localhost:9090/api/v1/query?query=sum(increase(new_connections_total%5B30s%5D))")
  RECONNECT_VALUE=$(echo "$RAW_RECONNECT" | jq -r '.data.result[0].value[1] // 0')

  echo "$TS,$ACTIVE_VALUE,$RECONNECT_VALUE" >> "$RESULT_DIR/prometheus_dump.csv"
  sleep "$SCRAPE_INTERVAL"
done
) &
PROM_COLLECT_PID=$!
log "Prometheus collector started (PID $PROM_COLLECT_PID)."

# Baseline before load
sleep 15

# ==============================================================
#  11. LOAD PHASES
# ==============================================================
section "11. Running Load Phases"

# --- PHASE 1: CONNECT (800 active smooth ramp, HPA scales UP) ---
log "PHASE: CONNECT -- Deploying 800 websocket clients (CPU_WORK=1)"
log "  Note: Clients linearly stagger connecting over 90s, then stop pinging at 120s."
echo "$(date +%s),CONNECT" >> "$RESULT_DIR/phase.log"

kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"

ELAPSED=0
while [ "$ELAPSED" -lt "$CONNECT_DURATION" ]; do
  REPLICAS=$(kubectl get hpa websocket-hpa -o jsonpath='{.status.currentReplicas}' 2>/dev/null || echo "?")
  CPU_PCT=$(kubectl get hpa websocket-hpa -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null || echo "?")
  CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
  log "  [CONNECT +${ELAPSED}s] replicas=$REPLICAS cpu=${CPU_PCT}% connections=$CONNS"
  sleep 15
  ELAPSED=$((ELAPSED + 15))
done

# --- PHASE 2: IDLE (Clients stop pinging around +120s. Connections stay flat at 800!) ---
log "PHASE: IDLE -- Clients automatically stop sending pings. CPUs will drop."
echo "$(date +%s),IDLE" >> "$RESULT_DIR/phase.log"

log "Waiting for HPA to observe low CPU and scale down to 2 replicas (should take ~60-90s)..."

ELAPSED=0
while [ "$ELAPSED" -lt "$IDLE_MAX_DURATION" ]; do
  REPLICAS=$(kubectl get hpa websocket-hpa -o jsonpath='{.status.currentReplicas}' 2>/dev/null || echo "?")
  CPU_PCT=$(kubectl get hpa websocket-hpa -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}' 2>/dev/null || echo "?")
  CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")

  if [ "$REPLICAS" = "2" ] && [ "$ELAPSED" -ge 45 ]; then
    log "  [IDLE +${ELAPSED}s] replicas=2! HPA successfully scaled down, dropping the 800 live connections!"
    # Give it another 45 seconds to fully capture the storm metrics and the recovery
    for i in {1..3}; do
      sleep 15
      ELAPSED=$((ELAPSED + 15))
      CONNS=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
      log "  [STORM_CAPTURE +${ELAPSED}s] replicas=2 connections=$CONNS"
    done
    break
  fi

  log "  [IDLE +${ELAPSED}s] replicas=$REPLICAS cpu=${CPU_PCT}% connections=$CONNS"
  sleep 15
  ELAPSED=$((ELAPSED + 15))
done

# ==============================================================
#  12. STOP COLLECTORS
# ==============================================================
section "12. Stopping Collectors"

kill $HPA_PID $CPU_PID $PROM_COLLECT_PID $POD_PID $PROM_PID 2>/dev/null || true
wait 2>/dev/null || true
log "All collectors stopped."

# ==============================================================
#  13. ANALYSIS
# ==============================================================
section "13. Running Analysis Pipeline"

PROCESSED_DIR="$PROJECT_ROOT/results/processed/websocket/$EXPERIMENT_NAME"
mkdir -p "$PROCESSED_DIR"

export RAW_DIR="$RESULT_DIR"
export PROCESSED_DIR

log "Parsing raw logs..."
if python3 "$PROJECT_ROOT/analysis/experiment-b3/parse_logs_experiment_b3.py"; then
  log "Parsing complete."
else
  log "ERROR: Parsing failed."
fi

log "Generating plots..."
if python3 "$PROJECT_ROOT/analysis/experiment-b3/plot_experiment_b3.py"; then
  log "Plots generated."
else
  log "ERROR: Plot generation failed."
fi

# ==============================================================
#  14. ARCHIVE
# ==============================================================
section "14. Archiving Results"

ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
mkdir -p "$ARCHIVE_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FINAL_ARCHIVE="${EXPERIMENT_NAME}_final_${TIMESTAMP}.tgz"
tar -czf "$ARCHIVE_DIR/$FINAL_ARCHIVE" \
  -C "$PROJECT_ROOT/results/raw/websocket" "$EXPERIMENT_NAME" \
  -C "$PROJECT_ROOT/results/processed/websocket" "$EXPERIMENT_NAME" 2>/dev/null || true
log "Archived -> $FINAL_ARCHIVE"

# ==============================================================
#  15. CLEANUP
# ==============================================================
section "15. Deleting Kind Cluster"

kind delete cluster --name "$CLUSTER_NAME"
log "Cluster deleted."

# ==============================================================
#  DONE
# ==============================================================
section "Experiment-B3 Complete"

echo ""
echo "  Raw results:       $RESULT_DIR"
echo "  Processed results: $PROCESSED_DIR"
echo "  Plots:             $PROCESSED_DIR/plots/"
echo ""
echo "  What to look for in the plots:"
echo "    - cpu.png:          CPU spikes during CONNECT, drops to 0% at 120s"
echo "    - replicas.png:     Replicas scale UP on CPU, then DOWN quickly (60s window)"
echo "    - connections.png:  Connections hold perfectly flat at 800 natively... until HPA terminates the pods."
echo "                        The graph will step down permanently, proving HPA kills live connections."
echo "    - combined.png:     Explicit proof that connection drop is tied 100% to pod scale-down."
echo ""
echo "  Compare with Experiment-C:"
echo "    Custom controller held replicas proportional to connections throughout."
echo ""
