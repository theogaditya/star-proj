# MQTT Experiments — Next Steps

## Current Status

> ⚠️ **All experiments need a fresh run.** Previous runs used a broken MQTT broker (`amqtt` crashes on Python 3.11). The broker has been rewritten in pure asyncio — re-run all three experiments to get valid data.

| Exp | Result Dir | Status |
|-----|-----------|--------|
| **A** | `results/raw/mqtt/experiment-a-hpa/` | ❌ Needs re-run (3-phase redesign) |
| **B** | `results/raw/mqtt/experiment-b-stateful/` | ❌ Missing — needs run |
| **C** | `results/raw/mqtt/experiment-c-idle-hpa/` + `...-star/` | ❌ Missing — needs run |

---

## What Are We Testing & Why?

The `StatefulAutoscaler` was proven to work with WebSocket connections. Now we prove it **generalises to MQTT** — a second, industrially important stateful protocol (used by IoT, messaging, etc.).

The core problem: **Kubernetes HPA scales based on CPU. MQTT clients hold persistent TCP connections that use near-zero CPU.** HPA sees low CPU → never scales up → clients are refused. The `StatefulAutoscaler` scales based on `active_connections` — the right signal for stateful workloads.

### The 3 Experiments

| Exp | Name | Scaler | What It Shows | Duration |
|-----|------|--------|---------------|----------|
| **A** | HPA Baseline (3 Failures) | HPA (CPU @ 50%) | Scale-up blindness + No redistribution + Violent disconnection | ~10 min |
| **B** | StatefulAutoscaler (3 Fixes) | StatefulAutoscaler | Fixes all 3 HPA failures: 0 refused + even distribution + graceful drain | ~13 min |
| **C** | Idle Connections | HPA vs StatefulAutoscaler | Side-by-side: HPA tears down idle sessions, StatefulAutoscaler holds them | ~25 min |

---

## Experiment A — 3 HPA Failure Modes (Redesigned)

Experiment A runs 3 phases in a single 10-minute experiment:

| Phase | Time | What | Failure Mode |
|-------|------|------|-------------|
| **1** | 0–240s | Ramp 1000 clients, HPA active, 1 replica | **Scale-up Blindness**: CPU ≈ 3m, HPA threshold 50m, never scales. ~661 clients refused. |
| **2** | 240–420s | Delete HPA, manually scale to 3, start 300 new clients | **No Redistribution**: Old 340 connections stay on pod-1. New clients go to pods 2+3. |
| **3** | 420–600s | Scale back to 1, re-enable HPA | **Violent Disconnection**: Pods 2+3 killed, ~300 connections severed instantly. |

### Plot Output — Exp A (3 panels, red failure labels)

- **Top**: Active connections (blue) + replica count (red step) + phase markers ❌
- **Middle**: CPU millicores (green) + Memory MiB (orange) — shows memory growing while CPU stays flat
- **Bottom**: Per-pod connection distribution — shows imbalance and violent drop

---

## Experiment B — StatefulAutoscaler Fixes (3 Phases)

Experiment B runs the **same 1000-client load as Exp A** but with `StatefulAutoscaler` instead of HPA.
Each phase directly mirrors an Exp A failure and shows it solved.

| Phase | Time | What | Fix Demonstrated |
|-------|------|------|-----------------|
| **1** | 0–300s | 1000 clients, StatefulAutoscaler active | **Fix ❶**: Scales to ~7 pods (target 150 conn/pod). 0 clients refused. |
| **2** | 300–480s | Same clients, steady state observation | **Fix ❷**: Per-pod connections balanced at ~142 each. No overloaded pod. |
| **3** | 480–780s | Kill loadgen, watch graceful drain | **Fix ❸**: Replicas step down 1 at a time with /drain. No sudden connection drop. |

### StatefulAutoscaler Configuration

```yaml
# experiments/mqtt/experiment-b-stateful/statefulautoscaler.yaml
targetConnectionsPerPod: 150   # scale at 150 conn/pod
maxScaleUpStep: 3              # fast scale-up
scaleUpCooldownSeconds: 20     # react quickly to new connections
scaleDownCooldownSeconds: 120  # wait before scale-down + drain
drain:
  enabled: true                # calls /drain before killing a pod
  timeoutSeconds: 45
```

### Plot Output — Exp B (3 panels, green fix labels ✅)

- **Top**: Active connections rising to ~1000 + replicas stepping up to ~7. All clients connected.
- **Middle**: CPU stays near-zero (same as Exp A) — proves StatefulAutoscaler doesn't need CPU signal
- **Bottom**: Per-pod lines all near 150 (target threshold line shown in green)



See: `experiments/mqtt/mqtt-exp-a-analysis.md` for full analysis.

---

