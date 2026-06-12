"""
Generate plots for Experiment-D (HPA + Custom Connection-Count Metric).

Reads CSVs produced by parse_logs_experiment_d.py and saves PNG plots in
PROCESSED_DIR/plots/.

Produces:
  connections.png  - active_connections over time
  replicas.png     - spec / HPA current / HPA desired replicas over time
  reconnections.png- reconnection rate over time
  combined.png     - overlay of all three signals

Usage:
  RAW_DIR=... PROCESSED_DIR=... python plot_experiment_d.py
  or just: python plot_experiment_d.py  (uses default paths)
"""

import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW_DIR = os.environ.get(
    "RAW_DIR",
    os.path.join(_base, "results", "raw", "websocket", "experiment-d-hpa-custom-metric"),
)
PROCESSED_DIR = os.environ.get(
    "PROCESSED_DIR",
    os.path.join(_base, "results", "processed", "websocket", "experiment-d-hpa-custom-metric"),
)

PLOTS_DIR = os.path.join(PROCESSED_DIR, "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)

print(f"PROCESSED_DIR: {PROCESSED_DIR}")
print(f"PLOTS_DIR:     {PLOTS_DIR}")


# --------------------------------------------------
# Load CSVs
# --------------------------------------------------
replicas_path = os.path.join(PROCESSED_DIR, "replicas.csv")
connections_path = os.path.join(PROCESSED_DIR, "connections.csv")

for path, name in [(replicas_path, "replicas.csv"), (connections_path, "connections.csv")]:
    if not os.path.exists(path):
        raise RuntimeError(f"{name} not found in {PROCESSED_DIR}. Run parse_logs_experiment_d.py first.")

replicas = pd.read_csv(replicas_path)
connections = pd.read_csv(connections_path)

# Use spec_replicas as primary replica signal; fall back to hpa_current
replicas["replicas"] = replicas["spec_replicas"].combine_first(replicas["hpa_current"])


# --------------------------------------------------
# Normalize timestamps to t=0
# --------------------------------------------------
global_start = min(replicas["timestamp"].min(), connections["timestamp"].min())

def norm(df):
    df = df.copy()
    df["time_sec"] = df["timestamp"] - global_start
    return df


# --------------------------------------------------
# Multi-run helper
# --------------------------------------------------
def compute_multi_timeseries(proc_dir, filename, resample_s=1.0):
    # Centralized layout: results/processed/<workload>/multi/<experiment>/run_*/<file>
    multi_dir = os.path.join(os.path.dirname(proc_dir), "multi", os.path.basename(proc_dir))
    if not os.path.isdir(multi_dir):
        return None
    runs = sorted([d for d in os.listdir(multi_dir) if d.startswith("run_")])
    if not runs:
        return None
    series_list = []
    max_durations = []
    for r in runs:
        p = os.path.join(multi_dir, r, filename)
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p)
        if df.empty or "timestamp" not in df.columns:
            continue
        t0 = df["timestamp"].min()
        t = (df["timestamp"] - t0).to_numpy()
        val_cols = [c for c in df.columns if c not in ("timestamp", "time_sec")]
        if not val_cols:
            continue
        y = df[val_cols[0]].to_numpy()
        series_list.append((t, y))
        max_durations.append(t.max())
    if not series_list:
        return None
    end_t = min(max_durations)
    grid = np.arange(0, end_t + 1e-9, resample_s)
    arr = np.full((len(series_list), grid.size), np.nan, dtype=float)
    for i, (t, y) in enumerate(series_list):
        try:
            arr[i, :] = np.interp(grid, t, y, left=np.nan, right=np.nan)
        except Exception:
            arr[i, :] = np.nan
    mean = np.nanmean(arr, axis=0)
    std = np.nanstd(arr, axis=0)
    return grid, mean, std

replicas = norm(replicas)
connections = norm(connections)


