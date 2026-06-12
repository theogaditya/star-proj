import asyncio
import websockets
import random
import sys

TARGET = sys.argv[1]
CLIENTS = int(sys.argv[2])
RECONNECT_DELAY = 2


async def client_worker(worker_id):
    """Open a WebSocket connection and hold it idle (no sends).

    This keeps active_connections high on the server while generating
    virtually zero CPU load because the server's CPU_WORK loop is only
    triggered by incoming messages.
    """
    while True:
        try:
            async with websockets.connect(TARGET, ping_interval=20, ping_timeout=60) as ws:
                # Just wait for the server to close or for a network error.
                # We never send data, so the server CPU stays near zero.
                async for _ in ws:
                    pass
        except Exception:
            await asyncio.sleep(random.uniform(0.5, RECONNECT_DELAY))


async def main():
    tasks = [client_worker(i) for i in range(CLIENTS)]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
