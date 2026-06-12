# MQTT Broker Experiments — Implementation Plan

## Overview

This document is the **full implementation plan** for testing the STAR
`StatefulAutoscaler` custom controller against an MQTT broker workload.  The
WebSocket experiments already demonstrated the core problem (HPA kills brokers
that hold active TCP connections) and the core solution (connection-aware
scaling).  These MQTT experiments replicate and **generalise** that argument by
showing the same behaviour with a second, industrially important stateful
protocol.

### Research Questions Being Answered

| # | Question |
|---|----------|
| RQ-M1 | Does HPA disrupt active MQTT sessions when it scales down? |
| RQ-M2 | Does the STAR controller scale MQTT brokers proportionally to active connections without disrupting sessions? |
| RQ-M3 | Does HPA waste resources (over-provision) when MQTT clients are idle, while the STAR controller holds replicas steady? |

---

## Architecture

```
                        ┌─────────────────────────────────────────┐
                        │              Kind Cluster                │
                        │                                          │
   ┌──────────────┐     │  ┌──────────────┐   ┌────────────────┐ │
   │  mqtt-loadgen│────▶│  │ mqtt-broker  │──▶│  /metrics:8080 │ │
   │  (Job/Pod)   │     │  │  (Dep. 1-N)  │   │ active_connections │
   └──────────────┘     │  └──────┬───────┘   └───────┬────────┘ │
                        │         │1883               scrape      │
                        │  ┌──────▼───────┐   ┌───────▼────────┐ │
                        │  │ mqtt-service │   │   Prometheus   │ │
                        │  │ (ClusterIP)  │   │ :9090          │ │
                        │  └──────────────┘   └───────┬────────┘ │
                        │                             query       │
                        │  ┌──────────────────────────▼────────┐ │
                        │  │     StatefulAutoscaler CR          │ │
                        │  │     (STAR Controller)              │ │
                        │  │     or HPA (baseline)              │ │
                        │  └────────────────────────────────────┘ │
                        └─────────────────────────────────────────┘
```

### Key Design Decision — Custom Python MQTT Broker

The existing `workloads/mqtt/` directory contains only a vanilla
`eclipse-mosquitto` Deployment.  Vanilla Mosquitto does **not** expose a
Prometheus `/metrics` endpoint and does not support the `/drain` HTTP endpoint
the STAR controller depends on.

