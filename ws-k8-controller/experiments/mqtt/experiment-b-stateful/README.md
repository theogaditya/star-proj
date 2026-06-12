# Experiment B — STAR StatefulAutoscaler (MQTT)

## Purpose
Demonstrate that the STAR controller correctly scales MQTT broker replicas **proportional to active connections**, and that clients are **not disconnected** during scale-down thanks to the drain lifecycle hook.

## Scenario (Two Phases)
1. **Phase 1** (300s): Ramp 600 MQTT clients → controller scales to 4 replicas (600/150 = 4)
2. **Phase 2** (300s): Drop to 150 clients → controller scales down to 1 after 120s stabilisation window
3. Scale-down triggers `/drain` → no abrupt client disconnections

## Run
```bash
bash scripts/run-experiment-mqtt-b.sh
```

## Duration
~15 minutes total (300s Phase 1 + 300s Phase 2 + cluster setup)

## Expected Results
- Replicas rise to 4 within ~2 min of ramp completing
- `active_connections` stays proportional; no sudden drops
- Replica step-down does NOT correlate with connection-count drops (drain works)
