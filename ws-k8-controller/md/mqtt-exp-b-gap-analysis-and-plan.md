# MQTT Experiment B: Proof Plan for Showing `StatefulAutoscaler` Is the Answer

## Short Answer

**No.** The current version of this markdown is honest, but it is **not yet doing the job you need**.

It is mainly a **gap analysis**. That is useful for correctness, but it does not yet give you a **proof strategy** for arguing:

> `StatefulAutoscaler` is the solution to the three MQTT failures shown in Experiment A.

To make that argument well, the plan needs to do two things at the same time:

1. stay truthful about what the current MQTT-B run actually proves
2. define a revised Experiment B that can produce the exact evidence needed to support the solution claim

This document is that revised plan.

---

## The Key Framing Change

If we keep the claim too broad, MQTT-B will keep looking like a failure.

The current broad claim is:

> `StatefulAutoscaler` solves all three MQTT issues by itself.

That is too broad because **plain autoscaling cannot transparently migrate already-open TCP/MQTT sessions**.

So the correct and defensible claim boundary should be:

> `StatefulAutoscaler` is the correct solution to the autoscaling and lifecycle problem for MQTT:
> it chooses the right scaling signal, scales to the right replica count, avoids destructive scale-down, and enables safe rebalancing through drain-and-reconnect semantics.
> It is not a transparent TCP session migration system.

That framing still lets you argue it is the answer, but it makes clear **what problem it answers**.

---

## What the Current MQTT-B Run Proves

The current run already gives you three useful facts:

### 1. It proves CPU is the wrong signal

- HPA in Exp A reacts too late because CPU does not track persistent MQTT session pressure.
- In MQTT-B, `StatefulAutoscaler` scales from **1 -> 2 at `t=31s`** and **2 -> 3 at `t=46s`** based on `active_connections` in [results/raw/mqtt/experiment-b-stateful/out.csv](/home/aditya/cohort/startproj/ws-k8-controller/results/raw/mqtt/experiment-b-stateful/out.csv:23).

This is strong evidence for:

> connection count is the correct scaling signal for MQTT, not CPU.

### 2. It proves the controller computes the correct replica target

- Peak observed connections are **339**
- Target is **150 connections/pod**
- So the mathematically correct replica count is `ceil(339 / 150) = 3`
- The controller reaches **3 replicas** in [results/raw/mqtt/experiment-b-stateful/out.csv](/home/aditya/cohort/startproj/ws-k8-controller/results/raw/mqtt/experiment-b-stateful/out.csv:32)

This is strong evidence for:

> `StatefulAutoscaler` makes the right scaling decision once the connection signal is visible.

### 3. It proves scale-down was not triggered while connections were still present

- Connections remain at **339** through most of the run
- Replicas stay at **3**
- Replicas do not begin reducing until long after connections hit **0** in [results/raw/mqtt/experiment-b-stateful/out.csv](/home/aditya/cohort/startproj/ws-k8-controller/results/raw/mqtt/experiment-b-stateful/out.csv:359)

This is partial evidence for:

> the controller does not aggressively collapse replicas while connection state exists.

---

## What the Current MQTT-B Run Does Not Yet Prove

These are the gaps that prevent the current MQTT-B result from fully carrying the argument.

### 1. Failure 1 is not fully closed

The load generator still plateaus at **339/1000 connected** in [results/raw/mqtt/experiment-b-stateful/loadgen-phase1.log](/home/aditya/cohort/startproj/ws-k8-controller/results/raw/mqtt/experiment-b-stateful/loadgen-phase1.log:5).

So right now you can only say:

> `StatefulAutoscaler` reacts earlier than HPA.

You cannot yet say:

> `StatefulAutoscaler` prevented admission failure during the burst.

### 2. Failure 2 is currently framed in an impossible way

The per-pod distribution stays **339 / 0 / 0** in [results/raw/mqtt/experiment-b-stateful/out_perpod.csv](/home/aditya/cohort/startproj/ws-k8-controller/results/raw/mqtt/experiment-b-stateful/out_perpod.csv:9).

If Failure 2 means:

> “after scale-up, old connections should magically move to the new pods”

then neither HPA nor `StatefulAutoscaler` can prove that, because Kubernetes Services do not migrate existing TCP sockets.

So Failure 2 must be reframed from:

- “automatic redistribution”

to:

- “safe rebalance policy for persistent connections”

That is something `StatefulAutoscaler` can actually solve.

### 3. Failure 3 is not yet tested under live-client scale-down

