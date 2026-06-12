#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  echo "[*] Cleaning up background processes..."
  kill ${HPA_PID:-} 2>/dev/null || true
  kill ${CPU_PID:-} 2>/dev/null || true
  kill ${POD_PID:-} 2>/dev/null || true
  kill ${PROM_COLLECT_PID:-} 2>/dev/null || true
  kill ${PROM_PID:-} 2>/dev/null || true
}
trap cleanup EXIT

# ==========================================================
# Experiment-B2
# Extended LOW Duration (90s)
# Full metric parity (CPU + HPA + Connections)
# ==========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="stateful-exp"
EXPERIMENT_NAME="experiment-b2-hpa-churn-instrumented"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/$EXPERIMENT_NAME"
# -------------------------
# Workload Parameters
# -------------------------
CLIENTS=800
HIGH_DURATION=60
LOW_DURATION=90
CYCLES=5

# Archive Previous Results
if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR")" ]; then
  ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
  mkdir -p "$ARCHIVE_DIR"
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  ARCHIVE_NAME="${EXPERIMENT_NAME}_${TIMESTAMP}.tgz"
  tar -czf "$ARCHIVE_DIR/$ARCHIVE_NAME" -C "$RESULT_DIR" .
  if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
    echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
  else
    rm -rf "$RESULT_DIR"
    mkdir -p "$RESULT_DIR"
  fi
  echo "[*] Archived previous results"
else
  if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
    echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
  else
    rm -rf "$RESULT_DIR"
    mkdir -p "$RESULT_DIR"
  fi
fi

echo "=============================================="
echo " Running $EXPERIMENT_NAME"
echo "=============================================="

# ------------------------------------------------
# Fresh Cluster
# ------------------------------------------------
kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

# ------------------------------------------------
# Install Metrics Server
# ------------------------------------------------
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

echo "[*] Waiting for metrics API..."
until kubectl top pods >/dev/null 2>&1; do sleep 5; done


# ------------------------------------------------
# Deploy Prometheus
# ------------------------------------------------
kubectl apply -f monitoring/prometheus/namespace.yaml
kubectl apply -f monitoring/prometheus/rbac.yaml
kubectl apply -f monitoring/prometheus/configmap.yaml
kubectl apply -f monitoring/prometheus/deployment.yaml
kubectl apply -f monitoring/prometheus/service.yaml

kubectl -n monitoring rollout status deployment/prometheus
# ------------------------------------------------
# Build + Load Images
# ------------------------------------------------
cd "$PROJECT_ROOT/workloads/websocket/app-instrumented"
docker build -t websocket-server-instrumented:latest .
kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT"

# ------------------------------------------------
# Deploy Workload
# ------------------------------------------------
kubectl apply -f workloads/websocket/k8s/deployment-instrumented.yml
kubectl apply -f workloads/websocket/k8s/service.yml

# ------------------------------------------------
# Apply HPA (60s stabilization)
# ------------------------------------------------
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
    scaleDown:
      stabilizationWindowSeconds: 60
EOF

kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s

# ------------------------------------------------
# Wait for HPA to report valid CPU metrics
# ------------------------------------------------
echo "[*] Waiting for HPA to report valid CPU metrics (not <unknown>)..."
MAX_WAIT=180
WAITED=0
while true; do
  HPA_TARGETS=$(kubectl get hpa websocket-hpa --no-headers -o custom-columns=TARGETS:.status.currentMetrics 2>/dev/null || echo "unknown")
  # Also check via kubectl top that metrics exist for our pods
  TOP_OUTPUT=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null || echo "")
  if [ -n "$TOP_OUTPUT" ] && ! echo "$HPA_TARGETS" | grep -q "unknown"; then
    echo "[*] HPA is reporting valid CPU metrics."
    break
  fi
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    echo "[!] WARNING: Timed out waiting for HPA metrics after ${MAX_WAIT}s. Proceeding anyway."
    break
  fi
  echo "[*] Still waiting for metrics... (${WAITED}s elapsed)"
  sleep 10
  WAITED=$((WAITED + 10))
done

# ------------------------------------------------
# Start Collectors
# ------------------------------------------------
echo "[*] Starting collectors..."

