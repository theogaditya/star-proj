# Experiment Runbook (Paper 2 revisions)

All commands run from `ws-k8-controller/`. Requires: docker, kind, kubectl, make, python3 (matplotlib, numpy), jq, ansible.

## What to run for what

| Experiment | One-shot (Ansible) | Direct script |
|---|---|---|
| Sensitivity analysis (cooldown, T, scrape, step) | `ansible-playbook ansible/sensitivity.yml` | `scripts/run-sensitivity-sweep.sh` |
| Single sensitivity run (custom params) | - | `scripts/run-sensitivity.sh --cooldown 120 --target 100 --scrape 15 --step 2 --gap 90 --clients 800` |
| MQTT-B x5 replication (Table 5 mean/std) | `ansible-playbook ansible/mqtt_b_replication.yml` | `RUNS=5 scripts/run-mqtt-b-multi.sh` |
| T derivation (memory per connection) | `ansible-playbook ansible/t_memory_measurement.yml` | `scripts/measure-connection-memory.sh --limit-mib 512` |
| Scalability (800/1600/3200 clients) | `ansible-playbook ansible/scalability.yml` | `scripts/run-experiment-c-scale.sh` |
| **Everything at once** | `ansible-playbook ansible/run_all.yml` | - |

## Outputs

- Logs: `ws-k8-controller/logs/<experiment>_<timestamp>.log`
- Raw data: `ws-k8-controller/results/raw/...`
- Tables + plots: `ws-k8-controller/results/processed/...` (auto-copied to `Paper-2-StatefulAutoscaler/figures/`)
- Key plot: `figures/sensitivity/safe_zone_boundary.png` (safe-zone boundary)
- After runs, fill the `XX` placeholders in `Paper-2-StatefulAutoscaler/main.tex` from the generated `table_*.csv` files.

## If an experiment fails

1. **Cluster creation fails / leftover cluster**: `kind delete cluster --name stateful-sens` (also `stateful-mem`, `stateful-exp`), then re-run.
2. **`kubectl top` empty / metrics errors**: metrics-server not ready; wait 2 min or re-run (scripts wait automatically).
3. **Prometheus never scrapes `active_connections`**: check `kubectl -n monitoring logs deploy/prometheus`; ensure port 9090 is free (`ss -tln | grep 9090`).
4. **Connections plateau below client count (1600/3200, MQTT)**: OS TCP ceiling (ephemeral ports, `somaxconn`). Expected on Kind; the achieved peak is recorded and should be reported as the documented ceiling.
5. **A sweep run fails midway**: safe to re-run only that point, e.g. `scripts/run-sensitivity.sh --sweep cooldown --cooldown 90 --gap 120 --reuse-cluster --keep-cluster`; each run overwrites only its own result dir.
6. **MQTT-B run fails**: the multi-run wrapper continues with the next run; re-run the wrapper with `RUNS=1` to backfill the missing run, then re-run `analysis/mqtt-multi/aggregate_mqtt_runs.py --base results/raw/mqtt/experiment-b-stateful`.
7. **Analysis fails (`missing logs`)**: the raw dir of that run is incomplete; delete it and re-run that single point.
8. **Docker build failures**: `docker system prune -f` and ensure ~10 GB free disk.
