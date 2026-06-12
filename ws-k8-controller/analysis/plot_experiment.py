import os
import pandas as pd
import matplotlib.pyplot as plt

PROCESSED_DIR = os.environ.get("PROCESSED_DIR")
RAW_DIR = os.environ.get("RAW_DIR")

if PROCESSED_DIR is None or RAW_DIR is None:
    raise RuntimeError("RAW_DIR and PROCESSED_DIR must be set as environment variables.")

PLOTS_DIR = os.path.join(PROCESSED_DIR, "plots")
os.makedirs(PLOTS_DIR, exist_ok=True)

# ---------------------------------------------------
# Utility: Normalize timestamps
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
cpu = pd.read_csv(f"{PROCESSED_DIR}/cpu.csv")
replicas = pd.read_csv(f"{PROCESSED_DIR}/replicas.csv")
connections = pd.read_csv(f"{PROCESSED_DIR}/connections.csv")

# Aggregate CPU across pods
cpu = cpu.groupby("timestamp")["cpu_millicores"].sum().reset_index()

cpu = normalize_time(cpu)
replicas = normalize_time(replicas)
connections = normalize_time(connections)

# ---------------------------------------------------
# Load Phase Markers (if exist)
# ---------------------------------------------------
phase_markers = []
phase_file = os.path.join(RAW_DIR, "phase.log")

if os.path.exists(phase_file):
    with open(phase_file) as f:
        for line in f:
            ts, label = line.strip().split(",")
            phase_markers.append(int(ts))

    # Normalize
    if phase_markers:
        start = min(cpu["timestamp"])
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
# Plot Connections
# ---------------------------------------------------
plt.figure()
plt.plot(connections["time_sec"], connections["active_connections"])
for p in phase_markers:
    plt.axvline(x=p, linestyle="--", linewidth=0.8)
plt.title("Active Connections Over Time")
plt.xlabel("Time (seconds)")
plt.ylabel("Connections")
plt.tight_layout()
plt.savefig(f"{PLOTS_DIR}/connections.png")
plt.close()

print("Plots generated successfully.")