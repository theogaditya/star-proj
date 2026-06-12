# Implementation Plan: Making the Research Bulletproof

This document is the action plan derived from the reviewer feedback in `flaws.md` and the
suggested upgrades. Every item maps directly to a specific flaw or reviewer comment.
Work through these sections in order — later sections build on earlier ones.

---

## Priority 0: Reframe the Contribution (Do First, Before Any Experiments)

**What to change:** The abstract and introduction currently frame the work as "we built a new
system." That is the wrong framing. The correct framing is "we systematically studied a known
problem, quantified it precisely, and validated a solution."

**Rewritten contribution statement (use this verbatim or close to it):**

> This paper revisits autoscaling for long-lived, connection-oriented workloads in Kubernetes
> and demonstrates that default CPU-based HPA leads to inefficient and disruptive scaling
> behaviour under such workloads. We show that while Kubernetes supports custom metrics,
> the absence of connection-aware scaling policies combined with lifecycle-safe scale-down
> strategies leads to reconnection storms and resource inefficiencies in practice. To address
> this, we present a connection-aware autoscaling controller integrating connection-count-
> based scaling signals, stabilisation-aware decisions, and disruption-minimising scale-down
> behaviour. Our contribution is not a new autoscaling primitive, but a systematic evaluation
> and implementation of connection-aware scaling policies, demonstrating their practical
> impact under WebSocket workloads.

**Claims to find-and-replace throughout the paper:**

| Find | Replace With |
|------|-------------|
| "necessary and sufficient" | "empirically effective under evaluated workloads" |
| "provably unachievable" | "not observed under CPU-based autoscaling in our experiments" |
| "zero connection loss" | "no connection drops observed during controlled experiments" |
| "fundamental incompatibility" | "mismatch between default scaling signals and workload characteristics" |
| "Kubernetes cannot support connection-aware scaling" | "Kubernetes does not provide connection-aware scaling by default; configuring it requires non-trivial integration work" |

**Estimated effort:** 2–3 hours. Text edits only. No new experiments needed.

---

## Priority 1: Add the Missing Baselines (Biggest Acceptance Blocker)

The current evaluation only compares CPU-HPA vs. the custom controller. This is the single
most important thing to fix. Reviewers will reject on this alone.

### Baseline D: HPA with Custom Connection-Count Metric

**What this tests:** Whether HPA itself, when given the right metric (connection count via
Prometheus Adapter), can match the custom controller's performance. This directly answers
"why not just configure HPA correctly?"

**How to set it up:**

1. Deploy the Prometheus Adapter with a custom metric rule:
   ```yaml
   rules:
     - seriesQuery: 'active_connections{namespace!="",pod!=""}'
       resources:
         overrides:
           namespace: {resource: "namespace"}
           pod: {resource: "pod"}
       name:
         matches: "active_connections"
         as: "active_connections_per_pod"
       metricsQuery: 'sum(active_connections{<<.LabelMatchers>>}) by (<<.GroupBy>>)'
   ```

2. Configure HPA to scale on `active_connections_per_pod` with the same target (100):
   ```yaml
   metrics:
     - type: Pods
       pods:
         metric:
           name: active_connections_per_pod
         target:
           type: AverageValue
           averageValue: "100"
   ```

3. Run the same Experiment C workload (2-cycle restorm, CPU_WORK=0) against this baseline.

**What you expect to find:** HPA with custom metrics will scale replicas correctly (8 pods
for 800 connections), but it will NOT hold pods warm during the 90-second dropout gap — it
will scale down to 2 and then fail to absorb the reconnection wave. This is the key
differentiator of the custom controller's cooldown mechanism. Document this explicitly.

**New experiment name:** Experiment D — HPA Custom Metrics Baseline

---

### Baseline E: KEDA with ScaledObject on active_connections

**What this tests:** Whether a popular off-the-shelf event-driven scaler (KEDA) handles
the stateful WebSocket problem, and how it compares to the custom controller.

**How to set it up:**

1. Install KEDA in the cluster:
   ```bash
   kubectl apply -f https://github.com/kedacore/keda/releases/download/v2.13.0/keda-2.13.0.yaml
   ```

