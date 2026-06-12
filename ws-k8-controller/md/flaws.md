# Paper Review

## Summary

This paper is about a problem with how Kubernetes handles scaling for WebSocket servers. The default Kubernetes scaler (called HPA — Horizontal Pod Autoscaler) looks at CPU usage to decide when to spin up or kill server pods. That works fine for regular HTTP APIs, but WebSocket servers are different: they keep thousands of long-lived TCP connections alive, and those connections don't necessarily burn CPU. So HPA can't "see" them.

The paper argues that this mismatch causes two real problems:
1. **Over-provisioning**: HPA thinks load is high based on CPU spikes caused by reconnection bursts, so it keeps too many pods running long after they're needed.
2. **Connection destruction**: When HPA decides CPU is low and kills pods, it severs all WebSocket sessions those pods were holding — without warning, permanently.

The authors built a custom Kubernetes controller that reads `active_connections` from Prometheus instead of using CPU, and uses that to make scaling decisions. They ran experiments to show that this approach doesn't destroy connections and doesn't over-provision.

The paper got a **Weak Reject** (5/10) from reviewers. That doesn't mean the work is bad — it means it's close but needs specific fixes before it can be published. The issues are mostly about how it's framed and how incomplete the comparison experiments are. This document explains each flaw in plain terms.

---

## Overall Score: 5 / 10
**Recommendation: Weak Reject**

A "Weak Reject" at 5/10 does **not** mean the paper is bad. It means the work is real and useful but has specific, fixable problems. All the issues listed below are solvable with more experiments, reworded claims, and a stronger comparison section.

---

## What the Paper Does Well (Strengths)

### 1. The Problem is Real and Matters in Production

The core issue — that CPU is a poor signal for WebSocket-heavy workloads — is something every backend engineer building a real-time app would recognise immediately. Chat apps, live dashboards, multiplayer games: all of these hold thousands of WebSocket connections that idle silently. HPA cannot see them. This paper is directly relevant to anyone operating that type of system.

### 2. The Explanation of Why CPU Fails is Clear

The paper walks through the exact failure chain in a way that is easy to follow:
- Clients go idle → CPU drops → HPA kills pods → connections break → clients all reconnect at once → CPU spikes → HPA thinks there is a new load surge → it scales back up → then back down again → the cycle never stops.

This "reconnection storm" narrative is well-written and gives reviewers an immediate intuition for the problem.

### 3. The Prototype Actually Works

The authors did not just write about the problem; they built a Kubernetes operator using Kubebuilder, wired it to Prometheus, and implemented a real cooldown-based scaling policy that runs in a real cluster. A lot of papers never get this far.

### 4. The Experiments Show Something Real

Even though the experiments need more rigor (covered below), they do show observable, real effects:
- Pods staying at 15x the required count while serving ~50 idle connections.
- Peak reconnection rates of 1,400 connections/second measured via Prometheus.
- All 800 connections wiped out step-by-step as HPA kills pods one batch at a time.

These numbers are directionally correct and clearly demonstrate that the failure mode is real.

---

## What Needs to be Fixed (Weaknesses)

### Flaw 1: The Approach Is Not New Enough — and the Paper Does Not Acknowledge That *(Major)*

**What the reviewer is saying:**
Scaling on `active_connections` via Prometheus is not a brand-new idea. Kubernetes has supported custom metrics since around 2018. There is also a popular tool called **KEDA** (Kubernetes Event-Driven Autoscaler) that does exactly this kind of external-metric scaling out of the box, without writing any custom controller code.

**Why this is a problem:**
A reviewer reading the paper will know about KEDA. If the paper never mentions it and claims novel contribution, the reviewer's first reaction is: "Why didn't they just use KEDA? Is this just KEDA, manually implemented?" That is a rejection flag.

**In plain terms:**
If someone says "I built a tool that scales Kubernetes pods based on custom metrics from Prometheus," a senior engineer will immediately ask "Why not KEDA?" The paper must answer that question directly.

**The fix is not to add novelty. The fix is to reframe the contribution:**
The actual value is not the mechanism (Prometheus → scale decision). The actual value is the *study*: showing exactly how badly CPU-based HPA behaves for WebSocket workloads, measuring the failure modes with precise numbers, and demonstrating that a connection-aware policy combined with lifecycle-safe scale-down behaviour fixes it. The paper is a *systems evaluation with a validated implementation*, not a new mechanism invention.

---

### Flaw 2: The Experiments Only Compare Against a Weak Opponent *(Critical — Biggest Blocker)*

