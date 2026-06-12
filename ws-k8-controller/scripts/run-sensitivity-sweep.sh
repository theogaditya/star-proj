#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  Full sensitivity sweep for Paper 2 (Section: Sensitivity Analysis).
#  Runs the four professor-requested sweeps, then parses + plots results.
#
#  Test 1: cooldown in {30,60,90,120,150,180}s, each with a gap 30s shorter
#          and 30s longer than the cooldown  -> safe-zone boundary plot
#  Test 2: targetConnectionsPerPod T in {50,100,150,200}
#  Test 3: Prometheus scrape interval in {15,30,60}s
#  Test 4: maxScaleDownStep in {1,2,3,4}
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN="$SCRIPT_DIR/run-sensitivity.sh"
COMMON="--reuse-cluster --keep-cluster"

echo "=== Sensitivity sweep started: $(date) ==="

# --- Test 1: cooldown window / safe-zone boundary ---
for CD in 30 60 90 120 150 180; do
  SHORT=$((CD - 30)); [ "$SHORT" -lt 15 ] && SHORT=15
  LONG=$((CD + 30))
  for GAP in "$SHORT" "$LONG"; do
    bash "$RUN" --sweep cooldown --cooldown "$CD" --gap "$GAP" $COMMON
  done
done

# --- Test 2: target connections per pod (T) ---
for T in 50 100 150 200; do
  bash "$RUN" --sweep target --target "$T" $COMMON
done

# --- Test 3: Prometheus scrape interval ---
for S in 15 30 60; do
  bash "$RUN" --sweep scrape --scrape "$S" $COMMON
done

# --- Test 4: maxScaleDownStep ---
for ST in 1 2 3 4; do
  bash "$RUN" --sweep step --step "$ST" $COMMON
done

kind delete cluster --name stateful-sens 2>/dev/null || true

echo "=== All sweeps done. Running analysis. ==="
python3 "$PROJECT_ROOT/analysis/sensitivity/parse_sensitivity.py"
python3 "$PROJECT_ROOT/analysis/sensitivity/plot_sensitivity.py"
echo "=== Sensitivity sweep complete: $(date) ==="
echo "Tables: results/processed/websocket/sensitivity/"
echo "Plots : results/processed/websocket/sensitivity/plots/"
