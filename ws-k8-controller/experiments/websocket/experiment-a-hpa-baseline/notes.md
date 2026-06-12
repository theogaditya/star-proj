# Experiment-A — Internal Notes & Technical Observations

## 1. Experimental Integrity

- Fresh cluster used per run.
- No contamination from previous experiments.
- Metrics-server warm-up handled explicitly.
- Prometheus service explicitly defined.
- Logging synchronized with timestamps.
- Processed CSV generation validated.

Data pipeline is stable and reproducible.

---

## 2. Scaling Behavior Observations

### Scale-Up

- HPA reacted within expected time window after CPU crossed 60%.
- Replica transitions were gradual (2 → 4 → 5).
- No aggressive overshoot.
- CPU utilization per pod dropped after scale-up, indicating proper load distribution.

Conclusion:
Scale-up responsiveness is adequate under monotonic load.

---

### Scale-Down

- HPA respected default stabilization window (~300s).
- Pods remained over-provisioned briefly after load drop.
- No abrupt oscillation observed.
- Replica count returned cleanly to baseline (2).

Conclusion:
Stabilization window prevents oscillation under steady-state behavior.

---

## 3. CPU–Connection Correlation

- CPU usage scaled proportionally with active connections.
- CPU metric is a reasonable proxy for connection load in this workload.
- No unexpected CPU noise detected.
- No delayed CPU spikes after load termination.

Observation:
CPU-based scaling is valid under steady persistent load.

Limitation:
CPU does not encode connection state semantics.

---

## 4. No Instability Observed

Experiment-A intentionally avoided:

- Abrupt load cycling
- Rapid churn
- Multi-phase bursts

As expected:

- No reconnection storms occurred.
- No replica oscillation observed.
- No scaling thrashing.

This confirms the baseline system is stable.

---

## 5. Architectural Insight

The default HPA model assumes:

- Stateless workload
- Metric sufficiency (CPU)
- Safe termination semantics

However, persistent connection systems introduce:

- Long-lived TCP sessions
- Stateful client-server relationships
- Potential disruption during pod termination

Experiment-A does not stress these properties.

This motivates Experiment-B.

---

## 6. What This Experiment Validates

- Infrastructure correctness
- Logging correctness
- Monitoring correctness
- Autoscaler behavior under monotonic load
- Reproducibility of baseline

Experiment-A is not designed to demonstrate failure.
It is designed to establish normal behavior.

---

## 7. Known Limitations

- No measurement of connection drops during scale-down.
- No measurement of reconnection latency.
- No per-pod connection distribution analysis.
- No jitter or burst stress.
- No oscillation-triggering load.

These limitations are intentional and addressed in Experiment-B.

---

## 8. Research Position After Experiment-A

We can now assert:

"Default CPU-based HPA scales correctly for steady persistent workloads."

The next step is to test behavior under dynamic churn.

Experiment-A provides a clean baseline for comparison.

---

## 9. Readiness for Experiment-B

All prerequisites are satisfied:

- Stable infrastructure
- Stable logging
- Accurate parsing
- Clear graph generation
- Controlled cluster lifecycle

The system is ready to introduce cyclic load patterns to evaluate instability behavior.

Experiment-A is formally concluded.