#!/usr/bin/env bash
# Experiment-C MQTT: Idle Connections — HPA vs STAR comparison
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$PROJECT_ROOT"

source "$SCRIPT_DIR/config.env"
source "$PROJECT_ROOT/experiments/mqtt/mqtt_preflight.sh"

# ──────────────────────────────────────────────────────────────────
# run_phase: self-contained function — creates cluster, runs experiment, cleans up
# ──────────────────────────────────────────────────────────────────
run_phase() {
  local SCALER=$1        # "hpa" or "star"
  local CLUSTER_NAME=$2
  local RESULT_DIR=$3

  # Reset bg pids for this phase
  BG_PIDS=()

  echo ""
  echo "======================================================"
  echo " Experiment-C MQTT: Idle Connections — Scaler=$SCALER"
  echo "======================================================"

  # ── Stage 0: Pre-flight ────────────────────────────────────────
  run_preflight "$CLUSTER_NAME"
  archive_previous_results "$RESULT_DIR" "exp-c-${SCALER}" "$PROJECT_ROOT"

  # ── Stage 1: Infrastructure ────────────────────────────────────
  echo ""
  echo "── Stage 1: Infrastructure ──────────────────────"
  setup_cluster "$CLUSTER_NAME" "$PROJECT_ROOT"
  deploy_metrics_server
  deploy_prometheus "$PROJECT_ROOT"

  # ── Stage 2: Build & Load Images ──────────────────────────────
  echo ""
  echo "── Stage 2: Build & Load Images ─────────────────"
  build_and_load_images "$PROJECT_ROOT" "$CLUSTER_NAME" \
    "mqtt-broker:latest" "$PROJECT_ROOT/workloads/mqtt/app" \
    "mqtt-loadgen:latest" "$PROJECT_ROOT/load-generator/mqtt-client"

  # ── Stage 3: Deploy Workloads ──────────────────────────────────
  echo ""
  echo "── Stage 3: Deploy Workloads ────────────────────"
  kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/deployment.yml"

  # HPA phase: pre-warm 2 replicas to show scale-down behaviour clearly
  # STAR phase: start at default (1) — the controller owns replica count
  if [ "$SCALER" = "hpa" ]; then
    kubectl patch deployment mqtt-broker -p '{"spec":{"replicas":2}}'
  fi

  kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/service.yml"

  if [ "$SCALER" = "hpa" ]; then
    kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/hpa.yml"
  else
    build_and_load_controller "$PROJECT_ROOT" "$CLUSTER_NAME"
    deploy_star_controller "$PROJECT_ROOT"
    kubectl apply -f "$SCRIPT_DIR/statefulautoscaler.yaml"
  fi

  kubectl wait --for=condition=ready pod -l app=mqtt-broker --timeout=300s
  log_ok "Broker + $SCALER deployed"

  # Verify Prometheus scrape
  start_port_forward
  wait_for_prometheus_scrape

  # ── Stage 4: Data Collection + Load Generation ────────────────
  echo ""
  echo "── Stage 4: Data Collection + Load ──────────────"
  start_all_collectors "$RESULT_DIR" "$SCALER"

  kubectl run mqtt-loadgen \
    --image=mqtt-loadgen:latest \
    --restart=Never \
    --image-pull-policy=Never \
    --env="BROKER_HOST=mqtt-service" \
    --env="CLIENTS=$CLIENTS" \
    --env="RAMP_SECONDS=$RAMP_SECONDS" \
    --env="PING_INTERVAL=$PING_INTERVAL"

  log_ok "Load generator started — running for ${DURATION}s"
  sleep "$DURATION"

  kubectl logs mqtt-loadgen >> "$RESULT_DIR/loadgen.log" 2>/dev/null || true
  kubectl delete pod mqtt-loadgen --ignore-not-found

  # ── Stage 5: Cleanup & Summary ────────────────────────────────
  echo ""
  echo "── Stage 5: Cleanup & Summary ───────────────────"
  print_result_summary "$RESULT_DIR"
  cleanup_background
  cleanup_cluster "$CLUSTER_NAME"
  log_ok "$SCALER phase complete. Results in $RESULT_DIR"
}

# ══════════════════════════════════════════════════════════════════
# Run both phases sequentially
# ══════════════════════════════════════════════════════════════════
run_phase "hpa"  "$CLUSTER_NAME_HPA"  "$RESULT_DIR_HPA"
run_phase "star" "$CLUSTER_NAME_STAR" "$RESULT_DIR_STAR"

echo ""
log_ok "Experiment-C MQTT complete (both phases)."
