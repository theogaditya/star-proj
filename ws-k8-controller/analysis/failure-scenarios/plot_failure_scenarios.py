#!/usr/bin/env python3
"""
plot_failure_scenarios.py — Generate plots for all 3 failure scenarios.

Outputs (per scenario):
  results/processed/websocket/<scenario>/combined.png
    — connections (left axis, orange) + replicas (right axis, blue)

Comparative output:
  results/processed/websocket/failure-comparative.png
    — 3-column figure, one column per scenario, connections + replicas overlay
"""

from __future__ import annotations

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import pandas as pd

_here         = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(_here))
PROC_BASE     = os.path.join(_project_root, "results", "processed", "websocket")

SCENARIOS = [
    ("failure-1-metric-staleness", "Metric Staleness\n(scrape_interval=60s)"),
    ("failure-2-instant-spike",    "Instant Spike\n(RAMP_UP=0)"),
    ("failure-3-prometheus-outage", "Prometheus Outage\n(120s kill)"),
]

# Colour palette
COL_CONN = "#e67e22"   # orange — connections
COL_REP  = "#2980b9"   # blue   — replicas
COL_VLINE= "#c0392b"   # red    — phase markers / events


def _load(scenario_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (connections_df, replicas_df, phases_df) — empty DF if missing."""
    proc = os.path.join(PROC_BASE, scenario_id)

    def read(name: str) -> pd.DataFrame:
        p = os.path.join(proc, name)
        return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()

    return read("connections.csv"), read("replicas.csv"), read("phases.csv")


def _normalise_time(df: pd.DataFrame, t0: int, col: str = "timestamp") -> pd.DataFrame:
    df = df.copy()
    df[col] = df[col] - t0
    return df


def _t0(*dfs: pd.DataFrame) -> int:
    mins = [int(df["timestamp"].min()) for df in dfs if not df.empty]
    return min(mins) if mins else 0


def plot_individual(scenario_id: str, title: str) -> None:
    conn, reps, phases = _load(scenario_id)
    proc = os.path.join(PROC_BASE, scenario_id)

    if conn.empty and reps.empty:
        print(f"  SKIP {scenario_id}: no data")
        return

    t0 = _t0(conn, reps, phases)
    if not conn.empty:
        conn = _normalise_time(conn, t0)
    if not reps.empty:
        reps = _normalise_time(reps, t0)
    if not phases.empty:
        phases = _normalise_time(phases, t0)

    fig, ax1 = plt.subplots(figsize=(10, 5))
    ax2 = ax1.twinx()

    if not conn.empty:
        ax1.plot(conn["timestamp"], conn["active_connections"],
                 color=COL_CONN, linewidth=1.8, label="Active Connections")

    if not reps.empty:
        ax2.step(reps["timestamp"], reps["replicas"],
                 color=COL_REP, linewidth=2, where="post", label="Replicas")

    # Phase markers
    if not phases.empty:
        for _, row in phases.iterrows():
            t   = row["timestamp"]
            tag = row["phase"].strip()
            ax1.axvline(x=t, color=COL_VLINE, linewidth=1, linestyle="--", alpha=0.7)
            ax1.text(t + 2, ax1.get_ylim()[1] * 0.92, tag,
                     color=COL_VLINE, fontsize=7, rotation=90, va="top")

    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Active Connections", color=COL_CONN)
    ax2.set_ylabel("Replicas", color=COL_REP)
    ax1.tick_params(axis="y", labelcolor=COL_CONN)
    ax2.tick_params(axis="y", labelcolor=COL_REP)
    ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right", fontsize=9)

    ax1.set_title(f"Failure Scenario: {title.replace(chr(10), ' ')}", fontsize=12)
    ax1.grid(True, linestyle=":", alpha=0.5)
    fig.tight_layout()

    out = os.path.join(proc, "combined.png")
    plt.savefig(out, dpi=150)
    plt.close(fig)
    print(f"  Saved → {out}")


def plot_comparative() -> None:
    n = len(SCENARIOS)
    fig, axes = plt.subplots(1, n, figsize=(6 * n, 5), sharey=False)
    if n == 1:
        axes = [axes]

    for ax, (scenario_id, title) in zip(axes, SCENARIOS):
        conn, reps, phases = _load(scenario_id)

        if conn.empty and reps.empty:
            ax.text(0.5, 0.5, "No data", ha="center", va="center",
                    transform=ax.transAxes)
            ax.set_title(title)
            continue

        t0 = _t0(conn, reps, phases)
        if not conn.empty:
            conn = _normalise_time(conn, t0)
        if not reps.empty:
            reps = _normalise_time(reps, t0)
        if not phases.empty:
            phases = _normalise_time(phases, t0)

        ax2 = ax.twinx()

        if not conn.empty:
            ax.plot(conn["timestamp"], conn["active_connections"],
                    color=COL_CONN, linewidth=1.5, label="Connections")
        if not reps.empty:
            ax2.step(reps["timestamp"], reps["replicas"],
                     color=COL_REP, linewidth=2, where="post", label="Replicas")

        if not phases.empty:
            y_top = conn["active_connections"].max() if not conn.empty else 100
            for _, row in phases.iterrows():
                t = row["timestamp"]
                tag = row["phase"].strip()
                ax.axvline(x=t, color=COL_VLINE, linewidth=0.8, linestyle="--", alpha=0.7)
                ax.text(t + 1, y_top * 0.92, tag, color=COL_VLINE,
                        fontsize=6, rotation=90, va="top")

        ax.set_xlabel("Time (s)", fontsize=9)
        ax.set_ylabel("Connections", color=COL_CONN, fontsize=9)
        ax2.set_ylabel("Replicas", color=COL_REP, fontsize=9)
        ax.tick_params(axis="y", labelcolor=COL_CONN)
        ax2.tick_params(axis="y", labelcolor=COL_REP)
        ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax.set_title(title, fontsize=10)
        ax.grid(True, linestyle=":", alpha=0.5)

    fig.suptitle("Failure Scenario Comparison", fontsize=13, y=1.02)
    fig.tight_layout()

    out = os.path.join(PROC_BASE, "failure-comparative.png")
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Comparative plot → {out}")


if __name__ == "__main__":
    print("Plotting failure scenarios…")
    for scenario_id, title in SCENARIOS:
        print(f"  {scenario_id}")
        plot_individual(scenario_id, title)
    plot_comparative()
    print("Done.")
