#!/usr/bin/env python3
"""
analysis.py — PCM HPA Experiment Analysis
Loads CSV logs from experiment runs and generates:
  1. Time-series plots (replicas, CPU, HTTP request rate, HPA desired vs current)
  2. Derived metrics table
  3. Comparison between PCM-CPU scraping periods and PCM-H vs PCM-CH

Usage:
    python3 analysis.py [results_dir]
    Default results_dir: ../results
"""

import os
import sys
import sys
from pathlib import Path

try:
    import pandas as pd
    import matplotlib.pyplot as plt
except ImportError as e:
    print("\n[ERROR] Missing Python dependencies.")
    print(f"  {e}")
    print("\nPlease run:")
    print("  source venv/bin/activate")
    print("  pip install -r requirements.txt")
    print("\nOr manually:")
    print("  pip install pandas matplotlib")
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_DIR = SCRIPT_DIR.parent / "results"
RESULTS_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RESULTS_DIR
OUTPUT_DIR = RESULTS_DIR / "plots"
TARGET_CPU_UTIL = 60  # target CPU utilization %

# Experiment directories to look for
EXPERIMENT_DIRS = {
    # PCM-CPU scraping period experiments
    "pcm-cpu-60s": "pcm-cpu/60s",
    "pcm-cpu-30s": "pcm-cpu/30s",
    "pcm-cpu-15s": "pcm-cpu/15s",
    # PCM-H and PCM-CH comparison
    "pcm-h": "pcm-h",
    "pcm-ch": "pcm-ch",
}


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
    filepath = run_dir / "phases.log"
    if not filepath.exists():
        return pd.DataFrame()
    df = pd.read_csv(filepath, parse_dates=["timestamp"])
    return df


