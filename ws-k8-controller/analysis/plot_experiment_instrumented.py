import os
import pandas as pd
import matplotlib.pyplot as plt

PROCESSED_DIR = os.environ.get("PROCESSED_DIR")
RAW_DIR = os.environ.get("RAW_DIR")

if PROCESSED_DIR is None or RAW_DIR is None:
    raise RuntimeError("RAW_DIR and PROCESSED_DIR must be set.")

PLOTS_DIR = os.path.join(PROCESSED_DIR, "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)


# ---------------------------------------------------
# Normalize timestamps
# ---------------------------------------------------
def normalize_time(df):
    if df.empty:
        return df
    start = df["timestamp"].min()
    df["time_sec"] = df["timestamp"] - start
    return df


# ---------------------------------------------------
# Load CSVs
# ---------------------------------------------------
cpu_path = f"{PROCESSED_DIR}/cpu.csv"
replicas_path = f"{PROCESSED_DIR}/replicas.csv"
connections_path = f"{PROCESSED_DIR}/connections.csv"

if not os.path.exists(cpu_path):
    raise RuntimeError("cpu.csv missing")

if not os.path.exists(replicas_path):
    raise RuntimeError("replicas.csv missing")

if not os.path.exists(connections_path):
    raise RuntimeError("connections.csv missing — Prometheus parsing failed")

cpu = pd.read_csv(cpu_path)
replicas = pd.read_csv(replicas_path)
connections = pd.read_csv(connections_path)

cpu = normalize_time(cpu)
replicas = normalize_time(replicas)
connections = normalize_time(connections)


# ---------------------------------------------------
# Load Phase Markers
# ---------------------------------------------------
phase_markers = []
phase_file = os.path.join(RAW_DIR, "phase.log")

if os.path.exists(phase_file):
    with open(phase_file) as f:
        for line in f:
            ts, label = line.strip().split(",")
            phase_markers.append(int(ts))

    if phase_markers:
        start = cpu["timestamp"].min()
        phase_markers = [p - start for p in phase_markers]


# ---------------------------------------------------
# Plot CPU
# ---------------------------------------------------
plt.figure()
plt.plot(cpu["time_sec"], cpu["cpu_millicores"])
for p in phase_markers:
    plt.axvline(x=p, linestyle="--", linewidth=0.8)
plt.title("Total CPU Usage (millicores)")
plt.xlabel("Time (seconds)")
plt.ylabel("CPU (m)")
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/cpu.png")
plt.close()


# ---------------------------------------------------
# Plot Replicas
# ---------------------------------------------------
plt.figure()
plt.plot(replicas["time_sec"], replicas["replicas"])
for p in phase_markers:
    plt.axvline(x=p, linestyle="--", linewidth=0.8)
plt.title("Replica Count Over Time")
plt.xlabel("Time (seconds)")
plt.ylabel("Replicas")
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/replicas.png")
plt.close()


# ---------------------------------------------------
# Plot Active Connections
# ---------------------------------------------------
plt.figure()
plt.plot(connections["time_sec"], connections["active_connections"])
for p in phase_markers:
    plt.axvline(x=p, linestyle="--", linewidth=0.8)
plt.title("Cluster-wide Active Connections")
plt.xlabel("Time (seconds)")
plt.ylabel("Connections")
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/connections.png")
plt.close()


# ---------------------------------------------------
# Plot Reconnection Rate
# ---------------------------------------------------
plt.figure()
plt.plot(connections["time_sec"], connections["reconnect_rate"])
for p in phase_markers:
    plt.axvline(x=p, linestyle="--", linewidth=0.8)
plt.title("Cluster-wide Reconnection Rate (connections/sec)")
plt.xlabel("Time (seconds)")
plt.ylabel("Reconnect Rate")
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/reconnections.png")
plt.close()


# ---------------------------------------------------
# Compute Key Instability Metrics
# ---------------------------------------------------

replicas_sorted = replicas.sort_values("time_sec")
replicas_sorted["delta"] = replicas_sorted["replicas"].diff().abs()

scaling_events = replicas_sorted["delta"].fillna(0).gt(0).sum()
replica_churn = replicas_sorted["delta"].fillna(0).sum()

oscillation_amplitude = (
    replicas["replicas"].max() - replicas["replicas"].min()
)


# ---------------------------------------------------
# Plot Scaling Activity
# ---------------------------------------------------
plt.figure()
plt.bar(["Scaling Events", "Replica Churn"],
        [scaling_events, replica_churn])
plt.title("Replica Scaling Activity")
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/scaling_activity.png")
plt.close()

print("Cleaned and focused plots generated successfully.")