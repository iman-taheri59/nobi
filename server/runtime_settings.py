"""Non-secret settings schema and read-only view of the running process.

No application imports, directory creation, file reads or environment mutation.
The env loader and the web editor share this allowlist and serialization rules.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATH_KEYS = {
    "NBX_JOURNAL_DB", "NBX_AUTH_DB", "NBX_LIVE_DB", "NBX_CANDLE_CACHE",
    "NBX_BT_DIR", "NBX_EXPORT_DIR",
}
INT_RANGES = {
    "NBX_SCAN_LOOP_S": (0, 86400), "NBX_GATE_PREFETCH": (0, 20),
    "NBX_BOOK_TTL_S": (10, 3600), "NBX_BOOK_GATE_TTL_S": (10, 7200),
    "NBX_BOOK_KEEP_S": (60, 86400),
}
CHOICES = {
    "NBX_SCAN_LOOP_TF": {"15m", "1h", "4h", "D"},
    "NBX_LIVE_MODE": {"disabled", "dry-run"},
}
ALLOWED = PATH_KEYS | set(INT_RANGES) | set(CHOICES)
DEFAULTS = {
    "NBX_JOURNAL_DB": str(ROOT / "server/journal.db"),
    "NBX_AUTH_DB": str(ROOT / "server/auth.db"),
    "NBX_LIVE_DB": str(ROOT / "server/live_execution.db"),
    "NBX_CANDLE_CACHE": str(ROOT / "server/candle_cache"),
    "NBX_BT_DIR": str(ROOT / "server/backtest_reports"),
    "NBX_EXPORT_DIR": str(ROOT / "server/exports"),
    "NBX_SCAN_LOOP_S": "0", "NBX_SCAN_LOOP_TF": "15m", "NBX_GATE_PREFETCH": "10",
    "NBX_BOOK_TTL_S": "120", "NBX_BOOK_GATE_TTL_S": "300", "NBX_BOOK_KEEP_S": "3600",
    "NBX_LIVE_MODE": "disabled",
}


def clean_values(values: dict[str, Any]) -> dict[str, str]:
    """Validate the whole request before any filesystem effect. Never echo values."""
    if set(values) - ALLOWED:
        raise ValueError("unsupported setting(s)")
    clean: dict[str, str] = {}
    for key, value in values.items():
        raw = str(value)
        if any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in "\u2028\u2029" for c in raw):
            raise ValueError(f"{key} contains a control character")
        val = raw.strip()
        if key in PATH_KEYS:
            path = Path(val).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{key} must be an absolute path")
            val = str(path)
        elif key in INT_RANGES:
            try:
                number = int(val)
            except ValueError:
                raise ValueError(f"{key} must be an integer") from None
            low, high = INT_RANGES[key]
            if not low <= number <= high or (key == "NBX_SCAN_LOOP_S" and 0 < number < 60):
                raise ValueError(f"{key} is outside the supported range")
            val = str(number)
        elif val not in CHOICES[key]:
            raise ValueError(f"{key} has an unsupported value")
        clean[key] = val
    return clean


def parse_managed(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in content.split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition("=")
        if not sep or key not in ALLOWED or key in values:
            raise ValueError("invalid, duplicate or unsupported managed setting")
        val = val.strip()
        if val.startswith('"'):
            try:
                val = json.loads(val)
            except (ValueError, TypeError):
                raise ValueError("invalid quoted managed setting") from None
            if not isinstance(val, str):
                raise ValueError("managed value must be a string")
        elif len(val) >= 2 and val[0] == val[-1] == "'":
            val = val[1:-1]
        else:
            val = val.split(" #", 1)[0].rstrip()
        values[key] = val
    return clean_values(values)


def serialize_managed(values: dict[str, Any]) -> str:
    clean = clean_values(values)
    lines = ["# Managed by the nobitrader owner installation wizard. No secrets."]
    for key, val in sorted(clean.items()):
        # Quote only when needed; simple legacy KEY=value remains compatible.
        if any(c in val for c in "#\"'\\"):
            val = "'" + val + "'"  # literal wrapper shared by legacy CLI parsers
        lines.append(f"{key}={val}")
    return "\n".join(lines) + "\n"


def effective_values() -> dict[str, str]:
    """Use already-loaded runtime objects over env; do not import/start them here."""
    values = {key: os.getenv(key) or default for key, default in DEFAULTS.items()}
    bindings = {
        "NBX_AUTH_DB": ("auth", "AUTH_DB"), "NBX_JOURNAL_DB": ("journal", "DB_PATH"),
        "NBX_CANDLE_CACHE": ("candle_cache", "CACHE_ROOT"),
        "NBX_BT_DIR": ("backtest", "REPORTS"), "NBX_EXPORT_DIR": ("main", "EXPORT_DIR"),
        "NBX_BOOK_TTL_S": ("main", "BOOK_PREFETCH_TTL_S"),
        "NBX_BOOK_GATE_TTL_S": ("main", "BOOK_GATE_TTL_S"),
        "NBX_BOOK_KEEP_S": ("main", "BOOK_EVICT_TTL_S"),
    }
    for key, (module, attribute) in bindings.items():
        value = getattr(sys.modules.get(module), attribute, None)
        if value is not None:
            values[key] = format(value, "g") if isinstance(value, float) else str(value)
    client = getattr(sys.modules.get("live_api"), "CLIENT", None)
    if client is not None:
        values["NBX_LIVE_DB"] = str(client.ledger.path)
        values["NBX_LIVE_MODE"] = client.mode.value
    return values


def describe(managed: dict[str, str]) -> dict[str, dict[str, Any]]:
    effective = effective_values()
    origins = getattr(sys.modules.get("_env"), "SOURCES", {})
    result: dict[str, dict[str, Any]] = {}
    for key in sorted(ALLOWED):
        configured = os.getenv(key) or DEFAULTS[key]
        origin = origins.get(key, "process_environment") if os.getenv(key) else "default"
        result[key] = {
            "effective": effective[key], "managed": managed.get(key), "default": DEFAULTS[key],
            "source": origin if effective[key] == configured else "runtime-resolved",
            "configured_source": origin,
            "pending_or_overridden": key in managed and managed[key] != effective[key],
        }
    return result
