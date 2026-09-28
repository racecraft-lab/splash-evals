"""Hostname-checking forwarder for the grader egress gateway.

The gateway redirects restricted TCP 80/443 traffic to allowlisted IPs here. Allowlisted IPs
can be shared front ends (Cloudflare, Google), so the IP alone does not identify the site:
forward only when the TLS SNI (443) or HTTP Host (80) is itself allowlisted, to the original
destination. HTTP requests get `Connection: close` so one connection cannot switch hosts.
"""

from __future__ import annotations

import asyncio
import os
import pwd
import socket
import struct
import sys

ALLOW = frozenset(d.strip().lower() for d in os.environ.get("ALLOW", "").split(",") if d.strip())
HTTP_PORT = 8080
TLS_PORT = 8443
SO_ORIGINAL_DST = 80
MAX_HEAD_BYTES = 64 * 1024
IDLE_SECONDS = 900


def log(message: str) -> None:
    print(f"egress_proxy: {message}", flush=True)


def original_destination(writer: asyncio.StreamWriter) -> tuple[str, int]:
    sock = writer.get_extra_info("socket")
    raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    port = struct.unpack("!H", raw[2:4])[0]
    return socket.inet_ntoa(raw[4:8]), port


def server_name(hello: bytes) -> str | None:
    """Return the SNI host name from a TLS ClientHello handshake message, if present."""
    try:
        if hello[0] != 1:
            return None
        pos = 4 + 2 + 32
        pos += 1 + hello[pos]
        pos += 2 + struct.unpack("!H", hello[pos : pos + 2])[0]
        pos += 1 + hello[pos]
        end = pos + 2 + struct.unpack("!H", hello[pos : pos + 2])[0]
        pos += 2
        while pos + 4 <= end:
            kind, length = struct.unpack("!HH", hello[pos : pos + 4])
            pos += 4
            if kind == 0:
                names = hello[pos + 2 : pos + length]
                if names[:1] == b"\x00":
                    size = struct.unpack("!H", names[1:3])[0]
                    return names[3 : 3 + size].decode("ascii").lower()
                return None
            pos += length
    except (IndexError, struct.error, UnicodeDecodeError):
        return None
    return None


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await asyncio.wait_for(reader.read(65536), IDLE_SECONDS):
            writer.write(data)
            await writer.drain()
    except (TimeoutError, OSError):
        pass
    finally:
        writer.close()


async def relay(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    destination: tuple[str, int],
    first: bytes,
) -> None:
    try:
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(*destination), 30
        )
    except (TimeoutError, OSError):
        client_writer.close()
        return
    upstream_writer.write(first)
    await upstream_writer.drain()
    await asyncio.gather(
        pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer)
    )


async def handle_tls(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    destination = original_destination(writer)
    try:
        header = await asyncio.wait_for(reader.readexactly(5), 30)
        if header[0] != 22:
            raise ValueError
        body = await asyncio.wait_for(
            reader.readexactly(struct.unpack("!H", header[3:5])[0]), 30
        )
    except (TimeoutError, asyncio.IncompleteReadError, ValueError, OSError):
        writer.close()
        return
    name = server_name(body)
    if name not in ALLOW:
        log(f"deny tls sni={name} dst={destination[0]}")
        writer.close()
        return
    log(f"allow tls sni={name} dst={destination[0]}")
    await relay(reader, writer, destination, header + body)


async def handle_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    destination = original_destination(writer)
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
        writer.close()
        return
    lines = head[:-4].split(b"\r\n")
    hosts = [
        line.split(b":", 1)[1].strip() for line in lines[1:] if line.lower().startswith(b"host:")
    ]
    name = (
        hosts[0].decode("ascii", "replace").rsplit(":", 1)[0].lower() if len(hosts) == 1 else None
    )
    if name not in ALLOW:
        log(f"deny http host={name} dst={destination[0]}")
        writer.close()
        return
    log(f"allow http host={name} dst={destination[0]}")
    kept = [
        line
        for line in lines[1:]
        if not line.lower().startswith((b"connection:", b"keep-alive:", b"proxy-connection:"))
    ]
    rewritten = b"\r\n".join([lines[0], *kept, b"Connection: close"]) + b"\r\n\r\n"
    await relay(reader, writer, destination, rewritten)


async def main() -> None:
    listeners = []
    for port in (HTTP_PORT, TLS_PORT):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        listeners.append(sock)
    account = pwd.getpwnam("egressproxy")
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    http = await asyncio.start_server(handle_http, sock=listeners[0], limit=MAX_HEAD_BYTES)
    tls = await asyncio.start_server(handle_tls, sock=listeners[1])
    log(f"started allow={','.join(sorted(ALLOW))}")
    async with http, tls:
        await asyncio.gather(http.serve_forever(), tls.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 - report and exit nonzero
        print(f"egress_proxy: failed {type(exc).__name__}", file=sys.stderr, flush=True)
        sys.exit(1)
