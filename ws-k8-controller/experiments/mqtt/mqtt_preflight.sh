#!/usr/bin/env bash
# mqtt_preflight.sh — Shared pre-flight checks and helper functions for MQTT experiments.
# Source this file from each experiment's run.sh.

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[!]${NC} $*"; }
log_fail() { echo -e "${RED}[✗]${NC} $*"; }
log_info() { echo -e "    $*"; }

# ─── Pre-flight checks ───────────────────────────────────────────
preflight_check_tools() {
  local tools=("docker" "kind" "kubectl" "curl" "python3" "make")
  local missing=()
  for t in "${tools[@]}"; do
    if ! command -v "$t" &>/dev/null; then
      missing+=("$t")
    fi
  done
  if [ ${#missing[@]} -gt 0 ]; then
    log_fail "Missing required tools: ${missing[*]}"
    exit 1
  fi
  log_ok "All required tools present"
}

preflight_check_docker() {
  if ! docker info &>/dev/null; then
    log_fail "Docker daemon is not running"
    exit 1
  fi
  log_ok "Docker daemon is running"
}

preflight_check_memory() {
  local free_mb
  free_mb=$(free -m | awk '/^Mem:/{print $7}')
  if [ "$free_mb" -lt 1500 ]; then
    log_warn "Low memory: ${free_mb}MB available (need ~2GB). Experiment may fail."
  else
    log_ok "Memory OK: ${free_mb}MB available"
  fi
}

preflight_check_cluster_conflict() {
  local cluster_name=$1
  if kind get clusters 2>/dev/null | grep -qx "$cluster_name"; then
    log_warn "Cluster '$cluster_name' already exists — will be deleted"
  fi
}

preflight_check_port() {
  local port=$1
  if ss -tln 2>/dev/null | grep -q ":${port} "; then
    log_warn "Port $port is already in use — Prometheus port-forward may fail"
  fi
}

run_preflight() {
  local cluster_name=$1
  echo ""
  echo "── Pre-flight Checks ──────────────────────────────"
  preflight_check_tools
  preflight_check_docker
  preflight_check_memory
  preflight_check_cluster_conflict "$cluster_name"
  preflight_check_port 9090
  echo "───────────────────────────────────────────────────"
  echo ""
}

# ─── Infrastructure helpers ───────────────────────────────────────

setup_cluster() {
  local cluster_name=$1
  local project_root=$2
  log_info "Creating Kind cluster: $cluster_name"
  kind delete cluster --name "$cluster_name" 2>/dev/null || true
  kind create cluster --name "$cluster_name" --config "$project_root/scripts/kind.yml"
  log_ok "Cluster '$cluster_name' ready"
}

deploy_metrics_server() {
  log_info "Deploying metrics-server..."
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
      "--cert-dir=/tmp","--secure-port=10250",
      "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
      "--kubelet-use-node-status-port","--metric-resolution=15s",
      "--kubelet-insecure-tls"]}]'
  kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
  until kubectl top pods >/dev/null 2>&1; do sleep 5; done
  log_ok "Metrics server ready"
}

deploy_prometheus() {
  local project_root=$1
  log_info "Deploying Prometheus..."
  kubectl apply -f "$project_root/monitoring/prometheus/namespace.yaml"
  kubectl apply -f "$project_root/monitoring/prometheus/rbac.yaml"
  kubectl apply -f "$project_root/monitoring/prometheus/configmap.yaml"
  kubectl apply -f "$project_root/monitoring/prometheus/deployment.yaml"
  kubectl apply -f "$project_root/monitoring/prometheus/service.yaml"
  kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
  log_ok "Prometheus ready"
}

# ─── Image build & load ──────────────────────────────────────────

build_and_load_images() {
  local project_root=$1
  local cluster_name=$2
  shift 2
  # Remaining args: pairs of (image_name, context_dir)

  local tmpdir
  tmpdir=$(mktemp -d)

  log_info "Building images..."
  while [ $# -ge 2 ]; do
    local name="$1"
    local ctx="$2"
    shift 2
    docker build --provenance=false -t "$name" "$ctx"
    docker save "$name" -o "$tmpdir/${name//[:\/]/_}.tar"
    kind load image-archive "$tmpdir/${name//[:\/]/_}.tar" --name "$cluster_name"
    log_ok "Image '$name' loaded into cluster"
  done
  rm -rf "$tmpdir"
}

build_and_load_controller() {
  local project_root=$1
  local cluster_name=$2
  local tmpdir
  tmpdir=$(mktemp -d)

  log_info "Building STAR controller..."
  cd "$project_root/controller"
  IMG=star-controller:latest make docker-build IMG=star-controller:latest 2>&1 | tail -3
  cd "$project_root"

  docker save star-controller:latest -o "$tmpdir/star-controller.tar"
  kind load image-archive "$tmpdir/star-controller.tar" --name "$cluster_name"
  rm -rf "$tmpdir"
  log_ok "STAR controller image loaded"
}

deploy_star_controller() {
  local project_root=$1
  log_info "Deploying STAR controller..."
  cd "$project_root/controller"
  IMG=star-controller:latest make deploy IMG=star-controller:latest
  cd "$project_root"
  kubectl -n controller-system rollout status deployment/controller-controller-manager --timeout=300s
  log_ok "STAR controller deployed"
}

# ─── Prometheus scrape verification ──────────────────────────────

wait_for_prometheus_scrape() {
  log_info "Waiting for Prometheus to scrape active_connections metric..."
  local retries=24  # 24 * 5s = 120s max
  for ((i=1; i<=retries; i++)); do
    local val
    val=$(curl -sf "http://localhost:9090/api/v1/query?query=active_connections" 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d['data']['result']))" 2>/dev/null || echo "0")
    if [ "$val" != "0" ]; then
      log_ok "Prometheus scraping active_connections (found $val target(s))"
      return 0
    fi
    sleep 5
  done
  log_warn "Prometheus has not scraped active_connections after 120s — data may be incomplete"
}