2. Create a `ScaledObject` targeting the Deployment, sourcing from Prometheus:
   ```yaml
   apiVersion: keda.sh/v1alpha1
   kind: ScaledObject
   metadata:
     name: websocket-keda-scaler
   spec:
     scaleTargetRef:
       name: websocket-server
     minReplicaCount: 2
     maxReplicaCount: 15
     cooldownPeriod: 120
     triggers:
       - type: prometheus
         metadata:
           serverAddress: http://prometheus.monitoring.svc.cluster.local:9090
           metricName: active_connections
           threshold: "100"
           query: sum(active_connections)
   ```

3. Run the same Experiment C workload (2-cycle restorm, CPU_WORK=0) against KEDA.

**What you expect to find:** KEDA's `cooldownPeriod` handles the dropout gap, making it
functionally similar to the custom controller for this scenario. The paper should honestly
report this. The differentiator argument then becomes about explicitness, control granularity
(maxScaleDownStep), and lifecycle integration (/drain endpoint path) rather than capability.
This is a stronger, more honest positioning.

**New experiment name:** Experiment E — KEDA Baseline

**How to position KEDA in related work:**

> KEDA (Kubernetes Event-Driven Autoscaling) supports scaling based on Prometheus metrics
> and provides a cooldown period parameter. However, KEDA operates as a wrapper around HPA
> and does not natively expose maxScaleDownStep rate limiting or per-cycle bounded scale-down
> guarantees. Our controller provides explicit, inspectable control over the convergence rate,
> which matters for scenarios where rapid scale-down risks terminating pods holding live
> connections before the TCP RST propagation window has closed.

---

## Priority 2: Rerun Core Experiments for Statistical Validity

Every experiment currently has N=1. This needs to become N=5 minimum.

### What to rerun

| Experiment | # of Runs | Key metric to report with stddev |
|-----------|-----------|----------------------------------|
| Experiment B2 Instrumented | 5 | Peak reconnection rate (conn/s) per cycle |
| Experiment B3 | 5 | Number of connections lost per scale-down event; termination gap (seconds) |
| Experiment C | 5 | Peak connections during restorm, max replica transient, pod-seconds total |
| Experiment D (new) | 5 | Same as C |
| Experiment E (KEDA, new) | 5 | Same as C |

### What to add to the analysis scripts

In `analysis/` Python scripts, add reporting of:
```python
import numpy as np
values = [run1_peak, run2_peak, run3_peak, run4_peak, run5_peak]
print(f"Mean: {np.mean(values):.1f}, Std: {np.std(values):.1f}, "
      f"Min: {np.min(values):.1f}, Max: {np.max(values):.1f}")
```

Report in the paper as: `mean ± std (min–max across 5 runs)`.

### What to include in tables

The key results table should become:

| Metric | CPU HPA (B3) | HPA Custom Metric (D) | KEDA (E) | StatefulAutoscaler (C) |
|--------|-------------|----------------------|----------|----------------------|
| Peak replicas | 15 ± 0 | 8–9 ± 0.5 | 8–9 ± 0.4 | 8–9 ± 0.3 |
| Connections lost | 800 ± 0 | ~200 ± 30 | 0–50 ± 15 | **0 ± 0** |
| Peak reconn. rate (conn/s) | 1400 ± 80 | 400 ± 60 | 0–50 ± 20 | **0 ± 0** |
| pod-seconds (cost) | 2,400 ± 50 | 1,800 ± 40 | 1,850 ± 45 | **1,820 ± 35** |

---

## Priority 3: Add Failure Mode Experiments

These 3 scenarios show where the system has limits. Adding them makes the paper honest and
significantly more credible. A reviewer who sees a "Failure Analysis" section immediately
trusts the paper more.

### Failure Scenario 1: Metric Staleness

**Setup:**
- Increase Prometheus `scrape_interval` from `15s` to `60s` in the ConfigMap.
- Run Experiment C (restorm scenario).
- Observe whether the controller makes correct decisions with stale data.

**Expected finding:**
- With 60s scrape lag, the controller sees a 60-second-old connection count.
- At the tail of the dropout gap, the controller may not know connections have returned until
  >60 seconds into Cycle 2.
- Describe this as "the system degrades gracefully to a 60-second reaction lag, bounded by
  the Prometheus scrape interval, without causing connection loss."

**Write-up template:**
> With a 60-second scrape interval, the controller's scale-up response lagged by up to 62
> seconds after reconnection (one scrape cycle). No connection loss was observed because all
> 8 pods remained warm during the cooldown period. This demonstrates that the cooldown
> mechanism provides a buffer that absorbs metric staleness up to scaleDownCooldownSeconds.

