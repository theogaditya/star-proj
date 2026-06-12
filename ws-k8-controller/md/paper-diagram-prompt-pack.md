# Paper Diagram Prompt Pack

This file is a practical guide for adding more non-graph visuals to the paper in [Paper-Latex/paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex).

The paper already has many plots. What it is missing is a small number of explanatory diagrams that make the control logic, system architecture, and semantics easier to understand at first glance.

This guide answers:

- where new diagrams should go in the paper,
- what kind of diagram belongs there,
- what the diagram must show,
- a ready-to-use prompt for GPT or Gemini to generate a 4K technical image,
- file naming suggestions,
- LaTeX insertion suggestions.

---

## Design Rules For All New Paper Diagrams

Use these constraints in every prompt:

- resolution: `3840x2160` minimum, 16:9 or slightly wider,
- style: clean technical schematic, publication-safe, vector-like, not photorealistic,
- background: white or very light gray,
- line style: crisp dark lines, restrained color palette,
- text: large enough to stay readable after shrinking into a two-column paper,
- typography: simple sans-serif, uniform, no decorative fonts,
- layout: spacious, symmetric, uncluttered,
- labels: precise and short,
- no marketing look,
- no fake dashboards,
- no 3D,
- no glossy icons,
- no gradients unless extremely subtle,
- export target: ideally `SVG` or `PDF`; if the model only gives bitmap, ask for high-resolution `PNG`.

Good palette:

- dark gray for structure,
- blue for metrics/data flow,
- orange for scaling decisions,
- green for healthy/held state,
- red only for destructive termination or failure.

Keep every generated figure technically honest:

- do not invent components that are not in the repo,
- do not claim the current controller already performs logic that is only planned,
- if drain is shown, label it as application endpoint support or planned enhancement unless the caption explicitly narrows scope.

---

## Existing Visual Coverage

The paper already has:

- HPA architecture figure,
- KRM and PCM performance plots,
- experiment plots for A, B1, B2, B3, C, D, E,
- failure scenario comparative plot,
- one C vs D comparison figure.

The main missing visual categories are:

1. metric-path architecture clarification,
2. evidence-chain overview,
3. pod termination sequence semantics,
4. controller architecture,
5. controller internal decision flow,
6. semantic comparison of HPA vs KEDA vs StatefulAutoscaler,
7. operational boundary/failure envelope.

---

## Best Insertion Points In `paper.tex`

### 1. KRM vs PCM Pipeline Comparison

Best place:

- after the PCM bullet list, before or after the current PCM scrape figure.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:203)

Why:

- the text explains KRM and PCM, but there is no explicit side-by-side pipeline diagram.
- this is the first place where a reader benefits from a "what path does the metric take?" visual.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/krm-vs-pcm-pipeline.png`

Suggested figure label:

- `fig:krm_pcm_pipeline`

### 2. Problem Evidence Chain Diagram

Best place:

- near the start of Section 4, before Experiment A.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:236)

Why:

- the paper has a strong experiment progression, but that progression is currently mostly verbal.
- a single chain diagram would make the logic of A -> B1 -> B2 -> B3 -> C instantly clear.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/evidence-chain-overview.png`

Suggested figure label:

- `fig:evidence_chain_overview`

### 3. Pod Termination / 30-Second Limbo Sequence Diagram

Best place:

- inside Experiment B3, right after the paragraph titled "Pod termination mechanics and the 30-second limbo".
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:329)

Why:

- this is one of the most important concepts in the paper.
- the text is good, but a timing/sequence diagram would make it much easier to understand.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/pod-termination-limbo-sequence.png`

Suggested figure label:

- `fig:termination_limbo_sequence`

### 4. StatefulAutoscaler System Architecture

Best place:

- immediately after the opening paragraph of Section 5, before Design Principles.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:377)

Why:

- the controller section jumps straight into principles and formulas.
- a system architecture diagram would help readers anchor the controller in the cluster.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/statefulautoscaler-architecture.png`

