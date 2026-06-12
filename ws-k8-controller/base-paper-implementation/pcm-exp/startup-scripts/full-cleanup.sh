#!/usr/bin/env bash
# full-cleanup.sh
# Comprehensive cleanup script to remove all resources created by the PCM experiment project.
# This includes:
# 1. Kind clusters
# 2. Background processes (hey, kubectl port-forward, collection scripts)
# 3. Docker images created for the project
# 4. Results and temporary files

set -euo pipefail

echo "=== PCM Project Full Cleanup ==="

# 1. Kill background processes
echo "[*] Keying experiment processes..."
pkill -f "run-experiment.sh" || true
pkill -f "collect_.*.sh" || true
pkill -f "hey" || true
pkill -f "kubectl .* port-forward" || true
pkill -f "python3 analysis/analysis.py" || true
echo "    ...Processes killed."

# 2. Delete Kind Cluster
echo "[*] Deleting Kind cluster..."
if kind get clusters | grep -q "^kind$"; then
    kind delete cluster --name kind
    echo "    ...Cluster 'kind' deleted."
else
    echo "    ...Cluster 'kind' not found."
fi

# 3. Remove Docker Image
echo "[*] Removing Docker image 'cpu-http-app:latest'..."
if docker images | grep -q "cpu-http-app"; then
    docker rmi cpu-http-app:latest || true
    echo "    ...Image removed."
else
    echo "    ...Image not found."
fi


# 4. Clean Python Environment (Optional, un-comment if you want to delete venv)
# echo "[*] Removing virtual environment..."
# rm -rf venv/

echo "=== Cleanup Complete ==="
echo "The environment is reset. To start over, run './run-experiment.sh' (which will recreate the cluster)."
