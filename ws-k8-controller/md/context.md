# Connection-Aware Stateful Autoscaler for Persistent Workloads in Kubernetes

## 1. Project Overview

This project aims to design, implement, and evaluate a Kubernetes-native autoscaler specifically built for **stateful, persistent-connection workloads** such as WebSocket servers and MQTT brokers.

Traditional Kubernetes Horizontal Pod Autoscaler (HPA) is optimized for stateless request/response workloads. It primarily scales based on CPU or memory utilization. However, persistent-connection systems (e.g., WebSocket backends, IoT gateways using MQTT, streaming RPC services) exhibit fundamentally different scaling dynamics:

- Connections are long-lived.
- Load is often connection-bound rather than CPU-bound.
- Scaling down can abruptly terminate active sessions.
- Pod deletion can trigger large reconnection storms.
- Reconnection storms can destabilize the control loop.

The core goal of this project is to build a **custom Kubernetes controller** that replaces default HPA behavior for persistent workloads by:

1. Scaling based on active connection density.
2. Implementing graceful draining before scale-down.
3. Preventing reconnection storms.
4. Introducing stability mechanisms such as cooldown windows and scaling rate limits.

This project is research-focused but architected to be production-aligned and deployable on real Kubernetes clusters (EKS, GKE, or self-managed clusters).



## 2. Problem Definition

### 2.1 Why Default HPA Fails for Stateful Systems

Default HPA makes the following assumptions:

- Pods are stateless and interchangeable.
- Requests are short-lived.
- Resource usage (CPU/memory) reflects workload pressure.
- Deleting pods does not cause systemic side effects.

These assumptions break down for:

- WebSocket servers maintaining thousands of persistent connections.
- MQTT brokers managing device sessions.
- Real-time collaborative systems.
- Streaming backends.

In persistent workloads:

- CPU may remain low even when connection counts are dangerously high.
- Memory and file descriptor usage grow per connection.
- Deleting a pod drops all active sessions.
- Dropped sessions trigger reconnection bursts.
- Reconnection bursts create CPU spikes and scaling oscillations.

Thus, scaling must be aware of **connection semantics**, not just resource utilization.



## 3. High-Level Architecture

The system consists of the following components:

### 3.1 Application Layer

- WebSocket server deployment.
- MQTT broker deployment.
- Each pod exposes a `/metrics` endpoint.
- Metrics include:
  - `active_connections`
  - `new_connections_total`
  - `connection_duration_seconds`
  - Optionally: `reconnect_events_total`

### 3.2 Metrics Layer

- Prometheus deployed in the cluster.
- Prometheus scrapes application metrics.
- The custom autoscaler queries Prometheus via HTTP API.

Prometheus is used for the research phase due to:
- Industry-standard usage.
- Time-series support.
- PromQL flexibility.
- Histogram support for latency analysis.

### 3.3 Control Layer

A custom Kubernetes controller (Operator) replaces HPA entirely.

Responsibilities:
- Watch `StatefulHPA` CRD.
- Query Prometheus for connection metrics.
- Compute desired replica count.
- Patch Deployment replicas.
- Manage pod draining lifecycle.
- Enforce cooldown and stability constraints.

No modification to Kubernetes core is required.



## 4. Core Design Principles

### 4.1 Replace HPA Entirely

The project does not wrap or modify HPA behavior.

Reasons:
- HPA internal sync intervals and stabilization windows complicate evaluation.
- Mixing control loops reduces clarity.
- Clean experimental comparison requires full control of scaling logic.

The custom controller becomes the sole scaling authority for stateful workloads.



## 5. Scaling Logic

### 5.1 Base Replica Formula

Initial scaling formula:
```bash
desiredReplicas = ceil(total_active_connections / target_connections_per_pod)
```

Where:
- `total_active_connections` is aggregated across all pods.
- `target_connections_per_pod` is a configured threshold.

### 5.2 Enhancements to Basic Formula

To avoid instability, the formula will include:

- Safety margin multiplier (e.g., 1.1).
- Maximum scale step per control loop.
- Minimum replica floor.
- Maximum replica ceiling.
- Cooldown period between scaling events.

This prevents:
- Overreaction to short spikes.
- Rapid oscillation.
- Resource thrashing.



## 6. Graceful Scale-Down Design

### 6.1 Pod Lifecycle State Machine

Pods transition through:

- `Active`
- `Draining`
- `Terminated`

### 6.2 Draining Protocol

When scaling down:

1. Select pod(s) for termination.
2. Annotate pod with:
```bash
stateful-draining=true
```
3. Application stops accepting new connections.
4. Existing connections continue.
5. Controller monitors `active_connections`.
6. When zero (or timeout reached), pod is deleted.

### 6.3 Drain Timeout

If connections last excessively long:

