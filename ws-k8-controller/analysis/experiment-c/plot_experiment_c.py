import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROCESSED_DIR = os.environ.get("PROCESSED_DIR")
RAW_DIR = os.environ.get("RAW_DIR")

if PROCESSED_DIR is None or RAW_DIR is None:
    raise RuntimeError("RAW_DIR and PROCESSED_DIR must be set.")

PLOTS_DIR = os.path.join(PROCESSED_DIR, "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)


# ---------------------------------------------------
# Normalize timestamps to start at t=0
# ---------------------------------------------------
def normalize_time(df, global_start):
    if df.empty:
        return df
    df["time_sec"] = df["timestamp"] - global_start
    return df


# ---------------------------------------------------
# Multi-run helper
# ---------------------------------------------------
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


# ---------------------------------------------------
# Load CSVs
# ---------------------------------------------------
cpu_path = f"{PROCESSED_DIR}/cpu.csv"
replicas_path = f"{PROCESSED_DIR}/replicas.csv"
connections_path = f"{PROCESSED_DIR}/connections.csv"

for path, name in [(cpu_path, "cpu.csv"),
                    (replicas_path, "replicas.csv"),
                    (connections_path, "connections.csv")]:
    if not os.path.exists(path):
        raise RuntimeError(f"{name} missing in {PROCESSED_DIR}")

cpu = pd.read_csv(cpu_path)
replicas = pd.read_csv(replicas_path)
connections = pd.read_csv(connections_path)

# Global start time for consistent normalization
global_start = min(cpu["timestamp"].min(),
                   replicas["timestamp"].min(),
                   connections["timestamp"].min())

cpu = normalize_time(cpu, global_start)
replicas = normalize_time(replicas, global_start)
connections = normalize_time(connections, global_start)


# ---------------------------------------------------
# Load Phase Markers
# ---------------------------------------------------
phase_markers = []
phase_labels = []
phase_file = os.path.join(RAW_DIR, "phase.log")

if os.path.exists(phase_file):
    with open(phase_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) == 2:
                phase_markers.append(int(parts[0]) - global_start)
                phase_labels.append(parts[1])


# ---------------------------------------------------
# Helper: draw phase markers
# ---------------------------------------------------
def draw_phases(ax):
    colors = {
        "CYCLE_1": "#2196F3",
        "DROP_1": "#FF9800",
        "CYCLE_2": "#2196F3",
        "FINAL_DROP": "#FF9800"
    }
    for ts, label in zip(phase_markers, phase_labels):
        color = colors.get(label, "#999999")
        ax.axvline(x=ts, linestyle="--", linewidth=0.9, color=color, alpha=0.7)


# ---------------------------------------------------
# Plot 1: CPU Usage
# ---------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(cpu["time_sec"], cpu["cpu_millicores"], color="#E53935", linewidth=1.2)
draw_phases(ax)
ax.set_title("Total CPU Usage (millicores)")
ax.set_xlabel("Time (seconds)")
ax.set_ylabel("CPU (m)")
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(f"{PLOTS_DIR}/cpu.png", dpi=150)
plt.close(fig)
print("  Generated: cpu.png")


# ---------------------------------------------------
# Plot 2: Active Connections
# ---------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 4))
multi = compute_multi_timeseries(PROCESSED_DIR, "connections.csv")
if multi is not None:
    grid, mean, std = multi
    ax.plot(grid, mean, color="#1E88E5", linewidth=1.2)
    ax.fill_between(grid, mean - std, mean + std, color="#1E88E5", alpha=0.2)
else:
    ax.plot(connections["time_sec"], connections["active_connections"],
            color="#1E88E5", linewidth=1.2)
draw_phases(ax)
ax.set_title("Cluster-Wide Active Connections")
ax.set_xlabel("Time (seconds)")
ax.set_ylabel("Connections")
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(f"{PLOTS_DIR}/connections.png", dpi=150)
plt.close(fig)
print("  Generated: connections.png")


# ---------------------------------------------------
# Plot 3: Replica Count
# ---------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 4))
multi = compute_multi_timeseries(PROCESSED_DIR, "replicas.csv")
if multi is not None:
    grid, mean, std = multi
    ax.plot(grid, mean, color="#43A047", linewidth=1.5)
    ax.fill_between(grid, mean - std, mean + std, color="#43A047", alpha=0.2)
else:
    ax.step(replicas["time_sec"], replicas["replicas"], where="post",
            color="#43A047", linewidth=1.5)
draw_phases(ax)
ax.set_title("Replica Count Over Time")
ax.set_xlabel("Time (seconds)")
ax.set_ylabel("Replicas")
ax.set_ylim(bottom=0)
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig(f"{PLOTS_DIR}/replicas.png", dpi=150)
plt.close(fig)
print("  Generated: replicas.png")


# ---------------------------------------------------
# Plot 4: Combined Overlay (CPU + Connections + Replicas)
# ---------------------------------------------------
fig, ax1 = plt.subplots(figsize=(12, 5))

# Connections on left axis
color_conn = "#1E88E5"
ax1.set_xlabel("Time (seconds)")
ax1.set_ylabel("Active Connections", color=color_conn)
multi_conn = compute_multi_timeseries(PROCESSED_DIR, "connections.csv")
if multi_conn is not None:
    grid, mean, std = multi_conn
    ax1.plot(grid, mean, color=color_conn, linewidth=1.2, label="Active Connections")
    ax1.fill_between(grid, mean - std, mean + std, color=color_conn, alpha=0.15)
else:
    ax1.plot(connections["time_sec"], connections["active_connections"],
             color=color_conn, linewidth=1.2, label="Active Connections")
ax1.tick_params(axis="y", labelcolor=color_conn)

# CPU on right axis
ax2 = ax1.twinx()
color_cpu = "#E53935"
ax2.set_ylabel("CPU (millicores)", color=color_cpu)
ax2.plot(cpu["time_sec"], cpu["cpu_millicores"],
         color=color_cpu, linewidth=1.0, alpha=0.7, label="CPU")
ax2.tick_params(axis="y", labelcolor=color_cpu)

# Replicas as bar overlay
ax3 = ax1.twinx()
ax3.spines["right"].set_position(("outward", 60))
color_rep = "#43A047"
ax3.set_ylabel("Replicas", color=color_rep)
multi_rep = compute_multi_timeseries(PROCESSED_DIR, "replicas.csv")
if multi_rep is not None:
    grid, mean, std = multi_rep
    ax3.plot(grid, mean, color=color_rep, linewidth=1.5, alpha=0.9, label="Replicas")
    ax3.fill_between(grid, mean - std, mean + std, color=color_rep, alpha=0.15)
else:
    ax3.step(replicas["time_sec"], replicas["replicas"], where="post",
             color=color_rep, linewidth=1.5, alpha=0.8, label="Replicas")
ax3.tick_params(axis="y", labelcolor=color_rep)
ax3.set_ylim(bottom=0)

draw_phases(ax1)

ax1.set_title("Experiment-C: Connections vs CPU vs Replicas (Custom Controller)")

# Combined legend
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
lines3, labels3 = ax3.get_legend_handles_labels()
ax1.legend(lines1 + lines2 + lines3, labels1 + labels2 + labels3,
           loc="upper left", fontsize=8)

ax1.grid(True, alpha=0.2)
fig.tight_layout()
fig.savefig(f"{PLOTS_DIR}/combined.png", dpi=150)
plt.close(fig)
print("  Generated: combined.png")


# ---------------------------------------------------
# Summary Statistics
# ---------------------------------------------------
print("\n  --- Summary ---")
print(f"  Peak connections: {connections['active_connections'].max():.0f}")
print(f"  Peak CPU:         {cpu['cpu_millicores'].max():.0f}m")
print(f"  Peak replicas:    {replicas['replicas'].max()}")
print(f"  Min replicas:     {replicas['replicas'].min()}")

replicas_sorted = replicas.sort_values("time_sec")
replicas_sorted["delta"] = replicas_sorted["replicas"].diff().abs().fillna(0)
scaling_events = (replicas_sorted["delta"] > 0).sum()
print(f"  Scaling events:   {scaling_events}")

print("\nExperiment-C plots generated successfully.")
