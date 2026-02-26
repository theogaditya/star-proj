# Introduction

Elastic scalability is a foundational requirement in cloud-native systems. Kubernetes addresses this requirement through the **Horizontal Pod Autoscaler (HPA)**, which dynamically adjusts the number of pod replicas based on observed resource utilization. In its default configuration, HPA operates on **Resource Metrics**, primarily CPU utilization, exposed through the `metrics.k8s.io` API.

In the native metrics pipeline, CPU usage is collected from kubelets via cAdvisor, aggregated by the Metrics Server, and made available to the HPA controller. The controller periodically evaluates the observed utilization against a configured target and computes the required replica count using proportional scaling logic.


::contentReference[oaicite:0]{index=0}


Here, `currentUtilization` represents the average CPU utilization across pods, expressed as a percentage relative to the **requested CPU**, not the container limit. This distinction is important: if a pod requests 100m CPU but consumes 200m, the utilization reported to HPA is 200%.

The HPA controller executes its control loop at a fixed synchronization interval (typically 15 seconds). However, metric freshness depends on the Metrics Server update cycle, introducing sampling effects into the feedback loop. As discussed in the HPA research literature, this architecture effectively forms a discrete-time proportional control system with delayed actuation.

This experiment studies the behavior of HPA under its **native Kubernetes Resource Metrics (KRM)** pipeline. The objective is to characterize:

- Time-to-scale-up following a workload burst
- Convergence and stabilization dynamics
- Replica divergence between desired and actual states
- CPU overshoot and undershoot relative to target utilization
- Aggregate resource consumption during scaling events

A controlled square-wave workload is applied to induce repeated transitions between low and high load phases. By observing HPA behavior under these conditions, the study isolates the intrinsic control properties, latency characteristics, and efficiency trade-offs of Kubernetes’ built-in autoscaling mechanism.

The findings provide a rigorous empirical characterization of native HPA behavior, grounded in the control-theoretic principles outlined in the HPA research literature.

# Objectives of the KRM Experiment

The primary objective of this study is to rigorously characterize the behavior of Kubernetes Horizontal Pod Autoscaler under its native Resource Metrics pipeline. Rather than introducing alternative scaling signals, this experiment focuses exclusively on understanding the intrinsic properties of the default HPA control loop.

The study is structured around three core analytical dimensions: responsiveness, control stability, and resource efficiency.

---

## 1. Evaluate Responsiveness and Control Latency

A central objective is to quantify how quickly the native HPA mechanism responds to sudden workload changes.

When a high-load phase begins, CPU utilization rapidly increases beyond the configured target (60%). The HPA must detect this deviation, compute a new desired replica count, and trigger scaling actions. However, the observed response time is influenced by multiple factors:

- Metrics Server update interval
- HPA synchronization period
- Scheduling latency
- Container startup and readiness time

This experiment measures the **time-to-scale-up**, defined as the elapsed time between the start of a high-load phase and the first observable increase in replica count. It also measures **time-to-stabilization**, representing the time required for replica counts to converge and remain stable for a sustained duration.

Together, these metrics provide a quantitative assessment of control-loop responsiveness and practical actuation delay in native HPA.

---

## 2. Analyze Control Stability and Tracking Accuracy

HPA attempts to maintain CPU utilization near the configured target using proportional control. However, due to discrete sampling and delayed actuation, perfect tracking is rarely achieved.

This study evaluates:

- The magnitude of CPU overshoot above the target
- Undershoot during scale-down transitions
- Oscillatory behavior in replica counts
- Divergence between desired and current replicas

To formally quantify tracking performance, the experiment computes the **integral of absolute error** between observed CPU utilization and the target value over time. This metric captures how well the controller maintains its intended operating point across dynamic workload transitions.

By analyzing these characteristics, the study reveals whether native HPA behaves as a stable proportional controller or exhibits staircase effects and delayed correction under burst conditions.

---

## 3. Measure Resource Efficiency and Cost Implications

Autoscaling systems must balance responsiveness against resource efficiency. Rapid scaling can improve performance but may increase infrastructure consumption.

To evaluate this trade-off, the experiment measures:

- Maximum replica count reached during high-load phases
- Total replica-seconds consumed (pod-seconds)
- Average CPU utilization during stabilized periods
- Variance in per-pod CPU usage

These metrics provide insight into how efficiently the system allocates compute resources while attempting to maintain the target utilization.

Understanding this efficiency baseline is critical for assessing whether the default HPA configuration achieves a reasonable balance between performance stability and resource consumption.

