
**Repository**
- GitHub: <https://github.com/MistaHolmes/ws-k8-controller>
- Clone: `git clone https://github.com/MistaHolmes/ws-k8-controller.git`

**Overview**
- **Purpose:** How to run each experiment in this workspace, common debug commands, and cleanup steps to remove Kind clusters when finished.

**Quick Run (per-experiment)**
- **Experiment C (StatefulAutoscaler / connection-based):**
	- Run:

```bash
./scripts/run-experiment-c.sh
```
	- What it does: creates a Kind cluster, deploys Prometheus, the controller, the websocket server and a load-job; collects raw + processed results under `results/raw/websocket/experiment-c-stateful` and `results/processed/websocket/experiment-c-stateful`.

- **Experiment D (HPA + custom Prometheus metric):**
	- Run:

```bash
./scripts/run-experiment-d.sh
```
	- What it does: similar flow but exercises the HPA with the Prometheus Adapter.

- **Experiment E (KEDA baseline):**
	- Run:

```bash
./scripts/run-experiment-e.sh
```
	- What it does: installs KEDA (via Helm), runs the workload and collects results under `results/raw/websocket/experiment-e-keda` and `results/processed/...`.

- **Experiment B3 / C variants:**
	- Run:

```bash
./scripts/run-experiment-b3.sh
# or
./scripts/run-experiment-c.sh
```

- **Experiment C — Scalability sweep (800 / 1600 / 3200 clients, single run):**
	- Run:

```bash
./scripts/run-experiment-c-scale.sh
```

	- What it does: runs the two-cycle restorm scenario three times in sequence (once per client count) on a shared Kind cluster; calls `parse_sensitivity.py` + `plot_scalability.py`; leaves results under `results/raw/websocket/sensitivity/scale_*/` and `results/processed/websocket/scalability/`.

- **Experiment C — Scalability sweep multi-run (N replicates):**
	- Run:

```bash
N=3 ./scripts/run-experiment-c-scale-multi.sh
# Optionally override client counts or wipe previous multi data:
N=5 CLIENTS_LIST="800 1600 3200" ./scripts/run-experiment-c-scale-multi.sh
CLEAN_MULTI=1 N=3 ./scripts/run-experiment-c-scale-multi.sh
```

	- **Result layout:**

| Path | Contents |
|------|----------|
| `results/raw/websocket/experiment-c-scale/` | Raw logs for the *current* (last) single run |
| `results/processed/websocket/experiment-c-scale/` | Parsed CSVs + plots for the current single run |
| `results/tar/experiment-c-scale_run_<i>_<ts>.tgz` | Per-run archive (raw + processed bundled together) |
| `results/raw/websocket/multi/experiment-c-scale/run_<i>/` | Saved raw logs for each replicate |
| `results/processed/websocket/multi/experiment-c-scale/run_<i>/` | Saved processed results for each replicate |
| `results/processed/websocket/multi/experiment-c-scale/aggregate_stats.csv` | Cross-run mean ± std per client count |
| `results/processed/websocket/multi/experiment-c-scale/plots/scalability_aggregate.png` | Aggregate 3-panel summary plot |

	- Each run archives itself to `results/tar/` **before** being moved into the multi-run store, so no data is ever lost between iterations.
	- After all N runs `analysis/scalability/aggregate_scale.py` is called automatically to produce the aggregate CSV and plot.
	- To re-run the aggregation manually on existing multi data:

```bash
python3 analysis/scalability/aggregate_scale.py \
  --multi-proc-dir results/processed/websocket/multi/experiment-c-scale
```

- **Multi-run (N replicates):**
	- Example:

```bash
EXPERIMENT=c N=5 ./scripts/run-multi.sh
```

	This runs the chosen experiment script N times and saves per-run raw/processed results under `results/raw/websocket/multi/<experiment>/run_<i>` and `results/processed/websocket/multi/<experiment>/run_<i>`. Use `analysis/multi_run_stats.py` separately to aggregate `summary.csv` files when desired.

- **Failure scenarios (three scenarios in one run):**
	- Run:

```bash
./scripts/run-failure-scenarios.sh
```

  Runs three failure scenarios sequentially and invokes the failure analysis scripts.