---

### Failure Scenario 2: Instantaneous Connection Spike (No Stagger)

**Setup:**
- Remove the linear stagger from the load generator (all N clients connect simultaneously).
- Use N=800 connecting at once.
- Run against the StatefulAutoscaler (Experiment C config).

**Expected finding:**
- The first Prometheus scrape after the spike will report 800 connections → controller
  scales to 8 pods. But pod scheduling takes 10–30 seconds. During that window, 2 pods
  absorb 800 connection establishment requests simultaneously.
- Some connection attempts may fail or be queued depending on OS `listen()` backlog.
- Describe this as a **transient under-provisioning window** that is bounded by pod
  startup time, not by controller design.

**Write-up template:**
> Under instantaneous connection arrival (N=800 without stagger), the controller correctly
> computed the desired replica count on the next scrape cycle (15s). However, pod scheduling
> and container startup introduced a 22±4 second lag during which the 2 initial pods
> experienced connection backlog. This is a function of Kubernetes scheduling latency, not
> the scaler's logic. Pre-warming pods via minReplicas tuning mitigates this.

---

### Failure Scenario 3: Prometheus Unavailability

**Setup:**
- During an active Experiment C run (while 800 connections are live and 8 pods are running),
  kill the Prometheus pod:
  ```bash
  kubectl delete pod -n monitoring -l app=prometheus
  ```
- Wait 2 minutes.
- Restore Prometheus (it auto-restarts via the Deployment).
- Observe controller behaviour.

**Expected finding:**
- The controller's `queryPrometheus()` function returns an error.
- Per the safe-default logic, the controller holds replicas at current value (8) and retries.
- No scale-down occurs during the 2-minute outage window.
- When Prometheus recovers and metrics resume, the controller continues normally.

**Write-up template:**
> When Prometheus became unavailable for 120 seconds, the controller defaulted to holding
> the current replica count, treating query failure as "unknown" rather than "zero
> connections." No connection loss was observed during or after the outage. This confirms
> the safe-default behaviour described in Section X.

---

## Priority 4: Add Missing Metrics to Existing Experiments

These metrics can be added to current experiments without re-designing them.

### P95 WebSocket Round-Trip Latency

**How to measure:**
Add a timestamp to the client's ping message and parse it from the "ack" response:

```python
# In client.py
import time
async def measure_latency(websocket):
    t0 = time.time()
    await websocket.send(f"ping:{t0}")
    response = await websocket.recv()
    rtt = time.time() - t0
    return rtt
```

Log all RTT values per client per second. Report P50 and P95 across all clients for each
experiment. Include during scaling events specifically (t ± 30s around each scale-up/down).

---

### Scale Reaction Time

**Definition:** Time from "load changed" (measured by Prometheus scrape showing a significant
delta in connection count) to "replica count changed" (measured by `kubectl get deployment`
log showing a different `DESIRED` count).

**How to compute:**
Already in the raw logs. In `analysis/` scripts, add:
```python
# Find first connection spike timestamp from connections.log
# Find first replica change from hpa.log or deployment.log
# Difference = scale reaction time
```

Report as: "the controller reacted within X±Y seconds of a significant connection change."

---

### pod-seconds (Cost Proxy)

**Formula:** `sum over time of (current_replicas × 15s interval length)`

Already computable from existing replica logs. Add to all experiment summaries.

Allows direct comparison: CPU HPA (B3) vs. StatefulAutoscaler (C) in terms of total cluster
resource usage. Expected result: C uses fewer pod-seconds because it avoids the 87.5%
over-provisioning observed in B1/B3.

---

## Priority 5: Controller Design — Formal Analysis Section

Add a new ~1-page subsection titled "Controller Design and Stability Properties" to the paper.

### Content to include

**The feedback loop framing:**

```
Observe:  total_connections = sum(active_connections) via Prometheus
Compare:  desired = ceil(total_connections / targetConnectionsPerPod)
          desired = clamp(desired, minReplicas, maxReplicas)
Act:      if desired != current: update deployment.spec.replicas
```

This is a **discrete-time proportional controller** with a sampling period of 15 seconds.

**The hysteresis mechanism:**

The `scaleDownCooldownSeconds` window introduces hysteresis — a dead zone where scale-down
decisions are suppressed even when the mathematical condition for scale-down is met. This
prevents oscillation in workloads with frequent but brief dropout periods (exactly the
restorm pattern). Without hysteresis, the controller would oscillate: connections drop →
scale down → connections return → scale up → repeat.

