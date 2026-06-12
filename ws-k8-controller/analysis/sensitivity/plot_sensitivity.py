#!/usr/bin/env python3
"""Plot the sensitivity-analysis results for Paper 2.

Produces (under results/processed/websocket/sensitivity/plots/):
  - safe_zone_boundary.png : gap vs cooldown, safe/unsafe markers (KEY plot)
  - t_sweep.png            : scale-up time and blast radius vs T
  - scrape_sweep.png       : scale-up/scale-down reaction vs scrape interval
  - step_sweep.png         : scale-down duration and connections-at-risk vs step
"""
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.environ.get("SENS_OUT", os.path.join(ROOT, "results", "processed", "websocket", "sensitivity"))
PLOTS = os.path.join(OUT, "plots")
CSV = os.path.join(OUT, "sensitivity_results.csv")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load():
    with open(CSV) as f:
        return list(csv.DictReader(f))


def save(fig, name):
    path = os.path.join(PLOTS, name)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"[ok] {path}")


def plot_safe_zone(rows):
    rows = [r for r in rows if r["sweep"] == "cooldown" and r["safe"] != ""]
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(7, 5))
    for r in rows:
        safe = int(r["safe"]) == 1
        ax.scatter(num(r["cooldown"]), num(r["gap"]),
                   c="tab:green" if safe else "tab:red",
                   marker="o" if safe else "x", s=90, zorder=3)
    cds = sorted({num(r["cooldown"]) for r in rows})
    ax.plot(cds, cds, "k--", lw=1, label="gap = cooldown (theoretical boundary)")
    ax.fill_between(cds, 0, cds, alpha=0.08, color="green")
    ax.scatter([], [], c="tab:green", marker="o", label="safe (pods held through gap)")
    ax.scatter([], [], c="tab:red", marker="x", label="unsafe (scale-down during gap)")
    ax.set_xlabel("scaleDownCooldownSeconds (s)")
    ax.set_ylabel("Connection gap duration (s)")
    ax.set_title("Safe-zone boundary: cooldown vs disconnection gap")
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)
    save(fig, "safe_zone_boundary.png")


def plot_xy(rows, sweep, xkey, series, title, xlabel, fname):
    rows = sorted([r for r in rows if r["sweep"] == sweep], key=lambda r: num(r[xkey]) or 0)
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(7, 4.5))
    axes = [ax]
    if len(series) > 1:
        axes.append(ax.twinx())
    colors = ["tab:blue", "tab:orange"]
    for (key, label), a, c in zip(series, axes, colors):
        xs = [num(r[xkey]) for r in rows if num(r[key]) is not None]
        ys = [num(r[key]) for r in rows if num(r[key]) is not None]
        a.plot(xs, ys, "o-", color=c, label=label)
        a.set_ylabel(label, color=c)
    ax.set_xlabel(xlabel)
    ax.set_title(title)
    ax.grid(alpha=0.3)
    save(fig, fname)


def main():
    if not os.path.exists(CSV):
        sys.exit(f"Run parse_sensitivity.py first ({CSV} missing).")
    os.makedirs(PLOTS, exist_ok=True)
    rows = load()
    plot_safe_zone(rows)
    plot_xy(rows, "target", "target",
            [("scale_up_s", "Scale-up time (s)"), ("blast_radius_conns", "Blast radius (conns/cycle)")],
            "Sensitivity to targetConnectionsPerPod (T)", "T (connections per pod)", "t_sweep.png")
    plot_xy(rows, "scrape", "scrape",
            [("scale_up_s", "Scale-up reaction (s)"), ("scaledown_reaction_s", "Scale-down reaction (s)")],
            "Sensitivity to Prometheus scrape interval", "Scrape interval (s)", "scrape_sweep.png")
    plot_xy(rows, "step", "step",
            [("scaledown_duration_s", "Scale-down duration (s)"), ("blast_radius_conns", "Connections at risk per cycle")],
            "Sensitivity to maxScaleDownStep", "maxScaleDownStep", "step_sweep.png")


if __name__ == "__main__":
    main()