# --------------------------------------------------
# Phase markers (from phase.log if it exists)
# --------------------------------------------------
phase_markers, phase_labels = [], []
phase_file = os.path.join(RAW_DIR, "phase.log")
if os.path.exists(phase_file):
    with open(phase_file) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) == 2:
                try:
                    phase_markers.append(int(parts[0]) - global_start)
                    phase_labels.append(parts[1])
                except ValueError:
                    pass

_PHASE_COLORS = {"CYCLE_1": "#2196F3", "DROP_1": "#FF9800",
                 "CYCLE_2": "#2196F3", "FINAL_DROP": "#FF9800"}

def draw_phases(ax):
    for ts, label in zip(phase_markers, phase_labels):
        ax.axvline(x=ts, linestyle="--", linewidth=0.9,
                   color=_PHASE_COLORS.get(label, "#999999"), alpha=0.7)
        ax.text(ts + 1, ax.get_ylim()[1] * 0.95, label,
                fontsize=6, color=_PHASE_COLORS.get(label, "#666666"), rotation=90,
                va="top", alpha=0.8)


# --------------------------------------------------
# Plot 1: Active Connections
# --------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 4))
multi = compute_multi_timeseries(PROCESSED_DIR, "connections.csv")
if multi is not None:
    grid, mean, std = multi
    ax.plot(grid, mean, color="#1E88E5", linewidth=1.4, label="Active Connections")
    ax.fill_between(grid, mean - std, mean + std, color="#1E88E5", alpha=0.2)
else:
    ax.plot(connections["time_sec"], connections["active_connections"],
            color="#1E88E5", linewidth=1.4, label="Active Connections")
draw_phases(ax)
ax.set_title("Experiment-D: Active Connections Over Time (HPA + Custom Metric)")
ax.set_xlabel("Time (seconds)")
ax.set_ylabel("Active Connections")
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(PLOTS_DIR, "connections.png"), dpi=150)
plt.close(fig)
print("  Generated: connections.png")


# --------------------------------------------------
# Plot 2: Replicas (spec / HPA current / HPA desired)
# --------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 4))
multi = compute_multi_timeseries(PROCESSED_DIR, "replicas.csv")
if multi is not None:
    grid, mean, std = multi
    ax.plot(grid, mean, color="#43A047", linewidth=1.5, label="Spec Replicas (mean)")
    ax.fill_between(grid, mean - std, mean + std, color="#43A047", alpha=0.2)
else:
    ax.step(replicas["time_sec"], replicas["spec_replicas"].ffill(),
            where="post", color="#43A047", linewidth=1.5, label="Spec Replicas")
if replicas["hpa_current"].notna().any() and multi is None:
    ax.step(replicas["time_sec"], replicas["hpa_current"].ffill(),
            where="post", color="#FB8C00", linewidth=1.2, linestyle="--", label="HPA Current")
if replicas["hpa_desired"].notna().any() and multi is None:
    ax.step(replicas["time_sec"], replicas["hpa_desired"].ffill(),
            where="post", color="#E53935", linewidth=1.2, linestyle=":", label="HPA Desired")
draw_phases(ax)
ax.set_title("Experiment-D: Replica Count Over Time (HPA + Custom Metric)")
ax.set_xlabel("Time (seconds)")
ax.set_ylabel("Replicas")
ax.set_ylim(bottom=0)
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(os.path.join(PLOTS_DIR, "replicas.png"), dpi=150)
plt.close(fig)
print("  Generated: replicas.png")


