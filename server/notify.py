"""Telegram delivery for operational events (ADR-0011 §2.2).

The dashboard used to *claim* it had notified Telegram. It had not: there was
no sender anywhere in the codebase. This module makes the claim true when it
can be, and makes the absence explicit when it cannot.

Configuration (both must be set, otherwise the channel is simply off):

    NBX_TELEGRAM_TOKEN   bot token from @BotFather
    NBX_TELEGRAM_CHAT_ID destination chat / channel id

Design constraints:

* **Never block a control action.** Sending happens after the audit row is
  written, with a short timeout, and a failure is recorded — not raised.
* **Never lie.** `status()` reports `configured: false` and the UI says
  "not configured" instead of "sent".
* **Rate friendly.** Telegram allows ~30 msg/s; one message per control action
  is far below that. Failures back off for `NBX_TELEGRAM_BACKOFF_S` (default
  300s) so a revoked token cannot spam the log on every tick.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from logsafe import quiet_httpx

log = logging.getLogger("nbx-app.notify")

TOKEN = os.getenv("NBX_TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.getenv("NBX_TELEGRAM_CHAT_ID", "").strip()
BACKOFF_S = int(os.getenv("NBX_TELEGRAM_BACKOFF_S", "300"))

_state: dict[str, Any] = {"sent": 0, "failed": 0, "last_error": None,
                          "last_sent_at": None, "blocked_until": 0.0}


def configured() -> bool:
    return bool(TOKEN and CHAT_ID)


def status() -> dict[str, Any]:
    """What the UI is allowed to say about this channel."""
    return {
        "configured": configured(),
        "transport": "telegram" if configured() else "disabled",
        "chat": ("…" + CHAT_ID[-4:]) if configured() and len(CHAT_ID) > 4 else None,
        "sent": _state["sent"],
        "failed": _state["failed"],
        "last_error": _state["last_error"],
        "last_sent_at": _state["last_sent_at"],
        "backoff_s": BACKOFF_S,
        "backing_off": time.time() < _state["blocked_until"],
    }


async def send(text: str, *, timeout: float = 6.0) -> dict[str, Any]:
    """Send one message. Returns a result dict; never raises."""
    if not configured():
        return {"sent": False, "transport": "disabled", "reason": "not-configured"}
    if time.time() < _state["blocked_until"]:
        return {"sent": False, "transport": "telegram", "reason": "backing-off"}

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        # The token is inside the URL; httpx logs URLs at INFO.
        with quiet_httpx():
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json={
                    "chat_id": CHAT_ID,
                    "text": text[:4000],
                    "disable_web_page_preview": True,
                })
        if r.status_code == 200 and r.json().get("ok"):
            _state.update(sent=_state["sent"] + 1, last_sent_at=int(time.time()),
                          last_error=None)
            return {"sent": True, "transport": "telegram"}
        # 401/400 are permanent (bad token/chat); 429 is the usual transient one.
        body = r.text[:200]
        _state.update(failed=_state["failed"] + 1, last_error=f"{r.status_code}: {body}")
        if r.status_code in (400, 401, 403):
            _state["blocked_until"] = time.time() + BACKOFF_S
        log.warning("telegram send failed (%s): %s", r.status_code, body)
        return {"sent": False, "transport": "telegram",
                "reason": f"http-{r.status_code}", "error": body}
    except Exception as e:  # noqa: BLE001 - a notifier must not break the caller
        # `str(e)` for an httpx transport error can embed the request URL, and
        # that URL contains the bot token. Keep the type and drop the message.
        detail = type(e).__name__
        _state.update(failed=_state["failed"] + 1, last_error=detail)
        log.warning("telegram unreachable: %s", detail)
        return {"sent": False, "transport": "telegram", "reason": "unreachable",
                "error": detail}
