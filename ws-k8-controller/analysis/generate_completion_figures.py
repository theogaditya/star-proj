#!/usr/bin/env python3
"""
generate_completion_figures.py
==============================
Tasks addressed:
  Task 1  — Extracts Run 1 per-run metrics from existing processed data
  Task 5  — Parameter sweep: convergence bound (Eq.3) across maxScaleDownStep × replica-gap
  Task 6  — Mean ± SD band plots for Figures 5,7,8,11 (Exp C, D, E, MQTT-B)

Outputs go to:
  results/processed/websocket/multi/<exp>/plots/mean_band.png
  Paper-Latex/processed-results-websockets/<exp-dir>/mean_band.png
  analysis/convergence_sweep_table.csv
  analysis/run1_metrics.txt
"""

from __future__ import annotations

import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_here       = os.path.dirname(os.path.abspath(__file__))
_root       = os.path.dirname(_here)

MULTI_BASE  = os.path.join(_root, "results", "processed", "websocket", "multi")
MQTT_BASE   = os.path.join(_root, "results", "processed", "mqtt")
LATEX_BASE  = os.path.join(_root, "Paper-Latex", "processed-results-websockets")

EXP_C = os.path.join(MULTI_BASE, "experiment-c-stateful")
EXP_D = os.path.join(MULTI_BASE, "experiment-d-hpa-custom-metric")
EXP_E = os.path.join(MULTI_BASE, "experiment-e-keda")

plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "legend.fontsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "font.family": "DejaVu Sans",
})

COL_CONN = "#e67e22"
COL_REP  = "#2980b9"

def _mkdir(p: str) -> str:
    os.makedirs(p, exist_ok=True)
    return p


def _savefig(fig: plt.Figure, *dest_dirs: str, fname: str) -> None:
    for d in dest_dirs:
        _mkdir(d)
        fig.savefig(os.path.join(d, fname), dpi=180, bbox_inches="tight")
    plt.close(fig)


def _load_run(exp_base: str, run_id: int, fname: str) -> pd.DataFrame:
    p = os.path.join(exp_base, f"run_{run_id}", fname)
    if not os.path.exists(p):
        return pd.DataFrame()
    return pd.read_csv(p)


def _rep_col(df: pd.DataFrame) -> str:
    for c in ("spec_replicas", "replicas"):
        if c in df.columns:
            return c
    return df.columns[1]


def _normalise_to_grid(series: pd.Series, timestamps: pd.Series,
                        t0: float, t_max: float, dt: float = 5.0) -> pd.Series:
    """Resample a (timestamp, value) series to a uniform grid [0, t_max] step dt."""
    grid = np.arange(0, t_max + dt, dt)
    t    = (timestamps - t0).values
    v    = series.values
    # forward-fill interpolation
    out  = np.full(len(grid), np.nan)
    j    = 0
    for i, gt in enumerate(grid):
        while j + 1 < len(t) and t[j + 1] <= gt:
            j += 1
        if j < len(t):
            out[i] = v[j]
    return pd.Series(out, index=grid)


# ===========================================================================
# Task 1 — Extract Run 1 metrics from processed summary.csv
# ===========================================================================
def extract_run1_metrics() -> dict:
    summary_path = os.path.join(EXP_C, "run_1", "summary.csv")
    if not os.path.exists(summary_path):
        print("  [WARN] Run 1 summary.csv not found — skipping Task 1")
        return {}

    df = pd.read_csv(summary_path)
    row = df.iloc[0]
    metrics = {
        "pod_seconds":          float(row.get("pod_seconds",          float("nan"))),
        "scale_up_reaction_s":  float(row.get("scale_up_reaction_s",  float("nan"))),
        "scale_down_reaction_s": float(row.get("scale_down_reaction_s", float("nan"))),
        "peak_connections":     float(row.get("peak_connections",     float("nan"))),
        "peak_replicas":        float(row.get("peak_replicas",        float("nan"))),
    }
    out_path = os.path.join(_here, "run1_metrics.txt")
    with open(out_path, "w") as f:
        f.write("Experiment C — Run 1 Per-Run Metrics (extracted from processed data)\n")
        f.write("=" * 70 + "\n")
        f.write(f"  Pod-seconds:           {metrics['pod_seconds']:.0f}\n")
        f.write(f"  Scale-up reaction (s): {metrics['scale_up_reaction_s']:.1f}\n")
        f.write(f"  Scale-down reaction (s): {metrics['scale_down_reaction_s']:.1f}\n")
        f.write(f"  Peak connections:      {metrics['peak_connections']:.0f}\n")
        f.write(f"  Peak replicas:         {metrics['peak_replicas']:.0f}\n")
        f.write("\n")
        f.write("Note: Run 1 pod-seconds elevated (92,291) due to test-harness artefact\n")
        f.write("(residual replicas from prior cluster invocation).\n")
        f.write("Correctness metrics (scale-up, scale-down, peak connections) are valid.\n")

    print(f"  [OK] Run 1 metrics → {out_path}")
    return metrics