Suggested figure label:

- `fig:statefulautoscaler_arch`

### 5. Reconciliation Flow / UML Activity Diagram

Best place:

- right before or right after Algorithm 1 in Reconciliation Loop Design.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:424)

Why:

- the algorithm is rigorous, but many readers understand control flow faster through a UML activity diagram.
- this is especially useful for reviewers who skim.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/statefulautoscaler-reconcile-activity.png`

Suggested figure label:

- `fig:statefulautoscaler_reconcile_activity`

### 6. Stabilization Semantics Comparison: HPA vs KEDA vs StatefulAutoscaler

Best place:

- in the Baseline Comparisons section, ideally before the sensitivity paragraph.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:642)

Why:

- this is where the paper makes its most nuanced claim.
- the reader needs a conceptual picture of what each scaler "remembers" and how each one decides scale-down.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/stabilization-semantics-comparison.png`

Suggested figure label:

- `fig:stabilization_semantics_compare`

### 7. Failure Envelope / Operational Boundary Diagram

Best place:

- at the start of the failure analysis section, before the first failure scenario.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:748)

Why:

- the paper has good failure analysis text, but no non-graph visual showing what the controller tolerates, degrades under, and fails under.

Suggested filename:

- `Paper-Latex/processed-results-websockets/diagrams/failure-envelope.png`

Suggested figure label:

- `fig:failure_envelope`

---

## Priority Order

If you only add four diagrams, add these:

1. `statefulautoscaler-architecture`
2. `termination-limbo-sequence`
3. `stabilization-semantics-comparison`
4. `evidence-chain-overview`

If you add six or seven, include the rest.

---

## Prompt 1: KRM vs PCM Pipeline Comparison

### Purpose

Show the difference between the two autoscaling metric pipelines in the background section.

### What the diagram must communicate

- KRM path:
  - workload CPU appears in pod,
  - kubelet/cAdvisor,
  - Metrics Server,
  - Kubernetes Metrics API,
  - HPA.
- PCM path:
  - application exposes `/metrics`,
  - Prometheus scrapes,
  - Prometheus Adapter translates query,
  - Kubernetes Custom Metrics API,
  - HPA.
- KRM is simpler but less flexible.
- PCM is more flexible but adds scrape and adapter stages.
- both still end in HPA.

### Ready prompt

```text
Generate a 4K technical research diagram, publication-safe, white background, vector-like style, for a Kubernetes autoscaling paper.

Title inside the figure:
"Kubernetes Autoscaling Metric Pipelines: KRM vs PCM"

Create a clean side-by-side architecture comparison with two horizontal lanes.

Top lane label: "KRM (Kubernetes Resource Metrics)"
Show the exact flow:
Stateless HTTP workload pod -> CPU usage in container -> kubelet / cAdvisor -> Metrics Server -> metrics.k8s.io API -> HPA controller -> Deployment replica count

Bottom lane label: "PCM (Prometheus Custom Metrics)"
Show the exact flow:
Application pod exposing /metrics -> Prometheus scrape -> Prometheus time-series store -> Prometheus Adapter -> custom.metrics.k8s.io API -> HPA controller -> Deployment replica count

Add a small callout on the top lane:
"Simple native pipeline; CPU/memory oriented"

Add a small callout on the bottom lane:
"Flexible application-defined metrics; extra scrape + adapter latency"

Add a subtle annotation below both:
"Both pipelines still rely on HPA; only the metric acquisition path differs."

Use dark gray boxes, blue arrows for metric flow, orange highlight around HPA, and minimal accent colors.
No 3D, no dashboards, no gradients, no decorative icons.
Make text large and legible after shrinking for a two-column academic paper.
Output as a technical figure that could be inserted directly into a systems paper.
```

### Suggested caption direction