**What the reviewer is saying:**
The paper compares:
- CPU-based HPA (factory settings, completely blind to connections)
vs.
- The custom controller (reads connection count from Prometheus)

But it never compares:
- HPA configured with a custom connection metric via Prometheus Adapter
- HPA with a tuned scale-down stabilisation window
- KEDA doing the same connection-aware scaling

**Why this is a problem:**
Imagine a boxing match where the challenger only fights someone who shows up blindfolded. Even if they win convincingly, nobody is impressed — because the real question is "would you beat someone who can actually see?" HPA *can* read connection metrics if you configure it. The paper does not show it can beat that version.

**In plain terms:**
Run a 4th or 5th experiment where HPA reads from Prometheus via the Prometheus Adapter and targets `active_connections`. That is HPA's strongest available configuration. If the custom controller still performs better (which it likely will, due to the cooldown mechanism and smarter scale-down policy), that is a publishable result. If they tie, that is also interesting. But skipping this comparison makes the whole evaluation suspect.

---

### Flaw 3: Several Claims Are Too Strong and Will Get the Paper Rejected On Their Own *(Critical)*

**The specific phrases that are flagged:**

| What the paper currently says | Why it is a problem |
|---|---|
| "necessary and sufficient" | This is a formal mathematical phrase. It means "this is the only possible solution and it always works." You need a formal proof to say this. |
| "provably unachievable by CPU-based autoscaling" | "Provably" means you have proven it mathematically. A set of experiments showing it did not work in 5 scenarios is not a proof. |
| "zero connection loss" | In distributed systems, this claim is almost never true at scale. Node crashes, network blips, and client-side timeouts all cause connection loss. Saying "zero" when you mean "none observed in controlled lab conditions" is an overclaim. |
| "fundamental incompatibility" | "Fundamental" implies this is a deep, inherent, un-patchable architectural truth. The reviewer correctly points out that HPA with custom metrics partially works — it is a mismatch, not a fundamental impossibility. |

**What to say instead:**
- "no connection drops were observed during controlled experiments"
- "CPU-based HPA with default configuration was not observed to scale correctly under any of our evaluated workload scenarios"
- "a mismatch between default scaling signals and workload semantics"

The facts in the paper are correct. The language is just too absolute.

---

### Flaw 4: Each Experiment Was Only Run Once *(Major)*

**What the reviewer is saying:**
Every experiment in the paper runs one time and reports one number. There are no repeated trials, no error bars, no standard deviations.

**Why this is a problem:**
Cluster experiments are noisy. CPU measurements on a shared `kind` cluster competing with Docker overhead are not perfectly reproducible. If you run the same experiment 5 times, you will get slightly different peak connection counts and slightly different timing gaps. That variation is normal. Reporting a single run means nobody knows whether the result is characteristic or a fluke.

**In plain terms:**
Statistical rigor in experiments is like tests in software: one green build does not mean it is working. Five green builds in a row is much more convincing.

**The fix:** Run each key experiment 5 times. Report mean ± standard deviation. The scripts are already automated — this is not much extra work.

---

### Flaw 5: No Experiments Showing Where the System Breaks *(Major)*

**What the reviewer is saying:**
The paper only shows scenarios where the custom controller works. There are no scenarios where it struggles or degrades. That is suspicious.

**Why this is a problem:**
A paper that only shows its system succeeding looks like cherry-picking. Every real system has edge cases. Honest experimental evaluation shows both the nominal cases (where everything works as designed) and the boundary cases (where the system is close to its limits or fails).

**In plain terms:**
Think of it like writing a production runbook. A good runbook has both the happy path AND the failure modes: "here is what happens when Prometheus is down for 30 seconds," "here is what happens when 5,000 clients connect simultaneously faster than our 15-second reconciliation loop can react."

**The specific scenarios to test:**
1. **Metric staleness**: Artificially delay Prometheus scrapes by 30–60 seconds. Does the controller make bad decisions from stale data?
2. **Sudden spike**: Launch 2,000 clients simultaneously (not staggered). Can the controller scale fast enough, or does it temporarily under-provision?
3. **Prometheus downtime**: Take Prometheus offline for 2 minutes mid-experiment. Does the controller safely hold pod count or crash to zero?

Showing that the controller degrades predictably and safely under these conditions actually *strengthens* the paper, not weakens it.

---

### Flaw 6: The Paper Implies Kubernetes Cannot Do Connection-Aware Scaling — It Can *(Moderate)*

