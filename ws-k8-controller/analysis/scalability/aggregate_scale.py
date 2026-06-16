#!/usr/bin/env python3
"""aggregate_scale.py — Aggregate multi-run Experiment-C scalability results.

Reads the per-run scalability tables (table_scale.csv, produced by
parse_sensitivity.py) from every run_* directory inside --multi-proc-dir
and computes, per client-count bucket (800, 1600, 3200):
    mean ± std, min, max for:
        peak_replicas, expected_replicas, scale_up_s, peak_connections, safe

Writes:
    <multi-proc-dir>/aggregate_stats.csv   — machine-readable aggregate
    <multi-proc-dir>/plots/scalability_aggregate.png — summary figure

Usage:
    python3 analysis/scalability/aggregate_scale.py \
        --multi-proc-dir results/processed/websocket/multi/experiment-c-scale \
        [--out results/processed/websocket/multi/experiment-c-scale/aggregate_stats.csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


# ─────────────────────────── helpers ────────────────────────────

def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_scale_table(path: str) -> list[dict]:
    """Read a table_scale.csv and return list of row dicts."""
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def collect_all_rows(multi_proc_dir: str) -> list[dict]:
    """Walk run_* subdirs, pick up scalability/table_scale.csv from each."""
    all_rows: list[dict] = []
    for entry in sorted(os.listdir(multi_proc_dir)):
        if not entry.startswith("run_"):
            continue
        run_dir = os.path.join(multi_proc_dir, entry)
        # table_scale.csv can be in scalability/ or directly in run dir
        candidates = [
            os.path.join(run_dir, "scalability", "table_scale.csv"),
            os.path.join(run_dir, "sensitivity", "table_scale.csv"),
            os.path.join(run_dir, "table_scale.csv"),
        ]
        found = next((p for p in candidates if os.path.exists(p)), None)
        if found is None:
            print(f"  WARNING: no table_scale.csv found in {run_dir} – skipping.", file=sys.stderr)
            continue
        rows = load_scale_table(found)
        for r in rows:
            r["_run"] = entry
        all_rows.extend(rows)
        print(f"  loaded {len(rows)} rows from {found}")
    return all_rows


# ─────────────────────── aggregate per client count ─────────────

METRICS = ["peak_replicas", "expected_replicas", "scale_up_s", "peak_connections", "safe"]


def aggregate(rows: list[dict]) -> list[dict]:
    """Group by clients, compute mean/std/min/max/n for each metric."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        c = num(r.get("clients"))
        if c is None:
            continue
        buckets[int(c)].append(r)

    agg_rows = []
    for clients in sorted(buckets):
        bucket = buckets[clients]
        n_runs = len({r["_run"] for r in bucket})
        agg: dict = {"clients": clients, "n_runs": n_runs}
        for m in METRICS:
            vals = [num(r.get(m)) for r in bucket]
            vals = [v for v in vals if v is not None]
            if vals:
                agg[f"{m}_mean"]  = float(np.mean(vals))
                agg[f"{m}_std"]   = float(np.std(vals, ddof=1) if len(vals) > 1 else 0.0)
                agg[f"{m}_min"]   = float(np.min(vals))
                agg[f"{m}_max"]   = float(np.max(vals))
                agg[f"{m}_n"]     = len(vals)
            else:
                for sfx in ("mean", "std", "min", "max", "n"):
                    agg[f"{m}_{sfx}"] = ""
        agg_rows.append(agg)
    return agg_rows


# ───────────────────────────── CSV ──────────────────────────────

