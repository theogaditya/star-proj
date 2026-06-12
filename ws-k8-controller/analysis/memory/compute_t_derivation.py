#!/usr/bin/env python3
"""Derive the recommended targetConnectionsPerPod (T) from measured
per-connection memory (Paper 2 revision: T-parameter derivation).

    T = floor( safety_factor * memory_limit_mib / memory_per_connection_mib )

Input CSV (from scripts/measure-connection-memory.sh):
    connections,pods,total_memory_mib
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "results", "processed", "websocket", "memory")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=os.path.join(ROOT, "results", "raw", "websocket",
                                                  "memory", "memory_per_connection.csv"))
    ap.add_argument("--limit-mib", type=float, default=512.0,
                    help="Pod memory limit in MiB (default: 512)")
    ap.add_argument("--safety", type=float, default=0.7,
                    help="Safety factor alpha (default: 0.7)")
    args = ap.parse_args()

    conns, mem = [], []
    with open(args.csv) as f:
        for row in csv.DictReader(f):
            pods = max(1, int(float(row["pods"])))
            conns.append(float(row["connections"]) / pods)
            mem.append(float(row["total_memory_mib"]) / pods)
    if len(conns) < 3:
        sys.exit("Need at least 3 measurement levels.")

    slope_mib, intercept_mib = np.polyfit(conns, mem, 1)
    if slope_mib <= 0:
        sys.exit("Non-positive slope: measurement noise too high, re-run with more levels.")

    per_conn_kib = slope_mib * 1024.0
    t_rec = int((args.safety * args.limit_mib) // slope_mib)

    result = {
        "baseline_memory_mib": round(float(intercept_mib), 2),
        "memory_per_connection_mib": round(float(slope_mib), 4),
        "memory_per_connection_kib": round(float(per_conn_kib), 1),
        "memory_limit_mib": args.limit_mib,
        "safety_factor": args.safety,
        "recommended_T": t_rec,
        "formula": "T = floor(safety * limit_mib / per_conn_mib)",
    }
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "t_derivation.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(json.dumps(result, indent=2))
    print(f"[ok] {out_path}")
    print(f"\n  Per-connection memory : {per_conn_kib:.1f} KiB")
    print(f"  Recommended T         : {t_rec}  "
          f"(= floor({args.safety} * {args.limit_mib} MiB / {slope_mib:.4f} MiB))")


if __name__ == "__main__":
    main()
