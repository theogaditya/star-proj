#!/usr/bin/env python3
"""Scalability summary for Paper 2: Experiment C at 800/1600/3200 clients.

Reads the 'scale' sweep rows from the shared sensitivity_results.csv and
produces a summary plot + table. Documents the Kind OS-level TCP ceiling by
comparing attempted client count vs achieved peak connections.
"""
import csv
import os
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SENS = os.path.join(ROOT, "results", "processed", "websocket", "sensitivity")
OUT = os.path.join(ROOT, "results", "processed", "websocket", "scalability")
PLOTS = os.path.join(OUT, "plots")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    src = os.path.join(SENS, "sensitivity_results.csv")
    if not os.path.exists(src):
        sys.exit(f"Run parse_sensitivity.py first ({src} missing).")
    with open(src) as f:
        rows = [r for r in csv.DictReader(f) if r["sweep"] == "scale"]
    if not rows:
        sys.exit("No 'scale' sweep rows found.")
    rows.sort(key=lambda r: num(r["clients"]) or 0)

    os.makedirs(PLOTS, exist_ok=True)
    shutil.copy(os.path.join(SENS, "table_scale.csv"), os.path.join(OUT, "table_scale.csv"))

    clients = [num(r["clients"]) for r in rows]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

    axes[0].plot(clients, [num(r["expected_replicas"]) for r in rows], "k--o", label="expected ceil(N/T)")
    axes[0].plot(clients, [num(r["peak_replicas"]) for r in rows], "o-", color="tab:blue", label="achieved peak")
    axes[0].set_title("Replica targeting")
    axes[0].set_xlabel("Attempted clients"); axes[0].set_ylabel("Replicas"); axes[0].legend(fontsize=8)

    axes[1].plot(clients, [num(r["scale_up_s"]) for r in rows], "o-", color="tab:green")
    axes[1].set_title("Scale-up reaction time")
    axes[1].set_xlabel("Attempted clients"); axes[1].set_ylabel("Seconds")

    axes[2].plot(clients, clients, "k--", label="attempted")
    axes[2].plot(clients, [num(r["peak_connections"]) for r in rows], "o-", color="tab:red", label="achieved peak")
    axes[2].set_title("OS TCP ceiling (attempted vs achieved)")
    axes[2].set_xlabel("Attempted clients"); axes[2].set_ylabel("Connections"); axes[2].legend(fontsize=8)

    for ax in axes:
        ax.grid(alpha=0.3)
    fig.tight_layout()
    png = os.path.join(PLOTS, "scalability_summary.png")
    fig.savefig(png, dpi=150)
    print(f"[ok] {png}")
    for r in rows:
        print(f"  clients={r['clients']} expected={r['expected_replicas']} "
              f"peak_replicas={r['peak_replicas']} scale_up={r['scale_up_s']}s "
              f"peak_conns={r['peak_connections']} safe_gap={r['safe']}")


if __name__ == "__main__":
    main()