- A configurable drain timeout will be enforced.
- After timeout, controlled termination occurs.
- Only limited pods can drain simultaneously.

This prevents deadlock and excessive resource holding.

## 7. Reconnection Storm Mitigation

Reconnection storms occur when many sessions drop simultaneously.

Mitigation mechanisms:

- Limit concurrent draining pods.
- Introduce delay between pod terminations.
- Enforce scale-down rate limit.
- Optionally introduce termination jitter.

These measures smooth reconnect bursts and stabilize scaling behavior.


## 8. Stability Controls

To maintain control-loop stability:

- Scale-up cooldown window.
- Scale-down cooldown window.
- Maximum scaling delta per interval.
- Replica floor/ceiling bounds.
- Optional moving average smoothing on metrics.

Without these, connection-based scaling can oscillate under bursty load.



## 9. Experimental Design

### 9.1 Workloads

Two stateful systems:

1. WebSocket server.
2. MQTT broker.

Each exposes connection metrics.

### 9.2 Comparison Structure

For each workload:

Experiment A:
- Default HPA (CPU-based).
- No drain logic.

Experiment B:
- Custom Stateful Autoscaler.
- Connection-aware scaling.
- Graceful drain.

Only the autoscaler changes. Workload remains identical.

This isolates causal impact.



## 10. Load Patterns

Each experiment includes:

1. Gradual connection increase.
2. Sudden connection burst.
3. Sudden connection drop.
4. Optional: node failure simulation.

These patterns expose:

- Scaling responsiveness.
- Stability under burst.
- Reconnection behavior.
- Scale-down correctness.



## 11. Metrics Collected for Evaluation

Primary metrics:

- Total dropped connections.
- Reconnection rate (per second).
- Active connections over time.
- Replica count over time.
- Scaling oscillation frequency.
- Latency (if applicable).
- Replica churn rate.

These metrics are extracted from Prometheus and exported.



## 12. Visualization and Analysis

Python scripts will:

- Query Prometheus or export metrics.
- Generate time-series plots using matplotlib/seaborn.
- Plot:
- Active connections vs time.
- Replica count vs time.
- Reconnect rate vs time.
- Latency vs time.

Graphs will demonstrate:

- HPA instability under stateful load.
- Connection loss during scale-down.
- Reconnection spikes.
- Improved stability under custom controller.

Narrative analysis will accompany each graph.



## 13. Implementation Stack

- Kubernetes cluster (local or cloud).
- Prometheus for metrics.
- Go (controller-runtime) for custom controller.
- CRD: `StatefulHPA`.
- Python for plotting and post-analysis.

No SDK inside application code is required.

Applications only expose metrics and optionally support drain mode.



## 14. Production Alignment

The final system can be deployed by:

1. Installing the operator.
2. Defining a `StatefulHPA` resource.
3. Ensuring application exposes metrics and supports drain behavior.

The controller runs entirely inside the cluster and works across:

- AWS EKS
- Google GKE
- On-prem clusters

Cloud provider APIs are not required.



## 15. Final Project Definition

This project delivers:

A Kubernetes-native, connection-aware autoscaler for persistent-connection workloads that:

- Replaces default HPA.
- Scales based on active connection density.
- Implements safe draining during scale-down.
- Prevents reconnection storms.
- Improves control-loop stability.
- Demonstrates measurable improvement over default HPA.

This design is research-valid, production-aligned, and suitable for publication or advanced systems engineering evaluation.



## File Stucture:
future-work/
│
├── controller/                     # Custom Stateful Autoscaler implementation
│   ├── api/                        # CRD definitions
│   ├── internal/                   # Control loop logic
│   ├── config/                     # RBAC, deployment YAML
│   └── main.go
│
├── workloads/
│   ├── websocket/
│   │   ├── app/                    # WebSocket server source
│   │   ├── k8s/                    # Deployment + Service YAML
│   │   └── metrics/                # Metric definitions
│   │
│   └── mqtt/
│       ├── app/
│       ├── k8s/
│       └── metrics/
│
├── monitoring/
│   ├── prometheus/                 # Prometheus deployment + scrape config
│   └── queries/                    # PromQL queries used in experiments
│
├── experiments/
│   ├── websocket/
│   │   ├── experiment-a-hpa/
│   │   └── experiment-b-stateful/
│   │
│   └── mqtt/
│       ├── experiment-c-hpa/
│       └── experiment-d-stateful/
│
├── load-generator/
│   ├── websocket-client/
│   └── mqtt-client/
│
├── results/
│   ├── raw/
│   │   ├── websocket/
│   │   └── mqtt/
│   │
│   └── processed/
│
├── analysis/
│   ├── export_prometheus.py
│   ├── plot_connections.py
│   ├── plot_reconnects.py
│   └── plot_replicas.py
│
└── README.md