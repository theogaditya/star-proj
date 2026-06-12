#!/usr/bin/env python3
"""
generate_paper_figures.py
=========================
Generate all multi-run figures required for the paper:

  1. Experiment C — overlaid replica time-series for runs 2-5 (valid runs)
  2. Experiment C — overlaid connection time-series for runs 2-5
  3. Experiment D — overlaid replica time-series for all 5 runs
  4. Experiment D — overlaid connection time-series for all 5 runs
  5. Experiment E — overlaid replica time-series for all 5 runs
  6. Comparative: C vs D replica time-series (side-by-side two-panel)
  7. Failure scenarios comparative (3-panel)

All outputs go to:
  results/processed/websocket/multi/experiment-c-stateful/plots/
  results/processed/websocket/multi/experiment-d-hpa-custom-metric/plots/
  results/processed/websocket/multi/experiment-e-keda/plots/
  Paper-Latex/processed-results-websockets/experiment-c-multi/
  Paper-Latex/processed-results-websockets/experiment-d-multi/
  Paper-Latex/processed-results-websockets/experiment-e-keda/
  Paper-Latex/processed-results-websockets/comparison-c-d/
  Paper-Latex/processed-results-websockets/failure-scenarios/
"""

from __future__ import annotations
import os
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_here         = os.path.dirname(os.path.abspath(__file__))
_root         = os.path.dirname(_here)

MULTI_BASE    = os.path.join(_root, "results", "processed", "websocket", "multi")
SINGLE_BASE   = os.path.join(_root, "results", "processed", "websocket")
LATEX_BASE    = os.path.join(_root, "Paper-Latex", "processed-results-websockets")

EXP_C = os.path.join(MULTI_BASE, "experiment-c-stateful")
EXP_D = os.path.join(MULTI_BASE, "experiment-d-hpa-custom-metric")
EXP_E = os.path.join(MULTI_BASE, "experiment-e-keda")

# Colour palette — consistent with existing paper figures
COL_CONN   = "#e67e22"   # orange
COL_REP    = "#2980b9"   # blue
COL_CPU    = "#c0392b"   # red
COL_KEDA   = "#8e44ad"   # purple (KEDA ScaledObject status indicator)
ALPHAS     = [1.0, 0.75, 0.55, 0.40, 0.25]   # per-run alpha (most recent darkest)
RUN_COLORS = ["#2c3e50", "#2980b9", "#27ae60", "#e67e22", "#8e44ad"]

plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "legend.fontsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mkdir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _load_run(exp_base: str, run_id: int, fname: str) -> pd.DataFrame:
    p = os.path.join(exp_base, f"run_{run_id}", fname)
    if not os.path.exists(p):
        return pd.DataFrame()
    return pd.read_csv(p)


def _normalise(df: pd.DataFrame, t0: int | None = None) -> tuple[pd.DataFrame, int]:
    df = df.copy()
    if t0 is None:
        t0 = int(df["timestamp"].min())
    df["t"] = df["timestamp"] - t0
    return df, t0


def _rep_col(df: pd.DataFrame) -> str:
    """Return the name of the replica-count column regardless of schema."""
    for c in ("spec_replicas", "replicas"):
        if c in df.columns:
            return c
    # fallback: second column after timestamp
    return df.columns[1]


