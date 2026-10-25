#!/usr/bin/env python3
"""Minimal root helper for nobiTrader; install root-owned mode 0755, never setuid.

Copy this file and deploy/{nobi.service,nginx.conf} to root-owned
/usr/local/libexec/nobi-privileged-helper and /usr/local/share/nobi-installer first.
Grant only these exact commands through sudoers to the unprivileged service user:
  nobi ALL=(root) NOPASSWD: /opt/nobi/scripts/nobi_privileged_helper.py apply-config *
  nobi ALL=(root) NOPASSWD: /opt/nobi/scripts/nobi_privileged_helper.py restart *
Then set NBX_INSTALL_HELPER to a root-owned wrapper that invokes sudo with this file.
The helper accepts two fixed actions and validates its tiny JSON schema itself.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path("/opt/nobi")
TEMPLATE_ROOT = Path("/usr/local/share/nobi-installer")
DOMAIN = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
SERVICE = Path("/etc/systemd/system/nobi.service")
NGINX = Path("/etc/nginx/sites-available/nobi")
ENABLED = Path("/etc/nginx/sites-enabled/nobi")


def run(argv: list[str]) -> None:
    proc = subprocess.run(argv, text=True, encoding="utf-8", capture_output=True, timeout=20, check=False,
                          env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    if proc.returncode:
        raise RuntimeError((proc.stdout + proc.stderr)[-2000:])


def atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".nobi-new")
    partial.write_text(content, encoding="utf-8")
    partial.chmod(0o644)
    os.replace(partial, path)


def apply(domain: str) -> None:
    if not DOMAIN.fullmatch(domain):
        raise ValueError("invalid domain")
    service = (TEMPLATE_ROOT / "nobi.service").read_text(encoding="utf-8").replace("/opt/nobi", str(APP_ROOT))
    nginx = (TEMPLATE_ROOT / "nginx.conf").read_text(encoding="utf-8").replace("nobi.example.ir", domain)
    old_service = SERVICE.read_bytes() if SERVICE.exists() else None
    old_nginx = NGINX.read_bytes() if NGINX.exists() else None
    try:
        atomic(SERVICE, service)
        atomic(NGINX, nginx)
        if ENABLED.is_symlink() or ENABLED.exists():
            ENABLED.unlink()
        ENABLED.symlink_to(NGINX)
        run(["/usr/bin/systemd-analyze", "verify", str(SERVICE)])
        run(["/usr/sbin/nginx", "-t"])
        run(["/usr/bin/systemctl", "daemon-reload"])
        run(["/usr/bin/systemctl", "enable", "nobi"])
        run(["/usr/bin/systemctl", "reload", "nginx"])
    except Exception:
        if old_service is None:
            SERVICE.unlink(missing_ok=True)
        else:
            SERVICE.write_bytes(old_service)
        if old_nginx is None:
            NGINX.unlink(missing_ok=True)
        else:
            NGINX.write_bytes(old_nginx)
        raise


def main() -> int:
    if os.geteuid() != 0:
        raise PermissionError("helper must run as root through constrained sudo policy")
    if len(sys.argv) != 3:
        raise ValueError("exactly action and JSON payload are required")
    action, raw = sys.argv[1:]
    payload = json.loads(raw)
    if action == "apply-config" and set(payload) == {"domain"}:
        apply(str(payload["domain"]).lower())
    elif action == "restart" and payload == {}:
        run(["/usr/bin/systemctl", "restart", "nobi"])
    else:
        raise ValueError("unsupported action or payload")
    print(json.dumps({"ok": True, "action": action}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"helper error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
