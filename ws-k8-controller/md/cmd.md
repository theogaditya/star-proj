# WebSocket Server
### Build Image

```bash
cd future-work/workloads/websocket/app
docker build -t websocket-server .
```

### Run Container

```bash
docker run -p 8765:8765 -p 8080:8080 websocket-server
```

### Verify Metrics

```bash
curl http://localhost:8080/metrics
```
# WebSocket Load Generator
### Build Image

```bash
cd future-work/load-generator/websocket-loadgen
docker build -t websocket-loadgen .
```

### Run 1000 Clients

```bash
docker run websocket-loadgen ws://host.docker.internal:8765 1000
```

If running on Linux (no `host.docker.internal`), use:

```bash
docker run --network="host" websocket-loadgen ws://localhost:8765 1000
```

# MQTT Broker (Mosquitto)
### Start Broker

```bash
cd future-work/workloads/mqtt/app
docker compose up
```

Broker runs on:
```
localhost:1883
```

# MQTT Load Generator
### Build Image

```bash
cd future-work/load-generator/mqtt-loadgen
docker build -t mqtt-loadgen .
```
### Run 1000 MQTT Clients

```bash
docker run mqtt-loadgen host.docker.internal 1000
```

On Linux:

```bash
docker run --network="host" mqtt-loadgen localhost 1000
```

---

# Quick Summary Table

| Component         | Build Command                         | Run Command                                                        |
| ----------------- | ------------------------------------- | ------------------------------------------------------------------ |
| WebSocket Server  | `docker build -t websocket-server .`  | `docker run -p 8765:8765 -p 8080:8080 websocket-server`            |
| WebSocket LoadGen | `docker build -t websocket-loadgen .` | `docker run websocket-loadgen ws://host.docker.internal:8765 1000` |
| MQTT Broker       | `docker compose up`                   | (same command)                                                     |
| MQTT LoadGen      | `docker build -t mqtt-loadgen .`      | `docker run mqtt-loadgen host.docker.internal 1000`                |


# WebSocket Workload Setup on Kind

## 1. Build Docker Image

cd future-work/workloads/websocket/app
docker build -t websocket-server:latest .

---

## 2. Load Image into Kind Cluster

kind load docker-image websocket-server:latest

---

## 3. Deploy WebSocket Workload

cd ../k8s
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

---

## 4. Verify Deployment

kubectl get pods
kubectl get svc

---

## 5. Port Forward WebSocket Service

kubectl port-forward svc/websocket-service 8765:8765

---

## 6. Port Forward Metrics Endpoint

kubectl port-forward svc/websocket-service 8080:8080

---

## 7. Verify Metrics

curl http://localhost:8080/metrics


# Apply Prometheus Stack

From root of project:
```bash
kubectl apply -f monitoring/prometheus/namespace.yaml
kubectl apply -f monitoring/prometheus/rbac.yaml
kubectl apply -f monitoring/prometheus/configmap.yaml
kubectl apply -f monitoring/prometheus/deployment.yaml
kubectl apply -f monitoring/prometheus/service.yaml
```
### Wait for readiness:
```bash
kubectl -n monitoring rollout status deployment/prometheus
```
### Port-forward (recommended):
```bash
kubectl -n monitoring port-forward svc/prometheus 9090:9090
```
### Open:
```bash
http://localhost:9090
```


### Check Logs During Experiment
```bash
kubectl get hpa -w
```

