import os
import re
import pandas as pd
from datetime import datetime
import shutil

RAW_DIR = os.environ.get("RAW_DIR")
PROCESSED_DIR = os.environ.get("PROCESSED_DIR")

if RAW_DIR is None or PROCESSED_DIR is None:
    raise RuntimeError("RAW_DIR and PROCESSED_DIR must be set as environment variables.")

if os.path.exists(PROCESSED_DIR):
    if os.environ.get("MULTI_RUN", "0") in ("1", "true", "True"):
        print("  MULTI_RUN detected: preserving existing PROCESSED_DIR")
    else:
        shutil.rmtree(PROCESSED_DIR)

os.makedirs(PROCESSED_DIR, exist_ok=True)

# ----------------------------
# Parse CPU log
# ----------------------------
def parse_cpu():
    rows = []
    current_ts = None

    with open(f"{RAW_DIR}/cpu.log") as f:
        for line in f:
            line = line.strip()

            # If line is timestamp
            if line.isdigit():
                current_ts = int(line)
                continue

            parts = line.split()
            if len(parts) >= 3 and current_ts is not None:
                pod = parts[0]
                cpu = parts[1].replace("m", "")

                try:
                    rows.append({
                        "timestamp": current_ts,
                        "pod": pod,
                        "cpu_millicores": int(cpu)
                    })
                except:
                    continue

    df = pd.DataFrame(rows)

    if not df.empty:
        df.to_csv(f"{PROCESSED_DIR}/cpu.csv", index=False)

# ----------------------------
# Parse HPA log
# ----------------------------
def parse_hpa():
    rows = []
    current_ts = None

    with open(f"{RAW_DIR}/hpa.log") as f:
        for line in f:
            line = line.strip()

            # Timestamp line
            if line.isdigit():
                current_ts = int(line)
                continue

            # Skip header lines
            if line.startswith("NAME") or not line:
                continue

            parts = line.split()

            if parts[0] == "websocket-hpa" and current_ts is not None:
                try:
                    # TARGETS column is split: cpu: 52%/60%
                    cpu_percent = parts[3].split("/")[0].replace("%", "")
                    replicas = int(parts[6])

                    rows.append({
                        "timestamp": current_ts,
                        "cpu_percent": float(cpu_percent),
                        "replicas": replicas
                    })
                except:
                    continue

    df = pd.DataFrame(rows)

    if not df.empty:
        df.to_csv(f"{PROCESSED_DIR}/replicas.csv", index=False)

# ----------------------------
# Parse active connections
# ----------------------------
def parse_connections():
    rows = []
    with open(f"{RAW_DIR}/active_connections.log") as f:
        lines = f.readlines()
        for i in range(0, len(lines), 2):
            try:
                ts = int(lines[i].strip())
                conn = int(lines[i+1].split()[-1])
                rows.append({
                    "timestamp": ts,
                    "active_connections": conn
                })
            except:
                continue
    df = pd.DataFrame(rows)
    df.to_csv(f"{PROCESSED_DIR}/connections.csv", index=False)


if __name__ == "__main__":
    parse_cpu()
    parse_hpa()
    parse_connections()
    print("Parsing complete.")