Compare the native KRM and Prometheus-based PCM metric paths, emphasizing that PCM adds flexibility and latency-bearing stages while still feeding the same HPA decision loop.

---

## Prompt 2: Problem Evidence Chain Overview

### Purpose

Show how the experiments build logically from baseline to failure proof to solution.

### What the diagram must communicate

- A is ideal HPA baseline.
- B1 shows over-provisioning.
- B2 shows reconnection storms.
- B3 shows permanent idle-session destruction.
- C shows solution with StatefulAutoscaler.
- D and E are stronger baselines or comparison branches, not part of the original failure chain.

### Ready prompt

```text
Generate a 4K technical research diagram, white background, clean vector-like style, for a systems paper.

Title inside the figure:
"Progressive Evidence Chain of HPA Failure and Controller Validation"

Build a left-to-right progression diagram with five main blocks:

1. Experiment A
Label: "A: Baseline"
Subtext: "CPU proportional to connection activity"
Outcome: "HPA works under ideal signal correlation"

2. Experiment B1
Label: "B1: Cyclic Churn"
Subtext: "HIGH/LOW activity cycles, idle connections remain open"
Outcome: "HPA over-provisions and recovers slowly"

3. Experiment B2
Label: "B2: Instrumented Scale-Down"
Subtext: "Pods removed while connections are active"
Outcome: "Reconnection storms measured"

4. Experiment B3
Label: "B3: Idle Non-Reconnecting Clients"
Subtext: "Connections remain alive, CPU falls to zero"
Outcome: "Permanent staircase connection loss"

5. Experiment C
Label: "C: StatefulAutoscaler"
Subtext: "Connection-aware scaling with cooldown"
Outcome: "Pods held warm, no connection loss observed"

Add a secondary branch below the main chain from Experiment C:
- Experiment D: "HPA + custom connection metric"
- Experiment E: "KEDA with cooldownPeriod"
Label this branch as: "Baseline comparisons"

Use red accents for failure outcomes in B1-B3, green accents for C, and neutral blue for A.
Include a thin annotation under the chain:
"The progression moves from best-case HPA behavior to quantified failure modes, then to a controller-level mitigation and stronger baselines."

Make the composition compact, highly legible, and suitable for an academic paper.
No plot axes, no fake dashboards, no photorealism.
```

### Suggested caption direction

Summarize the paper's experimental logic as a progressive evidence chain from idealized HPA success to quantified failure and controller-based mitigation.

---

## Prompt 3: Pod Termination And 30-Second Limbo Sequence

### Purpose

Make the B3 failure mechanism visually obvious.

### What the diagram must communicate

- HPA decides to scale down.
- Pod removed from Service endpoints immediately.
- SIGTERM sent.
- existing TCP sessions still alive during grace period.
- after 30 seconds, SIGKILL.
- TCP RST propagation.
- client sees disconnect.
- if reconnect is false, session is gone forever.

### Ready prompt

```text
Generate a 4K technical sequence diagram for an academic systems paper, white background, vector-like style, crisp typography.

Title inside the figure:
"Kubernetes Pod Termination and the 30-Second Connection Limbo"

Use a sequence or timing diagram with these actors from left to right:
HPA
Kubernetes control plane
Service / EndpointSlice
Target WebSocket Pod
OS / TCP stack
Client

Show the following ordered events:
1. HPA reduces desired replicas.
2. Kubernetes selects a pod for termination.
3. Pod is removed from Service endpoints immediately, so no new connections arrive.
4. SIGTERM is sent to the pod process.
5. Existing TCP connections remain open during terminationGracePeriodSeconds = 30s.
6. After 30s, SIGKILL is sent.
7. OS forcibly resets remaining TCP sockets with TCP RST.
8. Client receives disconnect signal.
9. If reconnect=false, session is permanently lost.

Add a highlighted band over the 30-second grace interval labeled:
"Termination limbo: pod is doomed, existing connections still appear alive"

Add a red callout near the end:
"State destruction occurs at SIGKILL, not at the initial scale-down decision"

Use blue for control-plane actions, gray for normal states, orange for HPA decision, and red for destructive events.
Do not draw decorative elements. Keep it precise, technical, and publication-ready.
```

