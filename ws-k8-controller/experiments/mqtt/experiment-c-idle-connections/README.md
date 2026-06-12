# Experiment C — Idle Connections: HPA vs STAR (MQTT)

## Purpose
Demonstrate the **idle connection problem**: clients that are connected but not publishing generate zero CPU load. HPA sees low CPU and scales down even though many persistent sessions are active. STAR holds steady.

## Scenario
1. Start 2 broker replicas under **both** HPA and STAR (run sequentially in separate Kind clusters)
2. Connect 400 idle MQTT clients (PING_INTERVAL=120s — very infrequent keepalive)
3. Observe for 600s:
   - **HPA**: CPU ≈ 0% → scales down to 1 → 50% clients disconnect
   - **STAR**: active_connections ≈ 400 → holds at ceil(400/150) = 3 replicas

## Run
```bash
bash scripts/run-experiment-mqtt-c.sh
```

## Duration
~25 minutes total (two sequential 600s runs + cluster setup each time)

## Expected Results
- HPA sub-run: replicas fall 2→1, connections halve abruptly
- STAR sub-run: replicas hold at 3, connections remain flat
