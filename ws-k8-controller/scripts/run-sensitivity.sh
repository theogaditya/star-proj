#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  Parameterized two-cycle restorm run -- Paper 2 Sensitivity Analysis.
#  Varies one StatefulAutoscaler / infrastructure parameter per invocation.
#
#  Usage:
#    run-sensitivity.sh [--cooldown N] [--target N] [--scrape N] [--step N]
#                       [--gap N] [--clients N] [--sweep NAME] [--tag NAME]
#                       [--reuse-cluster] [--keep-cluster]
#
#  Defaults: cooldown=120s target=100 scrape=15s step=2 gap=90s clients=800
#  Results : results/raw/websocket/sensitivity/<tag>/  (+ params.json)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

COOLDOWN=120; TARGET=100; SCRAPE=15; STEP=2; GAP=90; CLIENTS=800
SWEEP="manual"; TAG=""; REUSE=0; KEEP=0
CYCLE1_DURATION=150; CYCLE2_DURATION=150; SAMPLE_INTERVAL=5

while [ $# -gt 0 ]; do
  case "$1" in
    --cooldown) COOLDOWN=$2; shift 2;;
    --target)   TARGET=$2;   shift 2;;
    --scrape)   SCRAPE=$2;   shift 2;;
    --step)     STEP=$2;     shift 2;;
    --gap)      GAP=$2;      shift 2;;
    --clients)  CLIENTS=$2;  shift 2;;
    --sweep)    SWEEP=$2;    shift 2;;
    --tag)      TAG=$2;      shift 2;;
    --reuse-cluster) REUSE=1; shift;;
    --keep-cluster)  KEEP=1;  shift;;
    *) echo "Unknown argument: $1"; exit 1;;
  esac
done

EXPECTED=$(( (CLIENTS + TARGET - 1) / TARGET ))
MAXR=$(( EXPECTED + 2 ))
FINAL_DROP_DURATION=$(( COOLDOWN + 150 ))
[ -z "$TAG" ] && TAG="${SWEEP}_cd${COOLDOWN}_T${TARGET}_s${SCRAPE}_st${STEP}_g${GAP}_c${CLIENTS}"

CLUSTER_NAME="stateful-sens"
RESULT_DIR="$PROJECT_ROOT/results/raw/websocket/sensitivity/$TAG"
CONTROLLER_DIR="$PROJECT_ROOT/controller"

log() { echo "[$(date '+%H:%M:%S')] [$TAG] $1"; }

CPU_PID=""; REPLICA_PID=""; PROM_COLLECT_PID=""; PROM_PID=""; POD_PID=""
cleanup() {
  kill ${CPU_PID:-} ${REPLICA_PID:-} ${PROM_COLLECT_PID:-} ${POD_PID:-} ${PROM_PID:-} 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT

rm -rf "$RESULT_DIR"; mkdir -p "$RESULT_DIR"

# ---------- 1. Cluster (fresh or reused) ----------
if [ "$REUSE" = "1" ] && kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  log "Reusing existing cluster '$CLUSTER_NAME' -- resetting workload state."
  kubectl delete statefulautoscaler websocket-autoscaler --ignore-not-found 2>/dev/null || true
  kubectl delete job websocket-loadgen --ignore-not-found
else
  log "Creating fresh Kind cluster '$CLUSTER_NAME'."
  kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
  kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
      "--cert-dir=/tmp","--secure-port=10250",
      "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
      "--kubelet-use-node-status-port","--metric-resolution=15s",
      "--kubelet-insecure-tls"]}]'
  kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s

  log "Building and loading images..."
  docker build -t websocket-server-instrumented:latest "$PROJECT_ROOT/workloads/websocket/app-instrumented"
  kind load docker-image websocket-server-instrumented:latest --name "$CLUSTER_NAME"
  docker build -t websocket-loadgen:latest "$PROJECT_ROOT/load-generator/websocket-client"
  kind load docker-image websocket-loadgen:latest --name "$CLUSTER_NAME"
  ( cd "$CONTROLLER_DIR" && make docker-build IMG=controller:latest )
  kind load docker-image controller:latest --name "$CLUSTER_NAME"

  ( cd "$CONTROLLER_DIR" && make install && make deploy IMG=controller:latest )
  kubectl -n controller-system rollout status deployment/controller-controller-manager --timeout=300s
fi

# ---------- 2. Prometheus with the requested scrape interval ----------
kubectl apply -f monitoring/prometheus/namespace.yaml
kubectl apply -f monitoring/prometheus/rbac.yaml
sed "s/scrape_interval: 15s/scrape_interval: ${SCRAPE}s/; s/evaluation_interval: 15s/evaluation_interval: ${SCRAPE}s/" \
  monitoring/prometheus/configmap.yaml | kubectl apply -f -
kubectl apply -f monitoring/prometheus/deployment.yaml
kubectl apply -f monitoring/prometheus/service.yaml
kubectl -n monitoring rollout restart deployment/prometheus
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
log "Prometheus ready (scrape_interval=${SCRAPE}s)."

# ---------- 3. Workload ----------
kubectl apply -f workloads/websocket/k8s/deployment-experiment-c.yml
kubectl apply -f workloads/websocket/k8s/service.yml
kubectl scale deployment websocket-server --replicas=2
kubectl wait --for=condition=ready pod -l app=websocket-server --timeout=180s
log "WebSocket server ready (2 replicas)."