# ===========================================================================
# Task 5 — Convergence bound parameter sweep
# ===========================================================================
def convergence_sweep() -> pd.DataFrame:
    """
    Compute theoretical bound: ceil((current - desired) / maxScaleDownStep)
    for a grid of (current_replicas, desired_replicas, maxScaleDownStep).
    Each cycle ≈ 5–15 s (controller reconciles every 5 s; scheduling may add latency).
    """
    configs = []
    for current in [4, 6, 8, 10, 12, 15]:
        for desired in [2]:
            for step in [1, 2, 3, 4]:
                if current <= desired:
                    continue
                gap   = current - desired
                cycles = math.ceil(gap / step)
                # lower bound: 5s/cycle (no scheduling overhead), upper: 15s
                t_lb  = cycles * 5
                t_ub  = cycles * 15
                configs.append({
                    "current_replicas":  current,
                    "desired_replicas":  desired,
                    "maxScaleDownStep":  step,
                    "replica_gap":       gap,
                    "theoretical_cycles": cycles,
                    "time_lower_s":      t_lb,
                    "time_upper_s":      t_ub,
                    "convergence_range": f"{t_lb}–{t_ub} s",
                })
    # Also add common patterns with non-2 desired
    for current, desired in [(10, 2), (15, 5), (8, 2), (12, 4), (16, 2), (32, 2)]:
        for step in [1, 2, 4]:
            if current <= desired:
                continue
            gap   = current - desired
            cycles = math.ceil(gap / step)
            t_lb  = cycles * 5
            t_ub  = cycles * 15
            configs.append({
                "current_replicas":  current,
                "desired_replicas":  desired,
                "maxScaleDownStep":  step,
                "replica_gap":       gap,
                "theoretical_cycles": cycles,
                "time_lower_s":      t_lb,
                "time_upper_s":      t_ub,
                "convergence_range": f"{t_lb}–{t_ub} s",
            })

    df = pd.DataFrame(configs).drop_duplicates(
        subset=["current_replicas", "desired_replicas", "maxScaleDownStep"]
    ).sort_values(["current_replicas", "desired_replicas", "maxScaleDownStep"])

    out_path = os.path.join(_here, "convergence_sweep_table.csv")
    df.to_csv(out_path, index=False)
    print(f"  [OK] Convergence sweep table → {out_path}")
    return df


def print_convergence_table(df: pd.DataFrame) -> None:
    print()
    print("=== Convergence Bound Parameter Sweep (Eq. 3) ===")
    print(f"{'current':>8} {'desired':>8} {'step':>6} {'gap':>5} {'cycles':>7} {'time (s)':>20}")
    print("-" * 62)
    for _, r in df.iterrows():
        print(f"{int(r['current_replicas']):>8} {int(r['desired_replicas']):>8} "
              f"{int(r['maxScaleDownStep']):>6} {int(r['replica_gap']):>5} "
              f"{int(r['theoretical_cycles']):>7}   {r['convergence_range']:>20}")
    print()


# ===========================================================================
# Task 6 — Mean ± SD band plots
# ===========================================================================

T_MAX_WS   = 570.0   # WebSocket experiment window (seconds)
T_MAX_MQTT = 600.0   # MQTT experiment window (seconds)
DT         = 5.0     # grid step (seconds)


