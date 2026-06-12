"""
Shared metric computation utilities used by all experiment parse scripts.

Functions
---------
compute_pod_seconds(replicas_df, time_col, replicas_col, interval_s)
    Total pod-seconds consumed — the cost proxy.

compute_reaction_time(connections_df, replicas_df)
    Seconds between first significant connection spike and first replica change
    (scale-up reaction time).  Also returns scale-down reaction time.

compute_stats(values)
    Dict with mean, std, min, max for a list of scalar values.

write_summary(processed_dir, **kwargs)
    Appends/writes a summary.csv into processed_dir with arbitrary key=value pairs.
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd
from typing import Any


# ---------------------------------------------------------------------------
# Pod-seconds (cost proxy)
# ---------------------------------------------------------------------------

def compute_pod_seconds(
    replicas_df: pd.DataFrame,
    *,
    time_col: str = "timestamp",
    replicas_col: str = "spec_replicas",
    interval_s: float = 5.0,
) -> float:
    """
    Sum of replicas × time-interval for every row.

    Uses forward-fill to handle sparse replica logs (where the value is only
    written when it changes).  If the dataframe is empty returns 0.
    """
    if replicas_df.empty:
        return 0.0

    df = replicas_df[[time_col, replicas_col]].copy().sort_values(time_col)
    df[replicas_col] = df[replicas_col].ffill().fillna(0)

    # Compute per-row interval from timestamp deltas where available;
    # fall back to the supplied default interval for single-row frames.
    if len(df) > 1:
        df["_dt"] = df[time_col].diff().shift(-1).fillna(interval_s)
    else:
        df["_dt"] = interval_s

    return float((df[replicas_col] * df["_dt"]).sum())


# ---------------------------------------------------------------------------
# Scale reaction time
# ---------------------------------------------------------------------------

def compute_reaction_time(
    connections_df: pd.DataFrame,
    replicas_df: pd.DataFrame,
    *,
    conn_col: str = "active_connections",
    time_col: str = "timestamp",
    replicas_col: str = "spec_replicas",
    spike_threshold: float = 50.0,
    drop_threshold: float = -50.0,
) -> dict[str, float]:
    """
    Compute scale-up and scale-down reaction times.

    Scale-up reaction:
        Time from the first timestamp where active_connections increases by
        more than `spike_threshold` to the first timestamp where replicas
        increases.

    Scale-down reaction:
        Time from the first timestamp where active_connections drops below
        the spike_threshold (sustained) to the first timestamp where replicas
        decreases.

    Returns dict with keys:
        scale_up_s   — seconds (NaN if not detectable)
        scale_down_s — seconds (NaN if not detectable)
    """
    result: dict[str, float] = {"scale_up_s": float("nan"), "scale_down_s": float("nan")}

    if connections_df.empty or replicas_df.empty:
        return result

    conn = connections_df[[time_col, conn_col]].copy().sort_values(time_col)
    reps = replicas_df[[time_col, replicas_col]].copy().sort_values(time_col)

    # --- scale-up ---
    conn["_delta"] = conn[conn_col].diff()
    spike_rows = conn[conn["_delta"] >= spike_threshold]
    if not spike_rows.empty:
        t_spike = spike_rows.iloc[0][time_col]
        reps_after = reps[reps[time_col] >= t_spike]
        if len(reps_after) > 1:
            rep_changes = reps_after[reps_after[replicas_col].diff() > 0]
            if not rep_changes.empty:
                result["scale_up_s"] = float(rep_changes.iloc[0][time_col] - t_spike)

    # --- scale-down ---
    # first point where connections fall significantly from a high value
    conn["_delta"] = conn[conn_col].diff()
    drop_rows = conn[conn["_delta"] <= drop_threshold]
    if not drop_rows.empty:
        t_drop = drop_rows.iloc[0][time_col]
        reps_after = reps[reps[time_col] >= t_drop]
        if len(reps_after) > 1:
            rep_drops = reps_after[reps_after[replicas_col].diff() < 0]
            if not rep_drops.empty:
                result["scale_down_s"] = float(rep_drops.iloc[0][time_col] - t_drop)

    return result


# ---------------------------------------------------------------------------
# Statistical summary across N runs
# ---------------------------------------------------------------------------

def compute_stats(values: list[float]) -> dict[str, float]:
    """Return mean, std, min, max for a list of floats."""
    arr = np.array([v for v in values if not np.isnan(v)], dtype=float)
    if arr.size == 0:
        return {"mean": float("nan"), "std": float("nan"),
                "min": float("nan"), "max": float("nan"), "n": 0}
    return {
        "mean": float(np.mean(arr)),
        "std":  float(np.std(arr, ddof=1) if arr.size > 1 else 0.0),
        "min":  float(np.min(arr)),
        "max":  float(np.max(arr)),
        "n":    int(arr.size),
    }


# ---------------------------------------------------------------------------
# Convenience: write summary CSV
# ---------------------------------------------------------------------------

def write_summary(processed_dir: str, **kwargs: Any) -> None:
    """Write key=value pairs to processed_dir/summary.csv (one row)."""
    os.makedirs(processed_dir, exist_ok=True)
    path = os.path.join(processed_dir, "summary.csv")
    df = pd.DataFrame([kwargs])
    df.to_csv(path, index=False)
    print(f"  Wrote summary.csv → {path}")