## Infrastructure (What Was Fixed)

| Component | Old (Broken) | New (Fixed) |
|-----------|-------------|-------------|
| MQTT Broker | `amqtt 0.11.0` — crashes on Python 3.11 with `CancelledError` | Pure `asyncio` broker — no external MQTT lib |
| Image load | `docker save \| kind load` — pipe corruption | `docker save -o file.tar` + `kind load image-archive file.tar` |
| Namespace ref | `star-controller-system` (wrong) | `controller-system` (correct kustomize output) |
| Post-cleanup | Clusters leaked after Exp A | `kind delete cluster` in all teardowns |
| Pre-flight | None | Tool checks, memory check, port check, Prometheus scrape verify |

---

## How to Run

### Option A — Bash (recommended for debugging)

```bash
cd /home/aditya/cohort/startproj/ws-k8-controller

# Run sequentially — each auto-cleans up its cluster
bash scripts/run-experiment-mqtt-a.sh   # ~10 min (3 phases)
bash scripts/run-experiment-mqtt-b.sh   # ~15 min
bash scripts/run-experiment-mqtt-c.sh   # ~25 min
```

Each script runs through **5 stages** and prints progress:

```
── Pre-flight Checks ─────────────────────────────
[✓] All required tools present
[✓] Docker daemon is running
[✓] Memory OK: 3421MB available
── Stage 1: Infrastructure ──────────────────────
── Stage 2: Build & Load Images ─────────────────
── Stage 3: Deploy Workloads ────────────────────
── Stage 4: Data Collection + Load ──────────────
    ▶ PHASE 1: Scale-up Blindness — ramping 1000 clients
    ▶ PHASE 2: No Redistribution — scaling to 3 replicas
    ▶ PHASE 3: Violent Disconnection — scaling back to 1
── Stage 5: Cleanup & Summary ───────────────────
```

### Option B — Ansible (runs everything + parses + plots)

```bash
# All 3 experiments + parse + plot in one command:
ansible-playbook scripts/ansi/mqtt_all.yml

# Or individually:
ansible-playbook scripts/ansi/mqtt_experiment_a.yml
ansible-playbook scripts/ansi/mqtt_experiment_b.yml
ansible-playbook scripts/ansi/mqtt_experiment_c.yml
```

### Before Running — Pre-flight Check

```bash
# Check memory (need >1.5GB free)
free -h

# Check no leftover clusters
kind get clusters

# Delete any stale ones
kind delete cluster --name mqtt-exp-a
kind delete cluster --name mqtt-exp-b
kind delete cluster --name mqtt-exp-c-hpa
kind delete cluster --name mqtt-exp-c-star
```

---

## Step 4 — Parse Logs & Generate Plots

> Run **after** all experiments complete. If you used `mqtt_all.yml`, this step is already done.

```bash
cd /home/aditya/cohort/startproj/ws-k8-controller

# ── Experiment A (3 failure modes) ───────────────────────────────
python3 analysis/mqtt/parse_logs_mqtt.py \
  results/raw/mqtt/experiment-a-hpa \
  results/raw/mqtt/experiment-a-hpa/out.csv

# Generates: out.csv, out_perpod.csv, out_phases.json

python3 analysis/mqtt/plot_experiment_mqtt.py \
  --csv   results/raw/mqtt/experiment-a-hpa/out.csv \
  --title "Exp-A: HPA Baseline — 3 Failure Modes (MQTT)" \
  --out   results/raw/mqtt/experiment-a-hpa/plot.png

# ── Experiment B ─────────────────────────────────────────────────
python3 analysis/mqtt/parse_logs_mqtt.py \
  results/raw/mqtt/experiment-b-stateful \
  results/raw/mqtt/experiment-b-stateful/out.csv

# Generates: out.csv, out_perpod.csv, out_phases.json

python3 analysis/mqtt/plot_experiment_mqtt.py \
  --csv            results/raw/mqtt/experiment-b-stateful/out.csv \
  --title          "Exp-B: StatefulAutoscaler — 3 HPA Failures Solved (MQTT)" \
  --mode           exp_b \
  --target-per-pod 150 \
  --out            results/raw/mqtt/experiment-b-stateful/plot.png

# ── Experiment C — parse both phases ─────────────────────────────
python3 analysis/mqtt/parse_logs_mqtt.py \
  results/raw/mqtt/experiment-c-idle-hpa \
  results/raw/mqtt/experiment-c-idle-hpa/out.csv

python3 analysis/mqtt/parse_logs_mqtt.py \
  results/raw/mqtt/experiment-c-idle-star \
  results/raw/mqtt/experiment-c-idle-star/out.csv

# ── Experiment C — comparison plot ───────────────────────────────
python3 analysis/mqtt/plot_experiment_mqtt.py \
  --csv-hpa  results/raw/mqtt/experiment-c-idle-hpa/out.csv \
  --csv-star results/raw/mqtt/experiment-c-idle-star/out.csv \
  --out      results/raw/mqtt/experiment-c-comparison.png
```

