#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
# Experiment-A MQTT: HPA Baseline — 3 HPA Failure Modes
#
# Phase 1 (0–240s):  Scale-up Blindness
#   → 1000 clients, HPA at 1 replica, ~400 refused
#
# Phase 2 (240–420s): No Connection-Aware Redistribution
#   → Manual scale to 3 replicas, 300 new clients
#   → Old connections pinned to pod-1, no redistribution
#
# Phase 3 (420–600s): Violent Disconnection
#   → Scale back to 1, pods 2+3 killed, connections drop
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$PROJECT_ROOT"

source "$SCRIPT_DIR/config.env"
source "$PROJECT_ROOT/experiments/mqtt/mqtt_preflight.sh"

echo "══════════════════════════════════════════════════"
echo " Experiment-A MQTT: HPA Baseline (3 Failure Modes)"
echo "══════════════════════════════════════════════════"

# ── Stage 0: Pre-flight ──────────────────────────────────────────
run_preflight "$CLUSTER_NAME"
archive_previous_results "$RESULT_DIR" "experiment-a-mqtt" "$PROJECT_ROOT"

# ── Stage 1: Infrastructure ──────────────────────────────────────
echo ""
echo "── Stage 1: Infrastructure ──────────────────────"
setup_cluster "$CLUSTER_NAME" "$PROJECT_ROOT"
deploy_metrics_server
deploy_prometheus "$PROJECT_ROOT"

trap "full_cleanup '$CLUSTER_NAME'" EXIT

# ── Stage 2: Build & Load Images ────────────────────────────────
echo ""
echo "── Stage 2: Build & Load Images ─────────────────"
build_and_load_images "$PROJECT_ROOT" "$CLUSTER_NAME" \
  "mqtt-broker:latest" "$PROJECT_ROOT/workloads/mqtt/app" \
  "mqtt-loadgen:latest" "$PROJECT_ROOT/load-generator/mqtt-client"

# ── Stage 3: Deploy Workloads ────────────────────────────────────
echo ""
echo "── Stage 3: Deploy Workloads ────────────────────"
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/deployment.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/service.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/hpa.yml"
kubectl wait --for=condition=ready pod -l app=mqtt-broker --timeout=300s
log_ok "Broker + HPA deployed (1 replica, CPU target 50%)"

# Verify Prometheus is scraping before starting data collection
start_port_forward
wait_for_prometheus_scrape

# ── Stage 4: Data Collection + Load Generation ──────────────────
echo ""
echo "── Stage 4: Data Collection + Load ──────────────"
start_all_collectors "$RESULT_DIR" "hpa"
start_memory_collector "$RESULT_DIR"

# ┌──────────────────────────────────────────────────────────────┐
# │  PHASE 1: Scale-up Blindness (0–${PHASE1_DURATION}s)        │
# │  1000 clients → HPA stays at 1 → ~40% connections refused   │
# │  Memory grows, CPU stays at 2–3m                            │
# └──────────────────────────────────────────────────────────────┘
echo ""
log_info "▶ PHASE 1: Scale-up Blindness — ramping ${PHASE1_CLIENTS} clients"
echo "PHASE1_START $(date +%s)" >> "$RESULT_DIR/phases.log"

kubectl run mqtt-loadgen-p1 \
  --image=mqtt-loadgen:latest \
  --restart=Never \
  --image-pull-policy=Never \
  --env="BROKER_HOST=mqtt-service" \
  --env="CLIENTS=$PHASE1_CLIENTS" \
  --env="RAMP_SECONDS=$PHASE1_RAMP_SECONDS" \
  --env="PING_INTERVAL=$PING_INTERVAL"

log_ok "Phase 1 load generator running — waiting ${PHASE1_DURATION}s"
sleep "$PHASE1_DURATION"

kubectl logs mqtt-loadgen-p1 >> "$RESULT_DIR/loadgen-phase1.log" 2>/dev/null || true
echo "PHASE1_END $(date +%s)" >> "$RESULT_DIR/phases.log"