def _mean_band_two_panel(
    exp_base: str,
    runs: list[int],
    title: str,
    conn_ylim: tuple | None = None,
    rep_ylim: tuple | None = None,
    t_max: float = T_MAX_WS,
    conn_target: float | None = 800.0,
    rep_target:  float | None = 8.0,
    latex_subdir: str | None = None,
    fname: str = "mean_band.png",
) -> None:
    """Two-panel (connections top, replicas bottom) mean ± SD band figure."""
    grid = np.arange(0, t_max + DT, DT)

    conn_matrix: list[np.ndarray] = []
    rep_matrix:  list[np.ndarray] = []

    for r in runs:
        conn_df = _load_run(exp_base, r, "connections.csv")
        rep_df  = _load_run(exp_base, r, "replicas.csv")
        if conn_df.empty or rep_df.empty:
            print(f"    [WARN] run_{r}: missing data, skipping")
            continue

        t0 = float(conn_df["timestamp"].min())

        conn_s = _normalise_to_grid(conn_df["active_connections"], conn_df["timestamp"], t0, t_max, DT)
        rc     = _rep_col(rep_df)
        rep_s  = _normalise_to_grid(rep_df[rc], rep_df["timestamp"], t0, t_max, DT)

        # forward-fill NaN
        conn_s = conn_s.ffill().bfill()
        rep_s  = rep_s.ffill().bfill()

        conn_matrix.append(conn_s.values)
        rep_matrix.append(rep_s.values)

    if not conn_matrix:
        print(f"  [WARN] No data for {title}, skipping")
        return

    conn_arr = np.array(conn_matrix)
    rep_arr  = np.array(rep_matrix)

    conn_mean = np.nanmean(conn_arr, axis=0)
    conn_std  = np.nanstd(conn_arr, axis=0, ddof=1) if len(conn_matrix) > 1 else np.zeros_like(conn_mean)
    rep_mean  = np.nanmean(rep_arr, axis=0)
    rep_std   = np.nanstd(rep_arr, axis=0, ddof=1) if len(rep_matrix) > 1 else np.zeros_like(rep_mean)

    fig, (ax_conn, ax_rep) = plt.subplots(2, 1, figsize=(7, 5.5), sharex=True)

    # --- connections panel ---
    ax_conn.plot(grid, conn_mean, color=COL_CONN, linewidth=2.0, label="Mean")
    ax_conn.fill_between(grid,
                          conn_mean - conn_std, conn_mean + conn_std,
                          color=COL_CONN, alpha=0.25, label="±1 SD")
    if conn_target is not None:
        ax_conn.axhline(conn_target, color="grey", linestyle="--", linewidth=0.8,
                         label=f"Target ({conn_target:.0f})")
    ax_conn.set_ylabel("Active Connections")
    ax_conn.legend(loc="upper right", ncol=3)
    ax_conn.grid(True, linestyle=":", alpha=0.5)
    if conn_ylim:
        ax_conn.set_ylim(*conn_ylim)

    # --- replicas panel ---
    ax_rep.plot(grid, rep_mean, color=COL_REP, linewidth=2.0, label="Mean")
    ax_rep.fill_between(grid,
                         rep_mean - rep_std, rep_mean + rep_std,
                         color=COL_REP, alpha=0.25, label="±1 SD")
    if rep_target is not None:
        ax_rep.axhline(rep_target, color="grey", linestyle="--", linewidth=0.8,
                        label=f"Expected ({rep_target:.0f})")
    ax_rep.set_ylabel("Replica Count")
    ax_rep.set_xlabel("Elapsed Time (s)")
    ax_rep.legend(loc="upper right", ncol=3)
    ax_rep.grid(True, linestyle=":", alpha=0.5)
    if rep_ylim:
        ax_rep.set_ylim(*rep_ylim)

    n_runs = len(conn_matrix)
    ax_conn.set_title(f"{title} — Connections  (n={n_runs}, mean ± 1 SD)")
    ax_rep.set_title(f"{title} — Replicas  (n={n_runs}, mean ± 1 SD)")

    fig.tight_layout(h_pad=2.0)

    dest_dirs = [_mkdir(os.path.join(exp_base, "plots"))]
    if latex_subdir:
        dest_dirs.append(_mkdir(os.path.join(LATEX_BASE, latex_subdir)))

    _savefig(fig, *dest_dirs, fname=fname)
    print(f"  [OK] Mean±SD band plot → {fname}")


