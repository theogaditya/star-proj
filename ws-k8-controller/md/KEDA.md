# KEDA Reference and Diagnostics

This document summarizes KEDA's behavior, how it interacts with the Kubernetes HPA, common causes why KEDA may not decrease replicas during a drop in connections, diagnostics you can run, and recommended mitigations. Use this as a quick reference when comparing KEDA behaviour to the custom `StatefulAutoscaler` implemented in this repo.

**What KEDA does:**
- **Watches external metrics:** KEDA supports many scalers (Prometheus, Kafka, RabbitMQ, etc.). For this project we use a Prometheus trigger on `sum(active_connections)`.
- **Manages an HPA:** KEDA does not directly patch `deployment.spec.replicas` during normal operation. Instead it creates/updates an `HPA` (or `ScaledObject` -> `HPA`) and sets `spec.minReplicas` (and `minReplicaCount` in ScaledObject). This communicates the desired floor to HPA.

**How scale-up and scale-down flow works with KEDA:**
- Scale-up: KEDA observes the metric rise → increases `minReplicas` → HPA is required to provision additional pods (HPA still controls provisioning details).
- Scale-down: KEDA observes the metric drop → decreases `minReplicas` → HPA is then permitted to remove pods, and HPA's internal logic performs the actual termination.

**Key KEDA parameters and semantics:**
- `cooldownPeriod` (seconds): a delay KEDA uses before lowering the floor after the metric falls below the target. This prevents immediate downscaling on short drops.
- `minReplicaCount` / `maxReplicaCount`: configured floor/ceiling that KEDA enforces via the HPA it manages.
- Activation/threshold: triggers often have a threshold or activation value; KEDA treats the scaler as "active" while the metric crosses or equals the threshold depending on the trigger configuration.

Why KEDA may not decrease `minReplicas` (practical causes)
- **Cooldown not yet expired:** If `cooldownPeriod` > observed downtime, KEDA will hold the previous `minReplicas` until the period ends.
- **Metric still reports positive connections:** Prometheus scrape interval, RST propagation, and query windowing can cause `sum(active_connections)` to remain >0 for several seconds after pods are killed. Clients may be in an OS-level limbo (TCP RST propagation) that keeps the metric non-zero.
- **Prometheus scrape / query lag:** If Prometheus scrapes every 15s (as in experiments), metric decay is stepwise; KEDA's decision may see the older value for one or more cycles.
- **Query failures / transient errors:** If KEDA cannot query Prometheus, depending on scaler implementation it may choose to treat the scaler as still active or avoid lowering the floor to stay safe.
- **Leftover HPA or conflicting controller:** If an HPA was manually created or left behind, it may set its own `minReplicas`/`spec.replicas` and conflict with KEDA's updates.
- **Permissions / operator errors:** KEDA operator may encounter RBAC/permission issues or runtime errors that prevent it from updating the HPA.

How this interacts with the TCP termination window (important):
- When Kubernetes terminates a pod, clients connected to that pod will only be informed by the OS-level TCP stack after a short propagation period (empirically ~30s in our environment). During that window, the application-level Prometheus metric can still report connections or Prometheus scrape artifacts may keep the metric non-zero; KEDA will therefore consider load not yet fully dropped and will delay lowering `minReplicas` (or HPA will delay killing pods). If many pods are removed at once, those clients re-establish simultaneously, causing a reconnection storm.

Diagnostics (commands you can run during/after a run)
- Inspect ScaledObject and HPA:
  - `kubectl get scaledobject -n <ns>`
  - `kubectl describe scaledobject <name> -n <ns>`
  - `kubectl get hpa -n <ns>`
  - `kubectl describe hpa <hpa-name> -n <ns>`
  - `kubectl get deployment <name> -n <ns> -o yaml`
- Check the ScaledObject YAML (fields to verify):
  - `kubectl get scaledobject <name> -n <ns> -o yaml`
  - Verify `cooldownPeriod`, `minReplicaCount`, `maxReplicaCount`, and trigger config.
- Query Prometheus directly (port-forward + query):
  - `kubectl -n monitoring port-forward svc/prometheus 9090:9090`
  - `curl 'http://localhost:9090/api/v1/query?query=sum(active_connections)'
  - Repeat this query across the DROP interval and for several scrape intervals afterwards to observe decay.
- Check KEDA operator logs (look for scaler decision messages and errors):
  - `kubectl logs -n keda deploy/keda-operator --tail=200`
- Inspect HPA vs Deployment replica fields:
  - `kubectl get hpa <hpa-name> -n <ns> -o yaml`
  - `kubectl get deployment <deploy> -n <ns> -o yaml`

Recommended mitigations and trade-offs
- **Tune `cooldownPeriod` to exceed the RST window + scrape lag**: If the OS-level termination + scrape latency creates a 30–45s tail, set `cooldownPeriod` = RST window + safety margin (e.g., 120s used in experiments). This prevents premature lowering of `minReplicas` during the limbo.
- **Use rate-limited scale-down when appropriate:** KEDA does not expose `maxScaleDownStep`. If you need bounded convergence (max pods removed per cycle), a custom controller (like `StatefulAutoscaler`) that directly patches `spec.replicas` with a `maxScaleDownStep` is the safer option.
- **Ensure no conflicting HPA exists:** Delete any existing HPA before allowing KEDA to manage scaling for a Deployment.
- **Verify Prometheus scrape interval and query windows:** Shorten scrape interval for more responsive decay if cluster load and Prometheus performance allow it, or increase `cooldownPeriod` to match.
- **Monitor operator logs and scaler metrics:** Watch `keda_scaler_active`, `keda_scaler_metrics_value`, and KEDA operator logs to diagnose why a scaler remains active.

Experiment notes (how this repo exercised KEDA)
- In Experiment E we deployed KEDA v2.13.0 with a `ScaledObject` configured for `sum(active_connections)` with `threshold: 100`, `minReplicaCount: 2`, `maxReplicaCount: 15`, and `cooldownPeriod: 120`. This matched the `StatefulAutoscaler`'s `scaleDownCooldownSeconds=120` used in Experiment C.
- Observed behaviour:
  - KEDA held pods through the 90s DROP (cooldown working as intended).
  - After cooldown expiry, KEDA lowered `minReplicas` and HPA performed scale-down without per-cycle step bounding, producing a faster convergence than the `StatefulAutoscaler` in terms of wall-clock completion.
  - Faster convergence translated into a larger single-cycle termination blast radius and measurable reconnection spikes relative to the `StatefulAutoscaler` which used `maxScaleDownStep=2`.

Reference checklist for future experiments
- Always snapshot `ScaledObject` and HPA YAML before and after runs.
- Capture Prometheus query traces for `sum(active_connections)` at 1–5s cadence during DROP to see stepwise decay.
- Collect KEDA operator logs and `keda` metrics (`keda_scaler_metrics_value`, `keda_scaler_active`).

Notes on design choice: why `StatefulAutoscaler` differs
- `StatefulAutoscaler` directly computes desired replicas using `sum(active_connections)` and patches `deployment.spec.replicas` with two protections:
  - a sliding-window `scaleDownCooldownSeconds` that suppresses scale-down when recent history indicates higher demand, and
  - a `maxScaleDownStep` that bounds the number of pods removed per reconciliation cycle.
- These two controls together produce a deterministic convergence bound:
  - `ceil((current - desired) / maxScaleDownStep)` cycles to reach `desired` after cooldown expires.

Contact / further reading
- KEDA docs: https://keda.sh/docs/
- KEDA Prometheus scaler: https://keda.sh/docs/2.13/scalers/prometheus/

---
Generated on: April 29, 2026