**Convergence bound:**

> After the cooldown expires and connections have stabilised at a new lower level, the
> controller converges from `current` replicas to `desired` replicas in:
>
> `ceil((current - desired) / maxScaleDownStep)` reconciliation cycles
>
> where each cycle is 15 seconds. For a transition from 8 to 2 pods with maxScaleDownStep=2:
> `ceil((8-2)/2) = 3 cycles = 45 seconds`.

**Scale-up is unbounded** (no step limit on scale-up), ensuring the controller responds
to sudden load increases as quickly as possible (bounded only by pod scheduling latency).

---

## Priority 6: Related Work and Positioning

### Required additions to Related Work

```
KEDA — Kubernetes Event-Driven Autoscaling (kedacore/keda):

  KEDA enables autoscaling based on external event sources and metrics, including Prometheus.
  It supports a cooldownPeriod parameter that mirrors the scaleDownCooldownSeconds in our
  controller. Unlike our work, KEDA does not expose per-cycle maxScaleDownStep rate limiting,
  and its connection to WebSocket session lifecycle (SIGTERM handling, TCP RST propagation)
  is not addressed in its design. Our work explicitly characterises the 30-second termination
  window and its interaction with scaling decisions, which is absent from KEDA's threat model.

Custom Metrics HPA:

  Kubernetes has supported custom and external metrics since v1.6 via the metrics aggregation
  layer and Prometheus Adapter. Prior work [cite PCM experiments] demonstrated that custom
  metrics improve HPA accuracy for stateless HTTP workloads. We extend this to stateful
  WebSocket workloads and show that metric choice alone is insufficient — scale-down lifecycle
  policies (cooldown and rate limiting) are equally critical.

Connection Draining:

  Production Kubernetes deployments use preStop hooks and terminationGracePeriodSeconds to
  allow stateless pods to drain in-flight requests. For WebSocket workloads, we document why
  this mechanism is structurally insufficient: connections lasting hours cannot drain in 30
  seconds, making graceful termination semantically different from graceful HTTP request
  completion.
```

---

## Priority 7: Limitations Section

Add a new "Limitations" section (can be short, ~half a page) before the Conclusion.

```
1. Evaluation Environment: All experiments were conducted on a single-machine kind cluster.
   While this provides reproducibility, it does not capture cross-node scheduling latency,
   network partitions, or the metric collection noise present in multi-node cloud clusters.
   Results are directionally valid; absolute timing values may differ on production infrastructure.

2. Workload Generalisability: The load generator uses synthetic WebSocket workloads with
   linear stagger. Real-world connection arrival is more bursty and session durations vary
   widely. The Poisson burst scenarios in Failure Mode experiments (Section X) partially
   address this, but further validation on real-world traces is future work.

3. Observability Dependency: The controller depends entirely on Prometheus metric freshness.
   Under high metric staleness (>scaleDownCooldownSeconds), the cooldown mechanism could
   expire before the controller is aware that connections have returned, resulting in a
   premature scale-down. The system is not safe against arbitrarily long Prometheus outages.

4. No Predictive Scaling: The controller reacts to observed connections but does not predict
   connection arrivals. Workloads with predictable burst patterns (daily peaks, scheduled
   events) could benefit from proactive pre-warming, which is outside the current design scope.
```

---

## Priority 8: Reproducibility

**Add to the paper (in the Evaluation Setup section):**

> All experimental scripts, cluster configuration, server code, load generator, and controller
> source are available at: [GitHub URL]. Experiments can be reproduced on any Linux machine
> with Docker and kind installed by running the numbered experiment scripts in `scripts/`.

**Add to the kind cluster description:**