The experiment script deletes the load generator first in [experiments/mqtt/experiment-b-stateful/run.sh](/home/aditya/cohort/startproj/ws-k8-controller/experiments/mqtt/experiment-b-stateful/run.sh:127).

So the current run proves:

- clients went away
- then scale-down happened

It does **not** prove:

- a pod holding live clients was drained safely
- the controller prevented violent disconnect under active load

---

## Revised Claim: What We Need MQTT-B to Prove

To make the paper argument work, MQTT-B should be redesigned to prove these three solution statements:

| Exp A failure | What MQTT-B must prove | Strong claim you can make |
|---|---|---|
| Failure 1: Scale-up Blindness | Connection-count scaling reacts early enough to provision capacity for bursty MQTT load | `StatefulAutoscaler` solves the metric-selection problem that makes HPA blind to MQTT session pressure |
| Failure 2: No Connection-Aware Redistribution | The system can rebalance safely through drain + reconnect, even if it cannot transparently migrate sockets | `StatefulAutoscaler` solves the lack of connection-aware rebalance policy |
| Failure 3: Violent Disconnection | Scale-down happens through lifecycle-aware drain while clients are still live, with no cliff-drop loss | `StatefulAutoscaler` solves destructive scale-down |

This table is the real center of the argument.

---

## The Proof Strategy You Should Use

The cleanest argument is not:

> “MQTT-B made everything perfect instantly.”

The cleaner and more believable argument is:

> “Experiment A identifies three real failures caused by CPU-based, connection-unaware autoscaling. `StatefulAutoscaler` addresses those same three failure classes with three corresponding mechanisms:
> connection-count scaling for Failure 1,
> connection-aware drain/reconnect rebalance for Failure 2,
> and lifecycle-safe scale-down with cooldown plus drain for Failure 3.”

Then MQTT-B becomes the experiment that validates that mapping.

That is a much stronger paper structure because it ties:

- each failure
- to a controller mechanism
- to a measurable success criterion

---

## Revised Experiment B Design

The current Phase 1/2/3 design should be changed so each phase proves one specific solution property.

## Phase 1: Solve Failure 1

### Goal

Show that `StatefulAutoscaler` prevents the scale-up blindness that broke HPA.

### What to test

- same 1000-client burst
- controller scales on `active_connections`
- clients remain alive and retry while capacity comes online
- after replicas reach 3, the run script waits again so retrying clients have time to reconnect
- optionally use a slower ramp, for example `RAMP_SECONDS=120` instead of `60`, so fewer clients pile up in the initial storm

### What success looks like

- first scale-up happens much earlier than HPA
- connected clients continue increasing after new pods appear
- total successful clients are dramatically higher than 339
- ideally close to all 1000 by end of ramp/retry window

### What you can claim if it works

> Unlike CPU-HPA, `StatefulAutoscaler` observes the relevant workload state directly and provisions replicas in response to MQTT connection pressure, removing scale-up blindness.

### Required implementation changes

- update the run script to wait after the controller reaches 3 replicas, so retrying clients have time to reconnect
- keep the load generator alive during that post-scale-up wait
- alternatively, slow the burst with `RAMP_SECONDS=120` so the initial connection storm is less concentrated

---

## Phase 2: Solve Failure 2

### Goal

Show that `StatefulAutoscaler` provides a **connection-aware rebalance policy**, not transparent session migration.

### Important wording

Do **not** try to prove:

> existing sockets are redistributed automatically

Instead prove:

> overloaded pods can be drained and clients can reconnect onto the expanded replica set, producing gradual rebalance over time

### What to test

- keep clients alive
- scale up to multiple replicas
- mark one loaded pod as draining
- stop new traffic to that pod
- clients that disconnect or are gently nudged should reconnect through the Service onto non-draining pods

### What success looks like

- per-pod distribution moves from hot-spot concentration toward a more balanced state
- new connections avoid the draining pod
- cluster stays available during rebalance

### What you can claim if it works

> `StatefulAutoscaler` addresses the lack of connection-aware redistribution by providing a safe rebalance mechanism based on drain-and-reconnect semantics, which is the realistic control surface for long-lived MQTT/TCP sessions in Kubernetes.

### Required implementation changes