# ─── Data collection helpers ─────────────────────────────────────

# Arrays to track background PIDs for cleanup
BG_PIDS=()

start_pod_collector() {
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/pods.log"
    kubectl get pods -l app=mqtt-broker --no-headers >> "$result_dir/pods.log" 2>/dev/null || true
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_hpa_collector() {
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/hpa.log"
    kubectl get hpa mqtt-hpa --no-headers >> "$result_dir/hpa.log" 2>/dev/null || true
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_autoscaler_collector() {
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/autoscaler.log"
    kubectl get statefulautoscaler mqtt-autoscaler -o wide --no-headers >> "$result_dir/autoscaler.log" 2>/dev/null || true
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_cpu_collector() {
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/cpu.log"
    kubectl top pods -l app=mqtt-broker --no-headers 2>/dev/null >> "$result_dir/cpu.log" || true
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_memory_collector() {
  # Dedicated memory log: extracts MiB from kubectl top pods
  # Format per block:  <timestamp>\n<total_memory_mi>
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/memory.log"
    kubectl top pods -l app=mqtt-broker --no-headers 2>/dev/null \
      | awk '{gsub(/Mi/,"",$3); total+=$3} END{print (total?total:0)}' \
      >> "$result_dir/memory.log" || echo "0" >> "$result_dir/memory.log"
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_connection_collector() {
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/active_connections.log"
    curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); \
        r=d['data']['result']; print(r[0]['value'][1] if r else '0')" \
      >> "$result_dir/active_connections.log" 2>/dev/null || echo "0" >> "$result_dir/active_connections.log"
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_perpod_connection_collector() {
  # Logs per-pod active_connections from Prometheus (one JSON line per sample)
  # Format per block:  <timestamp>\n<json_dict>
  local result_dir=$1
  (while true; do
    echo "$(date +%s)" >> "$result_dir/perpod_connections.log"
    curl -s "http://localhost:9090/api/v1/query?query=active_connections" \
      | python3 -c "
import sys, json
d = json.load(sys.stdin)
out = {}
for r in d.get('data',{}).get('result',[]):
    metric = r.get('metric', {})
    pod = (
        metric.get('pod')
        or metric.get('kubernetes_pod_name')
        or metric.get('instance')
        or 'unknown'
    )
    val = int(float(r['value'][1]))
    out[pod] = val
print(json.dumps(out))
" >> "$result_dir/perpod_connections.log" 2>/dev/null || echo "{}" >> "$result_dir/perpod_connections.log"
    sleep 5
  done) &
  BG_PIDS+=($!)
}

start_port_forward() {
  kubectl port-forward svc/prometheus -n monitoring 9090:9090 >/dev/null 2>&1 &
  BG_PIDS+=($!)
  # Wait until port-forward is actually ready (use query API, not /-/ready)
  for ((i=1; i<=30; i++)); do
    if curl -sf --max-time 2 "http://localhost:9090/api/v1/query?query=up" >/dev/null 2>&1; then
      log_ok "Prometheus port-forward ready"
      return 0
    fi
    sleep 1
  done
  log_warn "Port-forward may not be ready"
}

start_all_collectors() {
  local result_dir=$1
  local scaler_type=$2  # "hpa" or "star"

  start_pod_collector "$result_dir"
  if [ "$scaler_type" = "hpa" ]; then
    start_hpa_collector "$result_dir"
  else
    start_autoscaler_collector "$result_dir"
  fi
  start_cpu_collector "$result_dir"
  start_connection_collector "$result_dir"
  start_perpod_connection_collector "$result_dir"
  log_ok "All data collectors running"
}

# ─── Cleanup ─────────────────────────────────────────────────────

cleanup_background() {
  for pid in "${BG_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  BG_PIDS=()
}

cleanup_cluster() {
  local cluster_name=$1
  log_info "Deleting cluster '$cluster_name'..."
  kind delete cluster --name "$cluster_name" 2>/dev/null || true
  log_ok "Cluster deleted"
}

full_cleanup() {
  local cluster_name=$1
  cleanup_background
  kubectl delete pod mqtt-loadgen --ignore-not-found 2>/dev/null || true
  cleanup_cluster "$cluster_name"
}

# ─── Archive helper ──────────────────────────────────────────────

archive_previous_results() {
  local result_dir=$1
  local archive_name=$2
  local project_root=$3

  if [ -d "$result_dir" ] && [ "$(ls -A "$result_dir" 2>/dev/null)" ]; then
    local archive_dir="$project_root/results/tar"
    mkdir -p "$archive_dir"
    local ts
    ts=$(date +%Y%m%d_%H%M%S)
    tar -czf "$archive_dir/${archive_name}_${ts}.tgz" -C "$result_dir" .
    rm -rf "$result_dir"
    mkdir -p "$result_dir"
    log_info "Archived previous results → ${archive_name}_${ts}.tgz"
  fi
  mkdir -p "$result_dir"
}

# ─── Result summary ─────────────────────────────────────────────

print_result_summary() {
  local result_dir=$1
  echo ""
  echo "── Result Summary ─────────────────────────────────"
  for f in "$result_dir"/*.log; do
    if [ -f "$f" ]; then
      local lines
      lines=$(wc -l < "$f")
      local size
      size=$(du -h "$f" | cut -f1)
      printf "  %-30s %6s lines  %s\n" "$(basename "$f")" "$lines" "$size"
    fi
  done
  echo "───────────────────────────────────────────────────"
}