### Suggested caption direction

Explain that HPA's scale-down decision and connection destruction are temporally separated by the Kubernetes termination grace period, creating a limbo window rather than safety.

---

## Prompt 4: StatefulAutoscaler System Architecture

### Purpose

Give the reader one clean picture of the solution system before the controller internals.

### What the diagram must communicate

- WebSocket clients connect through Service to pods.
- each pod exposes `/metrics`.
- Prometheus scrapes `active_connections`.
- controller queries Prometheus for `sum(active_connections)`.
- controller patches Deployment replica count.
- cluster contains Deployment, Service, Prometheus, controller, CRD object.

### Ready prompt

```text
Generate a 4K technical architecture diagram for a Kubernetes research paper, white background, vector-like, minimal, publication-safe.

Title inside the figure:
"StatefulAutoscaler System Architecture"

Show a Kubernetes cluster boundary containing these components:
- WebSocket Deployment with multiple pod replicas
- Kubernetes Service in front of the pods
- Prometheus server
- StatefulAutoscaler controller manager
- StatefulAutoscaler CRD / custom resource

Outside the cluster boundary, show:
- Client population (many persistent WebSocket clients)

Show these flows clearly:
1. Clients -> Service -> WebSocket pods
2. Each WebSocket pod exposes /metrics with active_connections
3. Prometheus scrapes pod metrics
4. Controller queries Prometheus for sum(active_connections)
5. Controller reads StatefulAutoscaler custom resource parameters
6. Controller patches Deployment.spec.replicas

Show the most important CRD fields in a small side panel:
- targetRef
- minReplicas
- maxReplicas
- targetConnectionsPerPod
- maxScaleDownStep
- scaleDownCooldownSeconds

Add a small annotation:
"CPU is not part of the control path"

Use blue arrows for metric flow, orange arrows for scaling decisions, dark gray boxes for Kubernetes objects, and green for healthy connection-aware control path.
No photorealistic servers or cloud stock art. Keep it precise and clean.
```

### Suggested caption direction

Depict the StatefulAutoscaler as a Prometheus-driven Kubernetes controller that computes replica count from live connection density rather than CPU.

---

## Prompt 5: Reconciliation Flow / UML Activity Diagram

### Purpose

Turn Algorithm 1 into a quicker visual for readers who think in flowcharts.

### What the diagram must communicate

- read CRD and target Deployment,
- query Prometheus,
- if query fails, requeue safely,
- compute raw desired,
- clamp min/max,
- append to sliding window,
- evict old entries,
- compute stabilized max,
- apply step limits,
- patch Deployment if needed,
- requeue.

### Ready prompt

```text
Generate a 4K UML-style activity diagram for a systems research paper, white background, crisp vector-like style.

Title inside the figure:
"StatefulAutoscaler Reconciliation Flow"

Create a vertical activity diagram with these exact stages:

Start
-> Read StatefulAutoscaler resource
-> Read target Deployment
-> Query Prometheus: sum(active_connections)
Decision: query successful?
  No -> Requeue after 10s, no replica change, End
  Yes -> Continue
-> Compute rawDesired = ceil(connections / targetConnectionsPerPod)
-> Clamp to minReplicas / maxReplicas
-> Append desired count to sliding window history
-> Evict entries older than scaleDownCooldownSeconds
-> stabilized = max(window)
Decision: stabilized > current replicas?
  Yes -> Apply maxScaleUpStep bound
Decision: stabilized < current replicas?
  Yes -> Apply maxScaleDownStep bound
Else -> Keep current replicas
-> Patch Deployment.spec.replicas if target changed
-> Requeue after 5s
End

Add a callout beside the sliding window step:
"This is the key mechanism that preserves warm pods through transient connection drops"

Color code:
- blue for normal processing
- green for safe hold / no-op outcomes
- orange for scaling actions
- red only for query failure branch

Use proper UML activity symbols or a clean flowchart equivalent. No decorative styling.
```

