# STAR Project Intern Guide

> Audience: intern or early learner.
> Goal: understand the STAR research project deeply enough to explain every phase, read the code, reproduce the experiments, and then implement the MQTT part independently.

This guide is intentionally slower than a normal engineering README. It teaches the missing prerequisites before each phase, then explains the actual project idea from basic to advanced. Do not rush it. The project is not just "Kubernetes scaling"; it is about why the default scaling idea breaks when the workload holds long-lived connections.

---

## Navigation Map

1. [How to Use This Guide](#how-to-use-this-guide)
2. [Big Picture in Plain English](#big-picture-in-plain-english)
3. [Prerequisite Bootcamp](#prerequisite-bootcamp)
4. [Project Structure You Should Know](#project-structure-you-should-know)
5. [Phase 0: Base Paper Replication](#phase-0-base-paper-replication)
6. [Phase 1 Shared Infrastructure: WebSocket Experiments](#phase-1-shared-infrastructure-websocket-experiments)
7. [Experiment A: HPA Baseline](#experiment-a-hpa-baseline)
8. [Experiment B1: Cyclic Churn and Over-Provisioning](#experiment-b1-cyclic-churn-and-over-provisioning)
9. [Experiment B2: Forced Scale-Down and Reconnection Storms](#experiment-b2-forced-scale-down-and-reconnection-storms)
10. [Experiment B3: Idle Connections and Permanent Loss](#experiment-b3-idle-connections-and-permanent-loss)
11. [Experiment C: StatefulAutoscaler Solution](#experiment-c-statefulautoscaler-solution)
12. [Experiment D: HPA with Custom Connection Metric](#experiment-d-hpa-with-custom-connection-metric)
13. [Experiment E: KEDA Baseline](#experiment-e-keda-baseline)
14. [Failure Mode Experiments](#failure-mode-experiments)
15. [Evidence Chain Summary](#evidence-chain-summary)
16. [How to Read the Controller Code](#how-to-read-the-controller-code)
17. [Phase 2: MQTT Generalisation Project](#phase-2-mqtt-generalisation-project)
18. [Common Mistakes to Avoid](#common-mistakes-to-avoid)
19. [Final Master Quiz](#final-master-quiz)

---

## How to Use This Guide

Read in order the first time. The experiments form a chain:

```text
Base HPA theory
-> WebSocket failure evidence
-> Custom StatefulAutoscaler solution
-> Stronger baselines: HPA custom metric and KEDA
-> Failure scenarios
-> MQTT generalisation project
```

For every phase, ask yourself:

- What is being tested?
- Why is this experiment needed?
- What must already be true before the experiment makes sense?
- What did the result prove?
- What did it not prove?

The root folders with theory are:

- `md/` - project notes, plans, experiment guides, MQTT plan, KEDA notes, flaws/review notes.
- `Paper-Latex/` - paper source, final plots, paper-specific framing.
- `paper.md` - current paper game plan and status.
- `experiments.md` - chronological experiment log.
- `README.md` - main project overview.

---

## Big Picture in Plain English

Kubernetes can automatically add or remove pods. The built-in tool for this is HPA, the Horizontal Pod Autoscaler. HPA usually watches CPU.

That works for a normal HTTP API:

```text
request arrives -> pod uses CPU -> request finishes -> pod is free again
```

But this project studies persistent connections such as WebSocket and MQTT:

```text
client connects -> connection stays open for minutes or hours -> client may send nothing
```

An idle connection may use almost no CPU, but it is still important state. If Kubernetes kills the pod holding that connection, the client is disconnected. If many clients are disconnected at the same time, they reconnect at the same time. That creates a reconnection storm.

The STAR project builds and evaluates a custom Kubernetes controller called `StatefulAutoscaler`. Instead of asking "how much CPU is being used?", it asks "how many active connections exist?" It then keeps enough pods alive for those connections and uses a cooldown window before scaling down.

Current paper status from `paper.md` and actual results in the repo:

- WebSocket evidence chain is complete.
- Experiment C, the custom controller validation, has 5 multi-run data sets (runs 2-5 used; run 1 excluded for log artifact).
- Experiment D, HPA with a custom connection metric, has 5 multi-run data sets.
- Experiment E, KEDA, has 5 completed runs with processed results (pod_seconds ~4145, scale-up ~26s, peak replicas 8).
- Failure scenarios (metric staleness, instant spike, Prometheus outage) are fully tested with raw data, processed CSVs, and plots.
- MQTT experiments are implemented (all code, manifests, scripts created) but not yet run. Results are pending.

---

## Prerequisite Bootcamp

This section gives you the minimum vocabulary needed before the phases.

### Kubernetes Objects

Think of Kubernetes as a system that keeps the real world matching a desired state.

| Term | Easy meaning | Why it matters here |
|---|---|---|
| Cluster | The whole Kubernetes environment | All experiments run inside a local cluster |
| Node | One machine in the cluster | Pods are scheduled onto nodes |
| Pod | One running instance of an app | Connections live inside pods |
| Deployment | A rule saying "keep N pods running" | Autoscalers change `spec.replicas` on a Deployment |
| Service | Stable network name for a group of pods | Clients connect to the Service, not a specific pod |
| HPA | Built-in scaler that changes replica count | Main baseline being tested |
| Metrics Server | Provides CPU/memory metrics to Kubernetes | Needed by CPU-based HPA |
| Prometheus | Pulls app metrics like `active_connections` | Used by the custom controller and custom metric baselines |
| CRD | Adds a new custom Kubernetes object type | `StatefulAutoscaler` is a CRD |
| Controller | Program that watches objects and acts | STAR controller watches `StatefulAutoscaler` objects |

### Containers and `kind`

The experiments use `kind`, which means Kubernetes in Docker. A `kind` cluster is made of Docker containers pretending to be Kubernetes nodes. This makes experiments cheap and reproducible.

Why use `kind`:

- No cloud bill.
- Same cluster can be recreated again and again.
- Good for controlled experiments.

Limitations:

- All nodes still share one physical machine.
- CPU measurements are relative to the host.
- Real cloud scheduling and networking may behave differently.

### HPA in One Formula

HPA periodically computes:

```text
desiredReplicas = ceil(currentReplicas * currentMetric / targetMetric)
```

Example:

```text
currentReplicas = 2
current CPU = 120%
target CPU = 60%

desiredReplicas = ceil(2 * 120 / 60) = ceil(4) = 4
```

So HPA scales from 2 pods to 4 pods.

Important: HPA does not think like a human. It does not know users, sessions, or protocols. It sees numbers and changes replica count.

### Scale-Up vs Scale-Down

Scale-up is usually fast because lack of capacity hurts users immediately.

Scale-down is usually delayed because removing pods too quickly can cause instability. HPA's default scale-down stabilization window is 300 seconds, or 5 minutes.

That delay helps stateless apps, but it does not solve the persistent connection problem. Eventually HPA still removes pods, and if those pods hold live sessions, the sessions die.

### Stateless vs Stateful

Stateless workload:

```text
request 1 can go to pod A
request 2 can go to pod B
no important memory is tied to pod A
```

Stateful persistent-connection workload:

```text
client opens WebSocket or MQTT session to pod A
session stays on pod A
if pod A dies, the session dies
```

This difference is the heart of the project.

### WebSocket

WebSocket is a long-lived connection often used for chat, multiplayer games, live dashboards, and collaboration tools. Unlike normal HTTP, the connection remains open so both client and server can send messages whenever needed.

In this repo:

- WebSocket server: `workloads/websocket/app/server.py`
- Instrumented server: `workloads/websocket/app-instrumented/server.py`
- Load generator: `load-generator/websocket-client/client.py`

The instrumented server exposes:

- `active_connections` - current open connections.
- `new_connections_total` - total connections ever opened.
- `/metrics` - Prometheus scrape endpoint.
- `/drain` - asks server to stop accepting new connections.

### MQTT

MQTT is a publish/subscribe protocol used heavily in IoT.

Core words:

| MQTT term | Meaning |
|---|---|
| Broker | Server that receives and routes messages |
| Client | Device/app that connects to the broker |
| Topic | Named channel like `factory/line1/temp` |
| Publish | Send a message to a topic |
| Subscribe | Ask to receive messages from a topic |
| Keepalive | Lightweight heartbeat so broker knows client is alive |
| QoS | Delivery guarantee level |
| Session | Broker-side state for a client |

MQTT has the same scaling problem as WebSocket: clients can stay connected while sending almost nothing. CPU can be near zero while thousands of sessions are alive.

### Prometheus Metrics: Gauge vs Counter

A Gauge can go up and down.

```text
active_connections = 800
active_connections = 790
active_connections = 812
```

A Counter only goes up.

```text
new_connections_total = 1000
new_connections_total = 1800
new_connections_total = 2600
```

Why this matters:

- Use a Gauge for "how many are active now?"
- Use a Counter for "how fast are new connections arriving?"

Prometheus can calculate reconnection storm rate with:

```text
rate(new_connections_total[15s])
```

### Check Yourself

Questions:

1. Why can CPU be a bad signal for WebSocket or MQTT load?
2. What does `active_connections` measure?
3. Why is `new_connections_total` a Counter instead of a Gauge?
4. What does HPA actually modify when it scales a Deployment?
5. Why is `kind` useful for this project?

Answers:

1. Because clients can keep long-lived sessions open while sending no messages, so CPU can be near zero even though the app is serving many live users.
2. It measures the number of currently open persistent connections.
3. Reconnection rate is calculated from the increase over time. A Counter only increases, so Prometheus `rate()` can safely compute connections per second.
4. It changes the Deployment's desired replica count, usually `deployment.spec.replicas`.
5. It creates a reproducible local Kubernetes cluster without cloud infrastructure.

---

## Project Structure You Should Know

Important paths:

```text
workloads/websocket/
  app/server.py
  app-instrumented/server.py
  k8s/deployment.yml
  k8s/service.yml

workloads/mqtt/
  app/broker.py
  app/Dockerfile
  k8s/deployment.yml
  k8s/service.yml
  k8s/hpa.yml

load-generator/websocket-client/
  client.py

load-generator/mqtt-client/
  client.py
  Dockerfile

controller/
  api/v1alpha1/statefulautoscaler_types.go
  internal/controller/statefulautoscaler_controller.go
  internal/controller/prometheus.go

monitoring/prometheus/
  configmap.yaml
  deployment.yaml
  service.yaml

scripts/
  run-experiment-a.sh
  run-experiment-b1.sh
  run-experiment-b2-instrumented.sh
  run-experiment-b3.sh
  run-experiment-c.sh
  run-experiment-d.sh
  run-experiment-e.sh
  run-failure-scenarios.sh
  run-experiment-mqtt-a.sh
  run-experiment-mqtt-b.sh
  run-experiment-mqtt-c.sh
  run-multi.sh

experiments/websocket/
  experiment-a-hpa-baseline/
  experiment-b1-hpa-churn/
  experiment-b2-hpa-churn-instrumented/
  experiment-b3-hpa-idle-connections/
  experiment-c-stateful/
  experiment-d-hpa-custom-metric/
  experiment-e-keda/

experiments/mqtt/
  experiment-a-hpa-baseline/
  experiment-b-stateful/
  experiment-c-idle-connections/

analysis/
  parse_logs.py, plot_experiment.py
  experiment-b3/
  experiment-c/
  experiment-d/
  experiment-e/
  failure-scenarios/
  mqtt/

results/
  raw/websocket/       (single-run raw logs)
  raw/websocket/multi/ (multi-run raw logs for C, D, E)
  raw/websocket/failure-{1,2,3}-*/
  processed/websocket/ (parsed CSVs, plots, summaries)

md/
  theory, plans, KEDA notes, MQTT plan, next-steps

Paper-Latex/
  paper source and publication figures
```

Every experiment follows the same skeleton:

```text
create fresh kind cluster
-> install metrics-server
-> install Prometheus
-> build/load images
-> deploy workload and scaler
-> run load generator
-> collect logs every few seconds
-> parse logs
-> generate plots
-> interpret result
```

---

## Phase 0: Base Paper Replication

Phase 0 is not the final contribution. It is the foundation. It teaches what normal HPA behavior looks like when HPA is used on stateless HTTP workloads.

### Prerequisites for Phase 0

Before Phase 0, understand:

- HTTP request lifecycle.
- CPU utilization.
- HPA formula.
- Metrics Server.
- Prometheus scrape interval.
- Difference between a lagging metric and leading metric.

Lagging metric:

```text
CPU rises after work has already arrived.
```

Leading metric:

```text
Request rate rises as soon as traffic arrives, before CPU fully reacts.
```

### Concept: Why Start with Stateless HTTP?

The project begins with stateless HTTP because that is where HPA is expected to work. If HPA behaves badly even there, then later WebSocket failures might be caused by broken setup. By proving the setup works for stateless workloads, the project creates a fair baseline.

This is good experimental design:

```text
first show the tool works in its intended environment
then change one assumption
then observe what breaks
```

### KRM Experiment: CPU-Based HPA

KRM means Kubernetes Resource Metrics. In practice, this means HPA reads CPU from Metrics Server.

What was tested:

- A stateless HTTP app that burns CPU per request.
- A load generator sends high and low request phases.
- HPA scales based on average CPU.
- Metrics Server resolution is changed: 15s, 30s, 60s.

Why:

- To learn how metric freshness affects HPA reaction time.
- To reproduce the base paper behavior.
- To establish that the local Kubernetes setup can produce meaningful scaling data.

Important idea: the staircase effect.

If Metrics Server refreshes every 60 seconds but HPA checks every 15 seconds, HPA may read the same stale CPU value several times:

```text
t=0s:   load starts
t=15s:  HPA checks old CPU
t=30s:  HPA checks old CPU
t=45s:  HPA checks old CPU
t=60s:  Metrics Server finally has new CPU
t=60s:  HPA can react
```

Result:

- 15s resolution reacts faster.
- 60s resolution creates more visible lag and stepwise scaling.
- For stateless HTTP, this is a performance problem, not a correctness problem.

Why it matters later:

Even perfect CPU sampling cannot tell us whether a pod holds live WebSocket or MQTT connections. Phase 0 teaches that metric timing matters, but later phases show that metric meaning matters even more.

### PCM Experiment: Prometheus Custom Metrics

PCM means Prometheus Custom Metrics.

Instead of using only CPU from Metrics Server, HPA can consume metrics exposed by the application and collected by Prometheus.

Three configurations:

| Name | Metric | Meaning |
|---|---|---|
| PCM-CPU | CPU through Prometheus | Same signal, different pipeline |
| PCM-H | HTTP requests per second | Leading traffic signal |
| PCM-CH | Max of CPU and HTTP recommendation | Hybrid safety signal |

What was tested:

- Does a custom metric help HPA react faster?
- How does Prometheus scrape interval affect freshness?
- Can request rate act before CPU saturates?

Why:

- To show that choosing better metrics can improve autoscaling.
- To create the bridge from "CPU only" to "application-level metrics."

Result:

- Request rate can react earlier than CPU for HTTP.
- Prometheus scrape interval still matters.
- Hybrid metrics can reduce saturation.

But:

This still assumes scale-down is safe. For persistent connections, scale-down itself can be dangerous.

### Phase 0 Lesson

HPA can be made smarter for stateless workloads. But Phase 0 does not solve stateful scaling. It only teaches the measurement pipeline and the control loop.

### Phase 0 Quiz

Questions:

1. What does KRM use as the metric source?
2. What does PCM add that KRM does not have?
3. Why does a 60s scrape or metric resolution create a staircase?
4. Why is HTTP request rate a leading indicator?
5. Why does Phase 0 not prove anything about WebSocket safety?

Answers:

1. Metrics Server resource metrics, mainly CPU and memory.
2. Application/custom metrics through Prometheus and the Prometheus Adapter path.
3. HPA checks more often than the metric updates, so it repeatedly sees stale values and changes replicas in delayed steps.
4. It changes when traffic arrives, before CPU has fully accumulated.
5. Stateless HTTP has no long-lived session tied to a pod, so killing a pod is not the same as killing hundreds of live sessions.

---

## Phase 1 Shared Infrastructure: WebSocket Experiments

Before looking at individual WebSocket experiments, understand the common setup.

### Prerequisites for Phase 1

You should know:

- WebSocket is persistent.
- A Kubernetes Service load-balances new connections to pods.
- Once a WebSocket is connected, it stays on that pod.
- Prometheus scrapes `/metrics` every 15 seconds.
- `active_connections` is a Gauge.
- `new_connections_total` is a Counter.

### WebSocket Server

The instrumented server is in:

```text
workloads/websocket/app-instrumented/server.py
```

Core behavior:

```text
new WebSocket connects
-> ACTIVE_CONNECTIONS increases by 1
-> NEW_CONNECTIONS increases by 1
-> client sends messages
-> optional CPU loop runs depending on CPU_WORK
-> connection closes
-> ACTIVE_CONNECTIONS decreases by 1
```

`CPU_WORK` is critical:

| `CPU_WORK` | Meaning |
|---|---|
| `1` or more | Messages cause CPU work, so CPU roughly follows active sending |
| `0` | Messages do almost no CPU work, so connections can exist while CPU stays low |

### Load Generator

The load generator creates many async WebSocket clients. It can:

- ramp clients gradually,
- keep connections active by sending pings,
- hold connections idle,
- reconnect or refuse to reconnect depending on experiment.

The ramp is important. If 800 clients connect at exactly the same instant, the first 2 pods may be overloaded before Kubernetes can scale.

### Prometheus

Prometheus finds pods using annotations:

```yaml
prometheus.io/scrape: "true"
prometheus.io/port: "8080"
prometheus.io/path: "/metrics"
```

It scrapes every 15 seconds in this project. That gives a reasonably fresh view while still matching HPA's normal timing.

### What and Why of the WebSocket Evidence Chain

The WebSocket experiments are not random. Each one removes one excuse.

```text
A: HPA can work when CPU matches connections.
B1: Real cyclic load causes over-provisioning.
B2: Scale-down causes reconnection storms.
B3: Idle connections can be permanently destroyed.
C: Custom controller avoids the failure.
D: Metric alone is not enough.
E: KEDA is a strong baseline but not fully equivalent yet.
```

---

## Experiment A: HPA Baseline

### Prerequisites

Know:

- HPA CPU scaling.
- `CPU_WORK`.
- Why a baseline matters.

### What Was Tested?

Can CPU-based HPA scale a WebSocket app at all if we give it ideal conditions?

The setup makes CPU and connections correlated:

```text
more clients -> more pings -> more CPU -> HPA scales up
fewer clients -> less CPU -> HPA scales down
```

### Why This Experiment Exists

If HPA failed immediately, a reviewer could say the setup is broken. Experiment A shows HPA works when its assumption is true.

### Result

In the observed baseline:

- active connections peaked around 388 in the clean trace,
- HPA scaled from 2 to 5 pods,
- scale-down happened only after the long default stabilization window.

### What This Teaches

HPA is not useless. It is useful when CPU is a good proxy for load. The later experiments are not "HPA is bad"; they are "CPU is the wrong signal for persistent idle sessions."

### Experiment A Quiz

Questions:

1. Why is Experiment A called a best-case HPA experiment?
2. What role does `CPU_WORK=1` play?
3. Why does HPA wait before scaling down?
4. What would happen if clients stayed connected but stopped sending pings?

Answers:

1. Because every active connection produces CPU, which is exactly what HPA watches.
2. It creates artificial CPU work per message so CPU tracks active clients.
3. HPA has a scale-down stabilization window to avoid removing pods during short dips.
4. CPU would drop, so HPA would eventually think pods are unnecessary even if sessions are still alive.

---

## Experiment B1: Cyclic Churn and Over-Provisioning

### Prerequisites

Know:

- HPA scale-down stabilization.
- Difference between active and idle phases.
- Why repeated cycles can confuse a control loop.

### What Was Tested?

The workload alternates:

```text
HIGH phase: clients send messages, CPU rises
LOW phase: clients stop sending, CPU falls
repeat
```

Connections may remain open even when CPU falls.

### Why This Experiment Exists

Real systems are cyclic:

- games have rounds,
- trading apps have bursts,
- dashboards refresh in waves,
- chat apps have quiet periods.

The experiment asks: what happens when low periods are shorter than HPA's scale-down window?

### The Trap

HPA's default scale-down window is 300 seconds. If the LOW phase is only around 30-60 seconds, the scale-down timer starts but never finishes.

```text
LOW starts -> HPA begins waiting
HIGH returns before 300s -> CPU rises -> wait resets
```

Scale-up can happen repeatedly, but scale-down is blocked. Replica count climbs and stays too high.

### Result

The paper's headline behavior:

- HPA hit `maxReplicas=15` quickly.
- It took hundreds of seconds to recover after the cycles ended.
- This created major over-provisioning.

The paper frames one key comparison as 15 pods vs an intended correct count around 8 pods for an 800-client design, which is 87.5% over-provisioning.

### What This Teaches

HPA's default safety behavior can become resource waste for cyclic stateful workloads. This is not connection destruction yet. It is the first warning sign.

### Experiment B1 Quiz

Questions:

1. Why does HPA scale up during HIGH phases?
2. Why does it not scale down during short LOW phases?
3. Why is over-provisioning a problem?
4. Does B1 prove connections are killed?

Answers:

1. CPU rises above the target.
2. The LOW phase ends before the scale-down stabilization window expires.
3. Extra pods cost resources and can hide instability until later scale-down happens.
4. No. B1 mainly proves slow recovery and over-provisioning.

---

## Experiment B2: Forced Scale-Down and Reconnection Storms

There are two B2 ideas:

- B2 Extended LOW: a pilot that qualitatively showed scale-down kills live connections.
- B2 Instrumented: the publishable version that measured the storm.

### Prerequisites

Know:

- What happens when Kubernetes terminates a pod.
- Difference between a current count and a rate.
- Why a reconnecting client can create CPU load.

### What Was Tested?

The LOW phase is long enough for HPA to actually scale down. When HPA removes pods, clients on those pods are disconnected. Reconnecting clients create a burst.

### Why This Experiment Exists

B1 showed waste. B2 shows correctness failure. It answers:

```text
When HPA finally removes pods, what happens to the live sessions?
```

### Reconnection Storm

If 800 clients are connected and HPA kills pods holding many of them:

```text
connections die
-> clients notice
-> clients reconnect
-> server receives many handshakes at once
-> CPU spikes
-> HPA may scale up again
```

This feedback loop can keep the system unstable.

### Instrumentation

B2 Instrumented added Prometheus metrics:

- `active_connections`: current open sessions.
- `new_connections_total`: cumulative connections opened.

Storm rate:

```text
rate(new_connections_total[15s])
```

### Result

Important measured numbers:

- target clients: 800,
- active connection overshoot: up to 1,215,
- peak reconnection storm: about 1,400 connections/second,
- HPA repeatedly hit `maxReplicas=15`.

The overshoot can happen because new connections arrive while old server-side connection objects have not fully cleaned up yet.

### What This Teaches

HPA scale-down can actively create the next scale-up. The scaler causes the disruption, then reacts to the disruption as if it were external load.

### Experiment B2 Quiz

Questions:

1. What is a reconnection storm?
2. Why is `new_connections_total` needed?
3. Why can active connections briefly exceed the intended client count?
4. What did B2 prove that B1 did not?

Answers:

1. Many clients reconnect at the same time after being disconnected.
2. It lets Prometheus calculate how quickly new connections are being established.
3. Old connection state may not be cleaned up before new connections arrive.
4. B2 proved that HPA scale-down can directly cause large measurable reconnection bursts.

---

## Experiment B3: Idle Connections and Permanent Loss

### Prerequisites

Know:

- Idle WebSocket connections can use almost no CPU.
- HPA can be configured with a shorter scale-down window.
- Clients may or may not reconnect after failure.

### What Was Tested?

Clients connect and initially send messages, causing CPU so HPA scales up. Then clients stop sending but keep the socket open.

The clients are deliberately configured not to reconnect if disconnected during the idle phase.

### Why This Experiment Exists

B2 showed reconnecting clients cause storms. B3 shows something even sharper: if clients do not reconnect, HPA permanently destroys sessions.

This models:

- embedded devices with weak reconnect logic,
- batch stream consumers,
- long-running monitoring sessions,
- clients where disconnect is treated as fatal.

### The Core Timeline

```text
800 clients connect
-> CPU rises
-> HPA scales up
-> clients go idle
-> CPU falls to near zero
-> HPA waits around 60s
-> HPA scales down
-> pods die
-> connections on those pods die
-> clients do not reconnect
-> active_connections drops permanently
```

### Kubernetes Termination Detail

When Kubernetes terminates a pod:

```text
SIGTERM sent
-> pod enters Terminating
-> Service stops sending new traffic to that pod
-> existing connections may remain briefly
-> after terminationGracePeriodSeconds, SIGKILL ends the process
```

Default grace period is often 30 seconds. This creates a short "termination limbo": the pod is doomed but connections may not disappear instantly.

### Result

The paper reports a permanent staircase pattern, approximately:

```text
800 -> 744 -> 697 -> 445 -> 79
```

The exact step sizes depend on how connections were distributed across pods. The important point is that the drops are synchronized with HPA removing pods.

### What This Teaches

CPU-based HPA can be actively dangerous for idle persistent connections. Low CPU does not mean "safe to remove pods."

### Experiment B3 Quiz

Questions:

1. Why do idle connections fool CPU-based HPA?
2. Why are clients configured not to reconnect in B3?
3. What does the staircase shape prove?
4. Why does the 30-second termination grace period matter?

Answers:

1. They are live sessions but produce little or no CPU.
2. To make every HPA-caused disconnection permanently visible in the graph.
3. Each scale-down step kills the connections that were on removed pods.
4. It explains why connection drops can appear shortly after the replica change, not at the exact same second.

---

## Experiment C: StatefulAutoscaler Solution

Experiment C is the main solution experiment.

### Prerequisites

Know:

- Kubernetes controller pattern: observe, compare, act.
- CRD meaning.
- Prometheus query basics.
- Ceiling division.
- Cooldown/hysteresis idea.

### What Is the StatefulAutoscaler?

It is a custom Kubernetes controller in `controller/`.

It watches custom objects of kind `StatefulAutoscaler`. The CRD spec includes:

- target Deployment,
- min replicas,
- max replicas,
- target connections per pod,
- max scale-up step,
- max scale-down step,
- scale-up cooldown seconds,
- scale-down cooldown seconds,
- drain policy fields.

Code locations:

```text
controller/api/v1alpha1/statefulautoscaler_types.go
controller/internal/controller/statefulautoscaler_controller.go
controller/internal/controller/prometheus.go
```

### How the Controller Thinks

The current controller asks Prometheus:

```text
sum(active_connections)
```

Then it computes:

```text
rawDesired = ceil(totalConnections / targetConnectionsPerPod)
```

Example:

```text
totalConnections = 800
targetConnectionsPerPod = 100

rawDesired = ceil(800 / 100) = 8 pods
```

Then it clamps the result:

```text
if rawDesired < minReplicas -> use minReplicas
if rawDesired > maxReplicas -> use maxReplicas
```

Then it applies scale-down stabilization. It remembers recent desired replica values during the cooldown window and uses the highest recent value.

Plain English:

```text
"If we recently needed 8 pods, do not immediately drop to 2 just because the metric briefly fell."
```

Then it applies step limits:

```text
do not add more than maxScaleUpStep pods in one reconcile
do not remove more than maxScaleDownStep pods in one reconcile
```

### Important Code Truth

The CRD has drain fields, and the WebSocket app exposes `/drain`. But the current reconcile code primarily computes desired replicas and patches the Deployment. Do not assume a full controller-driven drain workflow exists unless you implement and verify it.

This distinction matters for MQTT. The MQTT implementation should expose `/drain`, but you must check whether it is actually used by lifecycle hooks, controller logic, or only future design notes.

### What Was Tested?

Experiment C uses the same general problem shape as B3 but changes the scaler:

```text
CPU-HPA is removed
StatefulAutoscaler is installed
controller scales on active_connections
CPU_WORK=0
```

The controller must work even when CPU carries no useful signal.

### Why This Experiment Exists

It proves the complete fix:

```text
right metric + cooldown memory + bounded scale-down
```

The experiment creates a 90-second gap between connection waves. HPA would treat the drop as a reason to remove pods. The StatefulAutoscaler should hold pods warm because the cooldown is 120 seconds.

### Result

Current multi-run result summary from `paper.md`:

- valid Experiment C runs: runs 2-5,
- run 1 excluded from primary aggregate due to a log concatenation artifact,
- pod_seconds: about 4280 +/- 67,
- scale-up reaction: about 22 +/- 6 seconds,
- scale-down reaction: about 119 +/- 2 seconds,
- peak replicas: about 9-10,
- no connection loss observed in controlled valid runs.

Careful wording: say "no connection loss was observed in these controlled runs." Do not claim the system can never lose a connection in production.

### Why Experiment C Wins

During the 90-second gap:

```text
connections temporarily drop
-> controller remembers recent high demand
-> cooldown has not expired
-> replicas stay warm
-> next wave reconnects without waiting for pods to be recreated
```

This is the main idea of connection-context stabilization.

### Experiment C Quiz

Questions:

1. What metric does the current controller query?
2. What formula turns connection count into replica count?
3. Why is scale-down cooldown needed?
4. What does `maxScaleDownStep` protect against?
5. Why is `CPU_WORK=0` important in Experiment C?

Answers:

1. `sum(active_connections)` from Prometheus.
2. `ceil(totalConnections / targetConnectionsPerPod)`, then clamp to min/max.
3. It prevents brief connection drops from immediately destroying warm capacity.
4. It prevents removing too many pods in one reconciliation cycle.
5. It proves the controller is not secretly depending on CPU.

---

## Experiment D: HPA with Custom Connection Metric

### Prerequisites

Know:

- HPA can use custom metrics.
- Prometheus Adapter can expose app metrics to HPA.
- Correct metric and correct lifecycle policy are different things.

### What Was Tested?

Instead of CPU, vanilla HPA is pointed at `active_connections`.

This asks:

```text
Do we really need a custom controller?
Or can HPA with the right metric solve the problem?
```

### Why This Experiment Exists

This is the strongest reviewer objection. If the paper only compares CPU-HPA to the custom controller, the custom controller is fighting a weak baseline.

Experiment D makes the baseline stronger.

### Result

Current multi-run summary from `paper.md`:

- 5 runs collected,
- peak replicas: 8 in all runs,
- pod_seconds: about 3934 +/- 148,
- scale-up reaction: about 54 +/- 29 seconds, median 48,
- scale-down reaction: about 313 +/- 96 seconds, median 355,
- run 3 has an anomalous slow scale-up around 104 seconds and should be investigated/disclosed.

### Interpretation

HPA with the right metric does much better at choosing the right replica count. It reaches 8 pods for the 800-connection case.

But metric choice alone is not the whole solution.

The custom controller's advantage is the connection-context cooldown behavior. It can hold pods warm across a short zero-connection gap. HPA with a custom metric still uses HPA's stabilization semantics, and those semantics are not the same as STAR's sliding high-water behavior.

### Important Trade-Off

Experiment D can have lower pod_seconds than Experiment C because it removes pods earlier. Lower pod_seconds is not automatically better. If pods are removed during a gap and then recreated for the next cycle, users may wait or reconnect into cold capacity.

### Experiment D Quiz

Questions:

1. Why is Experiment D a stronger baseline than CPU-HPA?
2. What did Experiment D get right?
3. What did it not fully solve?
4. Why can lower pod_seconds be misleading?

Answers:

1. It gives HPA the same basic connection metric the custom controller uses.
2. It chose the correct peak replica count in the collected runs.
3. It did not fully reproduce STAR's connection-context stabilization behavior.
4. It may mean pods were removed too early, saving resources while hurting readiness for the next connection wave.

---

## Experiment E: KEDA Baseline

### Prerequisites

Know:

- KEDA means Kubernetes Event-Driven Autoscaling.
- KEDA can scale based on external systems like Prometheus.
- KEDA usually manages or wraps an HPA rather than directly replacing all HPA behavior.

### What Was Tested?

KEDA is configured with a Prometheus trigger on:

```text
sum(active_connections)
```

and a cooldown period similar to STAR's:

```text
cooldownPeriod = 120 seconds
```

### Why This Experiment Exists

KEDA is a real production tool. A reviewer will ask:

```text
Why not use KEDA?
```

The paper must answer by testing KEDA directly.

### Current Status

Experiment E now has **5 completed runs** with processed results. All 5 runs are stored in `results/raw/websocket/multi/experiment-e-keda/run_{1..5}/` with parsed CSVs and plots in `results/processed/websocket/multi/experiment-e-keda/`.

From the 5 runs:

- pod_seconds: approximately 4145 (range: 4138-4148)
- scale-up reaction: approximately 26s (range: 22-33s)
- peak replicas: 8 in all 5 runs
- peak connections: 800 in all 5 runs
- scale-down reaction: not captured in current parser (KEDA manages through HPA)

From `md/KEDA.md`, the observed behavior is:

- KEDA can hold pods through a short drop when cooldown is active.
- After cooldown expires, KEDA lowers its floor and HPA performs scale-down.
- KEDA does not expose the same `maxScaleDownStep` behavior as the custom controller.
- Because KEDA works through HPA, it has different control semantics.

### Interpretation

KEDA is a serious baseline, not a strawman. The custom controller should be framed carefully:

```text
not "Kubernetes cannot do this"
but "this controller gives explicit connection-aware cooldown and bounded scale-down semantics for this workload"
```

Note: An aggregate summary CSV for Experiment E has not been generated yet. The data exists in per-run summaries and should be aggregated using the same `multi_run_stats.py` pattern used for Experiments C and D.

### Experiment E Quiz

Questions:

1. Why must the paper compare against KEDA?
2. What does `cooldownPeriod` help with?
3. Why is KEDA not identical to StatefulAutoscaler?
4. How many Experiment E runs exist now?

Answers:

1. KEDA is the known production-grade tool for event-driven autoscaling.
2. It delays scale-down after the metric falls, protecting short gaps.
3. KEDA manages scaling through HPA and does not expose the same direct replica patching and step limits.
4. Five runs exist with processed results, matching the statistical depth of C and D.

---

## Failure Mode Experiments

### Prerequisites

Know:

- A system should be tested outside the happy path.
- Prometheus can be stale or unavailable.
- Kubernetes pod scheduling takes time.
- Safe failure often means "do not scale down when unsure."

### Why Failure Scenarios Exist

A convincing systems project does not only show success. It also asks:

```text
Where does this design struggle?
What happens when dependencies are slow or unavailable?
Does it fail safely?
```

### Current Status

All three failure scenarios are **fully tested** with results. Raw data is in `results/raw/websocket/failure-{1,2,3}-*/`, and processed CSVs, per-scenario plots, and a comparative plot are in `results/processed/websocket/`.

The run script is `scripts/run-failure-scenarios.sh` which runs all three scenarios sequentially on a single Kind cluster, then automatically parses and plots results.

### Failure 1: Metric Staleness

What:

- Prometheus scrape interval is increased to 60 seconds.

Why:

- To test whether stale connection data causes bad scale decisions.

Result (from processed data):

- pod_seconds: 4470
- scale-up reaction: 25s
- scale-down reaction: 112s
- peak connections: 1082 (overshoot due to stale metrics)
- peak replicas: 11
- Degraded reaction time, but the controller still functioned. Stale metrics caused overshoot because the controller briefly saw outdated connection counts.
- Safe only if cooldown is longer than the scrape staleness risk.

### Failure 2: Instant Spike

What:

- All 800 clients arrive simultaneously instead of ramping slowly (RAMP_UP_DURATION=0).

Why:

- To test whether the controller can react faster than Kubernetes can schedule pods.

Result (from processed data):

- pod_seconds: 4374
- scale-up reaction: 6s (fastest of all scenarios — instant demand triggers immediate scaling)
- scale-down reaction: 359s
- peak connections: 800 (no overshoot — clean client behavior)
- peak replicas: 8
- The bottleneck becomes Kubernetes scheduling and pod readiness, around tens of seconds.
- This is an infrastructure limit, not only a scaler formula problem.

### Failure 3: Prometheus Outage

What:

- Prometheus pod is killed at t=75s into CYCLE_1, unavailable for 120s.

Why:

- The controller depends on Prometheus for connection count.

Result (from processed data):

- pod_seconds: 4667 (highest — controller held pods conservatively during outage)
- scale-up reaction: 21s
- scale-down reaction: 118s
- peak connections: 921 (some overshoot during recovery)
- peak replicas: 10
- The controller correctly avoided unsafe scale-down during the outage. Query errors caused a requeue without patching replicas.
- Be careful: an empty successful Prometheus result is parsed as 0 in `prometheus.go`, so metric shape and scrape configuration still matter.

### Comparative Results

A side-by-side comparison plot exists at `results/processed/websocket/failure-comparative.png` and `results/processed/websocket/failure_comparative.png`.

| Scenario | pod_seconds | Scale-up (s) | Scale-down (s) | Peak conns | Peak replicas |
|---|---|---|---|---|---|
| Metric Staleness | 4470 | 25 | 112 | 1082 | 11 |
| Instant Spike | 4374 | 6 | 359 | 800 | 8 |
| Prometheus Outage | 4667 | 21 | 118 | 921 | 10 |

Key takeaway: the STAR controller degrades gracefully in all tested adversarial cases. The worst outcome is temporary over-provisioning (higher pod_seconds), not connection loss.

### Failure Mode Quiz

Questions:

1. Why is stale data dangerous for autoscaling?
2. Why can an instant spike still hurt even with a correct scaler?
3. What should a controller do if Prometheus is unreachable?
4. Why is "empty metric result" different from "query error"?
5. Which failure scenario had the fastest scale-up reaction and why?

Answers:

1. The scaler may act on old reality rather than current load.
2. New pods need scheduling and startup time; no scaler can make pods ready instantly.
3. It should avoid unsafe scale-down and retry later.
4. A query error clearly means unknown; an empty result may be interpreted as zero unless the code handles it separately.
5. Instant spike (6s) because all 800 clients arrive at once, creating immediate demand signal.

---

## Evidence Chain Summary

Use this when explaining the project to someone else:

```text
Phase 0 KRM:
  CPU-HPA works for stateless HTTP, but metric freshness causes lag.

Phase 0 PCM:
  Prometheus custom metrics can improve stateless HTTP scaling.

Experiment A:
  HPA can scale WebSockets when CPU is artificially tied to connections.

Experiment B1:
  Cyclic active/idle behavior causes HPA to over-provision and recover slowly.

Experiment B2:
  When HPA scales down, it kills live connections and causes reconnection storms.

Experiment B3:
  Idle clients that do not reconnect are permanently destroyed by CPU-based scale-down.

Experiment C:
  StatefulAutoscaler scales on active connections and holds pods through short drops.
  5 runs completed. No connection loss observed in controlled valid runs.

Experiment D:
  HPA with connection metric gets replica count right but metric alone is not full lifecycle policy.
  5 runs completed.

Experiment E:
  KEDA is a strong comparison. 5 runs completed.
  pod_seconds ~4145, scale-up ~26s, peak replicas 8.
  KEDA works well but has different control semantics than STAR.

Failure scenarios:
  All 3 tested (metric staleness, instant spike, Prometheus outage).
  STAR degrades gracefully: worst outcome is over-provisioning, not connection loss.
  Results include per-scenario summaries and comparative plots.

MQTT:
  Implementation complete (broker, load gen, k8s manifests, experiment scripts, analysis).
  Experiments not yet run. Results pending.
```

---

## Key Numbers to Remember

These are the headline numbers from current project notes and actual results in the repo. Always check source files before paper submission.

| Topic | Number |
|---|---|
| B1 over-provisioning headline | 15 pods vs about 8 needed, 87.5% over |
| B2 peak reconnection storm | about 1,400 connections/second |
| B2 active connection overshoot | up to 1,215 for 800 target clients |
| B3 permanent loss pattern | about 800 -> 79 remaining |
| C valid runs | runs 2-5, run 1 excluded for log artifact |
| C scale-down reaction | about 119 +/- 2 seconds |
| C pod_seconds | about 4280 +/- 67 |
| C observed connection loss | none observed in controlled valid runs |
| D runs | 5 runs |
| D peak replicas | 8 in all runs |
| D pod_seconds | about 3934 +/- 148 |
| E runs | 5 runs completed |
| E pod_seconds | about 4145 (range 4138-4148) |
| E scale-up reaction | about 26s (range 22-33s) |
| E peak replicas | 8 in all 5 runs |
| E aggregate summary | not yet generated (per-run summaries exist) |
| Failure 1 (metric staleness) | pod_seconds 4470, scale-up 25s, peak conns 1082, peak replicas 11 |
| Failure 2 (instant spike) | pod_seconds 4374, scale-up 6s, peak conns 800, peak replicas 8 |
| Failure 3 (Prometheus outage) | pod_seconds 4667, scale-up 21s, peak conns 921, peak replicas 10 |

---

## How to Read the Controller Code

Start with the CRD type:

```text
controller/api/v1alpha1/statefulautoscaler_types.go
```

Understand these fields:

```text
targetRef
minReplicas
maxReplicas
targetConnectionsPerPod
maxScaleUpStep
maxScaleDownStep
scaleUpCooldownSeconds
scaleDownCooldownSeconds
drain
```

Then read:

```text
controller/internal/controller/prometheus.go
```

It hard-codes the Prometheus query:

```text
sum(active_connections)
```

This is why MQTT can reuse the controller if the broker exposes the same metric name.

Then read:

```text
controller/internal/controller/statefulautoscaler_controller.go
```

Follow this flow:

```text
load StatefulAutoscaler object
-> load target Deployment
-> read current replicas
-> query Prometheus for total connections
-> compute raw desired replicas
-> clamp to min/max
-> stabilize scale-down using recent desired history
-> apply max scale-up/down step limits
-> patch Deployment replicas if needed
-> requeue
```

Controller quiz:

1. Where is the Prometheus query defined?
2. Why is the query protocol-agnostic?
3. What happens when `targetConnectionsPerPod` is zero?
4. What data structure stores stabilization history?

Answers:

1. `controller/internal/controller/prometheus.go`.
2. It only asks for `active_connections`, not WebSocket-specific data.
3. The controller requeues without scaling.
4. A package-level map named `scaleDownHistory`, protected by a mutex.

---

## Phase 2: MQTT Generalisation Project

The MQTT implementation is **complete**. All code, manifests, experiment scripts, analysis scripts, and wrapper scripts have been created. The experiments have not yet been run, so results and plots are pending.

The WebSocket work proves the idea for one protocol. MQTT is how the project shows the idea generalises to another important persistent-connection protocol.

### Prerequisites for MQTT Work

Before coding, the intern should understand:

- MQTT broker/client/topic model.
- Difference between publish and subscribe.
- Keepalive and idle sessions.
- QoS basics.
- Python async basics.
- Kubernetes Deployment and Service YAML.
- Prometheus metrics exposition.
- Existing WebSocket server pattern.
- Existing STAR controller query: `sum(active_connections)`.

### What MQTT Must Prove

Research questions from `md/mqtt-experiment-plan.md`:

| ID | Question |
|---|---|
| RQ-M1 | Does HPA disrupt active MQTT sessions when it scales down? |
| RQ-M2 | Does STAR scale MQTT brokers proportionally to active connections without disrupting sessions? |
| RQ-M3 | Does HPA mishandle idle MQTT clients while STAR holds appropriate capacity? |

### Why Mosquitto Is Not Enough

The original MQTT deployment used `eclipse-mosquitto:2`, which does not expose `/metrics` or `/drain`. A custom Python broker using `amqtt` has been implemented instead.

Implemented files:

```text
workloads/mqtt/app/broker.py           ← custom Python MQTT broker
workloads/mqtt/app/requirements.txt
workloads/mqtt/app/Dockerfile
workloads/mqtt/k8s/deployment.yml      ← updated to use custom broker
workloads/mqtt/k8s/service.yml
workloads/mqtt/k8s/hpa.yml
load-generator/mqtt-client/client.py   ← persistent MQTT load generator
load-generator/mqtt-client/requirements.txt
load-generator/mqtt-client/Dockerfile
```

Mosquitto is a real broker, but for this experiment we need:

- `active_connections` metric on `/metrics`,
- optional `new_connections_total`,
- HTTP `/drain`,
- behavior that mirrors the WebSocket experiment server.

The plan proposes a custom Python broker using `amqtt` plus an HTTP server, similar to the WebSocket server.

### MQTT Architecture

Target architecture:

```text
mqtt-loadgen Job
-> mqtt-service
-> mqtt-broker pods on port 1883
-> broker exposes /metrics on port 8080
-> Prometheus scrapes active_connections
-> STAR controller queries sum(active_connections)
-> controller scales mqtt-broker Deployment
```

### MQTT Component 1: Custom Broker

Create:

```text
workloads/mqtt/app/broker.py
workloads/mqtt/app/requirements.txt
workloads/mqtt/app/Dockerfile
```

Broker requirements:

- listen on MQTT port 1883,
- expose HTTP `/metrics` on port 8080,
- expose HTTP `/drain` on port 8080,
- count active MQTT connections,
- ideally count total new connections,
- reject new clients when draining,
- keep metric name exactly `active_connections`.

Acceptance test:

```text
run broker locally or in a pod
curl /metrics
see: active_connections 0
connect one MQTT client
curl /metrics
see: active_connections 1
disconnect client
curl /metrics
see: active_connections 0
```

### MQTT Component 2: Load Generator

Create:

```text
load-generator/mqtt-client/client.py
load-generator/mqtt-client/requirements.txt
load-generator/mqtt-client/Dockerfile
```

Load generator requirements:

- create N MQTT clients,
- ramp over configurable seconds,
- subscribe to unique topics,
- optionally publish tiny ping messages,
- support idle mode with long `PING_INTERVAL`,
- log connects and disconnects,
- keep running long enough for HPA/STAR decisions.

Useful environment variables:

```text
BROKER_HOST
BROKER_PORT
CLIENTS
RAMP_SECONDS
PING_INTERVAL
DURATION
```

Acceptance test:

```text
start broker
run load generator with CLIENTS=10
broker /metrics should reach about 10 active connections
load generator logs should show stable connected clients
```

### MQTT Component 3: Kubernetes Manifests

Update or create:

```text
workloads/mqtt/k8s/deployment.yml
workloads/mqtt/k8s/service.yml
workloads/mqtt/k8s/hpa.yml
```

Deployment must:

- use custom `mqtt-broker:latest` image,
- expose container ports 1883 and 8080,
- add Prometheus scrape annotations,
- set resource requests and limits,
- consider `terminationGracePeriodSeconds`,
- optionally add a `preStop` hook that calls `/drain`.

Service must:

- expose MQTT port 1883,
- expose metrics port 8080 if needed for debugging.

HPA must:

- target `mqtt-broker`,
- use CPU for MQTT-A and MQTT-C HPA side,
- set clear min/max replicas.

### MQTT Component 4: Prometheus

The current Prometheus config already scrapes annotated pods generally:

```text
monitoring/prometheus/configmap.yaml
```

The intern should verify MQTT broker pods have the correct annotations so the existing `kubernetes-pods` job discovers them.

Acceptance test:

```text
port-forward Prometheus
query sum(active_connections)
confirm MQTT broker metric appears
```

### MQTT Component 5: StatefulAutoscaler CR

Create:

```text
experiments/mqtt/experiment-b-stateful/statefulautoscaler.yaml
experiments/mqtt/experiment-c-idle-connections/statefulautoscaler.yaml
```

Example settings:

```yaml
targetConnectionsPerPod: 150
minReplicas: 1
maxReplicas: 5
maxScaleUpStep: 2
maxScaleDownStep: 1
scaleDownCooldownSeconds: 120
```

Why no controller code change should be needed:

```text
controller asks Prometheus for sum(active_connections)
MQTT broker exposes active_connections
therefore the controller does not care whether the protocol is WebSocket or MQTT
```

Only change controller code if you intentionally parameterize the metric query or implement drain behavior.

### MQTT Component 6: Experiments

Create:

```text
experiments/mqtt/experiment-a-hpa-baseline/
experiments/mqtt/experiment-b-stateful/
experiments/mqtt/experiment-c-idle-connections/
```

Each experiment directory should have:

```text
README.md
config.env
run.sh
scaler-specific YAML if needed
```

Also create wrappers:

```text
scripts/run-experiment-mqtt-a.sh
scripts/run-experiment-mqtt-b.sh
scripts/run-experiment-mqtt-c.sh
```

### MQTT-A: HPA Baseline

What:

- Run MQTT broker with CPU-based HPA.
- Connect many mostly idle MQTT clients.
- Observe HPA behavior.

Why:

- To reproduce the core failure with MQTT.

Expected:

- CPU remains low.
- HPA may scale down.
- Clients on removed pods disconnect.
- `active_connections` drops.

What to collect:

- pods over time,
- HPA status,
- CPU,
- `active_connections`,
- load generator logs.

Quiz:

1. Why should idle MQTT clients produce little CPU?
2. What should HPA incorrectly conclude?
3. What metric proves sessions were lost?

Answers:

1. They mostly keep a TCP session alive without frequent messages.
2. That extra pods are unnecessary.
3. A drop in `active_connections` correlated with replica scale-down.

### MQTT-B: STAR Controller

What:

- Run MQTT broker under `StatefulAutoscaler`.
- Connect many clients.
- Reduce clients later.
- Observe controlled scale-up and scale-down.

Why:

- To prove STAR works for MQTT using the same `active_connections` abstraction.

Expected:

- Replica count follows connection count.
- Scale-down waits for cooldown.
- No sudden connection drop during protected periods.

What to collect:

- pods over time,
- StatefulAutoscaler status,
- CPU,
- `active_connections`,
- broker logs,
- load generator logs.

Quiz:

1. Why does STAR not need MQTT-specific controller logic?
2. What should `ceil(600 / 150)` equal?
3. Why keep `maxScaleDownStep` small?

Answers:

1. It only needs the shared metric `active_connections`.
2. 4 replicas.
3. To avoid terminating too many broker pods at once.

### MQTT-C: Idle HPA vs STAR Side-by-Side

What:

- Run the same idle MQTT workload twice:
  - once with CPU-HPA,
  - once with STAR.

Why:

- To make the comparison obvious and reviewer-friendly.

Expected:

- HPA side: low CPU causes unsafe scale-down.
- STAR side: active connections keep enough replicas alive.

What to collect:

- side-by-side replica and connection timelines,
- disconnection logs,
- pod_seconds,
- connection loss count.

Quiz:

1. Why is MQTT-C the strongest MQTT experiment?
2. Why should `PING_INTERVAL=120` be useful?
3. What would show STAR winning?

Answers:

1. It directly compares HPA and STAR on the same idle IoT-style workload.
2. It simulates sleeping devices that keep sessions but rarely publish.
3. STAR preserves active connections while HPA loses them or scales unsafely.

### MQTT Component 7: Analysis

Create:

```text
analysis/mqtt/parse_logs_mqtt.py
analysis/mqtt/plot_experiment_mqtt.py
```

Parser should produce CSVs like:

```text
time_s, replicas, active_connections, cpu
```

Plotter should produce:

- active connections over time,
- replicas over time,
- combined plot,
- HPA vs STAR side-by-side plot for MQTT-C.

Acceptance criteria:

- Plots clearly show when load starts and stops.
- Replica changes are readable.
- Connection drops are visible.
- Summary metrics are written to a CSV.

### MQTT Final Implementation Checklist

```text
[x] Read `md/mqtt-experiment-plan.md` fully.
[x] Read WebSocket instrumented server.
[x] Implement custom MQTT broker.
[x] Add broker Dockerfile and requirements.
[x] Implement MQTT load generator.
[x] Add load generator Dockerfile and requirements.
[x] Update MQTT Deployment and Service.
[x] Add MQTT HPA YAML.
[ ] Verify Prometheus can scrape MQTT `/metrics`.
[x] Add StatefulAutoscaler CR for MQTT.
[x] Create MQTT-A experiment folder and run script.
[x] Create MQTT-B experiment folder and run script.
[x] Create MQTT-C experiment folder and run script.
[x] Add wrapper scripts in `scripts/`.
[x] Add MQTT parser and plotter.
[ ] Run a 10-client smoke test.
[ ] Run MQTT-A.
[ ] Run MQTT-B.
[ ] Run MQTT-C.
[ ] Generate plots.
[ ] Summarize results in `md/` and update paper plan.
```

Next steps guide: `md/mqtt-next-steps.md`

### MQTT "Done" Definition

MQTT work is done when:

- broker exposes correct metrics,
- load generator can hold hundreds of clients,
- Prometheus query `sum(active_connections)` works,
- STAR controller scales MQTT broker without controller code changes,
- MQTT-A shows HPA failure or clearly explains if the failure does not appear,
- MQTT-B shows STAR behavior,
- MQTT-C compares HPA and STAR on idle clients,
- raw logs and processed plots are saved,
- the intern can explain every result without reading from notes.

---

## Common Mistakes to Avoid

### Mistake 1: Thinking CPU Low Means No Users

For this project, CPU low often means users are idle, not absent.

### Mistake 2: Calling All Connection Loss "Network Noise"

Check whether drops align with HPA scale-down. If yes, they are likely caused by pod termination.

### Mistake 3: Treating Pod-Seconds as Always Good When Lower

Lower pod_seconds can mean efficiency, but it can also mean capacity was removed too early.

### Mistake 4: Saying "Zero Connection Loss" Too Broadly

Use careful language:

```text
No connection loss was observed in controlled valid runs.
```

Do not say:

```text
The system guarantees zero connection loss.
```

### Mistake 5: Forgetting KEDA

Always mention KEDA as a serious baseline. The paper's contribution is stronger when it honestly compares against it.

### Mistake 6: Assuming Drain Is Fully Implemented

Check code. The API has drain fields and workloads can expose `/drain`, but current controller behavior must be verified before claiming drain-based graceful migration.

---

## Final Master Quiz

Questions:

1. What is the core mismatch between HPA and persistent connections?
2. Why does Experiment A matter if later experiments show HPA failing?
3. What is the main failure in B1?
4. What metric measures reconnection storm rate?
5. Why is B3 more severe than B2?
6. What two things make StatefulAutoscaler different from "just use connection count"?
7. Why is Experiment D important?
8. Why is KEDA important?
9. What must MQTT expose for the existing controller to work?
10. What should the controller do when Prometheus is unreachable?

Answers:

1. HPA usually watches CPU, but persistent connections can be alive while using little CPU.
2. It proves the setup and HPA can work when CPU is a valid signal.
3. HPA over-provisions because short LOW phases cannot complete the scale-down stabilization window.
4. `rate(new_connections_total[15s])`.
5. B3 shows permanent session destruction when clients do not reconnect.
6. It uses active connection count and connection-context scale-down stabilization with bounded scale-down steps.
7. It tests whether HPA with the correct metric is enough.
8. KEDA is the production-grade event-driven scaler reviewers will compare against.
9. A Prometheus metric named `active_connections`, ideally plus `/metrics` and `/drain`.
10. Avoid unsafe scale-down, keep existing replicas, and retry later.

---

## Glossary

| Term | Meaning |
|---|---|
| Active connection | A currently open WebSocket or MQTT session |
| Autoscaler | A system that changes replica count automatically |
| Cooldown | A wait period before allowing scale-down |
| CRD | Kubernetes extension that adds a new resource type |
| Gauge | Metric that can go up and down |
| Counter | Metric that only goes up |
| HPA | Kubernetes Horizontal Pod Autoscaler |
| KEDA | Kubernetes Event-Driven Autoscaling |
| KRM | Kubernetes Resource Metrics |
| PCM | Prometheus Custom Metrics |
| Pod-seconds | Replica count multiplied by time, used as cost proxy |
| Prometheus | Metrics collection and query system |
| Reconcile loop | Controller cycle: observe, compare, act |
| Reconnection storm | Many clients reconnecting at the same time |
| Scale-down stabilization | HPA delay before removing pods |
| Stateful workload | Workload with important per-client/session state |
| Stateless workload | Workload where each request can be handled independently |
| STAR | This project's StatefulAutoscaler research system |
| WebSocket | Long-lived bidirectional connection protocol |
| MQTT | Publish/subscribe protocol used heavily by IoT systems |

---

## What the Intern Should Be Able to Do at the End

You are ready for the MQTT implementation when you can explain:

- why CPU is not enough,
- why Phase 0 was still necessary,
- how B1, B2, and B3 build the failure argument,
- how the custom controller computes replicas,
- why cooldown matters,
- why HPA custom metrics and KEDA are important baselines,
- what MQTT shares with WebSocket,
- exactly what files must be created for MQTT,
- how to prove the MQTT implementation works using metrics and plots.

Then implement the MQTT part yourself. Use the WebSocket work as the pattern, but do not blindly copy. For each file you create, be able to answer:

```text
What does this file do?
Why does the experiment need it?
How will I know it works?
```