---

Overall, the objective of this experiment is not merely to observe scaling events, but to systematically characterize the native HPA control loop in terms of latency, stability, and efficiency under controlled workload transitions.

# Experimental Methodology

This experiment evaluates the behavior of the Kubernetes Horizontal Pod Autoscaler under its native Resource Metrics pipeline. The methodology is designed to isolate the intrinsic characteristics of HPA without introducing external metric systems or custom scaling signals.

The experimental setup consists of four components: cluster configuration, workload deployment, HPA configuration, and workload execution.

---

## 1. Cluster Configuration

A multi-node Kubernetes cluster was created using Kind (Kubernetes-in-Docker) to simulate a distributed environment capable of horizontal scaling.

### Kind Cluster Configuration

```yaml
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
```

This configuration provides sufficient scheduling capacity for scaling up to the configured maximum replica count.

### Metrics Server Patch

Because Kind does not expose kubelet certificates in a production-ready manner, the Metrics Server was patched to enable metric collection.

```yaml
# metrics-server-patch.yaml
spec:
  template:
    spec:
      containers:
      - name: metrics-server
        args:
        - --kubelet-insecure-tls
        - --kubelet-preferred-address-types=InternalIP,Hostname,InternalDNS,ExternalDNS,ExternalIP
```

This patch ensures that:

- Metrics Server can communicate with kubelets
- CPU metrics are correctly exposed via `metrics.k8s.io`
- `kubectl top pods` functions correctly

The resulting metric pipeline is:

```
Pod CPU Usage
   ↓
kubelet (cAdvisor)
   ↓
Metrics Server
   ↓
metrics.k8s.io
   ↓
HPA Controller
```

No modifications were made to the default HPA synchronization interval (15 seconds).

---

## 2. Application Deployment

The workload consists of a CPU-intensive HTTP service designed to increase CPU utilization proportionally with incoming traffic.

### Deployment Manifest

```yaml
# cpu-app.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cpu-app
spec:
  replicas: 4
  selector:
    matchLabels:
      app: cpu-app
  template:
    metadata:
      labels:
        app: cpu-app
    spec:
      containers:
      - name: cpu-http
        image: cpu-http-app:latest
        ports:
        - containerPort: 8000
        resources:
          requests:
            cpu: "100m"
          limits:
            cpu: "500m"
```

Key characteristics:

- Initial replicas: 4
- CPU request: 100m
- CPU limit: 500m

Since HPA computes utilization relative to requested CPU, a pod consuming 200m CPU reports 200% utilization. This enables controlled saturation during high-load phases.

---

## 3. HPA Configuration (KRM)

The Horizontal Pod Autoscaler is configured using the autoscaling/v2 API with a CPU resource metric.

```yaml
# hpa-krm.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cpu-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cpu-app
  minReplicas: 4
  maxReplicas: 24
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
```

Configuration summary:

- Metric type: Resource (CPU)
- Target utilization: 60%
- Min replicas: 4
- Max replicas: 24

At each synchronization cycle, HPA computes the desired replica count using proportional scaling.

---

## 4. Workload Design

Traffic is generated using a structured square-wave pattern to produce repeated load transitions.

### Load Script (Excerpt)

```bash
# run-load-phase.sh (excerpt)
# Arguments: <output_dir> <cycles> <high_duration> <low_duration>

# High Phase
hey -z ${HIGH_DURATION}s -q 50 http://<service-endpoint>

# Low Phase
hey -z ${LOW_DURATION}s -q 2 http://<service-endpoint>
```

Each cycle consists of:

- High-load phase: sustained request rate exceeding 60% CPU utilization
- Low-load phase: minimal traffic allowing scale-down
- Fixed duration per phase (100 seconds)

Multiple cycles are executed to observe repeated scale-up and scale-down behavior.

---

## 5. Data Collection

The following data streams are recorded during each experiment run:

### HPA Status

- `currentReplicas`
- `desiredReplicas`
- `currentCPUUtilizationPercent`

### Pod CPU Metrics

- Raw CPU usage per pod (via `kubectl top pods`)
- Used to compute variance and dispersion

### Replica Count Timeline

- Number of running pods over time

### Phase Markers

- Timestamps marking high and low load transitions

These logs are post-processed to compute:

- Time-to-scale-up
- Time-to-stabilization
- Maximum replica count
- Overshoot/undershoot integral
- Pod-seconds (resource cost proxy)
- CPU utilization statistics

---

