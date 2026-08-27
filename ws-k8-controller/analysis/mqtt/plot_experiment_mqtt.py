"""
plot_experiment_mqtt.py

Produces time-series plots for MQTT experiments.

Single experiment (Exp A or B):
  python plot_experiment_mqtt.py --csv out.csv --title "..." --out plot.png

Comparison (Exp C):
  python plot_experiment_mqtt.py \
    --csv-hpa  exp-c-hpa/out.csv \
    --csv-star exp-c-star/out.csv \
    --out comparison.png
"""
import argparse
import csv
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from matplotlib.patches import FancyArrowPatch

# ── Styling ───────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 14,
    "axes.titlesize": 16,
    "axes.labelsize": 14,
    "legend.fontsize": 12,
    "xtick.labelsize": 12,
    "ytick.labelsize": 12,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.facecolor": "#f8f9fa",
    "figure.facecolor": "#ffffff",
    "axes.axisbelow": True,
})

COLOR_CONN   = "#1f77b4"   # blue
COLOR_REP    = "#d62728"   # red
COLOR_CPU    = "#2ca02c"   # green
COLOR_MEM    = "#ff7f0e"   # orange
COLOR_PHASE  = "#999999"   # grey for phase markers
PERPOD_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]


def load_csv(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = {
                "time_s":             float(row["time_s"]),
                "replicas":           float(row["replicas"]),
                "active_connections": float(row["active_connections"]),
            }
            if "cpu_millicores" in row:
                d["cpu_millicores"] = float(row["cpu_millicores"])
            if "memory_mi" in row:
                d["memory_mi"] = float(row["memory_mi"])
            rows.append(d)
    return rows


def load_perpod_csv(path):
    """Load per-pod connection CSV. Returns (time_list, {pod_name: [values]})"""
    if not os.path.exists(path):
        return None, None
    pods = {}
    times = []
    with open(path) as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames
        pod_cols = [c for c in cols if c.startswith("conn_")]
        for row in reader:
            times.append(float(row["time_s"]))
            for pc in pod_cols:
                pod_name = pc.replace("conn_", "")
                pods.setdefault(pod_name, []).append(float(row[pc]))
    return times, pods


def format_perpod_label(pod_name):
    """Return a readable legend label for real pod names and degraded fallbacks."""
    if pod_name in {"x", "unknown"}:
        return "Aggregated connections"
    if ":" in pod_name and "." in pod_name:
        return f"Target {pod_name}"
    if "-" in pod_name:
        short_name = pod_name.split("-")[-1][:8]
    else:
        short_name = pod_name[:12]
    return f"Pod {short_name}"


def load_phases(path):
    """Load phase timestamps from JSON."""
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def has_data(rows, key):
    return rows and key in rows[0] and any(r.get(key, 0) > 0 for r in rows)


# Phase labels for each experiment mode
PHASE_LABELS_EXP_A = {
    "PHASE1_START": ("Phase 1\n[FAIL] Scale-up\n   Blindness",       "#c0392b"),
    "PHASE2_START": ("Phase 2\n[FAIL] No\n   Redistribution",        "#c0392b"),
    "PHASE3_START": ("Phase 3\n[FAIL] Violent\n   Disconnection",    "#c0392b"),
}
PHASE_LABELS_EXP_B = {
    "PHASE1_START": ("Phase 1\n[OK] Scale-up\n   Fixed",            "#27ae60"),
    "PHASE2_START": ("Phase 2\n[OK] Even\n   Distribution",         "#27ae60"),
    "PHASE3_START": ("Phase 3\n[OK] Graceful\n   Drain",            "#27ae60"),
}


def add_phase_markers(ax, phases, y_max, mode="exp_a"):
    """Add vertical dashed lines and labels for phase transitions."""
    label_map = PHASE_LABELS_EXP_B if mode == "exp_b" else PHASE_LABELS_EXP_A
    for key, (label, color) in label_map.items():
        if key in phases:
            t = phases[key]
            ax.axvline(x=t, color=color, linestyle="--", linewidth=1.5, alpha=0.8)
            ax.text(t + 5, y_max * 0.92, label, fontsize=10, color=color,
                    va="top", ha="left", fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.85, edgecolor=color))