**What the reviewer is saying:**
The paper's framing implies that HPA is fundamentally incapable of scaling on connection count. That is not true. Kubernetes has a Custom Metrics API that lets HPA scale on any Prometheus-exposed metric. Engineers have been doing this for years.

**Why this is a problem:**
Any Kubernetes-familiar reviewer will catch this and lose confidence in the paper's accuracy.

**What is actually true:**
The problem is not that Kubernetes *cannot* scale on connection count. The problem is that *default Kubernetes does not*, and that configuring it requires non-trivial integration work (Prometheus Adapter install, custom metrics API wiring, stabilisation policy tuning). The paper should characterise this as a configuration and tooling gap, not a platform limitation.

**The fix:** One sentence change. Replace any implication of "Kubernetes cannot" with "Kubernetes does not by default, and the integration overhead is non-trivial."

---

### Flaw 7: The Experiments Only Measure Pod Count and Connection Survival *(Moderate)*

**What the reviewer is saying:**
The graphs show replicas and connection count over time. There is no data on:
- How long it takes for the controller to react after a load change (scale reaction time)
- What end-to-end WebSocket latency looks like during a scaling event
- How much the cluster costs per unit time under each configuration (pod-seconds)
- How long it takes the system to recover after a spike

**Why this is a problem:**
Connection survival is a prerequisite, not a sufficient proof of improvement. A reviewer will ask: "Does this extra stability come at the cost of higher latency? Does holding warm pods during cooldown use more resources?"

**The fix:** Add P95 latency at the WebSocket client, scale reaction time, and pod-seconds per experiment. These do not require new experiments — they can be extracted from existing logs with additional instrumentation.

---

### Flaw 8: Key Related Work and Tools Are Missing *(Moderate)*

**What the reviewer is saying:**
The Related Work section does not mention:
- **KEDA** — the most popular event-driven autoscaler for Kubernetes
- Papers on load-aware scheduling
- Connection draining techniques used in production systems

**The fix:** Add a paragraph in Related Work explicitly comparing KEDA. Explain what KEDA does, why the paper did not just use KEDA, and what the custom controller adds specifically (connection-lifecycle-aware scale-down policy and cooldown semantics that KEDA does not natively provide for WebSocket connection management).

---

### Flaw 9: Someone Else Cannot Reproduce the Experiments *(Moderate)*

**What the reviewer is saying:**
The paper does not include enough detail for an independent researcher to redo the experiments. There are no links to code, no exact cluster configuration parameters, no workload scripts.

**The fix:** Include a link to the public GitHub repository. The cluster config, server code, load generator, and controller are all already in the repo. A single URL resolves this entirely.

---

### Flaw 10: The Controller Design Section Reads Like a README, Not a Research Paper *(Moderate)*

**What the reviewer is saying:**
The paper explains what the controller does at a "here is how the code works" level, but does not explain the design in terms researchers care about:
- Why is ceiling division the right formula?
- Is the system stable? Could it oscillate?
- What is the worst-case convergence time?
- What are the formal properties of the cooldown window?

**The fix:** Add a short "Controller Design" subsection that frames the reconciliation loop as a feedback control system, explains that the cooldown window introduces hysteresis that prevents oscillation, and gives the convergence bound: the system reaches desired replica count in at most `ceil((current - desired) / maxScaleDownStep)` reconciliation cycles after cooldown expires.

---

## Summary Assessment

The paper solves a real problem, with real code, producing real measurements. The work itself is solid. The issues are in how it is framed and how incomplete the comparison experiments are.

| Issue | Severity | Fix Difficulty |
|---|---|---|
| No comparison with HPA custom metrics or KEDA | Critical | Medium (run 2 more experiments) |
| Overclaimed language ("zero", "provably", "necessary and sufficient") | Critical | Easy (word changes only) |
| Every experiment run only once, no statistics | Major | Medium (re-run scripts 5x, add stddev) |
| No failure mode experiments | Major | Medium (add 2–3 adversarial scenarios) |
| Implies Kubernetes cannot do custom metrics | Moderate | Easy (one sentence fix) |
| Missing KEDA in related work | Moderate | Easy (add one paragraph) |
| No latency or cost metrics | Moderate | Medium (add instrumentation) |
| Controller described, not analysed formally | Moderate | Easy (write convergence argument) |
| No reproducibility artefacts / code link | Moderate | Easy (add GitHub link) |

With these fixes, the paper moves from borderline-reject to a strong accept candidate. None of the issues require throwing away existing work — they are all additive.

---

## Decision: Weak Reject → Fixable to Accept
