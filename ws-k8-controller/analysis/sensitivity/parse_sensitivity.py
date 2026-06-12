#!/usr/bin/env python3
"""Parse raw sensitivity-analysis runs into per-run metrics and the four
result tables required by Paper 2 (Section: Sensitivity Analysis).

Input : results/raw/websocket/sensitivity/<tag>/{params.json,replicas.log,
        phase.log,prometheus_dump.csv}
Output: results/processed/websocket/sensitivity/sensitivity_results.csv
        results/processed/websocket/sensitivity/table_{cooldown,target,scrape,step,scale}.csv
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW = os.environ.get("SENS_RAW", os.path.join(ROOT, "results", "raw", "websocket", "sensitivity"))
OUT = os.environ.get("SENS_OUT", os.path.join(ROOT, "results", "processed", "websocket", "sensitivity"))


def read_replicas(d):
    rows = []
    path = os.path.join(d, "replicas.log")
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            try:
                ts, r = line.strip().split(",")
                rows.append((int(ts), int(r)))
            except ValueError:
                continue
    return rows


def read_conns(d):
    rows = []
    path = os.path.join(d, "prometheus_dump.csv")
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        next(f, None)
        for line in f:
            try:
                ts, v = line.strip().split(",")
                rows.append((int(ts), float(v)))
            except ValueError:
                continue
    return rows


def read_phases(d):
    phases = {}
    path = os.path.join(d, "phase.log")
    if not os.path.exists(path):
        return phases
    with open(path) as f:
        for line in f:
            try:
                ts, name = line.strip().split(",")
                phases.setdefault(name, int(ts))
            except ValueError:
                continue
    return phases


def first(seq, pred):
    for item in seq:
        if pred(item):
            return item
    return None


def analyse_run(d):
    with open(os.path.join(d, "params.json")) as f:
        p = json.load(f)
    replicas = read_replicas(d)
    conns = read_conns(d)
    phases = read_phases(d)
    if not replicas or not phases:
        print(f"[skip] {d}: missing logs", file=sys.stderr)
        return None

    expected = p["expected_replicas"]
    target = p["target"]
    minr = p["min_replicas"]

    row = dict(p)
    row["peak_replicas"] = max(r for _, r in replicas)
    row["peak_connections"] = max((v for _, v in conns), default=0)

    # --- scale-up reaction time ---
    t_c1 = phases.get("CYCLE_1")
    t_trigger = first(conns, lambda x: x[0] >= t_c1 and x[1] > target * minr) if t_c1 else None
    t_reached = first(replicas, lambda x: t_trigger and x[0] >= t_trigger[0] and x[1] >= expected)
    row["scale_up_s"] = (t_reached[0] - t_trigger[0]) if (t_trigger and t_reached) else ""

    # --- DROP 1 safety ---
    t_d1, t_c2 = phases.get("DROP_1"), phases.get("CYCLE_2")
    if t_d1 and t_c2:
        at_start = first(reversed([x for x in replicas if x[0] <= t_d1]), lambda x: True)
        gap_vals = [r for ts, r in replicas if t_d1 <= ts < t_c2]
        row["replicas_at_gap_start"] = at_start[1] if at_start else ""
        row["min_replicas_gap"] = min(gap_vals) if gap_vals else ""
        if at_start and gap_vals:
            row["safe"] = int(min(gap_vals) >= at_start[1])
        else:
            row["safe"] = ""
    else:
        row["replicas_at_gap_start"] = row["min_replicas_gap"] = row["safe"] = ""

    # --- FINAL DROP scale-down behaviour ---
    t_fd = phases.get("FINAL_DROP")
    if t_fd:
        base = first(reversed([x for x in replicas if x[0] <= t_fd]), lambda x: True)
        base_r = base[1] if base else expected
        t_start = first(replicas, lambda x: x[0] > t_fd and x[1] < base_r)
        t_end = first(replicas, lambda x: x[0] > t_fd and x[1] <= minr)
        row["scaledown_reaction_s"] = (t_start[0] - t_fd) if t_start else ""
        row["scaledown_duration_s"] = (t_end[0] - t_fd) if t_end else ""
        post = [x for x in replicas if x[0] > t_fd]
        max_step = 0
        for (_ts1, r1), (_ts2, r2) in zip(post, post[1:]):
            max_step = max(max_step, r1 - r2)
        row["max_step_down"] = max_step
    else:
        row["scaledown_reaction_s"] = row["scaledown_duration_s"] = row["max_step_down"] = ""

    row["blast_radius_conns"] = p["step"] * target  # theoretical per-cycle bound
    return row


def main():
    if not os.path.isdir(RAW):
        sys.exit(f"No raw sensitivity results at {RAW}")
    rows = []
    for tag in sorted(os.listdir(RAW)):
        d = os.path.join(RAW, tag)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "params.json")):
            r = analyse_run(d)
            if r:
                rows.append(r)
    if not rows:
        sys.exit("No parsable runs found.")

    os.makedirs(OUT, exist_ok=True)
    cols = ["tag", "sweep", "cooldown", "target", "scrape", "step", "gap", "clients",
            "expected_replicas", "scale_up_s", "replicas_at_gap_start", "min_replicas_gap",
            "safe", "scaledown_reaction_s", "scaledown_duration_s", "max_step_down",
            "blast_radius_conns", "peak_replicas", "peak_connections"]

    def write(path, subset):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(subset)
        print(f"[ok] {path} ({len(subset)} rows)")

    write(os.path.join(OUT, "sensitivity_results.csv"), rows)
    for sweep in sorted({r["sweep"] for r in rows}):
        write(os.path.join(OUT, f"table_{sweep}.csv"), [r for r in rows if r["sweep"] == sweep])


if __name__ == "__main__":
    main()
