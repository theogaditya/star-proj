## Introduction

Horizontal scalability is a fundamental requirement in modern cloud-native systems. In containerized environments orchestrated by Kubernetes, elasticity is primarily achieved through the **Horizontal Pod Autoscaler (HPA)**, which dynamically adjusts the number of pod replicas based on observed metrics.

By default, Kubernetes HPA relies on **Resource Metrics (CPU and memory)** collected via the Metrics Server. However, many real-world workloads exhibit behavior that cannot be accurately captured through CPU utilization alone. Applications such as API gateways, e-commerce systems, and real-time services are often better characterized by **application-level signals** such as request rate or queue length.

To address this limitation, Kubernetes supports **Custom Metrics**, typically integrated through Prometheus and the Prometheus Adapter. This introduces an additional observability-control pipeline:

```
Pod → Prometheus → Adapter → Custom Metrics API → HPA Controller
```

While this architecture enables flexible autoscaling, it also introduces additional latency, sampling effects, and potential control instability due to scraping intervals and query resolution.

This project performs a structured experimental analysis of HPA behavior under multiple scaling strategies:

- **PCM-CPU** - CPU-based scaling via Prometheus Custom Metrics
- **PCM-H** - HTTP request-rate-based scaling
- **PCM-CH** - Hybrid CPU + HTTP scaling

The study focuses on:

- Control-loop responsiveness
- Impact of metric scraping interval (60s vs 30s vs 15s)
- Desired vs actual replica convergence
- CPU saturation patterns
- Scaling efficiency and overprovisioning behavior
- Stability trade-offs in hybrid metric strategies

The objective of this work is not merely to deploy HPA, but to analyze it as a **distributed feedback control system**, examining how metric collection granularity and signal selection influence scaling accuracy, latency, and resource efficiency.

## Research Background and HPA Context

The Horizontal Pod Autoscaler (HPA) in Kubernetes operates as a periodic feedback controller embedded within the `kube-controller-manager`. At fixed synchronization intervals (15 seconds by default), it evaluates observed metrics and computes a desired replica count using a proportional scaling model.

The core scaling relationship implemented by HPA is:

```
desiredReplicas = ceil(currentReplicas × (currentMetricValue / targetMetricValue))
```

This equation represents a proportional control strategy: when the observed metric exceeds its target, the replica count increases proportionally; when it falls below the target, the system eventually scales down (subject to stabilization windows and safety constraints).

For this mechanism to behave predictably, several assumptions must hold:

- Metric observations must accurately reflect real workload pressure.
- Sampling intervals must be sufficiently fine-grained.
- Metric propagation latency must remain bounded.
- The control loop must not operate on stale or aliased data.

If these assumptions are violated, scaling may become delayed, oscillatory, or inefficient.

While Kubernetes provides default Resource Metrics (CPU and memory), modern microservice workloads are often better characterized by application-level signals such as request rate or traffic throughput. For this reason, Kubernetes exposes the **Custom Metrics API**, which allows external monitoring systems to provide autoscaling signals.

This report focuses exclusively on the **Prometheus Custom Metrics (PCM)** pipeline.


### Prometheus Custom Metrics Architecture

In the PCM experiment, the autoscaling signal traverses a distributed observability pipeline before reaching the HPA controller:

```
Application Pod
    ↓
Prometheus (scraping + time-series storage)
    ↓
Prometheus Adapter (PromQL transformation)
    ↓
custom.metrics.k8s.io API
    ↓
Horizontal Pod Autoscaler
```

Compared to default Resource Metrics, this pipeline introduces multiple processing stages:

- Prometheus periodically scrapes application metrics endpoints.
- Metrics are stored as time-series data.
- PromQL functions (such as `rate()`) transform raw counters into derived signals.
- The Prometheus Adapter exposes transformed metrics via the Kubernetes Custom Metrics API.
- HPA consumes the processed signal during its sync cycle.

This architecture enables scaling based on:

- HTTP request rate,
- CPU metrics proxied through Prometheus,
- Hybrid combinations of CPU and HTTP signals,
- Arbitrary domain-specific metrics.

However, it also introduces additional control dynamics:

- Scrape interval misalignment with the HPA sync period,
- Metric smoothing and extrapolation effects,
- Query evaluation overhead,
- API aggregation latency.

