"""The health probe's cached result must carry its own freshness semantics."""
from __future__ import annotations

import time
from datetime import datetime

import main


def test_cached_upstream_probe_is_marked_stale_while_refresh_is_scheduled(monkeypatch):
    now = time.time()
    monkeypatch.setattr(main, "_upstream", {
        "ts": now - 3600, "ok": True, "latency_ms": 5.0,
        "error": None, "probing": False,
    })
    monkeypatch.setattr(main, "UPSTREAM_TTL_S", 60)
    scheduled = []

    def spawn(coro):
        scheduled.append(coro)
        coro.close()

    monkeypatch.setattr(main, "_spawn", spawn)
    out = main._upstream_snapshot()
    assert out["ok"] is True  # last result, not a current guarantee
    assert out["stale"] is True
    assert out["refresh_scheduled"] is True
    assert 3599 <= out["age_s"] <= 3601
    # ISO microseconds intentionally discard sub-microsecond float precision.
    assert abs(datetime.fromisoformat(out["checked_at"]).timestamp() - (now - 3600)) < 0.001
    assert len(scheduled) == 1


def test_fresh_upstream_probe_does_not_schedule_a_duplicate(monkeypatch):
    monkeypatch.setattr(main, "_upstream", {
        "ts": time.time(), "ok": True, "latency_ms": 5.0,
        "error": None, "probing": False,
    })
    monkeypatch.setattr(main, "UPSTREAM_TTL_S", 60)

    def forbidden(_coro):
        raise AssertionError("a fresh probe was needlessly refreshed")

    monkeypatch.setattr(main, "_spawn", forbidden)
    out = main._upstream_snapshot()
    assert out["stale"] is False
    assert out["refresh_scheduled"] is False
    assert out["checked_at"] is not None