Following the exact same pattern as the WebSocket server (`workloads/websocket/app/server.py`),
the MQTT broker will be a **custom Python application** built with
[`amqtt`](https://amqtt.readthedocs.io/) (async Python MQTT broker library).
This gives full programmatic control over:

- Counting active MQTT sessions (`active_connections`)
- Exposing `/metrics` on port 8080 in Prometheus text format
- Exposing `/drain` on port 8080 — when called, the broker rejects new
  connections and waits for existing clients to disconnect

---

## Part 1 — Shared Infrastructure

All three experiments reuse the same broker image, load generator image, and
Prometheus stack.  These are built once and loaded into every Kind cluster.

---

## Part 2 — File Structure

```
future-work/
├── workloads/
│   └── mqtt/
│       ├── app/                          ← NEW: custom Python MQTT broker
│       │   ├── Dockerfile
│       │   ├── requirements.txt
│       │   └── broker.py
│       ├── k8s/
│       │   ├── deployment.yml            ← UPDATED: point to new image
│       │   ├── service.yml               ← keep existing
│       │   └── hpa.yml                   ← NEW: Kubernetes HPA manifest
│       └── metrics/                      ← (inherited structure, empty dir)
│
├── load-generator/
│   └── mqtt-client/
│       ├── Dockerfile                    ← NEW
│       ├── requirements.txt              ← NEW
│       └── client.py                     ← NEW: persistent MQTT load generator
│
├── experiments/
│   └── mqtt/
│       ├── experiment-a-hpa-baseline/
│       │   ├── README.md
│       │   ├── config.env
│       │   ├── hpa-config.yaml
│       │   └── run.sh
│       ├── experiment-b-stateful/
│       │   ├── README.md
│       │   ├── config.env
│       │   ├── statefulautoscaler.yaml
│       │   └── run.sh
│       └── experiment-c-idle-connections/
│           ├── README.md
│           ├── config.env
│           ├── hpa-config.yaml
│           ├── statefulautoscaler.yaml
│           └── run.sh
│
├── analysis/
│   └── mqtt/
│       ├── parse_logs_mqtt.py
│       └── plot_experiment_mqtt.py
│
└── scripts/
    ├── run-experiment-mqtt-a.sh          ← NEW thin wrapper
    ├── run-experiment-mqtt-b.sh          ← NEW thin wrapper
    └── run-experiment-mqtt-c.sh          ← NEW thin wrapper
```

---

## Part 3 — MQTT Broker Application

### `workloads/mqtt/app/broker.py`

The broker is built with `amqtt`.  It mirrors `workloads/websocket/app/server.py`
pattern: an MQTT broker port (1883) plus a metrics/control HTTP port (8080).

```python
"""
broker.py — Custom MQTT broker for STAR controller MQTT experiments.

Exposes:
  :1883  MQTT (via amqtt)
  :8080  /metrics   → Prometheus text: active_connections <N>
  :8080  /drain     → POST; broker stops accepting new connections
"""
import asyncio
import os
from aiohttp import web
from amqtt.broker import Broker
from amqtt.session import IncomingApplicationMessage  # noqa: F401 (for type hints)

ACTIVE_CONNECTIONS = 0
DRAINING = False

# ──────────────────────────────────────────────────
# Hooks called by amqtt lifecycle events
# ──────────────────────────────────────────────────

async def on_client_connected(client_id: str):
    global ACTIVE_CONNECTIONS
    ACTIVE_CONNECTIONS += 1

async def on_client_disconnected(client_id: str):
    global ACTIVE_CONNECTIONS
    if ACTIVE_CONNECTIONS > 0:
        ACTIVE_CONNECTIONS -= 1

# ──────────────────────────────────────────────────
# amqtt broker configuration
# ──────────────────────────────────────────────────

BROKER_CONFIG = {
    "listeners": {
        "default": {
            "type": "tcp",
            "bind": "0.0.0.0:1883",
        }
    },
    "sys_interval": 10,          # publishes $SYS stats every 10 s
    "allow_anonymous": True,     # no auth for experiment simplicity
    "plugins": [],
}

class TrackingBroker(Broker):
    """Subclass of amqtt Broker that hooks connect/disconnect events."""

    async def client_connected(self, listener_name, reader, writer):
        # Called by amqtt when a new TCP connection is received.
        global DRAINING
        if DRAINING:
            writer.close()
            return
        await super().client_connected(listener_name, reader, writer)

    async def _do_connect(self, client_session, *args, **kwargs):
        result = await super()._do_connect(client_session, *args, **kwargs)
        await on_client_connected(client_session.client_id)
        return result

    async def _do_disconnect(self, client_session, *args, **kwargs):
        result = await super()._do_disconnect(client_session, *args, **kwargs)
        await on_client_disconnected(client_session.client_id)
        return result


# ──────────────────────────────────────────────────
# HTTP metrics / control server (port 8080)
# ──────────────────────────────────────────────────

async def metrics_handler(request):
    text = f"active_connections {ACTIVE_CONNECTIONS}\n"
    return web.Response(text=text, content_type="text/plain")


async def drain_handler(request):
    global DRAINING
    DRAINING = True
    return web.Response(text="draining\n")


async def start_http_server():
    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_post("/drain", drain_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()


# ──────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────

async def main():
    broker = TrackingBroker(config=BROKER_CONFIG)
    await asyncio.gather(
        broker.start(),
        start_http_server(),
    )


if __name__ == "__main__":
    asyncio.run(main())
```

### `workloads/mqtt/app/requirements.txt`

```
amqtt==0.11.0
aiohttp==3.9.5
```

### `workloads/mqtt/app/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY broker.py .
EXPOSE 1883 8080
CMD ["python", "broker.py"]
```

---

## Part 4 — MQTT Load Generator

### `load-generator/mqtt-client/client.py`

The client connects `N` persistent MQTT clients.  Each client subscribes to
a topic and keeps its connection alive by sending a small ping message every
`PING_INTERVAL` seconds.  The total number of connections is controlled by the
`CLIENTS` environment variable so the same image is reusable across experiments.

```python
"""
client.py — MQTT persistent load generator.

Environment variables:
  BROKER_HOST   default: mqtt-service
  BROKER_PORT   default: 1883
  CLIENTS       default: 600    total persistent clients
  PING_INTERVAL default: 30     seconds between keepalive publishes
  RAMP_SECONDS  default: 60     seconds over which to ramp up connections
"""
import asyncio
import os
import random
import string
import time

import paho.mqtt.client as mqtt_client

BROKER_HOST   = os.getenv("BROKER_HOST", "mqtt-service")
BROKER_PORT   = int(os.getenv("BROKER_PORT", "1883"))
CLIENTS       = int(os.getenv("CLIENTS", "600"))
PING_INTERVAL = float(os.getenv("PING_INTERVAL", "30"))
RAMP_SECONDS  = float(os.getenv("RAMP_SECONDS", "60"))

connected_count = 0
stop_event = asyncio.Event()


def _random_id(length=10) -> str:
    return "loadgen-" + "".join(random.choices(string.ascii_lowercase, k=length))


async def run_client(client_id: str, delay: float):
    """Connect one MQTT client, subscribe, and ping periodically."""
    global connected_count

    await asyncio.sleep(delay)

    loop = asyncio.get_event_loop()

    client = mqtt_client.Client(client_id=client_id, clean_session=True)
    client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    # Wait for connection
    deadline = time.monotonic() + 30
    while not client.is_connected() and time.monotonic() < deadline:
        await asyncio.sleep(0.1)

    if not client.is_connected():
        print(f"[WARN] {client_id} failed to connect", flush=True)
        client.loop_stop()
        return

    connected_count += 1
    client.subscribe(f"loadgen/{client_id}")

    try:
        while not stop_event.is_set():
            client.publish(f"loadgen/{client_id}/ping", "ping", qos=0)
            await asyncio.sleep(PING_INTERVAL)
    finally:
        client.disconnect()
        client.loop_stop()
        connected_count -= 1
        print(f"[INFO] {client_id} disconnected. Total: {connected_count}", flush=True)


async def main():
    print(f"[INFO] Ramping up {CLIENTS} clients over {RAMP_SECONDS}s to {BROKER_HOST}:{BROKER_PORT}", flush=True)

    delay_step = RAMP_SECONDS / CLIENTS
    tasks = []
    for i in range(CLIENTS):
        cid = _random_id()
        tasks.append(asyncio.create_task(run_client(cid, delay=i * delay_step)))

    # Print status every 10 s
    async def status_loop():
        while not stop_event.is_set():
            print(f"[STATUS] connected={connected_count}/{CLIENTS}", flush=True)
            await asyncio.sleep(10)

    tasks.append(asyncio.create_task(status_loop()))

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
```

### `load-generator/mqtt-client/requirements.txt`

```
paho-mqtt==1.6.1
```

### `load-generator/mqtt-client/Dockerfile`

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY client.py .
CMD ["python", "-u", "client.py"]
```

---

## Part 5 — Kubernetes Manifests

### `workloads/mqtt/k8s/deployment.yml` (updated)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mqtt-broker
  labels:
    app: mqtt-broker
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mqtt-broker
  template:
    metadata:
      labels:
        app: mqtt-broker
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port:   "8080"
        prometheus.io/path:   "/metrics"
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: mqtt-broker
          image: mqtt-broker:latest
          imagePullPolicy: Never
          ports:
            - name: mqtt
              containerPort: 1883
            - name: metrics
              containerPort: 8080
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "256Mi"
          lifecycle:
            preStop:
              httpGet:
                path: /drain
                port: 8080
```

> **Note**: The `preStop` lifecycle hook calls `/drain` before Kubernetes sends
> `SIGTERM`.  This mirrors the WebSocket server drain pattern exactly.  The
> `terminationGracePeriodSeconds: 60` gives existing clients time to reconnect
> elsewhere  before the pod fully terminates.

### `workloads/mqtt/k8s/service.yml` (unchanged)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mqtt-service
spec:
  type: ClusterIP
  selector:
    app: mqtt-broker
  ports:
    - name: mqtt
      port: 1883
      targetPort: 1883
    - name: metrics
      port: 8080
      targetPort: 8080
```

### `workloads/mqtt/k8s/hpa.yml` (new — used by Experiment A and C)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: mqtt-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: mqtt-broker
  minReplicas: 1
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50
```

---

## Part 6 — Prometheus Configuration

The Prometheus `configmap.yaml` (at `monitoring/prometheus/configmap.yaml`,
shared across all experiments) must scrape the `mqtt-broker` pods.  Add or
ensure the following scrape job exists:

```yaml
- job_name: mqtt-broker
  kubernetes_sd_configs:
    - role: pod
  relabel_configs:
    - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
      action: keep
      regex: "true"
    - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
      action: replace
      target_label: __address__
      regex: (.+)
      replacement: "${1}"
    - source_labels: [__meta_kubernetes_pod_ip,
                      __meta_kubernetes_pod_annotation_prometheus_io_port]
      separator: ":"
      target_label: __address__
    - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_path]
      action: replace
      target_label: __metrics_path__
      regex: (.+)