As a result, autoscaling behavior is influenced not only by workload demand, but also by the characteristics of the observability pipeline itself.


### Context from HPA Research Literature

Existing research on HPA behavior demonstrates that metric collection configuration directly influences scaling performance.

The referenced study emphasizes that:

1. **Scraping interval determines responsiveness.**
   Longer scraping periods delay metric updates, causing HPA to react slowly to sudden workload spikes.

2. **Metric aggregation mechanics affect control stability.**
   Instantaneous resource metrics behave differently from rate-based metrics derived through time-series processing.

3. **Sampling misalignment can cause aliasing.**
   When scrape intervals and HPA sync intervals are not aligned, the controller may operate on partially updated or stale values.

4. **Higher-frequency sampling improves reactivity but increases overhead.**
   Reducing scrape intervals increases time-series resolution, but also raises computational, storage, and network costs.

While prior work compares Kubernetes Resource Metrics (KRM) and Prometheus Custom Metrics (PCM), this report isolates and analyzes only the **PCM behavior**, examining how:

- Scrape interval variation (60s vs 30s vs 15s),
- HTTP-only scaling strategies (PCM-H),
- Hybrid CPU + HTTP strategies (PCM-CH),
- Desired vs current replica divergence,

influence control fidelity, scaling accuracy, and system efficiency.


### System-Level Implications

When using Prometheus Custom Metrics, HPA becomes part of a multi-stage distributed feedback loop rather than a simple resource-triggered scaler.

The autoscaling decision now depends on:

- Observability resolution (scrape frequency),
- Signal transformation accuracy (PromQL processing),
- Metric propagation latency,
- Synchronization between the scrape cycle and the HPA control cycle.

These factors introduce measurable system-level consequences:

- **Over-provisioning** when aggressive rate extrapolation amplifies transient traffic spikes.
- **Delayed reaction** when coarse scrape intervals obscure rapid workload increases.
- **Replica overshoot** under bursty traffic conditions.
- **Improved stability** when hybrid metrics provide multiple scaling perspectives.

Therefore, Prometheus-based autoscaling should be treated as a control system whose behavior is shaped by sampling resolution, signal transformation, and distributed timing effects - not merely as a configuration enhancement.

This study empirically evaluates these effects through controlled PCM experiments.

## Objectives of the PCM Experiment

The objective of this experiment is to rigorously evaluate the behavior of the **Prometheus Custom Metrics (PCM)** pipeline when used as the autoscaling signal source for Kubernetes Horizontal Pod Autoscaler (HPA).

Rather than comparing PCM against Kubernetes Resource Metrics (KRM), this study isolates the PCM architecture and analyzes how its internal design decisions influence autoscaling dynamics.

The experiment is structured around the following primary objectives:


### 1. Evaluate Control-Loop Responsiveness

The first objective is to determine how quickly HPA reacts to workload changes when metrics are sourced from Prometheus.

Specifically, the experiment measures:

- Time taken for replica count to increase after traffic surge
- Alignment between workload ramp-up and replica convergence
- Lag between metric spike and scaling action

This allows us to quantify whether PCM introduces measurable control latency due to scraping, query processing, or API aggregation.

### 2. Analyze the Impact of Scrape Interval

Prometheus allows configurable scrape intervals. This experiment evaluates how different scrape periods (60s, 30s, and 15s) affect:

- Metric freshness
- Scaling aggressiveness
- Overshoot behavior
- Stability of replica count

The goal is to determine whether reducing scrape interval meaningfully improves scaling fidelity, and whether such improvement justifies the increased observability overhead.

### 3. Compare Metric Strategies Within PCM

The PCM experiment evaluates three scaling strategies:

- **PCM-CPU** - CPU-based scaling using Prometheus as the metric source
- **PCM-H** - HTTP request rate-based scaling
- **PCM-CH** - Hybrid CPU + HTTP scaling

This comparison aims to understand:

- Whether request-rate scaling reacts earlier than CPU-based scaling
- Whether hybrid scaling reduces instability
- How metric choice influences over-provisioning or under-scaling

### 4. Measure Desired vs Current Replica Divergence

HPA computes a desired replica count during each sync cycle, but the actual number of running pods may lag due to scheduling, container startup time, and readiness delays.

This experiment measures:

- Gap between desired and current replicas
- Duration of unmet scaling demand
- Convergence behavior under burst traffic

This helps evaluate how effectively the PCM pipeline supports accurate and timely actuation.

### 5. Evaluate Scaling Efficiency

Beyond responsiveness, autoscaling must also be resource-efficient.

The experiment analyzes:

- CPU utilization relative to replica count
- Over-provisioned states (high replicas, low CPU)
- Under-provisioned states (high CPU, low replicas)

The objective is to identify trade-offs between responsiveness and resource utilization under different PCM strategies.

### 6. Understand System-Level Behavior Under Bursty Traffic

The workload used in this experiment follows alternating high-traffic and low-traffic phases. This structure allows us to observe:

- Scale-up behavior during rapid load increase
- Stability during sustained high load
- Scale-down latency and hysteresis
- Behavior during traffic drop

This controlled workload design enables precise observation of PCM-driven scaling dynamics.

Collectively, these objectives frame the PCM experiment as a system-level evaluation of autoscaling behavior under varying sampling resolution and metric strategies. The results provide empirical insight into how Prometheus-based autoscaling behaves in realistic burst scenarios.

## Experimental Methodology

The PCM experiment was implemented using a Kind-based Kubernetes cluster with Prometheus and Prometheus Adapter to expose custom metrics to the HPA controller.

Rather than describing the setup abstractly, this section presents the core manifests used in the experiment.

### 1. Cluster Configuration (Kind)

The cluster was created using a multi-node Kind configuration to allow horizontal scaling across worker nodes.

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

This configuration ensures sufficient scheduling capacity for replica scaling experiments.

### 2. Application Deployment

The application is a CPU-sensitive HTTP service exposing Prometheus metrics.

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
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
        prometheus.io/path: "/metrics"
    spec:
      containers:
        - name: cpu-http
          image: cpu-http-app:latest
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "100m"
            limits:
              cpu: "200m"
```

Key characteristics:

- CPU requests and limits enforce measurable utilization.
- Prometheus scrape annotations expose metrics.
- Initial replicas are set to 4 to allow both scale-up and scale-down observation.

### 3. Prometheus Configuration

Prometheus was configured with adjustable scrape intervals to evaluate responsiveness effects.

```yaml
# prometheus.yaml (excerpt)
global:
  scrape_interval: 60s   # Modified to 30s and 15s in experiments
  evaluation_interval: 15s

scrape_configs:
  - job_name: "kubernetes-pods"
    kubernetes_sd_configs:
      - role: pod
```

The scrape interval was varied across:

- 60 seconds
- 30 seconds
- 15 seconds

This variation isolates the effect of sampling resolution on autoscaling dynamics.

### 4. Prometheus Adapter Rules

The adapter exposes transformed metrics to the HPA via `custom.metrics.k8s.io`.

```yaml
# prometheus-adapter.yaml (rule excerpt)
rules:
  - seriesQuery: 'http_requests_total'
    resources:
      overrides:
        kubernetes_namespace: { resource: "namespace" }
        kubernetes_pod_name: { resource: "pod" }
    name:
      matches: "^(.*)$"
      as: "http_requests_per_second"
    metricsQuery: 'sum(rate(<<.Series>>{<<.LabelMatchers>>}[1m])) by (<<.GroupBy>>)'
```

This rule converts cumulative request counters into per-second rates using `rate()`, enabling request-based autoscaling.

### 5. HPA Configurations

#### PCM-H (HTTP Only)

```yaml
# hpa-pcm-http.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cpu-app-hpa-http
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cpu-app
  minReplicas: 4
  maxReplicas: 24
  metrics:
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: 3
```



#### PCM-CPU (CPU via Prometheus)

```yaml
# hpa-pcm-cpu.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cpu-app-hpa-cpu
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cpu-app
  minReplicas: 4
  maxReplicas: 24
  metrics:
    - type: Pods
      pods:
        metric:
          name: cpu_usage
        target:
          type: AverageValue
          averageValue: 60m
