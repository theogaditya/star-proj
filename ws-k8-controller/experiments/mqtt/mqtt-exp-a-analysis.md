# MQTT Experiment A — HPA Failure Analysis

## Core Thesis

> **HPA's CPU-based autoscaling is fundamentally broken for MQTT (and all stateful/persistent-connection protocols).** This experiment demonstrates three distinct failure modes in a single 10-minute run.

---

## Experiment Design: 3 Phases, 3 Failures

### Phase 1: Scale-up Blindness (0–240s)

**Setup:** 1 replica, HPA active (`cpu: 50%` target, request `100m`), ramp 1000 MQTT clients over 60s.

**What happens:** MQTT is an efficient keepalive protocol — 1000 persistent TCP connections generate only **2–5 millicores** of CPU. The HPA threshold is `50m`. Since actual CPU never exceeds 5m, HPA **never scales up**.

**Observable result:**
- ~340 of 1000 clients connect; **660 clients (66%) are refused**
- HPA reports `cpu: 4%` → `21%` during ramp → settles at `5%` — all well below 50%
- Replica count stays at **1 the entire phase**
- Memory grows from 20Mi to 32Mi while CPU stays flat

### Phase 2: No Connection-Aware Redistribution (240–420s)

**Setup:** Delete HPA, manually scale deployment to 3 replicas, start 300 new clients.

**What happens:** Kubernetes `Service` round-robins **new** connections across all 3 pods. But the **existing 340 connections stay pinned** to pod-1. Pods 2 and 3 get ~150 new clients each. Pod-1 still carries the full original load.

**Observable result:**
- Pod-1: ~340 connections (old) — overloaded
- Pod-2: ~150 connections (new) — underloaded
- Pod-3: ~150 connections (new) — underloaded
- Total connections: ~640 (but unevenly distributed)

### Phase 3: Violent Disconnection (420–600s)

**Setup:** Scale deployment back to 1 replica (simulating what HPA would do when it sees low average CPU), re-enable HPA.

**What happens:** Kubernetes terminates pods 2 and 3. All connections on those pods are **instantly severed**. There is no drain, no graceful migration, no warning. The ~300 connections on pods 2+3 drop to zero.

**Observable result:**
- Total connections drop from ~640 to ~340 in one step
- 300 clients immediately disconnected
- HPA re-enabled; continues to see low CPU; stays at 1 replica

---

## Summary: Three HPA Failures in One Experiment

| Phase | Failure | Root Cause | Observable Effect |
|-------|---------|-----------|-------------------|
| 1 | **Scale-up blindness** | CPU ≈ 3m, HPA target 50m | 660/1000 clients refused (66%) |
| 2 | **No redistribution** | TCP connections are pinned | Pod-1: 340, Pod-2: 150, Pod-3: 150 |
| 3 | **Violent disconnection** | No drain on scale-down | 300 connections severed instantly |

All three are solved by the **StatefulAutoscaler**: it watches `active_connections` instead of CPU, scales proactively, and uses `/drain` for graceful scale-down.

---

## Data Collected

| Log File | What It Contains |
|----------|-----------------|
| `pods.log` | Timestamp + running pod list (every 5s) |
| `hpa.log` | HPA status with CPU % (every 5s) |
| `cpu.log` | `kubectl top pods` output: CPU + memory (every 5s) |
| `memory.log` | Total memory in MiB across all broker pods (every 5s) |
| `active_connections.log` | Total connections from Prometheus `sum(active_connections)` |
| `perpod_connections.log` | Per-pod connection JSON from Prometheus `active_connections` |
| `phases.log` | Phase start/end timestamps |
| `loadgen-phase1.log` | Phase 1 load generator output |
| `loadgen-phase2.log` | Phase 2 load generator output |

## Parsed Output

| File | Contents |
|------|---------|
| `out.csv` | `time_s,replicas,active_connections,cpu_millicores,memory_mi` |
| `out_perpod.csv` | `time_s,conn_<pod1>,conn_<pod2>,conn_<pod3>` |
| `out_phases.json` | `{"PHASE1_START": 0, "PHASE1_END": 240, ...}` |

## Plot

3-panel plot (`plot.png`):
- **Panel 1 (top):** Active connections (blue) + replica count (red step) + phase markers
- **Panel 2 (middle):** CPU millicores (green) + memory MiB (orange)
- **Panel 3 (bottom):** Per-pod connection distribution (one line per pod)

---

## Commands

```bash
# Run the experiment
bash scripts/run-experiment-mqtt-a.sh

# Parse logs
python3 analysis/mqtt/parse_logs_mqtt.py \
  results/raw/mqtt/experiment-a-hpa \
  results/raw/mqtt/experiment-a-hpa/out.csv

# Generate plot
python3 analysis/mqtt/plot_experiment_mqtt.py \
  --csv   results/raw/mqtt/experiment-a-hpa/out.csv \
  --title "Exp-A: HPA Baseline — 3 Failure Modes (MQTT)" \
  --out   results/raw/mqtt/experiment-a-hpa/plot.png
```
