# Understanding Experiment B: StatefulAutoscaler on MQTT Load

> **Prerequisite:** Read `md/mqtt-exp-a-explainer.md` first. Experiment B uses the same MQTT broker and load generator, but replaces HPA with the custom StatefulAutoscaler.
> **Plot:** `results/raw/mqtt/experiment-b-stateful/plot.png`
> **Raw data:** `results/raw/mqtt/experiment-b-stateful/`

---

## 1. What this run proves

Experiment B is the completed StatefulAutoscaler run for MQTT persistent connections.

The result is useful, but it is a little more nuanced than the original expectation:

| Question | Result from this run |
|----------|----------------------|
| Did StatefulAutoscaler react to MQTT connection count? | **Yes.** It scaled from 1 to 2 replicas at t=31s, then to 3 at t=46s. |
| Did CPU explain the scale-up? | **No.** CPU stayed low; the controller was reacting to `active_connections`. |
| Did the system accept all 1000 clients? | **No.** 339 connected and 661 failed during the aggressive initial ramp. |
| Did existing MQTT connections redistribute across the new pods? | **No.** Existing TCP sessions stayed pinned to the original pod. |
| Did scale-down avoid killing live connections? | **Yes.** Connections dropped to zero before replicas were removed. |

So the clean thesis is:

> StatefulAutoscaler correctly adds capacity based on live MQTT connections and scales down after the workload drains, but Kubernetes does not automatically rebalance already-open MQTT/TCP connections across newly-created pods.

That last sentence matters. It makes the paper stronger because it separates **autoscaling** from **connection migration/rebalancing**. StatefulAutoscaler can make the right replica decision, but moving established MQTT sessions requires a separate mechanism such as client reconnect, broker clustering, session migration, or deliberate drain/reconnect behavior.

---

## 2. Experiment setup

Experiment B deploys:

| Component | Value |
|-----------|-------|
| Broker | `workloads/mqtt/app/broker.py` |
| Load generator | `load-generator/mqtt-client/client.py` |
| Autoscaler | `experiments/mqtt/experiment-b-stateful/statefulautoscaler.yaml` |
| Target | `150` connections per pod |
| Max replicas | `10` |
| Scale-up cooldown | `20s` |
| Scale-down cooldown | `120s` |
| Drain enabled | `true` |

The StatefulAutoscaler watches the Prometheus metric:

```promql
sum(active_connections)
```

Then it computes:

```text
desired_replicas = ceil(total_active_connections / 150)
```

For this run, peak connections were 339, so the expected steady replica count is:

```text
ceil(339 / 150) = 3 replicas
```

That is exactly what happened.

---

## 3. Run timeline

Phase markers from `out_phases.json`:

```json
{
  "PHASE1_START": 8,
  "PHASE1_END": 308,
  "PHASE2_START": 309,
  "PHASE2_END": 489,
  "PHASE3_START": 490,
  "PHASE3_END": 821
}
```

Timeline:

```text
0s                  309s                 490s                 821s
|-------------------|--------------------|--------------------|
Phase 1             Phase 2              Phase 3
Ramp 1000 clients   Observe steady state Kill loadgen and drain
```

Key events from `out.csv`:

| Time | Connections | Replicas | Meaning |
|------|-------------|----------|---------|
| t=0s | 0 | 1 | Broker starts with one replica |
| t=10s | 21 | 1 | Initial MQTT clients connect |
| t=26s | 271 | 1 | Connections exceed 150 target |
| t=31s | 271 | 2 | StatefulAutoscaler scales up |
| t=41s | 339 | 2 | Peak connection count reached |
| t=46s | 339 | 3 | Controller reaches desired replica count |
| t=490s | 339 | 3 | Phase 3 begins; loadgen is deleted |
| t=524s | 0 | 3 | Clients have disconnected cleanly |
| t=641s | 0 | 2 | Scale-down starts after cooldown |
| t=646s | 0 | 1 | Controller returns to minimum replica count |

The final zero-replica point near t=825s is cluster cleanup, not autoscaler behavior.

---

## 4. Load generator result

The load generator attempted 1000 MQTT clients over a 60-second ramp.

From `loadgen-phase2.log`:

```text
[STATUS] connected=0/1000
[STATUS] connected=166/1000
[STATUS] connected=332/1000
[STATUS] connected=339/1000
...
[STATUS] connected=339/1000
```

Final outcome:

| Metric | Value |
|--------|-------|
| Requested clients | 1000 |
| Connected clients | 339 |
| Failed/refused clients | 661 |
| Peak Prometheus `active_connections` | 339 |

The failures happened during the initial connection storm. The broker accepted 339 long-lived MQTT sessions, while the remaining clients timed out or failed to complete their initial connection. This is not evidence that the StatefulAutoscaler failed to calculate replicas; it did calculate 3 replicas correctly. It shows that reactive scale-up can still be too late for a sharp connection ramp unless capacity exists before the storm or clients retry after new pods become ready.

---

## 5. How to read the graph

The plot has three panels:

1. Total MQTT connections and broker replicas
2. CPU usage
3. Per-pod MQTT connections

Each panel tells a different part of the story.

---

## 6. Panel 1: Total connections and replicas

Panel 1 is the main autoscaling panel.

What you should see:

```text
Connections:
0 -> 21 -> 271 -> 339 -------------------- 339 -> 0

Replicas:
1 ---------------- 2 ---- 3 -------------- 3 ---- 2 -> 1
                  t=31s  t=46s                  t=641s
```

Interpretation:

- The blue connection line rises quickly during Phase 1 and plateaus at 339 connected clients.
- The red replica line starts at 1, steps to 2 at t=31s, and steps to 3 at t=46s.
- The desired replica count for 339 connections is `ceil(339 / 150) = 3`, so the controller reaches the correct target.
- During Phase 2, the system holds 3 replicas while the 339 MQTT sessions remain open.
- During Phase 3, the load generator is deleted at t=490s. Connections drop to 0 by t=524s.
- The controller does not immediately remove pods when clients disappear. It waits for the scale-down cooldown and then reduces replicas at t=641s and t=646s.

The most important takeaway from Panel 1:

> StatefulAutoscaler reacts to connection count, not CPU. It scales to the mathematically correct replica count for the observed MQTT load.

---

## 7. Panel 2: CPU usage

Panel 2 explains why CPU-based HPA is the wrong signal for this workload.

From `out.csv`:

| Metric | Value |
|--------|-------|
| Peak total CPU | 67m |
| Time of peak CPU | t=59s |
| Steady-state CPU | roughly 3m-10m |

From `cpu.log`, the broker pods stayed very light:

```text
1778234101
mqtt-broker-58977c7947-c24gj   36m   24Mi
mqtt-broker-58977c7947-mpq8z   15m   24Mi
mqtt-broker-58977c7947-tszbt   16m   27Mi
```

At that moment, total CPU was 67m across three pods, or roughly 22m per pod on average. MQTT idle connections consume memory and file descriptors, but very little CPU. That is exactly why a CPU-only autoscaler can miss the real pressure.

Important note about memory:

`memory.log` recorded `0` for this run, so the plotted CSV has `memory_mi=0`. The CPU log still includes memory from `kubectl top`; from that log, peak total broker memory was about 79Mi and peak per-pod memory was about 29Mi. The graph should therefore be treated as a CPU graph, not a reliable memory graph for this run.

The most important takeaway from Panel 2:

> CPU stayed low even while hundreds of persistent MQTT sessions were open, so CPU is not a reliable scaling signal for this workload.

---

## 8. Panel 3: Per-pod connections

Panel 3 is the most important correction to the original expectation.

The per-pod CSV now contains real pod labels:

```csv
time_s,conn_mqtt-broker-58977c7947-c24gj,conn_mqtt-broker-58977c7947-mpq8z,conn_mqtt-broker-58977c7947-tszbt
...
42,0,0,339
...
```

Maximum per-pod values:

| Pod | Max connections |
|-----|-----------------|
| `mqtt-broker-58977c7947-tszbt` | 339 |
| `mqtt-broker-58977c7947-mpq8z` | 0 |
| `mqtt-broker-58977c7947-c24gj` | 0 |

That means the 339 successful MQTT sessions stayed on the first broker pod. The two pods created by the StatefulAutoscaler were ready, but they did not receive the already-established sessions.

