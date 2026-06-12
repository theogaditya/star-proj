# Experiment D: HPA with Custom Connection-Count Metric (Baseline)

## Purpose

This experiment directly answers the reviewer question: **"Why not just configure HPA with
the right metric?"** It tests whether standard HPA, when given `active_connections` as its
scaling signal via the Prometheus Adapter, can match the StatefulAutoscaler's performance.

**Expected answer:** HPA with custom metrics will scale replicas correctly, but it WILL still
scale down during the 90-second dropout gap (because the metric genuinely reads zero and HPA
has no cooldown semantics tied to session lifecycle). This demonstrates that metric selection
alone is insufficient — scale-down lifecycle policy is the missing piece.

## Hypothesis

HPA with a `active_connections_per_pod` custom metric will:
1. Scale to 8 replicas correctly for 800 connections (same as StatefulAutoscaler) ✓
2. Scale down to `minReplicas=2` during DROP 1 despite the 90-second gap being shorter than
   the HPA scale-down stabilization window of 120s → expected to hold IF we use 120s window
3. FAIL to hold pods when connections drop in a scenario where the gap is short (<60s) compared
   to the stabilization window, but then reconnecting clients trigger a reconnection storm
   because pods are gone

The critical differentiator shown: HPA's stabilization window works on metric recommendations
(CPU-derived zero → recommends minReplicas → window stabilizes at minReplicas). The custom
controller's window works on connection-derived counts (last known high-water mark).

## Required Components

### 1. Prometheus Adapter
The Prometheus Adapter must be deployed and configured with a rule exposing
`active_connections` as a custom pod metric.

### 2. Custom HPA
HPA configured to scale on `active_connections_per_pod` with `targetAverageValue: 100`.

## Setup

```bash
# Deploy Prometheus Adapter
kubectl apply -f manifests/prometheus-adapter-configmap.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/prometheus-adapter/v0.12.0/deploy/manifests/deployment.yaml

# Verify custom metric is available
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1" | jq '.resources[] | select(.name | contains("connections"))'

# Deploy HPA
kubectl apply -f manifests/hpa-custom-metric.yaml

# Run experiment
../../../scripts/run-experiment-d.sh
```

## Load Pattern

Same as Experiment C (2-cycle restorm simulation):
- CYCLE 1 (0–150s): 800 clients ramp up
- DROP 1 (150–240s): 90-second gap (all clients deleted)
- CYCLE 2 (240–390s): 800 fresh clients
- FINAL DROP (390–570s): permanent disconnect

## Expected Results

| Phase | Expected replicas | Expected connections lost |
|-------|------------------|--------------------------|
| CYCLE 1 | 8 (correct) | 0 |
| DROP 1 | 8 → 2 (HPA scale-down fires) | 0 (pods not yet serving during DROP) |
| CYCLE 2 start | 2 → 8 (must re-scale) | Reconnection storm likely (1000+ conn/s) |
| FINAL DROP | 8 → 2 (graceful) | 0 |

## Key Difference from Experiment C

In Experiment C (StatefulAutoscaler), DROP 1 results in a "flat bridge" — all 8 pods remain
warm. In Experiment D, we expect HPA to scale down to 2 during DROP 1, even with the same
stabilization window duration, because HPA's window buffers metric-derived recommendations
(which read zero) rather than the last known connection high-water mark.

## Metrics to Collect

Same as Experiment C:
- `active_connections` gauge over time
- `kube_deployment_spec_replicas` over time
- CPU utilization (should be ~0 since `CPU_WORK=0`)
- Reconnection rate: `rate(new_connections_total[15s])`
- Scale reaction time (connection spike → replica count change)