This methodology isolates the intrinsic behavior of native HPA under controlled workload transitions, enabling quantitative evaluation of responsiveness, stability, and efficiency.

# Results

This section presents the empirical observations from the native Kubernetes Resource Metrics (KRM) experiment. The results evaluate responsiveness, control stability, and resource efficiency under the default HPA configuration.

---

## 1. Replica Scaling Over Time

![Replicas Over Time](./replicas_over_time.png)

The evolution of replica counts shows a clear stepwise scaling pattern characteristic of discrete control systems.

During high-load phases, replicas increase incrementally rather than continuously. Scale-up occurs in proportional steps aligned with the HPA synchronization loop and Metrics Server refresh timing. In multiple instances, replicas remain unchanged for several control cycles before increasing, producing a visible staircase effect.

Scale-down transitions occur more gradually due to HPA stabilization logic, which prevents rapid oscillation.

Key observations:

- Scaling is reactive rather than predictive.
- Replica increases occur in discrete proportional steps.
- Staircase-like patterns appear during sustained load.

---

## 2. CPU Utilization Dynamics

![CPU Over Time](./cpu_over_time.png)

CPU utilization is measured as a percentage of requested CPU (100m per pod). During high-load phases, utilization frequently exceeds 100%, with values approaching 200% prior to scaling convergence.

This indicates temporary CPU saturation before sufficient replicas are provisioned.

Observations:

- Significant overshoot above the 60% target during burst onset.
- Gradual reduction in utilization as replicas increase.
- Stabilized utilization approaches but does not perfectly match the target.

The deviation reflects control-loop latency and discrete replica adjustments.

---

## 3. Desired vs Current Replica Divergence

![Desired vs Current](./desired_vs_current.png)

A measurable divergence exists between `desiredReplicas` and `currentReplicas` during scale-up events.

When high-load phases begin:

- Desired replicas increase immediately upon metric evaluation.
- Current replicas lag due to scheduling and container startup time.

This divergence persists until new pods become Ready and begin contributing to load handling. The duration of this gap quantifies actuation delay within the cluster.

Scale-down divergence is generally smaller but still present.

---

## 4. Scaling Efficiency

![Efficiency Scatter](./efficiency_scatter.png)

The efficiency scatter plot (replicas vs CPU utilization) highlights the responsiveness–efficiency trade-off.

Clusters of points above the 60% target appear during burst onset, reflecting overshoot. As replicas increase, points shift downward toward the target region.

The dispersion indicates:

- Temporary over-utilization during delayed scaling.
- Imperfect steady-state tracking.
- Stable but non-ideal proportional regulation.

---

## 5. Control Stability and Tracking Error

To quantify control performance, the experiment computes the integral of absolute error relative to the target utilization.


::contentReference[oaicite:0]{index=0}


This metric captures cumulative deviation from the target over time.

Findings:

- High-load bursts contribute significant positive error.
- Error decreases as replicas converge.
- Scale-down transitions introduce smaller undershoot error.

The system remains stable (no oscillatory instability), but exhibits measurable transient deviation due to discrete sampling and actuation delay.

---

Overall, the native KRM experiment demonstrates that Kubernetes HPA behaves as a stable discrete-time proportional controller. It responds reliably to workload bursts but exhibits measurable control latency, transient CPU saturation, and staircase scaling patterns under sustained load.

# Discussion of Results

The native Kubernetes Resource Metrics (KRM) experiment demonstrates that the default HPA behaves as a stable discrete-time proportional controller with measurable sampling and actuation delays. The derived metrics provide quantitative evidence of this behavior across responsiveness, stability, and efficiency dimensions.

---

## 1. Responsiveness and Time-to-Scale-Up

The `time_to_scale_up_s` metric directly captures detection and actuation delay. Across runs, scale-up does not occur immediately after the high-load phase begins. Instead, the system waits for metric refresh and the next HPA synchronization cycle before computing a new desired replica count.

This latency is compounded by pod scheduling and container startup time. As a result:

- CPU utilization remains well above the 60% target during early burst stages.
- Replica increments occur in discrete steps rather than smooth transitions.
- The system exhibits a visible staircase effect in the replica timeline.

The `time_to_stabilize_s` metric further shows that convergence to steady-state replica count requires multiple control cycles. This confirms that the controller is reactive and discretely sampled, not continuous.

---

## 2. Proportional Control Behavior and Stability

HPA computes scaling decisions according to:


::contentReference[oaicite:0]{index=0}


