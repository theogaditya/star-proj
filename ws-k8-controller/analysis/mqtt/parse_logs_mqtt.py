"""
parse_logs_mqtt.py

Parses the raw logs produced by the MQTT experiment run scripts and
outputs a single CSV per experiment suitable for plotting.

Usage:
  python parse_logs_mqtt.py <result_dir> <output_csv>

Example:
  python parse_logs_mqtt.py results/raw/mqtt/experiment-a-hpa out.csv
"""
import sys
import csv
import os
import json


def parse_timestamped_log(path, value_fn):
    """
    Parse logs where lines alternate:  <unix_timestamp>  then <value_line(s)>
    value_fn(lines_after_ts) → float or None
    """
    records = []
    if not os.path.exists(path):
        return records
    with open(path) as f:
        lines = [l.rstrip() for l in f]

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.isdigit():
            ts = int(line)
            vals = []
            i += 1
            while i < len(lines) and not lines[i].isdigit():
                vals.append(lines[i])
                i += 1
            v = value_fn(vals)
            if v is not None:
                records.append((ts, v))
        else:
            i += 1
    return records


def parse_replicas(result_dir):
    """Extract (timestamp, replica_count) from pods.log.
    Counts Running pods; returns 0 if none are Running (not None).
    """
    def count_running(lines):
        running = sum(1 for l in lines if "Running" in l)
        return running  # return 0 when none Running — don't drop the row
    return parse_timestamped_log(os.path.join(result_dir, "pods.log"), count_running)


def parse_connections(result_dir):
    """Extract (timestamp, active_connections) from active_connections.log."""
    records = []
    path = os.path.join(result_dir, "active_connections.log")
    if not os.path.exists(path):
        return records
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip()]
    i = 0
    while i < len(lines) - 1:
        if lines[i].isdigit():
            try:
                records.append((int(lines[i]), float(lines[i + 1])))
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    return records


def parse_cpu(result_dir):
    """Extract (timestamp, total_cpu_millicores) from cpu.log.
    Each timestamp block may have multiple pod lines like:
        mqtt-broker-xxx   12m   64Mi
    Sum the CPU column (strip trailing 'm').
    """
    def sum_cpu(lines):
        total = 0
        for l in lines:
            parts = l.split()
            if len(parts) >= 2:
                cpu_str = parts[1].rstrip("m")
                try:
                    total += int(cpu_str)
                except ValueError:
                    pass
        return total
    return parse_timestamped_log(os.path.join(result_dir, "cpu.log"), sum_cpu)


def parse_memory(result_dir):
    """Extract (timestamp, total_memory_mi) from memory.log.
    Format per block: <timestamp>\\n<total_mi_value>

    Falls back to parsing from cpu.log if memory.log doesn't exist.
    """
    mem_path = os.path.join(result_dir, "memory.log")
    if os.path.exists(mem_path):
        records = []
        with open(mem_path) as f:
            lines = [l.strip() for l in f if l.strip()]
        i = 0
        while i < len(lines) - 1:
            if lines[i].isdigit():
                try:
                    records.append((int(lines[i]), float(lines[i + 1])))
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        return records

    # Fallback: extract from cpu.log (format: pod-name  2m  30Mi)
    def sum_memory(lines):
        total = 0
        for l in lines:
            parts = l.split()
            if len(parts) >= 3:
                mem_str = parts[2].rstrip("Mi").rstrip("Gi")
                try:
                    val = float(mem_str)
                    if "Gi" in parts[2]:
                        val *= 1024
                    total += val
                except ValueError:
                    pass
        return total
    return parse_timestamped_log(os.path.join(result_dir, "cpu.log"), sum_memory)


def parse_perpod_connections(result_dir):
    """Extract per-pod connection data from perpod_connections.log.
    Format per block: <timestamp>\\n<json_dict>

    Returns:
        (list of (ts, dict), set of all pod names seen)
    """
    path = os.path.join(result_dir, "perpod_connections.log")
    if not os.path.exists(path):
        return [], set()

    records = []
    all_pods = set()
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip()]

    i = 0
    while i < len(lines) - 1:
        if lines[i].isdigit():
            ts = int(lines[i])
            try:
                pod_dict = json.loads(lines[i + 1])
                records.append((ts, pod_dict))
                all_pods.update(pod_dict.keys())
            except (json.JSONDecodeError, ValueError):
                pass
            i += 2
        else:
            i += 1
    return records, all_pods


