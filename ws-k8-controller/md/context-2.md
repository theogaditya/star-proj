```markdown
# Stateful Autoscaling Research Context

## Project Overview

This project investigates the limitations of Kubernetes Horizontal Pod Autoscaler (HPA) when applied to **persistent-connection workloads**, specifically:

- WebSocket servers
- MQTT brokers (planned next phase)

The core research question is:

> Can CPU-based reactive autoscaling (default HPA) handle cyclic connection churn in stateful systems without causing instability, inefficiency, or connection disruption?

The long-term goal is to design and implement a **Kubernetes-native connection-aware autoscaler** that addresses the shortcomings observed in default HPA.

---

# Architecture Overview

## Cluster Environment

- Kubernetes cluster: **kind (multi-node)**
- Fresh cluster created for every experiment
- Metrics Server installed per experiment
- Prometheus installed when required
- All experiments fully automated via scripts

Each experiment:
1. Deletes old cluster
2. Creates new multi-node kind cluster
3. Installs metrics-server
4. Builds and loads Docker images
5. Deploys workload
6. Applies HPA configuration
7. Runs load generator
8. Collects logs
9. Runs analysis
10. Deletes cluster

This ensures reproducibility and isolation between runs.

---

# Workload Architecture

## WebSocket Server

Location:
```

workloads/websocket/

```

Characteristics:
- Persistent connections
- Exposes:
  - CPU load
  - Active connection metric at `/metrics`
- Designed to simulate real-time stateful communication

## Load Generator

Location:
```

load-generator/websocket-client/

```

Behavior:
- Spawns large number of concurrent WebSocket clients
- Used to simulate:
  - Monotonic load (Experiment-A)
  - Cyclic churn (Experiment-B variants)

---

# Autoscaling Mechanism

All experiments use Kubernetes HPA (autoscaling/v2).

Scaling metric:
- CPU utilization
- Target: 60% average utilization

HPA parameters modified across experiments:
- maxReplicas
- scaleDown.stabilizationWindowSeconds

This allows controlled study of control-loop dynamics.

---

# Experiment Structure

```

experiments/websocket/
├── experiment-a-hpa/
├── experiment-b1-hpa-churn-60s-stab/
└── experiment-b2-hpa-churn-extended-low/

```

Raw results:
```

results/raw/websocket/<experiment-name>/

```

Processed results:
```

results/processed/websocket/<experiment-name>/

```

Each experiment generates:

Raw logs:
- cpu.log
- hpa.log
- active_connections.log
- phase.log (for cyclic experiments)

Processed CSV:
- cpu.csv
- replicas.csv
- connections.csv

Plots:
- cpu.png
- replicas.png
- connections.png

---

# Experiment-A: Baseline Monotonic Scaling

### Objective

Validate that HPA behaves correctly under monotonic load increase and decrease.

### Configuration

- Default HPA behavior
- maxReplicas: 10
- stabilizationWindowSeconds: default (300s)
- Load pattern: steady increase → steady decrease

### Observed Behavior

- Clean scale-up
- Clean scale-down
- No oscillation
- No control-loop instability

### Conclusion

HPA performs correctly under monotonic stateless-style load.

This establishes baseline correctness.

---

# Experiment-B1: Cyclic Churn (Short LOW, 60s Stabilization)

### Objective

Test HPA under connection churn.

### Configuration

- HIGH phase: 60 seconds
- LOW phase: 30 seconds
- stabilizationWindowSeconds: 60
- maxReplicas: 15

### Observed Behavior

- Rapid scale-up to maxReplicas
- CPU drops during LOW but replicas remain high
- No meaningful scale-down during LOW
- Sustained overprovisioning

### Interpretation

HPA prioritizes stability (damping) over efficiency.

Under churn:
- It prevents oscillation
- But wastes resources

This demonstrates inefficiency under persistent connection workloads.

---

# Experiment-B2: Cyclic Churn (Extended LOW, 60s Stabilization)

### Objective

Force real scale-down between cycles to induce oscillation.

### Configuration

- HIGH phase: 60 seconds
- LOW phase: 90 seconds
- stabilizationWindowSeconds: 60
- maxReplicas: 15

### Observed Behavior

- Scale-up during HIGH
- Partial scale-down during LOW
- Re-scale-up during next HIGH
- Repeated replica oscillation (e.g., 8 → 3 → 8 → 4 → 8)

### Interpretation

Reduced damping + extended low period results in:

- Control-loop oscillation
- Reactive instability
- Replica thrashing

This demonstrates that CPU-based HPA cannot simultaneously achieve:
- Stability
- Efficiency
- Responsiveness

Under persistent connection churn.

---

# Key Technical Findings So Far

1. HPA is stable under monotonic load.
2. Under cyclic connection churn:
   - With large stabilization → overprovisioning.
   - With reduced stabilization → oscillation.
3. CPU is a lagging metric for connection-heavy systems.
4. Reactive autoscaling is insufficient for stateful workloads.

---

# Measurement and Instrumentation

Collected metrics:

- Total CPU (sum across pods)
- Replica count
- Active connections
- Phase markers (HIGH/LOW transitions)

Analysis pipeline:

1. `parse_logs.py`
2. `plot_experiment.py`
3. Automated via `run-analysis.sh`

Plots normalized to start at t=0.
Phase transitions shown as vertical markers.

---

# Identified Limitations of HPA

Under persistent-connection workloads:

- Scale-down may terminate pods with active sessions.
- Reconnection storms spike CPU.
- CPU does not represent connection pressure directly.
- Control-loop lag induces instability or inefficiency.

---

# Next Research Direction

Move beyond CPU-based HPA toward:

## Stateful Autoscaler (Planned)

Goals:

- Connection-aware scaling
- Graceful scale-down (drain before terminate)
- Avoid reconnection storms
- Reduce oscillation
- Improve efficiency under churn

Architecture concept:

Custom Kubernetes controller that:
- Observes active connections per pod
- Implements safe scale-down thresholds
- Controls scaling behavior explicitly
- Possibly replaces HPA entirely

---

# Research Narrative So Far

The experimental sequence demonstrates:

1. HPA works for stateless workloads.
2. HPA struggles under stateful churn.
3. Trade-off exists between damping and efficiency.
4. CPU-based feedback is insufficient for connection-oriented systems.

This forms the justification for a custom connection-aware autoscaler.

---

# Current Status

Completed:
- Experiment-A
- Experiment-B1
- Experiment-B2
- Full automated pipeline
- Oscillation successfully induced

Next Phase:
- Implement connection-aware autoscaling controller
- Compare against B1 and B2
- Repeat experiments with custom controller
- Evaluate stability vs efficiency vs connection safety

---

# Final Research Objective

Demonstrate that:

> A Kubernetes-native connection-aware autoscaler provides superior stability and efficiency compared to default CPU-based HPA for persistent-connection workloads.

This document captures all experimental logic, structure, configuration, and interpretation up to this point.
```

If you’d like, I can also generate a second version optimized specifically for feeding into LLMs (with clearer system boundaries and variable definitions).
