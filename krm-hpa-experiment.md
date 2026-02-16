# KRM HPA Reproduction (Kind) — Step-by-step (paper-style)

> Reproducible experiment plan to reproduce the **Kubernetes Resource Metrics (KRM)** portion of the paper using a Kind cluster.
> This file contains **only** the experiment steps, commands, data collection, and the report/analysis plan — ready to give to an automation tool (Codex).

---

## 1. Goal

Reproduce KRM-based Horizontal Pod Autoscaler behavior on a **Kind** cluster and measure how different `metrics-server` scraping periods (`--metric-resolution`) affect scaling and stability.

---

## 2. Assumptions & notes

* Target: local **Kind** cluster (Kubernetes-in-Docker).
* KRM pipeline provided by **metrics-server** (runs in `kube-system`).
* HPA sync period (controller manager default) is 15s; metrics-server `--metric-resolution` controls resource metric sample frequency.
* Tests use CPU-based scaling only (no Prometheus / custom metrics).
* Use `--kubelet-insecure-tls` in Kind for convenience (only for local testing).

---

## 3. Files to produce

* `kind-config.yaml` — Kind cluster config.
* `metrics-server-patch.yaml` — patch to set `--metric-resolution` and Kind TLS flags.
* `cpu-app.yaml` — CPU workload deployment.
* `hpa-krm.yaml` — HPA manifest (Resource / CPU).
* `collect-scripts/collect_*` — small scripts to poll and log metrics.
* `run-experiment.sh` — orchestrates scraping period runs and collects logs.
* `analysis.ipynb` or `analysis.py` — notebook/script to load CSV logs and generate graphs/tables.

---

## 4. Exact manifests and commands (9 steps)

### Step 0 — Clean start (optional)

```bash
kind delete cluster || true
```

### Step 1 — Create Kind cluster (4-node)

`kind-config.yaml`

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
- role: worker
- role: worker
```

Create:

```bash
kind create cluster --config kind-config.yaml
kubectl get nodes
```

### Step 2 — Install Metrics Server

Apply upstream manifest:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

### Step 3 — Patch Metrics Server for Kind + set scraping resolution

`metrics-server-patch.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: metrics-server
  namespace: kube-system
spec:
  template:
    spec:
      containers:
      - name: metrics-server
        args:
        - --kubelet-insecure-tls
        - --kubelet-preferred-address-types=InternalIP
        - --metric-resolution=60s    # change to 30s / 15s for experiments
```

Apply patch and restart:

```bash
kubectl apply -f metrics-server-patch.yaml
kubectl -n kube-system rollout restart deployment/metrics-server
```

### Step 4 — Verify metrics availability

```bash
# wait a few seconds, then
kubectl top nodes
kubectl top pods
kubectl -n kube-system logs deployment/metrics-server --tail=200
```

### Step 5 — Deploy CPU workload (baseline: 4 replicas)

`cpu-app.yaml`

```yaml
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
    spec:
      containers:
      - name: cpu
        image: polinux/stress
        args: ["--cpu", "1"]
        resources:
          requests:
            cpu: "100m"
          limits:
            cpu: "200m"
```

Apply:

```bash
kubectl apply -f cpu-app.yaml
kubectl get pods -l app=cpu-app
```

### Step 6 — Create HPA (KRM / CPU)

`hpa-krm.yaml`

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cpu-hpa
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
```

Apply:

```bash
kubectl apply -f hpa-krm.yaml
kubectl get hpa cpu-hpa -o wide
```

### Step 7 — Observe / live-monitor (manual or automated)

Open watches:

```bash
kubectl get hpa cpu-hpa -w
kubectl get pods -w
watch -n 5 kubectl top pods
```

(Or run collectors in scripts — see section 5.)

### Step 8 — Scraping-period experiments (60s, 30s, 15s)

For each `metric-resolution` value do:

1. Edit `metrics-server-patch.yaml` (set `--metric-resolution=<value>`) and reapply.
2. Restart metrics-server:

   ```bash
   kubectl -n kube-system rollout restart deployment/metrics-server
   ```
3. Wait for metrics-server readiness (check logs).
4. Run workload pattern (High/Low cycles) and start collectors.
5. Record experiment for a fixed window (e.g., 10 minutes) per resolution value.

### Step 9 — Clean up

```bash
kind delete cluster
```

---

## 5. What to collect (automatable collectors)

Create a `collect-scripts` directory and add these scripts. Run them before starting each experiment window.

### A — Poll pod CPU & memory (every 5s)

