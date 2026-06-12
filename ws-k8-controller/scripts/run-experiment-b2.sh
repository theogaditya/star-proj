#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Experiment-B2
# Extended LOW Duration (90s)
# Full metric parity (CPU + HPA + Connections)
# ==========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="stateful-exp"
EXPERIMENT_NAME="experiment-b2-hpa-churn-extended-low"

RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/$EXPERIMENT_NAME"

# -------------------------
# Workload Parameters
# -------------------------
CLIENTS=800
HIGH_DURATION=60
LOW_DURATION=90
CYCLES=5

if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
  echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
else
  rm -rf "$RESULT_DIR"
  mkdir -p "$RESULT_DIR"
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
# Build + Load Images
# ------------------------------------------------
cd "$PROJECT_ROOT/workloads/websocket/app"
docker build -t websocket-server:latest .
kind load docker-image websocket-server:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT"

# ------------------------------------------------
# Deploy Workload
# ------------------------------------------------
kubectl apply -f workloads/websocket/k8s/deployment.yml
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

# Active connection collector
kubectl port-forward svc/websocket-service 8080:8080 >/dev/null 2>&1 &
PF_PID=$!
sleep 5

(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/active_connections.log"
  curl -s http://localhost:8080/metrics >> "$RESULT_DIR/active_connections.log"
  sleep 5
done
) &
CONN_PID=$!

# ------------------------------------------------
# Run Cyclic Load
# ------------------------------------------------
echo "[*] Starting cyclic load..."

for ((i=1; i<=CYCLES; i++)); do
  echo "$(date +%s),HIGH" >> "$RESULT_DIR/phase.log"
  kubectl apply -f load-generator/websocket-client/k8s/job.yaml
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
kill $CONN_PID 2>/dev/null || true
kill $PF_PID 2>/dev/null || true
wait 2>/dev/null || true

# ------------------------------------------------
# Automatic Analysis
# ------------------------------------------------
bash scripts/run-analysis.sh websocket "$EXPERIMENT_NAME"

echo "=============================================="
echo " Experiment-B2 Complete"
echo "=============================================="

kind delete cluster --name "$CLUSTER_NAME"