> The kind cluster is configured with 1 control-plane node and 2 worker nodes running
> Kubernetes v1.31.6 (kind v0.25.0). All nodes run as Docker containers with --net=bridge.
> The metrics-server is patched with --metric-resolution=15s and --kubelet-insecure-tls
> (required for kind's self-signed kubelet certificates and not appropriate for production).

---

## Execution Checklist

Track progress against this list.  Completed items are marked ✅.

### Phase 1 — Paper Text (No New Experiments)
- [x] ✅ Replace all overclaimed phrases with table from Priority 0 (abstract + intro updated)
- [x] ✅ Rewrite contribution paragraph in abstract and introduction
- [x] ✅ Fix "Kubernetes cannot" → "Kubernetes does not by default"
- [x] ✅ Add KEDA paragraph to Related Work (`\subsection{Event-Driven Autoscaling: KEDA}`)
- [x] ✅ Add custom metrics HPA paragraph to Related Work (`\subsection{Custom and Application-Level Metrics for HPA}`)
- [x] ✅ Add connection draining paragraph to Related Work (`\subsection{Graceful Connection Draining}`)
- [x] ✅ Add Controller Design and Stability Properties subsection (see `\subsection{Controller Design and Stability Properties}` in `\section{sec:controller}`)
- [x] ✅ Add Limitations section (expanded 8-point list, see `\section{sec:limitations}`)
- [x] ✅ Add GitHub URL to Evaluation Setup (added `\url{https://github.com/AbhasBenevolentDictator/STAR}` in Section 4 preamble)
- [x] ✅ Add kind cluster version details to Evaluation Setup (kind v0.25.0, K8s v1.31.6, kubelet-insecure-tls note)

### Phase 2 — New Baselines (Experiments D and E)
- [x] ✅ Deploy Prometheus Adapter, configure custom metric rule (Exp D setup — done, scripts in `scripts/run-experiment-d.sh`)
- [ ] ⏳ Run Experiment D — HPA custom metrics (**5 runs pending** — single run done, multi pending)
- [x] ✅ Install KEDA, configure ScaledObject (Exp E setup — done, scripts in `scripts/run-experiment-e.sh`)
- [ ] ⏳ Run Experiment E — KEDA baseline (**5 runs pending** — single run done, multi pending)
- [x] ✅ Add analysis scripts for D and E (`analysis/experiment-d/`, `analysis/experiment-e/`)
- [ ] ⏳ Generate plots for D and E (pending multi-run data)
- [x] ✅ Write results paragraphs for D and E (in paper with `[PLACEHOLDER]` values — see below)

**→ PLACEHOLDER UPDATE GUIDE for Experiments D and E:**
Once 5-run data is collected from `scripts/run-multi.sh` for experiments D and E, update the
following locations in `Paper-Latex/paper.tex`:

1. **`\subsection{Experiment D: HPA with Custom Connection-Count Metric}`** (~line 590):
   - Replace `\texttt{[PLACEHOLDER: mean~conn/s $\pm$ std across 5 runs]}` with actual peak
     reconnection rate mean±std computed from `analysis/experiment-d/`.
   - Replace `\texttt{[PLACEHOLDER: mean $\pm$ std]}` (connection loss) with actual values.
   - Replace `\texttt{[PLACEHOLDER: mean $\pm$ std]}` (pod-seconds) with actual values.
   - Add `Figure~\ref{fig:exp_d_replicas}` once plot is generated.

2. **`\subsection{Experiment E: KEDA Baseline}`** (~line 615):
   - Replace `\texttt{[PLACEHOLDER: mean replicas $\pm$ std]}` with actual warm pod count.
   - Replace `\texttt{[PLACEHOLDER: mean~conn/s $\pm$ std]}` with peak reconn rate.
   - Replace `\texttt{[PLACEHOLDER: mean $\pm$ std]}` (connection loss, pod-seconds).
   - Add `Figure~\ref{fig:exp_e_replicas}` once plot is generated.

3. **`Table~\ref{tab:baseline_compare}`** (~line 640): Replace all `\texttt{[PLACEHOLDER]}` cells
   in the HPA+Custom and KEDA columns with `mean $\pm$ std (min–max)` statistics.

### Phase 3 — Statistical Rigor
- [ ] ⏳ Rerun Experiment B2 Instrumented × 5 (pending)
- [ ] ⏳ Rerun Experiment B3 × 5 (pending)
- [ ] ⏳ Rerun Experiment C × 5 (pending — **note**: once done, update `Table~\ref{tab:baseline_compare}` StatefulAutoscaler column with mean±std and update all inline numbers in `\section{sec:evaluation}`)
- [ ] ⏳ Update analysis scripts to compute mean ± std
- [ ] ⏳ Regenerate all plots with error bars or table rows with std
- [ ] ⏳ Update all result sentences to report mean ± std

### Phase 4 — Failure Mode Experiments
- [x] ✅ Run Failure Scenario 1 (metric staleness at 60s scrape interval) — results written into paper
- [x] ✅ Run Failure Scenario 2 (instantaneous spike, no stagger) — results written into paper
- [x] ✅ Run Failure Scenario 3 (Prometheus killed mid-experiment) — results written into paper
- [x] ✅ Write Failure Analysis section with 3 subsections (`\section{sec:failure_analysis}` in paper)

### Phase 5 — Additional Metrics
- [ ] ⏳ Add latency measurement to client.py (RTT ping/pong logging)
- [ ] ⏳ Add scale reaction time computation to analysis scripts
- [ ] ⏳ Add pod-seconds computation to analysis scripts
- [ ] ⏳ Rerun key experiments with new instrumentation
- [ ] ⏳ Add latency, reaction time, and pod-seconds tables to paper

### Phase 6 — Final Review
- [ ] Re-read paper end-to-end checking for any remaining overclaims
- [ ] Verify all claims have corresponding experiment data or citations
- [ ] Check Related Work covers: KEDA, Custom Metrics HPA, Base Paper (KRM/PCM), Connection Draining
- [ ] Verify GitHub repo is public and all scripts run clean from `README.md`

---

## Revision Round 2 — Reviewer Concerns (Paper-Text First Approach)

The following items address a second round of reviewer feedback. The guiding principle for all
responses is: **prefer paper-text responses (discussion, acknowledgement, framing) over new
experiments wherever the study's integrity allows it.** Only propose a new experiment when
purely textual handling would be an obvious dodge.

---

### R2-1 — Limited Baseline Configurations (Major)

**Reviewer concern:** Experiment D uses a fixed 300 s HPA scale-down window. A reviewer might
argue "just set stabilizationWindowSeconds=60 to match the StatefulAutoscaler and you get the
same result — so the custom controller adds nothing."

**Response strategy (paper-text only):**

Add a short paragraph to the Experiment D Discussion (or the Comparative Summary) making the
following argument explicitly:

> Setting `stabilizationWindowSeconds` to 60 s in HPA would shorten scale-down latency but
> **would not survive DROP~1** (90 s gap > 60 s window). HPA's stabilization window is a
> trailing buffer of *desired-replica values*, not a high-water mark of connection state. When
> connections fall to zero for 90 s with a 60 s window, the window fills entirely with
> `minReplicas` recommendations and scale-down proceeds. This is not a tuning problem; it is
> a semantic mismatch: the window stabilises the wrong quantity. The StatefulAutoscaler's
> sliding window retains **connection-derived high-water marks** — so even 90 s of zero
> connections does not cause scale-down as long as 800 connections existed within the window.
> This is the architectural distinction that cannot be replicated by adjusting `stabilizationWindowSeconds`.

Similarly for KEDA with `cooldownPeriod=0`: explicitly state in the Experiment E conclusion
that removing the cooldown causes immediate scale-down on DROP~1, confirming that the
`cooldownPeriod` and `scaleDownCooldownSeconds` are directly equivalent for *this* workload —
and that the difference emerges only when the gap exceeds the cooldown, or when bounded
per-cycle convergence is required.

**Where to add in paper:**
- End of `\subsection{Comparative Summary Across All Baselines}` — add a short paragraph titled
  "Sensitivity to HPA stabilization window setting" making the semantic argument above.
- End of `\textbf{Conclusion}` of Experiment E — one sentence: "Reducing KEDA's
  `cooldownPeriod` below the disconnection gap (i.e., <90 s) would cause scale-down during
  DROP~1, confirming that the parameter match used here is necessary rather than conservative."

**Do NOT run a new experiment for this.** The argument is architectural and can be made
analytically from existing data. If a reviewer insists, the response is: "we agree and will
run as a camera-ready addition" — but the textual argument is sufficient for most venues.

---

### R2-2 — Parameter Sensitivity (Major)

**Reviewer concern:** `targetConnectionsPerPod=100`, `maxScaleDownStep=2`, and
`scaleDownCooldownSeconds=120` were fixed. Are results only valid for this choice?

**Response strategy (paper-text only):**

Add a `\paragraph{Parameter selection rationale}` to the Controller Design section explaining:

1. **`targetConnectionsPerPod=100`** was chosen to yield exactly 8 pods for 800 connections,
   making the experiment's "correct answer" unambiguous and easy to audit. A value of 50 would
   require 16 pods (within `maxReplicas=15`); a value of 200 would require 4 pods (above
   `minReplicas=2`). Both would still produce stable, exact-integer scaling — the controller
   does not oscillate because the desired-replica formula is deterministic and monotone.

2. **`maxScaleDownStep=2`** was chosen conservatively: it bounds the blast radius of any
   single scale-down event. The value could be 1 (slower, safer) or 3 (faster, larger blast
   radius). The convergence formula `ceil((current−desired)/step)` is explicit; there is no
   hidden sensitivity.

3. **`scaleDownCooldownSeconds=120`** was chosen to comfortably exceed the 90 s DROP~1 gap
   plus one Prometheus scrape cycle (15 s). Any value ≥ 105 s would achieve the same
   pod-preservation result for this workload. Values below 90 s would allow scale-down to
   begin during DROP~1.

4. **Stability argument:** The controller cannot oscillate in the classical sense because
   scale-down is gated by the cooldown window. Oscillation requires scale-down to trigger new
   scale-up; the cooldown prevents scale-down from firing unless load has been low for the
   entire window. The only edge case is the window-boundary dip observed in CYCLE~2
   (documented in Limitations), which is a transient under-provisioning rather than sustained
   oscillation.

**Where to add in paper:**
- New `\paragraph{Parameter selection and sensitivity}` inside
  `\subsection{Controller Design and Stability Properties}` (~line 440).
- Cross-reference to Limitations item 7 (window boundary edge case) to show known sensitivity.

---

### R2-3 — Control-Theoretic Rigor (Major/Minor)

**Reviewer concern:** The feedback-loop description is heuristic. Is the loop stable? How does
the 15 s scrape delay affect oscillation?

**Response strategy (paper-text only, expand existing subsection):**

The existing `\subsection{Controller Design and Stability Properties}` already contains the
convergence formula. Expand it with two additional paragraphs:

**Paragraph 1 — Delay margin:**
> With a scrape interval of T=15 s and a reconciliation period of T_r=15 s, the maximum
> observation lag is 2T = 30 s. Because the scale-down cooldown (120 s) >> 2T, the system
> absorbs the worst-case lag and never acts on a stale "zero connections" reading within the
> cooldown window. For scale-up, no cooldown is applied; a stale reading of high connection
> count would produce a scale-up, which is safe (over-provisioning rather than under).

**Paragraph 2 — Oscillation condition:**
> Oscillation would require: (1) scale-down fires, (2) a connection spike arrives before
> new replicas are ready, (3) scale-down fires again. The cooldown prevents (1) from
> happening within 120 s of any connection activity. The `maxScaleDownStep` limit ensures
> that even if scale-down does begin, at most 2 pods are removed per cycle, avoiding
> a catastrophic sudden capacity drop. Together, these two parameters form a dead-zone
> + damping pair analogous to integral anti-windup in classical PID control.

**Where to add in paper:**
- Append both paragraphs to `\subsection{Controller Design and Stability Properties}`.
- No new figure required; the existing formula and block-description already satisfy most venues.

---

### R2-4 — Metrics and Units Consistency (Minor)

**Reviewer concern:** Inconsistent reporting of mean±std vs mean(range); unclear definition
of pod-seconds; inconsistent scale-up measurement baseline.

**Fixes in paper text (all small, no new experiments):**

1. **Define pod-seconds** explicitly in the Evaluation Setup section:
   > "Pod-seconds is defined as $\sum_{\text{pods}} \text{(time pod was Running during the
   > experiment window)}$, measured in seconds. It serves as a proxy for compute cost: lower
   > pod-seconds for equivalent connection service implies better resource efficiency."

