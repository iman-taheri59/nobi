#!/usr/bin/env python3
"""Real FastAPI + SQLite + Chromium integration for ADR-0049 price alerts.

The browser logs in through the real session forms and uses the real bell. The
server uses scratch databases, a controlled exchange client, and a controlled
Telegram sender. No test-only HTTP endpoint, real credential, external order,
or production state is involved. A restart clears process caches and serves the
standalone build to prove that history comes from SQLite rather than RAM.

Run: python scripts/price_alerts_e2e_test.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from browser_launch import add_arguments, launch_args

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "A-strong-local-alert-e2e-password!"
USERS = {"owner": "e2e-owner", "operator": "e2e-operator", "viewer": "e2e-viewer"}
FORBIDDEN_MUTATION_PREFIXES = (
    "/api/paper", "/api/live", "/api/risk", "/api/exec", "/api/order",
)


class SimulatedSenderCrash(BaseException):
    """Crash after the durable trigger, before delivery can be recorded."""


@dataclass
class Session:
    role: str
    context: Any
    page: Any
    requests: list[dict[str, Any]] = field(default_factory=list)
    external: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def close(self) -> None:
        self.context.close()


class Server:
    def __init__(self, app: Any):
        self.app = app
        self.loop = asyncio.new_event_loop()
        self.port = self._free_port()
        self.server: Any = None
        self.thread: threading.Thread | None = None
        self.error: BaseException | None = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.server.serve())
        except BaseException as exc:  # surfaced by start/stop, not hidden in a thread
            self.error = exc

    def start(self) -> None:
        import uvicorn

        self.error = None
        config = uvicorn.Config(
            self.app, host="127.0.0.1", port=self.port,
            log_level="warning", access_log=False, lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self._run, daemon=True, name="alert-e2e-server")
        self.thread.start()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if self.server.started:
                return
            if self.error or not self.thread.is_alive():
                raise RuntimeError(f"FastAPI startup failed: {self.error}")
            time.sleep(0.05)
        raise TimeoutError("FastAPI did not start within 45 seconds")

    def call(self, coroutine: Any, timeout: float = 30) -> Any:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(45)
            if self.thread.is_alive():
                raise TimeoutError("FastAPI did not stop within 45 seconds")
        if self.error:
            raise RuntimeError(f"FastAPI stopped with an error: {self.error}")
        self.server = None
        self.thread = None

    def close(self) -> None:
        if self.server is not None:
            self.stop()
        if not self.loop.is_closed():
            self.loop.close()


def require(condition: Any, label: str, report: dict[str, Any]) -> None:
    if not condition:
        raise AssertionError(label)
    report["checks"] += 1
    print(f"PASS {report['checks']:02d} {label}", flush=True)


def alert_rules(path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM price_alert_rules ORDER BY id").fetchall()
        return [dict(row) for row in rows]


def alert_events(path: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM price_alert_events ORDER BY id").fetchall()
        return [dict(row) for row in rows]


def new_session(browser: Any, server: Server, role: str, report: dict[str, Any]) -> Session:
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.add_init_script("window.setInterval = () => 0;")
    page = context.new_page()
    session = Session(role=role, context=context, page=page)
    page.on("pageerror", lambda exc: session.errors.append(str(exc)))
    page.on("request", lambda request: session.requests.append({
        "method": request.method,
        "path": urlsplit(request.url).path,
        "csrf": bool(request.headers.get("x-csrf-token")),
    }))

    def guard(route: Any, request: Any) -> None:
        if request.url.startswith(server.base + "/"):
            route.continue_()
        else:
            session.external.append(request.url)
            route.abort()

    page.route("**/*", guard)
    page.goto(server.base + "/", wait_until="load")
    page.locator("#authGate").wait_for(state="visible", timeout=20000)
    page.locator("#authUser").fill(USERS[role])
    page.locator("#authPass").fill(PASSWORD)
    with page.expect_navigation(wait_until="load", timeout=30000):
        page.locator("#authLoginBtn").click()
    page.locator("#userChip").wait_for(state="visible")
    page.wait_for_function("window.NBXAlerts && typeof NBXAlerts.open === 'function'")
    page.evaluate("""
      document.documentElement.lang = 'en';
      document.documentElement.dir = 'ltr';
      NBXAlerts.render();
    """)
    page.wait_for_load_state("networkidle", timeout=45000)
    require(not session.errors,
            f"{role}: real login and bundle load without JavaScript errors: {session.errors}", report)
    return session


def open_alerts(session: Session) -> None:
    session.page.locator("#alertsBtn").click()
    session.page.locator("#alertsModal").wait_for(state="visible")
    session.page.wait_for_function(
        "document.querySelector('#alertsRulesCount').textContent !== '—' || "
        "!document.querySelector('#alertsContent').classList.contains('hidden') === false"
    )


def refresh_alerts(session: Session) -> None:
    session.page.locator("#alertsRefresh").click()
    session.page.wait_for_function(
        "!document.querySelector('#alertsRefresh').disabled && "
        "document.querySelector('#alertsRulesCount').textContent !== '—'"
    )


def run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument("--report", type=Path, help="optional path for the credential-free JSON report")
    args = parser.parse_args()
    report: dict[str, Any] = {
        "status": "RUNNING", "checks": 0, "external_network": "browser-blocked/server-fixtures",
        "real_telegram": False, "real_orders": False,
    }
    sessions: list[Session] = []
    server: Server | None = None
    with tempfile.TemporaryDirectory(prefix="nobi-alerts-e2e-") as temporary:
        scratch = Path(temporary)
        auth_db = scratch / "auth.db"
        journal_db = scratch / "journal.db"
        os.environ.update({
            "NBX_AUTH_DB": str(auth_db), "NBX_JOURNAL_DB": str(journal_db),
            "NBX_PAPER_STATE": str(scratch / "paper.json"),
            "NBX_LIVE_DB": str(scratch / "live.db"), "NBX_LIVE_MODE": "disabled",
            "NBX_SETUP_TOKEN_FILE": str(scratch / "setup.token"),
            "NBX_CANDLE_CACHE": str(scratch / "candles"),
            "NBX_BT_DIR": str(scratch / "backtests"),
            "NBX_EXPORT_DIR": str(scratch / "exports"),
            "NBX_ALERT_TICK_S": "3600", "NBX_TICK_S": "3600",
            "NBX_AUTO_INTERVAL_S": "3600", "NBX_SCAN_LOOP_S": "0",
            "NBX_DIGEST_HOURS": "0", "NBX_AUTOBUILD_PAGE": "0",
            "NBX_SERVE_INLINE": "0", "NBX_WS_URL": "",
            "NBX_API_TOKEN": "", "NBX_TRUSTED_PROXIES": "",
            "NBX_TELEGRAM_TOKEN": "", "NBX_TELEGRAM_CHAT_ID": "",
        })
        sys.path.insert(0, str(ROOT / "server"))
        import httpx

        import auth
        import fa_engine
        import journal
        import main

        # Patch the transport boundary as a second line of defence. A provider
        # accidentally left live must fail locally instead of reaching the
        # internet merely because its high-level method was not in our stub list.
        blocked_external: list[str] = []
        real_async_send = httpx.AsyncClient.send
        real_sync_send = httpx.Client.send

        def loopback_url(url: httpx.URL) -> bool:
            return url.scheme in {"http", "https"} and url.host in {
                "127.0.0.1", "localhost", "testserver",
            }

        async def guarded_async_send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            if not loopback_url(request.url):
                blocked_external.append(str(request.url))
                raise RuntimeError("external HTTP blocked by alert E2E fixture")
            return await real_async_send(client, request, *args, **kwargs)

        def guarded_sync_send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            if not loopback_url(request.url):
                blocked_external.append(str(request.url))
                raise RuntimeError("external HTTP blocked by alert E2E fixture")
            return real_sync_send(client, request, *args, **kwargs)

        setattr(httpx.AsyncClient, "send", guarded_async_send)  # noqa: B010
        setattr(httpx.Client, "send", guarded_sync_send)  # noqa: B010

        require(journal_db == journal.DB_PATH and auth_db == auth.AUTH_DB,
                "scratch auth/journal paths are active", report)
        for role, username in USERS.items():
            auth.create_user(username, PASSWORD, role=role)
        auth.reset_caches()

        prices = {"BTCIRT": 90.0, "BTCUSDT": 50.0, "USDTIRT": 2.0}
        exchange_reads = [0]
        sender_modes = ["sent", "failed", "crash"]
        sender_calls: list[dict[str, str]] = []

        async def get_market_stats() -> dict[str, Any]:
            exchange_reads[0] += 1
            return {"status": "ok", "stats": {
                symbol: {"latest": value, "volumeDst": 1_000_000,
                         "dayChange": 0, "isClosed": False}
                for symbol, value in prices.items()
            }}

        async def get_udf_history(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            return {"s": "no_data", "t": [], "o": [], "h": [], "l": [], "c": [], "v": []}

        async def get_orderbook(_symbol: str) -> dict[str, Any]:
            return {"status": "ok", "bids": [], "asks": []}

        async def guarded_get(path: str, **_kwargs: Any) -> dict[str, Any]:
            if path == "/v2/options":
                return {"status": "ok", "nobitex": {}}
            raise RuntimeError(f"external client path blocked by fixture: {path}")

        async def provider_down(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("fundamentals provider blocked by alert fixture")

        async def metadata_offline() -> None:
            return None

        async def fake_send(text: str) -> dict[str, Any]:
            index = len(sender_calls)
            mode = sender_modes[min(index, len(sender_modes) - 1)]
            sender_calls.append({"mode": mode, "text": text})
            if mode == "crash":
                raise SimulatedSenderCrash()
            if mode == "sent":
                return {"sent": True, "transport": "telegram", "reason": "sent"}
            return {"sent": False, "transport": "telegram", "reason": "http-400",
                    "error": "fixture response body must not be persisted"}

        main.client.get_market_stats = get_market_stats
        main.client.get_udf_history = get_udf_history
        main.client.get_orderbook = get_orderbook
        main.client.get = guarded_get
        main.notify.send = fake_send
        main.coin_metadata.CACHE.run = metadata_offline
        fa_engine.FAEngine._cg_overview = provider_down
        fa_engine.FAEngine._llama_protocols = provider_down
        fa_engine.FAEngine._listen_prices = provider_down
        server = Server(main.app)
        server.start()

        def tick(price: float, stamp: int, observed_at: int | None = None) -> dict[str, Any]:
            async def controlled() -> dict[str, Any]:
                prices["BTCIRT"] = price
                await main.load_universe()
                main._cached["universe_ts"] = stamp if observed_at is None else observed_at
                return await main.evaluate_price_alerts(now=stamp)
            return server.call(controlled())

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**cast(Any, launch_args(args.channel, args.executable)))
            try:
                anonymous = browser.new_context()
                response = anonymous.request.get(server.base + "/api/alerts/rules")
                require(response.status == 401, "anonymous API read is 401", report)
                anonymous.close()

                viewer = new_session(browser, server, "viewer", report)
                sessions.append(viewer)
                viewer_alert_requests = [r for r in viewer.requests if r["path"].startswith("/api/alerts/")]
                require(not viewer_alert_requests, "Viewer boot makes no alert request", report)
                open_alerts(viewer)
                require(viewer.page.locator("#alertsAccess").is_visible()
                        and viewer.page.locator("#alertsContent").is_hidden(),
                        "Viewer bell shows only the access explanation", report)
                require(not [r for r in viewer.requests if r["path"].startswith("/api/alerts/")],
                        "Viewer bell still makes zero alert requests", report)
                require(viewer.context.request.get(server.base + "/api/alerts/rules").status == 403
                        and viewer.context.request.get(server.base + "/api/alerts/events").status == 403,
                        "Viewer real API reads are both 403", report)
                viewer.close()

                owner = new_session(browser, server, "owner", report)
                sessions.append(owner)
                open_alerts(owner)
                owner.page.locator("#alertSymbol").fill("BTCIRT")
                owner.page.locator("#alertThreshold").fill("100")
                with owner.page.expect_response(
                    lambda r: r.url.endswith("/api/alerts/rules") and r.request.method == "POST"
                ) as created_response:
                    owner.page.locator("#alertSubmit").click()
                created = created_response.value
                require(created.status == 201 and created.request.headers.get("x-csrf-token"),
                        "Owner UI creation uses the real session CSRF contract", report)
                owner.page.locator("#alertsRules [data-rule-id]").wait_for()
                rules = alert_rules(journal_db)
                require(len(rules) == 1 and rules[0]["symbol"] == "BTCIRT"
                        and rules[0]["last_status"] == "arming",
                        "UI creation is durable in SQLite with an unarmed baseline", report)
                rule_id = int(rules[0]["id"])

                base = int(time.time()) - 20
                require(tick(90, base)["triggered"] == 0,
                        "first fresh observation only arms the rule", report)
                first = tick(110, base + 1)
                require(first["triggered"] == 1 and first["deliveries"][0]["sent"] is True,
                        "false-to-true crossing creates one confirmed delivery", report)
                require(tick(120, base + 1)["triggered"] == 0
                        and tick(120, base + 2)["triggered"] == 0,
                        "timestamp replay and staying above do not duplicate the event", report)
                require(tick(90, base + 3)["triggered"] == 0,
                        "return below the threshold only resets the crossing edge", report)
                second = tick(111, base + 4)
                require(second["triggered"] == 1
                        and second["deliveries"][0]["reason"] == "http-400",
                        "a later crossing records bounded failed delivery", report)
                require(tick(90, base + 5)["triggered"] == 0,
                        "second reset produces no event", report)
                try:
                    tick(112, base + 6)
                    raise AssertionError("sender crash did not reach the fixture boundary")
                except SimulatedSenderCrash:
                    report["checks"] += 1
                    print(f"PASS {report['checks']:02d} sender crash occurs after durable trigger", flush=True)
                # ALERT_MAX_AGE_S is max(2 x tick, 90 s); the scheduler tick is
                # parked at one hour here, so the stale evidence must be older
                # than two hours to exercise the product's freshness contract.
                stale_age = int(main.ALERT_MAX_AGE_S) + 60
                require(tick(80, base + 7, observed_at=base + 7 - stale_age)["triggered"] == 0,
                        f"stale evidence ({stale_age}s old) disarms without a new event", report)
                stale = alert_rules(journal_db)[0]
                require(stale["armed"] == 0 and stale["last_status"] == "stale",
                        f"stale state is durable and explicitly disarmed: {stale['armed']}/{stale['last_status']}", report)
                require(tick(80, base + 8)["triggered"] == 0,
                        "first fresh observation after outage is only a new baseline", report)

                events = alert_events(journal_db)
                require(len(events) == 3, "exactly three crossings are persisted", report)
                require([row["delivery_sent"] for row in events] == [1, 0, None]
                        and [row["delivery_reason"] for row in events] == ["sent", "http-400", "pending"],
                        "sent, failed, and pending are distinct SQLite facts", report)
                require("fixture response body" not in json.dumps(events),
                        "external Telegram error body is not persisted", report)
                before_ticks = len([r for r in owner.requests if r["path"].startswith("/api/alerts/")])
                owner.page.wait_for_timeout(300)
                require(len([r for r in owner.requests if r["path"].startswith("/api/alerts/")]) == before_ticks,
                        "scheduler progress creates no automatic browser polling", report)
                refresh_alerts(owner)
                owner.page.wait_for_function("document.querySelector('#alertsEventsCount').textContent === '3'")
                event_text = owner.page.locator("#alertsEvents").inner_text()
                require("Telegram delivery confirmed" in event_text
                        and "Delivery not confirmed" in event_text and "pending" in event_text,
                        "real API delivery states are visible in the bell", report)

                operator = new_session(browser, server, "operator", report)
                sessions.append(operator)
                open_alerts(operator)
                require(operator.page.locator(f'#alertsRules [data-rule-id="{rule_id}"]').count() == 1,
                        "Operator sees the account-shared rule created by Owner", report)
                toggle = f'#alertsRules [data-rule-id="{rule_id}"] [data-alert-action="toggle"]'
                with operator.page.expect_response(
                    lambda r: f"/api/alerts/rules/{rule_id}" in r.url and r.request.method == "PATCH"
                ) as disabled_response:
                    operator.page.locator(toggle).click()
                require(disabled_response.value.status == 200
                        and alert_rules(journal_db)[0]["enabled"] == 0,
                        "Operator disable mutates the real shared rule with CSRF", report)
                operator.page.wait_for_function(
                    f"document.querySelector('{toggle}')?.textContent === 'Enable'"
                )
                with operator.page.expect_response(
                    lambda r: f"/api/alerts/rules/{rule_id}" in r.url and r.request.method == "PATCH"
                ):
                    operator.page.locator(toggle).click()
                operator.page.wait_for_function(
                    f"document.querySelector('{toggle}')?.textContent === 'Disable'"
                )
                enabled = alert_rules(journal_db)[0]
                require(enabled["enabled"] == 1 and enabled["armed"] == 0
                        and enabled["last_status"] == "arming",
                        "re-enable requires a fresh baseline", report)
                operator.close()

                refresh_alerts(owner)
                delete_count = len([r for r in owner.requests if r["method"] == "DELETE"])
                owner.page.locator(f'#alertsRules [data-rule-id="{rule_id}"] [data-alert-action="delete"]').click()
                require(len([r for r in owner.requests if r["method"] == "DELETE"]) == delete_count,
                        "first delete click only opens confirmation", report)
                with owner.page.expect_response(
                    lambda r: f"/api/alerts/rules/{rule_id}" in r.url and r.request.method == "DELETE"
                ):
                    owner.page.locator(
                        f'#alertsRules [data-rule-id="{rule_id}"] [data-alert-action="confirm-delete"]'
                    ).click()
                owner.page.wait_for_function("document.querySelector('#alertsRulesCount').textContent === '0'")
                retained = alert_events(journal_db)
                require(not alert_rules(journal_db) and len(retained) == 3
                        and all(row["rule_id"] is None for row in retained),
                        "delete retains all event snapshots with null rule ids", report)

                require(all(not session.errors for session in sessions),
                        "all canonical role sessions remain free of JavaScript errors", report)
                forbidden = [r for session in sessions for r in session.requests
                             if r["method"] != "GET" and any(r["path"].startswith(prefix)
                             for prefix in FORBIDDEN_MUTATION_PREFIXES)]
                require(not forbidden, "no Paper/Live/risk/execution/order mutation was requested", report)
                owner.close()
            finally:
                browser.close()

        server.stop()
        main._cached.update({"alert_universe": [], "universe": [], "universe_ts": 0,
                             "premium": None, "premium_pct": None})
        auth.reset_caches()
        os.environ["NBX_SERVE_INLINE"] = "1"
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**cast(Any, launch_args(args.channel, args.executable)))
            try:
                owner = new_session(browser, server, "owner", report)
                sessions.append(owner)
                require(owner.page.locator("script#nbx-inline-nbx-alerts-js").count() == 1,
                        "restart serves the exact standalone alert module", report)
                open_alerts(owner)
                owner.page.wait_for_function(
                    "document.querySelector('#alertsRulesCount').textContent === '0' && "
                    "document.querySelector('#alertsEventsCount').textContent === '3'"
                )
                require("Deleted rule; retained history" in owner.page.locator("#alertsEvents").inner_text(),
                        "standalone after cache-clearing restart reads retained history from SQLite", report)
                require(not owner.errors, "standalone restart has no JavaScript errors", report)
                owner.close()
            finally:
                browser.close()

        server.stop()
        server.close()
        with sqlite3.connect(journal_db) as conn:
            actors = conn.execute(
                "SELECT action, actor FROM audit_log WHERE action LIKE 'price_alert.%' ORDER BY id"
            ).fetchall()
        require(("price_alert.create", USERS["owner"]) in actors
                and ("price_alert.disable", USERS["operator"]) in actors
                and ("price_alert.enable", USERS["operator"]) in actors
                and ("price_alert.delete", USERS["owner"]) in actors,
                "audit actors match the real Owner/Operator UI mutations", report)
        require(len(sender_calls) == 3 and all("Nobi price alert" in row["text"] for row in sender_calls),
                "three controlled Telegram attempts and no real Telegram delivery", report)
        require(exchange_reads[0] > 0, "controlled scheduler consumed the canonical exchange loader", report)
        report.update({
            "status": "PASS", "canonical_and_standalone": True,
            "roles": {"anonymous": 401, "viewer": 403, "mutations": "Owner/Operator session+CSRF"},
            "events": {"sent": 1, "failed": 1, "pending": 1},
            "replay_no_duplicate": True, "stale_disarm": True, "recovery_baseline": True,
            "restart_sqlite_persistence": True, "history_retained": True,
            "forbidden_trading_mutations": 0,
            "server_external_requests_blocked": len(blocked_external),
        })
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