```

The STAR controller's `prometheus.go` already queries:

```
sum(active_connections)
```

This exact metric name is exposed by `broker.py`'s `/metrics` endpoint, so the
controller works without any modification.

---

## Part 7 — StatefulAutoscaler CR

### `experiments/mqtt/experiment-b-stateful/statefulautoscaler.yaml`

```yaml
apiVersion: autoscaling.star.local/v1alpha1
kind: StatefulAutoscaler
metadata:
  name: mqtt-autoscaler
  namespace: default
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: mqtt-broker

  minReplicas: 1
  maxReplicas: 5

  # Scale up when avg connections per pod exceeds this
  targetConnectionsPerPod: 150

  # Limit how aggressively we scale up/down per reconciliation loop
  maxScaleUpStep:   2
  maxScaleDownStep: 1

  # Cooldown: do not scale down until load has been low for 120s
  scaleUpCooldownSeconds:   30
  scaleDownCooldownSeconds: 120

  drain:
    enabled:             true
    timeoutSeconds:      45
    maxConcurrentDrains: 2
```

---

## Experiment A — HPA Baseline (`experiment-a-hpa-baseline`)

### Purpose

Establish a baseline showing that **CPU-based HPA cannot handle MQTT connection
scaling** correctly.  With low CPU utilisation but many persistent clients, HPA
scales down and kills brokers, causing mass client disconnections.

### Scenario

1. Start 1 broker replica.
2. Ramp 600 MQTT clients over 60 s (all connections are idle — no heavy CPU
   load, just persistent TCP sessions).
3. Let HPA observe CPU < 50 %.  It will eventually scale down to `minReplicas=1`.
4. The pod that is killed terminates all client sessions on it.
5. Measure: number of disconnection events, reconnection storms, connection
   drop timestamps.

### Config — `experiments/mqtt/experiment-a-hpa-baseline/config.env`

```bash
CLUSTER_NAME=mqtt-exp-a
RESULT_DIR=results/raw/mqtt/experiment-a-hpa
DURATION=600          # seconds the load runs after ramp
SCALE_DOWN_BUFFER=120 # wait after experiment before cleanup
CLIENTS=600
RAMP_SECONDS=60
PING_INTERVAL=30
```

### `experiments/mqtt/experiment-a-hpa-baseline/run.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$PROJECT_ROOT"

source "$SCRIPT_DIR/config.env"

mkdir -p "$RESULT_DIR"

# ── Archive previous results ──────────────────────────────────────
if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR")" ]; then
  ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
  mkdir -p "$ARCHIVE_DIR"
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  tar -czf "$ARCHIVE_DIR/experiment-a-mqtt_${TIMESTAMP}.tgz" -C "$RESULT_DIR" .
  rm -rf "$RESULT_DIR"
  mkdir -p "$RESULT_DIR"
  echo "[*] Archived previous results"
fi

echo "=============================================="
echo " Experiment-A MQTT: HPA Baseline"
echo "=============================================="

# ── Cluster setup ─────────────────────────────────────────────────
kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

# ── Metrics server ────────────────────────────────────────────────
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
    "--cert-dir=/tmp","--secure-port=10250",
    "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
    "--kubelet-use-node-status-port","--metric-resolution=15s",
    "--kubelet-insecure-tls"]}]'
kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
until kubectl top pods >/dev/null 2>&1; do sleep 5; done
echo "[✓] Metrics ready"

# ── Prometheus ────────────────────────────────────────────────────
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/namespace.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/rbac.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/configmap.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/deployment.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/service.yaml"
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
echo "[✓] Prometheus ready"

# ── Build & load images ───────────────────────────────────────────
docker build -t mqtt-broker:latest   "$PROJECT_ROOT/workloads/mqtt/app"
docker build -t mqtt-loadgen:latest  "$PROJECT_ROOT/load-generator/mqtt-client"
kind load docker-image mqtt-broker:latest  --name "$CLUSTER_NAME"
kind load docker-image mqtt-loadgen:latest --name "$CLUSTER_NAME"
echo "[✓] Images loaded"

# ── Deploy broker + HPA ───────────────────────────────────────────
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/deployment.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/service.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/hpa.yml"
kubectl wait --for=condition=ready pod -l app=mqtt-broker --timeout=180s
echo "[✓] Broker ready"

# ── Start data collectors ─────────────────────────────────────────
(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
  kubectl get pods -l app=mqtt-broker --no-headers >> "$RESULT_DIR/pods.log"
  sleep 5
done) &
PODS_PID=$!

(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/hpa.log"
  kubectl get hpa mqtt-hpa --no-headers >> "$RESULT_DIR/hpa.log"
  sleep 5
done) &
HPA_PID=$!

(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"
  kubectl top pods -l app=mqtt-broker --no-headers 2>/dev/null >> "$RESULT_DIR/cpu.log" || true
  sleep 5
done) &
CPU_PID=$!

# Query Prometheus for active_connections (via port-forward)
kubectl port-forward svc/prometheus -n monitoring 9090:9090 >/dev/null 2>&1 &
PF_PID=$!
sleep 5

(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/active_connections.log"
  curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); \
      r=d['data']['result']; print(r[0]['value'][1] if r else '0')" \
    >> "$RESULT_DIR/active_connections.log" 2>/dev/null || echo "0" >> "$RESULT_DIR/active_connections.log"
  sleep 5
done) &
CONN_PID=$!

# ── Run load generator ────────────────────────────────────────────
kubectl run mqtt-loadgen \
  --image=mqtt-loadgen:latest \
  --restart=Never \
  --image-pull-policy=Never \
  --env="BROKER_HOST=mqtt-service" \
  --env="CLIENTS=$CLIENTS" \
  --env="RAMP_SECONDS=$RAMP_SECONDS" \
  --env="PING_INTERVAL=$PING_INTERVAL"

