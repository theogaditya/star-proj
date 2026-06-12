#!/usr/bin/env bash
set -euo pipefail

# ==========================================================
# Experiment-B1 — CPU-HPA Under Cyclic Churn
# maxReplicas = 15
# ==========================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="stateful-exp"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/experiment-b1-hpa-churn"
PROCESSED_DIR="$PROJECT_ROOT/results/processed/websocket/experiment-b1-hpa-churn"

HIGH_DURATION=60
LOW_DURATION=30
CYCLES=5
SAMPLING_INTERVAL=5

mkdir -p "$RESULT_DIR"

# ----------------------------------------------------------
# Archive Previous Results
# ----------------------------------------------------------
if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR")" ]; then
  ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
  mkdir -p "$ARCHIVE_DIR"
  TS=$(date +%Y%m%d_%H%M%S)
  tar -czf "$ARCHIVE_DIR/experiment-b1_${TS}.tgz" -C "$RESULT_DIR" .
    if [ "${MULTI_RUN:-0}" = "1" ] || [ "${MULTI_RUN:-}" = "true" ]; then
      echo "[*] MULTI_RUN=1 -> preserving $RESULT_DIR"
    else
      rm -rf "$RESULT_DIR"
      mkdir -p "$RESULT_DIR"
    fi
  echo "[*] Archived previous results"
fi

echo "=============================================="
echo " Experiment-B: HPA Under Cyclic Load Churn"
echo "=============================================="

# ----------------------------------------------------------
# Fresh Cluster
# ----------------------------------------------------------
kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

# ----------------------------------------------------------
# Install Metrics Server
# ----------------------------------------------------------
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
until kubectl top pods >/dev/null 2>&1; do sleep 5; done

# ----------------------------------------------------------
# Build + Load Images
# ----------------------------------------------------------
cd "$PROJECT_ROOT/workloads/websocket/app"
docker build -t websocket-server:latest .
kind load docker-image websocket-server:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT"

# ----------------------------------------------------------
# Deploy Workload
# ----------------------------------------------------------
kubectl apply -f workloads/websocket/k8s/deployment.yml
kubectl apply -f workloads/websocket/k8s/service.yml

# Apply HPA with maxReplicas=15
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
    scaleDown:
      stabilizationWindowSeconds: 300
EOF

kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s

# ----------------------------------------------------------
# Start Collectors
# ----------------------------------------------------------
echo "[*] Starting collectors..."

(
while true; do
  METRICS=$(kubectl top pods --no-headers 2>/dev/null || true)
  if [ -n "$METRICS" ]; then
    echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"
    echo "$METRICS" >> "$RESULT_DIR/cpu.log"
  fi
  sleep "$SAMPLING_INTERVAL"
done
) &
CPU_PID=$!

(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/hpa.log"
  kubectl get hpa websocket-hpa >> "$RESULT_DIR/hpa.log"
  sleep "$SAMPLING_INTERVAL"
done
) &
HPA_PID=$!

kubectl port-forward svc/websocket-service 8080:8080 >/dev/null 2>&1 &
PF_PID=$!
sleep 5

(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/active_connections.log"
  curl -s http://localhost:8080/metrics >> "$RESULT_DIR/active_connections.log"
  sleep "$SAMPLING_INTERVAL"
done
) &
CONN_PID=$!

# ----------------------------------------------------------
# Cyclic Load Pattern
# ----------------------------------------------------------
echo "[*] Running cyclic load..."

for ((i=1;i<=CYCLES;i++)); do

  echo "$(date +%s),CYCLE_${i}_HIGH_START" >> "$RESULT_DIR/phase.log"
  kubectl apply -f load-generator/websocket-client/k8s/job.yaml
  sleep "$HIGH_DURATION"

  echo "$(date +%s),CYCLE_${i}_HIGH_END" >> "$RESULT_DIR/phase.log"
  kubectl delete job websocket-loadgen --ignore-not-found

  echo "$(date +%s),CYCLE_${i}_LOW_START" >> "$RESULT_DIR/phase.log"
  sleep "$LOW_DURATION"
  echo "$(date +%s),CYCLE_${i}_LOW_END" >> "$RESULT_DIR/phase.log"

done

# ----------------------------------------------------------
# Wait for Final Scale-Down
# ----------------------------------------------------------
echo "[*] Waiting for final scale-down..."
while true; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.replicas}')
  if [ "$REPLICAS" -le 2 ]; then
    break
  fi
  sleep 5
done

sleep 30

# ----------------------------------------------------------
# Stop Collectors
# ----------------------------------------------------------
kill $CPU_PID 2>/dev/null || true
kill $HPA_PID 2>/dev/null || true
kill $CONN_PID 2>/dev/null || true
kill $PF_PID 2>/dev/null || true
wait 2>/dev/null || true

# ----------------------------------------------------------
# Automatic Analysis
# ----------------------------------------------------------
export RAW_DIR="$RESULT_DIR"
export PROCESSED_DIR="$PROCESSED_DIR"

bash scripts/run-analysis.sh websocket experiment-b1-hpa-churn

echo "=============================================="
echo " Experiment-B1 Completed"
echo "=============================================="

kind delete cluster --name "$CLUSTER_NAME"