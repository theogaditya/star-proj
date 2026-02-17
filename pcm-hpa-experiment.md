# PCM HPA Reproduction (Kind) — Step-by-step (paper-style)

> Reproducible experiment plan to reproduce the **Prometheus Custom Metrics (PCM)** portion of the paper using a Kind cluster.
> This file contains **only** the experiment steps, commands, data collection, and the report/analysis plan — ready to give to an automation tool.

---

## 1. Goal

Reproduce Prometheus-based Horizontal Pod Autoscaler behavior on a **Kind** cluster and measure how the **Prometheus Scrape Interval** (`scrape_interval`) affects scaling responsiveness ("The Staircase Effect"). Also evaluate the efficacy of HTTP-based scaling (`pcm-h`) and Hybrid High-Water Mark scaling (`pcm-ch`).

---

## 2. Assumptions & notes

* Target: local **Kind** cluster (Kubernetes-in-Docker).
* Pipeline: **Prometheus** (Store) + **Prometheus Adapter** (API Adapter) + **Metrics Server** (Hybrid backup).
* HPA sync period is 15s (default); Prometheus `scrape_interval` controls metric freshness.
* Tests use:
    *   **PCM-CPU**: Scaling on CPU metrics routed via Prometheus (isolates scrape latency).
    *   **PCM-H**: Scaling on custom `http_requests_per_second` (direct traffic signal).
    *   **PCM-CH**: Hybrid scaling (`max(CPU, HTTP)`).

---

## 3. Files to produce

* `kind-config.yaml` — Kind cluster config.
* `install-prometheus.sh` — Script to install Prometheus/Adapter and patch `scrape_interval`.
* `cpu-app.yaml` — Workload deployment (Python HTTP server + CPU stressor).
* `hpa-pcm-*.yaml` — HPA manifests for different scenarios.
* `collect-scripts/collect_*` — Scripts to poll Pod metrics, HPA status, and Prometheus query results.
* `run-experiment.sh` — Orchestrator for full suite.
* `analysis/analysis.py` — Python script to plot Time-to-Scale and Latency effects.

---

## 4. Exact manifests and commands (9 steps)

### Step 0 — Clean start

```bash
./full-cleanup.sh
# OR manually:
kind delete cluster || true
rm -rf results/
```

### Step 1 — Create Kind cluster & Build Image

`kind-config.yaml` (Same as KRM).

```bash
kind create cluster --config manifests/kind-config.yaml
docker build -t cpu-http-app:latest app/
kind load docker-image cpu-http-app:latest
```

### Step 2 — Install Prometheus Environment

Use the helper script which installs:
1.  **Prometheus Server** (ConfigMap with variable `scrape_interval`).
2.  **Prometheus Adapter** (Rules to expose `http_requests_per_second`).
3.  **Metrics Server** (For baseline CPU fallback).

```bash
# Example: Install with 15s scrape interval
bash manifests/install-prometheus.sh "15s"
```

### Step 3 — Deploy / Reset Workload

`cpu-app.yaml` must include Prometheus annotations:
```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8000"
```

```bash
kubectl apply -f manifests/cpu-app.yaml
kubectl wait --for=condition=ready pod -l app=cpu-app
```

### Step 4 — Verify Custom Metrics

Ensure the Adapter is serving the custom metric:

```bash
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/default/pods/*/http_requests_per_second" | jq .
```

### Step 5 — Apply HPA Scenario

**Scenario A: PCM-CPU (Latency Test)**
```yaml
type: Pods
metric:
  name: cpu_usage (via prometheus adapter rules)
  target: 60 (average)
```

**Scenario B: PCM-H (HTTP Rate)**
```yaml
type: Pods
metric:
  name: http_requests_per_second
  target: 3 (average value)
```

Apply:
```bash
kubectl apply -f manifests/hpa-pcm-cpu.yaml
```

### Step 6 — Run Workload (HEY)

Use `hey` to generate HTTP traffic, which triggers both internal CPU load (in the app) and HTTP count metrics (in Prometheus).

```bash
# 5 cycles of: 100s HIGH (50 RPS) / 100s LOW (2 RPS)
bash workload/run-load-phase.sh results/pcm-cpu/15s 5 100 100
```

### Step 7 — Observe / Monitor

```bash
# Watch HPA reaction
kubectl get hpa cpu-hpa -w
```

### Step 8 — Orchestrated Experiments

The `run-experiment.sh` script automates the permutation:

1.  **PCM-CPU-60s**: Prom scrape 60s. Expect "Staircase" response.
2.  **PCM-CPU-30s**: Prom scrape 30s. Reduced alaising.
3.  **PCM-CPU-15s**: Prom scrape 15s. "Nyquist Match" with HPA sync.
4.  **PCM-H**: Scaling purely on Request Rate.
5.  **PCM-CH**: Hybrid scaling (High-Water Mark).

### Step 9 — Clean up

```bash
./full-cleanup.sh
```

---

## 5. What to collect

### A — Poll HPA Status (Controller perspective)
Records what the HPA *sees* and *does*.
```bash
# collect-scripts/collect_hpa_status.sh
kubectl get hpa cpu-hpa -o jsonpath='{.status.currentReplicas},{.status.desiredReplicas}...'
```

### B — Poll Prometheus Stats (Source of truth)
Records what Prometheus *has stored* (detects lag).
```bash
# collect-scripts/collect_prometheus_metrics.sh
curl -s "http://localhost:9090/api/v1/query?query=http_requests_rate"
```

### C — Pod CPU (Real usage)
```bash
# collect-scripts/collect_pod_cpu.sh
kubectl top pods
```

---

## 6. Workload Pattern

**Tool**: `hey` (HTTP Load Generator).
**Pattern**: Square wave.
*   **High Phase**: 50 req/sec (triggers ~100m CPU/pod → Scale Up).
*   **Low Phase**: 2 req/sec (Base load → Scale Down).
*   **Duration**: 100s per phase (allows stabilization).

---

## 7. Analysis & Metrics

### Key Plots
1.  **Replicas Over Time**: Compare `pcm-cpu-60s` (laggy) vs `pcm-cpu-15s` (smooth) vs `pcm-h` (aggressive).
2.  **Scraping Period Comparison**: Visualize the "Staircase Effect" where HPA waits for new Prom data.
3.  **HPA Decision Latency**: Difference between `desiredReplicas` timestamp and actual scale event.

---

## 8. Report Structure

1.  **Methodology**: Defining the Prometheus Scrape Interval impact.
2.  **Results**:
    *   **Staircase Effect**: Evidence of 60s scrape causing delays.
    *   **Traffic vs CPU**: Comparing `pcm-h` (leading indicator) vs `pcm-cpu` (lagging indicator).
    *   **Cost**: Trade-off of storing high-resolution metrics.
3.  **Discussion**: Architectural push (Metrics Server) vs pull (Prometheus) trade-offs.

---

## 9. Tips

*   **Port Forwarding**: Ensure `kubectl port-forward svc/prometheus 9090:9090` is active for collectors.
*   **Adapter Latency**: The Prometheus Adapter itself has a discovery interval (set to 5s in our config) which adds a small fixed delay.
*   **Aliasing**: 60s scrape vs 15s HPA sync creates "beats" where HPA acts on old data 3 out of 4 times.