- controller must query per-pod connections, not only `sum(active_connections)` in [controller/internal/controller/prometheus.go](/home/aditya/cohort/startproj/ws-k8-controller/controller/internal/controller/prometheus.go:11)
- controller must orchestrate drain rather than only patching deployment replicas in [controller/internal/controller/statefulautoscaler_controller.go](/home/aditya/cohort/startproj/ws-k8-controller/controller/internal/controller/statefulautoscaler_controller.go:136)
- broker drain path in [workloads/mqtt/app/broker.py](/home/aditya/cohort/startproj/ws-k8-controller/workloads/mqtt/app/broker.py:160) must be extended beyond “reject new connections”
- draining pods should drop out of Service readiness

---

## Phase 3: Solve Failure 3

### Goal

Show that scale-down no longer causes violent live-session destruction.

### What to test

- keep clients connected
- trigger a legitimate scale-down condition while live sessions still exist
- controller drains one pod at a time
- monitor whether clients reconnect without a cliff drop

### What success looks like

- no instantaneous `639 -> 339` style cliff
- no single scale event destroys a whole pod’s sessions at once
- reconnects are spread over time
- replicas decrease only after drain conditions are satisfied

### What you can claim if it works

> `StatefulAutoscaler` replaces destructive replica removal with lifecycle-safe, connection-aware scale-down, eliminating the violent disconnection pattern observed in Experiment A.

### Required implementation changes

- controller-managed drain workflow
- per-pod drain state in CR status
- readiness false or endpoint removal for draining pods
- reconnect-capable clients

---

## Minimal Product Definition for “StatefulAutoscaler Is the Answer”

If your paper/thesis needs a simple sentence, this is the cleanest version:

> For MQTT workloads, `StatefulAutoscaler` is the correct autoscaling solution because it replaces CPU with connection count as the primary scaling signal and adds connection-aware scale-down semantics. Where persistent sessions must be rebalanced, it does so through controlled drain-and-reconnect rather than impossible transparent socket migration.

That sentence is both ambitious and defensible.

---

## Concrete Implementation Plan

## Step 1: Fix the experimental claim boundary

### Objective

Align the story with what can actually be proven.

### Actions

- replace “automatic redistribution” with “connection-aware rebalance”
- replace “all 3 fixed already” with “proof target for revised MQTT-B”
- update MQTT-B explainer and any paper text that implies transparent session migration

### Files

- [md/mqtt-exp-b-explainer.md](/home/aditya/cohort/startproj/ws-k8-controller/md/mqtt-exp-b-explainer.md:1)
- [Paper-Latex/paper.tex](/home/aditya/cohort/startproj/ws-k8-controller/Paper-Latex/paper.tex:838)
- [experiments/mqtt/experiment-b-stateful/README.md](/home/aditya/cohort/startproj/ws-k8-controller/experiments/mqtt/experiment-b-stateful/README.md:1)

---

## Step 2: Add the missing observability

### Objective

Make the controller and experiment measurable enough to support the proof.

### Actions

- query per-pod connections from Prometheus
- record drain state and selected victim pod
- log scale reason, drain start, drain completion, and scale execution
- add metrics for reconnect count, failed connects, and drain progress

### Files

- [controller/internal/controller/prometheus.go](/home/aditya/cohort/startproj/ws-k8-controller/controller/internal/controller/prometheus.go:1)
- [controller/internal/controller/statefulautoscaler_controller.go](/home/aditya/cohort/startproj/ws-k8-controller/controller/internal/controller/statefulautoscaler_controller.go:73)
- [controller/api/v1alpha1/statefulautoscaler_types.go](/home/aditya/cohort/startproj/ws-k8-controller/controller/api/v1alpha1/statefulautoscaler_types.go:1)
- [workloads/mqtt/app/broker.py](/home/aditya/cohort/startproj/ws-k8-controller/workloads/mqtt/app/broker.py:149)

### Acceptance criteria

- for any run, you can answer:
  - why did it scale?
  - which pod is draining?
  - how many connections did each pod hold?
  - when did reconnects happen?

---

## Step 3: Make Phase 1 winnable

### Objective

Turn earlier scale-up into successful admission.

### Actions

- document the 339-client ceiling as two timing effects working against each other:
  - clients fail during the initial connection storm
  - retry backoff such as `2s`, `4s`, `8s` pushes successful retries outside Phase 1's most critical window
- in the run script, add a second `kubectl wait` after replicas reach 3, then keep Phase 1 open while the retrying clients reconnect
- keep the load generator pod alive long enough for those retries to accumulate
- alternatively, use a slower ramp such as `RAMP_SECONDS=120` instead of `60` so fewer clients pile up at once

### Files

