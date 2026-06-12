#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CLUSTER_NAME="stateful-exp"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/experiment-a-hpa"
DURATION=300
SCALE_DOWN_BUFFER=300
CLIENTS=800

mkdir -p "$RESULT_DIR"

# ------------------------------------------------
# Archive Previous Results
# ------------------------------------------------
if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR")" ]; then
  ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
  mkdir -p "$ARCHIVE_DIR"
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  ARCHIVE_NAME="experiment-a-hpa_${TIMESTAMP}.tgz"
  tar -czf "$ARCHIVE_DIR/$ARCHIVE_NAME" -C "$RESULT_DIR" .
  rm -rf "$RESULT_DIR"
  mkdir -p "$RESULT_DIR"
  echo "[*] Archived previous results"
fi

echo "=============================================="
echo " Experiment-A: Fresh Cluster Baseline Run"
echo "=============================================="

# Delete + Create Cluster
kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

# Install Metrics Server
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
echo "[✓] Metrics ready"

# Install Prometheus
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/namespace.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/rbac.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/configmap.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/deployment.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/service.yaml"
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s

# Build + Load Images
cd "$PROJECT_ROOT/workloads/websocket/app"
docker build -t websocket-server:latest .
kind load docker-image websocket-server:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT/load-generator/websocket-client"
docker build -t websocket-loadgen:latest .
kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"

cd "$PROJECT_ROOT"

# Deploy Workload
kubectl apply -f "$PROJECT_ROOT/workloads/websocket/k8s/deployment.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/websocket/k8s/service.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/websocket/k8s/hpa.yml"
kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s

# Start Collectors
echo "[*] Starting collectors..."

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

(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/hpa.log"
  kubectl get hpa websocket-hpa >> "$RESULT_DIR/hpa.log"
  sleep 5
done
) &
HPA_PID=$!

(
while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
  kubectl get pods >> "$RESULT_DIR/pods.log"
  sleep 5
done
) &
PODS_PID=$!

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

# Start Load
echo "[*] Starting load..."
kubectl apply -f "$PROJECT_ROOT/load-generator/websocket-client/k8s/job.yaml"
sleep "$DURATION"

# Stop Load
echo "[*] Stopping load..."
kubectl delete job websocket-loadgen --ignore-not-found

# Wait for scale-down to 2
echo "[*] Waiting for scale-down..."
while true; do
  REPLICAS=$(kubectl get deployment websocket-server -o jsonpath='{.status.replicas}')
  if [ "$REPLICAS" -eq 2 ]; then
    echo "[✓] Reached 2 replicas"
    break
  fi
  sleep 5
done

echo "[*] Waiting 30 seconds to log stable 2 replicas..."
sleep 30

# Stop Collectors
echo "[*] Stopping collectors..."
kill $CPU_PID 2>/dev/null || true
kill $HPA_PID 2>/dev/null || true
kill $PODS_PID 2>/dev/null || true
kill $CONN_PID 2>/dev/null || true
kill $PF_PID 2>/dev/null || true
wait 2>/dev/null || true

# Dump Prometheus Data
echo "[*] Dumping Prometheus data..."
kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PID=$!
until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do sleep 2; done

END=$(date +%s)
START=$((END - DURATION - SCALE_DOWN_BUFFER))

curl -s "http://localhost:9090/api/v1/query_range?query=active_connections&start=${START}&end=${END}&step=5" \
> "$RESULT_DIR/prometheus_dump.json"

kill $PROM_PID 2>/dev/null || true

echo "=============================================="
echo " Experiment-A Completed Successfully"
echo "=============================================="

kind delete cluster --name "$CLUSTER_NAME"