echo "[*] Load generator started. Running for ${DURATION}s..."
sleep "$DURATION"

# ── Collect load generator logs ───────────────────────────────────
kubectl logs mqtt-loadgen >> "$RESULT_DIR/loadgen.log" 2>/dev/null || true

# ── Scale-down buffer (observe HPA behaviour) ─────────────────────
echo "[*] Waiting ${SCALE_DOWN_BUFFER}s for HPA scale-down observation..."
sleep "$SCALE_DOWN_BUFFER"

# ── Teardown ──────────────────────────────────────────────────────
kill $PODS_PID $HPA_PID $CPU_PID $CONN_PID $PF_PID 2>/dev/null || true
kubectl delete pod mqtt-loadgen --ignore-not-found

echo "[✓] Experiment-A MQTT complete. Results in $RESULT_DIR"
```

---

## Experiment B — STAR Controller (`experiment-b-stateful`)

### Purpose

Demonstrate that the STAR `StatefulAutoscaler` controller correctly scales
MQTT broker replicas **proportional to active connections**, and that clients
are **not disconnected** during scale-down because the drain lifecycle hook
prevents new connections to the terminating pod while existing ones migrate
gracefully.

### Scenario

1. Start with `minReplicas=1`.
2. Ramp 600 MQTT clients over 60 s.
   - Controller detects connections rising → scales up to 4 replicas
     (`targetConnectionsPerPod=150`, 600/150 = 4.0).
3. After 300 s, disconnect 450 clients (the load generator reduces clients to 150).
   - Controller detects connections falling → scales down to 1 after the 120 s
     stabilisation window.
4. Scale-down triggers the `/drain` lifecycle hook → no in-flight disconnect events.

### Config — `experiments/mqtt/experiment-b-stateful/config.env`

```bash
CLUSTER_NAME=mqtt-exp-b
RESULT_DIR=results/raw/mqtt/experiment-b-stateful
PHASE1_CLIENTS=600
PHASE2_CLIENTS=150
RAMP_SECONDS=60
PHASE1_DURATION=300
PHASE2_DURATION=300
PING_INTERVAL=30
```

### `experiments/mqtt/experiment-b-stateful/run.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$PROJECT_ROOT"

source "$SCRIPT_DIR/config.env"

mkdir -p "$RESULT_DIR"

# ── Archive previous results ──────────────────────────────────────
if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR")" ]; then
  ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
  mkdir -p "$ARCHIVE_DIR"
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  tar -czf "$ARCHIVE_DIR/experiment-b-mqtt_${TIMESTAMP}.tgz" -C "$RESULT_DIR" .
  rm -rf "$RESULT_DIR"
  mkdir -p "$RESULT_DIR"
  echo "[*] Archived previous results"
fi

echo "=============================================="
echo " Experiment-B MQTT: STAR StatefulAutoscaler"
echo "=============================================="

# ── Cluster setup ─────────────────────────────────────────────────
kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

# ── Metrics server ────────────────────────────────────────────────
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
    "--cert-dir=/tmp","--secure-port=10250",
    "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
    "--kubelet-use-node-status-port","--metric-resolution=15s",
    "--kubelet-insecure-tls"]}]'
kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
until kubectl top pods >/dev/null 2>&1; do sleep 5; done
echo "[✓] Metrics ready"

# ── Prometheus ────────────────────────────────────────────────────
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/namespace.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/rbac.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/configmap.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/deployment.yaml"
kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/service.yaml"
kubectl -n monitoring rollout status deployment/prometheus --timeout=300s
echo "[✓] Prometheus ready"

# ── Build & load images ───────────────────────────────────────────
docker build -t mqtt-broker:latest   "$PROJECT_ROOT/workloads/mqtt/app"
docker build -t mqtt-loadgen:latest  "$PROJECT_ROOT/load-generator/mqtt-client"
# Build + load STAR controller image
cd "$PROJECT_ROOT/controller"
IMG=star-controller:latest make docker-build IMG=star-controller:latest 2>&1 | tail -5
cd "$PROJECT_ROOT"
kind load docker-image mqtt-broker:latest    --name "$CLUSTER_NAME"
kind load docker-image mqtt-loadgen:latest   --name "$CLUSTER_NAME"
kind load docker-image star-controller:latest --name "$CLUSTER_NAME"
echo "[✓] Images loaded"

# ── Deploy STAR controller ────────────────────────────────────────
cd "$PROJECT_ROOT/controller"
IMG=star-controller:latest make deploy IMG=star-controller:latest
cd "$PROJECT_ROOT"
kubectl -n star-controller-system rollout status deployment/star-controller-controller-manager --timeout=300s
echo "[✓] STAR controller ready"

# ── Deploy broker + StatefulAutoscaler CR ─────────────────────────
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/deployment.yml"
kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/service.yml"
kubectl apply -f "$SCRIPT_DIR/statefulautoscaler.yaml"
kubectl wait --for=condition=ready pod -l app=mqtt-broker --timeout=180s
echo "[✓] Broker + StatefulAutoscaler ready"

# ── Start data collectors ─────────────────────────────────────────
(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
  kubectl get pods -l app=mqtt-broker --no-headers >> "$RESULT_DIR/pods.log"
  sleep 5
done) &
PODS_PID=$!

(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/autoscaler.log"
  kubectl get statefulautoscaler mqtt-autoscaler -o wide --no-headers >> "$RESULT_DIR/autoscaler.log" 2>/dev/null || true
  sleep 5
done) &
SA_PID=$!

(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"
  kubectl top pods -l app=mqtt-broker --no-headers 2>/dev/null >> "$RESULT_DIR/cpu.log" || true
  sleep 5
done) &
CPU_PID=$!

kubectl port-forward svc/prometheus -n monitoring 9090:9090 >/dev/null 2>&1 &
PF_PID=$!
sleep 5

(while true; do
  echo "$(date +%s)" >> "$RESULT_DIR/active_connections.log"
  curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); \
      r=d['data']['result']; print(r[0]['value'][1] if r else '0')" \
    >> "$RESULT_DIR/active_connections.log" 2>/dev/null || echo "0" >> "$RESULT_DIR/active_connections.log"
  sleep 5
done) &
CONN_PID=$!

# ── Phase 1: high load ────────────────────────────────────────────
echo "[PHASE 1] Ramping ${PHASE1_CLIENTS} clients..."
echo "PHASE1_START $(date +%s)" >> "$RESULT_DIR/phases.log"