# ┌──────────────────────────────────────────────────────────────┐
# │  PHASE 2: No Connection-Aware Redistribution (240–420s)     │
# │  Delete HPA, manually scale to 3 replicas                  │
# │  Start 300 new clients → they go to pods 2+3               │
# │  Old connections stay pinned to pod-1                       │
# └──────────────────────────────────────────────────────────────┘
echo ""
log_info "▶ PHASE 2: No Redistribution — scaling to ${PHASE2_REPLICAS} replicas"
echo "PHASE2_START $(date +%s)" >> "$RESULT_DIR/phases.log"

# Delete HPA so it doesn't fight us, keep the deployment at 3 replicas
kubectl delete hpa mqtt-hpa --ignore-not-found
kubectl scale deployment mqtt-broker --replicas="$PHASE2_REPLICAS"
log_info "Waiting for new pods to be ready..."
kubectl wait --for=condition=ready pod -l app=mqtt-broker --timeout=120s
log_ok "Scaled to $PHASE2_REPLICAS replicas"

# Start new batch of clients — these get round-robined to pods 2+3
kubectl run mqtt-loadgen-p2 \
  --image=mqtt-loadgen:latest \
  --restart=Never \
  --image-pull-policy=Never \
  --env="BROKER_HOST=mqtt-service" \
  --env="CLIENTS=$PHASE2_CLIENTS" \
  --env="RAMP_SECONDS=$PHASE2_RAMP_SECONDS" \
  --env="PING_INTERVAL=$PING_INTERVAL"

log_ok "Phase 2 load generator running — waiting ${PHASE2_DURATION}s"
sleep "$PHASE2_DURATION"

kubectl logs mqtt-loadgen-p2 >> "$RESULT_DIR/loadgen-phase2.log" 2>/dev/null || true
echo "PHASE2_END $(date +%s)" >> "$RESULT_DIR/phases.log"

# ┌──────────────────────────────────────────────────────────────┐
# │  PHASE 3: Violent Disconnection (420–600s)                  │
# │  Scale back to 1 replica — kills pods 2+3                  │
# │  ~300 connections drop instantly (no drain)                 │
# │  Re-enable HPA to show its continued blindness             │
# └──────────────────────────────────────────────────────────────┘
echo ""
log_info "▶ PHASE 3: Violent Disconnection — scaling back to 1"
echo "PHASE3_START $(date +%s)" >> "$RESULT_DIR/phases.log"

# Scale down — this kills pods 2 and 3, severing all their connections
kubectl scale deployment mqtt-broker --replicas=1
log_info "Scaled to 1 replica — connections on killed pods are DROPPED"

# Re-enable HPA for the rest of the experiment
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/hpa.yml"
log_ok "HPA re-enabled — observing for ${PHASE3_DURATION}s"
sleep "$PHASE3_DURATION"

# Kill loadgen pods
kubectl delete pod mqtt-loadgen-p1 --ignore-not-found 2>/dev/null || true
kubectl delete pod mqtt-loadgen-p2 --ignore-not-found 2>/dev/null || true
kubectl logs mqtt-loadgen-p1 >> "$RESULT_DIR/loadgen-phase1-final.log" 2>/dev/null || true
kubectl logs mqtt-loadgen-p2 >> "$RESULT_DIR/loadgen-phase2-final.log" 2>/dev/null || true
echo "PHASE3_END $(date +%s)" >> "$RESULT_DIR/phases.log"

# ── Stage 5: Cleanup & Summary ──────────────────────────────────
echo ""
echo "── Stage 5: Cleanup & Summary ───────────────────"
print_result_summary "$RESULT_DIR"

log_ok "Experiment-A MQTT complete — 3 HPA failure modes captured"
log_info "Results in $RESULT_DIR"
log_info "Run: python3 analysis/mqtt/parse_logs_mqtt.py $RESULT_DIR $RESULT_DIR/out.csv"
# trap EXIT handles: kill bg procs, delete loadgen pod, delete cluster