# --------------------------------------------------
# Plot 3: Reconnection Rate (if data present)
# --------------------------------------------------
if "reconnection_rate" in connections.columns and connections["reconnection_rate"].sum() > 0:
    fig, ax = plt.subplots(figsize=(10, 4))
    multi = compute_multi_timeseries(PROCESSED_DIR, "connections.csv")
    if multi is not None:
        # reconnection_rate may be in connections.csv; compute mean/std similarly
        # load reconnection_rate specifically across runs
        # fallback to single-run plot if multi-run reconnection not available
        conn_multi = compute_multi_timeseries(PROCESSED_DIR, "connections.csv")
        if conn_multi is not None:
            grid, mean_conn, std_conn = conn_multi
            # Try to extract reconnection_rate by reloading a run file if present
            # Simpler: plot single-run reconnection_rate when multi-run not available
            ax.plot(connections["time_sec"], connections["reconnection_rate"],
                    color="#E53935", linewidth=1.2, label="Reconnection Rate")
    else:
        ax.plot(connections["time_sec"], connections["reconnection_rate"],
                color="#E53935", linewidth=1.2, label="Reconnection Rate")
    draw_phases(ax)
    ax.set_title("Experiment-D: Reconnection Rate Over Time")
    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Rate (connections/s)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS_DIR, "reconnections.png"), dpi=150)
    plt.close(fig)
    print("  Generated: reconnections.png")


# --------------------------------------------------
# Plot 4: Combined overlay
# --------------------------------------------------
fig, ax1 = plt.subplots(figsize=(12, 5))

color_conn = "#1E88E5"
ax1.set_xlabel("Time (seconds)")
ax1.set_ylabel("Active Connections", color=color_conn)
multi_conn = compute_multi_timeseries(PROCESSED_DIR, "connections.csv")
if multi_conn is not None:
    grid, mean, std = multi_conn
    ax1.plot(grid, mean, color=color_conn, linewidth=1.4, label="Active Connections")
    ax1.fill_between(grid, mean - std, mean + std, color=color_conn, alpha=0.15)
else:
    ax1.plot(connections["time_sec"], connections["active_connections"],
             color=color_conn, linewidth=1.4, label="Active Connections")
ax1.tick_params(axis="y", labelcolor=color_conn)

ax2 = ax1.twinx()
color_rep = "#43A047"
ax2.set_ylabel("Replicas", color=color_rep)
multi_rep = compute_multi_timeseries(PROCESSED_DIR, "replicas.csv")
if multi_rep is not None:
    grid, mean, std = multi_rep
    ax2.plot(grid, mean, color=color_rep, linewidth=1.8, alpha=0.9, label="Spec Replicas")
    ax2.fill_between(grid, mean - std, mean + std, color=color_rep, alpha=0.15)
else:
    ax2.step(replicas["time_sec"], replicas["spec_replicas"].ffill(),
             where="post", color=color_rep, linewidth=1.8, alpha=0.9, label="Spec Replicas")
if replicas["hpa_desired"].notna().any() and multi_rep is None:
    ax2.step(replicas["time_sec"], replicas["hpa_desired"].ffill(),
             where="post", color="#E53935", linewidth=1.2, linestyle=":", alpha=0.8, label="HPA Desired")
ax2.tick_params(axis="y", labelcolor=color_rep)
ax2.set_ylim(bottom=0)

draw_phases(ax1)

ax1.set_title("Experiment-D: Connections vs Replicas (HPA + Custom Metric)", fontsize=11)

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)
ax1.grid(True, alpha=0.2)

fig.tight_layout()
fig.savefig(os.path.join(PLOTS_DIR, "combined.png"), dpi=150)
plt.close(fig)
print("  Generated: combined.png")


# --------------------------------------------------
# Summary statistics
# --------------------------------------------------
print("\n  --- Experiment-D Summary ---")
print(f"  Peak connections: {connections['active_connections'].max():.0f}")
print(f"  Peak replicas:    {replicas['spec_replicas'].max():.0f}")
print(f"  Min replicas:     {replicas['spec_replicas'].min():.0f}")

r = replicas.sort_values("time_sec")
scaling_events = (r["spec_replicas"].diff().abs().fillna(0) > 0).sum()
print(f"  Scaling events:   {scaling_events}")
print(f"  Duration:         {connections['time_sec'].max():.0f}s")
print(f"\n  Plots saved to: {PLOTS_DIR}")
