#!/usr/bin/env bash
# metrics-server-patch.sh — Patch metrics-server for Kind + set scraping resolution
# Usage: ./metrics-server-patch.sh [resolution]
# Default resolution: 60s

set -euo pipefail

RESOLUTION="${1:-60s}"

echo "[*] Patching metrics-server → --metric-resolution=$RESOLUTION"

kubectl -n kube-system patch deployment metrics-server --type='json' -p="[
  {\"op\": \"replace\", \"path\": \"/spec/template/spec/containers/0/args\", \"value\": [
    \"--cert-dir=/tmp\",
    \"--secure-port=10250\",
    \"--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname\",
    \"--kubelet-use-node-status-port\",
    \"--metric-resolution=$RESOLUTION\",
    \"--kubelet-insecure-tls\"
  ]}
]"

echo "[*] Restarting metrics-server..."
kubectl -n kube-system rollout restart deployment/metrics-server
kubectl -n kube-system rollout status deployment/metrics-server --timeout=120s
echo "[✓] metrics-server patched and ready (resolution=$RESOLUTION)"
