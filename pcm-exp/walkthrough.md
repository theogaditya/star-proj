# PCM HPA Experiment — Walkthrough

## What was built

Implemented the **Prometheus Custom Metrics (PCM)** HPA experiment from the paper, matching the structure of the existing `krm-experiment/`.

### File Structure (21 files)

```
pcm-exp/
├── PCM-EXPERIMENT.md              ← Run instructions
├── walkthrough.md                 ← This file
├── run-experiment.sh              ← Main orchestrator (3 scenarios)
├── setup_env.sh                   ← Setup Python venv
├── dashboard.json                 ← Grafana dashboard JSON
├── app/
│   ├── server.py                  ← CPU-intensive HTTP server with /metrics
│   └── Dockerfile                 ← Container image
├── manifests/
│   ├── kind-config.yaml           ← 1 control-plane + 3 workers
│   ├── cpu-app.yaml               ← Deployment + Service (NodePort 30080)
│   ├── prometheus.yaml            ← Prometheus (Persistent Data)
│   ├── prometheus-adapter.yaml    ← Adapter with PromQL rules
│   ├── grafana.yaml               ← Grafana with pre-loaded Dashboard
│   ├── hpa-pcm-cpu.yaml           ← HPA: custom metric only
│   ├── hpa-pcm-http.yaml          ← HPA: HTTP rate only (PCM-H)
│   ├── hpa-pcm-cpu-http.yaml      ← HPA: CPU + HTTP combined (PCM-CH)
│   └── install-prometheus.sh      ← Installs Stack (Prometheus + Grafana + Adapter)
├── collect-scripts/
│   ├── collect_*.sh               ← Data collection scripts (CPU, HPA, Pods, Prom)
└── workload/
│   └── run-load-phase.sh          ← HTTP load via 'hey' (high/low phases)
└── analysis/
    └── analysis.py                ← 7 plot types + derived metrics table
```

### Key Features

1.  **3 Experiment Scenarios** (from the paper):
    | Scenario | Metric(s) | Paper Section |
    |----------|-----------|---------------|
    | `pcm-cpu` | HTTP request rate (proxy for CPU) | 5.2.3, 5.2.4 |
    | `pcm-h` | HTTP request rate only | 5.2.6 |
    | `pcm-ch` | CPU (Resource) + HTTP rate (Custom) | 5.2.6 |

2.  **Data Persistence**:
    - Prometheus checks a PersistentVolumeClaim (`prometheus-pvc`) to retain data across experiment restarts.
    - This allows continuous visualization of all 5 experiment cycles.

3.  **Visualization**:
    - **Grafana Dashboard**: Pre-loaded with HTTP Rate and Pod Status panels.
    - **Analysis Script**: Generates publication-ready plots (`analysis.py`).

## Validation

- ✅ All shell scripts pass `bash -n` syntax check
- ✅ Python files compile successfully (`.pyc` generated)
- ✅ All source files present in correct structure

### Dashboard Access

**1. Grafana (Recommended)**
View the experiment live:
```bash
# Forward port 3000
kubectl -n monitoring port-forward svc/grafana 3000:3000 --address 0.0.0.0
```
- URL: `http://localhost:3000`
- Login: `admin` / `admin`
- Dashboard: **PCM Experiment Dashboard** (pre-loaded)

**2. Prometheus UI**
Debug raw metrics:
```bash
kubectl -n monitoring port-forward svc/prometheus 9091:9090 --address 0.0.0.0
```
- URL: `http://localhost:9091/graph`

**Debugging Recording:**
![Prometheus UI Check](/home/abhas/.gemini/antigravity/brain/5c6e2b81-a452-4599-be0f-a4ba62704061/prometheus_ui_debug_1771306439587.webp)

## How to Run

See [PCM-EXPERIMENT.md](file:///home/abhas/node/STAR/star-proj/pcm-exp/PCM-EXPERIMENT.md) for detailed instructions.

**Quick Start:**
```bash
cd pcm-exp
kind create cluster --config manifests/kind-config.yaml
# Build the image if you dont have it
# docker build -t cpu-http-app:latest app/
kind load docker-image cpu-http-app:latest

source venv/bin/activate

# Ensure all scripts are executable
chmod +x run-experiment.sh manifests/install-prometheus.sh workload/run-load-phase.sh collect-scripts/*.sh

# Run the full suite!
./run-experiment.sh

# Analyze results
bash setup_env.sh
./pcm-exp/venv/bin/python3 analysis/analysis.py
```