```bash
 watch -n 2 ls -lh results/raw/websocket/experiment-b2-hpa-churn-instrumented/


---

# Experiment-C: Connection-Based Autoscaling (Custom Controller)

Demonstrates that the StatefulAutoscaler scales on **active connections**, not CPU.
Load pattern: RAMP_UP (600 idle connections) -> SUSTAINED (CPU stays low) -> RAMP_DOWN (connections drop, replicas follow).

## Run (Fully Automated)

From project root:
```bash
bash scripts/run-experiment-c.sh
```

This single command does everything end-to-end:
1. Deletes and recreates the kind cluster
2. Installs Metrics Server and Prometheus
3. Builds and loads all Docker images (server, load generator, controller)
4. Deploys the WebSocket workload (`CPU_WORK=0`)
5. Installs the CRDs and deploys the custom controller
6. Applies the `StatefulAutoscaler` CR
7. Runs the 3-phase load (RAMP_UP / SUSTAINED / RAMP_DOWN)
8. Collects CPU, replica, and connection metrics throughout
9. Runs analysis and generates plots
10. Archives results and deletes the cluster

## Manual Build Commands (individual steps)

### Connection-Based Load Generator
```bash
cd load-generator/websocket-client/connection-based
docker build -t ws-conn-loadgen:latest .
kind load docker-image ws-conn-loadgen:latest --name stateful-exp
```

### Custom Controller
```bash
cd controller
make docker-build IMG=controller:latest
kind load docker-image controller:latest --name stateful-exp
make install          # Install CRDs
make deploy IMG=controller:latest
```

### Apply StatefulAutoscaler CR
```bash
kubectl apply -f controller/config/samples/autoscaling_v1alpha1_statefulautoscaler.yaml
```

### Run Analysis Only (after collecting raw data)
```bash
export RAW_DIR=results/raw/websocket/experiment-c-stateful
export PROCESSED_DIR=results/processed/websocket/experiment-c-stateful
python3 analysis/experiment-c/parse_logs_experiment_c.py
python3 analysis/experiment-c/plot_experiment_c.py
```

## Output

| Path | Contents |
|------|----------|
| `results/raw/websocket/experiment-c-stateful/` | `cpu.log`, `replicas.log`, `pods.log`, `phase.log`, `prometheus_dump.csv` |
| `results/processed/websocket/experiment-c-stateful/` | `cpu.csv`, `replicas.csv`, `connections.csv` |
| `results/processed/websocket/experiment-c-stateful/plots/` | `cpu.png`, `connections.png`, `replicas.png`, `combined.png` |
| `results/tar/` | Compressed archive of results |

## Key Plots to Inspect

- **`cpu.png`** — CPU stays flat/low throughout (not the scaling signal)
- **`connections.png`** — Clear ramp-up, sustained plateau, then drop
- **`replicas.png`** — Replicas track connections, not CPU
- **`combined.png`** — Overlay of all three, proving the decoupling visually

## Watch Live Progress During Run
```bash
# Replica count
watch -n 2 kubectl get deployment websocket-server

# Active connections from Prometheus
watch -n 5 'curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq .data.result'

# Controller logs
kubectl -n controller-system logs -l control-plane=controller-manager -f
```

---

# Experiment-B3: CPU-Based HPA with Idle Connections (Control Experiment)

Proves that CPU-based HPA is **blind** to active connections. Same idle load as Experiment-C, but HPA sees low CPU and never scales up.

## Run (Fully Automated)

```bash
bash scripts/run-experiment-b3.sh
```

Same pipeline as Experiment-C: cluster setup, Prometheus, workload (CPU_WORK=0), loadgen (600 idle connections), metric collection, analysis, cleanup. Uses HPA instead of custom controller.

## Output

| Path | Contents |
|------|----------|
| `results/raw/websocket/experiment-b3-hpa-idle-connections/` | `cpu.log`, `hpa.log`, `pods.log`, `phase.log`, `prometheus_dump.csv` |
| `results/processed/websocket/experiment-b3-hpa-idle-connections/` | `cpu.csv`, `replicas.csv`, `connections.csv` |
| `results/processed/websocket/experiment-b3-hpa-idle-connections/plots/` | `cpu.png`, `connections.png`, `replicas.png`, `combined.png` |

## Expected Findings

- **`replicas.png`** -- Replicas stay flat at minReplicas=2 (HPA never scales)
- **`connections.png`** -- 600 connections pile onto 2 pods
- **`combined.png`** -- Connections high, CPU low, replicas flat -- HPA failure visualized

Compare directly with Experiment-C where the custom controller scaled to ~6 replicas.