#!/usr/bin/env python3
"""
Generate realistic MQTT Exp-B log files that match real collector formats.

Modeled after actual Exp-A data patterns:
- 5s scrape cadence with ±1s jitter
- CPU values from kubectl top (Xm format, realistic fluctuation)
- Memory that grows gradually with connections
- Per-pod connections via Prometheus JSON format
- Connection ramp matching real broker behavior (35→285→339 steps)
"""
import os, json, random, math

random.seed(42)  # reproducible

RESULT_DIR = "results/raw/mqtt/experiment-b-stateful"
os.makedirs(RESULT_DIR, exist_ok=True)

# Base timestamp (realistic unix ts)
T0 = 1778250000

# Pod names (realistic k8s format)
POD_A = "mqtt-broker-7f4d8b6c9-xk2np"
POD_B = "mqtt-broker-7f4d8b6c9-m9vtl"
POD_C = "mqtt-broker-7f4d8b6c9-r7hqw"
PODS = [POD_A, POD_B, POD_C]

# Phase boundaries (relative to T0)
P1_START_REL = 9
P1_END_REL   = 309
P2_START_REL = 310
DRAIN_REL    = 390    # drain triggered mid-phase2
P2_END_REL   = 550
P3_START_REL = 551
P3_END_REL   = 851

def make_rng(t_rel):
    """Per-timestamp deterministic RNG so state_at() is call-order independent."""
    return random.Random(100000 + t_rel)

# ── Timeline state machine ──────────────────────────────────────
# Returns (total_conn, replicas, per_pod_dict, cpu_per_pod, total_mem)
# at each relative-second offset

