#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
#  Scalability validation for Paper 2: Experiment C at 800 / 1600 / 3200
#  clients. Reuses run-sensitivity.sh with default controller parameters and
#  sweep tag 'scale'. maxReplicas is auto-derived as ceil(clients/T)+2.
#
#  NOTE: a Kind cluster may hit OS-level TCP limits (ephemeral port range,
#  listen() backlog, net.core.somaxconn) at 1600/3200 clients. The achieved
#  peak connection count is recorded so the ceiling can be documented.
#
#  Usage: CLIENTS_LIST="800 1600 3200" ./run-experiment-c-scale.sh
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN="$SCRIPT_DIR/run-sensitivity.sh"
CLIENTS_LIST="${CLIENTS_LIST:-800 1600 3200}"

echo "=== Scalability runs: clients in [$CLIENTS_LIST] ==="
for C in $CLIENTS_LIST; do
  bash "$RUN" --sweep scale --clients "$C" --reuse-cluster --keep-cluster
done
kind delete cluster --name stateful-sens 2>/dev/null || true

echo "=== Analysis ==="
python3 "$PROJECT_ROOT/analysis/sensitivity/parse_sensitivity.py"
python3 "$PROJECT_ROOT/analysis/scalability/plot_scalability.py"
echo "Done. Plots: results/processed/websocket/scalability/plots/"
