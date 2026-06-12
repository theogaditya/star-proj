"""
broker.py — Minimal MQTT broker for STAR controller MQTT experiments.

Uses pure asyncio (no external MQTT library) — handles CONNECT/CONNACK/PINGREQ/PINGRESP/DISCONNECT.
Enough for persistent-connection load generation and active_connections tracking.

Exposes:
  :1883  MQTT TCP (CONNECT/CONNACK/PING/DISCONNECT)
  :8080  /metrics  → Prometheus text: active_connections <N>, broker_draining {0|1}
  :8080  /drain    → POST; starts gradual client disconnect + stops accepting new connections
  :8080  /drain/status → GET; returns JSON with drain progress
  :8080  /ready    → GET; returns 200 when healthy, 503 when draining (for readiness probe)
  :8080  /healthz  → GET; always 200
"""
import asyncio
import logging
import struct
import json
from aiohttp import web

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mqtt-broker")

ACTIVE_CONNECTIONS = 0
DRAINING = False
DRAIN_REMAINING = 0

# Track connected client writers for gradual drain nudge
CONNECTED_CLIENTS: list[asyncio.StreamWriter] = []
CONNECTED_CLIENTS_LOCK = asyncio.Lock()

# ── MQTT packet type constants ────────────────────────────────────
CONNECT     = 0x10
CONNACK     = 0x20
PINGREQ     = 0xC0
PINGRESP    = 0xD0
DISCONNECT  = 0xE0
SUBSCRIBE   = 0x80
SUBACK      = 0x90
PUBLISH     = 0x30

# Pre-built response packets
CONNACK_PKT  = bytes([CONNACK, 0x02, 0x00, 0x00])  # Session not present, return code 0
PINGRESP_PKT = bytes([PINGRESP, 0x00])
DISCONNECT_PKT = bytes([DISCONNECT, 0x00])


async def read_remaining_length(reader: asyncio.StreamReader) -> int:
    """Decode MQTT variable-length remaining-length field."""
    multiplier = 1
    value = 0
    for _ in range(4):
        byte = (await reader.readexactly(1))[0]
        value += (byte & 0x7F) * multiplier
        multiplier *= 128
        if not (byte & 0x80):
            break
    return value


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    global ACTIVE_CONNECTIONS, DRAINING

    peer = writer.get_extra_info("peername")

    if DRAINING:
        log.info("Draining — refusing new connection from %s", peer)
        writer.close()
        return

    connected = False
    try:
        while True:
            # Read fixed header (1 byte)
            try:
                header_bytes = await asyncio.wait_for(reader.readexactly(1), timeout=120)
            except asyncio.TimeoutError:
                log.warning("Client %s timed out (no data for 120s)", peer)
                break
            except asyncio.IncompleteReadError:
                break  # Client disconnected cleanly

            pkt_type = header_bytes[0] & 0xF0

            # Read remaining length
            remaining = await read_remaining_length(reader)

            # Read payload
            payload = b""
            if remaining > 0:
                payload = await reader.readexactly(remaining)

            if pkt_type == CONNECT:
                # Parse client_id from CONNECT payload
                # Bytes 0-1: protocol name length (always 4 for MQTT)
                # Bytes 2-5: "MQTT"
                # Byte 6:    protocol level
                # Byte 7:    connect flags
                # Bytes 8-9: keepalive
                # Bytes 10-11: client_id length
                if len(payload) >= 12:
                    cid_len = struct.unpack("!H", payload[10:12])[0]
                    client_id = payload[12:12 + cid_len].decode("utf-8", errors="replace")
                else:
                    client_id = "unknown"

                writer.write(CONNACK_PKT)
                await writer.drain()
                ACTIVE_CONNECTIONS += 1
                connected = True
                async with CONNECTED_CLIENTS_LOCK:
                    CONNECTED_CLIENTS.append(writer)
                log.info("CONNECT %s from %s  (total=%d)", client_id, peer, ACTIVE_CONNECTIONS)

            elif pkt_type == PINGREQ:
                writer.write(PINGRESP_PKT)
                await writer.drain()

            elif pkt_type == DISCONNECT:
                log.info("DISCONNECT from %s", peer)
                break

            elif pkt_type == SUBSCRIBE:
                # Send SUBACK — QoS 0 for all topics
                # payload: 2-byte packet id, then topic filters
                if len(payload) >= 2:
                    pkt_id = payload[0:2]
                    # Count topic filters (each has 2-byte len + topic + 1-byte QoS)
                    idx = 2
                    return_codes = []
                    while idx < len(payload):
                        if idx + 2 > len(payload):
                            break
                        tlen = struct.unpack("!H", payload[idx:idx+2])[0]
                        idx += 2 + tlen + 1  # skip topic + QoS
                        return_codes.append(0x00)  # QoS 0 granted
                    suback_payload = pkt_id + bytes(return_codes)
                    suback_len = len(suback_payload)
                    writer.write(bytes([SUBACK, suback_len]) + suback_payload)
                    await writer.drain()

            elif pkt_type == PUBLISH:
                # Accept publish, no response needed for QoS 0
                pass

            else:
                log.debug("Unknown packet type 0x%02X from %s, ignoring", pkt_type, peer)

    except Exception as exc:
        log.debug("Connection %s closed: %s", peer, exc)
    finally:
        try:
            writer.close()
        except Exception:
            pass
        if connected:
            ACTIVE_CONNECTIONS = max(0, ACTIVE_CONNECTIONS - 1)
            async with CONNECTED_CLIENTS_LOCK:
                try:
                    CONNECTED_CLIENTS.remove(writer)
                except ValueError:
                    pass
            log.info("Client %s gone (total=%d)", peer, ACTIVE_CONNECTIONS)


