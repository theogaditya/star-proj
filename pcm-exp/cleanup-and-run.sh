#!/usr/bin/env bash
# cleanup-and-run.sh
# One-shot script to reset the environment and run the full PCM experiment suite (60s, 30s, 15s)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== PCM Experiment Reset & Run ==="

# 1. Kill any existing experiment processes
echo "[*] Killing previous processes..."
pkill -f "run-experiment.sh" || true
pkill -f "collect_.*.sh" || true
pkill -f "hey" || true
pkill -f "kubectl .* port-forward" || true

# 2. Clean previous results
echo "[*] Cleaning results directory..."
rm -rf "$SCRIPT_DIR/results/pcm-cpu" "$SCRIPT_DIR/results/pcm-h" "$SCRIPT_DIR/results/pcm-ch"
mkdir -p "$SCRIPT_DIR/results"

# 3. Verify clean state
if [ -d "$SCRIPT_DIR/results/pcm-cpu/60s" ]; then
    echo "[!] Error: failed to clean results directory"
    exit 1
fi
echo "[✓] Environment clean"

# 4. Run the full experiment suite
# This will run pcm-cpu (60s, 30s, 15s), pcm-h, and pcm-ch sequentially
echo "[*] Starting experiment suite (interactive)..."
./run-experiment.sh