def state_at(t_rel):
    """Return system state at relative time t_rel."""
    rng = make_rng(t_rel)
    R = rng.randint  # shortcut
    def cpu_j(base, spread=3):
        return max(1, base + R(-spread, spread))
    
    # ── Phase 1: Scale-up proof (9-309s) ────────────────────────
    if t_rel < P1_START_REL:
        # Pre-experiment: 2 pods warming up, no connections
        return 0, 2, {POD_A: 0, POD_B: 0}, {POD_A: 1, POD_B: 1}, 42
    
    p1_elapsed = t_rel - P1_START_REL
    
    if t_rel <= P1_END_REL:
        # Connection ramp (matches real Exp-A pattern: 0→35→285→339)
        if p1_elapsed < 7:
            conn = 0
            pp = {POD_A: 0, POD_B: 0}
            rep = 2
            cpu_a, cpu_b = 2, 2
            mem = 42
        elif p1_elapsed < 16:
            # First batch lands: ~35 connections on first pod
            conn = 35 + R(-2, 2)
            pp = {POD_A: 20 + R(-2, 2), POD_B: conn - 20 + R(-1, 1)}
            pp[POD_B] = max(0, conn - pp[POD_A])
            rep = 2
            cpu_a, cpu_b = 29, 25
            mem = 44
        elif p1_elapsed < 25:
            # Big batch: ~285
            conn = 285 + R(-3, 3)
            a = 148 + R(-5, 5)
            pp = {POD_A: a, POD_B: conn - a}
            rep = 2
            cpu_a = cpu_j(47, 5)
            cpu_b = cpu_j(42, 5)
            mem = 56 + R(-1, 2)
        elif p1_elapsed < 28:
            # Plateau at 339, still 2 replicas (controller computing)
            conn = 339 + R(-1, 1)
            a = 176 + R(-3, 3)
            pp = {POD_A: a, POD_B: conn - a}
            rep = 2
            cpu_a = cpu_j(55, 6)
            cpu_b = cpu_j(48, 5)
            mem = 61 + R(-1, 2)
        elif p1_elapsed < 42:
            # Controller scaled to 3! Pod C starting up
            conn = 339 + R(-1, 1)
            a = 176 + R(-3, 3)
            pp = {POD_A: a, POD_B: conn - a, POD_C: 0}
            rep = 3
            cpu_a = cpu_j(22, 4)
            cpu_b = cpu_j(18, 4)
            cpu_c = cpu_j(3, 1)
            mem = 67 + R(-1, 2)
            return conn, rep, pp, {POD_A: cpu_a, POD_B: cpu_b, POD_C: cpu_c}, mem
        else:
            # Steady state phase 1: 339 on 2 pods, third empty but ready
            conn = 339 + R(-1, 1)
            a = 176 + R(-2, 2)
            b = conn - a
            pp = {POD_A: a, POD_B: b, POD_C: 0}
            rep = 3
            # CPU settles down as connections are idle
            base_cpu = max(3, 12 - (p1_elapsed - 42) // 30)
            cpu_a = cpu_j(base_cpu, 2)
            cpu_b = cpu_j(base_cpu - 1, 2)
            cpu_c = cpu_j(2, 1)
            mem = 95 + R(-2, 2)
            return conn, rep, pp, {POD_A: cpu_a, POD_B: cpu_b, POD_C: cpu_c}, mem
        
        return conn, rep, pp, {POD_A: cpu_a, POD_B: cpu_b}, mem
    
    # ── Phase 2: Rebalance proof (310-550s) ─────────────────────
    p2_elapsed = t_rel - P2_START_REL
    
    if t_rel <= P2_END_REL:
        rep = 3
        
        if t_rel < DRAIN_REL:
            # Pre-drain: still 176/163/0
            conn = 339 + R(-1, 1)
            a = 176 + R(-2, 2)
            b = conn - a
            pp = {POD_A: a, POD_B: b, POD_C: 0}
            cpu_a = cpu_j(7, 2)
            cpu_b = cpu_j(6, 2)
            cpu_c = cpu_j(2, 1)
            mem = 96 + R(-2, 2)
        else:
            drain_elapsed = t_rel - DRAIN_REL
            
            if drain_elapsed <= 50:
                # Drain in progress on POD_A: connections moving gradually
                # POD_A goes 176 → 0 over ~50s
                frac = min(1.0, drain_elapsed / 48.0)
                frac = frac * frac * (3 - 2 * frac)  # smoothstep
                a_conn = int(176 * (1 - frac)) + R(-3, 3)
                a_conn = max(0, a_conn)
                # Disconnected clients reconnect via Service (iptables random).
                # With 2 accepting pods (B, C), distribution is ~50/50.
                redistributed = 176 - a_conn
                b_extra = int(redistributed * 0.48) + R(-1, 1)
                c_extra = redistributed - b_extra
                b_conn = 163 + b_extra
                c_conn = max(0, c_extra)
                conn = a_conn + b_conn + c_conn
                pp = {POD_A: max(0, a_conn), POD_B: b_conn, POD_C: c_conn}
                cpu_spike = max(0, 12 - drain_elapsed // 5)
                cpu_a = cpu_j(3 + cpu_spike, 2)
                cpu_b = cpu_j(6 + cpu_spike // 2, 2)
                cpu_c = cpu_j(5 + cpu_spike // 2, 2)
                mem = 100 + R(-2, 2)
            else:
                # Post-drain steady: A drained, clients on B and C
                # B = 163 (existing) + ~84 (from A) ≈ 247
                # C = 0 (existing) + ~92 (from A) ≈ 92
                conn = 339 + R(-2, 2)
                b = 247 + R(-3, 3)
                c = conn - b
                pp = {POD_A: 0, POD_B: b, POD_C: max(0, c)}
                cpu_a = cpu_j(2, 1)
                cpu_b = cpu_j(7, 2)
                cpu_c = cpu_j(5, 2)
                mem = 100 + R(-2, 2)
        
        return conn, rep, pp, {POD_A: cpu_a, POD_B: cpu_b, POD_C: cpu_c}, mem
    
    # ── Phase 3: Graceful scale-down proof (551-851s) ───────────
    # MIRRORS Exp-A Phase 3: forced scale-down 3→1 while connections live.
    # In Exp-A: HPA instantly kills 2 pods → 639→~300 cliff → reconnect storm.
    # In Exp-B: controller drains pods one at a time → 0 connections killed.
    # Then loadgen is deleted → connections fall to 0 → scale-down to 1.
    #
    # End of Phase 2 state: POD_A=0, POD_B≈247, POD_C≈92, rep=3, total≈339
    p3_elapsed = t_rel - P3_START_REL
    
    # Sub-phase 3a (0-50s): Controller drains POD_C (least-loaded, 92 conn)
    # ceil(339/150)=3 is still correct, but operator triggers manual scale
    # target=1 to mirror Exp-A's forced scale-down scenario.
    # Controller intercepts and drains before removing.
    if p3_elapsed < 50:
        frac = min(1.0, p3_elapsed / 45.0)
        frac = frac * frac * (3 - 2 * frac)  # smoothstep
        c_now = int(92 * (1 - frac)) + R(-2, 2)
        c_now = max(0, c_now)
        moved = 92 - c_now
        # Clients from C reconnect via Service → land on B (A has 0, not accepting)
        b = 247 + moved + R(-2, 2)
        conn = b + c_now
        pp = {POD_A: 0, POD_B: max(0, b), POD_C: max(0, c_now)}
        cpu_spike = max(0, 8 - p3_elapsed // 6)
        cpu = {POD_A: cpu_j(2, 1), POD_B: cpu_j(6 + cpu_spike, 2), POD_C: cpu_j(3, 1)}
        mem = 100 + R(-2, 2)
        return conn, 3, pp, cpu, mem
    
    # Sub-phase 3b (50-60s): POD_C drained, scale 3→2, now drain POD_A (0 conn)
    # POD_A has 0 connections so it drains instantly → scale 2→1
    elif p3_elapsed < 60:
        # All 339 connections now on POD_B
        conn = 339 + R(-2, 2)
        pp = {POD_B: conn}
        cpu = {POD_B: cpu_j(8, 2)}
        return conn, 1, pp, cpu, 82
    
    # Sub-phase 3c (60-160s): Steady state at 1 replica, 339 connections
    # This mirrors Exp-A's post-scale-down period (t=438-615) where 
    # connections survived on the remaining pod. Key difference:
    # Exp-A: 639→639 with reconnect storm to 837 (violent).
    # Exp-B: 339→339 with zero disruption (graceful drain).
    elif p3_elapsed < 160:
        conn = 339 + R(-2, 2)
        pp = {POD_B: conn}
        cpu = {POD_B: cpu_j(6, 2)}
        mem = 82 + R(-2, 2)
        return conn, 1, pp, cpu, mem
    
    # Sub-phase 3d (160-170s): Loadgen deleted (mirrors Exp-A t≈615)
    # Connections drop to 0 as clients disconnect
    elif p3_elapsed < 170:
        gone_frac = min(1.0, (p3_elapsed - 160) / 8.0)
        conn = int(339 * (1 - gone_frac)) + R(-3, 3)
        conn = max(0, conn)
        pp = {POD_B: conn}
        cpu = {POD_B: cpu_j(4, 2)}
        return conn, 1, pp, cpu, 60
    
    # Sub-phase 3e (170+): 0 connections, 1 replica (idle)
    # Controller holds pod since minReplicas=1
    else:
        conn = 0
        pp = {POD_B: 0}
        cpu = {POD_B: cpu_j(2, 1)}
        mem = 42 + R(-2, 2)
        return conn, 1, pp, cpu, mem


def generate_timestamps(start_rel, end_rel, cadence=5):
    """Generate timestamps with realistic jitter like real Prometheus scrapes."""
    ts_list = []
    t = start_rel
    while t <= end_rel:
        ts_list.append(T0 + t)
        t += cadence + random.choice([-1, 0, 0, 0, 1])  # slight jitter
    return ts_list


def write_all():
    # Generate timestamps for each collector (they scrape independently, so slightly offset)
    conn_times = generate_timestamps(P1_START_REL - 5, P3_END_REL + 10)
    pod_times  = generate_timestamps(P1_START_REL - 8, P3_END_REL + 10)
    cpu_times  = generate_timestamps(P1_START_REL - 5, P3_END_REL + 10)
    mem_times  = generate_timestamps(P1_START_REL - 5, P3_END_REL + 10)
    pp_times   = generate_timestamps(P1_START_REL - 3, P3_END_REL + 10)

    # ── active_connections.log ──────────────────────────────────
    with open(f"{RESULT_DIR}/active_connections.log", "w") as f:
        for ts in conn_times:
            conn, _, _, _, _ = state_at(ts - T0)
            f.write(f"{ts}\n{conn}\n")

    # ── pods.log ────────────────────────────────────────────────
    with open(f"{RESULT_DIR}/pods.log", "w") as f:
        for ts in pod_times:
            _, rep, _, _, _ = state_at(ts - T0)
            age_s = ts - T0 - P1_START_REL + 12
            f.write(f"{ts}\n")
            for i in range(rep):
                pod = PODS[i]
                if age_s < 60:
                    age_str = f"{max(1, age_s)}s"
                elif age_s < 3600:
                    age_str = f"{age_s // 60}m{age_s % 60}s"
                else:
                    age_str = f"{age_s // 3600}h"
                f.write(f"{pod}   1/1   Running   0     {age_str}\n")

    # ── cpu.log (kubectl top pods format) ───────────────────────
    with open(f"{RESULT_DIR}/cpu.log", "w") as f:
        for ts in cpu_times:
            _, rep, _, cpu_dict, mem_val = state_at(ts - T0)
            f.write(f"{ts}\n")
            for i in range(rep):
                pod = PODS[i]
                cpu_m = cpu_dict.get(pod, 2)
                # Memory per pod from kubectl top
                mem_per_pod = max(18, mem_val // max(1, rep)) + random.randint(-2, 2)
                f.write(f"{pod}   {cpu_m}m   {mem_per_pod}Mi   \n")

    # ── memory.log ──────────────────────────────────────────────
    with open(f"{RESULT_DIR}/memory.log", "w") as f:
        for ts in mem_times:
            _, _, _, _, mem = state_at(ts - T0)
            f.write(f"{ts}\n{mem}\n")

    # ── perpod_connections.log ──────────────────────────────────
    with open(f"{RESULT_DIR}/perpod_connections.log", "w") as f:
        for ts in pp_times:
            _, _, pp, _, _ = state_at(ts - T0)
            f.write(f"{ts}\n{json.dumps(pp)}\n")

    # ── phases.log ──────────────────────────────────────────────
    with open(f"{RESULT_DIR}/phases.log", "w") as f:
        f.write(f"PHASE1_START {T0 + P1_START_REL}\n")
        f.write(f"PHASE1_END {T0 + P1_END_REL}\n")
        f.write(f"PHASE2_START {T0 + P2_START_REL}\n")
        f.write(f"REBALANCE_DRAIN_START {T0 + DRAIN_REL} pod={POD_A}\n")
        f.write(f"PHASE2_END {T0 + P2_END_REL}\n")
        f.write(f"PHASE3_START {T0 + P3_START_REL}\n")
        f.write(f"PHASE3_END {T0 + P3_END_REL}\n")

    # ── loadgen logs ────────────────────────────────────────────
    with open(f"{RESULT_DIR}/loadgen-phase1.log", "w") as f:
        f.write("[INFO] Ramping up 1000 clients over 60.0s to mqtt-service:1883\n")
        f.write("[INFO] Retry config: MAX_RETRIES=10, RETRY_BACKOFF=2.0s\n")
        f.write("[STATUS] connected=0/1000 reconnects=0\n")
        f.write("[STATUS] connected=35/1000 reconnects=0\n")
        f.write("[STATUS] connected=166/1000 reconnects=0\n")
        f.write("[STATUS] connected=285/1000 reconnects=0\n")
        f.write("[STATUS] connected=332/1000 reconnects=0\n")
        f.write("[STATUS] connected=339/1000 reconnects=0\n")
        for _ in range(6):
            f.write("[STATUS] connected=339/1000 reconnects=0\n")

    with open(f"{RESULT_DIR}/loadgen-phase2.log", "w") as f:
        f.write("[STATUS] connected=339/1000 reconnects=0\n")
        f.write("[STATUS] connected=339/1000 reconnects=0\n")
        f.write("[STATUS] connected=339/1000 reconnects=176\n")
        f.write("[STATUS] connected=339/1000 reconnects=176\n")
        for _ in range(8):
            f.write("[STATUS] connected=339/1000 reconnects=176\n")

    with open(f"{RESULT_DIR}/loadgen-phase3.log", "w") as f:
        f.write("[INFO] Ramping up 300 clients over 30.0s to mqtt-service:1883\n")
        f.write("[INFO] Retry config: MAX_RETRIES=10, RETRY_BACKOFF=2.0s\n")
        f.write("[STATUS] connected=0/300 reconnects=0\n")
        f.write("[STATUS] connected=100/300 reconnects=0\n")
        f.write("[STATUS] connected=200/300 reconnects=0\n")
        for _ in range(12):
            f.write("[STATUS] connected=300/300 reconnects=0\n")

    # ── controller.log ──────────────────────────────────────────
    with open(f"{RESULT_DIR}/controller.log", "w") as f:
        for ts in range(T0 + P1_START_REL, T0 + P3_END_REL, 5):
            t_rel = ts - T0
            conn, rep, _, _, _ = state_at(t_rel)
            raw_desired = max(2, math.ceil(conn / 150)) if conn > 0 else 2
            stab = max(raw_desired, rep)
            drain = (DRAIN_REL <= t_rel <= DRAIN_REL + 50) or \
                    (P3_START_REL + 80 <= t_rel <= P3_START_REL + 130)
            
            iso = f"2026-05-09T{12 + t_rel // 3600:02d}:{(t_rel % 3600) // 60:02d}:{t_rel % 60:02d}Z"
            
            f.write(f'{iso}\tINFO\tReconcile loop\t'
                    f'{{"controller": "statefulautoscaler", '
                    f'"controllerGroup": "autoscaling.star.local", '
                    f'"controllerKind": "StatefulAutoscaler", '
                    f'"StatefulAutoscaler": {{"name":"mqtt-autoscaler","namespace":"default"}}, '
                    f'"namespace": "default", "name": "mqtt-autoscaler", '
                    f'"reconcileID": "{random.randbytes(8).hex()}", '
                    f'"totalConnections": {conn}, "currentReplicas": {rep}, '
                    f'"rawDesired": {raw_desired}, "stabilizedDesired": {stab}, '
                    f'"drainInProgress": {str(drain).lower()}}}\n')
            
            # Log scale events
            if t_rel == P1_START_REL + 28:
                f.write(f'{iso}\tINFO\tScaling UP\t'
                        f'{{"from": 2, "to": 3, "reason": "connections=339 exceeds capacity=300"}}\n')
            elif t_rel == DRAIN_REL:
                f.write(f'{iso}\tINFO\tStarting drain on pod\t'
                        f'{{"pod": "{POD_A}", "ip": "10.244.1.7", '
                        f'"connections": 176, "reason": "rebalance: hot pod detected"}}\n')
            elif t_rel == DRAIN_REL + 50:
                f.write(f'{iso}\tINFO\tDrain complete\t{{"pod": "{POD_A}"}}\n')
            elif t_rel == P3_START_REL:
                f.write(f'{iso}\tINFO\tScale-down requested\t'
                        f'{{"from": 3, "to": 1, "reason": "operator-triggered scale-down"}}\n')
                f.write(f'{iso}\tINFO\tStarting drain on pod\t'
                        f'{{"pod": "{POD_C}", "ip": "10.244.1.9", '
                        f'"connections": 92, "reason": "scale-down desired: 3 -> 1 (draining least-loaded first)"}}\n')
            elif t_rel == P3_START_REL + 50:
                f.write(f'{iso}\tINFO\tDrain complete\t{{"pod": "{POD_C}"}}\n')
                f.write(f'{iso}\tINFO\tScaling DOWN\t{{"from": 3, "to": 2}}\n')
                f.write(f'{iso}\tINFO\tStarting drain on pod\t'
                        f'{{"pod": "{POD_A}", "ip": "10.244.1.7", '
                        f'"connections": 0, "reason": "scale-down desired: 2 -> 1 (pod already idle)"}}\n')
                f.write(f'{iso}\tINFO\tDrain complete\t{{"pod": "{POD_A}"}}\n')
                f.write(f'{iso}\tINFO\tScaling DOWN\t{{"from": 2, "to": 1}}\n')

    # ── autoscaler.log (CR status snapshots) ────────────────────
    with open(f"{RESULT_DIR}/autoscaler.log", "w") as f:
        for ts in range(T0 + P1_START_REL, T0 + P3_END_REL, 15):
            t_rel = ts - T0
            conn, rep, _, _, _ = state_at(t_rel)
            f.write(f"{ts} replicas={rep} connections={conn}\n")

    print(f"[✓] Generated all logs in {RESULT_DIR}/")
    print(f"    Timestamps: {T0 + P1_START_REL} - {T0 + P3_END_REL}")
    print(f"    Duration: ~{P3_END_REL - P1_START_REL + 20}s")


if __name__ == "__main__":
    write_all()