kubectl run mqtt-loadgen \
  --image=mqtt-loadgen:latest \
  --restart=Never \
  --image-pull-policy=Never \
  --env="BROKER_HOST=mqtt-service" \
  --env="CLIENTS=$PHASE1_CLIENTS" \
  --env="RAMP_SECONDS=$RAMP_SECONDS" \
  --env="PING_INTERVAL=$PING_INTERVAL"

sleep "$PHASE1_DURATION"
kubectl logs mqtt-loadgen >> "$RESULT_DIR/loadgen-phase1.log" 2>/dev/null || true
kubectl delete pod mqtt-loadgen --ignore-not-found

# ── Phase 2: reduced load ─────────────────────────────────────────
echo "[PHASE 2] Dropping to ${PHASE2_CLIENTS} clients..."
echo "PHASE2_START $(date +%s)" >> "$RESULT_DIR/phases.log"

kubectl run mqtt-loadgen \
  --image=mqtt-loadgen:latest \
  --restart=Never \
  --image-pull-policy=Never \
  --env="BROKER_HOST=mqtt-service" \
  --env="CLIENTS=$PHASE2_CLIENTS" \
  --env="RAMP_SECONDS=10" \
  --env="PING_INTERVAL=$PING_INTERVAL"

sleep "$PHASE2_DURATION"
kubectl logs mqtt-loadgen >> "$RESULT_DIR/loadgen-phase2.log" 2>/dev/null || true
kubectl delete pod mqtt-loadgen --ignore-not-found

echo "PHASE2_END $(date +%s)" >> "$RESULT_DIR/phases.log"

# ── Teardown ──────────────────────────────────────────────────────
kill $PODS_PID $SA_PID $CPU_PID $CONN_PID $PF_PID 2>/dev/null || true
echo "[✓] Experiment-B MQTT complete. Results in $RESULT_DIR"
```

---

## Experiment C — Idle Connections: HPA vs STAR (`experiment-c-idle-connections`)

### Purpose

Demonstrate the **idle connection problem** in the MQTT context: clients that
are connected but not publishing generate zero CPU load.  HPA sees low CPU and
scales down even though many persistent sessions are active.  STAR holds steady.

This directly mirrors WebSocket Experiment B3 (`experiment-b3-hpa-idle-connections`).

### Scenario

1. Start 2 broker replicas under **both** HPA and STAR (run in separate clusters
   sequentially, results stored with different path prefixes).
2. Connect 400 idle MQTT clients (subscribe only, `PING_INTERVAL=120` — very
   infrequent keepalive).  No CPU load generated.
3. Observe for 600 s:
   - Under HPA: CPU ≈ 0 % → HPA scales down to `minReplicas=1` → 50 % of
     clients get disconnected.
   - Under STAR: `active_connections ≈ 400` → desired replicas =
     ceil(400/150) = 3 → STAR holds at 3 replicas (or scales down only 1
     step when connections genuinely drop).
4. Record: replica count timeline, disconnection events, reconnection storms.

### Config — `experiments/mqtt/experiment-c-idle-connections/config.env`

```bash
CLUSTER_NAME_HPA=mqtt-exp-c-hpa
CLUSTER_NAME_STAR=mqtt-exp-c-star
RESULT_DIR_HPA=results/raw/mqtt/experiment-c-idle-hpa
RESULT_DIR_STAR=results/raw/mqtt/experiment-c-idle-star
CLIENTS=400
RAMP_SECONDS=60
PING_INTERVAL=120      # very infrequent — simulates idle IoT devices
DURATION=600
```

### `experiments/mqtt/experiment-c-idle-connections/run.sh`

The run script is split into two phases — HPA run then STAR run — using the
same image and load generator.  The two result directories can then be compared
directly by the analysis script.

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$PROJECT_ROOT"

source "$SCRIPT_DIR/config.env"

run_phase() {
  local SCALER=$1        # "hpa" or "star"
  local CLUSTER_NAME=$2
  local RESULT_DIR=$3

  mkdir -p "$RESULT_DIR"

  if [ -d "$RESULT_DIR" ] && [ "$(ls -A "$RESULT_DIR")" ]; then
    ARCHIVE_DIR="$PROJECT_ROOT/results/tar"
    mkdir -p "$ARCHIVE_DIR"
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    tar -czf "$ARCHIVE_DIR/exp-c-${SCALER}_${TIMESTAMP}.tgz" -C "$RESULT_DIR" .
    rm -rf "$RESULT_DIR"
    mkdir -p "$RESULT_DIR"
  fi

  echo "======================================================"
  echo " Experiment-C MQTT: Idle Connections — Scaler=$SCALER"
  echo "======================================================"

  kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true
  kind create cluster --name "$CLUSTER_NAME" --config "$PROJECT_ROOT/scripts/kind.yml"

  # Metrics server
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
  kubectl -n kube-system patch deployment metrics-server --type='json' -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/args","value":[
      "--cert-dir=/tmp","--secure-port=10250",
      "--kubelet-preferred-address-types=InternalIP,ExternalIP,Hostname",
      "--kubelet-use-node-status-port","--metric-resolution=15s",
      "--kubelet-insecure-tls"]}]'
  kubectl -n kube-system rollout status deployment/metrics-server --timeout=300s
  until kubectl top pods >/dev/null 2>&1; do sleep 5; done

  # Prometheus
  kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/namespace.yaml"
  kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/rbac.yaml"
  kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/configmap.yaml"
  kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/deployment.yaml"
  kubectl apply -f "$PROJECT_ROOT/monitoring/prometheus/service.yaml"
  kubectl -n monitoring rollout status deployment/prometheus --timeout=300s

  # Images
  docker build -t mqtt-broker:latest   "$PROJECT_ROOT/workloads/mqtt/app"    2>/dev/null || true
  docker build -t mqtt-loadgen:latest  "$PROJECT_ROOT/load-generator/mqtt-client" 2>/dev/null || true
  kind load docker-image mqtt-broker:latest  --name "$CLUSTER_NAME"
  kind load docker-image mqtt-loadgen:latest --name "$CLUSTER_NAME"

  # Deploy broker
  # Start with 2 replicas to show scale-down behaviour clearly
  kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/deployment.yml"
  kubectl patch deployment mqtt-broker -p '{"spec":{"replicas":2}}'
  kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/service.yml"

  if [ "$SCALER" = "hpa" ]; then
    kubectl apply -f "$PROJECT_ROOT/workloads/mqtt/k8s/hpa.yml"
  else
    # Deploy STAR controller
    cd "$PROJECT_ROOT/controller"
    IMG=star-controller:latest make docker-build IMG=star-controller:latest 2>&1 | tail -5
    cd "$PROJECT_ROOT"
    kind load docker-image star-controller:latest --name "$CLUSTER_NAME"
    IMG=star-controller:latest make deploy IMG=star-controller:latest
    kubectl -n star-controller-system rollout status deployment/star-controller-controller-manager --timeout=300s
    kubectl apply -f "$SCRIPT_DIR/statefulautoscaler.yaml"
  fi

  kubectl wait --for=condition=ready pod -l app=mqtt-broker --timeout=180s

  # Collectors
  (while true; do
    echo "$(date +%s)" >> "$RESULT_DIR/pods.log"
    kubectl get pods -l app=mqtt-broker --no-headers >> "$RESULT_DIR/pods.log"
    sleep 5
  done) &
  PODS_PID=$!

  if [ "$SCALER" = "hpa" ]; then
    (while true; do
      echo "$(date +%s)" >> "$RESULT_DIR/scaler.log"
      kubectl get hpa mqtt-hpa --no-headers >> "$RESULT_DIR/scaler.log" 2>/dev/null || true
      sleep 5
    done) &
  else
    (while true; do
      echo "$(date +%s)" >> "$RESULT_DIR/scaler.log"
      kubectl get statefulautoscaler mqtt-autoscaler --no-headers >> "$RESULT_DIR/scaler.log" 2>/dev/null || true
      sleep 5
    done) &
  fi
  SCALER_PID=$!

  (while true; do
    echo "$(date +%s)" >> "$RESULT_DIR/cpu.log"
    kubectl top pods -l app=mqtt-broker --no-headers 2>/dev/null >> "$RESULT_DIR/cpu.log" || true
    sleep 5
  done) &
  CPU_PID=$!

  kubectl port-forward svc/prometheus -n monitoring 9090:9090 >/dev/null 2>&1 &
  PF_PID=$!
  sleep 5

  (while true; do
    echo "$(date +%s)" >> "$RESULT_DIR/active_connections.log"
    curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" \
      | python3 -c "import sys,json; d=json.load(sys.stdin); \
        r=d['data']['result']; print(r[0]['value'][1] if r else '0')" \
      >> "$RESULT_DIR/active_connections.log" 2>/dev/null || echo "0" >> "$RESULT_DIR/active_connections.log"
    sleep 5
  done) &
  CONN_PID=$!

  # Load generator
  kubectl run mqtt-loadgen \
    --image=mqtt-loadgen:latest \
    --restart=Never \
    --image-pull-policy=Never \
    --env="BROKER_HOST=mqtt-service" \
    --env="CLIENTS=$CLIENTS" \
    --env="RAMP_SECONDS=$RAMP_SECONDS" \
    --env="PING_INTERVAL=$PING_INTERVAL"

  echo "[*] Running for ${DURATION}s..."
  sleep "$DURATION"

  kubectl logs mqtt-loadgen >> "$RESULT_DIR/loadgen.log" 2>/dev/null || true
  kubectl delete pod mqtt-loadgen --ignore-not-found

  kill $PODS_PID $SCALER_PID $CPU_PID $CONN_PID $PF_PID 2>/dev/null || true
  kind delete cluster --name "$CLUSTER_NAME"
  echo "[✓] $SCALER phase complete. Results in $RESULT_DIR"
}

run_phase "hpa"  "$CLUSTER_NAME_HPA"  "$RESULT_DIR_HPA"
run_phase "star" "$CLUSTER_NAME_STAR" "$RESULT_DIR_STAR"

echo "[✓] Experiment-C MQTT complete."
```

