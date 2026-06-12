#!/usr/bin/env python3
"""
analysis.py — KRM HPA Experiment Analysis
Loads CSV logs from experiment runs and generates:
  1. Time-series plots (replicas, CPU, HPA desired vs current)
  2. Derived metrics table (time-to-scale-up, stabilization, etc.)
  3. Comparison summary across metric-resolution values

Usage:
    python3 analysis.py [results_dir]
    Default results_dir: ./results
"""

import os
import sys
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
from datetime import datetime, timedelta

# ── Configuration ─────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = SCRIPT_DIR.parent / "results"
RESULTS_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RESULTS_DIR
OUTPUT_DIR = RESULTS_DIR / "plots"
RESOLUTIONS = ["60s", "30s", "15s"]
TARGET_UTIL = 60  # target CPU utilization %

# ── Data Loading ──────────────────────────────────────────────────────────────


def load_csv(run_dir: Path, filename: str) -> pd.DataFrame:
    """Load a CSV file from a run directory, return empty DataFrame if missing."""
    filepath = run_dir / filename
    if not filepath.exists():
        print(f"  [WARN] Missing: {filepath}")
        return pd.DataFrame()
    df = pd.read_csv(filepath, parse_dates=["timestamp"])
    return df


def load_phases(run_dir: Path) -> pd.DataFrame:
    """Load phases.log."""
    filepath = run_dir / "phases.log"
    if not filepath.exists():
        return pd.DataFrame()
    df = pd.read_csv(filepath, parse_dates=["timestamp"])
    return df


def load_all_runs():
    """Load data for all resolution runs."""
    data = {}
    for res in RESOLUTIONS:
        run_dir = RESULTS_DIR / res
        if not run_dir.exists():
            print(f"[SKIP] No data for {res}")
            continue
        print(f"[LOAD] {res}...")
        data[res] = {
            "pod_cpu": load_csv(run_dir, "pod_cpu.csv"),
            "hpa_log": load_csv(run_dir, "hpa_log.csv"),
            "podcount": load_csv(run_dir, "podcount.csv"),
            "phases": load_phases(run_dir),
        }
    return data


# ── Derived Metrics ───────────────────────────────────────────────────────────