### CSV Output Format

**Main CSV** (`out.csv`) — 5 columns:
```
time_s,replicas,active_connections,cpu_millicores,memory_mi
0,1,0,2,20
5,1,45,3,22
60,1,339,5,30
...
```

**Per-pod CSV** (`out_perpod.csv`) — one column per pod:
```
time_s,conn_mqtt-broker-xxx,conn_mqtt-broker-yyy,conn_mqtt-broker-zzz
0,0,0,0
240,340,0,0
300,340,150,150
```

**Phases JSON** (`out_phases.json`):
```json
{
  "PHASE1_START": 0,
  "PHASE1_END": 240,
  "PHASE2_START": 240,
  "PHASE2_END": 420,
  "PHASE3_START": 420,
  "PHASE3_END": 600
}
```

---

## What to Look For in Results

### Exp A (3 HPA Failures — the main exhibit)

**Panel 1 (Connections + Replicas):**
- Connections ramp to ~340 and plateau (not 1000) → **660 refused**
- At t=240s: replica count jumps to 3 (manual), connections rise to ~640
- At t=420s: replica drops to 1, connections crash from ~640 to ~340
- Phase marker lines show the 3 distinct failure modes

**Panel 2 (CPU + Memory):**
- CPU flat at 2–5m throughout → **proves CPU is the wrong signal**
- Memory grows linearly with connections → **proves memory exhaustion risk**

**Panel 3 (Per-Pod Connections):**
- Pod-1 line stays high (~340) across all phases → **connections pinned**
- Pods 2+3 only get new clients, never receive old ones → **no redistribution**
- At t=420s: pods 2+3 lines drop to 0 → **violent disconnection**

### Exp B (StatefulAutoscaler — the solution)
- Phase 1: connections → 600, replicas rise to 3–4 (connection-aware)
- Phase 2: connections drop to 150, replicas gracefully drop to 1 (drain works)
- No connection spikes on scale-down

### Exp C (Side-by-side comparison)
- **Left (HPA)**: connections idle → HPA scales down → sessions drop
- **Right (StatefulAutoscaler)**: connections held, replicas stable

---

## Result Files Per Experiment

### Exp A (`experiment-a-hpa/`)
| File | Contents |
|------|---------|
| `pods.log` | Timestamp + pod status every 5s |
| `hpa.log` | HPA metrics every 5s |
| `cpu.log` | `kubectl top` CPU/mem every 5s |
| `memory.log` | Total memory MiB every 5s |
| `active_connections.log` | Prometheus total connections every 5s |
| `perpod_connections.log` | Per-pod connection JSON every 5s |
| `phases.log` | Phase start/end timestamps |
| `loadgen-phase1.log` | Phase 1 load generator output |
| `loadgen-phase2.log` | Phase 2 load generator output |
| `out.csv` | Parsed merged timeseries |
| `out_perpod.csv` | Parsed per-pod connections |
| `out_phases.json` | Phase timestamps (relative) |
| `plot.png` | Final 3-panel graph |

---

## Total Time Estimate

| Task | Time |
|------|------|
| Experiment A (3 phases) | ~10 min |
| Experiment B | ~15 min |
| Experiment C | ~25 min |
| Parse + Plot | ~1 min |
| **Total** | **~51 min** |

---

## Key Source Files

| File | Purpose |
|------|---------|
| `workloads/mqtt/app/broker.py` | Pure-asyncio MQTT broker (port 1883 + /metrics + /drain) |
| `load-generator/mqtt-client/client.py` | paho-mqtt load generator |
| `experiments/mqtt/mqtt_preflight.sh` | Shared pre-flight checks + helper functions |
| `experiments/mqtt/experiment-a-hpa-baseline/run.sh` | Exp A: 3-phase HPA failure demo |
| `experiments/mqtt/experiment-a-hpa-baseline/config.env` | Exp A configuration |
| `experiments/mqtt/experiment-b-stateful/run.sh` | Exp B: StatefulAutoscaler demo |
| `experiments/mqtt/experiment-c-idle-connections/run.sh` | Exp C: comparison demo |
| `experiments/mqtt/mqtt-exp-a-analysis.md` | Detailed Exp A analysis |
| `analysis/mqtt/parse_logs_mqtt.py` | Log parser → CSV + perpod CSV + phases JSON |
| `analysis/mqtt/plot_experiment_mqtt.py` | CSV → 3-panel PNG plots |
| `scripts/ansi/mqtt_all.yml` | Ansible master playbook |