```



#### PCM-CH (Hybrid CPU + HTTP)

```yaml
# hpa-pcm-cpu-http.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cpu-app-hpa-hybrid
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
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: 3
```

In the hybrid configuration, HPA computes replica recommendations for both metrics and applies the maximum value, effectively combining a leading signal (HTTP rate) with a stabilizing signal (CPU utilization).

##### Workload Design

Traffic was generated using an automated experiment script that produced structured burst cycles consisting of alternating high-traffic and low-traffic phases.

Each experiment included:

- A sustained high-load phase to trigger scale-up
- A reduced-load phase to observe stabilization
- An observation window to evaluate scale-down behavior

This step-like workload pattern ensures clear visibility of:

- Scaling latency
- Replica convergence speed
- Overshoot behavior
- Downscale hysteresis

The controlled traffic structure enables accurate evaluation of PCM-driven autoscaling dynamics under burst conditions.

## Results

This section presents the empirical observations from the Prometheus Custom Metrics (PCM) experiment. The results are organized to evaluate responsiveness, scaling stability, metric sensitivity, and efficiency across different PCM configurations.

### 1. Replica Scaling Over Time

![Replicas Over Time](./results/plots/replicas_over_time.png)

The evolution of replica counts under different PCM strategies reveals distinct behavioral patterns:

- **PCM-H (HTTP-only)** reacts earliest to traffic spikes, acting as a leading indicator.
- **PCM-CPU** scales more gradually, responding only after CPU saturation increases.
- **PCM-CH (Hybrid)** combines early reaction with stability, often matching HTTP-driven scale-up while preventing aggressive oscillation during stabilization.

Replica overshoot is most visible in HTTP-only scaling during burst onset, where request rate spikes precede CPU stabilization.

### 2. CPU Utilization Dynamics

![CPU Over Time](./results/plots/cpu_over_time.png)

CPU utilization patterns show the trade-off between responsiveness and efficiency:

- PCM-H occasionally drives CPU utilization lower than necessary due to rapid scale-out.
- PCM-CPU allows temporary CPU saturation before scaling catches up.
- PCM-CH maintains tighter utilization control by incorporating resource-based feedback.

This demonstrates that request-rate scaling prioritizes responsiveness, while CPU-based scaling prioritizes controlled resource usage.

### 3. Scrape Interval Sensitivity (PCM-CPU)

![Scraping Period Comparison](./results/plots/scraping_period_comparison.png)

Reducing the Prometheus scrape interval from 60s to 15s results in:

- Faster detection of workload changes
- Reduced delay between traffic surge and replica expansion
- More granular scaling adjustments

The most significant improvement occurs between 60s and 30s. The gain between 30s and 15s is comparatively smaller, indicating diminishing returns beyond moderate sampling frequency.

### 4. Desired vs Current Replica Divergence

![Desired vs Current](./results/plots/desired_vs_current.png)

During rapid load increases:

- Desired replicas spike immediately based on metric computation.
- Current replicas lag due to scheduling delay and container startup time.

The divergence duration represents unmet demand and directly impacts request handling efficiency. PCM-H typically exhibits larger short-term divergence because of more aggressive desired replica calculations.

### 5. HTTP-Only vs Hybrid Comparison

![PCM-H vs PCM-CH](./results/plots/pcm_h_vs_pcm_ch.png)

Comparison between HTTP-only and hybrid scaling shows:

- PCM-H scales earlier but may over-provision.
- PCM-CH moderates extreme scaling by incorporating CPU feedback.
- Hybrid scaling reduces oscillatory behavior during traffic transitions.

This confirms that combining leading and stabilizing signals improves control robustness.

### 6. Scaling Efficiency Analysis

![Efficiency Scatter](./results/plots/efficiency_scatter.png)

The CPU-versus-replica scatter analysis highlights the responsiveness-efficiency trade-off:

- Points in the upper-right region represent efficient high-load operation.
- Points in the lower-right region indicate over-provisioning (high replicas, low CPU).
- PCM-H shows greater density in over-provisioned regions.
- PCM-CH clusters closer to balanced operating zones.

This suggests that while HTTP-based scaling improves responsiveness, hybrid scaling better maintains resource efficiency.

#### Summary of Observations

Across all PCM configurations:

- Reduced scrape interval improves responsiveness but increases metric overhead.
- HTTP-only scaling acts as a leading indicator of traffic bursts.
- CPU-based scaling provides stabilizing feedback.
- Hybrid scaling balances early detection with controlled resource allocation.
- Replica divergence is unavoidable but minimized with finer sampling resolution.

The results demonstrate that PCM-based autoscaling behaves as a multi-stage feedback system whose dynamics are shaped by sampling resolution, signal processing, and metric composition.

## Discussion of Results

The experimental results demonstrate that Prometheus Custom Metrics (PCM) significantly influence the behavior of Kubernetes Horizontal Pod Autoscaler (HPA), not only in terms of responsiveness but also in control stability and resource efficiency.

Rather than acting as a simple metric replacement, PCM introduces additional control dynamics due to scraping frequency, PromQL processing, and API aggregation layers.

### 1. Responsiveness vs Stability Trade-off

HTTP-based scaling (PCM-H) consistently reacts earlier to traffic surges than CPU-based scaling. Since request rate is a leading indicator of incoming workload, HPA scales almost immediately when request bursts begin.

However, this responsiveness introduces two side effects:

- Temporary over-provisioning during short-lived spikes
- Larger divergence between desired and current replicas

CPU-based scaling reacts slightly later because CPU saturation must first occur before scaling is triggered. While this delays scale-out, it reduces the risk of unnecessary replica expansion.

Hybrid scaling (PCM-CH) effectively balances these two behaviors. HTTP rate triggers early scaling, while CPU utilization moderates aggressive overshoot. This confirms that combining leading and stabilizing metrics improves overall control robustness.

### 2. Impact of Scrape Interval

Scrape interval directly affects how quickly HPA receives updated signals.

Reducing scrape interval from 60 seconds to 30 seconds produces noticeable improvements in scale-up latency. Further reduction to 15 seconds improves granularity but yields diminishing returns relative to the added observability overhead.

This suggests that:

- Extremely coarse sampling (60s) can delay control decisions.
- Moderate sampling (30s) offers a practical balance.
- Very fine sampling (15s) mainly benefits highly bursty workloads.

The experiment confirms that scrape resolution must be aligned with HPA’s 15-second sync cycle to minimize aliasing effects.

### 3. Replica Divergence and Convergence Behavior

All PCM configurations exhibit temporary divergence between desired and current replicas during traffic spikes.

This divergence arises from:

- Pod scheduling delay
- Container startup latency
- Readiness time

More aggressive scaling strategies (PCM-H) amplify this gap because desired replicas increase sharply. Hybrid scaling reduces extreme divergence by moderating replica growth.

This highlights that autoscaling performance is constrained not only by metric detection speed but also by actuation latency within the cluster.

### 4. Efficiency vs Over-Provisioning

The efficiency scatter analysis reveals that responsiveness and efficiency are inversely correlated:

- HTTP-only scaling prioritizes fast reaction but increases over-provisioned states.
- CPU-based scaling maintains tighter resource utilization but reacts slower.
- Hybrid scaling achieves a middle ground with reduced waste.

This trade-off is fundamental to autoscaling design. Systems optimized for responsiveness will typically consume more resources, while systems optimized for efficiency may tolerate short-lived saturation.

### 5. PCM as a Multi-Stage Feedback System

The experiment confirms that PCM-based autoscaling behaves as a distributed feedback loop:

1. Metrics are scraped at fixed intervals.
2. PromQL transforms raw counters into rate signals.
3. Adapter exposes processed metrics to the API server.
4. HPA evaluates metrics at its sync interval.
5. Replica scaling is scheduled and executed.

Each stage introduces latency and transformation effects.

Therefore, PCM autoscaling must be understood as a control system shaped by:

- Sampling resolution
- Signal transformation
- Synchronization intervals
- Distributed actuation delay

This explains why identical workloads can produce different scaling patterns under different scrape configurations and metric compositions.

### Overall Interpretation

The PCM experiment demonstrates that:

- HTTP-based scaling improves reaction speed.
- CPU-based scaling improves efficiency stability.
- Hybrid scaling offers superior overall balance.
- Scrape interval tuning significantly impacts responsiveness.
- Autoscaling accuracy depends on both metric detection and cluster actuation capacity.

These findings emphasize that Prometheus-based autoscaling should be engineered carefully, with deliberate consideration of sampling theory, signal selection, and workload characteristics.

## Future Work

While the PCM experiment provides empirical insight into Prometheus-based autoscaling behavior, several extensions can further strengthen the evaluation and move the study toward production-grade relevance.

### 1. Real-World Cluster Deployment

The current experiment was conducted on a Kind-based local cluster. Future work can replicate the PCM configurations on:

- Managed Kubernetes environments (e.g., cloud-based clusters)
- Larger multi-node production-like clusters
- Heterogeneous node environments

This would allow evaluation of additional variables such as network latency, node autoscaling interaction, and real scheduling constraints.

### 2. Integration with Cluster Autoscaler

This study isolates Horizontal Pod Autoscaler behavior. In production systems, HPA interacts with the Cluster Autoscaler (CA).

Future investigation could analyze:

- Interaction between pod scaling and node scaling
- Delays introduced by node provisioning
- Resource fragmentation under aggressive PCM-H scaling
- Stability of hybrid strategies under combined autoscaling layers

This would provide a more complete system-level understanding.

### 3. Advanced Metric Engineering

The experiment used basic PromQL rate transformations. Future enhancements may include:

- Using `irate()` for faster short-term responsiveness
- Applying smoothing windows to reduce oscillation
- Incorporating percentile-based metrics (e.g., latency percentiles)
- Scaling based on queue depth or backlog length

This would help evaluate whether alternative signal processing improves control fidelity.

### 4. Adaptive Scrape Interval Strategies

Rather than static scrape intervals (60s, 30s, 15s), future systems could implement:

- Dynamically adjustable scrape intervals
- Burst-aware sampling strategies
- Event-triggered metric refresh

This may allow balancing observability overhead with responsiveness dynamically.

### 5. Control-Theoretic Analysis

The PCM pipeline can be modeled formally as a sampled feedback control system. Future work may include:

- Stability analysis using control theory
- Modeling sampling aliasing mathematically
- Quantifying overshoot and settling time
- Deriving optimal scrape-to-sync ratios

Such analysis would elevate PCM autoscaling evaluation from empirical observation to theoretical modeling.

### 6. Fault Injection and Resilience Testing

Future experiments can simulate:

- Prometheus downtime
- Prometheus Adapter failure
- Metric API unavailability
- Network partition scenarios

This would help evaluate how PCM-based HPA behaves under degraded observability conditions and whether hybrid scaling improves resilience.

### 7. Cost-Aware Scaling Evaluation

Beyond performance metrics, future work may analyze:

- Infrastructure cost implications
- Resource wastage under over-provisioning
- Cost-benefit trade-offs between 30s and 15s scraping

This would provide practical deployment guidance for production environments.

Overall, the PCM experiment establishes a foundation for understanding Prometheus-driven autoscaling. Future extensions can expand the analysis across scale, resilience, economics, and formal control modeling to build a comprehensive framework for metric-driven elasticity in Kubernetes.

## Conclusion

This study evaluated the behavior of Kubernetes Horizontal Pod Autoscaler (HPA) when driven by Prometheus Custom Metrics (PCM). By isolating the PCM pipeline and systematically varying scrape intervals and metric strategies, the experiment provides empirical insight into how sampling resolution and signal selection influence autoscaling dynamics.

The results demonstrate that HTTP-based scaling (PCM-H) acts as a leading indicator, enabling faster reaction to workload bursts but increasing the likelihood of temporary over-provisioning. CPU-based scaling, when routed through Prometheus, reacts more conservatively and maintains tighter resource efficiency, though at the cost of delayed responsiveness. The hybrid strategy (PCM-CH) achieves a balanced behavior by combining early traffic detection with stabilizing resource feedback.

Scrape interval tuning was shown to be a critical parameter. Reducing the interval from 60 seconds to 30 seconds significantly improved responsiveness, while further reduction to 15 seconds yielded diminishing returns relative to the increased observability overhead. This highlights the importance of aligning metric sampling frequency with the HPA sync period to reduce aliasing and control lag.

The experiment confirms that Prometheus-driven autoscaling is not merely a configuration enhancement but a distributed, multi-stage feedback system whose behavior is shaped by sampling resolution, signal transformation, synchronization intervals, and cluster actuation latency.

Overall, the PCM architecture enables flexible, application-aware autoscaling, but its effectiveness depends on deliberate engineering of metric design, scrape configuration, and hybrid control strategies. Careful tuning is essential to achieve an optimal balance between responsiveness, stability, and resource efficiency.