def plot_single(rows, title, out, perpod_path=None, phases_path=None, mode="exp_a", target_per_pod=None):
    show_cpu = has_data(rows, "cpu_millicores")
    show_mem = has_data(rows, "memory_mi")

    # Load optional data
    perpod_times, perpod_data = load_perpod_csv(perpod_path) if perpod_path else (None, None)
    phases = load_phases(phases_path) if phases_path else {}

    # Determine panel count
    nrows = 1
    if show_cpu or show_mem:
        nrows = 2
    if perpod_data:
        nrows = 3

    ratios = [3] + [1.5] * (nrows - 1)
    fig, axes = plt.subplots(nrows, 1, figsize=(14, 3.5 * nrows + 1), sharex=True,
                             gridspec_kw={"height_ratios": ratios})
    if nrows == 1:
        axes = [axes]

    t    = [r["time_s"] for r in rows]
    conn = [r["active_connections"] for r in rows]
    rep  = [r["replicas"] for r in rows]

    # ── Panel 1: Connections + Replicas ──────────────────────────
    ax1 = axes[0]
    ax1.plot(t, conn, color=COLOR_CONN, label="Active connections", linewidth=1.8, alpha=0.9)
    ax1.set_ylabel("Active MQTT Connections", color=COLOR_CONN, fontweight="bold")
    ax1.tick_params(axis="y", labelcolor=COLOR_CONN)
    ax1.fill_between(t, conn, alpha=0.08, color=COLOR_CONN)

    ax2 = ax1.twinx()
    ax2.step(t, rep, color=COLOR_REP, label="Replica count", linewidth=2.5, where="post", alpha=0.85)
    ax2.set_ylabel("Broker Replicas", color=COLOR_REP, fontweight="bold")
    ax2.set_ylim(0, max(rep) + 1)  # Always start at 0 so replicas don't look like zero
    ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    ax2.tick_params(axis="y", labelcolor=COLOR_REP)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left",
               framealpha=0.9, edgecolor="#cccccc")

    if phases:
        max_conn = max(conn) if conn else 100
        add_phase_markers(ax1, phases, max_conn, mode=mode)

    # ── Panel 2: CPU + Memory ────────────────────────────────────
    if nrows >= 2:
        ax_res = axes[1]
        if show_cpu:
            cpu = [r.get("cpu_millicores", 0) for r in rows]
            ax_res.plot(t, cpu, color=COLOR_CPU, linewidth=1.5, label="CPU (millicores)", alpha=0.9)
            ax_res.fill_between(t, cpu, alpha=0.15, color=COLOR_CPU)
            ax_res.set_ylabel("CPU (m)", color=COLOR_CPU, fontweight="bold")
            ax_res.tick_params(axis="y", labelcolor=COLOR_CPU)

        if show_mem:
            mem = [r.get("memory_mi", 0) for r in rows]
            if show_cpu:
                ax_mem = ax_res.twinx()
            else:
                ax_mem = ax_res
            ax_mem.plot(t, mem, color=COLOR_MEM, linewidth=1.5, label="Memory (MiB)",
                        linestyle="-", alpha=0.9)
            ax_mem.fill_between(t, mem, alpha=0.1, color=COLOR_MEM)
            ax_mem.set_ylabel("Memory (MiB)", color=COLOR_MEM, fontweight="bold")
            ax_mem.tick_params(axis="y", labelcolor=COLOR_MEM)

        # Build combined legend
        handles, labels = [], []
        for ax in ([ax_res, ax_mem] if show_mem and show_cpu else [ax_res]):
            h, l = ax.get_legend_handles_labels()
            handles.extend(h)
            labels.extend(l)
        ax_res.legend(handles, labels, loc="upper left", framealpha=0.9, edgecolor="#cccccc")

    # ── Panel 3: Per-Pod Connections ─────────────────────────────
    if nrows >= 3 and perpod_data:
        ax_pp = axes[2]
        for i, (pod_name, values) in enumerate(sorted(perpod_data.items())):
            color = PERPOD_COLORS[i % len(PERPOD_COLORS)]
            ax_pp.plot(perpod_times, values, color=color, linewidth=1.3,
                       label=format_perpod_label(pod_name), alpha=0.85)
            ax_pp.fill_between(perpod_times, values, alpha=0.08, color=color)
        # For Exp B: draw the target connections/pod threshold line
        if mode == "exp_b" and target_per_pod:
            ax_pp.axhline(y=target_per_pod, color="#27ae60", linestyle=":",
                          linewidth=1.5, alpha=0.8, label=f"Target: {target_per_pod} conn/pod")

        ax_pp.set_ylabel("Connections per Pod", fontweight="bold")
        ax_pp.set_xlabel("Time (s)")
        ax_pp.legend(loc="upper left", framealpha=0.9, edgecolor="#cccccc", fontsize=8, ncol=2)
    else:
        axes[-1].set_xlabel("Time (s)")

    fig.suptitle(title, fontsize=18, fontweight="bold", y=0.98)
    plt.tight_layout()
    plt.savefig(out, dpi=300, bbox_inches="tight")
    print(f"[✓] Saved {out}")
    plt.close()