- [load-generator/mqtt-client/client.py](/home/aditya/cohort/startproj/ws-k8-controller/load-generator/mqtt-client/client.py:41)
- [experiments/mqtt/experiment-b-stateful/run.sh](/home/aditya/cohort/startproj/ws-k8-controller/experiments/mqtt/experiment-b-stateful/run.sh:84)
- [experiments/mqtt/experiment-b-stateful/statefulautoscaler.yaml](/home/aditya/cohort/startproj/ws-k8-controller/experiments/mqtt/experiment-b-stateful/statefulautoscaler.yaml:1)

### Acceptance criteria

- successful connections rise materially above 339
- retries make use of newly-ready pods
- the result is clearly stronger than HPA

---

## Step 4: Implement controller-managed drain

### Objective

Make drain a real control-loop behavior, not just a preStop side effect.

### Actions

- list deployment pods and fetch per-pod connection counts
- choose a drain candidate deliberately
- call `/drain` before scale-down
- annotate or label the draining pod
- hold scale-down until the pod empties or timeout occurs
- enforce `maxConcurrentDrains`

### Files

- [controller/internal/controller/statefulautoscaler_controller.go](/home/aditya/cohort/startproj/ws-k8-controller/controller/internal/controller/statefulautoscaler_controller.go:73)
- [workloads/mqtt/app/broker.py](/home/aditya/cohort/startproj/ws-k8-controller/workloads/mqtt/app/broker.py:160)
- [workloads/mqtt/k8s/deployment.yml](/home/aditya/cohort/startproj/ws-k8-controller/workloads/mqtt/k8s/deployment.yml:1)

### Acceptance criteria

- logs clearly show drain before scale-down
- draining pods stop receiving new traffic
- scale-down happens one lifecycle step at a time

---

## Step 5: Add reconnect-aware rebalance behavior

### Objective

Turn drain into actual rebalance for persistent MQTT sessions.

### Actions

- on disconnect, clients retry with jitter and resubscribe
- broker optionally nudges existing clients off draining pods gradually
- expose a metric showing live connections on draining pods over time

### Files

- [load-generator/mqtt-client/client.py](/home/aditya/cohort/startproj/ws-k8-controller/load-generator/mqtt-client/client.py:41)
- [workloads/mqtt/app/broker.py](/home/aditya/cohort/startproj/ws-k8-controller/workloads/mqtt/app/broker.py:51)

### Acceptance criteria

- hot-spot pod count decreases over time
- other pods pick up reconnected clients
- rebalance happens without a destructive cliff

---

## Step 6: Rewrite Experiment B around proof, not hope

### Objective

Make each phase correspond to one failure and one solution mechanism.

### New phase structure

1. **Phase 1: Admission under burst**
   prove scale-up blindness is removed
2. **Phase 2: Rebalance under live load**
   prove connection-aware rebalance policy
3. **Phase 3: Live-client scale-down**
   prove violent disconnection is eliminated

### Files

- [experiments/mqtt/experiment-b-stateful/run.sh](/home/aditya/cohort/startproj/ws-k8-controller/experiments/mqtt/experiment-b-stateful/run.sh:74)
- [analysis/mqtt/parse_logs_mqtt.py](/home/aditya/cohort/startproj/ws-k8-controller/analysis/mqtt/parse_logs_mqtt.py:1)
- [analysis/mqtt/plot_experiment_mqtt.py](/home/aditya/cohort/startproj/ws-k8-controller/analysis/mqtt/plot_experiment_mqtt.py:1)

### Acceptance criteria

- every phase has one question
- every question has one metric
- every metric maps to one failure from Exp A

---

## What You Can Say Right Now vs After the Revised Plan

### What you can say right now

> The current MQTT-B run shows that `StatefulAutoscaler` uses the right signal and computes the right replica count, but it does not yet fully prove that all three MQTT failure modes are solved end-to-end.

### What you should be able to say after this revised plan is implemented

> The revised MQTT-B experiment demonstrates that `StatefulAutoscaler` removes MQTT scale-up blindness, replaces connection-unaware redistribution with a safe drain-and-reconnect rebalance policy, and eliminates violent disconnection during scale-down.

That second sentence is the one you actually want.

---

## Final Recommendation

If your goal is to prove that MQTT Exp B shows `StatefulAutoscaler` is the answer, then the markdown should not stay as a generic gap-analysis note.

It should act as a **proof blueprint**:

- define the exact claim boundary
- redefine Failure 2 into something the controller can really solve
- specify the evidence needed for each failure
- drive code and experiment changes that produce that evidence

That is what this revised plan is designed to do.
