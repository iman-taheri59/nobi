# mypy: check_untyped_defs = False
"""Regressions from the live capoy.ir owner probe (2026-11-02).

Covers the three production findings repaired in 2.1.9:
* Secure cookies / security headers must not depend on nginx inheritance.
* global drift cohorts must not zip an empty dimension list against "global".
* the approval queue must preview the same execution geometry the broker enforces.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import identity
import main
import paper_trading
import security
import signal_drift
from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine

ROOT = Path(__file__).resolve().parents[2]


def test_cookie_secure_mode_fails_closed_and_can_force_production(monkeypatch):
    monkeypatch.delenv("NBX_COOKIE_SECURE", raising=False)
    assert identity.cookie_secure_mode() == "auto"
    monkeypatch.setenv("NBX_COOKIE_SECURE", "always")
    assert identity.cookie_secure_mode() == "always"
    monkeypatch.setenv("NBX_COOKIE_SECURE", "typo")
    assert identity.cookie_secure_mode() == "always"


def test_force_secure_does_not_depend_on_forwarded_proto(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("NBX_COOKIE_SECURE", "always")
    monkeypatch.delenv("NBX_TRUSTED_PROXIES", raising=False)
    request = Request({
        "type": "http", "scheme": "http", "method": "POST", "path": "/api/auth/login",
        "raw_path": b"/api/auth/login", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8000),
    })
    assert identity.is_secure(request) is True
    # HSTS still follows the observed transport, not the cookie policy override.
    assert identity.transport_is_https(request) is False


def test_response_hardening_headers_and_api_no_store(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    auth.reset_caches()
    client = TestClient(main.app, base_url="https://dashboard.test")
    page = client.get("/")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["referrer-policy"] == "same-origin"
    assert page.headers["strict-transport-security"] == "max-age=31536000"
    health = client.get("/api/health")
    assert "no-store" in health.headers["cache-control"]
    assert health.headers["x-content-type-options"] == "nosniff"
    plain = TestClient(main.app, base_url="http://dashboard.test").get("/api/health")
    assert "strict-transport-security" not in plain.headers


def test_login_cookies_honour_force_secure_even_on_loopback_http(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(security, "API_TOKEN", "")
    monkeypatch.setenv("NBX_COOKIE_SECURE", "always")
    auth.reset_caches()
    security.reset_rate_limits()
    auth.create_user("owner1", "a long enough password", role="owner")
    client = TestClient(main.app, base_url="http://dashboard.test")
    r = client.post("/api/auth/login", json={"username": "owner1", "password": "a long enough password"})
    assert r.status_code == 200
    raw = r.headers.get_list("set-cookie")
    session = next(c for c in raw if c.startswith("nbx_session="))
    csrf = next(c for c in raw if c.startswith("nbx_csrf="))
    assert "Secure" in session and "HttpOnly" in session
    assert "Secure" in csrf and "HttpOnly" not in csrf


def test_nginx_proxy_locations_repeat_forwarded_proto_and_security_headers():
    text = (ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    # Every location that proxies must restate X-Forwarded-Proto: nginx does not
    # inherit server-level proxy_set_header into a location with its own.
    proxied = [m.group(2) for m in re.finditer(r"location ([^{]+)\{([^}]*)\}", text)
               if "proxy_pass" in m.group(2)]
    assert proxied
    for body in proxied:
        assert "X-Forwarded-Proto $scheme" in body
        assert "X-Forwarded-For   $proxy_add_x_forwarded_for" in body
    for header in ("X-Frame-Options", "Content-Security-Policy", "X-Content-Type-Options"):
        assert f'add_header {header}' in text
        assert f"proxy_hide_header {header};" in text
    # Locations that own Cache-Control must hide the upstream copy as well.
    assert text.count("proxy_hide_header Cache-Control;") == 2


def test_global_drift_cohorts_empty_and_populated(monkeypatch, tmp_path):
    import journal

    monkeypatch.setattr(journal, "DB_PATH", tmp_path / "journal.db")
    empty = signal_drift.cohort_inventory(mode="global", days=30, now=2_000_000_000)
    assert empty["rows"] == []
    with journal.connect() as conn:
        signal_drift.ensure(conn)
        conn.execute(
            "INSERT INTO signal_log(ts,symbol,timeframe,direction,regime,config_hash,price_contract) "
            "VALUES(?,?,?,?,?,?,?)",
            (1_999_900_000, "BTCIRT", "15m", "BUY", "TREND", "cfg", journal.IRT_PRICE_CONTRACT),
        )
        conn.commit()
    result = signal_drift.cohort_inventory(mode="global", days=30, now=2_000_000_000)
    assert result["dimensions"] == []
    assert result["rows"][0]["cohort_key"] == "global"
    assert result["rows"][0]["dimensions"] == {}


class Feed:
    def __init__(self, ask: float):
        self.ask = ask

    async def get_orderbook(self, symbol):
        return {"bids": [[str(self.ask * 0.999), "1000"]],
                "asks": [[str(self.ask), "1000"]],
                "lastTradePrice": str(self.ask)}

    async def get_market_stats(self):
        return {"stats": {"USDTIRT": {"latest": "100000"}}}


@pytest.fixture()
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    return PaperBroker(100_000, Feed(100.0), RiskEngine(RiskConfig(equity=100_000)))


def test_execution_preview_matches_broker_rr_refusal_without_mutation(broker):
    broker.client = Feed(102.0)
    preview = asyncio.run(broker.execution_preview(
        "BTCUSDT", "buy", 10, stop=95, tp1=106, tp2=110,
    ))
    assert preview["executable"] is False
    assert preview["blocked_by"] == "execution-rr-too-low"
    assert preview["execution_rr"] < preview["min_rr"]
    before = (broker.cash, broker.equity, dict(broker.positions), len(broker.orders))
    pos = asyncio.run(broker.place_limit("BTCUSDT", "buy", 10, None, 95, 106, 110, "QUEUE"))
    assert pos is None
    assert broker.last_error.startswith("execution-rr-too-low")
    assert (broker.cash, broker.equity, dict(broker.positions)) == before[:3]
    assert len(broker.orders) == before[3] + 1
    row = main._order_rows(broker, 1)[0]
    assert row["rejection"]["rr"] == pytest.approx(preview["execution_rr"])
    assert row["rejection"]["min_rr"] == preview["min_rr"]
    assert row["rejection"]["tp1"] == 106
    assert row["rejection"]["tp2"] == 110


def test_execution_preview_accepts_the_same_geometry_broker_accepts(broker):
    preview = asyncio.run(broker.execution_preview(
        "BTCUSDT", "buy", 10, stop=95, tp1=106, tp2=110,
    ))
    assert preview["executable"] is True
    assert preview["blocked_by"] is None
    pos = asyncio.run(broker.place_limit("BTCUSDT", "buy", 10, None, 95, 106, 110, "QUEUE"))
    assert pos is not None
    assert pos.entry_price == pytest.approx(preview["execution_entry"])


def test_whatif_can_return_execution_preview(monkeypatch, tmp_path, broker):
    from money import Rate

    monkeypatch.setattr(auth, "AUTH_DB", tmp_path / "auth.db")
    monkeypatch.setattr(main, "get_broker", lambda *a, **k: broker)
    monkeypatch.setitem(main._cached, "signals", {"BTCUSDT": {"lastClose": 100, "risk": {"atr": 2}}})

    async def rate(sym):
        return Rate(toman_per_usdt=100000, as_of=time.time())

    monkeypatch.setattr(main, "_fx", rate)
    client = TestClient(main.app)
    r = client.post("/api/risk/whatif", json={
        "symbol": "BTCUSDT", "side": "buy", "entry": 100, "stop": 95,
        "tp1": 106, "tp2": 110, "size_quote": 1000, "execution_preview": True,
    })
    body = r.json()
    assert body["ok"] is True
    assert body["execution"]["executable"] is True
    assert body["execution"]["plan_entry"] == 100
