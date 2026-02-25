#!/usr/bin/env bash
# install-prometheus.sh — Install Prometheus + Adapter into the Kind cluster
# Usage: ./install-prometheus.sh [scrape_interval]
# Default scrape_interval: 60s

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRAPE_INTERVAL="${1:-60s}"

echo "============================================================"
echo " Installing Prometheus Stack (scrape_interval=${SCRAPE_INTERVAL})"
echo "============================================================"

# 1. Generate prometheus.yaml with the correct scrape interval
echo "[*] Generating Prometheus config with scrape_interval=${SCRAPE_INTERVAL}..."
sed "s/__SCRAPE_INTERVAL__/${SCRAPE_INTERVAL}/g" \
    "$SCRIPT_DIR/prometheus.yaml" > /tmp/prometheus-rendered.yaml

# 2. Apply Prometheus manifests
echo "[*] Applying Prometheus manifests..."
kubectl apply -f /tmp/prometheus-rendered.yaml

# 3. Wait for Prometheus to be ready
echo "[*] Waiting for Prometheus deployment..."
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
echo "[✓] Prometheus is ready"

# 4. Install metrics-server (needed for PCM-CH which also uses Resource CPU)
echo "[*] Checking metrics-server..."
if ! kubectl -n kube-system get deployment metrics-server &>/dev/null; then
    echo "[*] Installing metrics-server..."
    kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
    # Patch for Kind (insecure TLS)
    kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
      {"op": "replace", "path": "/spec/template/spec/containers/0/args", "value": [
        "--cert-dir=/tmp",
        "--secure-port=10250",
        "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
        "--kubelet-use-node-status-port",
        "--metric-resolution=60s",
        "--kubelet-insecure-tls"
      ]}
    ]'
    echo "[*] Waiting for metrics-server..."
    kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
    echo "[✓] metrics-server is ready"
else
    echo "[✓] metrics-server already installed"
fi

# 5. Apply Prometheus Adapter
echo "[*] Applying Prometheus Adapter..."
kubectl apply -f "$SCRIPT_DIR/prometheus-adapter.yaml"

echo "[*] Waiting for Prometheus Adapter deployment..."
kubectl -n monitoring rollout status deployment/prometheus-adapter --timeout=300s
echo "[✓] Prometheus Adapter is ready"

# 6. Verify custom metrics API is registered
echo "[*] Waiting for custom metrics API to be available (up to 90s)..."
for i in $(seq 1 18); do
    if kubectl get --raw /apis/custom.metrics.k8s.io/v1beta1 &>/dev/null; then
        echo "[✓] custom.metrics.k8s.io API is available"
        break
    fi
    echo "    ...not ready yet (attempt $i/18)"
    sleep 5
done

# 7. Summary
echo ""
echo "============================================================"
echo " Prometheus Stack Installed Successfully"
echo "  - Prometheus: monitoring/prometheus (port 9090)"
echo "  - Adapter:    monitoring/prometheus-adapter"
echo "  - Scrape interval: ${SCRAPE_INTERVAL}"
echo "============================================================"