def load_all_runs():
    """Load data for all experiment runs that exist."""
    data = {}
    for label, subdir in EXPERIMENT_DIRS.items():
        run_dir = RESULTS_DIR / subdir
        if not run_dir.exists():
            print(f"[SKIP] No data for {label} ({run_dir})")
            continue
        print(f"[LOAD] {label}...")
        data[label] = {
            "pod_cpu": load_csv(run_dir, "pod_cpu.csv"),
            "hpa_log": load_csv(run_dir, "hpa_log.csv"),
            "podcount": load_csv(run_dir, "podcount.csv"),
            "phases": load_phases(run_dir),
            "prometheus": load_csv(run_dir, "prometheus_metrics.csv")
            if (run_dir / "prometheus_metrics.csv").exists()
            else pd.DataFrame(),
            # Dedicated HTTP req/s collector output (new)
            "http_rps": load_csv(run_dir, "http_rps.csv")
            if (run_dir / "http_rps.csv").exists()
            else pd.DataFrame(),
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

    # Time-to-scale-up
    high_starts = phases[(phases["phase"] == "high") & (phases["action"] == "start")]
    if not high_starts.empty and not hpa.empty:
        first_high = high_starts.iloc[0]["timestamp"]
        initial_replicas = (
            hpa.iloc[0]["currentReplicas"] if "currentReplicas" in hpa.columns else None
        )
        if initial_replicas is not None:
            scaled = hpa[
                (hpa["timestamp"] > first_high)
                & (hpa["currentReplicas"] > initial_replicas)
            ]
            if not scaled.empty:
                metrics["time_to_scale_up_s"] = (
                    scaled.iloc[0]["timestamp"] - first_high
                ).total_seconds()

    # Time-to-stabilize
    stabilized_start_time = None
    if "currentReplicas" in hpa.columns and len(hpa) > 1:
        stable_start = None
        for i in range(1, len(hpa)):
            if hpa.iloc[i]["currentReplicas"] == hpa.iloc[i - 1]["currentReplicas"]:
                if stable_start is None:
                    stable_start = hpa.iloc[i - 1]["timestamp"]
                elapsed = (hpa.iloc[i]["timestamp"] - stable_start).total_seconds()
                if elapsed >= 60:
                    first_high = (
                        high_starts.iloc[0]["timestamp"]
                        if not high_starts.empty
                        else hpa.iloc[0]["timestamp"]
                    )
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

    # Average CPU utilization
    if "currentCPUUtilizationPercent" in hpa.columns:
        cpu_col = pd.to_numeric(
            hpa["currentCPUUtilizationPercent"], errors="coerce"
        )
        if stabilized_start_time is not None:
            cpu_vals = cpu_col[hpa["timestamp"] >= stabilized_start_time]
        else:
            cpu_vals = cpu_col
        metrics["avg_cpu_util"] = round(cpu_vals.dropna().mean(), 1)

    # Average HTTP requests/s — prefer dedicated http_rps.csv, then fall back
    avg_http_rps = None

    # 1. Best source: dedicated http_rps.csv (irate-based, cluster-wide total)
    http_rps_df = run_data.get("http_rps", pd.DataFrame())
    if not http_rps_df.empty and "total_rps" in http_rps_df.columns:
        vals = pd.to_numeric(http_rps_df["total_rps"], errors="coerce").dropna()
        if not vals.empty:
            avg_http_rps = vals.mean()
            metrics["peak_http_rps"] = round(float(vals.max()), 2)

    # 2. Fallback: HPA custom metric field
    if avg_http_rps is None and "httpRequestsPerSecond" in hpa.columns:
        http_col = pd.to_numeric(hpa["httpRequestsPerSecond"], errors="coerce")
        if not http_col.dropna().empty:
            avg_http_rps = http_col.dropna().mean()

    # 3. Fallback: prometheus_metrics.csv
    if avg_http_rps is None:
        prom = run_data.get("prometheus", pd.DataFrame())
        if not prom.empty and "metric" in prom.columns:
            prom_rates = prom[prom["metric"] == "http_requests_rate"]
            if not prom_rates.empty:
                vals = pd.to_numeric(prom_rates["value"], errors="coerce")
                avg_http_rps = vals.dropna().mean()

    if avg_http_rps is not None:
        metrics["avg_http_rps"] = round(float(avg_http_rps), 2)

    # Overshoot/undershoot area (CPU)
    if "currentCPUUtilizationPercent" in hpa.columns:
        hpa_sorted = hpa.sort_values("timestamp")
        area = 0.0
        prev_time = hpa_sorted.iloc[0]["timestamp"]
        for _, row in hpa_sorted.iterrows():
            curr_time = row["timestamp"]
            dt = (curr_time - prev_time).total_seconds()
            util = pd.to_numeric(
                row["currentCPUUtilizationPercent"], errors="coerce"
            )
            if pd.notna(util):
                area += abs(util - TARGET_CPU_UTIL) * dt
            prev_time = curr_time
        metrics["overshoot_undershoot_area"] = round(area, 1)

    # Resource cost (pod-seconds)
    if "currentReplicas" in hpa.columns:
        metrics["pod_seconds"] = int(hpa["currentReplicas"].sum() * 5)

    # Pod CPU variance
    pod_cpu_df = run_data.get("pod_cpu", pd.DataFrame())
    if not pod_cpu_df.empty and "cpu" in pod_cpu_df.columns:

        def parse_cpu(val):
            val = str(val).strip()
            if val.endswith("m"):
                return int(val[:-1])
            try:
                return int(float(val) * 1000)
            except ValueError:
                return None

        try:
            cpu_milli = pod_cpu_df["cpu"].apply(parse_cpu).dropna()
            metrics["pod_cpu_std_dev"] = round(cpu_milli.std(), 1)
        except Exception:
            pass

    return metrics


# ── Plotting ──────────────────────────────────────────────────────────────────


def add_phase_shading(ax, phases_df, t0):
    """Add shaded regions for high/low phases."""
    if phases_df.empty:
        return
    high_starts = phases_df[
        (phases_df["phase"] == "high") & (phases_df["action"] == "start")
    ]
    high_ends = phases_df[
        (phases_df["phase"] == "high") & (phases_df["action"] == "end")
    ]
    for _, start_row in high_starts.iterrows():
        s = (start_row["timestamp"] - t0).total_seconds() / 60
        # Find matching end
        matching_ends = high_ends[high_ends["timestamp"] > start_row["timestamp"]]
        if not matching_ends.empty:
            e = (matching_ends.iloc[0]["timestamp"] - t0).total_seconds() / 60
            ax.axvspan(s, e, alpha=0.1, color="red", label=None)


def plot_replicas_over_time(data: dict):
    """Plot pod replicas vs time for all experiments."""
    fig, ax = plt.subplots(figsize=(14, 6))
    for label, run_data in data.items():
        hpa = run_data["hpa_log"]
        if hpa.empty or "currentReplicas" not in hpa.columns:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60
        ax.plot(minutes, hpa["currentReplicas"], label=label, linewidth=1.5)

    ax.set_xlabel("Time (minutes)")
    ax.set_ylabel("Replicas")
    ax.set_title("Pod Replicas Over Time (PCM Experiments)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "replicas_over_time.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'replicas_over_time.png'}")


def plot_cpu_over_time(data: dict):
    """Plot average CPU utilization vs time."""
    fig, ax = plt.subplots(figsize=(14, 6))
    for label, run_data in data.items():
        hpa = run_data["hpa_log"]
        if hpa.empty or "currentCPUUtilizationPercent" not in hpa.columns:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60
        cpuutil = pd.to_numeric(
            hpa["currentCPUUtilizationPercent"], errors="coerce"
        )
        ax.plot(minutes, cpuutil, label=label, linewidth=1.5)

    ax.axhline(
        y=TARGET_CPU_UTIL,
        color="red",
        linestyle="--",
        alpha=0.7,
        label=f"Target ({TARGET_CPU_UTIL}%)",
    )
    ax.set_xlabel("Time (minutes)")
    ax.set_ylabel("CPU Utilization (%)")
    ax.set_title("HPA CPU Utilization Over Time (PCM Experiments)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "cpu_over_time.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'cpu_over_time.png'}")


def plot_http_rate_over_time(data: dict):
    """Plot HTTP request rate (req/s) over time for all experiments.

    Data source priority:
      1. http_rps.csv — dedicated irate collector (most accurate)
      2. hpa_log.csv  — httpRequestsPerSecond from HPA custom metric
      3. prometheus_metrics.csv — rate(http_requests_total[30s]) per pod
    """
    fig, ax = plt.subplots(figsize=(14, 6))
    has_data = False

    for label, run_data in data.items():
        hpa = run_data["hpa_log"]
        http_rps_df = run_data.get("http_rps", pd.DataFrame())
        prom = run_data.get("prometheus", pd.DataFrame())

        rps_series = None
        times = None

        # ── Source 1: dedicated http_rps.csv (total_rps = irate sum) ──────────
        if not http_rps_df.empty and "total_rps" in http_rps_df.columns:
            series = pd.to_numeric(http_rps_df["total_rps"], errors="coerce")
            if not series.dropna().empty:
                rps_series = series
                t0 = http_rps_df["timestamp"].iloc[0]
                times = (http_rps_df["timestamp"] - t0).dt.total_seconds() / 60

        # ── Source 2: HPA custom metric httpRequestsPerSecond ─────────────────
        if rps_series is None and not hpa.empty and "httpRequestsPerSecond" in hpa.columns:
            series = pd.to_numeric(hpa["httpRequestsPerSecond"], errors="coerce")
            if not series.dropna().empty:
                rps_series = series
                t0 = hpa["timestamp"].iloc[0]
                times = (hpa["timestamp"] - t0).dt.total_seconds() / 60

        # ── Source 3: prometheus_metrics.csv — per-pod rate averaged ─────────
        if rps_series is None and not prom.empty and "metric" in prom.columns:
            prom_rates = prom[prom["metric"] == "http_requests_rate"].copy()
            if not prom_rates.empty:
                prom_rates["value"] = pd.to_numeric(prom_rates["value"], errors="coerce")
                df_avg = prom_rates.groupby("timestamp")["value"].sum().reset_index()
                df_avg = df_avg.sort_values("timestamp")
                if not df_avg.empty:
                    rps_series = df_avg["value"]
                    t0 = hpa["timestamp"].iloc[0] if not hpa.empty else df_avg["timestamp"].iloc[0]
                    times = (df_avg["timestamp"] - t0).dt.total_seconds() / 60

        if rps_series is not None and times is not None:
            has_data = True
            ax.plot(times, rps_series.values, label=label, linewidth=1.5)

    if not has_data:
        plt.close(fig)
        print("  [SKIP] No HTTP req/s data available")
        return

    ax.set_xlabel("Time (minutes)")
    ax.set_ylabel("HTTP Requests / second (cluster total)")
    ax.set_title("HTTP Request Rate Over Time (PCM Experiments)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "http_rate_over_time.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'http_rate_over_time.png'}")


def plot_desired_vs_current(data: dict):
    """Plot HPA desired vs current replicas for each experiment."""
    n = len(data)
    if n == 0:
        return
    fig, axes = plt.subplots(1, min(n, 5), figsize=(6 * min(n, 5), 5), sharey=True)
    if n == 1:
        axes = [axes]

    for ax, (label, run_data) in zip(axes, list(data.items())[:5]):
        hpa = run_data["hpa_log"]
        if hpa.empty:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60
        if "currentReplicas" in hpa.columns:
            ax.plot(minutes, hpa["currentReplicas"], label="current", linewidth=1.5)
        if "desiredReplicas" in hpa.columns:
            ax.plot(
                minutes,
                hpa["desiredReplicas"],
                label="desired",
                linestyle="--",
                linewidth=1.5,
            )
        ax.set_title(label)
        ax.set_xlabel("Time (min)")
        ax.legend()
        ax.grid(True, alpha=0.3)

    axes[0].set_ylabel("Replicas")
    fig.suptitle("HPA Desired vs Current Replicas (PCM)", fontsize=14)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "desired_vs_current.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'desired_vs_current.png'}")