### Suggested caption direction

Summarize the controller's reconcile loop and highlight the sliding-window maximum as the key differentiating mechanism.

---

## Prompt 6: Stabilization Semantics Comparison

### Purpose

This is the most valuable explanatory diagram in the baseline-comparison section.

### What the diagram must communicate

- HPA stabilizes metric-derived desired replicas.
- KEDA delays lowering HPA's floor through cooldownPeriod.
- StatefulAutoscaler remembers recent connection-derived demand and also limits step size.
- For a short gap, all can hold pods if configured appropriately.
- Their semantics diverge after cooldown or with different gap durations.

### Ready prompt

```text
Generate a 4K conceptual comparison diagram for an academic systems paper, white background, clean technical style.

Title inside the figure:
"Scale-Down Semantics: HPA vs KEDA vs StatefulAutoscaler"

Create three side-by-side vertical panels:

Panel 1: HPA
Top label: "HPA"
Show:
- metric value falls
- desired replicas computed from metric each cycle
- stabilization window buffers desiredReplicas values
- when the window fills with low values, scale-down proceeds
Add short note:
"Stabilizes metric-derived recommendations"

Panel 2: KEDA
Top label: "KEDA"
Show:
- external metric falls
- KEDA lowers HPA minReplicas only after cooldownPeriod
- HPA still performs actual termination
Add short note:
"Delays floor reduction, delegates termination to HPA"

Panel 3: StatefulAutoscaler
Top label: "StatefulAutoscaler"
Show:
- active_connections falls temporarily
- controller retains recent high-water desired count
- scale-down suppressed during cooldown window
- after cooldown, replica reduction proceeds with maxScaleDownStep
Add short note:
"Stabilizes connection-derived demand and bounds per-cycle removal"

Across the bottom, add a shared scenario strip:
"Short transient 90-second disconnection gap"
Show visually that all three may hold replicas if configured broadly enough, but only the StatefulAutoscaler explicitly models recent connection demand and bounded release.

Use orange for scale actions, blue for metrics, green for safe hold behavior, and red for unsafe release.
Avoid plots and axes; this should be a conceptual semantics diagram, not a graph.
```

### Suggested caption direction

Contrast the semantics of what each scaler remembers and how each one authorizes scale-down, clarifying that the difference is not just parameter value but the quantity being stabilized.

---

## Prompt 7: Failure Envelope / Operational Boundary Diagram

### Purpose

Show the reader the controller's safe zone and edge cases before the detailed failure scenarios.

### What the diagram must communicate

- safe baseline region,
- degradation under long scrape intervals,
- infrastructure limit under instantaneous spike,
- safe hold under temporary Prometheus outage,
- unsafe region if outage exceeds cooldown or stale interval exceeds cooldown.

### Ready prompt

```text
Generate a 4K technical conceptual diagram for a systems paper, white background, vector-like style, no charts with numeric axes.

Title inside the figure:
"Operational Envelope of the StatefulAutoscaler"

Create a clean conceptual map with four zones or panels:

Zone 1: Normal operation
Label:
"15s scrape, staggered ramp, Prometheus healthy"
Outcome:
"Exact connection-aware scaling; no connection loss observed"

Zone 2: Metric staleness
Label:
"Long scrape interval"
Outcome:
"Graceful degradation: delayed reaction, possible connection/replica overshoot"
Boundary note:
"Unsafe if scrape interval exceeds scaleDownCooldownSeconds"

Zone 3: Instantaneous spike
Label:
"No ramp stagger, 800 clients connect at once"
Outcome:
"Controller computes target quickly, but pod scheduling latency becomes bottleneck"

Zone 4: Prometheus outage
Label:
"Temporary metric source unavailable"
Outcome:
"Safe-default hold; no immediate scale-down"
Boundary note:
"Indefinite outage can eventually expire stabilization memory"

Add a central annotation:
"The controller is robust to bounded delay and bounded outage, but not to arbitrary stale or missing telemetry."

Use green for safe region, amber for degraded-but-safe region, red-bordered zones for explicit boundary conditions.
This should look like a research systems boundary diagram, not a business infographic.
```