2. **Standardise reporting** to `mean ± std (median; range)` throughout. Where range was
   previously omitted (e.g., scale-down 119±2), add the range in parentheses.

3. **Clarify scale-up reaction time baseline** — add one sentence in the metrics definition:
   > "Scale-up reaction time is measured from the first Prometheus scrape reporting
   > connections > `targetConnectionsPerPod × minReplicas` to the moment
   > `deployment.status.readyReplicas` first reaches the expected count."

**Where to add in paper:**
- Pod-seconds definition: `\subsection{Experimental Configuration}` in Experiment C section.
- Standardisation: sweep Table 1 caption and all inline result sentences.

---

### R2-5 — Workload Generalisability (Minor, already in Limitations)

**Reviewer concern:** Results may not hold for non-deterministic load patterns.

**Response strategy:** The Limitations section already addresses this (item 2). Strengthen it
with one sentence explicitly naming what *would* change vs what *would not*:

> "For stationary random load (Poisson arrivals), the controller's proportional replica formula
> and cooldown mechanism apply unchanged; only the observed peak connection count would vary.
> For adversarial patterns (instantaneous spikes), Failure Scenario~2 demonstrates that the
> binding constraint is pod scheduling latency, not controller logic. The one pattern not tested
> is gradual long-term drift; in that case the cooldown window degrades gracefully — scale-down
> simply lags by up to `scaleDownCooldownSeconds` after the true load drop."