def plot_pcm_h_vs_pcm_ch(data: dict):
    """Side-by-side comparison of PCM-H and PCM-CH (paper Section 5.2.6)."""
    if "pcm-h" not in data or "pcm-ch" not in data:
        print("  [SKIP] Need both pcm-h and pcm-ch data for comparison plot")
        return

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for label in ["pcm-h", "pcm-ch"]:
        hpa = data[label]["hpa_log"]
        if hpa.empty:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60

        # CPU
        if "currentCPUUtilizationPercent" in hpa.columns:
            cpu = pd.to_numeric(hpa["currentCPUUtilizationPercent"], errors="coerce")
            axes[0].plot(minutes, cpu, label=label, linewidth=1.5)

        # Replicas
        if "currentReplicas" in hpa.columns:
            axes[1].plot(
                minutes, hpa["currentReplicas"], label=label, linewidth=1.5
            )

    axes[0].set_title("CPU Utilization (%)")
    axes[0].set_xlabel("Time (min)")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    axes[1].set_title("Replicas")
    axes[1].set_xlabel("Time (min)")
    axes[1].legend()
    axes[1].grid(True, alpha=0.3)

    fig.suptitle("PCM-H vs PCM-CH Comparison (Paper Section 5.2.6)", fontsize=14)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "pcm_h_vs_pcm_ch.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'pcm_h_vs_pcm_ch.png'}")


