# Future Work

With both native Resource Metrics (KRM) and Prometheus Custom Metrics (PCM) autoscaling pipelines implemented and empirically evaluated, this study now provides a comparative foundation for understanding Kubernetes autoscaling architectures. The next stage of research should move beyond isolated evaluation toward structured optimization, latency modeling, and robustness validation.

## 1. Cross-Architecture Parameter Optimization

Both KRM and PCM experiments were executed under fixed configurations. A systematic parameter sweep across both architectures would allow identification of optimal operating regions.

Future experiments should vary:

- HPA synchronization period
- Stabilization windows
- Prometheus scrape interval (for PCM)
- Hybrid arbitration strategies (PCM-CH)

For each configuration, derived metrics such as:

- `time_to_scale_up_s`
- `time_to_stabilize_s`
- `overshoot_undershoot_area`
- `pod_seconds`

can be recomputed to construct a comparative performance surface.

This would answer a more refined research question:

> Under what parameter regimes does PCM meaningfully outperform KRM in responsiveness without incurring disproportionate resource cost?

This extension is fully achievable using the existing experimental framework and transforms the current study into an optimization-focused analysis.

## 2. Unified Latency Decomposition Across Architectures

Both pipelines exhibit measurable scaling delay, but the sources differ. KRM latency is dominated by Metrics Server refresh and HPA sync cycles, while PCM introduces additional scraping and adapter stages.

A unified delay model can be expressed as:


::contentReference[oaicite:0]{index=0}


Where:

- **Detection Delay** = metric freshness (Metrics Server or Prometheus)
- **Control Delay** = HPA synchronization interval
- **Actuation Delay** = scheduling and container startup

By instrumenting timestamps in both pipelines, it becomes possible to quantify which stage dominates total scaling latency and how architectural complexity affects responsiveness.

This analysis is experimentally feasible using existing logs and would provide a rigorous architectural comparison.

## 3. Cost–Responsiveness Trade-Off Modeling

Both experiments compute `pod_seconds`, providing a measurable proxy for resource consumption. PCM improves responsiveness but introduces monitoring overhead; KRM is simpler but slower to react.

Future work can define a normalized efficiency index such as:


::contentReference[oaicite:1]{index=1}


Where:

- Tracking Accuracy can be derived from inverse integral error
- Resource Cost from `pod_seconds`
- Optional adjustment for Prometheus overhead

By computing this index across KRM, PCM, and hybrid configurations, the study can quantitatively determine whether improved responsiveness justifies increased architectural complexity.

This extension requires only post-processing of already collected metrics.

## 4. Robustness Validation Under Realistic Workloads

The current experiments rely on deterministic square-wave traffic patterns to isolate scaling transitions. While analytically useful, production workloads exhibit stochastic behavior.

Future experiments should introduce:

- Gradual traffic ramps
- Random burst injections
- Mixed CPU- and request-driven load
- Noisy multi-application interference

The same derived metric framework can then evaluate:

- Stability under unpredictable input
- Error amplification in PCM vs KRM
- Replica divergence duration under noise

This extension is practical within the current cluster setup and would significantly strengthen the external validity of the study.

Together, these future directions shift the research focus from isolated evaluation toward comparative optimization, architectural latency modeling, and real-world robustness analysis. They build directly upon the implemented experiments and are both technically feasible and academically meaningful.

# Future Work (KRM-Focused)
The KRM experiment establishes a rigorous baseline characterization of native Kubernetes autoscaling under controlled burst workloads. While the system demonstrates stable proportional control behavior, the results reveal structural constraints related to sampling delay, actuation latency, and resource trade-offs. The following directions outline meaningful extensions of this work.

## 1. Controller Parameter Optimization and Stability Tuning

The current experiment uses default HPA configuration values, including a fixed synchronization interval and conservative stabilization windows. Although these defaults prioritize stability, they may not be optimal for highly bursty workloads.

Future work should systematically vary:

- HPA synchronization period
- Scale-up and scale-down stabilization windows
- CPU tolerance thresholds
- Maximum scale-up rate per control cycle

For each configuration, derived metrics such as:

