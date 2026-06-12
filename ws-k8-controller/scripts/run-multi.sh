#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run-multi.sh  — Run any experiment script N times and collect replicates.
#
# Usage:
#   EXPERIMENT=c N=5 ./scripts/run-multi.sh
#   EXPERIMENT=d N=3 ./scripts/run-multi.sh
#
# What it does:
#   1. Runs scripts/run-experiment-${EXPERIMENT}.sh N times.
#   2. After each run moves raw + processed results into a centralized
#      `multi/<experiment>/run_<i>` folder so consecutive runs don't clobber each other.
#
# Environment variables:
#   EXPERIMENT  single letter/tag that selects the run script (default: c)
#   N           number of repetitions (default: 5)
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

EXPERIMENT="${EXPERIMENT:-c}"
N="${N:-5}"
RUN_SCRIPT="$SCRIPT_DIR/run-experiment-${EXPERIMENT}.sh"

if [ ! -f "$RUN_SCRIPT" ]; then
    echo "ERROR: $RUN_SCRIPT not found."
    exit 1
fi

# Determine experiment name from the run script (same logic the script uses)
EXPERIMENT_NAME=$(grep -E '^EXPERIMENT_NAME=' "$RUN_SCRIPT" | head -1 | cut -d= -f2 | tr -d '"')
if [ -z "$EXPERIMENT_NAME" ]; then
    echo "ERROR: Could not determine EXPERIMENT_NAME from $RUN_SCRIPT"
    exit 1
fi

RAW_BASE="$PROJECT_ROOT/results/raw/websocket"
PROC_BASE="$PROJECT_ROOT/results/processed/websocket"
# New layout: group multi-run results under a central 'multi' folder so that
# runs are organized as:
#   results/raw/websocket/multi/<experiment>/run_<i>
#   results/processed/websocket/multi/<experiment>/run_<i>
MULTI_RAW="$RAW_BASE/multi/$EXPERIMENT_NAME"
MULTI_PROC="$PROC_BASE/multi/$EXPERIMENT_NAME"

mkdir -p "$MULTI_RAW" "$MULTI_PROC"

# If CLEAN_MULTI=1, wipe only the multi/ subdirs to start a fresh batch.
if [ "${CLEAN_MULTI:-0}" = "1" ]; then
    echo "CLEAN_MULTI=1 -> removing previous multi-run folders"
    rm -rf "$MULTI_RAW" "$MULTI_PROC"
    echo "  Removed $MULTI_RAW"
    echo "  Removed $MULTI_PROC"
fi

echo "=========================================="
echo "  Multi-run: experiment=${EXPERIMENT}  N=${N}"
echo "  Script:    $RUN_SCRIPT"
echo "  Experiment name: $EXPERIMENT_NAME"
echo "  Results → results/raw/websocket/$EXPERIMENT_NAME/multi/run_<i>"
echo "=========================================="

for i in $(seq 1 "$N"); do
    echo ""
    echo "-------- Run $i / $N --------"

    # Ensure multi dirs exist before the run so they survive across iterations.
    # (MULTI_RAW/MULTI_PROC are nested inside the experiment dir, so the run
    #  script must not blow them away — hence MULTI_RUN=1 guards the parse scripts.)
    mkdir -p "$MULTI_RAW" "$MULTI_PROC"

    # Run the experiment. MULTI_RUN=1 prevents parse scripts from deleting PROCESSED_DIR.
    MULTI_RUN=1 bash "$RUN_SCRIPT"

    # Archive raw results — move every entry in the experiment dir *except*
    # the 'multi' subfolder (when present) into run_i. We keep a single
    # centralized multi directory at results/raw/websocket/multi/<experiment>.
    SRC_RAW="$RAW_BASE/$EXPERIMENT_NAME"
    if [ -d "$SRC_RAW" ]; then
        rm -rf "$MULTI_RAW/run_${i}"
        mkdir -p "$MULTI_RAW/run_${i}"
        find "$SRC_RAW" -maxdepth 1 -mindepth 1 ! -name "multi" \
            -exec mv {} "$MULTI_RAW/run_${i}/" \;
        echo "  Saved raw   → $MULTI_RAW/run_${i}"
    else
        echo "  WARNING: raw dir $SRC_RAW not found after run $i"
    fi

    # Archive processed results — same approach. Processed 'multi' dirs are
    # centralized under results/processed/websocket/multi/<experiment>.
    SRC_PROC="$PROC_BASE/$EXPERIMENT_NAME"
    if [ -d "$SRC_PROC" ]; then
        rm -rf "$MULTI_PROC/run_${i}"
        mkdir -p "$MULTI_PROC/run_${i}"
        find "$SRC_PROC" -maxdepth 1 -mindepth 1 ! -name "multi" \
            -exec mv {} "$MULTI_PROC/run_${i}/" \;
        echo "  Saved proc  → $MULTI_PROC/run_${i}"
    else
        echo "  WARNING: processed dir $SRC_PROC not found after run $i"
    fi

    echo "  Run $i complete."
done

echo ""
echo "=========================================="
echo "All $N runs complete. Multi-run results saved under:"
echo "  raw:  $MULTI_RAW"
echo "  proc: $MULTI_PROC"
echo "=========================================="
