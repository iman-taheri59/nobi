# mypy: check_untyped_defs = False
"""Offline tests for the public edge-header checker."""

from __future__ import annotations

import importlib.util
import sys
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_edge_headers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("check_edge_headers", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def good_headers(cache: str = "no-store") -> Message:
    headers = Message()
    headers["Strict-Transport-Security"] = "max-age=31536000"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Referrer-Policy"] = "same-origin"
    headers["X-Frame-Options"] = "DENY"
    headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    headers["Cache-Control"] = cache
    return headers


def test_good_dashboard_and_api_headers_pass():
    module = load_module()
    assert all(c.status == "pass" for c in module.evaluate("/", 200, good_headers(), "https"))
    api = good_headers("private, no-store, max-age=0")
    assert all(c.status == "pass" for c in module.evaluate("/api/health", 200, api, "https"))


def test_duplicate_and_missing_headers_fail():
    module = load_module()
    headers = good_headers()
    headers["X-Content-Type-Options"] = "nosniff"
    headers["Cache-Control"] = "no-store"
    del headers["Strict-Transport-Security"]

    results = module.evaluate("/", 200, headers, "https")
    failed = {c.name for c in results if c.status == "fail"}
    assert "x-content-type-options count" in failed
    assert "dashboard cache-control" in failed
    assert "hsts max-age" in failed


def test_plain_http_does_not_require_hsts():
    module = load_module()
    headers = good_headers()
    del headers["Strict-Transport-Security"]
    results = module.evaluate("/", 200, headers, "http")
    assert all(c.status == "pass" for c in results)


def test_script_self_test():
    module = load_module()
    assert module.self_test() == 0
