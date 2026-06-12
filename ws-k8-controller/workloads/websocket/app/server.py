import asyncio
import os
from aiohttp import web
import websockets

ACTIVE_CONNECTIONS = 0
DRAINING = False

CPU_WORK = int(os.getenv("CPU_WORK", "0"))


async def websocket_handler(websocket):
    global ACTIVE_CONNECTIONS, DRAINING

    if DRAINING:
        await websocket.close()
        return

    ACTIVE_CONNECTIONS += 1
    try:
        async for message in websocket:
            if CPU_WORK > 0:
                for _ in range(CPU_WORK):
                    pass
            await websocket.send("ack")
    finally:
        ACTIVE_CONNECTIONS -= 1


async def start_ws_server():
    async with websockets.serve(websocket_handler, "0.0.0.0", 8765):
        await asyncio.Future()


def metrics_handler(request):
    return web.Response(text=f"active_connections {ACTIVE_CONNECTIONS}\n")


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


async def main():
    await asyncio.gather(
        start_ws_server(),
        start_metrics_server()
    )


if __name__ == "__main__":
    asyncio.run(main())