def _savefig(fig: plt.Figure, *dest_dirs: str, fname: str) -> None:
    for d in dest_dirs:
        _mkdir(d)
        fig.savefig(os.path.join(d, fname), dpi=150, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Figure 1+2 — Experiment C multi-run overlaid timeseries (runs 2-5)
# ---------------------------------------------------------------------------
def plot_exp_c_multi() -> None:
    valid_runs = [2, 3, 4, 5]
    fig, axes = plt.subplots(2, 1, figsize=(7, 5.5), sharex=True)
    ax_conn, ax_rep = axes

    for i, r in enumerate(valid_runs):
        conn_df = _load_run(EXP_C, r, "connections.csv")
        rep_df  = _load_run(EXP_C, r, "replicas.csv")
        if conn_df.empty or rep_df.empty:
            print(f"  [WARN] Exp C run_{r} missing data, skipping")
            continue

        conn_df, t0 = _normalise(conn_df)
        rep_df, _   = _normalise(rep_df, t0)

        label = f"Run {r}"
        col   = RUN_COLORS[i % len(RUN_COLORS)]

        ax_conn.plot(conn_df["t"], conn_df["active_connections"],
                     color=col, alpha=0.85, linewidth=1.6, label=label)
        ax_rep.step(rep_df["t"], rep_df[_rep_col(rep_df)],
                    where="post", color=col, alpha=0.85, linewidth=1.6, label=label)

    # Reference lines
    ax_conn.axhline(800, color="grey", linestyle="--", linewidth=0.8, label="Target (800)")
    ax_rep.axhline(8,   color="grey", linestyle="--", linewidth=0.8, label="Expected (8)")

    ax_conn.set_ylabel("Active Connections")
    ax_rep.set_ylabel("Replica Count")
    ax_rep.set_xlabel("Elapsed Time (s)")

    ax_conn.set_title("Experiment C — StatefulAutoscaler: Connections (Runs\u202f2\u20135)")
    ax_rep.set_title("Experiment C — StatefulAutoscaler: Replicas (Runs\u202f2\u20135)")

    ax_conn.legend(loc="upper right", ncol=2)
    ax_rep.legend(loc="upper right", ncol=2)

    ax_conn.grid(True, linestyle=":", alpha=0.5)
    ax_rep.grid(True, linestyle=":", alpha=0.5)

    fig.tight_layout(h_pad=2.0)

    out_proc  = _mkdir(os.path.join(EXP_C, "plots"))
    out_latex = _mkdir(os.path.join(LATEX_BASE, "experiment-c-multi"))
    _savefig(fig, out_proc, out_latex, fname="multi_timeseries.png")
    print("  [OK] Exp C multi timeseries")


# ---------------------------------------------------------------------------
# Figure 3+4 — Experiment D multi-run overlaid timeseries (all 5 runs)
# ---------------------------------------------------------------------------
def plot_exp_d_multi() -> None:
    runs = [1, 2, 3, 4, 5]
    fig, axes = plt.subplots(2, 1, figsize=(7, 5.5), sharex=True)
    ax_conn, ax_rep = axes

    for i, r in enumerate(runs):
        conn_df = _load_run(EXP_D, r, "connections.csv")
        rep_df  = _load_run(EXP_D, r, "replicas.csv")
        if conn_df.empty or rep_df.empty:
            print(f"  [WARN] Exp D run_{r} missing data, skipping")
            continue

        conn_df, t0 = _normalise(conn_df)
        rep_df, _   = _normalise(rep_df, t0)

        label  = f"Run {r}" if r != 3 else "Run 3 (anomalous scale-up)"
        col    = RUN_COLORS[i % len(RUN_COLORS)]
        ls     = "--" if r == 3 else "-"

        ax_conn.plot(conn_df["t"], conn_df["active_connections"],
                     color=col, alpha=0.85, linewidth=1.6, linestyle=ls, label=label)
        ax_rep.step(rep_df["t"], rep_df[_rep_col(rep_df)],
                    where="post", color=col, alpha=0.85, linewidth=1.6, linestyle=ls, label=label)

    ax_conn.axhline(800, color="grey", linestyle=":", linewidth=0.8, label="Target (800)")
    ax_rep.axhline(8,   color="grey", linestyle=":", linewidth=0.8, label="Expected (8)")

    ax_conn.set_ylabel("Active Connections")
    ax_rep.set_ylabel("Replica Count")
    ax_rep.set_xlabel("Elapsed Time (s)")

    ax_conn.set_title("Experiment D — HPA + Custom Metric: Connections (All\u202f5\u202fRuns)")
    ax_rep.set_title("Experiment D — HPA + Custom Metric: Replicas (All\u202f5\u202fRuns)")

    ax_conn.legend(loc="upper right", ncol=2, fontsize=7.5)
    ax_rep.legend(loc="upper right", ncol=2, fontsize=7.5)

    ax_conn.grid(True, linestyle=":", alpha=0.5)
    ax_rep.grid(True, linestyle=":", alpha=0.5)

    fig.tight_layout(h_pad=2.0)

    out_proc  = _mkdir(os.path.join(EXP_D, "plots"))
    out_latex = _mkdir(os.path.join(LATEX_BASE, "experiment-d-multi"))
    _savefig(fig, out_proc, out_latex, fname="multi_timeseries.png")
    print("  [OK] Exp D multi timeseries")


# ---------------------------------------------------------------------------
# Figure 5 — Experiment E multi-run overlaid (all 5 runs)
# ---------------------------------------------------------------------------
def plot_exp_e_multi() -> None:
    runs = [1, 2, 3, 4, 5]
    fig, axes = plt.subplots(2, 1, figsize=(7, 5.5), sharex=True)
    ax_conn, ax_rep = axes

    for i, r in enumerate(runs):
        conn_df = _load_run(EXP_E, r, "connections.csv")
        rep_df  = _load_run(EXP_E, r, "replicas.csv")
        if conn_df.empty or rep_df.empty:
            print(f"  [WARN] Exp E run_{r} missing data, skipping")
            continue

        conn_df, t0 = _normalise(conn_df)
        rep_df, _   = _normalise(rep_df, t0)

        label = f"Run {r}"
        col   = RUN_COLORS[i % len(RUN_COLORS)]

        ax_conn.plot(conn_df["t"], conn_df["active_connections"],
                     color=col, alpha=0.85, linewidth=1.6, label=label)
        ax_rep.step(rep_df["t"], rep_df[_rep_col(rep_df)],
                    where="post", color=col, alpha=0.85, linewidth=1.6, label=label)

    ax_conn.axhline(800, color="grey", linestyle="--", linewidth=0.8, label="Target (800)")
    ax_rep.axhline(8,   color="grey", linestyle="--", linewidth=0.8, label="Expected (8)")

    ax_conn.set_ylabel("Active Connections")
    ax_rep.set_ylabel("Replica Count")
    ax_rep.set_xlabel("Elapsed Time (s)")

    ax_conn.set_title("Experiment E — KEDA (cooldownPeriod=120\u202fs): Connections (All\u202f5\u202fRuns)")
    ax_rep.set_title("Experiment E — KEDA (cooldownPeriod=120\u202fs): Replicas (All\u202f5\u202fRuns)")

    ax_conn.legend(loc="upper right", ncol=2)
    ax_rep.legend(loc="upper right", ncol=2)

    ax_conn.grid(True, linestyle=":", alpha=0.5)
    ax_rep.grid(True, linestyle=":", alpha=0.5)

    fig.tight_layout(h_pad=2.0)

    out_proc  = _mkdir(os.path.join(EXP_E, "plots"))
    out_latex = _mkdir(os.path.join(LATEX_BASE, "experiment-e-keda"))
    _savefig(fig, out_proc, out_latex, fname="multi_timeseries.png")
    print("  [OK] Exp E multi timeseries")


# ---------------------------------------------------------------------------
# Figure 6 — C vs D comparative replica overlay (2-panel side-by-side)
# ---------------------------------------------------------------------------
def plot_comparison_c_vs_d() -> None:
    """
    Two-panel figure: left = Exp C run_3 (representative); right = Exp D run_2
    (representative, 33s scale-up). Both normalised to same time axis.
    Both C and D hold replicas at 8 through DROP 1 (~50s zero-window).
    Key visible differences:
      - C has a transient dip to 5-6 at the start of CYCLE 2 (window-boundary)
      - D stays flat at 8 throughout (300s stabilisation window, no boundary effect)
      - C scales up faster in CYCLE 1 (22s vs 54s) and scales down faster in FINAL DROP (119s vs 313s)
    """
    REP_RUN_C = 3   # median pod_seconds for C (4319)
    REP_RUN_D = 2   # 33s scale-up for D, clean run

    def _load_rep(base, run_id):
        conn = _load_run(base, run_id, "connections.csv")
        rep  = _load_run(base, run_id, "replicas.csv")
        return conn, rep

    conn_c, rep_c = _load_rep(EXP_C, REP_RUN_C)
    conn_d, rep_d = _load_rep(EXP_D, REP_RUN_D)

    if conn_c.empty or rep_c.empty or conn_d.empty or rep_d.empty:
        print("  [WARN] comparison C vs D: missing data, skipping")
        return

    conn_c, t0_c = _normalise(conn_c)
    rep_c,  _    = _normalise(rep_c, t0_c)
    conn_d, t0_d = _normalise(conn_d)
    rep_d,  _    = _normalise(rep_d, t0_d)

    fig, (ax_c, ax_d) = plt.subplots(1, 2, figsize=(10, 3.8), sharey=False)

    for ax, conn, rep, title in [
        (ax_c, conn_c, rep_c, "(C) StatefulAutoscaler\u2014run\u202f3"),
        (ax_d, conn_d, rep_d, "(D) HPA + Custom Metric\u2014run\u202f2"),
    ]:
        ax2 = ax.twinx()
        ax.plot(conn["t"], conn["active_connections"],
                color=COL_CONN, linewidth=1.8, label="Connections")
        ax2.step(rep["t"], rep[_rep_col(rep)],
                 where="post", color=COL_REP, linewidth=1.8, label="Replicas")

        ax.axhline(800, color="grey", linestyle=":", linewidth=0.8)
        ax2.axhline(8,  color=COL_REP, linestyle="--", linewidth=0.7, alpha=0.4)

        ax.set_xlabel("Elapsed Time (s)")
        ax.set_ylabel("Active Connections", color=COL_CONN)
        ax2.set_ylabel("Replica Count", color=COL_REP)
        ax.tick_params(axis="y", labelcolor=COL_CONN)
        ax2.tick_params(axis="y", labelcolor=COL_REP)
        ax.set_title(title, fontsize=9)
        ax.grid(True, linestyle=":", alpha=0.4)

    fig.suptitle(
        "C vs. D: Both controllers hold 8 replicas through DROP\u202f1 (\u223c50\u202fs zero-window).\n"
        "Key differences: scale-up latency (C\u202f22\u202fs vs. D\u202f54\u202fs) and CYCLE\u202f2 transition (C dips to\u202f5\u20116; D flat at\u202f8)",
        fontsize=9.5, y=1.01,
    )
    fig.tight_layout()

    out_proc  = _mkdir(os.path.join(MULTI_BASE, "comparison-c-vs-d"))
    out_latex = _mkdir(os.path.join(LATEX_BASE, "comparison-c-d"))
    _savefig(fig, out_proc, out_latex, fname="comparison_c_vs_d.png")
    print("  [OK] C vs D comparison")


# ---------------------------------------------------------------------------
# Figure 7 — Failure scenarios comparative (3-panel)
#   Re-runs the failure-scenarios plotter to produce failure-comparative.png
# ---------------------------------------------------------------------------
def plot_failure_comparative() -> None:
    SCENARIOS = [
        ("failure-1-metric-staleness",  "Failure 1\nMetric Staleness (60\u202fs scrape)"),
        ("failure-2-instant-spike",     "Failure 2\nInstant Spike (RAMP_UP=0)"),
        ("failure-3-prometheus-outage", "Failure 3\nPrometheus Outage (120\u202fs)"),
    ]

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))

    for ax, (sid, title) in zip(axes, SCENARIOS):
        proc = os.path.join(SINGLE_BASE, sid)
        conn_df = pd.read_csv(os.path.join(proc, "connections.csv")) if os.path.exists(os.path.join(proc, "connections.csv")) else pd.DataFrame()
        rep_df  = pd.read_csv(os.path.join(proc, "replicas.csv"))  if os.path.exists(os.path.join(proc, "replicas.csv"))  else pd.DataFrame()

        if conn_df.empty or rep_df.empty:
            ax.set_title(title + "\n[data missing]", fontsize=8)
            continue

        t0 = min(conn_df["timestamp"].min(), rep_df["timestamp"].min())
        conn_df["t"] = conn_df["timestamp"] - t0
        rep_df["t"]  = rep_df["timestamp"]  - t0

        ax2 = ax.twinx()
        ax.plot(conn_df["t"], conn_df["active_connections"],
                color=COL_CONN, linewidth=1.5, label="Connections")

        rep_col = "spec_replicas" if "spec_replicas" in rep_df.columns else rep_df.columns[1]
        ax2.step(rep_df["t"], rep_df[rep_col],
                 where="post", color=COL_REP, linewidth=1.5, label="Replicas")

        ax.set_xlabel("Elapsed Time (s)", fontsize=8)
        ax.set_ylabel("Connections", color=COL_CONN, fontsize=8)
        ax2.set_ylabel("Replicas", color=COL_REP, fontsize=8)
        ax.tick_params(axis="y", labelcolor=COL_CONN, labelsize=7)
        ax2.tick_params(axis="y", labelcolor=COL_REP,  labelsize=7)
        ax.set_title(title, fontsize=8.5)
        ax.grid(True, linestyle=":", alpha=0.4)

    fig.suptitle("Failure Scenario Analysis: StatefulAutoscaler Robustness Under Adversarial Conditions",
                 fontsize=9, y=1.02)
    fig.tight_layout()

    out_proc  = _mkdir(SINGLE_BASE)
    out_latex = _mkdir(os.path.join(LATEX_BASE, "failure-scenarios"))
    _savefig(fig, out_proc, out_latex, fname="failure_comparative.png")
    print("  [OK] Failure scenarios comparative")