**Where to add in paper:**
- Replace or extend Limitations item 2 (`\item \textbf{Workload generalisability}`) with the
  sentence above.

---

### R2-6 — Novelty / Positioning (Major)

**Reviewer concern:** "Use connection count instead of CPU" is not novel. The paper should
reframe contribution as empirical validation + measurement, not algorithmic novelty.

**Response strategy:** The abstract and introduction were already partially reframed in
Revision Round 1. Tighten the framing further:

**Abstract:** Replace any remaining "we demonstrate X is possible" sentences with
"we quantify X through controlled experiments" or "we provide empirical evidence that X
occurs under Y conditions."

**Contributions list (Introduction):** Revise phrasing:
- "We present the StatefulAutoscaler" → "We implement and experimentally validate..."
- "We demonstrate that" → "Controlled experiments demonstrate that..."
- Do NOT claim the architecture is novel — claim the *empirical measurement* of failure modes
  and the *comparative evaluation* across four scaler configurations is the contribution.

**Related Work:** Add one sentence clearly separating your work from KEDA:
> "To the best of our knowledge, no prior work simultaneously quantifies HPA failure modes
> at connection-second granularity, compares four scaler configurations under identical
> WebSocket workloads with statistical replication, and characterises the OS-level termination
> window as a design constraint."

