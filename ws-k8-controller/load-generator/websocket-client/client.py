import asyncio
import os
import sys
import time
import numpy as np
import websockets

TARGET = sys.argv[1]
CLIENTS = int(sys.argv[2])
ACTIVE_DURATION = int(sys.argv[3]) if len(sys.argv) > 3 else 0

# RAMP_UP_DURATION=0 → instant spike (all clients connect simultaneously).
# Set via env var for failure-scenario-2 without changing the YAML.
RAMP_UP_DURATION = int(os.environ.get("RAMP_UP_DURATION", "90"))

GLOBAL_START_TIME = time.time()

# Shared RTT samples list — appended by all client coroutines.
_rtt_samples: list[float] = []


async def client_worker(client_index: int) -> None:
    # Linear stagger for each client to ramp up smoothly.
    # RAMP_UP_DURATION=0 means all connect at once (instant spike scenario).
    if RAMP_UP_DURATION > 0:
        delay = (client_index / CLIENTS) * RAMP_UP_DURATION
        await asyncio.sleep(delay)

    while True:
        elapsed = time.time() - GLOBAL_START_TIME
        if ACTIVE_DURATION > 0 and elapsed > ACTIVE_DURATION:
            # Disconnected during IDLE phase — give up permanently.
            return

        try:
            async with websockets.connect(TARGET, ping_interval=None, ping_timeout=None) as ws:
                while True:
                    elapsed = time.time() - GLOBAL_START_TIME
                    if ACTIVE_DURATION > 0 and elapsed > ACTIVE_DURATION:
                        # --- IDLE PHASE ---
                        # Stop sending pings, keep connection open by consuming messages.
                        async for _ in ws:
                            pass
                        # If the server closes during IDLE, record permanent connection loss.
                        return

                    # --- ACTIVE PHASE: send ping and measure RTT ---
                    t0 = time.time()
                    await ws.send("ping")
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=10.0)
                        rtt = time.time() - t0
                        _rtt_samples.append(rtt)
                    except asyncio.TimeoutError:
                        pass  # skip RTT sample on timeout

                    await asyncio.sleep(5)

        except Exception:
            elapsed = time.time() - GLOBAL_START_TIME
            if ACTIVE_DURATION > 0 and elapsed > ACTIVE_DURATION:
                # Do not reconnect during IDLE phase.
                return
            await asyncio.sleep(1)


async def main() -> None:
    tasks = [client_worker(i) for i in range(CLIENTS)]
    await asyncio.gather(*tasks)


def _print_rtt_summary() -> None:
    """Print a machine-parseable RTT summary line to stdout."""
    s = _rtt_samples
    if not s:
        print("RTT_SUMMARY count=0 p50=nan p95=nan mean=nan")
        return
    arr = np.array(s) * 1000  # convert to milliseconds
    print(
        f"RTT_SUMMARY count={len(arr)} "
        f"mean={np.mean(arr):.2f}ms "
        f"p50={np.percentile(arr, 50):.2f}ms "
        f"p95={np.percentile(arr, 95):.2f}ms "
        f"p99={np.percentile(arr, 99):.2f}ms "
        f"min={np.min(arr):.2f}ms "
        f"max={np.max(arr):.2f}ms"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        _print_rtt_summary()