def write_csv(agg_rows: list[dict], path: str) -> None:
    if not agg_rows:
        print("WARNING: no aggregate rows to write.", file=sys.stderr)
        return
    fieldnames = list(agg_rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(agg_rows)
    print(f"[ok] Aggregate stats → {path}")


# ─────────────────────────── table print ────────────────────────

def print_table(agg_rows: list[dict]) -> None:
    print()
    print("=== Experiment-C Scalability Aggregate ===")
    header = f"{'Clients':>8}  {'Runs':>4}  {'PeakReplicas':>14}  {'ExpectedR':>10}  "
    header += f"{'ScaleUp(s)':>12}  {'PeakConns':>11}  {'Safe(%)':>8}"
    print(header)
    print("-" * len(header))
    for r in agg_rows:
        def fmt(m):
            mn = r.get(f"{m}_mean")
            sd = r.get(f"{m}_std")
            if mn == "" or mn is None:
                return "     n/a"
            return f"{mn:7.1f}±{sd:.1f}"

        safe_pct = ""
        smn = r.get("safe_mean")
        if smn != "" and smn is not None:
            safe_pct = f"{float(smn)*100:6.0f}%"

        print(
            f"{r['clients']:>8}  {r['n_runs']:>4}  {fmt('peak_replicas'):>14}  "
            f"{fmt('expected_replicas'):>10}  {fmt('scale_up_s'):>12}  "
            f"{fmt('peak_connections'):>11}  {safe_pct:>8}"
        )
    print()


# ─────────────────────────── plotting ───────────────────────────

def plot(agg_rows: list[dict], out_dir: str) -> None:
    if not HAS_MPL:
        print("WARNING: matplotlib not available – skipping plots.", file=sys.stderr)
        return

    os.makedirs(out_dir, exist_ok=True)
    clients = [r["clients"] for r in agg_rows]

    def vals(m):
        return [r.get(f"{m}_mean") or 0 for r in agg_rows]

    def errs(m):
        return [r.get(f"{m}_std") or 0 for r in agg_rows]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle("Experiment-C Scalability – Multi-Run Aggregate", fontsize=13, fontweight="bold")

    # ── Panel 1: Replica targeting ──
    ax = axes[0]
    ax.errorbar(clients, vals("expected_replicas"), fmt="k--o", label="expected ceil(N/T)", capsize=4)
    ax.errorbar(clients, vals("peak_replicas"), yerr=errs("peak_replicas"),
                fmt="o-", color="tab:blue", label="achieved peak (mean±std)", capsize=4)
    ax.set_title("Replica Targeting")
    ax.set_xlabel("Attempted clients")
    ax.set_ylabel("Replicas")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # ── Panel 2: Scale-up reaction time ──
    ax = axes[1]
    ax.errorbar(clients, vals("scale_up_s"), yerr=errs("scale_up_s"),
                fmt="o-", color="tab:green", capsize=4)
    ax.set_title("Scale-Up Reaction Time")
    ax.set_xlabel("Attempted clients")
    ax.set_ylabel("Seconds")
    ax.grid(alpha=0.3)

    # ── Panel 3: OS TCP ceiling ──
    ax = axes[2]
    ax.plot(clients, clients, "k--", label="attempted")
    ax.errorbar(clients, vals("peak_connections"), yerr=errs("peak_connections"),
                fmt="o-", color="tab:red", label="achieved peak (mean±std)", capsize=4)
    ax.set_title("OS TCP Ceiling\n(attempted vs achieved)")
    ax.set_xlabel("Attempted clients")
    ax.set_ylabel("Connections")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    png = os.path.join(out_dir, "scalability_aggregate.png")
    fig.savefig(png, dpi=150)
    print(f"[ok] Plot → {png}")


# ─────────────────────────── main ───────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate multi-run Experiment-C scalability results")
    p.add_argument(
        "--multi-proc-dir",
        required=True,
        help="Path to results/processed/websocket/multi/experiment-c-scale/",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output CSV (default: <multi-proc-dir>/aggregate_stats.csv)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    mdir = args.multi_proc_dir

    if not os.path.isdir(mdir):
        sys.exit(f"ERROR: --multi-proc-dir '{mdir}' does not exist.")

    print(f"Collecting scale tables from {mdir} …")
    rows = collect_all_rows(mdir)
    if not rows:
        sys.exit("ERROR: no data rows found. Did the runs complete successfully?")

    print(f"Found {len(rows)} total rows across all runs.")
    agg_rows = aggregate(rows)

    out_csv = args.out or os.path.join(mdir, "aggregate_stats.csv")
    write_csv(agg_rows, out_csv)
    print_table(agg_rows)

    plots_dir = os.path.join(mdir, "plots")
    plot(agg_rows, plots_dir)


if __name__ == "__main__":
    main()