# HPA collector
(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/hpa.log"
  kubectl get hpa websocket-hpa >> "$RESULT_DIR/hpa.log"
  sleep 5
done
) &
HPA_PID=$!

# CPU collector
(
while true; do
  METRICS=$(kubectl top pods --no-headers 2>/dev/null || true)
  if [ -n "$METRICS" ]; then
    echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"
    echo "$METRICS" >> "$RESULT_DIR/cpu.log"
  fi
  sleep 5
done
) &
CPU_PID=$!

# Pod lifecycle collector
(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
  kubectl get pods -l app=websocket-server -o wide >> "$RESULT_DIR/pods.log"
  sleep 3
done
) &
POD_PID=$!

# ------------------------------------------------
# Prometheus Aggregated Metrics Collector
# ------------------------------------------------

echo "[*] Starting Prometheus port-forward..."

kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PID=$!

echo "[*] Waiting for Prometheus API..."
until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do
  sleep 2
done

echo "[*] Prometheus is ready"

echo "[*] Waiting for Prometheus to scrape websocket pods..."
PROM_WAIT=0
PROM_MAX=120
until curl -s "http://localhost:9090/api/v1/query?query=active_connections" | grep -q '"result":\[{'; do
  if [ "$PROM_WAIT" -ge "$PROM_MAX" ]; then
    echo "[!] WARNING: Prometheus has not scraped websocket metrics after ${PROM_MAX}s."
    echo "[!] Check Prometheus targets at http://localhost:9090/targets — proceeding anyway."
    break
  fi
  echo "[*] Waiting for active_connections metric... (${PROM_WAIT}s)"
  sleep 10
  PROM_WAIT=$((PROM_WAIT + 10))
done
echo "[*] Prometheus scraping websocket pods: OK"

# Write CSV header
echo "timestamp,active_connections,reconnect_rate" > "$RESULT_DIR/prometheus_dump.csv"

(
set +e

while true; do
  TS=$(date +%s)

  # Active connections (numeric)
  RAW_ACTIVE=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)")
  ACTIVE_VALUE=$(echo "$RAW_ACTIVE" | jq -r '.data.result[0].value[1] // 0')

  # Reconnect rate (numeric, robust against empty vectors)
  RAW_RECONNECT=$(curl -s "http://localhost:9090/api/v1/query?query=sum(increase(new_connections_total%5B30s%5D))")
  RECONNECT_VALUE=$(echo "$RAW_RECONNECT" | jq -r '.data.result[0].value[1] // 0')

  # Write clean structured output
  echo "$TS,$ACTIVE_VALUE,$RECONNECT_VALUE" >> "$RESULT_DIR/prometheus_dump.csv"

  sleep 5
done
) &
PROM_COLLECT_PID=$!

# ------------------------------------------------
# Run Cyclic Load
# ------------------------------------------------
echo "[*] Starting cyclic load..."

for ((i=1; i<=CYCLES; i++)); do
  echo "$(date +%s),HIGH" >> "$RESULT_DIR/phase.log"
  kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
  sleep "$HIGH_DURATION"
  kubectl delete job websocket-loadgen --ignore-not-found

  echo "$(date +%s),LOW" >> "$RESULT_DIR/phase.log"
  sleep "$LOW_DURATION"
done

# ------------------------------------------------
# Final Stabilization Wait
# ------------------------------------------------
echo "[*] Waiting for final scale-down..."
while true; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.replicas}')
  if [ "$REPLICAS" -eq 2 ]; then
    break
  fi
  sleep 5
done

sleep 30

# ------------------------------------------------
# Stop Collectors
# ------------------------------------------------
kill $HPA_PID 2>/dev/null || true
kill $CPU_PID 2>/dev/null || true
kill $PROM_COLLECT_PID 2>/dev/null || true
kill $PROM_PID 2>/dev/null || true
kill $POD_PID 2>/dev/null || true
wait 2>/dev/null || true

# ------------------------------------------------
# Automatic Analysis
# ------------------------------------------------
bash scripts/run-analysis.sh websocket "$EXPERIMENT_NAME"

echo "=============================================="
echo " Experiment-B2 Complete"
echo "=============================================="

kind delete cluster --name "$CLUSTER_NAME"