def plot_exp_c_mean_band() -> None:
    _mean_band_two_panel(
        EXP_C, runs=[2, 3, 4, 5],
        title="Experiment C — StatefulAutoscaler (Runs 2–5)",
        conn_ylim=(0, 1050), rep_ylim=(0, 14),
        latex_subdir="experiment-c-multi",
        fname="mean_band.png",
    )


def plot_exp_d_mean_band() -> None:
    _mean_band_two_panel(
        EXP_D, runs=[1, 2, 3, 4, 5],
        title="Experiment D — HPA + Custom Metric (All 5 Runs)",
        conn_ylim=(0, 1100), rep_ylim=(0, 14),
        latex_subdir="experiment-d-multi",
        fname="mean_band.png",
    )


def plot_exp_e_mean_band() -> None:
    _mean_band_two_panel(
        EXP_E, runs=[1, 2, 3, 4, 5],
        title="Experiment E — KEDA (cooldownPeriod=120 s, All 5 Runs)",
        conn_ylim=(0, 1000), rep_ylim=(0, 14),
        latex_subdir="experiment-e-keda",
        fname="mean_band.png",
    )


def plot_mqtt_b_mean_band() -> None:
    """MQTT-B mean ± SD band.  Reads from mqtt processed multi directory."""
    mqtt_multi = os.path.join(MQTT_BASE, "experiment-b-stateful", "multi") \
        if os.path.exists(os.path.join(MQTT_BASE, "experiment-b-stateful", "multi")) \
        else os.path.join(_root, "results", "processed", "mqtt-multi", "experiment-b-stateful")

    # Attempt common locations
    candidates = [
        os.path.join(_root, "results", "processed", "mqtt", "experiment-b-stateful", "multi"),
        os.path.join(_root, "results", "processed", "mqtt-multi", "experiment-b-stateful"),
        os.path.join(MULTI_BASE.replace("websocket", "mqtt"), "experiment-b-stateful"),
    ]
    mqtt_exp_base = None
    for c in candidates:
        if os.path.isdir(c) and any(
            os.path.isdir(os.path.join(c, d)) for d in os.listdir(c) if d.startswith("run_")
        ):
            mqtt_exp_base = c
            break

    if mqtt_exp_base is None:
        # Fall back to the mqtt-multi analysis directory processed output
        mqtt_exp_base_alt = os.path.join(_root, "results", "processed", "mqtt-multi")
        if os.path.isdir(mqtt_exp_base_alt):
            mqtt_exp_base = mqtt_exp_base_alt
        else:
            print("  [SKIP] MQTT-B multi processed data not found — skipping MQTT mean±band plot")
            return

    _mean_band_two_panel(
        mqtt_exp_base, runs=[1, 2, 3, 4],
        title="MQTT-B — StatefulAutoscaler with Drain (4 Runs)",
        conn_ylim=(0, 450), rep_ylim=(0, 6),
        conn_target=339.0, rep_target=3.0,
        t_max=T_MAX_MQTT,
        latex_subdir="mqtt-b-multi",
        fname="mean_band.png",
    )


# ===========================================================================
# Main
# ===========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print(" Paper Completion Analysis")
    print("=" * 60)

    print("\n--- Task 1: Extracting Run 1 Metrics ---")
    run1 = extract_run1_metrics()
    if run1:
        print(f"  scale_up_reaction_s  = {run1['scale_up_reaction_s']:.1f} s")
        print(f"  scale_down_reaction_s= {run1['scale_down_reaction_s']:.1f} s")
        print(f"  peak_connections     = {run1['peak_connections']:.0f}")
        print(f"  peak_replicas        = {run1['peak_replicas']:.0f}")
        print(f"  pod_seconds          = {run1['pod_seconds']:.0f}")

    print("\n--- Task 5: Convergence Bound Parameter Sweep ---")
    sweep_df = convergence_sweep()
    print_convergence_table(sweep_df)

    print("\n--- Task 6: Generating Mean ± SD Band Plots ---")
    plot_exp_c_mean_band()
    plot_exp_d_mean_band()
    plot_exp_e_mean_band()
    plot_mqtt_b_mean_band()

    print("\n" + "=" * 60)
    print(" All tasks complete.")
    print("=" * 60)