# ---------- 4. StatefulAutoscaler CR ----------
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
  maxReplicas: $MAXR
  targetConnectionsPerPod: $TARGET
  maxScaleUpStep: 3
  maxScaleDownStep: $STEP
  scaleUpCooldownSeconds: 10
  scaleDownCooldownSeconds: $COOLDOWN
  drain:
    enabled: false
    timeoutSeconds: 60
    maxConcurrentDrains: 1
EOF
log "CR applied: T=$TARGET step=$STEP cooldown=${COOLDOWN}s maxReplicas=$MAXR (expected=$EXPECTED)"

# ---------- 5. Prometheus port-forward + scrape wait ----------
kubectl -n monitoring port-forward svc/prometheus 9090:9090 >/dev/null 2>&1 &
PROM_PID=$!
until curl -s http://localhost:9090/-/ready >/dev/null 2>&1; do sleep 2; done
PROM_WAIT=0
until curl -s "http://localhost:9090/api/v1/query?query=active_connections" | grep -q '"result":\[{'; do
  [ "$PROM_WAIT" -ge 180 ] && { log "WARNING: no active_connections after 180s"; break; }
  sleep 10; PROM_WAIT=$((PROM_WAIT + 10))
done
log "Prometheus is scraping websocket pods."

# ---------- 6. Record run parameters ----------
cat > "$RESULT_DIR/params.json" <<EOF
{
  "tag": "$TAG", "sweep": "$SWEEP",
  "cooldown": $COOLDOWN, "target": $TARGET, "scrape": $SCRAPE, "step": $STEP,
  "gap": $GAP, "clients": $CLIENTS,
  "min_replicas": 2, "max_replicas": $MAXR, "expected_replicas": $EXPECTED,
  "cycle1": $CYCLE1_DURATION, "cycle2": $CYCLE2_DURATION, "final_drop": $FINAL_DROP_DURATION
}
EOF

# ---------- 7. Collectors ----------
(
  while true; do
    R=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")
    [ -z "$R" ] && R=0
    echo "$(date +%s),$R" >> "$RESULT_DIR/replicas.log"
    sleep "$SAMPLE_INTERVAL"
  done
) & REPLICA_PID=$!
(
  while true; do
    M=$(kubectl top pods -l app=websocket-server --no-headers 2>/dev/null || true)
    [ -n "$M" ] && { echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"; echo "$M" >> "$RESULT_DIR/cpu.log"; }
    sleep "$SAMPLE_INTERVAL"
  done
) & CPU_PID=$!
(
  while true; do
    echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
    kubectl get pods -l app=websocket-server -o wide >> "$RESULT_DIR/pods.log" 2>/dev/null || true
    sleep "$SAMPLE_INTERVAL"
  done
) & POD_PID=$!
echo "timestamp,active_connections" > "$RESULT_DIR/prometheus_dump.csv"
(
  set +e
  while true; do
    V=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // 0')
    echo "$(date +%s),$V" >> "$RESULT_DIR/prometheus_dump.csv"
    sleep "$SAMPLE_INTERVAL"
  done
) & PROM_COLLECT_PID=$!
log "Collectors started."
sleep 15

# ---------- 8. Load phases ----------
apply_loadgen() {
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
          args: ["ws://websocket-service:8765", "$CLIENTS", "120"]
EOF
}

phase() { echo "$(date +%s),$1" >> "$RESULT_DIR/phase.log"; log "PHASE: $1"; }

watch_for() {
  local DURATION=$1 LABEL=$2 ELAPSED=0
  while [ "$ELAPSED" -lt "$DURATION" ]; do
    local R C
    R=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "?")
    C=$(curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq -r '.data.result[0].value[1] // "?"' 2>/dev/null || echo "?")
    log "  [$LABEL +${ELAPSED}s] replicas=$R connections=$C"
    sleep 15; ELAPSED=$((ELAPSED + 15))
  done
}

phase CYCLE_1;    apply_loadgen;                                   watch_for "$CYCLE1_DURATION" CYCLE1
phase DROP_1;     kubectl delete job websocket-loadgen --ignore-not-found; watch_for "$GAP" DROP1
phase CYCLE_2;    apply_loadgen;                                   watch_for "$CYCLE2_DURATION" CYCLE2
phase FINAL_DROP; kubectl delete job websocket-loadgen --ignore-not-found

ELAPSED=0
while [ "$ELAPSED" -lt "$FINAL_DROP_DURATION" ]; do
  R=$(kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "?")
  log "  [FINAL_DROP +${ELAPSED}s] replicas=$R"
  if [ "$R" = "2" ] && [ "$ELAPSED" -ge "$COOLDOWN" ]; then
    log "  Scaled down to minReplicas after cooldown."; sleep 30; break
  fi
  sleep 15; ELAPSED=$((ELAPSED + 15))
done

# ---------- 9. Teardown ----------
cleanup
trap - EXIT
if [ "$KEEP" = "0" ]; then
  kind delete cluster --name "$CLUSTER_NAME"
  log "Cluster deleted."
else
  log "Cluster kept for next run (--keep-cluster)."
fi
log "Run complete. Raw results: $RESULT_DIR"
