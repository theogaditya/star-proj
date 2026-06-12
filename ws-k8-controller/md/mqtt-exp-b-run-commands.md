# MQTT Experiment B — Run Commands Cheatsheet

After all code changes are in place, here are the exact commands to run.

---

## 1. Verify the controller builds

```bash
cd /home/aditya/cohort/startproj/ws-k8-controller/controller
go build ./...
```

Expected: **no output, exit 0**.

---

## 2. Regenerate CRD manifests (only needed when types change)

```bash
cd /home/aditya/cohort/startproj/ws-k8-controller/controller
make generate && make manifests
```

> Run this again if you ever edit `statefulautoscaler_types.go`.

---

## 3. Run the experiment

```bash
cd /home/aditya/cohort/startproj/ws-k8-controller
bash experiments/mqtt/experiment-b-stateful/run.sh
```

**Total runtime:** ~840 s (~14 min).  
Results land in `results/raw/mqtt/experiment-b-stateful/`.

### What to watch live (separate terminal):

```bash
# Pod replica count changes in real time
watch -n5 kubectl get pods -l app=mqtt-broker

# Controller drain decisions
kubectl logs -n controller-system deployment/controller-controller-manager -f

# StatefulAutoscaler status (drain state)
watch -n5 kubectl get statefulautoscaler mqtt-autoscaler -o yaml
```

---

## 4. Parse logs → CSV

```bash
cd /home/aditya/cohort/startproj/ws-k8-controller
python3 analysis/mqtt/parse_logs_mqtt.py \
  results/raw/mqtt/experiment-b-stateful \
  results/raw/mqtt/experiment-b-stateful/out.csv
```

Produces:
- `out.csv` — time, replicas, connections, cpu, memory, **drain_active** column
- `out_perpod.csv` — per-pod connection counts over time
- `out_phases.json` — phase boundaries (relative seconds)
- `out_drain_events.json` — drain event timestamps (if drain triggered)

---

## 5. Plot results

```bash
python3 analysis/mqtt/plot_experiment_mqtt.py \
  results/raw/mqtt/experiment-b-stateful/out.csv \
  results/raw/mqtt/experiment-b-stateful/plot.png
```

---

## 6. Quick sanity checks after the run

### Check connected clients (Phase 1 win)
```bash
grep "\[STATUS\]" results/raw/mqtt/experiment-b-stateful/loadgen-phase1.log | tail -5
```
**Target:** `connected=XXX/1000` where XXX >> 339.

### Check per-pod distribution (Phase 2 win)
```bash
tail -5 results/raw/mqtt/experiment-b-stateful/perpod_connections.log
```
**Target:** connections spread across pods (not all on one).

### Check drain happened before scale-down (Phase 3 win)
```bash
grep -E "drain|scale" results/raw/mqtt/experiment-b-stateful/controller.log | head -40
```
**Target:** `Starting drain on pod` appears **before** `Scaling DOWN` in the log.

### Check no cliff-drop in Phase 3
```bash
python3 - <<'EOF'
import csv
rows = list(csv.DictReader(open("results/raw/mqtt/experiment-b-stateful/out.csv")))
for i in range(1, len(rows)):
    drop = float(rows[i-1]["active_connections"]) - float(rows[i]["active_connections"])
    if drop > 50:
        print(f"  t={rows[i]['time_s']}s: drop of {drop:.0f} connections")
EOF
```
**Target:** No single 5-second window shows more than ~50 connection drop.

---

## What proves each failure is solved

| Failure | Evidence to look for |
|---------|---------------------|
| ❶ Scale-up Blindness | `loadgen-phase1.log` final STATUS >> 339; replicas reach 7 in `out.csv` |
| ❷ No Redistribution | `out_perpod.csv` shows per-pod counts rebalancing after drain |
| ❸ Violent Disconnect | `controller.log` shows drain before scale-down; no cliff in `out.csv` |
