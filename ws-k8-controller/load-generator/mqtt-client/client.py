"""
client.py — MQTT persistent load generator.

Environment variables:
  BROKER_HOST   default: mqtt-service
  BROKER_PORT   default: 1883
  CLIENTS       default: 600    total persistent clients
  PING_INTERVAL default: 30     seconds between keepalive publishes
  RAMP_SECONDS  default: 60     seconds over which to ramp up connections
  MAX_RETRIES   default: 10     max reconnect attempts per client
  RETRY_BACKOFF default: 2.0    base backoff seconds (exponential)
"""
import asyncio
import os
import random
import string
import time

import paho.mqtt.client as mqtt_client

BROKER_HOST   = os.getenv("BROKER_HOST", "mqtt-service")
BROKER_PORT   = int(os.getenv("BROKER_PORT", "1883"))
CLIENTS       = int(os.getenv("CLIENTS", "600"))
PING_INTERVAL = float(os.getenv("PING_INTERVAL", "30"))
RAMP_SECONDS  = float(os.getenv("RAMP_SECONDS", "60"))
MAX_RETRIES   = int(os.getenv("MAX_RETRIES", "10"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "2.0"))

connected_count = 0
reconnect_count = 0
stop_event = asyncio.Event()


def _random_id(length=10) -> str:
    return "loadgen-" + "".join(random.choices(string.ascii_lowercase, k=length))


async def run_client(client_id: str, delay: float):
    """Connect one MQTT client with retry logic, subscribe, and ping periodically."""
    global connected_count, reconnect_count

    await asyncio.sleep(delay)

    attempt = 0
    while not stop_event.is_set():
        loop = asyncio.get_event_loop()

        client = mqtt_client.Client(client_id=client_id, clean_session=True)
        client.connect_async(BROKER_HOST, BROKER_PORT, keepalive=60)
        client.loop_start()

        # Wait for connection
        deadline = time.monotonic() + 30
        while not client.is_connected() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

        if not client.is_connected():
            client.loop_stop()
            attempt += 1
            if attempt > MAX_RETRIES:
                print(f"[WARN] {client_id} exhausted {MAX_RETRIES} retries, giving up", flush=True)
                return
            backoff = RETRY_BACKOFF * (2 ** min(attempt, 5)) + random.uniform(0, 1)
            backoff = min(backoff, 30.0)
            print(f"[RETRY] {client_id} attempt {attempt}/{MAX_RETRIES}, backoff {backoff:.1f}s", flush=True)
            await asyncio.sleep(backoff)
            continue

        # Successfully connected
        if attempt > 0:
            reconnect_count += 1
            print(f"[RECONN] {client_id} reconnected after {attempt} retries", flush=True)
        connected_count += 1
        attempt = 0  # reset attempt counter on success
        client.subscribe(f"loadgen/{client_id}")

        try:
            while not stop_event.is_set():
                if not client.is_connected():
                    # Connection dropped (server sent DISCONNECT or TCP reset)
                    print(f"[DROP] {client_id} connection lost, will retry", flush=True)
                    break
                client.publish(f"loadgen/{client_id}/ping", "ping", qos=0)
                await asyncio.sleep(PING_INTERVAL)
        finally:
            client.loop_stop()
            try:
                client.disconnect()
            except Exception:
                pass
            connected_count = max(0, connected_count - 1)

        if stop_event.is_set():
            print(f"[INFO] {client_id} stopped. Total: {connected_count}", flush=True)
            return

        # Connection was lost — retry
        attempt += 1
        if attempt > MAX_RETRIES:
            print(f"[WARN] {client_id} exhausted {MAX_RETRIES} retries after drop, giving up", flush=True)
            return
        backoff = RETRY_BACKOFF * (2 ** min(attempt, 5)) + random.uniform(0, 1)
        backoff = min(backoff, 30.0)
        reconnect_count += 1
        print(f"[RETRY] {client_id} reconnecting after drop, attempt {attempt}, backoff {backoff:.1f}s", flush=True)
        await asyncio.sleep(backoff)


async def main():
    print(f"[INFO] Ramping up {CLIENTS} clients over {RAMP_SECONDS}s to {BROKER_HOST}:{BROKER_PORT}", flush=True)
    print(f"[INFO] Retry config: MAX_RETRIES={MAX_RETRIES}, RETRY_BACKOFF={RETRY_BACKOFF}s", flush=True)

    delay_step = RAMP_SECONDS / CLIENTS
    tasks = []
    for i in range(CLIENTS):
        cid = _random_id()
        tasks.append(asyncio.create_task(run_client(cid, delay=i * delay_step)))

    # Print status every 10 s
    async def status_loop():
        while not stop_event.is_set():
            print(f"[STATUS] connected={connected_count}/{CLIENTS} reconnects={reconnect_count}", flush=True)
            await asyncio.sleep(10)

    tasks.append(asyncio.create_task(status_loop()))

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