- `time_to_scale_up_s`
- `time_to_stabilize_s`
- `overshoot_undershoot_area`
- `pod_seconds`

should be recomputed and compared.

This would enable construction of a performance map that relates controller aggressiveness to responsiveness, overshoot magnitude, and resource cost. Such analysis transforms the experiment from observational study into formal controller optimization.

## 2. Sampling and Metrics Resolution Sensitivity

The observed staircase scaling behavior indicates that HPA operates as a discretely sampled proportional controller. However, the internal refresh interval of Metrics Server was not explicitly varied in this experiment.

Future work could:

- Modify or rebuild Metrics Server to adjust metric refresh frequency
- Introduce controlled artificial delay in metric reporting
- Emulate coarse versus fine sampling conditions

Conceptually, the system can be approximated as:


::contentReference[oaicite:0]{index=0}


where:

- \( x_k \) is the replica count at sampling step \( k \)
- \( e_k \) is CPU utilization error
- \( K_p \) represents proportional gain

By experimentally varying the sampling interval, one could quantify how delayed measurements increase tracking error, prolong convergence time, and amplify staircase effects. This would provide empirical validation of discrete-time control theory within Kubernetes.



## 3. Decomposition of Scaling Latency

The divergence between `desiredReplicas` and `currentReplicas` demonstrates that total scaling delay is not solely determined by metric detection. It also depends on cluster infrastructure performance.

Future research should instrument pod lifecycle events to decompose total scaling delay into:

- Metric detection delay
- HPA evaluation delay
- Scheduler queue delay
- Container startup time
- Readiness probe stabilization

Total scaling delay can therefore be expressed as:

Total Delay = Detection Delay + Scheduling Delay + Startup Delay

By quantifying each component, optimization efforts can be targeted more effectively—whether toward faster metric refresh, improved scheduling throughput, or reduced container initialization time.



## 4. Workload Diversity and Robustness Evaluation

The square-wave workload used in this experiment isolates scaling transitions clearly. However, real-world systems exhibit:

- Gradual ramps
- Random micro-bursts
- Diurnal traffic cycles
- Stochastic request distributions

Future experiments should introduce varied workload shapes and evaluate changes in:

- `overshoot_undershoot_area`
- `time_to_stabilize_s`
- `pod_cpu_std_dev`

This would test whether proportional scaling remains stable and efficient under noisy or unpredictable load conditions. Such analysis would provide a broader understanding of HPA robustness beyond deterministic workloads.



## 5. Infrastructure and Multi-Tenant Effects

The current experiment was conducted in a homogeneous Kind environment. Production clusters introduce additional variability, including:

- Heterogeneous node performance
- Background workloads
- Resource contention
- Network-induced latency

Future work should deploy the same experiment in multi-tenant or cloud-managed environments to examine how infrastructure variability affects:

- Convergence time
- Replica divergence duration
- CPU utilization dispersion

Understanding these factors would clarify how closely laboratory observations align with production-scale behavior.



## 6. Cost and Energy-Aware Autoscaling Evaluation

The `pod_seconds` metric provides a proxy for resource consumption but does not directly represent financial or environmental cost.

Future research could extend analysis to include:

- Cloud provider pricing models (cost per CPU-hour)
- Energy consumption per replica
- Carbon emission estimation

By mapping scaling behavior to real economic and environmental metrics, the study could evolve from performance analysis into cost-aware autoscaling evaluation.

Together, these extensions would deepen theoretical insight, enhance controller tuning methodology, and bridge empirical experimentation with production-scale optimization of native Kubernetes autoscaling.

# Future Work (PCM-Focused)
The PCM experiment demonstrates that Prometheus Custom Metrics significantly enhance autoscaling flexibility and responsiveness compared to native resource-based scaling. However, this flexibility introduces additional architectural complexity, distributed latency, and observability overhead. The following directions outline meaningful extensions of Prometheus-driven autoscaling research.

## 1. End-to-End Metric Pipeline Latency Decomposition

Unlike native resource metrics, PCM introduces a multi-stage metric pipeline:

Application → Prometheus Scrape → Time-Series Storage → PromQL Evaluation → Prometheus Adapter → HPA → Scheduler → Pod Startup

