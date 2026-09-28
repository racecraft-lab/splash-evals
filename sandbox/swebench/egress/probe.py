"""Fail-closed egress probe, run inside a gateway's network namespace before a networked eval.

Exit 0 only if every check passes. Lines starting INFO record observations that are not gates.
"""

from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import time

DNS = "127.0.0.1"
ALLOW = [d for d in os.environ.get("ALLOW", "").split(",") if d]
EXPECT_UID = int(os.environ["EXPECT_UID"])
FOREIGN = "example.com"
BLOCKED_NAMES = [FOREIGN, "pypi.org", "host.docker.internal", "gateway.docker.internal"]
# Must time out (silent drop), matching upstream's unroutable-address tests.
DROPPED = [
    ("1.1.1.1", 443),
    ("8.8.8.8", 53),
    ("10.255.255.1", 80),
    ("192.168.65.254", 1234),
    ("192.168.65.254", 80),
]
DROPPED += [
    (t.rsplit(":", 1)[0], int(t.rsplit(":", 1)[1]))
    for t in os.environ.get("EXTRA_DROPPED", "").split(",")
    if t
]
LOCAL_REFUSED = [("127.0.0.11", 53), ("127.0.0.1", 80)]
failed = False


def report(ok: bool, what: str) -> None:
    global failed
    failed |= not ok
    print(("PASS " if ok else "FAIL ") + what, flush=True)


def resolve(name: str, server: str = DNS) -> list[str]:
    out = subprocess.run(  # noqa: S603 - fixed resolver argv; names are probe constants
        ["/usr/bin/dig", "+short", "+time=2", "+tries=1", f"@{server}", name, "A"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    return [a for a in out if a.count(".") == 3 and a.replace(".", "").isdigit()]


def connect(host: str, port: int, timeout: float) -> str:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return "connected"
    except TimeoutError:
        return "timeout"
    except OSError as exc:
        return type(exc).__name__
    finally:
        s.close()


def tls_first_line(ip: str, sni: str, host: str) -> str:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        with socket.create_connection((ip, 443), timeout=10) as raw:
            with context.wrap_socket(raw, server_hostname=sni) as tls:
                request = f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                tls.sendall(request.encode())
                line = tls.recv(128).split(b"\r\n")[0].decode(errors="replace")
                return line or "closed"
    except (OSError, ssl.SSLError) as exc:
        return f"error {type(exc).__name__}"


def http_first_line(ip: str, host: str) -> str:
    try:
        with socket.create_connection((ip, 80), timeout=10) as raw:
            raw.sendall(f"HEAD / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
            line = raw.recv(128).split(b"\r\n")[0].decode(errors="replace")
            return line or "closed"
    except OSError as exc:
        return f"error {type(exc).__name__}"


report(os.getuid() == EXPECT_UID, f"running as uid {os.getuid()}")
with open("/etc/resolv.conf", "rb") as handle:
    report(handle.read() == b"nameserver 127.0.0.1\n", "resolv.conf points only at the gateway")
for name in ALLOW:
    addrs = resolve(name)
    if not addrs:
        report(False, f"allowed {name} unresolved")
        continue
    ip = addrs[0]
    line = tls_first_line(ip, name, name)
    report(line.startswith("HTTP/"), f"allowed {name} -> {ip} tls {line}")
    line = tls_first_line(ip, FOREIGN, FOREIGN)
    report(not line.startswith("HTTP/"), f"foreign sni via {name} ip {ip}: {line}")
    line = http_first_line(ip, FOREIGN)
    report(not line.startswith("HTTP/"), f"foreign host via {name} ip {ip}: {line}")
    fronted = tls_first_line(ip, name, FOREIGN)
    print(f"INFO fronting sni={name} host={FOREIGN} ip={ip}: {fronted}", flush=True)
for name in BLOCKED_NAMES:
    if name in ALLOW:
        continue
    addrs = resolve(name)
    report(not addrs, f"blocked name {name} -> {addrs}")
report(not resolve(FOREIGN, "127.0.0.11"), "docker embedded DNS unreachable")
for host, port in DROPPED:
    start = time.time()
    status = connect(host, port, 3)
    report(status == "timeout", f"dropped {host}:{port} {status} {time.time() - start:.1f}s")
for host, port in LOCAL_REFUSED:
    status = connect(host, port, 3)
    report(status != "connected", f"local {host}:{port} {status}")
sys.exit(1 if failed else 0)