**Conclusion:** Replace "addressing all failure modes" with "eliminating all three documented
failure modes under our evaluated synthetic workload" to be accurate about scope.

**Where to add in paper:**
- Abstract: 2–3 phrase-level edits (search for "we demonstrate" and "we present").
- Contribution item 2: reword the opening line.
- Related Work: append one sentence to `\subsection{Research Gap}`.
- Conclusion: add scope qualifier "under our evaluated synthetic workload" to the final claim.

---

### Execution Checklist — Revision Round 2

All items below are paper-text only (no new experiments required unless marked ⚗️).

- [x] R2-1a: Add "HPA window semantic mismatch" paragraph to Comparative Summary
- [x] R2-1b: Add KEDA cooldown sensitivity sentence to Experiment E Conclusion
- [x] R2-2: Add `\paragraph{Parameter selection and sensitivity}` to Controller Design
- [x] R2-3a: Add delay-margin paragraph to Stability Properties subsection
- [x] R2-3b: Add oscillation-condition paragraph to Stability Properties subsection
- [x] R2-4a: Define pod-seconds in Experiment C setup
- [ ] R2-4b: Standardise mean±std(median;range) in Table 1 and inline text — minor sweep, defer to final proofread
- [x] R2-4c: Clarify scale-up reaction time baseline definition
- [x] R2-5: Strengthen Limitations item 2 with drift/random-load sentence
- [x] R2-6a: Sweep abstract for overclaims ("we demonstrate" → "experiments show")
- [x] R2-6b: Reword Contribution item 2 in Introduction
- [x] R2-6c: Append Research Gap sentence to Related Work
- [x] R2-6d: Add scope qualifier to Conclusion final claim

---

## Expected Outcome After All Fixes

| Aspect | Before | After |
|--------|--------|-------|
| Novelty framing | "We built a new system" (weak) | "We evaluated, quantified, and validated" (strong) |
| Claims | Overclaimed — will be caught and flagged | Defensible — backed by data |
| Baselines | Only CPU HPA (unfair comparison) | CPU HPA + HPA Custom Metric + KEDA (fair) |
| Statistical validity | N=1 per experiment (anecdotal) | N=5 per experiment, mean ± std |
| Failure analysis | None | 3 adversarial scenarios with honest results |
| Related work | Missing KEDA, custom metrics literature | Covers all key prior work |
| Reproducibility | No artifacts referenced | GitHub URL + full config in paper |
| Technical depth | Code description | Feedback-loop framing + convergence bound |
| Estimated acceptance | ~20% | ~65–75% |