This proportional relationship explains several observed behaviors:

- Larger deviations from the 60% target produce larger replica jumps.
- Scaling occurs in integer increments.
- Overshoot arises when utilization remains elevated across sampling cycles.

Despite transient deviations, the system remains stable across repeated workload cycles. The absence of oscillatory divergence indicates that the proportional gain implicit in the HPA formula is conservatively tuned.

The `max_replicas` metric confirms bounded scaling behavior, while stabilization windows demonstrate convergence rather than runaway scaling.

---

## 3. Tracking Accuracy and Integral Error

Control performance is quantitatively evaluated using the `overshoot_undershoot_area` metric, which computes the integral of absolute deviation from the target utilization:


::contentReference[oaicite:1]{index=1}


This metric reveals that:

- Most error accumulates during the initial burst phase.
- Error decreases significantly once replica count stabilizes.
- Undershoot during scale-down contributes comparatively less to total deviation.

The `avg_cpu_util` metric during stabilized windows indicates that steady-state operation approaches the target but does not perfectly match it. This reflects discrete control updates and integer replica constraints.

---

## 4. Desired vs Current Replica Divergence

The gap between `desiredReplicas` and `currentReplicas` provides a measure of actuation latency. The duration of this divergence correlates closely with the observed `time_to_scale_up_s` metric.

During divergence:

- The system operates under temporary CPU saturation.
- New replicas have been logically requested but are not yet contributing capacity.
- Resource pressure persists until scheduling completes.

This divergence highlights that control performance depends not only on HPA logic but also on cluster scheduling throughput and container startup latency.

---

## 5. Resource Efficiency and Cost Trade-offs

The `pod_seconds` metric quantifies aggregate resource consumption across the experiment. When compared with `max_replicas` and tracking error metrics, it reveals a fundamental trade-off:

- Faster convergence reduces tracking error but increases pod-seconds.
- Conservative scaling increases transient overshoot but reduces resource expansion.

The `pod_cpu_std_dev` metric further shows that per-pod load dispersion decreases after stabilization, indicating improved workload distribution once scaling converges.

Taken together, these metrics demonstrate that native HPA balances stability and resource conservation rather than aggressively minimizing error.

---

## Overall Analytical Interpretation

The derived metrics confirm that native HPA under the KRM pipeline operates as a stable discrete-time proportional controller with:

- Measurable detection delay (`time_to_scale_up_s`)
- Finite convergence time (`time_to_stabilize_s`)
- Transient tracking error (`overshoot_undershoot_area`)
- Bounded replica expansion (`max_replicas`)
- Moderate resource cost (`pod_seconds`)

The system exhibits predictable staircase scaling behavior under burst workloads and prioritizes stability over aggressive responsiveness.

This quantitative baseline provides a rigorous characterization of default Kubernetes autoscaling dynamics under controlled load transitions.

# Conclusion

This study presented a detailed empirical characterization of Kubernetes Horizontal Pod Autoscaler operating under its native Resource Metrics (KRM) pipeline. By isolating the default metrics path—kubelet to Metrics Server to `metrics.k8s.io` to HPA—the experiment established a clear baseline for understanding intrinsic autoscaling behavior without external metric systems or custom signals.

The results confirm that native HPA functions as a stable discrete-time proportional controller. Scaling decisions follow the proportional relationship:


::contentReference[oaicite:0]{index=0}


This formulation produces predictable stepwise replica adjustments aligned with the controller synchronization cycle and metric refresh timing.

Quantitative analysis using derived metrics revealed that:

- **Responsiveness** is bounded by metric detection and actuation delay (`time_to_scale_up_s`).
- **Convergence time** (`time_to_stabilize_s`) reflects discrete control updates and pod startup latency.
- **Tracking error** (`overshoot_undershoot_area`) accumulates primarily during burst onset, where CPU utilization temporarily exceeds the 60% target.
- **Replica divergence** between desired and current states highlights infrastructure-induced scaling delay.
- **Resource cost** (`pod_seconds`) demonstrates a trade-off between rapid convergence and conservative scaling.

The experiment shows that native HPA prioritizes stability and resource conservation over aggressive responsiveness. While transient CPU overshoot is common during rapid workload increases, no sustained oscillatory instability was observed across repeated load cycles.

Overall, this KRM baseline establishes a rigorous control and performance reference for Kubernetes autoscaling under default configuration. It provides a quantitative foundation for understanding sampling effects, convergence behavior, and efficiency trade-offs inherent to native resource-metric-based scaling.