### Suggested caption direction

Summarize the controller's observed operational envelope and failure boundaries without relying on yet another time-series graph.

---

## Optional Prompt 8: Two-Cycle Restorm Workload Timeline

### Purpose

Useful if you want one compact visual that explains the experiment pattern used for C, D, and E.

### Best place

- around Experiment C load-pattern subsection.
- current area: [paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:531)

### Ready prompt

```text
Generate a 4K timeline diagram for a systems research paper, white background, vector-like style.

Title inside the figure:
"Two-Cycle Restorm Workload Timeline"

Create a horizontal timeline with four labeled phases:
1. CYCLE 1: 0-150s
   "800 clients connect and stay active"
2. DROP 1: 150-240s
   "All clients disconnect; transient 90s zero-connection gap"
3. CYCLE 2: 240-390s
   "Fresh 800-client cohort reconnects"
4. FINAL DROP: 390-570s
   "Permanent disconnection; scale-down should proceed"

Above the timeline, show expected connection count state:
- high,
- zero,
- high,
- zero

Below the timeline, show what the ideal connection-aware controller should do:
- scale to 8
- hold 8 warm
- continue serving immediately
- then release down toward minReplicas

Add a small red note off to one side:
"CPU-only HPA misinterprets DROP 1 as safe to shrink"

Keep it minimal, precise, and very readable.
```

---

## Suggested LaTeX Figure Skeleton

Use something like this for any newly added PNG:

```latex
\begin{figure*}[t]
\centering
\includegraphics[width=0.85\textwidth]{processed-results-websockets/diagrams/<filename>.png}
\caption{<caption>}
\label{fig:<label>}
\end{figure*}
```

For smaller, single-column diagrams:

```latex
\begin{figure}[ht]
\centering
\includegraphics[width=\columnwidth]{processed-results-websockets/diagrams/<filename>.png}
\caption{<caption>}
\label{fig:<label>}
\end{figure}
```

---

## Recommended File Layout

Create a new paper image folder:

```text
Paper-Latex/processed-results-websockets/diagrams/
```

Put only the non-graph explanatory figures there.

Suggested filenames:

- `krm-vs-pcm-pipeline.png`
- `evidence-chain-overview.png`
- `pod-termination-limbo-sequence.png`
- `statefulautoscaler-architecture.png`
- `statefulautoscaler-reconcile-activity.png`
- `stabilization-semantics-comparison.png`
- `failure-envelope.png`
- `two-cycle-restorm-timeline.png`

---

## What Not To Add

Avoid adding diagrams that only repeat what existing plots already show.

Avoid:

- generic Kubernetes clipart,
- a second HPA architecture figure that duplicates the one already in the paper,
- screenshots of code,
- UML class diagrams for every Go struct,
- decorative research-style cubes, layers, or pseudo-3D cloud icons,
- "framework overview" diagrams that say nothing beyond the abstract.

The strongest additions are the ones that explain semantics, timing, and control path differences that are currently only verbal.

---

## Best Final Set

If you want the strongest final paper visual package without overloading it, add these five:

1. `statefulautoscaler-architecture`
2. `statefulautoscaler-reconcile-activity`
3. `pod-termination-limbo-sequence`
4. `stabilization-semantics-comparison`
5. `evidence-chain-overview`

That set would materially improve readability in the places where the current draft is most concept-dense.