def plot_comparison(rows_hpa, rows_star, out):
    """Side-by-side comparison for Experiment C."""
    show_cpu = has_data(rows_hpa, "cpu_millicores") or has_data(rows_star, "cpu_millicores")
    nrows = 2 if show_cpu else 1

    fig, all_axes = plt.subplots(nrows, 2, figsize=(18, 4 * nrows + 1), sharex="col",
                                 gridspec_kw={"height_ratios": [3, 1] if show_cpu else [1]})
    if nrows == 1:
        all_axes = [all_axes]

    for col, (rows, label) in enumerate([
        (rows_hpa,  "HPA (CPU-based)"),
        (rows_star, "StatefulAutoscaler (connection-aware)"),
    ]):
        t    = [r["time_s"] for r in rows]
        conn = [r["active_connections"] for r in rows]
        rep  = [r["replicas"] for r in rows]

        ax = all_axes[0][col]
        ax.plot(t, conn, color=COLOR_CONN, label="Active connections", linewidth=1.5, alpha=0.9)
        ax.set_ylabel("Active MQTT connections", color=COLOR_CONN)
        ax.tick_params(axis="y", labelcolor=COLOR_CONN)
        ax.fill_between(t, conn, alpha=0.08, color=COLOR_CONN)

        ax2 = ax.twinx()
        ax2.step(t, rep, color=COLOR_REP, label="Replica count", linewidth=2.2, where="post")
        ax2.set_ylabel("Broker replicas", color=COLOR_REP)
        ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
        ax2.tick_params(axis="y", labelcolor=COLOR_REP)

        lines1, labels1 = ax.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax.legend(lines1 + lines2, labels1 + labels2, loc="upper right",
                  framealpha=0.9, edgecolor="#cccccc")
        ax.set_title(label)

        if show_cpu:
            cpu = [r.get("cpu_millicores", 0) for r in rows]
            ax_cpu = all_axes[1][col]
            ax_cpu.fill_between(t, cpu, alpha=0.3, color=COLOR_CPU)
            ax_cpu.plot(t, cpu, color=COLOR_CPU, linewidth=1.2, label="CPU (m)")
            ax_cpu.set_xlabel("Time (s)")
            ax_cpu.set_ylabel("CPU (m)", color=COLOR_CPU)
            ax_cpu.tick_params(axis="y", labelcolor=COLOR_CPU)
            ax_cpu.legend(loc="upper right", framealpha=0.9, edgecolor="#cccccc")
        else:
            ax.set_xlabel("Time (s)")

    fig.suptitle("Experiment-C MQTT: Idle Connections — HPA vs StatefulAutoscaler",
                 fontsize=18, fontweight="bold", y=0.98)
    plt.tight_layout()
    plt.savefig(out, dpi=300, bbox_inches="tight")
    print(f"[✓] Saved {out}")
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Plot MQTT experiment results")
    parser.add_argument("--csv",             help="Single CSV input")
    parser.add_argument("--perpod-csv",      help="Per-pod connections CSV (optional)")
    parser.add_argument("--phases-json",     help="Phase timestamps JSON (optional)")
    parser.add_argument("--title",           default="MQTT Experiment")
    parser.add_argument("--mode",            default="exp_a", choices=["exp_a", "exp_b"],
                        help="exp_a: red failure labels; exp_b: green fix labels")
    parser.add_argument("--target-per-pod",  help="Draw target connections/pod line (Exp B only)")
    parser.add_argument("--csv-hpa",         help="HPA CSV (comparison mode)")
    parser.add_argument("--csv-star",        help="StatefulAutoscaler CSV (comparison mode)")
    parser.add_argument("--out",             required=True)
    args = parser.parse_args()

    if args.csv_hpa and args.csv_star:
        plot_comparison(load_csv(args.csv_hpa), load_csv(args.csv_star), args.out)
    elif args.csv:
        # Auto-detect perpod and phases files from CSV path
        perpod = args.perpod_csv or args.csv.replace(".csv", "_perpod.csv")
        phases = args.phases_json or args.csv.replace(".csv", "_phases.json")
        perpod = perpod if os.path.exists(perpod) else None
        phases = phases if os.path.exists(phases) else None
        target_per_pod = int(args.target_per_pod) if args.target_per_pod else None
        plot_single(load_csv(args.csv), args.title, args.out,
                    perpod_path=perpod, phases_path=phases,
                    mode=args.mode, target_per_pod=target_per_pod)
    else:
        print("Provide either --csv or both --csv-hpa and --csv-star")
        sys.exit(1)


if __name__ == "__main__":
    main()
