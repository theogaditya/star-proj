#!/usr/bin/env bash
# ==============================================================================
#  run-experiment-c-scale-multi.sh
#
#  Runs the full Experiment-C scalability sweep (800 / 1600 / 3200 clients)
#  N times to produce replicable multi-run data.
#
#  Result layout
#  ─────────────
#  Individual run (overwritten each iteration, then moved):
#    results/raw/websocket/experiment-c-scale/           ← raw logs per client count
#    results/processed/websocket/experiment-c-scale/     ← parsed CSV + plots
#    results/tar/experiment-c-scale_run_<i>_<ts>.tgz    ← per-run archive
#
#  Persistent multi-run store:
#    results/raw/websocket/multi/experiment-c-scale/run_<i>/
#    results/processed/websocket/multi/experiment-c-scale/run_<i>/
#
#  After all runs, aggregate_scale.py is called to produce:
#    results/processed/websocket/multi/experiment-c-scale/aggregate_stats.csv
#
#  Usage
#  ─────
#    N=3 ./scripts/run-experiment-c-scale-multi.sh
#    N=5 CLIENTS_LIST="800 1600 3200" ./scripts/run-experiment-c-scale-multi.sh
#    CLEAN_MULTI=1 N=3 ./scripts/run-experiment-c-scale-multi.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

N="${N:-3}"
CLIENTS_LIST="${CLIENTS_LIST:-800 1600 3200}"

EXPERIMENT_LABEL="experiment-c-scale"

RAW_BASE="$PROJECT_ROOT/results/raw/websocket"
PROC_BASE="$PROJECT_ROOT/results/processed/websocket"
TAR_BASE="$PROJECT_ROOT/results/tar"

# Where individual runs land (reset each iteration)
RAW_SINGLE="$RAW_BASE/$EXPERIMENT_LABEL"
PROC_SINGLE="$PROC_BASE/$EXPERIMENT_LABEL"

# Where the multi-run store lives
MULTI_RAW="$RAW_BASE/multi/$EXPERIMENT_LABEL"
MULTI_PROC="$PROC_BASE/multi/$EXPERIMENT_LABEL"

mkdir -p "$TAR_BASE" "$MULTI_RAW" "$MULTI_PROC"

# Optionally wipe previous multi store for a completely fresh batch
if [ "${CLEAN_MULTI:-0}" = "1" ]; then
  echo "[multi] CLEAN_MULTI=1 → removing previous multi-run data"
  rm -rf "$MULTI_RAW" "$MULTI_PROC"
  mkdir -p "$MULTI_RAW" "$MULTI_PROC"
fi

log() { echo "[$(date '+%H:%M:%S')] [scale-multi] $1"; }

echo "============================================================"
echo "  Experiment-C Scalability Multi-Run"
echo "  Runs:    $N"
echo "  Clients: $CLIENTS_LIST"
echo "  Multi → $MULTI_PROC/run_<i>"
echo "============================================================"

for i in $(seq 1 "$N"); do
  echo ""
  echo "──────────────────────────────────────────────────────────"
  log "Starting run $i / $N"
  echo "──────────────────────────────────────────────────────────"

  # ── 1. Clean single-run directories so this run starts fresh ──
  rm -rf "$RAW_SINGLE" "$PROC_SINGLE"
  mkdir -p "$RAW_SINGLE" "$PROC_SINGLE"

  # ── 2. Run the full scale sweep (800 → 1600 → 3200 clients) ──
  #
  #  run-experiment-c-scale.sh:
  #    • iterates over CLIENTS_LIST via run-sensitivity.sh
  #    • raw results land in results/raw/websocket/sensitivity/<tag>/
  #    • analysis (parse_sensitivity.py + plot_scalability.py) writes
  #      results/processed/websocket/sensitivity/ and
  #      results/processed/websocket/scalability/
  #
  #  We capture all of that by pointing the scale script's analysis
  #  outputs into our per-run processed dir via env vars, then move
  #  everything afterwards.
  CLIENTS_LIST="$CLIENTS_LIST" \
    SCALE_RAW_OUT="$RAW_SINGLE" \
    SCALE_PROC_OUT="$PROC_SINGLE" \
    bash "$SCRIPT_DIR/run-experiment-c-scale.sh"

  log "Run $i raw sweep complete."

  # ── 3. Copy sensitivity + scalability sub-results into the single dir ──
  #    parse_sensitivity and plot_scalability write to fixed paths inside
  #    results/processed/websocket/; copy them so the single dir is self-contained.
  SENS_PROC="$PROC_BASE/sensitivity"
  SCAL_PROC="$PROC_BASE/scalability"

  [ -d "$SENS_PROC" ] && cp -r "$SENS_PROC" "$PROC_SINGLE/sensitivity"
  [ -d "$SCAL_PROC" ] && cp -r "$SCAL_PROC" "$PROC_SINGLE/scalability"

  # ── 4. Archive this run's individual results ──
  TS=$(date +%Y%m%d_%H%M%S)
  ARCHIVE_NAME="${EXPERIMENT_LABEL}_run_${i}_${TS}.tgz"
  tar -czf "$TAR_BASE/$ARCHIVE_NAME" \
    -C "$RAW_BASE"  "$EXPERIMENT_LABEL" \
    -C "$PROC_BASE" "$EXPERIMENT_LABEL" 2>/dev/null || true
  log "Run $i archived → results/tar/$ARCHIVE_NAME"

  # ── 5. Move this run into the multi-run store ──
  rm -rf "$MULTI_RAW/run_${i}" "$MULTI_PROC/run_${i}"
  mkdir -p "$MULTI_RAW/run_${i}" "$MULTI_PROC/run_${i}"

  # Move raw logs
  if [ -d "$RAW_SINGLE" ]; then
    find "$RAW_SINGLE" -maxdepth 1 -mindepth 1 \
      -exec mv {} "$MULTI_RAW/run_${i}/" \;
    log "Raw results saved → $MULTI_RAW/run_${i}"
  fi

  # Move processed results
  if [ -d "$PROC_SINGLE" ]; then
    find "$PROC_SINGLE" -maxdepth 1 -mindepth 1 \
      -exec mv {} "$MULTI_PROC/run_${i}/" \;
    log "Processed results saved → $MULTI_PROC/run_${i}"
  fi

  log "Run $i / $N complete."
done

# ── 6. Aggregate across all runs ──
echo ""
echo "============================================================"
log "Aggregating multi-run scalability data …"
echo "============================================================"

python3 "$PROJECT_ROOT/analysis/scalability/aggregate_scale.py" \
  --multi-proc-dir "$MULTI_PROC" \
  --out "$MULTI_PROC/aggregate_stats.csv" || true

echo ""
echo "============================================================"
echo "  All $N runs complete."
echo ""
echo "  Per-run archives : results/tar/${EXPERIMENT_LABEL}_run_*"
echo "  Multi-run raw    : $MULTI_RAW"
echo "  Multi-run proc   : $MULTI_PROC"
echo "  Aggregate stats  : $MULTI_PROC/aggregate_stats.csv"
echo "============================================================"