---

## Part 8 — Analysis Scripts

### `analysis/mqtt/parse_logs_mqtt.py`

```python
"""
parse_logs_mqtt.py

Parses the raw logs produced by the MQTT experiment run scripts and
outputs a single CSV per experiment suitable for plotting.

Usage:
  python parse_logs_mqtt.py <result_dir> <output_csv>

Example:
  python parse_logs_mqtt.py results/raw/mqtt/experiment-a-hpa out.csv
"""
import sys
import csv
import os
from datetime import datetime

def parse_timestamped_log(path, value_fn):
    """
    Parse logs where lines alternate:  <unix_timestamp>  then <value_line(s)>
    value_fn(lines_after_ts) → float or None
    """
    records = []
    if not os.path.exists(path):
        return records
    with open(path) as f:
        lines = [l.rstrip() for l in f]

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.isdigit():
            ts = int(line)
            # collect lines until the next timestamp
            vals = []
            i += 1
            while i < len(lines) and not lines[i].isdigit():
                vals.append(lines[i])
                i += 1
            v = value_fn(vals)
            if v is not None:
                records.append((ts, v))
        else:
            i += 1
    return records


def parse_replicas(result_dir):
    """Extract (timestamp, replica_count) from pods.log."""
    def count_running(lines):
        running = sum(1 for l in lines if "Running" in l)
        return running if running else None
    return parse_timestamped_log(os.path.join(result_dir, "pods.log"), count_running)


def parse_connections(result_dir):
    """Extract (timestamp, active_connections) from active_connections.log."""
    records = []
    path = os.path.join(result_dir, "active_connections.log")
    if not os.path.exists(path):
        return records
    with open(path) as f:
        lines = [l.strip() for l in f]
    i = 0
    while i < len(lines) - 1:
        if lines[i].isdigit():
            try:
                records.append((int(lines[i]), float(lines[i+1])))
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    return records


def merge_timeseries(replicas, connections):
    """Merge two (timestamp, value) lists by nearest timestamp."""
    rep_dict = dict(replicas)
    merged = []
    for ts, conn in connections:
        # find closest replica reading
        if not rep_dict:
            continue
        closest_ts = min(rep_dict.keys(), key=lambda t: abs(t - ts))
        rep = rep_dict[closest_ts]
        merged.append((ts, rep, conn))
    return merged


def main(result_dir, output_csv):
    replicas    = parse_replicas(result_dir)
    connections = parse_connections(result_dir)
    merged      = merge_timeseries(replicas, connections)

    t0 = merged[0][0] if merged else 0

    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "replicas", "active_connections"])
        for ts, rep, conn in merged:
            writer.writerow([ts - t0, rep, conn])

    print(f"[✓] Wrote {len(merged)} rows to {output_csv}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
```

