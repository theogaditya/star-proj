# PCM HPA Experiment — Prometheus Custom Metrics

> Reproduces the **Prometheus Custom Metrics (PCM)** experiments from:
> *"Horizontal Pod Autoscaling in Kubernetes for Elastic Container Orchestration"* (Nguyen et al., 2020)

---

## Prerequisites

```bash
# Required tools
kind version        # Kubernetes-in-Docker
kubectl version     # Kubernetes CLI
docker version      # Container runtime
hey -h              # HTTP load generator (go install github.com/rakyll/hey@latest)
python3 --version   # For analysis (needs pandas, matplotlib)
```

Install `hey` if not present:

```bash
# Option A: Go install
go install github.com/rakyll/hey@latest

# Option B: Download binary
curl -Lo hey https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64
chmod +x hey && sudo mv hey /usr/local/bin/
```

Install Python dependencies for analysis:

```bash
pip3 install pandas matplotlib
```

---

## Quick Start (Full Automated Run)

The fastest way to run all 3 experiments:

```bash
cd pcm-exp

# Step 1: Create Kind cluster
kind delete cluster 2>/dev/null; kind create cluster --config manifests/kind-config.yaml

# Step 2: Build and load the CPU-HTTP app image
docker build -t cpu-http-app:latest app/
kind load docker-image cpu-http-app:latest

# Step 3: Run all experiments (PCM-CPU with 3 scraping periods + PCM-H + PCM-CH)
chmod +x run-experiment.sh manifests/install-prometheus.sh workload/run-load-phase.sh collect-scripts/*.sh
./run-experiment.sh

# Step 4: Analyze results
python3 analysis/analysis.py

# Step 5: Cleanup
kind delete cluster
```

> **Estimated time**: ~90 minutes for all scenarios (5 experiments × 5 cycles × 200s each + warm-up)

---

## Step-by-Step Guide

### 1. Create Kind Cluster

```bash
kind delete cluster 2>/dev/null
kind create cluster --config manifests/kind-config.yaml
kubectl get nodes
```

### 2. Build & Load Application Image

```bash
docker build -t cpu-http-app:latest app/
kind load docker-image cpu-http-app:latest
```

### 3. Make Scripts Executable

```bash
chmod +x run-experiment.sh
chmod +x manifests/install-prometheus.sh
chmod +x workload/run-load-phase.sh
chmod +x collect-scripts/*.sh
```

### 4. Install Prometheus Stack

```bash
# Install with default 60s scrape interval
bash manifests/install-prometheus.sh 60s

# Verify Prometheus is running
kubectl -n monitoring get pods
kubectl -n monitoring port-forward svc/prometheus 9090:9090 &
curl -s http://localhost:9090/api/v1/status/config | python3 -m json.tool
```

### 5. Deploy Test Application

```bash
kubectl apply -f manifests/cpu-app.yaml
kubectl get pods -l app=cpu-app
kubectl get svc cpu-app-svc

# Verify the app responds
NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
curl http://${NODE_IP}:30080/
curl http://${NODE_IP}:30080/metrics
```

### 6. Verify Custom Metrics

```bash
# Wait ~60s for Prometheus to scrape, then check
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1" | python3 -m json.tool
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/default/pods/*/http_requests_per_second" | python3 -m json.tool
```

### 7. Run Individual Experiments

```bash
# Run only PCM-CPU (scraping period comparison)
./run-experiment.sh pcm-cpu

# Run only PCM-H (HTTP rate only)
./run-experiment.sh pcm-h

# Run only PCM-CH (CPU + HTTP combined)
./run-experiment.sh pcm-ch

# Run all
./run-experiment.sh pcm-cpu pcm-h pcm-ch
```

### 8. Analyze Results

```bash
python3 analysis/analysis.py results/
```

Output plots are saved to `results/plots/`.

### 9. Cleanup

```bash
kind delete cluster
```

---

## Experiment Scenarios

| Scenario | HPA Metric(s) | Paper Section | Description |
|----------|---------------|---------------|-------------|
| **PCM-CPU** | `http_requests_per_second` (custom) | 5.2.3, 5.2.4 | Same metric type as KRM but via Prometheus pipeline; tested with 60s/30s/15s scraping |
| **PCM-H** | `http_requests_per_second` (custom) | 5.2.6 | HTTP request rate only |
| **PCM-CH** | CPU (resource) + `http_requests_per_second` (custom) | 5.2.6 | Multi-metric: scales up if either exceeds threshold |

---

## Results Structure

```
results/
├── pcm-cpu/
│   ├── 60s/              # Scraping period = 60s
│   ├── 30s/              # Scraping period = 30s
│   └── 15s/              # Scraping period = 15s
├── pcm-h/                # HTTP-only HPA
├── pcm-ch/               # CPU + HTTP combined HPA
└── plots/                # Generated analysis plots
    ├── replicas_over_time.png
    ├── cpu_over_time.png
    ├── http_rate_over_time.png
    ├── desired_vs_current.png
    ├── efficiency_scatter.png
    ├── pcm_h_vs_pcm_ch.png
    ├── scraping_period_comparison.png
    └── summary_table.csv
```

Each experiment directory contains:
- `pod_cpu.csv` — per-pod CPU/memory samples (5s intervals)
- `hpa_log.csv` — HPA replicas + metric values (5s intervals)
- `podcount.csv` — total pod count over time
- `prometheus_metrics.csv` — raw Prometheus query results
- `prometheus.log` — Prometheus server logs
- `phases.log` — workload phase timestamps
- `hey_*.csv` — HTTP load generator output per phase
- `hpa_final.yaml` — HPA state snapshot at end
- `hpa_describe.txt` — HPA description at end
- `final_events.log` — Kubernetes events at end

---

## Tuning Parameters

Edit these in `run-experiment.sh`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `NUM_CYCLES` | 5 | Number of high/low workload cycles |
| `HIGH_SECS` | 100 | Duration of high-load phase (seconds) |
| `LOW_SECS` | 100 | Duration of low-load phase (seconds) |

Edit these in `workload/run-load-phase.sh` or via env vars:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `HIGH_RPS` | 50 | Requests/sec during high phase |
| `LOW_RPS` | 15 | Requests/sec during low phase |
| `HIGH_CONCURRENCY` | 20 | Concurrent connections during high phase |
| `LOW_CONCURRENCY` | 5 | Concurrent connections during low phase |
