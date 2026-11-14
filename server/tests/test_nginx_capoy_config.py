# mypy: check_untyped_defs = False
"""Structural guards for the operator-specific capoy.ir nginx merge.

The live site file showed three nginx inheritance traps:
- a location with its own proxy_set_header lost X-Forwarded-*;
- a location with its own add_header lost the server security headers;
- upstream app headers were not hidden, so API responses doubled them.

These tests do not replace `nginx -t`; they keep the merged artifact from
quietly regressing before an operator installs it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "deploy" / "nginx-capoy.ir.conf"

SECURITY_HEADERS = (
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "X-Frame-Options",
    "Content-Security-Policy",
)
FORWARDED_HEADERS = (
    "Host",
    "X-Real-IP",
    "X-Forwarded-For",
    "X-Forwarded-Proto",
    "X-Forwarded-Host",
)


def text() -> str:
    return CONF.read_text(encoding="utf-8")


def uncommented(source: str) -> str:
    """Strip nginx comments so prose does not look like directives."""
    return re.sub(r"#[^\n]*", "", source)


def block_body(source: str, start: int) -> str:
    """Return the body of the first balanced {... block after start."""
    open_brace = source.find("{", start)
    assert open_brace != -1
    depth = 0
    for index in range(open_brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1:index]
    raise AssertionError("unbalanced nginx block")


def https_server() -> str:
    source = uncommented(text())
    # Find the server block that listens on 443.
    for m in re.finditer(r"server\s*\{", source):
        body = block_body(source, m.start())
        if "listen 443" in body:
            return body
    raise AssertionError("no HTTPS server block")


def locations(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in re.finditer(r"location\s+([^{]+)\{", body):
        name = m.group(1).strip()
        out[name] = block_body(body, m.start())
    return out


def test_preserves_live_host_upstream_tls_and_acme():
    source = text()
    assert "server_name capoy.ir;" in source
    assert "server 127.0.0.1:8000 fail_timeout=0;" in source
    assert "/etc/letsencrypt/live/capoy.ir/fullchain.pem" in source
    assert "/etc/letsencrypt/live/capoy.ir/privkey.pem" in source
    assert "location /.well-known/acme-challenge/ { root /var/www/html; }" in source
    assert "return 301 https://$host$request_uri;" in source


def test_http_redirect_block_does_not_add_hsts():
    source = uncommented(text())
    first = re.search(r"server\s*\{", source)
    assert first
    http = block_body(source, first.start())
    assert "listen 80" in http
    assert "add_header" not in http
    assert "Strict-Transport-Security" not in http


def test_https_server_has_one_authoritative_security_set_and_hides_upstream():
    body = https_server()
    for header in SECURITY_HEADERS:
        assert len(re.findall(rf"add_header {header}\b", body)) >= 1
        assert f"proxy_hide_header {header};" in body
    assert 'add_header Strict-Transport-Security "max-age=31536000" always;' in body
    assert 'add_header X-Frame-Options "DENY" always;' in body
    assert 'add_header Content-Security-Policy "frame-ancestors \'none\'" always;' in body


def test_every_proxied_location_repeats_full_forwarded_header_set():
    locs = locations(https_server())
    proxied = {name: body for name, body in locs.items() if "proxy_pass" in body}
    assert set(proxied) == {"/", "/assets/", "= /"}
    for body in proxied.values():
        for header in FORWARDED_HEADERS:
            assert re.search(rf"proxy_set_header\s+{header}\s+", body), header
        assert "proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;" in body
        assert "proxy_set_header X-Forwarded-Proto $scheme;" in body


def test_generic_location_inherits_security_and_does_not_hide_api_cache():
    body = locations(https_server())["/"]
    assert "add_header" not in body
    assert "proxy_hide_header" not in body
    assert "Cache-Control" not in body


def test_cache_owning_locations_hide_upstream_and_repeat_security_headers():
    locs = locations(https_server())
    for name in ("/assets/", "= /"):
        body = locs[name]
        assert "proxy_hide_header Cache-Control;" in body
        for header in SECURITY_HEADERS:
            assert f"proxy_hide_header {header};" in body
            assert re.search(rf"add_header {header}\b", body)
    assert 'add_header Cache-Control "no-store" always;' in locs["= /"]
    # `immutable` on a filename with no content hash pins a stale asset in the
    # browser for a week: the deploy is live and the user is still running the
    # previous CSS. Revalidation is the only correct policy without hashes.
    assert 'immutable' not in locs["/assets/"]
    assert 'add_header Cache-Control "public, max-age=0, must-revalidate" always;' in locs["/assets/"]
    # expires plus add_header Cache-Control can emit two Cache-Control values.
    assert not re.search(r"^\s*expires\s+", locs["/assets/"], re.MULTILINE)


def test_does_not_strip_bearer_token_or_log_it():
    source = uncommented(text())
    assert not re.search(r"proxy_set_header\s+X-API-Token\s+\"\"\s*;", source)
    assert "$http_x_api_token" not in source
    assert "log_format nobi" in source