This is expected behavior for Kubernetes Services and TCP:

- A Kubernetes Service load-balances **new** TCP connections.
- It does not move an existing TCP connection after it has been accepted by a backend pod.
- MQTT sessions are long-lived, so once a client is connected to one pod, it stays there until it disconnects.
- Scaling up creates new capacity for future clients, but it does not automatically rebalance old clients.

The most important takeaway from Panel 3:

> StatefulAutoscaler solved the replica-count decision, but this run did not solve connection redistribution. Rebalancing persistent MQTT sessions needs a separate strategy.

---

## 9. What each phase proves

### Phase 1: Scale-up response

The controller saw connections exceed the target:

```text
t=26s: 271 connections, 1 replica
```

Then it scaled:

```text
t=31s: 271 connections, 2 replicas
t=46s: 339 connections, 3 replicas
```

This proves the controller is watching the right metric and computes the right desired replica count.

### Phase 2: Steady state

The system stayed at:

```text
339 connections
3 replicas
```

This proves the controller held enough capacity for the observed connection count.

However, Panel 3 shows those connections were not spread across the replicas. So Phase 2 should be described as **steady capacity**, not **even distribution**.

### Phase 3: Scale-down behavior

At t=490s, the load generator is deleted. By t=524s, `active_connections` is 0.

Only after the workload is gone does the controller reduce replicas:

```text
t=641s: 3 -> 2 replicas
t=646s: 2 -> 1 replica
```

This proves scale-down happened after clients disconnected, avoiding the bad pattern where live connections are killed by pod termination.

---

## 10. Final numbers

| Metric | Experiment B result |
|--------|---------------------|
| Requested clients | 1000 |
| Connected clients | 339 |
| Failed clients | 661 |
| Peak `active_connections` | 339 |
| Peak replicas | 3 |
| First scale-up | t=31s |
| Reached 3 replicas | t=46s |
| Connections dropped to 0 | t=524s |
| First scale-down | t=641s |
| Returned to 1 replica | t=646s |
| Peak total CPU | 67m |
| Peak total memory from `cpu.log` | about 79Mi |
| Peak per-pod memory from `cpu.log` | about 29Mi |
| Per-pod distribution | 339 / 0 / 0 |

---

## 11. Files used for this explanation

| File | What it explains |
|------|------------------|
| `out.csv` | Main graph data: time, replicas, total connections, CPU |
| `out_perpod.csv` | Per-pod connection graph |
| `out_phases.json` | Phase boundary markers |
| `active_connections.log` | Raw Prometheus `sum(active_connections)` samples |
| `perpod_connections.log` | Raw Prometheus per-pod samples |
| `pods.log` | Pod lifecycle and replica count |
| `cpu.log` | Per-pod CPU and memory from `kubectl top` |
| `memory.log` | Intended memory collector, but recorded zeros in this run |
| `loadgen-phase1.log` | Early load generator output |
| `loadgen-phase2.log` | Later load generator output with final connected count |
| `phase2_pod_snapshot.log` | Confirms three broker pods were running in Phase 2 |

---

## 12. Paper-ready interpretation

Experiment B demonstrates that a connection-aware autoscaler makes better replica decisions for persistent-connection workloads than a CPU-only policy. The StatefulAutoscaler observed MQTT connection count directly and scaled from 1 to 3 replicas within 46 seconds, matching `ceil(339 / 150)`.

The run also exposes a key limitation: scaling alone does not redistribute existing MQTT sessions. Panel 3 shows all 339 live sessions remained on the original pod while the two new pods stayed at zero connections. This is normal TCP behavior under Kubernetes Services, and it means a full solution for MQTT needs both:

- a connection-aware autoscaler to choose the right replica count
- a connection-management strategy to spread or migrate long-lived sessions

For scale-down, the result is cleaner. The workload disconnected first, then the controller removed replicas after cooldown. That is the behavior we want for stateful protocols: do not terminate pods while they still own live client sessions.

The final claim should be:

> StatefulAutoscaler fixes the scaling signal and scale-down timing for MQTT, but per-pod redistribution of established MQTT sessions remains a separate systems problem.
