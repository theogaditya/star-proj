#!/usr/bin/env python3
"""Aggregate five MQTT-B replication runs into mean/std statistics and a
multi-run overlay figure (Paper 2 revision: Table 5 + multi-run figure).

Usage: aggregate_mqtt_runs.py --base <path/to/experiment-b-stateful>
       (expects sibling dirs <base>-run1 ... <base>-runN)

Output: results/processed/mqtt/experiment-b-multi/mqtt_b_multirun.csv
        results/processed/mqtt/experiment-b-multi/multi_timeseries.png
"""
import argparse
import csv
import glob
import os
import statistics
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "results", "processed", "mqtt", "experiment-b-multi")


def read_alternating(path, cast=float):
    """Collector format: alternating lines of <timestamp> then <value>."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    for ts, val in zip(lines[0::2], lines[1::2]):
        try:
            rows.append((int(ts), cast(val)))
        except ValueError:
            continue
    return rows


def read_pods_running(path):
    """pods.log: <timestamp> line followed by pod rows; count Running pods."""
    rows = []
    if not os.path.exists(path):
        return rows
    ts, count = None, 0
    with open(path) as f:
        for line in f:
            line = line.rstrip()
            if line.isdigit():
                if ts is not None:
                    rows.append((ts, count))
                ts, count = int(line), 0
            elif "Running" in line:
                count += 1
    if ts is not None:
        rows.append((ts, count))
    return rows


def read_phases(path):
    phases = {}
    if not os.path.exists(path):
        return phases
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    phases.setdefault(parts[0], int(parts[1]))
                except ValueError:
                    continue
    return phases


def analyse(run_dir):
    conns = read_alternating(os.path.join(run_dir, "active_connections.log"))
    pods = read_pods_running(os.path.join(run_dir, "pods.log"))
    phases = read_phases(os.path.join(run_dir, "phases.log"))
    if not conns or not pods:
        return None
    m = {"run": os.path.basename(run_dir)}
    m["peak_connections"] = max(v for _, v in conns)
    p1 = phases.get("PHASE1_START")
    up = next((ts for ts, n in pods if p1 and ts >= p1 and n >= 3), None)
    m["scale_up_s"] = (up - p1) if (p1 and up) else ""
    p3 = phases.get("PHASE3_START")
    if p3:
        window = [(ts, v) for ts, v in conns if ts >= p3]
        # stop at teardown (first zero after Phase 3 starts)
        pre = []
        for ts, v in window:
            if v <= 0 and pre:
                break
            pre.append((ts, v))
        drops = [a[1] - b[1] for a, b in zip(pre, pre[1:]) if a[1] > b[1]]
        m["max_step_drop_phase3"] = max(drops) if drops else 0
        m["connections_preserved"] = pre[-1][1] if pre else ""
    else:
        m["max_step_drop_phase3"] = m["connections_preserved"] = ""
    return m, conns, pods


def mean_std(vals):
    vals = [float(v) for v in vals if v != ""]
    if not vals:
        return "", ""
    mu = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return round(mu, 1), round(sd, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="Base raw result dir (without -runN suffix)")
    args = ap.parse_args()

    run_dirs = sorted(glob.glob(args.base + "-run*"))
    if not run_dirs:
        sys.exit(f"No run dirs matching {args.base}-run*")

    os.makedirs(OUT, exist_ok=True)
    metrics, series = [], []
    for d in run_dirs:
        res = analyse(d)
        if res:
            m, conns, pods = res
            metrics.append(m)
            series.append((os.path.basename(d), conns, pods))

    cols = ["run", "scale_up_s", "connections_preserved", "peak_connections", "max_step_drop_phase3"]
    csv_path = os.path.join(OUT, "mqtt_b_multirun.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(metrics)
        for key in cols[1:]:
            mu, sd = mean_std([m[key] for m in metrics])
            w.writerow({"run": f"{key}_mean", key: mu})
            w.writerow({"run": f"{key}_std", key: sd})
    print(f"[ok] {csv_path}")
    for key in cols[1:]:
        mu, sd = mean_std([m[key] for m in metrics])
        print(f"  {key}: {mu} +/- {sd}  (n={len(metrics)})")

    # --- multi-run overlay figure (similar to Figure 7 / fig:exp_c_multi) ---
    fig, ax1 = plt.subplots(figsize=(9, 5))
    ax2 = ax1.twinx()
    for name, conns, pods in series:
        if not conns:
            continue
        t0 = conns[0][0]
        ax1.plot([t - t0 for t, _ in conns], [v for _, v in conns],
                 alpha=0.7, lw=1.2, label=name)
        if pods:
            ax2.step([t - t0 for t, _ in pods], [n for _, n in pods],
                     alpha=0.5, lw=1.0, linestyle="--")
    ax1.set_xlabel("Elapsed time (s)")
    ax1.set_ylabel("Active MQTT connections", color="tab:orange")
    ax2.set_ylabel("Broker replicas (dashed)", color="tab:blue")
    ax1.set_title(f"MQTT-B: {len(series)}-run overlay (connections solid, replicas dashed)")
    ax1.legend(fontsize=7, loc="upper right")
    ax1.grid(alpha=0.3)
    fig.tight_layout()
    png = os.path.join(OUT, "multi_timeseries.png")
    fig.savefig(png, dpi=150)
    print(f"[ok] {png}")


if __name__ == "__main__":
    main()