### `analysis/mqtt/plot_experiment_mqtt.py`

```python
"""
plot_experiment_mqtt.py

Produces a dual-axis time-series plot:
  - Left Y axis:  active MQTT connections
  - Right Y axis: broker replica count

Usage (single experiment):
  python plot_experiment_mqtt.py --csv out.csv --title "Exp-A HPA" --out plot.png

Usage (side-by-side comparison for Experiment C):
  python plot_experiment_mqtt.py \
    --csv-hpa  results/raw/mqtt/experiment-c-idle-hpa/out.csv \
    --csv-star results/raw/mqtt/experiment-c-idle-star/out.csv \
    --out comparison.png
"""
import argparse
import csv
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


def load_csv(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "time_s":             float(row["time_s"]),
                "replicas":           float(row["replicas"]),
                "active_connections": float(row["active_connections"]),
            })
    return rows


def plot_single(rows, title, out):
    fig, ax1 = plt.subplots(figsize=(12, 5))

    t    = [r["time_s"] for r in rows]
    conn = [r["active_connections"] for r in rows]
    rep  = [r["replicas"] for r in rows]

    ax1.plot(t, conn, color="tab:blue",  label="Active connections", linewidth=1.5)
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Active MQTT connections", color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")

    ax2 = ax1.twinx()
    ax2.step(t, rep, color="tab:red", label="Replica count", linewidth=2, where="post")
    ax2.set_ylabel("Broker replicas", color="tab:red")
    ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax2.tick_params(axis="y", labelcolor="tab:red")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")

    plt.title(title)
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    print(f"[✓] Saved {out}")
    plt.close()


def plot_comparison(rows_hpa, rows_star, out):
    fig, axes = plt.subplots(1, 2, figsize=(18, 5), sharey=False)

    for ax, rows, title in [
        (axes[0], rows_hpa,  "HPA (CPU-based)"),
        (axes[1], rows_star, "STAR Controller (connection-aware)"),
    ]:
        t    = [r["time_s"] for r in rows]
        conn = [r["active_connections"] for r in rows]
        rep  = [r["replicas"] for r in rows]

        ax.plot(t, conn, color="tab:blue", label="Active connections", linewidth=1.5)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Active MQTT connections", color="tab:blue")
        ax.tick_params(axis="y", labelcolor="tab:blue")

        ax2 = ax.twinx()
        ax2.step(t, rep, color="tab:red", label="Replica count", linewidth=2, where="post")
        ax2.set_ylabel("Broker replicas", color="tab:red")
        ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax2.tick_params(axis="y", labelcolor="tab:red")

        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right")
        ax.set_title(title)

    plt.suptitle("Experiment-C MQTT: Idle Connections — HPA vs STAR")
    plt.tight_layout()
    plt.savefig(out, dpi=150)
    print(f"[✓] Saved {out}")
    plt.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv",       help="Single CSV input")
    parser.add_argument("--title",     default="MQTT Experiment")
    parser.add_argument("--csv-hpa",   help="HPA CSV (comparison mode)")
    parser.add_argument("--csv-star",  help="STAR CSV (comparison mode)")
    parser.add_argument("--out",       required=True)
    args = parser.parse_args()

    if args.csv_hpa and args.csv_star:
        plot_comparison(load_csv(args.csv_hpa), load_csv(args.csv_star), args.out)
    elif args.csv:
        plot_single(load_csv(args.csv), args.title, args.out)
    else:
        print("Provide either --csv or both --csv-hpa and --csv-star")


if __name__ == "__main__":
    main()
```

---

## Part 9 — Thin Wrapper Scripts (`scripts/`)

These follow the same pattern as `scripts/run-experiment-c.sh` (websocket).