def compute_derived_metrics(run_data: dict) -> dict:
    """Compute derived metrics for a single experiment run."""
    hpa = run_data["hpa_log"]
    phases = run_data["phases"]
    metrics = {}

    if hpa.empty or phases.empty:
        return metrics

    # Time-to-scale-up: time from first high-start to first replica increase
    high_starts = phases[(phases["phase"] == "high") & (phases["action"] == "start")]
    if not high_starts.empty and not hpa.empty:
        first_high = high_starts.iloc[0]["timestamp"]
        initial_replicas = hpa.iloc[0]["currentReplicas"] if "currentReplicas" in hpa.columns else None
        if initial_replicas is not None:
            scaled = hpa[
                (hpa["timestamp"] > first_high)
                & (hpa["currentReplicas"] > initial_replicas)
            ]
            if not scaled.empty:
                metrics["time_to_scale_up_s"] = (
                    scaled.iloc[0]["timestamp"] - first_high
                ).total_seconds()

    # Time-to-stabilize and stabilized start time
    stabilized_start_time = None
    if "currentReplicas" in hpa.columns and len(hpa) > 1:
        stable_start = None
        for i in range(1, len(hpa)):
            if hpa.iloc[i]["currentReplicas"] == hpa.iloc[i - 1]["currentReplicas"]:
                if stable_start is None:
                    stable_start = hpa.iloc[i - 1]["timestamp"]
                elapsed = (hpa.iloc[i]["timestamp"] - stable_start).total_seconds()
                if elapsed >= 60:
                    first_high = high_starts.iloc[0]["timestamp"] if not high_starts.empty else hpa.iloc[0]["timestamp"]
                    metrics["time_to_stabilize_s"] = (
                        stable_start - first_high
                    ).total_seconds()
                    stabilized_start_time = stable_start
                    break
            else:
                stable_start = None

    # Max replicas
    if "currentReplicas" in hpa.columns:
        metrics["max_replicas"] = int(hpa["currentReplicas"].max())

    # Average CPU utilization (during stabilized window)
    if stabilized_start_time and "currentCPUUtilizationPercent" in hpa.columns:
        cpu_vals = pd.to_numeric(hpa[hpa["timestamp"] >= stabilized_start_time]["currentCPUUtilizationPercent"], errors="coerce")
        metrics["avg_cpu_util"] = round(cpu_vals.dropna().mean(), 1)
    elif "currentCPUUtilizationPercent" in hpa.columns:
        # Fallback to overall mean if no stabilization found
        cpu_vals = pd.to_numeric(hpa["currentCPUUtilizationPercent"], errors="coerce")
        metrics["avg_cpu_util"] = round(cpu_vals.dropna().mean(), 1)

    # Overshoot/undershoot area: sum(abs(util - target) * dt)
    if "currentCPUUtilizationPercent" in hpa.columns:
        hpa_sorted = hpa.sort_values("timestamp")
        area = 0.0
        prev_time = hpa_sorted.iloc[0]["timestamp"]
        for _, row in hpa_sorted.iterrows():
            curr_time = row["timestamp"]
            dt = (curr_time - prev_time).total_seconds()
            util = pd.to_numeric(row["currentCPUUtilizationPercent"], errors="coerce")
            if pd.notna(util):
                # Simple rectangle integration
                error = abs(util - TARGET_UTIL)
                area += error * dt
            prev_time = curr_time
        metrics["overshoot_undershoot_area"] = round(area, 1)

    # Resource cost: sum(replicas * 5s) = pod-seconds
    if "currentReplicas" in hpa.columns:
        metrics["pod_seconds"] = int(hpa["currentReplicas"].sum() * 5)

    # Pod CPU Variance/StdDev (load dispersion)
    if "pod_cpu" in run_data and not run_data["pod_cpu"].empty:
        df_cpu = run_data["pod_cpu"]
        if "cpu" in df_cpu.columns:
             # Convert "100m" to float if needed, assuming clean float/int in CSV
             # The CSV collector stores raw kubectl output (e.g. 100m)
             # Need to clean it: remove 'm', convert to int. If no 'm', it's cores?
             # My collector clean-up logic isn't perfect, let's assume raw string
             # Actually, kubectl top pods returns "100m" or "1" (cores).
             # I need to parse it.
             def parse_cpu(val):
                 val = str(val).strip()
                 if val.endswith('m'):
                     return int(val[:-1])
                 return int(float(val) * 1000)
             
             try:
                 cpu_milli = df_cpu["cpu"].apply(parse_cpu)
                 metrics["pod_cpu_std_dev"] = round(cpu_milli.std(), 1)
             except Exception:
                 metrics["pod_cpu_std_dev"] = None

    return metrics


# ── Plotting ──────────────────────────────────────────────────────────────────


def plot_replicas_over_time(data: dict):
    """Plot pod replicas vs time for all resolutions."""
    fig, ax = plt.subplots(figsize=(14, 6))
    for res, run_data in data.items():
        hpa = run_data["hpa_log"]
        if hpa.empty or "currentReplicas" not in hpa.columns:
            continue
        # Normalize time to minutes from start
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60
        ax.plot(minutes, hpa["currentReplicas"], label=f"resolution={res}", linewidth=1.5)

    ax.set_xlabel("Time (minutes)")
    ax.set_ylabel("Replicas")
    ax.set_title("Pod Replicas Over Time (by metric-resolution)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "replicas_over_time.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'replicas_over_time.png'}")