**Common Commands & Debugging**
- **View cluster nodes:** `kubectl get nodes -o wide`
- **Pod status (all namespaces):** `kubectl get pods -A`
- **Pods for this workload:** `kubectl get pods -l app=websocket-server -o wide`
- **Current replica count (deployment):**
	- ````bash

kubectl get nodes -o wide

````

	- ````bash

kubectl get pods -A

````

	- ````bash

kubectl get pods -l app=websocket-server -o wide

````

	- **Current replica count (deployment):**

```bash
kubectl get deployment websocket-server -o wide
kubectl get deployment websocket-server -o jsonpath='{.status.readyReplicas}'
```

**Top CPU/memory for pods:**

```bash
kubectl top pods -l app=websocket-server --no-headers
```
- **Prometheus metrics (local port-forward):**
	- Start port-forward:

```bash
kubectl -n monitoring port-forward svc/prometheus 9090:9090 &
```

	- Open `http://localhost:9090` to inspect targets and query metrics such as `sum(active_connections)`.
- **Dump active_connections quickly:**
	- Run:

```bash
curl -s "http://localhost:9090/api/v1/query?query=sum(active_connections)" | jq .
```
- **Inspect HPA (for Experiment D):**
	- Run:

```bash
kubectl get hpa -A
kubectl describe hpa websocket-hpa
```
- **Inspect KEDA (for Experiment E):**
	- Run:

```bash
kubectl get pods -n keda
kubectl get deployments -n keda
kubectl get scaledobjects -A

# Get operator deployment name then inspect logs, e.g.:
kubectl -n keda get deploy
kubectl -n keda logs deploy/<keda-deploy-name>
```
- **Controller logs (StatefulAutoscaler):**
	- Run:

```bash
kubectl -n controller-system get pods
kubectl -n controller-system logs deploy/controller-controller-manager
```
- **Prometheus logs:**
	- Run:

```bash
kubectl -n monitoring get pods -l app=prometheus
kubectl -n monitoring logs deploy/prometheus
```
- **Check kube events for issues:** `kubectl get events -A --sort-by=.metadata.creationTimestamp`
	- Run:

```bash
kubectl get events -A --sort-by=.metadata.creationTimestamp
```

**How to check if KEDA is still running**
- Quick checks:
	- Run:

```bash
kubectl get pods -n keda
kubectl get deploy -n keda
kubectl get scaledobject -A
kubectl -n keda logs deploy/<keda-deploy-name>
```

**Ensuring Kind clusters are removed after experiments**
- The workspace includes a small helper script to delete one or more Kind clusters safely: `scripts/cleanup-kind.sh`.
- Usage:
	- Delete a single named cluster:

```bash
./scripts/cleanup-kind.sh stateful-exp
```

	- Delete multiple:

```bash
./scripts/cleanup-kind.sh stateful-exp exp-failure
```

	- Delete the known experiment clusters (defaults):

```bash
./scripts/cleanup-kind.sh
```

Add this to CI or manual teardown steps to avoid leftover clusters consuming local resources.

**Notes & Best Practices**
- Always inspect `results/raw/websocket/<experiment>` and `results/processed/websocket/<experiment>` after a run. The `summary.csv` in the processed directory contains derived metrics used by the analysis tools.
- If a run fails mid-way, copy the raw `results/raw/websocket/...` folder off the machine before re-running to preserve logs for debugging.
-- Use `EXPERIMENT` and `N` with `run-multi.sh` for reproducible multi-run experiments; per-run results are saved under `results/.../multi/<experiment>/run_<i>`. Aggregate manually with `analysis/multi_run_stats.py` if you want `aggregate_stats.csv`.

**Files**
- Run scripts: [scripts/run-experiment-c.sh](scripts/run-experiment-c.sh), [scripts/run-experiment-d.sh](scripts/run-experiment-d.sh), [scripts/run-experiment-e.sh](scripts/run-experiment-e.sh), [scripts/run-failure-scenarios.sh](scripts/run-failure-scenarios.sh)
- Helpers: [scripts/run-multi.sh](scripts/run-multi.sh), [analysis/multi_run_stats.py](analysis/multi_run_stats.py), [analysis/failure-scenarios/parse_failure_scenarios.py](analysis/failure-scenarios/parse_failure_scenarios.py)

---
Updated: instructions added for running experiments, debugging, KEDA checks, and cluster cleanup.