def parse_phases(result_dir):
    """Parse phases.log if it exists.
    Returns dict like: {"PHASE1_START": 1234567890, "PHASE1_END": ...}
    Also parses REBALANCE_DRAIN_START entries.
    """
    path = os.path.join(result_dir, "phases.log")
    phases = {}
    if not os.path.exists(path):
        return phases
    with open(path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                phases[parts[0]] = int(parts[1])
    return phases


def parse_drain_events(result_dir):
    """Extract drain event timestamps from phases.log and controller.log.
    Returns list of (start_ts, end_ts_or_None) tuples.
    """
    events = []

    # From phases.log: REBALANCE_DRAIN_START entries
    phases = parse_phases(result_dir)
    for key, ts in phases.items():
        if "DRAIN" in key:
            events.append(ts)

    # From controller.log: look for "Starting drain" and "Drain complete" lines
    ctrl_path = os.path.join(result_dir, "controller.log")
    if os.path.exists(ctrl_path):
        with open(ctrl_path) as f:
            for line in f:
                if "Starting drain" in line or "Drain complete" in line:
                    # Try to extract timestamp — controller logs use ISO format
                    # Just record the line for the drain_events output
                    pass

    return events


def merge_timeseries(*series_list):
    """Merge multiple (timestamp, value) lists by union of all timestamps.
    Forward-fills missing values (carries last known value forward).
    Returns list of (timestamp, val1, val2, ...) tuples sorted by time.
    """
    # Collect all timestamps
    all_ts = set()
    dicts = []
    for series in series_list:
        d = dict(series)
        dicts.append(d)
        all_ts.update(d.keys())

    if not all_ts:
        return []

    sorted_ts = sorted(all_ts)

    # Forward-fill
    result = []
    last_vals = [0.0] * len(dicts)
    for ts in sorted_ts:
        row = [ts]
        for idx, d in enumerate(dicts):
            if ts in d:
                last_vals[idx] = d[ts]
            row.append(last_vals[idx])
        result.append(tuple(row))

    return result


def main(result_dir, output_csv):
    replicas = parse_replicas(result_dir)
    connections = parse_connections(result_dir)
    cpu = parse_cpu(result_dir)
    memory = parse_memory(result_dir)

    merged = merge_timeseries(replicas, connections, cpu, memory)

    if not merged:
        print(f"[!] No data found in {result_dir}")
        sys.exit(1)

    t0 = next((row[0] for row in merged if row[0] > 1_000_000_000), merged[0][0])

    # Parse drain events for marking drain windows
    drain_timestamps = set(parse_drain_events(result_dir))

    # Write main CSV (with drain_active column)
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "replicas", "active_connections", "cpu_millicores", "memory_mi", "drain_active"])
        for row in merged:
            abs_ts = row[0]
            rel_ts = abs_ts - t0
            # Mark drain_active=1 if any drain event is within a reasonable window
            drain_flag = 0
            for dt in drain_timestamps:
                # Mark as draining for 60s after drain event start
                if 0 <= (abs_ts - dt) <= 60:
                    drain_flag = 1
                    break
            writer.writerow([rel_ts, row[1], row[2], row[3], row[4], drain_flag])

    print(f"[✓] Wrote {len(merged)} rows to {output_csv}")

    # Write per-pod connections CSV if available
    perpod_data, all_pods = parse_perpod_connections(result_dir)
    if perpod_data and all_pods:
        sorted_pods = sorted(all_pods)
        perpod_csv = output_csv.replace(".csv", "_perpod.csv")
        with open(perpod_csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s"] + [f"conn_{p}" for p in sorted_pods])
            for ts, pod_dict in perpod_data:
                row = [ts - t0] + [pod_dict.get(p, 0) for p in sorted_pods]
                writer.writerow(row)
        print(f"[✓] Wrote {len(perpod_data)} rows to {perpod_csv}")

    # Write phases as JSON if available
    phases = parse_phases(result_dir)
    if phases:
        phases_out = output_csv.replace(".csv", "_phases.json")
        # Convert to relative time
        phases_rel = {k: v - (merged[0][0] if merged else 0) for k, v in phases.items()}
        with open(phases_out, "w") as f:
            json.dump(phases_rel, f, indent=2)
        print(f"[✓] Wrote phases to {phases_out}")

    # Write drain events summary if any exist
    if drain_timestamps:
        drain_out = output_csv.replace(".csv", "_drain_events.json")
        drain_rel = sorted([dt - t0 for dt in drain_timestamps])
        with open(drain_out, "w") as f:
            json.dump({"drain_event_times_s": drain_rel}, f, indent=2)
        print(f"[✓] Wrote drain events to {drain_out}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python parse_logs_mqtt.py <result_dir> <output_csv>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
