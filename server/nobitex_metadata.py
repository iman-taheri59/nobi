"""Authoritative exchange metadata from /v2/options, separate from logo metadata.

Cached options are display-only: the trading engine still applies only a fresh
response via units.apply_options. No rule, network or name is invented locally.
"""
from __future__ import annotations

import copy
import json
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import coin_metadata
from coin_metadata import MAX_JSON, atomic_write

TTL = 3600
RETRY = 300
SYMBOL = re.compile(r"[A-Z0-9]{1,24}")
TEXT_FIELDS = ("name", "stdName", "defaultNetwork")
NETWORK_TEXT = ("network", "name", "addressRegex", "memoRegex")
NETWORK_BOOL = ("isDefault", "beta", "depositEnable", "withdrawEnable", "memoRequired")
NETWORK_NUMBER = ("minConfirm", "withdrawIntegerMultiple", "withdrawFee", "withdrawMin", "withdrawMax")
RULES = ("minOrders", "amountPrecisions", "pricePrecisions")


def numeric(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    if not text or len(text) > 64:
        return None
    try:
        number = Decimal(text)
        return text if number.is_finite() and number >= 0 else None
    except InvalidOperation:
        return None


def symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    upper = value.upper()
    return upper if SYMBOL.fullmatch(upper) else None


def clean_options(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or body.get("status") != "ok":
        raise ValueError("unsuccessful options response")
    nb = body.get("nobitex")
    nb = nb if isinstance(nb, dict) else {}
    cleaned: dict[str, Any] = {"status": "ok", "coins": [], "nobitex": {}}
    source_coins = body.get("coins", [])
    if not isinstance(source_coins, list) or len(source_coins) > 2000:
        raise ValueError("invalid coin list")
    seen = set()
    for raw in source_coins:
        if not isinstance(raw, dict):
            continue
        base = symbol(raw.get("coin"))
        if not base:
            continue
        if base in seen:
            raise ValueError("duplicate exchange symbol")
        seen.add(base)
        row: dict[str, Any] = {"coin": base}
        for field in TEXT_FIELDS:
            text = raw.get(field)
            if isinstance(text, str) and text.strip() and len(text) <= 160:
                row[field] = text.strip()
        precision = numeric(raw.get("displayPrecision"))
        if precision is not None:
            row["displayPrecision"] = precision
        networks = raw.get("networkList")
        if isinstance(networks, dict) and len(networks) <= 64:
            row["networkList"] = {}
            for key, net in networks.items():
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or not isinstance(net, dict):
                    continue
                network: dict[str, Any] = {}
                for field in NETWORK_TEXT:
                    text = net.get(field)
                    if isinstance(text, str) and len(text) <= 512:
                        network[field] = text
                for field in NETWORK_BOOL:
                    if isinstance(net.get(field), bool):
                        network[field] = net[field]
                for field in NETWORK_NUMBER:
                    number = numeric(net.get(field))
                    if number is not None:
                        network[field] = number
                row["networkList"][key] = network
        cleaned["coins"].append(row)
    for field in ("allCurrencies", "activeCurrencies", "topCurrencies", "testingCurrencies"):
        values = nb.get(field)
        if isinstance(values, list) and len(values) <= 2000:
            cleaned["nobitex"][field] = sorted({s for v in values if (s := symbol(v))})
    for field in RULES:
        raw_rules = nb.get(field)
        if isinstance(raw_rules, dict) and len(raw_rules) <= 5000:
            cleaned["nobitex"][field] = {
                key: number for key, value in raw_rules.items()
                if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9]{1,48}", key)
                and (number := numeric(value)) is not None
            }
    if not seen and not cleaned["nobitex"].get("allCurrencies"):
        raise ValueError("no exchange currencies returned")
    return cleaned


class NobitexMetadata:
    def __init__(self, cache_dir: Path):
        self.path = cache_dir / "nobitex-options.json"
        self.options: dict[str, Any] = {}
        self.updated_at: float | None = None
        self.last_attempt: float | None = None
        self.error: str | None = None
        try:
            if self.path.stat().st_size <= MAX_JSON:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                timestamp = saved.get("updated_at")
                if (isinstance(timestamp, (float, int)) and not isinstance(timestamp, bool)
                        and 0 < timestamp <= time.time()):
                    self.options = clean_options(saved["options"])
                    self.updated_at = timestamp
                else:
                    self.error = "cache_unreadable"
            else:
                self.error = "cache_unreadable"
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            self.error = "cache_unreadable" if self.path.exists() else None

    def ingest(self, body: Any) -> bool:
        self.last_attempt = time.time()
        try:
            options = clean_options(body)
            encoded = json.dumps({"updated_at": self.last_attempt, "options": options},
                                 ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_JSON:
                raise ValueError("options too large")
            atomic_write(self.path, encoded)
            self.options = options
            self.updated_at = self.last_attempt
            self.error = None
            return True
        except (OSError, ValueError, TypeError):
            self.error = "invalid_or_unwritable_options"
            return False

    def failed(self) -> None:
        self.last_attempt = time.time()
        self.error = "refresh_failed"

    def view(self) -> dict[str, Any]:
        stale = self.updated_at is None or time.time() - self.updated_at >= TTL
        nb = self.options.get("nobitex", {})
        rows = {r["coin"]: r for r in self.options.get("coins", [])}
        for base in nb.get("allCurrencies", []):
            rows.setdefault(base, {"coin": base})
        coins = {}
        for base, row in rows.items():
            coins[base] = {
                "symbol": base, "name": row.get("name"), "std_name": row.get("stdName"),
                "display_precision": row.get("displayPrecision"),
                "default_network": row.get("defaultNetwork"),
                "networks": copy.deepcopy(row.get("networkList")),
                "active": base in nb["activeCurrencies"] if "activeCurrencies" in nb else None,
                "source": "nobitex", "updated_at": self.updated_at, "stale": stale,
            }
        return {"source": "nobitex", "endpoint": "/v2/options", "error": self.error,
                "updated_at": self.updated_at, "last_attempt": self.last_attempt,
                "stale": stale, "refresh_interval_s": TTL, "coins": coins,
                # Exact provider units/keys are preserved (RLS is not relabelled
                # as Toman). Missing rules remain null, never documented defaults.
                "trading_rules": {key: copy.deepcopy(nb.get(key)) for key in RULES}}

    def merge(self, logos: dict[str, Any]) -> dict[str, Any]:
        exchange = self.view()
        coins = []
        cg_by_symbol = {r["symbol"]: r for r in logos["coins"]}
        for base in sorted(set(cg_by_symbol) | set(exchange["coins"])):
            cg = cg_by_symbol.get(base, {})
            nb = exchange["coins"].get(base)
            name_from_nb = bool(nb and nb.get("name"))
            name = nb["name"] if nb and name_from_nb else cg.get("name")
            coins.append({
                **cg, "id": cg.get("id"), "symbol": base, "name": name,
                "symbol_source": "nobitex" if nb else "curated_coingecko_mapping",
                "name_source": "nobitex" if name_from_nb else "coingecko" if name else None,
                "name_updated_at": nb["updated_at"] if nb and name_from_nb else cg.get("updated_at"),
                "name_stale": nb["stale"] if nb and name_from_nb else cg.get("stale", True),
                "image_url": cg.get("image_url"),
                "logo_source": "coingecko" if cg.get("image_url") else None,
                "updated_at": cg.get("updated_at"), "stale": cg.get("stale", True),
                "exchange": nb,
            })
        return {**logos, "source": "nobitex+coingecko", "coins": coins,
                "exchange": {k: v for k, v in exchange.items() if k != "coins"}}


CACHE = NobitexMetadata(coin_metadata.CACHE.cache_dir)