Command (append to CSV):

```bash
while true; do
  ts=$(date --iso-8601=seconds)
  kubectl top pods --no-headers | awk -v ts="$ts" '{print ts","$1","$2","$3}' >> pod_cpu.csv
  sleep 5
done
```

CSV header:

```
timestamp,pod,cpu,memory
```

### B — Poll HPA status (every 5s)

```bash
while true; do
  ts=$(date --iso-8601=seconds)
  current=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentReplicas}')
  desired=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.desiredReplicas}')
  cpuutil=$(kubectl get hpa cpu-hpa -o jsonpath='{.status.currentMetrics[*].resource.current.averageUtilization}' 2>/dev/null || echo "")
  echo "$ts,$current,$desired,$cpuutil" >> hpa_log.csv
  sleep 5
done
```

CSV header:

```
timestamp,currentReplicas,desiredReplicas,currentCPUUtilizationPercent
```

### C — Pod count & events (every 5s)

```bash
while true; do
  date --iso-8601=seconds >> podcount.log
  kubectl get pods --no-headers | wc -l >> podcount.log
  kubectl get events --sort-by='.lastTimestamp' | tail -n 50 > events_snapshot.log
  sleep 5
done
```

### D — Metrics-server logs (continuous)

```bash
kubectl -n kube-system logs deployment/metrics-server --follow >> metrics-server.log
```

---

## 6. Workload pattern (paper-style: alternating high/low)

Simulate alternating 100s high / 100s low CPU periods. Example `run-load-phase.sh`:

```bash
# runs inside a control shell; selects a pod and triggers a CPU loop for 100s
POD=$(kubectl get pod -l app=cpu-app -o jsonpath='{.items[0].metadata.name}')
kubectl exec -it $POD -- /bin/sh -c "timeout 100 sh -c 'while :; do :; done'"
# wait 100s for low period (no busy loop)
sleep 100
# repeat in a loop or orchestrate multiple cycles
```

Automate start/stop and tag timestamps in a `phases.log`:

```
timestamp,phase,action
2026-02-16T12:00:00Z,high,start
2026-02-16T12:01:40Z,high,end
2026-02-16T12:01:40Z,low,start
...
```

---

## 7. Analysis & what to compute (for the report)

From the recorded CSVs produce these time-series and derived metrics:

### Time-series plots

* Pod replicas vs time
* Average CPU per pod vs time
* HPA `desiredReplicas` vs `currentReplicas` vs time
* Metrics-server scrape occurrences (timestamps) vs metric value updates (optional)

### Derived metrics (per experiment run / scraping period)

* **Time-to-scale-up**: time from phase start (high) to first replica increase (seconds)
* **Time-to-stabilize**: time until replicas stop changing for >60s (seconds)
* **Max replicas reached**
* **Average CPU after scale** (mean CPU across pods in stabilized period)
* **Overshoot / undershoot area**: area between target utilization and observed utilization over time
* **Resource cost**: sum_over_time(replicas * 1 second) → pod-seconds

### Tables to include

* One table per scraping-period (15s, 30s, 60s) containing the derived metrics above.

---

## 8. Report structure (recommended)

1. Title, authors, abstract
2. Introduction & motivation
3. Experimental setup (Kind config, metrics-server args, workload)
4. Methods (data collection scripts, sampling rates, windows)
5. Results (graphs, tables, sample logs)
6. Analysis (interpret timing vs `--metric-resolution` and HPA sync cycle)
7. Threats to validity (Kind limitations: host CPU, container scheduling, `--kubelet-insecure-tls`)
8. Conclusion & recommendations (safe `metric-resolution`, readiness probe advice)
9. Appendix — exact commands, raw CSVs, scripts

---

## 9. Tips & final notes

* HPA sync loop typically runs every 15s; metrics-server resolution determines how fresh the CPU samples are when HPA reads them.
* Do not set `--metric-resolution` below 15s on Kind or large clusters without understanding overhead.
* Use `kubectl describe hpa cpu-hpa` to inspect how HPA computed desired replicas.
* Collect logs and CSVs in separate folders per experiment run and compress them for archiving.

---

## 10. Cleanup (recommended)

After experiments:

```bash
# stop collectors (identify PIDs)
# compress results
tar -czf krm_experiment_results_$(date +%Y%m%d%H%M%S).tgz pod_cpu.csv hpa_log.csv podcount.log metrics-server.log events_snapshot.log phases.log
# delete cluster
kind delete cluster
```

---