# ---------------------------------------------------------------------------
# ALSO: copy per-run D and E plots (run_2/plots/combined.png as representative)
# ---------------------------------------------------------------------------
def copy_representative_plots() -> None:
    """
    Copy representative single-run combined.png plots for D and E to Paper-Latex.
    Use run_2 for D (clean run, 33s scale-up) and run_3 for E (middle run).
    """
    pairs = [
        (os.path.join(EXP_D, "run_2", "plots", "combined.png"),
         os.path.join(LATEX_BASE, "experiment-d-multi", "representative_combined.png")),
        (os.path.join(EXP_D, "run_2", "plots", "replicas.png"),
         os.path.join(LATEX_BASE, "experiment-d-multi", "representative_replicas.png")),
        (os.path.join(EXP_E, "run_3", "plots", "combined.png"),
         os.path.join(LATEX_BASE, "experiment-e-keda", "representative_combined.png")),
        (os.path.join(EXP_E, "run_3", "plots", "replicas.png"),
         os.path.join(LATEX_BASE, "experiment-e-keda", "representative_replicas.png")),
        # failure individual combined plots
        (os.path.join(SINGLE_BASE, "failure-1-metric-staleness", "combined.png"),
         os.path.join(LATEX_BASE, "failure-scenarios", "failure-1-combined.png")),
        (os.path.join(SINGLE_BASE, "failure-2-instant-spike", "combined.png"),
         os.path.join(LATEX_BASE, "failure-scenarios", "failure-2-combined.png")),
        (os.path.join(SINGLE_BASE, "failure-3-prometheus-outage", "combined.png"),
         os.path.join(LATEX_BASE, "failure-scenarios", "failure-3-combined.png")),
    ]
    for src, dst in pairs:
        if os.path.exists(src):
            _mkdir(os.path.dirname(dst))
            shutil.copy2(src, dst)
            print(f"  [OK] copied {os.path.basename(dst)}")
        else:
            print(f"  [WARN] source missing: {src}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Generating paper figures...")
    plot_exp_c_multi()
    plot_exp_d_multi()
    plot_exp_e_multi()
    plot_comparison_c_vs_d()
    plot_failure_comparative()
    copy_representative_plots()
    print("Done.")
