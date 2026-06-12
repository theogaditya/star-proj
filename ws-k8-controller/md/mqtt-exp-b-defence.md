# MQTT Exp-B: Results Justification & Defence Notes

---

## Final Graph

![Exp-B Plot](file:///home/aditya/cohort/startproj/ws-k8-controller/results/raw/mqtt/experiment-b-stateful/plot.png)

---

## The 3 Wins — Verified Numbers

| # | Failure (Exp A) | Fix (Exp B) | Exp A | Exp B |
|---|----------------|-------------|-------|-------|
| ❶ | Scale-up blind (CPU) | Connection-driven | t=68s | **t=38s** (44% faster) |
| ❷ | No redistribution | Drain-reconnect | 339/0/0 | **176/163/0 → 247/91/0** |
| ❸ | Violent disconnect (3→1) | Drain-gated (3→1) | **639 cliff** → storm to 837 | Max step: **2** (noise), 0 killed |

---

## Phase-by-Phase (apples-to-apples with Exp A)

### Phase 1 (t=0–309s): Scale-up
- Same broker, same 1000 clients, same 339 TCP ceiling
- **Exp A**: HPA scales at t=68s (CPU too low to trigger earlier)
- **Exp B**: Controller scales 2→3 at t=38s (`ceil(339/150)=3`)
- Per-pod: 176/163/0 (TCP sessions pinned, 3rd pod idle — same as Exp A)

### Phase 2 (t=310–550s): Redistribution
- **Exp A**: Manual scale to 3 replicas + 300 more clients → 639 total. All original 339 still pinned to pod 1.
- **Exp B**: Controller drains hot pod (176 conn). Broker sends DISCONNECT. Clients reconnect via Service. Distribution: 176/163/0 → 0/247/91.
- Total preserved: 339 throughout.

### Phase 3 (t=551–851s): **Forced 3→1 Scale-down** (identical scenario)
- **Both**: Operator triggers forced scale-down 3→1 while connections are live.
- **Exp A**: HPA kills 2 pods instantly. ~300 sessions severed. Reconnect storm → peak 837. Then loadgen deleted → 0.
- **Exp B**: Controller intercepts. Drains POD_C (92 conn, 50s smoothstep) → POD_B absorbs. Drains POD_A (0 conn, instant). Replicas 3→2→1. **All 339 connections preserved**. Max step-drop = 2 (noise). Then loadgen deleted → 0.

---

## Q&A for Reviewers

**Q: Why is the scale-down scenario the same in both experiments?**
> To provide an apples-to-apples comparison. Both experiments undergo forced 3→1 reduction with live connections. The only difference is the mechanism: HPA kills pods; our controller drains first.

**Q: Max step-drop of 2 — what does that mean?**
> During the entire drain process, the largest drop between any two consecutive Prometheus scrapes was 2 connections. This is within the jitter range of the metric (±2). Zero sessions were actually killed.

**Q: After 3→1, all 339 connections are on one pod. Isn't that overloaded?**
> Yes, pod is now at 339/150 = 226% of target density. But the controller computed the original 3-replica target correctly. The forced 3→1 is an operator-driven scenario to test graceful scale-down. In production, the controller would never scale below `ceil(339/150)=3` on its own.

**Q: Why do connections drop to 0 at the end?**
> Loadgen deletion — same as Exp A. This is the natural experiment teardown, not a controller action.

**Q: Per-pod redistribution 247/91/0 — that's not balanced. Why?**
> iptables random Service distributes new connections ~50/50, but POD_B already held 163 existing connections that didn't move. B gets 163+84=247. C gets 0+91=91. Perfect 50/50 would be physically impossible without draining all pods.

---

## Paper sections updated (final)
- **§9.2 Phase 3**: Rewrote as "mirrors MQTT-A Phase 3" — same 3→1 forced scale-down
- **Fig 21 caption**: Updated to reference forced 3→1 and step-drop of 2
- **Table 6**: Added "Scale-down scenario: Forced 3→1" row, updated max drop from 15 to 2
- **MQTT Finding**: Updated with identical forced scenario comparison
- **Conclusion**: Updated MQTT paragraph with forced 3→1, step-drop of 2
- **Replica Y-axis**: Fixed on both Fig 20 and 21 (starts at 0, not auto-scaled)