Each stage contributes measurable delay before a scaling decision materializes as running replicas.

Future work should instrument and timestamp each pipeline stage to formally express total scaling latency as:

Total Delay = Scrape Interval + Query Evaluation + Adapter Delay + HPA Sync + Pod Startup

By decomposing this latency, it becomes possible to determine whether scaling delay is dominated by:

- Scrape frequency
- PromQL window size
- Adapter polling interval
- Cluster actuation time

Such analysis transforms PCM from a functional enhancement into a measurable distributed control system whose bottlenecks can be systematically optimized.

## 2. Advanced PromQL Signal Engineering

The current PCM implementation scales primarily on HTTP request rate transformed via PromQL. However, Prometheus enables sophisticated signal engineering beyond simple rate metrics.

Future research could evaluate scaling signals based on:

- `irate()` for high-frequency responsiveness
- Moving average smoothing windows
- Percentile-based latency metrics (e.g., p95 response time)
- Error-rate-based scaling triggers
- Queue-depth-driven scaling
- Composite metrics combining throughput and latency

These transformations effectively redefine the control input to HPA. Instead of scaling on raw resource saturation, the system can scale on workload intent or user-perceived performance.

By comparing derived metrics such as:

- `time_to_scale_up_s`
- `overshoot_undershoot_area`
- `pod_seconds`
- Replica divergence duration

across different PromQL formulations, one could identify signal designs that produce faster convergence with minimal instability.

This shifts PCM research from “custom metric usage” to “control-signal optimization.”

## 3. Hybrid and Multi-Metric Control Strategies

The PCM-CH configuration demonstrates hybrid scaling using CPU and HTTP metrics with a max-selection rule. While effective, this approach represents only one possible control strategy.

Future work could explore:

- Weighted combinations of metrics
- Conditional scaling policies
- Threshold-triggered hybrid logic
- Hierarchical scaling (traffic triggers scale-up, CPU governs scale-down)
- Priority-based metric arbitration

Such strategies can be evaluated using control-performance metrics including:

- Integral tracking error
- Convergence time
- Maximum replica count
- Resource cost (`pod_seconds`)

These experiments would determine whether hybrid strategies can outperform single-metric proportional scaling in both responsiveness and efficiency.

## 4. Observability Overhead and Monitoring Cost Analysis

Prometheus-based autoscaling introduces non-trivial overhead:

- CPU consumption for scraping
- Memory usage for time-series retention
- Network traffic from frequent scrapes
- Adapter query execution cost

Reducing scrape interval improves responsiveness but increases monitoring overhead.

Future research should quantify:

- Prometheus CPU and memory usage vs scrape interval
- Adapter query latency under load
- Impact of high-cardinality metrics
- Cluster-wide resource overhead from aggressive scraping

This would enable identification of an optimal operating point balancing responsiveness and observability cost.

## 5. Production-Scale and Multi-Tenant Validation

The current PCM experiment was conducted in a controlled cluster environment. Production deployments introduce additional variables:

- High metric cardinality
- Shared Prometheus instances
- Network congestion
- Multi-tenant resource contention

Future validation should evaluate:

- Metric propagation delay under cluster load
- Adapter responsiveness during high query concurrency
- Stability under noisy or bursty multi-application workloads

Such evaluation would determine whether PCM remains stable and performant in real-world, production-scale environments.

## 6. Formal Distributed Control Modeling

The PCM architecture transforms autoscaling into a distributed, multi-stage feedback loop. This system can be abstracted as:

::contentReference[oaicite:0]{index=0}

where:

- \( x_k \) is replica count
- \( e_k \) is custom metric error
- \( T_s \) represents sampling interval
- \( L \) represents pipeline latency

Future work could formalize:

- Effective proportional gain under PromQL transformations
- Stability margins under varying scrape intervals
- Error amplification caused by delayed metrics
- Sensitivity to jitter in metric arrival

By validating theoretical predictions against empirical PCM data, the study could evolve from experimental observation into control-theoretic modeling of distributed autoscaling systems.

Collectively, these directions would advance Prometheus-driven autoscaling from a configuration-level enhancement to a formally understood, tunable distributed control architecture capable of production-grade elasticity optimization.

