# Experiment E: KEDA Baseline

## Purpose

This experiment tests whether **KEDA (Kubernetes Event-Driven Autoscaling)**, a popular
off-the-shelf autoscaler, handles the stateful WebSocket restorm scenario, and how it compares
to the custom StatefulAutoscaler (Experiment C) and HPA with custom metrics (Experiment D).

KEDA is the most relevant prior art: it supports Prometheus metrics and has a `cooldownPeriod`
that superficially resembles the StatefulAutoscaler's `scaleDownCooldownSeconds`. This
experiment gives an honest answer to "does KEDA solve the problem already?"

## Hypothesis

KEDA will:
1. Scale to 8 replicas correctly for 800 connections ✓
2. **Likely hold pods through DROP 1** because `cooldownPeriod=120` matches the 90-second gap ✓
3. Differ from the StatefulAutoscaler in: no `maxScaleDownStep` rate limiting, KEDA updates
   HPA `minReplicas` rather than `spec.replicas` directly, and the `cooldownPeriod` is a
   global timer rather than a sliding high-water-mark window

**Expected finding:** KEDA may perform similarly to the StatefulAutoscaler for this specific
90-second dropout scenario. The paper should report this honestly. The differentiator then
becomes explicitness, control granularity, and inspectability — not raw capability.

## Required Components

### 1. KEDA operator
```bash
kubectl apply -f https://github.com/kedacore/keda/releases/download/v2.13.0/keda-2.13.0.yaml
```

### 2. ScaledObject
The `ScaledObject` replaces the HPA and scales based on a Prometheus query.

## Setup

```bash
# Install KEDA
kubectl apply -f https://github.com/kedacore/keda/releases/download/v2.13.0/keda-2.13.0.yaml
kubectl wait --for=condition=ready pod -l app=keda-operator -n keda --timeout=120s

# Remove any existing HPA for the deployment (KEDA manages its own HPA)
kubectl delete hpa websocket-hpa --ignore-not-found=true

# Deploy ScaledObject
kubectl apply -f manifests/keda-scaledobject.yaml

# Verify KEDA created the HPA
kubectl get hpa

# Run experiment
../../../scripts/run-experiment-e.sh
```

## Load Pattern

Same as Experiments C and D (2-cycle restorm simulation):
- CYCLE 1 (0–150s): 800 clients ramp up
- DROP 1 (150–240s): 90-second gap (clients deleted)
- CYCLE 2 (240–390s): 800 fresh clients
- FINAL DROP (390–570s): permanent disconnect

## Expected Results

| Phase | Expected replicas | Notes |
|-------|------------------|-------|
| CYCLE 1 | 8 (correct) | KEDA scales based on `sum(active_connections) / 100` |
| DROP 1 | Likely 8 (held by cooldown) | `cooldownPeriod=120s` > 90s gap |
| CYCLE 2 | 8 (if held) or 2→8 (if scaled down) | Key test |
| FINAL DROP | 8 → 2 after 120s | Graceful |

## KEDA vs StatefulAutoscaler Comparison Points

| Aspect | KEDA | StatefulAutoscaler |
|--------|------|--------------------|
| Cooldown mechanism | Global `cooldownPeriod` timer | Sliding high-water-mark window |
| Scale-down rate limiting | No per-cycle `maxScaleDownStep` | `maxScaleDownStep=2` per cycle |
| Convergence bound | Not explicit | `ceil((current-desired)/maxStep)` cycles |
| Metric update mechanism | Updates HPA `minReplicas` | Patches `spec.replicas` directly |
| Inspectability | Via HPA status + ScaledObject | Via CRD status field |
| Connection lifecycle (SIGTERM window) | Not modeled | Explicitly documented |

## Metrics to Collect

- `active_connections` gauge over time
- `kube_deployment_spec_replicas` over time
- Reconnection rate: `rate(new_connections_total[15s])`
- KEDA-specific: `keda_scaler_active` and `keda_scaler_metrics_value` from KEDA metrics endpoint
