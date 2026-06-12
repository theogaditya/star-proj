#!/usr/bin/env python3
"""
parse_failure_scenarios.py — Parse raw logs from all 3 failure scenarios
into processed CSVs and write per-scenario summary.csv.

Failure scenarios expected in:
  results/raw/websocket/failure-1-metric-staleness/
  results/raw/websocket/failure-2-instant-spike/
  results/raw/websocket/failure-3-prometheus-outage/

Processed output lands in:
  results/processed/websocket/failure-1-metric-staleness/
  results/processed/websocket/failure-2-instant-spike/
  results/processed/websocket/failure-3-prometheus-outage/

Log formats (same as experiment-c):
  replicas.log        : <unix_ts>,<ready_replicas>
  prometheus_dump.csv : timestamp,active_connections  (or header-less)
  phase.log           : <unix_ts>,<PHASE_TAG>
  cpu.log             : timestamp block / kubectl top output
"""

from __future__ import annotations

import os
import sys
import shutil

import pandas as pd

# Allow importing metrics_utils from parent (analysis/) dir
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))
from metrics_utils import compute_pod_seconds, compute_reaction_time, write_summary

_project_root = os.path.dirname(os.path.dirname(_here))

SCENARIOS = {
    "failure-1-metric-staleness": "Metric Staleness (60s scrape interval)",
    "failure-2-instant-spike":    "Instant Spike (RAMP_UP_DURATION=0)",
    "failure-3-prometheus-outage": "Prometheus Outage (120s kill)",
}

RAW_BASE  = os.path.join(_project_root, "results", "raw",       "websocket")
PROC_BASE = os.path.join(_project_root, "results", "processed", "websocket")


# ---------------------------------------------------------------------------
# Parsers (same logic as experiment-c / experiment-b3)
# ---------------------------------------------------------------------------

def parse_replicas(raw_dir: str, processed_dir: str) -> pd.DataFrame:
    path = os.path.join(raw_dir, "replicas.log")
    if not os.path.exists(path):
        print(f"  WARNING: no replicas.log in {raw_dir}")
        return pd.DataFrame()

    rows = []
    with open(path) as f:
        for line in f:
            parts = line.strip().split(",")
            if len(parts) == 2:
                try:
                    rows.append({"timestamp": int(parts[0]), "replicas": int(parts[1])})
                except ValueError:
                    pass

    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(os.path.join(processed_dir, "replicas.csv"), index=False)
        print(f"  replicas.csv: {len(df)} rows")
    else:
        print("  WARNING: no replica data.")
    return df


def parse_connections(raw_dir: str, processed_dir: str) -> pd.DataFrame:
    path = os.path.join(raw_dir, "prometheus_dump.csv")
    if not os.path.exists(path):
        print(f"  WARNING: no prometheus_dump.csv in {raw_dir}")
        return pd.DataFrame()

    try:
        with open(path) as f:
            first = f.readline().strip()
        if first and first[0].isdigit():
            df = pd.read_csv(path, header=None, names=["timestamp", "active_connections"])
        else:
            df = pd.read_csv(path)

        df["timestamp"] = df["timestamp"].astype(int)
        df["active_connections"] = df["active_connections"].astype(float)
        df.to_csv(os.path.join(processed_dir, "connections.csv"), index=False)
        print(f"  connections.csv: {len(df)} rows")
        return df
    except Exception as e:
        print(f"  ERROR parsing connections: {e}")
        return pd.DataFrame()


def parse_phases(raw_dir: str, processed_dir: str) -> pd.DataFrame:
    path = os.path.join(raw_dir, "phase.log")
    if not os.path.exists(path):
        return pd.DataFrame()

    rows = []
    with open(path) as f:
        for line in f:
            parts = line.strip().split(",", 1)
            if len(parts) == 2:
                try:
                    rows.append({"timestamp": int(parts[0]), "phase": parts[1]})
                except ValueError:
                    pass

    df = pd.DataFrame(rows)
    if not df.empty:
        df.to_csv(os.path.join(processed_dir, "phases.csv"), index=False)
        print(f"  phases.csv: {len(df)} rows")
    return df


def parse_cpu(raw_dir: str, processed_dir: str) -> None:
    path = os.path.join(raw_dir, "cpu.log")
    if not os.path.exists(path):
        return

    rows, current_ts = [], None
    with open(path) as f:
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
                        "cpu_millicores": int(parts[1].replace("m", "")),
                    })
                except (ValueError, IndexError):
                    pass

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.groupby("timestamp", as_index=False)["cpu_millicores"].sum()
        df.to_csv(os.path.join(processed_dir, "cpu.csv"), index=False)
        print(f"  cpu.csv: {len(df)} rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_scenario(scenario_id: str, label: str) -> None:
    print(f"\n--- {scenario_id}: {label} ---")
    raw_dir  = os.path.join(RAW_BASE,  scenario_id)
    proc_dir = os.path.join(PROC_BASE, scenario_id)

    if not os.path.isdir(raw_dir):
        print(f"  SKIP: raw dir not found: {raw_dir}")
        return

    if os.path.exists(proc_dir):
        shutil.rmtree(proc_dir)
    os.makedirs(proc_dir, exist_ok=True)

    replicas_df    = parse_replicas(raw_dir, proc_dir)
    connections_df = parse_connections(raw_dir, proc_dir)
    parse_phases(raw_dir, proc_dir)
    parse_cpu(raw_dir, proc_dir)

    if replicas_df.empty or connections_df.empty:
        print("  Skipping summary (missing data).")
        return

    pod_s    = compute_pod_seconds(replicas_df, replicas_col="replicas")
    reaction = compute_reaction_time(connections_df, replicas_df, replicas_col="replicas")
    peak_conn = float(connections_df["active_connections"].max())
    peak_rep  = float(replicas_df["replicas"].max())

    write_summary(
        proc_dir,
        scenario=scenario_id,
        pod_seconds=pod_s,
        scale_up_reaction_s=reaction["scale_up_s"],
        scale_down_reaction_s=reaction["scale_down_s"],
        peak_connections=peak_conn,
        peak_replicas=peak_rep,
    )
    print(f"  Summary: pod_s={pod_s:.1f}  scale_up={reaction['scale_up_s']:.1f}s  "
          f"peak_rep={peak_rep}")


if __name__ == "__main__":
    print("Parsing failure scenario logs…")
    for sid, slabel in SCENARIOS.items():
        process_scenario(sid, slabel)
    print("\nDone.")