def plot_cpu_over_time(data: dict):
    """Plot average CPU utilization vs time for all resolutions."""
    fig, ax = plt.subplots(figsize=(14, 6))
    for res, run_data in data.items():
        hpa = run_data["hpa_log"]
        if hpa.empty or "currentCPUUtilizationPercent" not in hpa.columns:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60
        cpuutil = pd.to_numeric(hpa["currentCPUUtilizationPercent"], errors="coerce")
        ax.plot(minutes, cpuutil, label=f"resolution={res}", linewidth=1.5)

    ax.axhline(y=TARGET_UTIL, color="red", linestyle="--", alpha=0.7, label=f"Target ({TARGET_UTIL}%)")
    ax.set_xlabel("Time (minutes)")
    ax.set_ylabel("CPU Utilization (%)")
    ax.set_title("HPA CPU Utilization Over Time (by metric-resolution)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "cpu_over_time.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'cpu_over_time.png'}")


def plot_desired_vs_current(data: dict):
    """Plot HPA desired vs current replicas for each resolution."""
    n = len(data)
    if n == 0:
        return
    fig, axes = plt.subplots(1, n, figsize=(7 * n, 5), sharey=True)
    if n == 1:
        axes = [axes]

    for ax, (res, run_data) in zip(axes, data.items()):
        hpa = run_data["hpa_log"]
        if hpa.empty:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60
        if "currentReplicas" in hpa.columns:
            ax.plot(minutes, hpa["currentReplicas"], label="current", linewidth=1.5)
        if "desiredReplicas" in hpa.columns:
            ax.plot(minutes, hpa["desiredReplicas"], label="desired", linestyle="--", linewidth=1.5)
        ax.set_title(f"resolution={res}")
        ax.set_xlabel("Time (min)")
        ax.legend()
        ax.grid(True, alpha=0.3)

    axes[0].set_ylabel("Replicas")
    fig.suptitle("HPA Desired vs Current Replicas", fontsize=14)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "desired_vs_current.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'desired_vs_current.png'}")


def plot_efficiency_scatter(data: dict):
    """Plot Scatter of Replicas vs CPU Utilization (Efficiency Frontier)."""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    for res, run_data in data.items():
        hpa = run_data["hpa_log"]
        if hpa.empty or "currentReplicas" not in hpa.columns or "currentCPUUtilizationPercent" not in hpa.columns:
            continue
            
        # Filter out NaN
        df = hpa.dropna(subset=["currentReplicas", "currentCPUUtilizationPercent"])
        if df.empty:
            continue

        replicas = df["currentReplicas"]
        cpu = pd.to_numeric(df["currentCPUUtilizationPercent"], errors="coerce")
        
        ax.scatter(replicas, cpu, alpha=0.4, s=30, label=f"resolution={res}")

    ax.set_xlabel("Replicas")
    ax.set_ylabel("CPU Utilization (%)")
    ax.set_title("Scaling Efficiency: CPU vs Replicas")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "efficiency_scatter.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'efficiency_scatter.png'}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print(f"KRM HPA Experiment Analysis")
    print(f"Results directory: {RESULTS_DIR}")
    print()

    # Load data
    data = load_all_runs()
    if not data:
        print("No data found. Run experiments first.")
        sys.exit(1)

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate plots
    print("\n[PLOTS]")
    plot_replicas_over_time(data)
    plot_cpu_over_time(data)
    plot_desired_vs_current(data)
    plot_efficiency_scatter(data)

    # Compute derived metrics
    print("\n[DERIVED METRICS]")
    summary = {}
    for res, run_data in data.items():
        metrics = compute_derived_metrics(run_data)
        summary[res] = metrics
        print(f"  {res}: {metrics}")

    # Summary table
    if summary:
        df_summary = pd.DataFrame(summary).T
        df_summary.index.name = "metric_resolution"
        print("\n=== Summary Table ===")
        print(df_summary.to_string())
        df_summary.to_csv(OUTPUT_DIR / "summary_table.csv")
        print(f"\n  → {OUTPUT_DIR / 'summary_table.csv'}")

    print("\n[DONE] All outputs in:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
