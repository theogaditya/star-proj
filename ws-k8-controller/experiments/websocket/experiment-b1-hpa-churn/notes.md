# Experiment-B — Internal Technical Notes

## 1. Design Intent

This experiment isolates the effect of load dynamics.

Workload and HPA configuration are unchanged from Experiment-A.
Only load pattern is modified.

This ensures causality:
Any instability observed is due to load churn, not configuration changes.

---

## 2. Control-Loop Stress

The cyclic pattern is intentionally shorter than:

- Scale-down stabilization window (300s)
- Complete control-loop convergence time

This forces HPA into overlapping scale decisions.

---

## 3. Expected Failure Modes

1. Replica oscillation:
   Rapid scale-up followed by partial scale-down and re-scale-up.

2. Reconnection storm:
   Pods terminated during low phase may still have active sessions.
   Clients reconnect simultaneously.

3. CPU spikes:
   Reconnection bursts increase CPU beyond steady-state levels.

---

## 4. Observability Strategy

Phase transitions are logged to:
phase.log

This allows overlaying vertical markers in plots.

This improves interpretability of oscillation timing.

---

## 5. Scientific Value

Experiment-B establishes that:

CPU-based HPA lacks semantic awareness of persistent sessions.

This motivates architectural changes rather than metric tuning alone.