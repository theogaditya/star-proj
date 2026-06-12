# STAR: Stateful Autoscaling for Persistent WebSocket Workloads in Kubernetes

> **Systematic empirical evaluation of Kubernetes HPA failure modes for stateful workloads, and the design, implementation, and validation of a connection-aware custom controller.**

---

## Table of Contents

1. [Project Overview and Research Motivation](#1-project-overview-and-research-motivation)
2. [Background: Kubernetes HPA Architecture](#2-background-kubernetes-hpa-architecture)
3. [Project Structure](#3-project-structure)
4. [Phase 0 — Base Paper: Stateless HPA Benchmarking](#4-phase-0--base-paper-stateless-hpa-benchmarking)
5. [Phase 1 — WebSocket Experiments: Shared Infrastructure](#5-phase-1--websocket-experiments-shared-infrastructure)
6. [Experiment A — HPA Baseline Under Monotonic Correlated Load](#6-experiment-a--hpa-baseline-under-monotonic-correlated-load)
7. [Experiment B1 — Cyclic Churn: Over-Provisioning and Recovery Asymmetry](#7-experiment-b1--cyclic-churn-over-provisioning-and-recovery-asymmetry)
8. [Experiment B2 (Extended LOW) — Pilot Run](#8-experiment-b2-extended-low--pilot-run-not-in-paper)
9. [Experiment B2 (Instrumented) — Quantifying the Reconnection Storm](#9-experiment-b2-instrumented--quantifying-the-reconnection-storm)
10. [Experiment B3 — The Fatal Flaw: Idle Connections and Scale-Down Destruction](#10-experiment-b3--the-fatal-flaw-idle-connections-and-scale-down-destruction)
11. [Experiment C — The Custom StatefulAutoscaler](#11-experiment-c--the-custom-statefulautoscaler)
12. [Custom Controller: Architecture Deep Dive](#12-custom-controller-architecture-deep-dive)
13. [Phase 2 — MQTT Experiments (Future Work)](#13-phase-2--mqtt-experiments-future-work)
14. [Edge Cases and System-Level Caveats](#14-edge-cases-and-system-level-caveats)
15. [Evidence Chain and Experiment Progression](#15-evidence-chain-and-experiment-progression)
16. [Key Numbers Across All Experiments](#16-key-numbers-across-all-experiments)
17. [Glossary](#17-glossary)

---

## 1. Project Overview and Research Motivation

### The Structural Incompatibility

The Kubernetes Horizontal Pod Autoscaler (HPA) scales workloads by evaluating a proportional control law over resource metrics — principally CPU utilization. This model operates correctly for stateless, request-driven workloads (REST APIs, HTTP microservices) where the termination of any pod is safe: an in-flight request is at worst retried by the client, and the next request from any client is served by any available pod.

WebSocket workloads invalidate every assumption embedded in this model:

1. **Connection state is pod-affined.** Each active WebSocket session exists on a specific pod. Terminating that pod immediately and irrecoverably severs every connection it hosts. There is no retry semantics — the client loses its session.
2. **CPU does not encode connection load.** An idle WebSocket client holds a TCP session open, consuming a file descriptor and memory on the server, but generating zero CPU. From HPA's perspective, a pod holding 200 idle sessions is indistinguishable from a pod holding zero.
3. **Scale-down destroys active state.** When HPA decides to scale down because CPU has fallen, it kills pods holding live sessions. Every client on those pods is disconnected.
4. **Reconnection storms amplify the damage.** Hundreds of simultaneously disconnected clients immediately attempt to reconnect, creating a concurrent burst of TCP handshakes that drives CPU sharply upward. HPA interprets this CPU spike as new load, scales back up, eventually scales down again — beginning an oscillation loop that never converges.

### What This Project Demonstrates

Through five controlled experiments, this project:

- **Quantifies** HPA over-provisioning at 87.5% under cyclic churn (hitting `maxReplicas=15` within 99 seconds when the correct count is 8).
- **Measures** reconnection storm peak rates of **1,400 connections/second** using Prometheus instrumentation.
- **Documents** the permanent step-wise destruction of 800 live idle connections by an HPA scale-down operating on a 60-second stabilization window.
- **Characterises** the 30-second termination limbo — a Kubernetes-native behaviour where pods are doomed but not yet dead, leaving connections in a zombie state for exactly `terminationGracePeriodSeconds`.
- **Designs, implements, and validates** the `StatefulAutoscaler`, a custom Kubernetes operator that scales exclusively on `sum(active_connections)` queried from Prometheus, incorporating a sliding-window stabilization mechanism that holds pods warm through transient disconnection gaps and produces zero connection loss across all observed scaling events.

---

## 2. Background: Kubernetes HPA Architecture

### The HPA Control Law

HPA implements a discrete-time proportional regulator evaluated every 15 seconds:

```
desiredReplicas = ceil(currentReplicas × (currentMetricValue / targetMetricValue))
```

This formula embeds a critical assumption: `currentMetricValue` faithfully encodes workload intensity. For CPU and stateless workloads, this is approximately true. For persistent-connection workloads, it is categorically false.

### The Two Metric Pipelines

**KRM (Kubernetes Resource Metrics)**: CPU consumption is collected by `cAdvisor` inside each node's `kubelet`, aggregated by the `metrics-server`, and exposed via the `metrics.k8s.io` API. HPA reads this value every 15 seconds.

**PCM (Prometheus Custom Metrics)**: Application pods expose a Prometheus-format `/metrics` HTTP endpoint. Prometheus scrapes these at a configured `scrape_interval`. The Prometheus Adapter translates PromQL query results into the Kubernetes Custom Metrics API (`custom.metrics.k8s.io`), and HPA consumes the resulting per-pod values.

### The Stabilization Windows

To prevent thrashing, HPA applies time-based filters before executing scaling decisions:

- **Scale-up stabilization window**: defaults to `0s` — HPA can scale up immediately.
- **Scale-down stabilization window**: defaults to `300s` (5 minutes) — HPA must observe the scale-down condition continuously for 5 full minutes before executing it.

Modifying these values is the primary tuning knob used across the experiments.

### Why This Breaks for Stateful Workloads

```
Scenario: 800 WebSocket clients connected. They go idle — no messages, but connections open.
          CPU drops to near 0%. HPA evaluates: "CPU < target for stabilizationWindow → scale down."
          HPA kills 3 pods. Each pod held ~100 live connections.
          300 clients receive TCP RST. Connections permanently destroyed.
          300 clients reconnect simultaneously. CPU spikes.
          HPA evaluates: "CPU > target → scale up."
          The problem repeats indefinitely.
```

---

## 3. Project Structure

```
future-work/
├── README.md                          ← This file
├── experiments.md                     ← High-level experiment log
│
├── base-paper-implementation/         ← Phase 0: stateless HPA benchmarks
│   ├── krm-experiment/                ← CPU-based HPA on stateless HTTP workload
│   │   ├── run-experiment.sh
│   │   ├── analysis/
│   │   ├── manifests/
│   │   ├── results/
│   │   └── workload/
│   └── pcm-exp/                       ← Prometheus custom metrics HPA
│       ├── analysis/
│       ├── app/
│       ├── manifests/
│       ├── results/
│       └── workload/
│
├── workloads/
│   ├── websocket/
│   │   ├── app/
│   │   │   └── server.py              ← Python asyncio WebSocket server (non-instrumented)
│   │   ├── app-instrumented/
│   │   │   └── server.py              ← Same + Prometheus gauge/counter exports
│   │   └── k8s/
│   │       ├── deployment.yml
│   │       ├── hpa.yml
│   │       └── service.yml
│   └── mqtt/                          ← Future: Python MQTT broker
│
├── load-generator/
│   ├── websocket-client/
│   │   ├── client.py                  ← Async WebSocket load generator
│   │   └── k8s/                       ← Kubernetes Job manifests
│   └── mqtt-client/
│       └── docker-compose.yml
│
├── experiments/
│   └── websocket/
│       ├── experiment-a-hpa-baseline/
│       ├── experiment-b1-hpa-churn/
│       ├── experiment-b2-hpa-churn-instrumented/
│       ├── experiment-b3-hpa-idle-connections/
│       └── experiment-c-stateful/
│
├── controller/                        ← Custom StatefulAutoscaler operator (Go / Kubebuilder)
│   ├── api/
│   │   └── v1alpha1/
│   │       └── statefulautoscaler_types.go   ← CRD type definition
│   ├── internal/
│   │   └── controller/
│   │       ├── statefulautoscaler_controller.go  ← Reconciliation loop
│   │       └── prometheus.go                     ← Prometheus HTTP query client
│   ├── config/
│   │   ├── crd/                       ← Auto-generated CRD YAML (make manifests)
│   │   ├── rbac/                      ← Auto-generated ClusterRole
│   │   ├── manager/                   ← Controller Deployment manifests
│   │   └── samples/                   ← Example StatefulAutoscaler CR
│   ├── cmd/main.go                    ← Operator entry point
│   ├── Dockerfile
│   ├── Makefile
│   └── go.mod
│
├── scripts/                           ← End-to-end experiment orchestration
│   ├── kind.yml                       ← kind cluster configuration (1 control-plane + 2 workers)
│   ├── run-experiment-a.sh
│   ├── run-experiment-b1.sh
│   ├── run-experiment-b2.sh
│   ├── run-experiment-b2-instrumented.sh
│   ├── run-experiment-b3.sh
│   └── run-experiment-c.sh
│
├── analysis/                          ← Python log parsing and plot generation
│   ├── parse_logs.py
│   ├── plot_experiment.py
│   ├── parse_logs_instrumented.py
│   ├── plot_experiment_instrumented.py
│   ├── experiment-b3/
│   └── experiment-c/
│
├── results/
│   ├── raw/                           ← Raw CSV/TSV logs from experiments
│   └── processed/                     ← Generated plots
│
└── Paper-Latex/
    ├── paper.tex                      ← Full research paper source (LaTeX / svjour3)
    └── reference.bib
```

Every experiment follows the same execution pattern:
1. Shell script creates a fresh `kind` cluster from `scripts/kind.yml`.
2. Deploys the server, HPA (or custom controller), load generator, and monitoring stack.
3. Executes the load pattern, collecting `kubectl` poll output at fixed intervals.
4. Saves raw logs to `results/raw/`.
5. Analysis Python scripts parse logs and generate plots to `results/processed/`.

---

## 4. Phase 0 — Base Paper: Stateless HPA Benchmarking

Before the WebSocket experiments, prior research evaluated HPA on stateless HTTP workloads. These baselines establish the behavioral envelope of HPA where its assumptions hold, providing the empirical foundation against which the stateful failures are contrasted.

### 4.1 KRM Experiment — CPU-Based HPA Baseline

**Location**: `base-paper-implementation/krm-experiment/`

#### Experimental Setup

A CPU-intensive HTTP server runs a configurable spin loop proportional to incoming request rate. The load generator alternates between HIGH (50 req/s, 100 seconds) and LOW (2 req/s, 100 seconds) phases. HPA targets 60% average CPU utilization. The primary variable is `--metric-resolution` on the `metrics-server`: tested at 15s, 30s, and 60s.

#### Observed Behaviors

**Staircase scaling**: Replica changes occur in discrete steps synchronized with the HPA evaluation period (15s). Even with instantaneous CPU changes, scaling actuation is inherently stepwise due to the discrete control loop.

**CPU overshoot during burst onset**: Observed pod CPU utilization exceeds 200% of the requested CPU allocation immediately after a HIGH phase begins. This reflects the inherent lag between demand growth and replica provisioning — the pods being scheduled and initialised cannot serve load until ready, so existing pods absorb disproportionate load for the entire provisioning window.

**Desired vs. current replica divergence**: HPA computes `desiredReplicas` immediately but `currentReplicas` only catches up after pod scheduling and container initialization complete. This gap is a compound of scheduler latency, image pull time, and container startup time. The divergence is directly visible in the Desired vs Current plots.

**Stable convergence**: Despite these transient deviations, the system reaches steady state without oscillation under unimodal load transitions. The stateless workload is inherently forgiving — dropped in-flight requests are retried transparently.

**The `pod_seconds` efficiency metric**: `pod_seconds = replicas × duration` is the proxy for cluster resource cost. Over-provisioning wastes pod_seconds; under-provisioning causes request failures. This metric is used throughout the base paper to compare configurations.

#### Key Insight

CPU is a **lagging indicator** — by the time utilization is high enough for HPA to react, the workload has already been saturating for one or more scrape cycles. The degree of lag is directly proportional to `metric-resolution`. At 60s resolution, an entire minute of saturated CPU load passes before HPA receives accurate data. For stateless HTTP, this is a performance concern. For WebSocket workloads, it is structurally irrelevant: even perfect CPU measurement cannot encode connection state.

---

### 4.2 PCM Experiment — Prometheus Custom Metrics

**Location**: `base-paper-implementation/pcm-exp/`

#### Three Configurations Evaluated

| Name | Metric Used by HPA | Signal Type |
|------|--------------------|-------------|
| PCM-CPU | CPU, sourced via Prometheus Adapter | Lagging |
| PCM-H | `http_requests_per_second` | Leading |
| PCM-CH | `max(CPU recommendation, HTTP recommendation)` | Hybrid |

#### The Staircase Effect — Detailed Mechanism

With `scrape_interval=60s`, Prometheus scrapes the application's `/metrics` endpoint once per minute. HPA evaluates every 15 seconds. Between scrapes, HPA reads a static, stale metric value from the Adapter cache:

```
t=0s:   Load spikes. http_requests_per_second = 200.
t=0s:   Prometheus has not scraped since the spike. Adapter still reports prior value (5 req/s).
t=15s:  HPA evaluates. Sees 5 req/s. No scaling needed.
t=30s:  HPA evaluates. Sees 5 req/s. No scaling needed.
t=45s:  HPA evaluates. Sees 5 req/s. No scaling needed.
t=60s:  Prometheus scrapes. Adapter now reports 200 req/s.
t=60s:  HPA evaluates. Computes desired replicas. Scales up.

→ 45–60 seconds of completely unhandled load saturation.
```

Reducing `scrape_interval` to 15s collapses this lag to a single cycle. Diminishing returns are observed between 30s and 15s because the fundamental lower bound is the HPA evaluation period itself (15s) — independent of scrape interval.

#### PCM-H: Leading Indicator Advantage

`http_requests_per_second` is measured at ingress. The metric increases the instant the first request of a burst arrives — before CPU has had time to rise. This **leading indicator** allows HPA to initiate scale-up while pods are at moderate utilization rather than after they are already saturated.

#### PCM-CH: Hybrid Max-Selection

Using `max(CPU recommendation, HTTP recommendation)` ensures HPA takes the scale-up decision whenever either metric signals load. CPU provides a stabilizing feedback signal (it confirms demand is real and sustained at the hardware level), while HTTP request rate provides early warning. The `max()` selection eliminates the transient over-provisioning that PCM-H alone can cause when request rate momentarily spikes but CPU has not yet risen.

#### The Bridge to Stateful Workloads

The PCM experiments improved HPA accuracy for stateless HTTP. But they still relied on the assumption that scale-down is safe. For WebSocket workloads, no metric tuning resolves the fact that **terminating a pod forcibly severs every live session it hosts**. That is an architectural problem, not a measurement problem. The base paper found its ceiling; this project extends beyond it.

---

## 5. Phase 1 — WebSocket Experiments: Shared Infrastructure

All five WebSocket experiments share the same foundational cluster, application server, load generator, and monitoring setup. This section documents the common substrate in full.

### 5.1 The Cluster Configuration

```bash
kind create cluster --config scripts/kind.yml
```

`kind.yml` defines a 3-node cluster: one control-plane node and two worker nodes, running Kubernetes v1.31.6. All nodes are Docker containers on the host machine — network latency between them is negligible. CPU resource measurements are relative to the host machine's physical cores and compete with the Docker overhead.

**Why `kind`?** Full reproducibility. Any researcher can recreate the exact cluster on any Linux machine with Docker installed, without cloud credentials or cost.

**Critical `kind`-specific requirement**: The `metrics-server` deployment requires `--kubelet-insecure-tls` because `kind` kubelets use self-signed TLS certificates that `metrics-server` rejects by default. This flag is applied via a deployment patch in each `run.sh`. It must never appear in a production cluster.

---

### 5.2 The WebSocket Server (`server.py`)

**Non-instrumented**: `workloads/websocket/app/server.py`
**Instrumented**: `workloads/websocket/app-instrumented/server.py`

A Python `asyncio` server using the `websockets` library.

#### Core Logic

```python
async def handler(websocket):
    ACTIVE_CONNECTIONS.inc()        # Prometheus Gauge: +1 on new connection
    NEW_CONNECTIONS.inc()           # Prometheus Counter: +1 on new connection (never decreases)
    try:
        async for message in websocket:
            if CPU_WORK > 0:
                for _ in range(CPU_WORK * 500_000):  # Artificial CPU burn loop
                    pass
            await websocket.send("ack")
    finally:
        ACTIVE_CONNECTIONS.dec()    # Prometheus Gauge: -1 on connection close (any reason)
```

#### Key Parameters

| Parameter | Description | Used In |
|-----------|-------------|---------|
| `CPU_WORK=1` | Spin loop executes per message. CPU ∝ ping rate. | Experiments A, B1, B2, B3 |
| `CPU_WORK=0` | No spin loop. Server is CPU-idle regardless of connections. | Experiment C |
| `ACTIVE_CONNECTIONS` | Gauge — current open connection count. | B2-Instrumented, C |
| `NEW_CONNECTIONS` | Counter — cumulative total ever opened. Rate = reconnection rate. | B2-Instrumented |

#### Why `NEW_CONNECTIONS` is a Counter (Not a Gauge)

The reconnection rate is computed as:
```
rate(new_connections_total[15s])
```
PromQL `rate()` requires a monotonically increasing counter. It computes the per-second increase over the trailing 15-second window. A spike in this rate directly measures a reconnection storm's intensity in connections/second. A Gauge cannot serve this purpose because it can decrease (connections closing) — `rate()` on a Gauge would give meaningless results when connections close between scrapes.

#### Exposed Endpoints

- **WebSocket** on port `8765`: main connection handler.
- **`/metrics`** on port `8080`: Prometheus exposition format. Scraped by Prometheus every 15 seconds.
- **`/drain`** on port `8080`: When POSTed, server stops accepting new connections and allows existing connections to close naturally. Used by the custom controller's graceful scale-down path (planned for future implementation).

---

### 5.3 The Load Generator (`client.py`)

**Location**: `load-generator/websocket-client/client.py`

Runs as a Kubernetes Job. Spawns N concurrent async WebSocket connections.

#### Linear Stagger — The Core Safety Mechanism

```python
delay = (client_index / CLIENTS) * RAMP_UP_DURATION
await asyncio.sleep(delay)
# Then connect:
async with websockets.connect(SERVER_URL, ping_timeout=None) as websocket:
    ...
```

Without staggering, all N clients arrive simultaneously, instantly saturating the 2 initial pods before HPA has had a single 15-second cycle to react. Staggering distributes the connection ramp linearly over `RAMP_UP_DURATION` (90 seconds), allowing HPA to scale incrementally as load builds.

#### Three-Phase Behavioral Sequence (Used in B3 and C)

The client supports time-bounded behavioral phases:

1. **ACTIVE Phase (0 → `ACTIVE_DURATION`)**: Clients ramp up, connect, and ping every 5 seconds. Drives server CPU via the spin loop when `CPU_WORK=1`. This is the only window in which HPA sees any CPU signal.

2. **IDLE Phase (`ACTIVE_DURATION` onwards)**: After `ACTIVE_DURATION` seconds, clients stop sending any messages but **hold the connection open** by entering `async for _ in ws:` — an async read loop that never fires because the server sends nothing unprompted. The TCP session remains fully established in the OS kernel. CPU collapses to ~0%.

3. **Disconnect Handling**: In B3, an exception during IDLE (indicating the server killed the connection) causes the client to `return` — permanently terminating without reconnecting. This is the "smoking gun" behavioral design: every lost connection is visible as a permanent step down.

#### `ping_timeout=None`

The `websockets` library sends WebSocket-level Ping frames to detect dead connections. Under heavy CPU load, servers respond slowly to pings; the library may classify the connection as dead and close it client-side, creating false disconnections before HPA has acted. Setting `ping_timeout=None` disables this, leaving connection lifecycle entirely under experimental control. This is appropriate for controlled experiments where the server is known to be live and only HPA-induced disconnects should be observed.

---

### 5.4 Prometheus Monitoring Stack

Deployed into the `monitoring` namespace. Global configuration:

```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: "websocket-pods"
    kubernetes_sd_configs:
      - role: pod
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
```

Prometheus service discovery automatically finds all pods annotated `prometheus.io/scrape: "true"` and scrapes their `/metrics` endpoint on the annotated port every 15 seconds. Metric values are stored as time series and queryable via PromQL at `http://prometheus.monitoring.svc.cluster.local:9090`.

**Why 15s scrape interval?** It matches the HPA evaluation cycle and the `metrics-server` resolution. At 60s, metric values would be stale across 3–4 HPA cycles. At 15s, the metric is refreshed every HPA cycle — worst-case lag is one 15-second window.

---

### 5.5 Metrics Server

Required for HPA to function in the KRM pipeline. Patched at deployment time to enforce `--metric-resolution=15s`:

```yaml
- op: replace
  path: /spec/template/spec/containers/0/args
  value:
    - --metric-resolution=15s
    - --kubelet-insecure-tls
    - --kubelet-preferred-address-types=InternalIP
```

Without this patch, the default 60s resolution means HPA evaluates data that is up to a minute old — the staircase effect from the base paper would appear in every WebSocket experiment.

---

## 6. Experiment A — HPA Baseline Under Monotonic Correlated Load

**Location**: `scripts/run-experiment-a.sh`, `experiments/websocket/experiment-a-hpa-baseline/`

### Objective

Before characterising failure modes, establish whether CPU-based HPA can mechanically scale a WebSocket workload at all — under the most favorable possible conditions.

### Why "Most Favorable Conditions"?

HPA has no knowledge of WebSocket connections. For it to scale correctly, CPU must tightly track the number of active connections. The server is run with `CPU_WORK=1` — every client ping triggers a spin loop. This creates an artificial but mathematically perfect correlation:

```
Connections ↑ → Pings ↑ → CPU ↑ → HPA scales up
Connections ↓ → Pings ↓ → CPU ↓ → HPA scales down
```

This is explicitly **not** how real WebSocket workloads behave (IoT devices, chat clients, and game sessions spend the vast majority of their lifetime idle). This condition exists solely to give HPA the best possible chance to succeed before the experiments systematically invalidate it.

### Configuration

| Parameter | Value |
|-----------|-------|
| `CPU_WORK` | `1` |
| Clients | 800 ramping over 90s |
| Active ping interval | 5 seconds |
| HPA target CPU | 60% average utilization |
| `minReplicas` | 2 |
| `maxReplicas` | 10 |
| Scale-down stabilization window | 300s (default) |
| Metrics pipeline | KRM (metrics-server at 15s resolution) |

### Observed Timeline

```
t=0s:      2 pods. No connections.
t=30s:     ~400 connections established. CPU spikes to 230–260m on 2 pods (>100% of CPU request).
t=45s:     HPA sync fires: 2 → 4 pods.
t=90s:     All ~800 clients connected. CPU still high. HPA: 4 → 5 pods.
t=120s:    5 pods. CPU settles near 60% target. System stable.
t=330s+:   Load generator Job terminates. All connections drop. CPU → ~0%.
t=680s:    After 350s of 0% CPU (> 300s stabilization window): HPA scales 5 → 2 pods.
```

### Results and Key Observations

HPA correctly scaled from 2 to 5 replicas. The 300-second scale-down stabilization window produced a 350-second lag between load cessation and scale-down execution — by design, to prevent premature termination.

**Peak active connections**: 388 (not 800; some early clients failed during the initial surge before stagger fully protected the pods).
**Peak replicas**: 5. Correct connection-derived optimum: `ceil(388/100)` ≈ 4. Minor over-provision of 1 pod.
**Scale-up latency**: ~45 seconds from load onset to first scale-up event.

The success here is entirely predicated on `CPU_WORK=1`. Removing it — as all real WebSocket deployments would — leaves HPA with no signal to act on. Experiment C deliberately sets `CPU_WORK=0` to prove the custom controller operates correctly without any CPU contribution.

### Edge Cases

- **388 connections, not 800**: The initial 2 pods were transiently overwhelmed during the very beginning of the ramp before stagger could fully distribute load, causing some early establishment attempts to fail at the application layer.
- **The 5-minute stabilization window is intentionally default here**: Represents the "best case" HPA behavior. Compressing it to 60 seconds (as in B3) causes HPA to destroy live sessions within minutes of load cessation.

---

## 7. Experiment B1 — Cyclic Churn: Over-Provisioning and Recovery Asymmetry

**Location**: `scripts/run-experiment-b1.sh`, `experiments/websocket/experiment-b1-hpa-churn/`

### Objective

Expose HPA behavior under workloads that alternate between high activity and idle periods — a pattern common in game servers (rounds vs. lobbies), trading platforms (market hours vs. off-hours), and event-driven systems.

### Load Pattern

```
HIGH phase (60s): 800 clients pinging at 5-second intervals. CPU > 60%.
LOW phase (30s):  All pings stop. CPU ≈ 0%.
                  Connections remain open — TCP sessions held in OS kernel.
```

Cycle period: 90 seconds. Repeated 5 times. The default 300-second scale-down stabilization window is intentionally preserved — this is an explicit test of HPA under its **factory settings**.

### The Mathematical Trap

The 300-second stabilization window requires 5 continuous minutes of below-target CPU before HPA authorizes a scale-down. With 30-second LOW phases:

```
Stabilization clock starts: t=0s (beginning of LOW phase).
LOW phase ends: t=30s. Only 30 of the required 300 seconds elapsed.
NEW HIGH phase begins: t=30s. CPU spikes. Stabilization clock resets.

Conclusion: The clock can never reach 300 seconds. HPA can never authorize scale-down during cycling.
```

Meanwhile, every HIGH phase drives additional scale-up events, accumulating replicas:

```
t=5s:    HIGH #1. HPA: 2 → 5 pods.
t=12s:   CPU still rising. HPA: 5 → 8 pods.
t=65s:   LOW #1. CPU drops. Stabilization starts. (Resets at t=83s)
t=83s:   HIGH #2. HPA: 8 → 10 pods.
t=99s:   HPA hits maxReplicas = 15. Ceiling reached within 99 seconds of start.
```

The correct connection-derived replica count at 800 connections with 100 connections/pod target: `ceil(800/100) = 8 pods`. HPA reached 15 — an **87.5% over-provision**.

### Recovery Duration

After all load permanently ceases (post-cycle 5), HPA must still complete the full stabilization window and then step down conservatively:

```
t=415s:  First scale-down finally authorized: 15 → 13
t=655s:  13 → 12
t=700s:  12 → 11
t=732s:  11 → 6
t=747s:  6 → 2 (minimum)
```

Total recovery time: **~650 seconds after the final cycle** — nearly 11 minutes of 15 pods serving ~45–55 idle connections.

### What Was NOT Observed in B1

This experiment deliberately uses the full 300-second stabilization window. HPA never executes a scale-down during the active cycling window. Therefore, **no live connections are destroyed during B1**. The failure in B1 is purely resource efficiency. The connection destruction failure mode is isolated to B3. This separation is intentional — each experiment isolates one failure mode for clarity.

### Edge Cases

- **Connections during LOW phase were ~45–55, not 0**: Some clients maintained connections even though pings stopped. 15 pods served 50 connections — extreme over-provisioning of cluster resources.
- **Any cyclic workload where cycle period < stabilization window triggers this**: With a 90-second cycle and a 300-second window, the stabilization clock is structurally guaranteed to never expire.

---

## 8. Experiment B2 (Extended LOW) — Pilot Run *(Not in Paper)*

**Location**: `scripts/run-experiment-b2.sh`, `experiments/websocket/experiment-b2-hpa-churn/`

### Purpose and Status

B2 Extended LOW was a non-instrumented pilot run conducted to confirm that the connection destruction failure mode was real before investing in a full Prometheus instrumentation stack. It does **not appear as a standalone experiment in the research paper** because it produced no quantitative measurements. It is documented here because understanding why it was superseded is integral to understanding the methodology.

### Design

Extend the LOW phase beyond the default 300-second stabilization window, forcing HPA to scale all the way to `minReplicas`:

```
HIGH (60s): ~500 connections, active pinging. HPA scales up to 8–10 pods.
Extended LOW (200s+): All pings stop. Connections held open. CPU = 0%.
                      After 300s of 0%, HPA scale-down window expires.
                      HPA scales down to 2 pods.
                      Kubernetes terminates 6–8 pods, each holding ~80–100 live connections.
```

Three full cycles executed. Full replica path: `2 → 6 → 8 → 2 → 6 → 8 → 10 → 2 → 5 → 10 → 4 → 2`.

### Why It Cannot Be Published

The server had no Prometheus metrics at this stage. The only collected data was `kubectl top pods`, `kubectl get hpa`, and `kubectl get pods` output. None of these answer the research questions:

- How many connections were severed per scale-down event?
- At what rate (connections/second) did clients attempt to reconnect?
- Did the server briefly see more connections than the target after a storm (overshoot)?

A research claim requires measurement. "Pods were killed and connections probably died" is an observation. "1,400 connections/second peak reconnection rate measured by `rate(new_connections_total[15s])`" is evidence.

### What It Accomplished

1. **Confirmed the failure mode is real**: Connections visibly died when HPA scaled down. The hypothesis was correct.
2. **Revealed exactly what needed to be measured**: Seeing reconnections with no numbers specified the instrumentation requirements for B2-Instrumented.
3. **First sighting of the 30-second termination limbo**: A visible gap between HPA's scale-down command and the actual connection drop was noticed qualitatively here, then precisely timestamped in B3.
4. **Justified the Prometheus stack investment**: Without this pilot confirming a real failure mode, deploying a full in-cluster Prometheus setup would have been a blind investment.

### Key Qualitative Discoveries

**Pod termination sequence observed**: When Kubernetes terminates a pod, the pod disappears from `kubectl get endpoints` (removed from Service routing) before its connections die. Then, ~30 seconds later, the connections drop. This sequence was first noticed here but only quantified in B3.

**Reconnection storm drives immediate CPU spike**: After each scale-down, the surviving 2-pod cluster experienced immediate CPU saturation from reconnecting clients. HPA reacted by immediately scaling back up. The cluster oscillated visibly through three full cycles.

---

## 9. Experiment B2 (Instrumented) — Quantifying the Reconnection Storm

**Location**: `scripts/run-experiment-b2-instrumented.sh`, `experiments/websocket/experiment-b2-hpa-churn-instrumented/`

### Objective

With Prometheus instrumentation in place, precisely quantify what B2 Extended LOW showed qualitatively: the rate at which connections are lost and re-established during HPA-triggered scale-down events, and whether the reconnection storm overshoots the original connection target.

### Changes from B2 Extended LOW

- Server upgraded to `app-instrumented/server.py` with `ACTIVE_CONNECTIONS` (Gauge) and `NEW_CONNECTIONS` (Counter) exported at `/metrics`.
- Full Prometheus stack deployed in-cluster at `scrape_interval: 15s`.
- HPA policy:
  - `stabilizationWindowSeconds: 0` for scale-up (immediate reaction to CPU spikes from storms).
  - `stabilizationWindowSeconds: 60` for scale-down (guarantees one full scale-down per cycle).
- Load pattern: HIGH=60s, LOW=90s per cycle (5 cycles). The 90s LOW is deliberately chosen to exceed the 60s scale-down window, ensuring at least one HPA scale-down per cycle.
- `maxReplicas: 15`, 800 client connections.

### Why `stabilizationWindowSeconds: 0` for Scale-Up?

Zero scale-up stabilization ensures that every reconnection storm (which spikes CPU massively) triggers an immediate HPA scale-up. This guarantees that each LOW → reconnection storm → HIGH cycle produces a complete, measurable, observable scale-up/scale-down sequence locked within the 150-second cycle window. Without it, the scale-up after a storm might be delayed by up to 15 additional seconds, making cross-cycle timing comparisons ambiguous.

### How Metrics Are Collected

**Step 1 — Application exposes metrics**:
```python
# workloads/websocket/app-instrumented/server.py
ACTIVE_CONNECTIONS = Gauge("active_connections", "Current number of active WebSocket connections")
NEW_CONNECTIONS = Counter("new_connections_total", "Total WebSocket connections established")
```

**Step 2 — Prometheus scrapes every 15 seconds**:
```yaml
global:
  scrape_interval: 15s
```

**Step 3 — Reconnection rate computed via PromQL**:
```
rate(new_connections_total[15s])
```
This returns the per-second rate of new connections established over the trailing 15-second window. A sudden spike indicates a reconnection storm.

**Step 4 — Analysis scripts extract and plot**:
`analysis/parse_logs_instrumented.py` and `analysis/plot_experiment_instrumented.py` read the raw Prometheus data and generate the reconnection vs. time graphs.

### Observed Results

**Reconnection storm peak rates:**

| Cycle | Peak `rate(new_connections_total[15s])` |
|-------|----------------------------------------|
| Cycle 1 | **1,400.9 connections/second** |
| Cycle 2 | **1,298.3 connections/second** |
| Cycle 3 | **1,399.5 connections/second** |
| Cycle 4 | **1,251.8 connections/second** |
| Cycle 5 | Partial (connection pool degraded by final cycle) |

**Connection overshoot**: During Cycle 2, `active_connections` peaked at **1,215** — a 51.9% overshoot above the 800-client target. The mechanism:

When a pod receives `SIGKILL`, the OS immediately closes all TCP file descriptors, sending a TCP RST to every connected client. All 800 clients detect the disconnect simultaneously and initiate reconnection. The reconnections arrive on surviving pods faster than:
- The OS can clear `CLOSE_WAIT` state from the RST'd sessions on the server side.
- The server's garbage collector can free `ACTIVE_CONNECTIONS` decrements from the old (dead) connection handler coroutines.

Result: both old (zombie socket state) and new (live) connection objects are simultaneously counted by the Gauge, briefly registering 1,215. This resolves naturally within 15–30 seconds as OS socket state clears.

**Causal chain confirmed**: Reconnection rate spikes occur within one 15-second Prometheus scrape window of each HPA scale-down event timestamp recorded in `kubectl get hpa` logs. Direct causality: HPA scale-down → pod SIGKILL → TCP RST → simultaneous reconnection → storm.

### Edge Cases

- **HPA hit `maxReplicas=15` in every cycle, every time**: The cluster was always resource-constrained at peak load. The replica ceiling was never enough.
- **The 1,215 overshoot means the server handled 51.9% more connections than sized for**: Service degradation risk during storms is real and proportional to overshoot magnitude.

---

## 10. Experiment B3 — The Fatal Flaw: Idle Connections and Scale-Down Destruction

**Location**: `scripts/run-experiment-b3.sh`, `experiments/websocket/experiment-b3-hpa-idle-connections/`

### Objective

Construct the definitive control baseline proving that CPU-based HPA is structurally incompatible with stateful persistent connections. Isolate a single, irrefutable scenario: 800 idle (zero CPU, fully open TCP sessions) connections are permanently destroyed by HPA because HPA cannot see them.

### Key Design Decisions

**1. Compressed scale-down window (60 seconds instead of 300)**:
```yaml
behavior:
  scaleDown:
    stabilizationWindowSeconds: 60   # Compressed from 300s default
  scaleUp:
    stabilizationWindowSeconds: 0    # Immediate scale-up
```
This forces scale-down execution within ~2 minutes of CPU falling, making the destruction event observable within a single experiment window.

**2. Two-phase client behavior**:
- **ACTIVE Phase (0–120s)**: Clients connect and ping actively (`CPU_WORK=1`). The pings are the only existing mechanism for HPA to detect load. CPU rises; HPA scales up to 15 pods.
- **IDLE Phase (120s+)**: Clients stop all message transmission but hold connections open via `async for _ in ws:`. TCP sessions remain live. CPU: ~0%. Connections: 800 (flat).

**3. No reconnection on disconnect**:
When a client detects a disconnect during the IDLE phase, it exits without reconnecting. Every lost connection is therefore a permanent step down on the connection graph — irrefutable visual proof of destroyed sessions.

### Full HPA Manifest (B3-Specific)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: websocket-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: websocket-server
  minReplicas: 2
  maxReplicas: 15
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 4
          periodSeconds: 15
    scaleUp:
      stabilizationWindowSeconds: 0
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
```

### Observed Timeline

```
t=0s:       2 pods. ACTIVE phase begins.
t=0–90s:    800 clients ramp up via linear stagger. CPU rises as pings hit spin loop.
            HPA: 2 → 6 → 10 → 15 replicas. (scaleUp = 0s, immediate)
t=90s:      All 800 connections established across 15 pods (~53 connections/pod average).
t=120s:     IDLE phase begins. All pings stop.
            CPU: drops from ~90% to ~0%.
            active_connections gauge: flat at 800.
            TCP sockets: all 800 fully open in OS kernel.
t=180s:     60 seconds of CPU=0%. HPA scale-down window expires.
            HPA compute: desiredReplicas = ceil(2 × (0% / 60%)) = 0 → bounded to minReplicas by
            stabilization logic → decides: 15 → 11.
            4 pods transition to Terminating state.
            Service endpoint slice updated: those 4 pods removed from routing.
            *** 30-second SIGTERM grace period begins ***
t=180s:     active_connections gauge: still 744. No change yet.
t=210–223s: 30 seconds elapsed. SIGKILL fires on the 4 Terminating pods.
            OS closes all file descriptors. TCP RST sent to every connected client.
            active_connections: 744 → ~697. Visible step-down in graph.
t=225s:     HPA evaluates again: CPU still 0%. Issues: 11 → 7.
            4 more pods enter Terminating. SIGTERM grace period begins.
t=255–261s: SIGKILL. Connections: ~697 → ~445.
t=270s:     HPA: 7 → 3. SIGTERM on 4 more pods.
t=306s:     SIGKILL. Connections: ~445 → ~79.
t=320s+:    HPA: 3 → 2. Final SIGTERM/SIGKILL.
            Remaining ~79 connections severed.
            All 800 original sessions permanently destroyed.
```

### 10.1 The 30-Second Termination Limbo

This is one of the most consequential empirical findings of the entire project.

#### Precisely Measured Timestamps

Raw log analysis from B3 reveals a consistent ~35-second gap between each HPA scale-down decision and the corresponding drop in `active_connections`:

| HPA Event | Replica Change | Connections When HPA Acted | Connection Drop Timestamp | Gap |
|-----------|---------------|---------------------------|--------------------------|-----|
| Scale-down #1 | 15 → 11 | 744 | 43 seconds later | ~43s |
| Scale-down #2 | 11 → 7 | 697 | 36 seconds later | ~36s |
| Scale-down #3 | 7 → 3 | 445 | 36 seconds later | ~36s |

The gap is not experimental noise or measurement delay. It is a deterministic Kubernetes system behavior: **`terminationGracePeriodSeconds`** (default 30 seconds) plus several seconds of endpoint propagation and SIGTERM send latency.

#### The Exact Sequence When Kubernetes Terminates a Pod

1. **etcd update**: Pod object updated to `Terminating` state. `deletionTimestamp` set.
2. **Service endpoint slice update**: The pod's IP:port is immediately removed from all relevant `EndpointSlice` objects. `kube-proxy` on all nodes propagates this change to iptables/IPVS rules. **No new TCP connections will be routed to this pod from this moment forward.**
3. **`SIGTERM` sent to container PID 1**: Within seconds of the termination decision, the container's entry process receives signal 15.
4. **Python server receives `SIGTERM`**: The `websockets` server has no `SIGTERM` handler registered. The signal is ignored by the default Python signal disposition (actually, Python defaults to raising `KeyboardInterrupt` for SIGTERM in the main thread, but async event loops may suppress this). The server continues running all active connection coroutines normally.
5. **`terminationGracePeriodSeconds` countdown** (default **30 seconds**): Kubernetes waits this long for the container to self-terminate after SIGTERM. During this entire window, the container process is alive, all TCP sockets are alive, and all 50+ connected clients' sessions are still fully functional — the clients experience no disruption whatsoever.
6. **`SIGKILL` sent**: After 30 seconds, Kubernetes sends signal 9. The OS kernel forces immediate process termination. No handler is possible. The Go runtime, Python interpreter, all application threads — everything halts instantaneously.
7. **OS file descriptor cleanup**: The kernel closes every open file descriptor the process held, including all TCP socket objects. For each socket, the OS sends a **TCP RST (reset)** packet to the remote end.
8. **Clients receive TCP RST**: Every connected client's WebSocket library receives the RST, which surfaces as a connection exception. In B3, clients catch this and exit without reconnecting.

#### The Implications

The `terminationGracePeriodSeconds` mechanism exists to allow stateless applications to finish serving in-flight HTTP requests before dying. A stateless HTTP handler typically completes within milliseconds; 30 seconds is massively more than needed.

For WebSocket connections that may persist for hours, 30 seconds provides **zero meaningful protection**. The connection is doomed at the moment HPA issues the scale-down decision. The 30-second window only delays the inevitable, creating a class of connections that are:

- **Still alive** (TCP session active, data can still flow bidirectionally)
- **Invisibly doomed** (the pod is in `Terminating` state, removed from routing)
- **Zombie state** (hold real OS resources — file descriptors, kernel socket buffers — while serving clients who do not yet know they are about to lose their session)

Even Kubernetes's own graceful shutdown mechanism cannot protect persistent connections — because the mechanism was designed without any awareness that persistent connections exist. This is not a Kubernetes bug. It is a structural incompatibility between the termination model and the connection lifecycle model.

### Edge Cases

- **Connection counts are uneven across pods**: Kubernetes routes new connections via round-robin at the Service level, but pods added at different stages of scale-up accumulate different connection counts. A pod added at `minReplicas=2` has been accepting connections longer than one added at `maxReplicas=15`. Scale-down step sizes therefore vary.
- **`CPU_WORK=1` is required for scale-up to occur**: Without it, HPA never sees a signal to scale up during the ACTIVE phase. B3 requires the scale-up to first happen so that scale-down can subsequently destroy connections.
- **The 60-second stabilization window is the attack vector**: The original 300-second default would have made the IDLE phase impractically long (5+ minutes of waiting). 60 seconds compresses the observation window to a manageable 4-minute experiment.

---

## 11. Experiment C — The Custom StatefulAutoscaler

**Location**: `scripts/run-experiment-c.sh`, `experiments/websocket/experiment-c-stateful/`

### Objective

Prove that a connection-aware custom controller completely resolves each failure mode identified in A through B3:
1. Exact replica targeting based on connection count — not CPU.
2. Complete pod preservation during transient connection gaps.
3. Zero connection loss across all observed scaling events.

### Configuration vs. B3 (Direct Head-to-Head)

| Parameter | B3 (HPA) | Experiment C (StatefulAutoscaler) |
|-----------|----------|-----------------------------------|
| Scaler | Kubernetes HPA | Custom `StatefulAutoscaler` CRD |
| Scaling signal | CPU utilization (KRM pipeline) | `sum(active_connections)` (Prometheus) |
| `CPU_WORK` | **1** | **0** (server CPU-idle throughout) |
| Scale-down trigger | CPU < target for stabilizationWindow | active_connections = 0 AND cooldown expired |
| Scale-down protection | Default window (60s in B3 config) | `scaleDownCooldownSeconds: 120` |
| Connection awareness | **Zero** | **Exact** |
| `maxReplicas` vs. correct count | 15 vs. 8 (87.5% over-provision) | 8–9 vs. 8 (0–12.5% margin) |

**`CPU_WORK=0` is essential to the proof.** If the controller correctly scaled to 8 pods for 800 connections while CPU was persistently 0% throughout the entire experiment, no argument remains that CPU contributed to the scaling decision. The controller operates on connection count alone.

### The StatefulAutoscaler Custom Resource

```yaml
apiVersion: autoscaling.star.io/v1alpha1
kind: StatefulAutoscaler
metadata:
  name: websocket-autoscaler
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: websocket-server
  targetConnectionsPerPod: 100       # Target: at most 100 connections per pod
  minReplicas: 2
  maxReplicas: 15
  scaleDownCooldownSeconds: 120      # Hold pods warm for 120s after connections reach 0
  maxScaleDownStep: 2                # Remove at most 2 pods per reconciliation cycle
```

**Replica formula**: `desired_pods = ceil(sum(active_connections) / targetConnectionsPerPod)`

Examples:
- 800 connections → `ceil(800/100)` = **8 pods**
- 250 connections → `ceil(250/100)` = **3 pods**
- 0 connections → `ceil(0/100)` = 0 → cooldown suppresses → holds current replicas for 120s

### The Two-Cycle Restorm Simulation

This scenario simulates a real-world pattern: a brief network-level outage (all connections simultaneously drop to 0) followed by a massive, instantaneous reconnection wave.

```
CYCLE 1 [t=0 → t=150s]:
  800 clients connect over 90s and actively ping (CPU_WORK=1 has no effect — CPU_WORK=0).
  Controller: sum(active_connections) = 800 → desired = ceil(800/100) = 8 pods.
  Scales to 8 pods. CPU: non-zero (clients ping, server does nothing with pings computationally).
  t=120s: Clients stop pinging. CPU drops to 0%.
  Controller: sum(active_connections) = 800. desired = 8. CPU ignored. No change.

DROP 1 [t=150s → t=240s] — The 90-Second Gap:
  Load generator Job deleted. All 800 clients forcefully disconnected.
  sum(active_connections): 800 → 0 (within one 15s Prometheus scrape cycle).

  HPA (B3 config) would do: Start 60s scale-down timer. By t=210s, begin killing pods.
  StatefulAutoscaler does:
    "sum(active_connections) = 0. desired = 0. But scaleDownCooldownSeconds = 120.
     I have been at 0 for only Δt seconds. Cooldown not expired. Maintain 8 pods."
  All 8 pods remain live. Warm. Ready to accept connections immediately.

CYCLE 2 — THE RESTORM [t=240s → t=390s]:
  90 seconds into the gap (cooldown not yet expired → 90s < 120s):
  NEW load generator Job launched. 800 clients reconnect over 90s.
  All 8 pods are still running. Load balancer directs reconnects evenly.
  No queue buildup. No connection refused errors. No scale-up lag.
  Controller: sum(active_connections) returns to 800. desired = 8. No action needed.
  Zero reconnection storms observed. Zero oscillation.

FINAL DROP [t=390s → t=570s]:
  Job deleted. All 800 connections drop permanently.
  sum(active_connections) = 0. Cooldown timer starts at t=390s.
  t=390s + 120s = t=510s: Cooldown expires.
  Controller scales down by maxScaleDownStep=2 per cycle:
    8 → 6 (t=510s), 6 → 4 (t=525s), 4 → 2 (t=540s). Floor reached.
```

### Observed Results

**CPU graph**: Non-zero only during active ping phases in Cycles 1 and 2. Flat 0% throughout both DROP periods, both idle transitions, and the entire cooldown window. Replica count shows zero correlation with CPU — definitively proving the controller ignores CPU.

**Connections graph**: Two rectangular blocks of ~800 connections. Clean 90-second gap between them. No overshoot beyond 854 (vs. 1,215 in B2's reconnection storm — 30× smaller relative excess).

**Replicas graph**: Steps to 8 during Cycle 1. Stays flat at 8 through the entire DROP 1 gap (90 seconds of zero connections). Stays flat during Cycle 2. Steps cleanly 8 → 6 → 4 → 2 after final 120-second cooldown.

**Zero connection loss**: No `active_connections` drop was caused by any controller action. Every connection that closed did so because of a deliberate client-side Job deletion — not because the controller terminated a pod.

### Why 854 Connections Instead of 800

The Cycle 2 reconnection wave briefly brought 854 connections — the same OS socket state overlap mechanism as in B2 (new connections establish before the server fully clears state from the prior disconnection). The controller correctly responded: `ceil(854/100) = 9`, scaled transiently to 9 pods. This demonstrates real-time proportional accuracy under reconnection conditions.

### Edge Cases

- **Prometheus unreachability is a known failure mode**: If the Prometheus query fails, `sum(active_connections)` is undefined — not 0. The controller must treat query failures as "unknown" and hold replicas, never as "0 connections" which would trigger a mass scale-down.
- **Cooldown is a global sliding window, not per-pod**: The 120-second window covers the entire deployment. If connections drop from 800 to 400, the cooldown for the 400-connection difference starts. If connections recover to 800 before the cooldown expires, the timer resets.
- **Cooldown state is ephemeral without status persistence**: The last-connection timestamp is stored in memory. A controller pod crash loses this state. On restart, if connections happen to be at 0, the controller could scale down prematurely. Fix: persist the timestamp to the `StatefulAutoscaler` `.status` subresource.
- **`maxScaleDownStep: 2` introduces deliberate scale-down latency**: Going from 8 to 2 pods requires `(8-2)/2 × 15s = 45 seconds`. This is a safety trade-off: slower scale-down is safe; abrupt scale-down risks catching live connections in the SIGTERM limbo window even when connections are already at 0 (due to the 15-second scrape lag).
- **Controller and HPA must not coexist on the same Deployment**: They both write to `deployment.spec.replicas` — the last writer wins, creating oscillation. `run-experiment-c.sh` explicitly deletes any existing HPA before applying the StatefulAutoscaler CR.

---

## 12. Custom Controller: Architecture Deep Dive

**Location**: `controller/`

Built with **Kubebuilder** — the official Go framework for Kubernetes operators. Kubebuilder scaffolds the project structure, generates CRD YAML from Go type annotations via `controller-gen`, manages RBAC, and wires the reconciliation loop into the `controller-runtime` manager.

### Key Files

| File | Purpose |
|------|---------|
| `api/v1alpha1/statefulautoscaler_types.go` | Go structs defining all CRD spec and status fields |
| `internal/controller/statefulautoscaler_controller.go` | The full reconciliation loop |
| `internal/controller/prometheus.go` | HTTP client querying the Prometheus instant query API |
| `config/crd/` | Auto-generated CRD YAML (`make manifests`) |
| `config/rbac/` | Auto-generated ClusterRole and binding |
| `config/manager/` | Controller Deployment and ServiceAccount manifests |
| `config/samples/` | Example `StatefulAutoscaler` CR YAML |
| `cmd/main.go` | Operator entry point; registers the controller with the manager |
| `Dockerfile` | Multi-stage build: `golang:1.25` → `distroless/static:nonroot` |

### The Reconciliation Loop

```go
func (r *StatefulAutoscalerReconciler) Reconcile(
    ctx context.Context, req reconcile.Request,
) (reconcile.Result, error) {

    // 1. Fetch the StatefulAutoscaler CR
    autoscaler := &autoscalingv1alpha1.StatefulAutoscaler{}
    if err := r.Get(ctx, req.NamespacedName, autoscaler); err != nil {
        return reconcile.Result{}, client.IgnoreNotFound(err)
    }

    // 2. Fetch the target Deployment
    deployment := &appsv1.Deployment{}
    r.Get(ctx, types.NamespacedName{
        Name:      autoscaler.Spec.TargetRef.Name,
        Namespace: req.Namespace,
    }, deployment)
    currentReplicas := *deployment.Spec.Replicas

    // 3. Query Prometheus: sum(active_connections) across all pods
    totalConnections, err := queryPrometheus("sum(active_connections)")
    if err != nil {
        // Safe default: query failure → do not scale down.
        // Log the error and retry at next reconciliation interval.
        return reconcile.Result{RequeueAfter: 15 * time.Second}, nil
    }

    // 4. Compute desired replicas via ceiling division
    desired := int32(math.Ceil(
        float64(totalConnections) /
        float64(autoscaler.Spec.TargetConnectionsPerPod),
    ))
    desired = max32(desired, autoscaler.Spec.MinReplicas)
    desired = min32(desired, autoscaler.Spec.MaxReplicas)

    // 5. Scale-down cooldown gate
    if desired < currentReplicas {
        elapsed := time.Since(r.lastConnectionsDroppedAt)
        if elapsed.Seconds() < float64(autoscaler.Spec.ScaleDownCooldownSeconds) {
            // Cooldown not yet expired. Hold pods warm. Retry after remaining cooldown.
            remaining := time.Duration(autoscaler.Spec.ScaleDownCooldownSeconds) *
                         time.Second - elapsed
            return reconcile.Result{RequeueAfter: remaining}, nil
        }
    }

    // 6. Rate-limit scale-down to maxScaleDownStep pods per cycle
    if currentReplicas-desired > autoscaler.Spec.MaxScaleDownStep {
        desired = currentReplicas - autoscaler.Spec.MaxScaleDownStep
    }

    // 7. Patch the Deployment if change is needed
    if desired != currentReplicas {
        deployment.Spec.Replicas = &desired
        r.Update(ctx, deployment)
    }

    return reconcile.Result{RequeueAfter: 15 * time.Second}, nil
}
```

### Prometheus HTTP Query Client (`prometheus.go`)

```go
func queryPrometheus(query string) (float64, error) {
    url := fmt.Sprintf(
        "http://prometheus.monitoring.svc.cluster.local:9090/api/v1/query?query=%s",
        url.QueryEscape(query),
    )
    resp, err := http.Get(url)
    if err != nil {
        return 0, fmt.Errorf("prometheus unreachable: %w", err)
    }
    defer resp.Body.Close()

    var result PrometheusQueryResponse
    if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
        return 0, fmt.Errorf("prometheus response decode error: %w", err)
    }

    if len(result.Data.Result) == 0 {
        return 0, nil  // No active pods reporting the metric yet
    }

    // result.Data.Result[0].Value[1] is the scalar value as a JSON string
    val, err := strconv.ParseFloat(result.Data.Result[0].Value[1].(string), 64)
    return val, err
}
```

The query `sum(active_connections)` aggregates the Gauge metric across all pods matching the metric name. This gives the total cluster-wide connection count regardless of how many pods are running or how connections are distributed across them.

### Building and Deploying the Controller

```bash
cd controller/

# 1. Generate CRD YAML and DeepCopy functions from Go struct annotations
make manifests generate

# 2. Build the Docker image
make docker-build IMG=localhost/stateful-autoscaler:latest

# 3. Load into kind cluster (no image registry required for local testing)
kind load docker-image localhost/stateful-autoscaler:latest

# 4. Install the CRD into the cluster
make install

# 5. Deploy the controller
make deploy IMG=localhost/stateful-autoscaler:latest

# 6. Apply a StatefulAutoscaler instance
kubectl apply -f config/samples/statefulautoscaler.yaml

# Verify
kubectl get statefulautoscaler -A
kubectl logs -n controller-system deployment/controller-manager --follow
```

### RBAC Design

Permissions are declared as Go annotation comments directly in the controller source. `make manifests` compiles them into a `ClusterRole` YAML automatically:

```go
// +kubebuilder:rbac:groups=autoscaling.star.io,resources=statefulautoscalers,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=autoscaling.star.io,resources=statefulautoscalers/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=apps,resources=deployments,verbs=get;list;watch;update;patch
// +kubebuilder:rbac:groups=core,resources=pods,verbs=get;list;watch
```

The controller needs only these exact permissions — principle of least privilege. It cannot create Deployments, cannot delete pods directly, and cannot access any other resource type.

### Docker Build Pipeline

```dockerfile
# Stage 1: Compile Go binary
FROM golang:1.25 AS builder
WORKDIR /workspace
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -a -o manager cmd/main.go

# Stage 2: Minimal runtime image
FROM gcr.io/distroless/static:nonroot
WORKDIR /
COPY --from=builder /workspace/manager .
USER 65532:65532
ENTRYPOINT ["/manager"]
```

`CGO_ENABLED=0` produces a fully static binary with no shared library dependencies. `distroless/static:nonroot` provides a minimal runtime — no shell, no package manager, no root. The final image is typically ~20MB.

---

## 13. Phase 2 — MQTT Experiments (Future Work)

**Location**: `md/mqtt-experiment-plan.md`, `workloads/mqtt/`, `load-generator/mqtt-client/`

### Motivation

Generalising the research from one protocol to two substantially strengthens the contribution. If the same structural failure modes appear in MQTT workloads and the same connection-aware controller resolves them, the claim extends beyond WebSockets to the broader class of persistent-connection protocols.

### What Makes MQTT Structurally Equivalent

MQTT clients connect to a **broker** and maintain persistent TCP sessions. Each client subscribes to topics or publishes messages via those persistent connections. From a scaling perspective, this is identical to WebSockets: N devices = N persistent connections distributed across some set of broker pods. Terminating a pod kills all its device sessions.

### Why a Custom Python Broker Rather Than Mosquitto

Vanilla Mosquitto does not expose a Prometheus-format `/metrics` endpoint and does not support a `/drain` graceful-handoff mechanism. A custom Python broker using the `amqtt` asyncio library mirrors the WebSocket server architecture exactly:

```python
# workloads/mqtt/app/broker.py
# MQTT on port 1883 (amqtt)
# /metrics on port 8080  → active_connections, new_connections_total
# /drain on port 8080    → stops accepting new connections, drains existing gracefully
```

### Three Planned Experiments

| Experiment | Scaler | Objective |
|-----------|--------|-----------|
| **MQTT-A** (HPA Baseline) | CPU HPA | Does HPA disrupt active MQTT sessions during scale-down? (B3 analogue) |
| **MQTT-B** (StatefulAutoscaler) | Custom controller | Does the STAR controller scale MQTT brokers proportionally with zero session disruption? (C analogue) |
| **MQTT-C** (Idle Connections) | Both compared | Does HPA waste resources when MQTT devices are idle, while STAR holds correct capacity? |

### MQTT-Specific State Destruction Risk

MQTT session state stored per-connection:
- **QoS 1/2 message queues**: unacknowledged messages stored broker-side
- **Topic subscriptions**: device → topic mappings
- **Retained messages**: per-topic state

If a broker pod is killed, all of this state is immediately lost. Devices must re-subscribe to all topics on reconnect. For QoS 1 messages not yet acknowledged, the broker's dead message queue is unrecoverable — messages may be lost or duplicated depending on the client's retry implementation. This is arguably worse than WebSocket disconnects for IoT applications managing physical hardware state.

---

## 14. Edge Cases and System-Level Caveats

### Kubernetes and Infrastructure

#### 1. The 30-Second Termination Limbo (Critical)
When any Kubernetes scale-down occurs, pods follow the exact sequence: `Terminating` state → removed from Service endpoints → `SIGTERM` to container → 30-second countdown → `SIGKILL` → OS closes all file descriptors → TCP RST to all connected clients. This is not a bug. It is the built-in graceful shutdown mechanism — designed for stateless apps draining in-flight requests. For WebSocket sessions lasting hours, 30 seconds provides zero actual protection. The connection is permanently doomed the moment HPA issues the scale-down; clients just don't know it yet.

**Every "HPA scale-down event" in experiment graphs is followed by a ~35-second delay before connections actually drop.** This delay = 30s SIGTERM window + ~5s for endpoint propagation and SIGTERM delivery latency.

#### 2. The `kind` Cluster is Single-Machine
All nodes are Docker containers on one host. CPU resource measurements compete with host OS overhead. Inter-node latency is negligible (Docker bridge ≈ loopback). Scheduling behavior may differ from real multi-node clusters. Results are directionally valid; absolute timing and CPU numbers would differ on cloud infrastructure.

#### 3. `metrics-server` Requires `--kubelet-insecure-tls` in Kind
`kind` kubelets use self-signed TLS certificates. `metrics-server` rejects these by default. The `--kubelet-insecure-tls` flag bypasses certificate validation. **Never use this in production.**

#### 4. HPA's 15-Second Evaluation Period Composes with `metric-resolution`
With `metric-resolution=15s` and HPA sync=15s, worst-case HPA reaction latency is up to 30 seconds: up to 15 seconds for the metric to refresh in `metrics-server`, plus up to 15 seconds for HPA's next evaluation cycle. Both clocks are independent and unsynchronized.

#### 5. HPA Never Scales Below `minReplicas`
Even at 0 connections and 0% CPU, HPA will not go below `minReplicas=2`. This is intentional: having 0 pods means the first incoming connection would hit a connection-refused error until pod scheduling completes (~10–30 seconds).

---

### WebSocket Protocol Specifics

#### 6. WebSocket-Level Ping Frames Must Be Disabled (`ping_timeout=None`)
The `websockets` Python library sends WebSocket Ping frames to detect dead connections. Under CPU saturation (heavy scale-up load), servers respond slowly to pings. The library may time out and close connections it incorrectly believes are dead — creating false disconnections before HPA has acted. In early B3 runs, connections dropped from 800 to 744 before HPA acted, creating ambiguity. Fix: `ping_timeout=None` disables the mechanism entirely for controlled experiments.

#### 7. Connection Overshoot During Reconnection Storms
In B2-Instrumented, `active_connections` peaked at 1,215 with only 800 clients. Mechanism: all 800 clients reconnect simultaneously; new connection establishment is faster than OS socket state cleanup (`CLOSE_WAIT`, `TIME_WAIT`). Both old (zombie) socket state objects and new (live) connection handler objects are simultaneously counted by the Gauge. Resolves naturally in 15–30 seconds.

**Implication**: During a reconnection storm, the server transiently handles more connection objects than the replica count was sized for. Service degradation risk is proportional to the overshoot magnitude.

#### 8. Load Balancing is Not Perfectly Uniform
Kubernetes Services route new connections via round-robin/randomized selection at the iptables/IPVS level. Pods added at different cluster sizes accumulate different connection counts. Scale-down step sizes therefore vary depending on which pods Kubernetes selects for termination.

---

### Custom Controller Specifics

#### 9. Prometheus Unreachability Must Default to "Hold" Not "Scale Down"
If the Prometheus HTTP query fails, the connection count is **unknown — not zero**. Treating an error response as 0 connections would trigger an immediate mass scale-down during any Prometheus restart or transient network hiccup. The controller must hold current replicas and retry silently.

#### 10. Cooldown State is Ephemeral Without Status Subresource Persistence
`scaleDownCooldownSeconds` is tracked via an in-memory timestamp in the controller process. If the controller pod crashes and restarts, the timestamp is lost. A restart coinciding with 0 active connections could permit a premature scale-down below the intended holding period. Resolution: persist `lastConnectionActiveTimestamp` to the `StatefulAutoscaler` `.status` field so it survives controller restarts.

#### 11. HPA and StatefulAutoscaler Must Not Coexist on the Same Deployment
Both write to `deployment.spec.replicas`. The last writer wins — creating a fight that oscillates the replica count unpredictably. They are mutually exclusive. `run-experiment-c.sh` explicitly deletes any existing HPA object before applying the `StatefulAutoscaler` CR.

#### 12. `maxScaleDownStep` Trades Safety for Latency
With `maxScaleDownStep=2` and a 15-second reconciliation period, scaling from 8 to 2 pods requires `(8-2)/2 × 15s = 45 seconds`. During this time, extra idle pods consume cluster resources. This is a deliberate safety trade-off: a bounded, gradual scale-down ensures that even if the cooldown expires just as new connections are arriving (a race condition), the blast radius of any mistaken scale-down is limited to 2 pods per cycle.

---

### Measurement and Analysis

#### 13. Prometheus's 15-Second Scrape Creates Step Functions in Plots
All connection metrics appear as discrete jumps at 15-second intervals. The true instantaneous connection count between scrapes is unobservable. When a "36-second connection drop delay" is reported, the actual delay was 30 seconds of SIGTERM + <6 seconds of scrape timing. All event timestamps have a ±15-second uncertainty envelope.

#### 14. Experiment Logs Are Wall-Clock Timestamped
Raw logs use `date +%s%3N` (Unix milliseconds from host wall clock). If collection scripts have variable loop overhead, timestamps from `cpu.log`, `hpa.log`, and `connections.log` may not be perfectly synchronized. When correlating across log files, always verify monotonicity and check for clock drift artifacts before interpreting cross-log timing.

---

## 15. Evidence Chain and Experiment Progression

Each experiment answers one specific question and directly motivates the next. The chain is deliberately ordered from least to most severe failure, culminating in complete resolution.

```
[Base Paper — KRM]
  Question:    Does CPU-based HPA work for stateless HTTP workloads?
  Finding:     Yes. Lag is proportional to metric-resolution. CPU is a lagging indicator.
  Implication: Metric freshness matters. But this only applies to stateless apps.

[Base Paper — PCM]
  Question:    Can Prometheus custom metrics improve HPA for stateless HTTP?
  Finding:     Yes. PCM-H (leading) reduces scale-up lag. PCM-CH (hybrid) eliminates saturation.
  Implication: Better signals → better HPA. For stateful apps, the signal is not the bottleneck.

[Experiment A]
  Question:    Can CPU-based HPA mechanically scale a WebSocket workload at all?
  Finding:     Yes — but ONLY under the artificial condition CPU_WORK=1 (CPU ∝ connections).
  Implication: HPA machinery works. The failure modes have not yet been exposed.

[Experiment B1]
  Question:    What happens under realistic cyclic HIGH/LOW load?
  Finding:     HPA hits maxReplicas=15 within 99 seconds. 87.5% over-provision.
               650-second recovery time. HPA paralysed by its own stabilization window.
  Implication: HPA cannot distinguish "idle connections" from "no connections needed."

[Experiment B2 Extended LOW]  ← Pilot run. NOT in paper.
  Question:    Does HPA actually kill live connections when it scales down?
  Finding:     Yes (qualitatively observed). Reconnection storms happen. Nothing measured.
  Implication: Failure is real. Full instrumentation required. Superseded entirely by B2-Instrumented.

[Experiment B2 Instrumented]
  Question:    How severe is the reconnection storm, precisely?
  Finding:     Peak 1,400 conn/s. Connection overshoot to 1,215 (51.9% above 800 target).
               Causality proven: each storm spike within 15s of each HPA scale-down event.
  Implication: Chaos is quantified. Root cause remains: HPA's blindness to idle connections.

[Experiment B3]
  Question:    Does HPA destroy connections even when they are idle with zero CPU?
  Finding:     Yes. All 800 connections permanently destroyed in a staircase pattern.
               30-second termination limbo documented at timestamp granularity (35–43s gaps).
  Implication: Fatal flaw definitively proven. The custom controller is now necessary.

[Experiment C]
  Question:    Does a connection-aware controller with cooldown semantics solve every failure mode?
  Finding:     Yes. 8 pods for 800 connections (vs. HPA's 15). Zero connection loss.
               All pods held warm through 90-second complete dropout gap.
               Zero reconnection storms. CPU=0% throughout — controller ignores CPU entirely.
  Implication: Connection-aware scaling with stabilization-window semantics is both necessary
               and sufficient for managing stateful WebSocket workloads at production scale.

[Future — MQTT]
  Question:    Does the problem and solution generalise beyond WebSockets?
  Expected:    Same failures (HPA kills broker sessions), same fix (controller holds pods warm).
  Implication: The contribution applies to the category: persistent-connection workloads.
```

---

## 16. Key Numbers Across All Experiments

The B2-Extended LOW pilot row is included for completeness. It is not a standalone paper experiment.

| Metric | Exp A | Exp B1 | ~~B2-Extended~~ *(pilot)* | Exp B2-Inst | Exp B3 | Exp C |
|--------|-------|--------|--------------------------|-------------|--------|-------|
| **In paper?** | ✅ | ✅ | ❌ Pilot | ✅ | ✅ | ✅ |
| Scaler | HPA | HPA | HPA | HPA | HPA | **Custom** |
| `CPU_WORK` | 1 | 1 | 1 | 1 | 1 | **0** |
| Scale-down window | 300s | 300s | 300s | 60s | **60s** | N/A (cooldown) |
| Target connections | 400 | 500 | ~500 | **800** | **800** | **800** |
| Correct replica count | 4 | 5 | 5 | 8 | 8 | **8** |
| Peak connections seen | 388 | 419 | ~500 (no metric) | **1,215\*** | ~800 | 854 |
| Peak replicas reached | 5 | **15** | 10 | **15** | 15 | **8–9** |
| `maxReplicas` hit? | No | **Yes (99s)** | Partial | **Yes** | Yes | **Never** |
| Reconnection storm | ❌ None | ❌ None | ✅ Unquantified | ✅ **1,400/s** | ✅ Yes | **❌ Zero** |
| Connections permanently lost | 0 | 0 | Many (unknown) | Many (measured) | **All 800** | **Zero** |
| Termination limbo documented | N/A | N/A | Qualitative | N/A | **35–43s/event** | N/A |
| Connection-aware? | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| Over-provision waste | ~25% | **87.5%** | ~50% | **87.5%** | **87.5%** | **~0%** |

\* Overshoot above 800-client ceiling due to OS socket state overlap during reconnection storm.

---

## 17. Glossary

| Term | Definition |
|------|-----------|
| **`active_connections`** | Prometheus Gauge metric tracking current open WebSocket sessions on a pod. Goes up on connect, down on disconnect. Primary scaling signal for the StatefulAutoscaler. |
| **cAdvisor** | Container Advisor. Daemon embedded in each Kubernetes kubelet. Collects per-container CPU, memory, and network metrics. Source of all CPU data consumed by metrics-server. |
| **Ceiling Division** | `ceil(a/b)` — always rounds up. Used by both HPA and the StatefulAutoscaler to ensure the integer replica count is never below the mathematical requirement. |
| **Cooldown Window** | Time-bounded suppression of scale-down decisions. If `sum(active_connections)` drops to zero, the StatefulAutoscaler waits `scaleDownCooldownSeconds` before reducing replicas, absorbing transient disconnection gaps. |
| **CRD (Custom Resource Definition)** | Kubernetes API extension mechanism. Defines a new object type (`StatefulAutoscaler`) storable in etcd and manageable with `kubectl` like any native resource. |
| **Distroless Image** | Minimal container base image (`gcr.io/distroless/static:nonroot`). Contains only the statically compiled binary. No shell, no package manager, no root access. |
| **HPA (Horizontal Pod Autoscaler)** | Built-in Kubernetes controller. Evaluates a proportional control law over resource metrics every 15 seconds and adjusts pod replica count accordingly. |
| **kind** | Kubernetes in Docker. Runs multi-node Kubernetes clusters entirely inside Docker containers on a single machine. Used for all experiments. |
| **KRM (Kubernetes Resource Metrics)** | Native K8s metric pipeline: cAdvisor → kubelet → metrics-server → `metrics.k8s.io` API → HPA. Provides CPU and memory utilization. |
| **Kubebuilder** | Go framework for building Kubernetes operators. Scaffolds project structure, generates CRD YAML and RBAC manifests from Go annotations, integrates with controller-runtime. |
| **Lagging Indicator** | A metric that reflects workload intensity only after the workload has fully manifested. CPU utilization is a lagging indicator. |
| **Leading Indicator** | A metric that signals workload intensity at or before demand arrives. HTTP request rate is a leading indicator. |
| **`maxScaleDownStep`** | StatefulAutoscaler parameter. Maximum pods removed per single reconciliation cycle. Prevents catastrophic sudden scale-downs. |
| **metrics-server** | Lightweight K8s add-on aggregating CPU/memory from kubelets, exposing via `metrics.k8s.io` API for HPA. Requires `--metric-resolution=15s` and `--kubelet-insecure-tls` in kind. |
| **MQTT** | Message Queuing Telemetry Transport. Lightweight publish-subscribe protocol for IoT. Clients maintain persistent TCP sessions with a broker. |
| **`new_connections_total`** | Prometheus Counter. Monotonically increases by 1 per new WebSocket connection (including reconnections). `rate(new_connections_total[15s])` measures storm intensity. |
| **Operator** | A Kubernetes application that watches custom resources and reconciles desired vs. actual cluster state automatically. The StatefulAutoscaler is an operator. |
| **PCM (Prometheus Custom Metrics)** | HPA extension routing application Prometheus metrics through the Prometheus Adapter to `custom.metrics.k8s.io`, allowing HPA to scale on arbitrary application signals. |
| **Pod** | Smallest Kubernetes deployable unit. Wraps one or more containers. One running instance of the application. |
| **`pod_seconds`** | `replicas × duration`. Proxy metric for cluster resource cost. Used in the base paper to quantify over-provisioning waste. |
| **Prometheus** | Open-source monitoring system. Scrapes `/metrics` HTTP endpoints from applications at a configured `scrape_interval`. Stores metric values as time series queryable via PromQL. |
| **Prometheus Adapter** | Kubernetes API server extension. Translates PromQL query results into `custom.metrics.k8s.io` API format for HPA consumption. |
| **RBAC** | Role-Based Access Control. Kubernetes permission system. Grants specific verbs on specific resource types to specific service accounts. |
| **Reconciliation Loop** | The `observe → compare → act` pattern executed continuously by every Kubernetes controller. StatefulAutoscaler: query Prometheus → compute desired replicas → patch Deployment. |
| **Reconnection Storm** | Burst of concurrent TCP reconnection attempts after a server-side mass disconnection. Peak measured rate: 1,400 connections/second (B2-Instrumented). |
| **SIGKILL** | Unix signal 9. Forces immediate OS-level process termination. No cleanup possible. All open file descriptors (including TCP sockets) closed instantly — TCP RST sent to all connected clients. |
| **SIGTERM** | Unix signal 15. Requests graceful process termination. Signal handlers can execute. Python `websockets` servers have no default WebSocket connection drain handler. |
| **Staircase Effect** | Discrete, step-wise HPA scaling behavior caused by a Prometheus `scrape_interval` longer than the HPA evaluation period. HPA sees the same stale metric value for multiple consecutive evaluation cycles, generating multiple scale steps from a single load change. |
| **StatefulAutoscaler** | The custom CRD and Kubernetes operator built in this project. Scales Deployments on `sum(active_connections)` from Prometheus with sliding-window cooldown and `maxScaleDownStep` rate limiting. |
| **Stateful Workload** | Application maintaining per-client session state across multiple requests/messages (WebSocket, MQTT, gRPC streaming, SSH). |
| **Stateless Workload** | Application handling each request independently with no memory between requests (HTTP REST API, stateless microservice). |
| **Stabilization Window** | Duration for which a scaling condition must hold continuously before HPA executes the action. Default: 300s for scale-down, 0s for scale-up. The primary tuning knob in experiments B2-Inst (60s) and B3 (60s). |
| **`terminationGracePeriodSeconds`** | Kubernetes pod specification field. Default: 30. Seconds Kubernetes waits after `SIGTERM` before escalating to `SIGKILL`. During this window, existing TCP connections remain alive but the pod is removed from Service routing — creating zombie connections. |
| **WebSocket** | Protocol (RFC 6455). Full-duplex, persistent communication over a single TCP connection. Session persists until explicitly closed by either party. Primary protocol evaluated in this research. |
| **Zombie Connection** | An active TCP session on a pod in `Terminating` state. The connection is live and functional but will be forcibly killed at `SIGKILL` after the grace period. The client does not yet know. No new traffic is routed to the pod, but the existing session continues consuming OS resources (file descriptors, kernel socket buffers). |