def plot_efficiency_scatter(data: dict):
    """Scatter of Replicas vs CPU Utilization."""
    fig, ax = plt.subplots(figsize=(10, 6))
    for label, run_data in data.items():
        hpa = run_data["hpa_log"]
        if (
            hpa.empty
            or "currentReplicas" not in hpa.columns
            or "currentCPUUtilizationPercent" not in hpa.columns
        ):
            continue
        df = hpa.dropna(subset=["currentReplicas", "currentCPUUtilizationPercent"])
        if df.empty:
            continue
        replicas = df["currentReplicas"]
        cpu = pd.to_numeric(df["currentCPUUtilizationPercent"], errors="coerce")
        ax.scatter(replicas, cpu, alpha=0.4, s=30, label=label)

    ax.set_xlabel("Replicas")
    ax.set_ylabel("CPU Utilization (%)")
    ax.set_title("Scaling Efficiency: CPU vs Replicas (PCM)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "efficiency_scatter.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'efficiency_scatter.png'}")


# ── Scraping Period Comparison (PCM-CPU) ──────────────────────────────────────


def plot_scraping_period_comparison(data: dict):
    """Compare PCM-CPU with different scraping periods (60s, 30s, 15s)."""
    scrape_data = {
        k: v for k, v in data.items() if k.startswith("pcm-cpu-")
    }
    if len(scrape_data) < 2:
        print("  [SKIP] Need ≥2 PCM-CPU scraping period runs for comparison")
        return

    fig, ax = plt.subplots(figsize=(8, 5))

    for label, run_data in scrape_data.items():
        hpa = run_data["hpa_log"]
        if hpa.empty:
            continue
        t0 = hpa["timestamp"].iloc[0]
        minutes = (hpa["timestamp"] - t0).dt.total_seconds() / 60

        if "currentReplicas" in hpa.columns:
            ax.plot(
                minutes, hpa["currentReplicas"], label=label, linewidth=1.5
            )

    ax.set_title("Replicas Over Time")
    ax.set_xlabel("Time (min)")
    ax.set_ylabel("Replicas")
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig.suptitle("PCM-CPU: Scraping Period Comparison", fontsize=14)
    fig.tight_layout()
    fig.savefig(OUTPUT_DIR / "scraping_period_comparison.png", dpi=150)
    plt.close(fig)
    print(f"  → {OUTPUT_DIR / 'scraping_period_comparison.png'}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print("PCM HPA Experiment Analysis")
    print(f"Results directory: {RESULTS_DIR}")
    print()

    data = load_all_runs()
    if not data:
        print("No data found. Run experiments first.")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate plots
    print("\n[PLOTS]")
    plot_replicas_over_time(data)
    plot_cpu_over_time(data)
    plot_http_rate_over_time(data)
    plot_desired_vs_current(data)
    plot_efficiency_scatter(data)
    plot_pcm_h_vs_pcm_ch(data)
    plot_scraping_period_comparison(data)

    # Compute derived metrics
    print("\n[DERIVED METRICS]")
    summary = {}
    for label, run_data in data.items():
        metrics = compute_derived_metrics(run_data)
        summary[label] = metrics
        print(f"  {label}: {metrics}")

    # Summary table
    if summary:
        df_summary = pd.DataFrame(summary).T
        df_summary.index.name = "experiment"
        print("\n=== Summary Table ===")
        print(df_summary.to_string())
        df_summary.to_csv(OUTPUT_DIR / "summary_table.csv")
        print(f"\n  → {OUTPUT_DIR / 'summary_table.csv'}")

    print("\n[DONE] All outputs in:", OUTPUT_DIR)


if __name__ == "__main__":
    main()
