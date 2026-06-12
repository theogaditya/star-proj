#!/usr/bin/env python3
"""
multi_run_stats.py — Aggregate summary.csv files across N replicate runs.

Usage:
    python3 analysis/multi_run_stats.py \
        --experiment experiment-c-stateful \
        --multi-proc-dir results/processed/websocket/experiment-c-stateful/multi

For each metric column found in summary.csv, computes mean ± std, min, max
across all run_* subdirectories and writes aggregate_stats.csv.

Also prints a human-readable table to stdout.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

# Allow importing metrics_utils from parent dir when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics_utils import compute_stats


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate multi-run summary CSVs")
    p.add_argument("--experiment", required=True, help="Experiment name (for display)")
    p.add_argument(
        "--multi-proc-dir",
        required=True,
        help="Path to <experiment>/multi/ containing run_1, run_2, … subdirs",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Output CSV path (default: <multi-proc-dir>/aggregate_stats.csv)",
    )
    return p.parse_args()


def collect_summaries(multi_proc_dir: str) -> pd.DataFrame:
    """Read summary.csv from every run_* subdir and stack them."""
    rows = []
    for entry in sorted(os.listdir(multi_proc_dir)):
        if not entry.startswith("run_"):
            continue
        run_dir = os.path.join(multi_proc_dir, entry)
        summary_path = os.path.join(run_dir, "summary.csv")
        if not os.path.exists(summary_path):
            print(f"  WARNING: {summary_path} not found, skipping.")
            continue
        df = pd.read_csv(summary_path)
        df["run"] = entry
        rows.append(df)

    if not rows:
        print("ERROR: No summary.csv files found under", multi_proc_dir)
        sys.exit(1)

    return pd.concat(rows, ignore_index=True)


def aggregate(combined: pd.DataFrame) -> pd.DataFrame:
    """For every metric column, compute mean/std/min/max/n."""
    skip_cols = {"run", "metric", "value"}  # pivot-style summary has metric+value cols

    # Detect format: wide (one column per metric) or long (metric, value)
    if "metric" in combined.columns and "value" in combined.columns:
        # Long format — pivot to wide first
        wide = combined.pivot_table(
            index="run", columns="metric", values="value", aggfunc="first"
        )
    else:
        wide = combined.drop(columns=[c for c in skip_cols if c in combined.columns], errors="ignore")
        # Drop non-numeric columns
        wide = wide.select_dtypes(include=[np.number])

    agg_rows = []
    for col in wide.columns:
        vals = wide[col].dropna().tolist()
        if not vals:
            continue
        stats = compute_stats(vals)
        agg_rows.append({
            "metric": col,
            "mean": stats["mean"],
            "std": stats["std"],
            "min": stats["min"],
            "max": stats["max"],
            "n": stats["n"],
        })

    return pd.DataFrame(agg_rows)


def print_table(agg: pd.DataFrame, experiment: str, n_runs: int) -> None:
    print()
    print(f"=== Aggregate Statistics: {experiment}  ({n_runs} runs) ===")
    print(f"{'Metric':<30} {'Mean':>12} {'Std':>10} {'Min':>10} {'Max':>10} {'N':>4}")
    print("-" * 78)
    for _, row in agg.iterrows():
        print(
            f"{row['metric']:<30} "
            f"{row['mean']:>12.3f} "
            f"{row['std']:>10.3f} "
            f"{row['min']:>10.3f} "
            f"{row['max']:>10.3f} "
            f"{int(row['n']):>4}"
        )
    print()


def main() -> None:
    args = parse_args()

    if not os.path.isdir(args.multi_proc_dir):
        print(f"ERROR: --multi-proc-dir '{args.multi_proc_dir}' does not exist.")
        sys.exit(1)

    combined = collect_summaries(args.multi_proc_dir)
    n_runs = combined["run"].nunique() if "run" in combined.columns else len(combined)

    agg = aggregate(combined)

    out_path = args.out or os.path.join(args.multi_proc_dir, "aggregate_stats.csv")
    agg.to_csv(out_path, index=False)
    print(f"  Aggregate stats written → {out_path}")

    print_table(agg, args.experiment, n_runs)


if __name__ == "__main__":
    main()
