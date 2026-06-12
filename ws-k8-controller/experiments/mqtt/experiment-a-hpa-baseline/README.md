# Experiment A — HPA Baseline (MQTT)

## Purpose
Establish a baseline showing that **CPU-based HPA cannot handle MQTT connection scaling** correctly.
With low CPU utilisation but many persistent clients, HPA scales down and kills brokers, causing mass client disconnections.

## Scenario
1. Start 1 broker replica with CPU-based HPA
2. Ramp 600 MQTT clients over 60s (persistent idle connections)
3. HPA sees CPU < 50% → scales down → kills broker pods → clients disconnect
4. Measure: disconnection events, replica count timeline, connection drops

## Run
```bash
bash scripts/run-experiment-mqtt-a.sh
```

## Duration
~12 minutes total (600s load + 120s observation buffer)

## Expected Results
- Replicas drop to 1 because CPU ≈ 0%
- `active_connections` drops sharply when pods are killed
- Load generator logs show burst of disconnection messages
