import asyncio
import os
from aiohttp import web
import websockets
from prometheus_client import Gauge, Counter, generate_latest
# ------------------------------------------------
# Prometheus Metrics
# ------------------------------------------------

ACTIVE_CONNECTIONS = Gauge(
    "active_connections",
    "Current number of active WebSocket connections"
)

NEW_CONNECTIONS = Counter(
    "new_connections_total",
    "Total number of WebSocket connections established"
)

DRAINING = False
CPU_WORK = int(os.getenv("CPU_WORK", "0"))


# ------------------------------------------------
# WebSocket Handler
# ------------------------------------------------

async def websocket_handler(websocket):
    global DRAINING

    if DRAINING:
        await websocket.close()
        return

    ACTIVE_CONNECTIONS.inc()
    NEW_CONNECTIONS.inc()

    try:
        async for message in websocket:
            if CPU_WORK > 0:
                for _ in range(CPU_WORK):
                    pass
            await websocket.send("ack")
    finally:
        ACTIVE_CONNECTIONS.dec()


async def start_ws_server():
    async with websockets.serve(websocket_handler, "0.0.0.0", 8765):
        await asyncio.Future()


# ------------------------------------------------
# Metrics Endpoint
# ------------------------------------------------

async def metrics_handler(request):
    return web.Response(
        body=generate_latest(),
        content_type="text/plain"
    )

def drain_handler(request):
    global DRAINING
    DRAINING = True
    return web.Response(text="Draining enabled\n")


async def start_metrics_server():
    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_post("/drain", drain_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()


# ------------------------------------------------
# Main
# ------------------------------------------------

async def main():
    await asyncio.gather(
        start_ws_server(),
        start_metrics_server()
    )


if __name__ == "__main__":
    asyncio.run(main())