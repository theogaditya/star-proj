import os
import pandas as pd
import shutil

RAW_DIR = os.environ.get("RAW_DIR")
PROCESSED_DIR = os.environ.get("PROCESSED_DIR")

if RAW_DIR is None or PROCESSED_DIR is None:
    raise RuntimeError("RAW_DIR and PROCESSED_DIR must be set.")

if os.path.exists(PROCESSED_DIR):
    if os.environ.get("MULTI_RUN", "0") in ("1", "true", "True"):
        print("  MULTI_RUN detected: preserving existing PROCESSED_DIR")
    else:
        shutil.rmtree(PROCESSED_DIR)

os.makedirs(PROCESSED_DIR, exist_ok=True)


# --------------------------------------------------
# Parse CPU (unchanged)
# --------------------------------------------------
def parse_cpu():
    rows = []
    current_ts = None

    with open(f"{RAW_DIR}/cpu.log") as f:
        for line in f:
            line = line.strip()

            if line.isdigit():
                current_ts = int(line)
                continue

            parts = line.split()
            if len(parts) >= 3 and current_ts is not None:
                try:
                    rows.append({
                        "timestamp": current_ts,
                        "pod": parts[0],
                        "cpu_millicores": int(parts[1].replace("m", ""))
                    })
                except:
                    continue

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.groupby("timestamp", as_index=False)["cpu_millicores"].sum()
        df.to_csv(f"{PROCESSED_DIR}/cpu.csv", index=False)


# --------------------------------------------------
# Parse HPA (unchanged)
# --------------------------------------------------
def parse_hpa():
    rows = []
    current_ts = None

    with open(f"{RAW_DIR}/hpa.log") as f:
        for line in f:
            line = line.strip()

            if line.isdigit():
                current_ts = int(line)
                continue

            if line.startswith("NAME") or not line:
                continue

            parts = line.split()

            if parts[0] == "websocket-hpa" and current_ts is not None:
                try:
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


# --------------------------------------------------
# Parse Prometheus CSV Dump (NEW ROBUST VERSION)
# --------------------------------------------------
def parse_prometheus():
    dump_file = f"{RAW_DIR}/prometheus_dump.csv"

    if not os.path.exists(dump_file):
        print("No prometheus_dump.csv found.")
        return

    try:
        # Detect if file has a header by checking if first line starts with a digit (timestamp)
        with open(dump_file) as f:
            first_line = f.readline().strip()

        if first_line and first_line[0].isdigit():
            # No header — provide column names
            df = pd.read_csv(dump_file, header=None, names=["timestamp", "active_connections", "reconnect_rate"])
        else:
            df = pd.read_csv(dump_file)

        if df.empty:
            print("Prometheus dump CSV empty.")
            return

        df["timestamp"] = df["timestamp"].astype(int)
        df["active_connections"] = df["active_connections"].astype(float)
        df["reconnect_rate"] = df["reconnect_rate"].astype(float)

        df.to_csv(f"{PROCESSED_DIR}/connections.csv", index=False)

    except Exception as e:
        print(f"Failed parsing Prometheus CSV: {e}")


if __name__ == "__main__":
    parse_cpu()
    parse_hpa()
    parse_prometheus()
    print("Prometheus instrumented parsing complete.")