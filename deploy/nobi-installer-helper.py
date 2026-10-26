#!/usr/bin/env python3
"""Root-owned, socket-activated broker for the browser installer.

Why a socket and not ``sudo``
-----------------------------
``deploy/nobi.service`` is hardened with ``NoNewPrivileges=yes`` and
``ProtectSystem=strict``. Both are good ideas, and both break ``sudo``: the
first makes the setuid bit inert, the second makes /etc read-only in the
service's mount namespace. A root helper reached through sudo therefore cannot
work on the very unit the project ships.

This daemon runs as its own systemd service (root, its own mount namespace) and
is started on demand by ``nobi-installer.socket``. The web process only opens a
Unix socket whose group is ``nobi`` — no setuid binary, no privilege transition,
no escalation path from the web process. It still accepts *only* an action id
from a fixed allowlist and forwards it to the root-owned CLI helper. No path,
domain, shell text or arbitrary argv is accepted.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

PROTOCOL = 1
CLI = Path("/usr/local/libexec/nobi-privileged-helper")
DEFAULT_SOCKET = Path("/run/nobi-installer.sock")
ACTIONS = frozenset({
    "install-service", "install-proxy", "restart-service", "reload-proxy",
    "rollback", "check",
})
MAX_REQUEST = 4096
TIMEOUT_S = 100


def handle(raw: bytes) -> dict:
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid JSON"}
    if not isinstance(request, dict):
        return {"ok": False, "error": "request must be an object"}
    if request.get("protocol") != PROTOCOL:
        return {"ok": False, "error": "protocol mismatch"}
    action = request.get("action")
    if action not in ACTIONS or set(request) != {"protocol", "action"}:
        return {"ok": False, "error": "unsupported action or extra fields"}
    if os.geteuid() != 0:
        return {"ok": False, "error": "helper service must run as root"}
    try:
        proc = subprocess.run(
            [sys.executable, str(CLI), "--protocol", str(PROTOCOL), action],
            capture_output=True, text=True, encoding="utf-8",
            timeout=TIMEOUT_S, check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "action": action, "error": type(exc).__name__}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    # The CLI helper prints a JSON object with the real reason. Flatten it so
    # the wizard/operations card can show "managed source is group/world
    # writable" instead of a useless "helper exited non-zero".
    try:
        cli_reply = json.loads(out)
        reason = cli_reply.get("error") if isinstance(cli_reply, dict) else None
        if reason is None and isinstance(cli_reply, dict) and cli_reply.get("ok") is False:
            # `check` reports failures as per-action fields, not a single
            # `error`. Surface the first one so preflight says *what* is wrong.
            reason = next(
                (f"{key}: {value}" for key, value in cli_reply.items()
                 if key not in {"ok", "protocol", "actions"} and value != "ready"),
                None,
            )
    except json.JSONDecodeError:
        reason = None
    # systemctl writes "Created symlink ..." to stderr on success. Calling that
    # an `error` made every successful install look like a failure in the UI.
    return {"ok": proc.returncode == 0, "action": action,
            "output": out[-4000:], "stderr": err[-1000:] or None,
            "error": (reason or err[-1000:] or "helper exited non-zero")
                     if proc.returncode else None,
            "exit_code": proc.returncode}


def serve_connection(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(10)
        chunks = bytearray()
        while len(chunks) < MAX_REQUEST:
            part = conn.recv(MAX_REQUEST - len(chunks))
            if not part:
                break
            chunks.extend(part)
            if b"\n" in part or len(part) < 4096:
                break
        try:
            payload = json.dumps(handle(bytes(chunks))).encode("utf-8")
        except Exception as exc:  # never let a bad request kill the daemon
            payload = json.dumps({"ok": False, "error": type(exc).__name__}).encode()
        conn.sendall(payload + b"\n")


def systemd_socket() -> socket.socket | None:
    """Reuse fd 3 when started by a .socket unit (LISTEN_FDS/LISTEN_PID)."""
    if os.getenv("LISTEN_PID") != str(os.getpid()) or os.getenv("LISTEN_FDS") != "1":
        return None
    return socket.socket(fileno=3)


def main() -> int:
    arg_socket = None
    if len(sys.argv) == 3 and sys.argv[1] == "--socket":
        arg_socket = Path(sys.argv[2])
    elif len(sys.argv) != 1:
        print("usage: nobi-installer-helper.py [--socket PATH]", file=sys.stderr)
        return 2
    listener = systemd_socket()
    if listener is None:
        path = arg_socket or DEFAULT_SOCKET
        if path.exists():
            path.unlink()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        os.chmod(path, 0o660)
    listener.listen(8)
    try:
        while True:
            conn, _ = listener.accept()
            serve_connection(conn)
    except KeyboardInterrupt:
        return 0
    finally:
        listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
