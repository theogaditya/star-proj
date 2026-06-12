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
# Parse CPU logs -> cpu.csv
# --------------------------------------------------
def parse_cpu():
    cpu_file = f"{RAW_DIR}/cpu.log"
    if not os.path.exists(cpu_file):
        print("No cpu.log found, skipping.")
        return

    rows = []
    current_ts = None

    with open(cpu_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

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
                except (ValueError, IndexError):
                    continue

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.groupby("timestamp", as_index=False)["cpu_millicores"].sum()
        df.to_csv(f"{PROCESSED_DIR}/cpu.csv", index=False)
        print(f"  Parsed cpu.csv: {len(df)} rows")
    else:
        print("  WARNING: No CPU data parsed.")


# --------------------------------------------------
# Parse HPA logs -> replicas.csv
# --------------------------------------------------
def parse_hpa():
    hpa_file = f"{RAW_DIR}/hpa.log"
    if not os.path.exists(hpa_file):
        print("No hpa.log found, skipping.")
        return

    rows = []
    current_ts = None

    with open(hpa_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            if line.isdigit():
                current_ts = int(line)
                continue

            if line.startswith("NAME") or not line:
                continue

            parts = line.split()
            # kubectl get hpa (autoscaling/v2) columns:
            #   NAME  REFERENCE  [cpu:]  TARGETS  MINPODS  MAXPODS  REPLICAS  AGE
            # REFERENCE contains "/" but is NOT the metric target.
            # Metric target looks like "45%/60%" or "<unknown>/60%".
            # Match only parts that contain "/" AND end with "%" (the x/y% pattern).
            if parts[0] == "websocket-hpa" and current_ts is not None:
                try:
                    replicas = int(parts[-2])

                    # Extract CPU% — only the token that matches "N%/M%" or "<unknown>/M%"
                    cpu_str = "0"
                    for p in parts:
                        if "/" in p and p.endswith("%"):
                            raw = p.split("/")[0].replace("%", "").strip()
                            if raw not in ["<unknown>", "unknown", ""]:
                                cpu_str = raw
                            break

                    try:
                        cpu_val = float(cpu_str)
                    except ValueError:
                        cpu_val = 0.0

                    rows.append({
                        "timestamp": current_ts,
                        "cpu_percent": cpu_val,
                        "replicas": replicas
                    })
                except (ValueError, IndexError):
                    continue


    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(f"{PROCESSED_DIR}/replicas.csv", index=False)
        print(f"  Parsed replicas.csv: {len(df)} rows")
    else:
        print("  WARNING: No HPA data parsed.")


# --------------------------------------------------
# Parse Prometheus CSV dump -> connections.csv
# --------------------------------------------------
def parse_prometheus():
    dump_file = f"{RAW_DIR}/prometheus_dump.csv"
    if not os.path.exists(dump_file):
        print("No prometheus_dump.csv found, skipping.")
        return

    try:
        with open(dump_file) as f:
            first_line = f.readline().strip()

        if first_line and first_line[0].isdigit():
            df = pd.read_csv(dump_file, header=None,
                             names=["timestamp", "active_connections"])
        else:
            df = pd.read_csv(dump_file)

        if df.empty:
            print("  WARNING: Prometheus dump CSV is empty.")
            return

        df["timestamp"] = df["timestamp"].astype(int)
        df["active_connections"] = df["active_connections"].astype(float)

        df.to_csv(f"{PROCESSED_DIR}/connections.csv", index=False)
        print(f"  Parsed connections.csv: {len(df)} rows")

    except Exception as e:
        print(f"  ERROR parsing Prometheus CSV: {e}")


# --------------------------------------------------
# Compute and write summary.csv
# --------------------------------------------------
def compute_summary():
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from metrics_utils import compute_pod_seconds, compute_reaction_time, write_summary

    rep_path = f"{PROCESSED_DIR}/replicas.csv"
    conn_path = f"{PROCESSED_DIR}/connections.csv"

    if not os.path.exists(rep_path) or not os.path.exists(conn_path):
        print("  Skipping summary: missing replicas.csv or connections.csv")
        return

    replicas_df = pd.read_csv(rep_path)
    connections_df = pd.read_csv(conn_path)

    pod_s = compute_pod_seconds(replicas_df, replicas_col="replicas")
    reaction = compute_reaction_time(
        connections_df, replicas_df, replicas_col="replicas"
    )

    peak_conn = float(connections_df["active_connections"].max()) if not connections_df.empty else float("nan")
    peak_rep = float(replicas_df["replicas"].max()) if not replicas_df.empty else float("nan")

    write_summary(
        PROCESSED_DIR,
        pod_seconds=pod_s,
        scale_up_reaction_s=reaction["scale_up_s"],
        scale_down_reaction_s=reaction["scale_down_s"],
        peak_connections=peak_conn,
        peak_replicas=peak_rep,
    )
    print(f"  Summary: pod_seconds={pod_s:.1f}  scale_up={reaction['scale_up_s']:.1f}s  peak_replicas={peak_rep}")


if __name__ == "__main__":
    print("Parsing experiment-b3 logs...")
    parse_cpu()
    parse_hpa()
    parse_prometheus()
    compute_summary()
    print("Experiment-B3 log parsing complete.")
