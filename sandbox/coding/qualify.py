#!/usr/bin/env python3
"""Adversarial synthetic qualification probes executed only inside Docker."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

CANARY = Path("/tmp/racecraft-sandbox-canary")  # noqa: S108 - container tmpfs.


def _unreachable(address: tuple[object, ...], family: socket.AddressFamily) -> bool:
    connection = socket.socket(family, socket.SOCK_STREAM)
    connection.settimeout(0.2)
    try:
        connection.connect(address)
    except OSError:
        return True
    finally:
        connection.close()
    return False


def _dns_unreachable() -> bool:
    try:
        socket.getaddrinfo("example.com", 443)
    except socket.gaierror:
        return True
    return False


def _host_gateway_unreachable() -> bool:
    try:
        socket.getaddrinfo("host.docker.internal", 1)
    except socket.gaierror:
        return True
    return False


def _path_unobservable(path: str) -> bool:
    try:
        return not Path(path).exists()
    except OSError:
        return True


def _paths_absent(paths: tuple[str, ...]) -> bool:
    return all(_path_unobservable(path) for path in paths)


def _secrets_absent() -> bool:
    sensitive_names = {
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "HF_TOKEN",
        "OPENAI_API_KEY",
    }
    sensitive_environment = any(
        key in sensitive_names
        or key.endswith("_PASSWORD")
        or key.endswith("_SECRET")
        or key.endswith("_TOKEN")
        for key in os.environ
    )
    return not sensitive_environment and _paths_absent(("/run/secrets", "/run/credentials"))


def _capabilities_empty() -> bool:
    try:
        status = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return False
    values = [
        line.split(":", 1)[1].strip() for line in status.splitlines() if line.startswith("CapEff:")
    ]
    return values == ["0000000000000000"]


def _write_refused(path: Path) -> bool:
    try:
        path.write_text("forbidden", encoding="utf-8")
    except OSError:
        return True
    path.unlink(missing_ok=True)
    return False


def _bounded_cgroup(path: Path) -> bool:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return value.isdigit() and int(value) > 0


def _loop_bounded() -> bool:
    try:
        subprocess.run(  # noqa: S603 - fixed interpreter and fixed synthetic code.
            [sys.executable, "-I", "-B", "-c", "while True: pass"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=0.2,
        )
    except subprocess.TimeoutExpired:
        return True
    return False


def _symlink_escape_refused() -> bool:
    directory = Path(tempfile.mkdtemp(prefix="racecraft-qualify-", dir="/tmp"))  # noqa: S108
    link = directory / "escape"
    try:
        link.symlink_to("/etc")
        return _write_refused(link / "racecraft-symlink-write")
    finally:
        link.unlink(missing_ok=True)
        directory.rmdir()


def _run_boundary_probes(host_sentinel: str) -> int:
    probes: tuple[tuple[str, Callable[[], bool]], ...] = (
        ("dns_unreachable", _dns_unreachable),
        ("ipv4_unreachable", lambda: _unreachable(("1.1.1.1", 53), socket.AF_INET)),
        (
            "ipv6_unreachable",
            lambda: _unreachable(("2606:4700:4700::1111", 53, 0, 0), socket.AF_INET6),
        ),
        ("host_gateway_unreachable", _host_gateway_unreachable),
        (
            "host_paths_absent",
            lambda: _paths_absent(
                ("/Users/host-user", "/home/host-user", "/host-sentinel-racecraft", "/root/.ssh")
                + (host_sentinel,)
            ),
        ),
        ("secrets_absent", _secrets_absent),
        ("docker_socket_absent", lambda: not Path("/var/run/docker.sock").exists()),
        ("non_root", lambda: os.geteuid() != 0 and os.getegid() != 0),
        ("capabilities_empty", _capabilities_empty),
        ("image_write_refused", lambda: _write_refused(Path("/opt/racecraft/grade.py"))),
        ("etc_write_refused", lambda: _write_refused(Path("/etc/racecraft-write"))),
        ("input_write_refused", lambda: _write_refused(Path("/work/input"))),
        ("pids_bounded", lambda: _bounded_cgroup(Path("/sys/fs/cgroup/pids.max"))),
        ("memory_bounded", lambda: _bounded_cgroup(Path("/sys/fs/cgroup/memory.max"))),
        ("loop_bounded", _loop_bounded),
        (
            "traversal_refused",
            lambda: _write_refused(Path("/tmp/../etc/racecraft-traversal-write")),  # noqa: S108
        ),
        ("symlink_escape_refused", _symlink_escape_refused),
    )
    checks = [{"name": name, "passed": probe()} for name, probe in probes]
    passed = all(check["passed"] is True for check in checks)
    json.dump(
        {"schema_version": 1, "passed": passed, "checks": checks},
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0 if passed else 1


def _simple(probe: str, passed: bool) -> int:
    json.dump(
        {"schema_version": 1, "passed": passed, "probe": probe},
        sys.stdout,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--write-canary", action="store_true")
    parser.add_argument("--assert-canary-absent", action="store_true")
    parser.add_argument("--hang", action="store_true")
    parser.add_argument("--flood", type=int)
    parser.add_argument("--host-sentinel")
    arguments = parser.parse_args()
    selected = sum(
        (
            arguments.hold,
            arguments.write_canary,
            arguments.assert_canary_absent,
            arguments.hang,
            arguments.flood is not None,
        )
    )
    if selected > 1:
        return 2
    if arguments.hold:
        time.sleep(300)
        return 0
    if arguments.write_canary:
        CANARY.write_text("canary", encoding="utf-8")
        return _simple("canary_written", CANARY.is_file())
    if arguments.assert_canary_absent:
        return _simple("canary_absent", not CANARY.exists())
    if arguments.hang:
        while True:
            time.sleep(1)
    if arguments.flood is not None:
        if not 0 < arguments.flood <= 1_048_576:
            return 2
        sys.stdout.write("x" * arguments.flood)
        return 0
    if not arguments.host_sentinel or not Path(arguments.host_sentinel).is_absolute():
        return 2
    return _run_boundary_probes(arguments.host_sentinel)


if __name__ == "__main__":
    raise SystemExit(main())
