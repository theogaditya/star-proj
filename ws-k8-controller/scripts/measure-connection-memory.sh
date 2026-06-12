#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  Per-WebSocket-connection memory measurement (Paper 2: T-parameter
#  derivation, Section 4). Holds a single server pod at fixed connection
#  levels, samples pod memory via metrics-server, and fits a linear model:
#      T = floor( safety_factor * memory_limit / memory_per_connection )
#
#  Usage: ./measure-connection-memory.sh [--limit-mib 512] [--keep-cluster]
#  Output: results/raw/websocket/memory/memory_per_connection.csv
#          results/processed/websocket/memory/t_derivation.json
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

LIMIT_MIB=512; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --limit-mib) LIMIT_MIB=$2; shift 2;;
    --keep-cluster) KEEP=1; shift;;
    *) echo "Unknown argument: $1"; exit 1;;
  esac
done

CLUSTER_NAME="stateful-mem"
RAW_DIR="$PROJECT_ROOT/results/raw/websocket/memory"
CSV="$RAW_DIR/memory_per_connection.csv"
LEVELS="0 100 200 400 800"

log() { echo "[$(date '+%H:%M:%S')] $1"; }
mkdir -p "$RAW_DIR"
echo "connections,pods,total_memory_mib" > "$CSV"

# ---------- Cluster ----------
if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
      "--cert-dir=/tmp","--secure-port=10250",
      "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
      "--kubelet-use-node-status-port","--metric-resolution=15s",
      "--kubelet-insecure-tls"]}]'
  kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
  until kubectl top pods >/dev/null 2>&1; do sleep 5; done

  docker build -t websocket-server-instrumented:latest "$PROJECT_ROOT/workloads/websocket/app-instrumented"
  kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"
  docker build -t websocket-loadgen:latest "$PROJECT_ROOT/load-generator/websocket-client"
  kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"
fi

# ---------- Single-replica server, NO autoscaler ----------
kubectl apply -f workloads/websocket/k8s/deployment-experiment-c.yml
kubectl apply -f workloads/websocket/k8s/service.yml
kubectl scale deployment websocket-server --replicas=1
kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s
log "Server ready (1 replica, no autoscaler)."
sleep 30  # metrics-server settle

sample_memory() {
  # 5 samples, 15s apart; print the median total MiB and pod count
  local samples=() pods=1
  for _ in 1 2 3 4 5; do
    local line
    line=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null \
      | awk '{gsub(/Mi/,"",$3); total+=$3; n+=1} END{print (total?total:0)","(n?n:1)}')
    samples+=("${line%%,*}"); pods="${line##*,}"
    sleep 15
  done
  local median
  median=$(printf '%s\n' "${samples[@]}" | sort -n | sed -n '3p')
  echo "$pods,$median"
}

for N in $LEVELS; do
  log "--- Level: $N connections ---"
  if [ "$N" -gt 0 ]; then
    cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: websocket-loadgen
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: websocket-loadgen
          image: websocket-loadgen:latest
          imagePullPolicy: IfNotPresent
          command: ["python", "client.py"]
          args: ["ws://websocket-service:8765", "$N", "0"]
          env:
            - name: RAMP_UP_DURATION
              value: "30"
EOF
    log "Waiting 120s for ramp + memory settle..."
    sleep 120
  fi
  PM=$(sample_memory)
  PODS="${PM%%,*}"; MEM="${PM##*,}"
  echo "$N,$PODS,$MEM" >> "$CSV"
  log "Recorded: connections=$N pods=$PODS total_memory=${MEM}MiB"
  kubectl delete job websocket-loadgen --ignore-not-found
  sleep 45  # connection drain
done

if [ "$KEEP" = "0" ]; then kind delete cluster --name "$CLUSTER_NAME"; fi

log "Computing per-connection memory and recommended T..."
python3 "$PROJECT_ROOT/analysis/memory/compute_t_derivation.py" --csv "$CSV" --limit-mib "$LIMIT_MIB"