# ── Gradual drain logic ──────────────────────────────────────────

async def gradual_drain():
    """Send DISCONNECT to connected clients at ~10/second for graceful rebalance."""
    global DRAIN_REMAINING
    log.info("Gradual drain started — nudging %d clients", len(CONNECTED_CLIENTS))
    while True:
        async with CONNECTED_CLIENTS_LOCK:
            if not CONNECTED_CLIENTS:
                break
            writer = CONNECTED_CLIENTS[0]  # take first; client handler will remove on close

        try:
            writer.write(DISCONNECT_PKT)
            await writer.drain()
            writer.close()
        except Exception:
            pass

        DRAIN_REMAINING = max(0, ACTIVE_CONNECTIONS)
        await asyncio.sleep(0.1)  # ~10 clients/second

    DRAIN_REMAINING = 0
    log.info("Gradual drain complete — all clients nudged off")


# ── HTTP metrics / control server ────────────────────────────────

async def metrics_handler(request):
    text = (
        "# HELP active_connections Number of active MQTT connections\n"
        "# TYPE active_connections gauge\n"
        f"active_connections {ACTIVE_CONNECTIONS}\n"
        "# HELP broker_draining Whether the broker is in drain mode\n"
        "# TYPE broker_draining gauge\n"
        f"broker_draining {1 if DRAINING else 0}\n"
    )
    return web.Response(text=text, content_type="text/plain; version=0.0.4")


async def drain_handler(request):
    global DRAINING, DRAIN_REMAINING
    DRAINING = True
    DRAIN_REMAINING = ACTIVE_CONNECTIONS
    log.info("Drain requested — rejecting new connections and nudging %d existing clients", ACTIVE_CONNECTIONS)
    # Start gradual drain in background
    asyncio.create_task(gradual_drain())
    return web.Response(text="draining\n")


async def drain_status_handler(request):
    body = json.dumps({
        "draining": DRAINING,
        "remaining": ACTIVE_CONNECTIONS,
        "total_at_start": DRAIN_REMAINING,
    })
    return web.Response(text=body + "\n", content_type="application/json")


async def ready_handler(request):
    """Readiness probe: 503 when draining so K8s Service stops routing new connections here."""
    if DRAINING:
        return web.Response(text="draining\n", status=503)
    return web.Response(text="ok\n")


async def healthz_handler(request):
    return web.Response(text="ok\n")


async def start_http_server():
    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_post("/drain", drain_handler)
    app.router.add_get("/drain/status", drain_status_handler)
    app.router.add_get("/ready", ready_handler)
    app.router.add_get("/healthz", healthz_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    log.info("HTTP server on :8080 (/metrics, /drain, /drain/status, /ready, /healthz)")


# ── Entry point ───────────────────────────────────────────────────

async def main():
    mqtt_server = await asyncio.start_server(handle_client, "0.0.0.0", 1883)
    log.info("MQTT broker listening on :1883")
    await start_http_server()
    async with mqtt_server:
        await mqtt_server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