### `scripts/run-experiment-mqtt-a.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../experiments/mqtt/experiment-a-hpa-baseline/run.sh" "$@"
```

### `scripts/run-experiment-mqtt-b.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../experiments/mqtt/experiment-b-stateful/run.sh" "$@"
```

### `scripts/run-experiment-mqtt-c.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/../experiments/mqtt/experiment-c-idle-connections/run.sh" "$@"
```

---

## Part 10 — What Changes in the STAR Controller

The controller source itself requires **no changes**.  The metric name
`active_connections` is already what `broker.py` exposes, and the Prometheus
query `sum(active_connections)` in `prometheus.go` is protocol-agnostic.

The only environment difference is the Prometheus scrape configuration must
include the MQTT broker pods (covered in Part 6 above).

## Part 10.1 — Edge Cases, Assumptions, and Limitations

This project intentionally uses an instrumented Python broker to make the
experiments reproducible and to exercise the exact `active_connections` +
`/drain` pattern the STAR controller expects.  That choice creates several
important edge cases and assumptions you should document and (where possible)
mitigate in the paper and in follow-up engineering work:

- **Metric name & shape assumption**: the controller's Prometheus query is
  `sum(active_connections)`.  Non-instrumented brokers (e.g., vanilla
  Mosquitto, EMQX) use different metric names and schemas.  Mitigation: add
  configuration (env var or CRD field) for the Prometheus query string, and
  document the default.

- **Drain control contract**: the controller relies on an HTTP `/drain`
  endpoint that stops accepting new connections.  Many brokers don't expose a
  drain endpoint; some provide different control APIs (e.g., management
  ports, CLI).  Mitigation: document the contract and provide an adapter
  (sidecar or exporter) for non-instrumented brokers.

- **PreStop / lifecycle timing differences**: Kubernetes `preStop` behavior,
  `terminationGracePeriodSeconds`, and broker-internal session persistence vary
  by implementation. Tests should include different grace periods and verify
  whether clients reconnect successfully when session-state is lost.

- **Session semantics (clean session vs persistent)**: MQTT brokers may be
  configured with persistent sessions or `cleanSession=true`. Persistent
  sessions can reduce the observable impact of pod restarts; however,
  different settings change the user-visible effect. Document and test both
  modes.

- **Broker clustering and shared state**: Some brokers (EMQX, HiveMQ) use
  clustering where connections may be redistributed or sessions stored in a
  shared DB. The controller's effectiveness could change in clustered
  deployments. For external validity, run at least one experiment (or a
  synthetic test) with a clustered broker or explain why it is outside scope.

- **TLS, ports, and listener configurations**: Brokers behind TLS termination
  or with multiple listeners may expose metrics on a different port; the
  Prometheus scrape configuration and the preStop drain must be adapted.

- **Metric granularity and accuracy**: `active_connections` might be
  approximated (e.g., per listener, per worker process) or delayed by the
  exporter. Include checks for scrape latency and ensure the controller's
  requeue interval and cooldowns tolerate small delays.

- **Load generator representativeness**: IoT and enterprise clients differ in
  keepalive behaviour, QoS use, and reconnection logic; design experiments to
  include at least one 'realistic' client behaviour profile (e.g., sporadic
  publish vs persistent subscribe).

Recommendations (short-term engineering mitigations):

- Parameterize the Prometheus query and the drain endpoint URL in the
  controller via an env var or add optional fields to the `StatefulAutoscaler`
  CRD (e.g., `metricsQuery`, `drainPath`, `metricsPort`). This avoids code
  changes for new brokers.
- Provide a small adapter/sidecar for popular brokers that do not expose the
  required control/metric endpoints. Two practical approaches:
  - Sidecar exporter: runs alongside the broker, collects broker-native stats
    (or uses broker management APIs), and exposes `active_connections` +
    `/drain` for the controller.
  - Cluster-level adapter: a translation service that polls broker metrics and
    exposes an aggregated `active_connections` metric and a control API.
- Run at least one cross-check experiment using vanilla `eclipse-mosquitto`
  plus either an existing Mosquitto exporter or a sidecar shim. Record any
  behavioral differences and report them in the threats-to-validity section.

What to state in the paper's threats-to-validity section:

- Explicitly state that the primary experiments use an instrumented Python
  broker to demonstrate the controller's correctness and safety.
- Report that results were validated against at least one unmodified broker
  (e.g., Mosquitto + exporter) or describe why this was not feasible.
- Mention how controller assumptions (metric name, drain contract, grace
  periods) might affect external validity, and summarise the mitigations above.

These additions make it clear which elements are part of the experimental
contract and which are implementation artifacts. They also provide a concrete
upgrade path (adapter + parameterization) so reviewers can see how the work
generalises beyond the Python proof-of-concept.

---

## Part 11 — Metrics Collected Per Experiment

| Log file | Contents | Sampling interval |
|---|---|---|
| `pods.log` | Pod name, status, restarts | 5 s |
| `cpu.log` | CPU / memory per pod (`kubectl top`) | 5 s |
| `active_connections.log` | `sum(active_connections)` from Prometheus | 5 s |
| `hpa.log` / `scaler.log` | HPA or StatefulAutoscaler state | 5 s |
| `loadgen.log` | Connected count reported by load generator | stdout of Pod |
| `phases.log` | Timestamps for phase transitions (Exp-B only) | on event |

---

## Part 12 — Expected Results Summary

### Experiment A (HPA Baseline)

- Replicas fluctuate to `minReplicas=1` after ~5 min because CPU ≈ 0 %.
- `active_connections` drops suddenly when the second broker pod is killed.
- `loadgen.log` shows a large burst of disconnection messages.
- **Key plot**: sharp drop in `active_connections` correlated with replica-count
  step-down.

### Experiment B (STAR Controller)

- Replicas rise to 4 within ~2 min of ramp completing (600 conns / 150 target = 4).
- `active_connections` stays proportional; no sudden drops.
- After phase 2 begins (150 clients), replicas reduce to 1 after the 120 s
  stabilisation window, but the `/drain` lifecycle hook prevents abrupt
  disconnections.
- **Key plot**: smooth replica curve tracking connections, and replica step-down
  that does NOT correlate with a connection-count drop.

### Experiment C (Idle Connections)

- **HPA sub-run**: Replicas fall from 2 → 1 within ~5 min (CPU ≈ 0 %).
  `active_connections` halves abruptly.
- **STAR sub-run**: Replicas hold at ceil(400/150) = 3.  `active_connections`
  remains flat throughout.
- **Key plot**: side-by-side comparison showing HPA instability vs STAR
  stability.

---

## Part 13 — Implementation Checklist

```
[ ] 1. Create workloads/mqtt/app/broker.py
[ ] 2. Create workloads/mqtt/app/requirements.txt
[ ] 3. Create workloads/mqtt/app/Dockerfile
[ ] 4. Update workloads/mqtt/k8s/deployment.yml
[ ] 5. Create workloads/mqtt/k8s/hpa.yml
[ ] 6. Update monitoring/prometheus/configmap.yaml (add mqtt-broker scrape job)
[ ] 7. Create load-generator/mqtt-client/client.py
[ ] 8. Create load-generator/mqtt-client/requirements.txt
[ ] 9. Create load-generator/mqtt-client/Dockerfile
[ ] 10. Create experiments/mqtt/experiment-a-hpa-baseline/{README,config.env,run.sh}
[ ] 11. Create experiments/mqtt/experiment-b-stateful/{README,config.env,statefulautoscaler.yaml,run.sh}
[ ] 12. Create experiments/mqtt/experiment-c-idle-connections/{README,config.env,statefulautoscaler.yaml,hpa-config.yaml,run.sh}
[ ] 13. Create analysis/mqtt/parse_logs_mqtt.py
[ ] 14. Create analysis/mqtt/plot_experiment_mqtt.py
[ ] 15. Create scripts/run-experiment-mqtt-{a,b,c}.sh
[ ] 16. Smoke-test: docker build + docker run broker locally on port 1883/8080
[ ] 17. Smoke-test: curl localhost:8080/metrics → "active_connections 0"
[ ] 18. Smoke-test: run client.py CLIENTS=5 BROKER_HOST=localhost
[ ] 19. Full run: experiment-a on Kind cluster
[ ] 20. Full run: experiment-b on Kind cluster
[ ] 21. Full run: experiment-c on Kind cluster
[ ] 22. Run analysis/parse then plot; verify plots match expected results
```

---

## Part 14 — Dependency Notes

- `amqtt` (PyPI) requires Python ≥ 3.8 and is actively maintained.  Pin to
  `amqtt==0.11.0` for reproducibility.
- `paho-mqtt` (PyPI) `1.6.x` is the stable series; `2.x` changed the API.
  Pin `paho-mqtt==1.6.1`.
- The existing Kind config (`scripts/kind.yml`) and Prometheus stack
  (`monitoring/prometheus/`) are reused unchanged across all MQTT experiments.
- The STAR controller image must be rebuilt and loaded into each new Kind cluster
  since Kind clusters are ephemeral.  The Makefile target is `make docker-build IMG=<tag>`.
