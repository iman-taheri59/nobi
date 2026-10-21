"""The login screen is HTML + JS in two files that must agree on ids.

There is no browser in CI, so this is the test that can still fail loudly when
someone renames `authPass` in the markup: `$('authPass')` would then be `null`
and the form would throw on the first keystroke *in the browser only* — the
server tests would stay green and the deployment would ship a dead login box.

It also pins the two properties that are easy to lose in a refactor:

* the gate is `hidden` in the markup, so a visitor with JavaScript disabled
  never sees a form that cannot submit;
* the gate ends up inside the built standalone file, because that is the page
  the server actually serves.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "index.html"
APP = ROOT / "nbx-app.js"
CORE = ROOT / "nbx-core.js"
STANDALONE = ROOT / "nobitex-mobile.html"

#: Everything the sign-in code looks up by id.
AUTH_IDS = {
    "authGate", "authTitle", "authLoginForm", "authRegisterForm", "authUser",
    "authPass", "authLoginBtn", "authCode", "authNewUser", "authNewPass",
    "authRegBtn", "authBackBtn", "authMsg", "authToRegister", "userChip",
    "userName", "userRole", "logoutBtn",
}


@pytest.fixture(scope="module")
def html() -> str:
    return HTML.read_text(encoding="utf-8")


def test_every_auth_id_the_script_uses_exists_in_the_markup(html: str) -> None:
    missing = [i for i in AUTH_IDS if f'id="{i}"' not in html]
    assert not missing, f"nbx-app.js looks these up but the markup has no such id: {missing}"


def test_the_script_only_looks_up_ids_that_exist(html: str) -> None:
    """The reverse direction: a renamed id leaves a `null` behind.

    Scoped to the account screen. The rest of the app creates elements at
    runtime (`mobileBottomNav`, `liveBadge`, …), so "every `$()` must be in the
    markup" is false for the file as a whole and true for this block.
    """
    src = APP.read_text(encoding="utf-8")
    start = src.index("Accounts (ADR-0018)")
    end = src.index("async function start()")
    block = src[start:end]
    used = set(re.findall(r"\$\('([^']+)'\)", block))
    used |= set(re.findall(r'getElementById\("([^"]+)"\)', block))
    # Some ids are produced by the panel's own template literal (`invGo`,
    # `invRole`, …): they never appear in the static markup but do exist by the
    # time the code looks them up. Accept either source, reject neither.
    created = set(re.findall(r'id="([A-Za-z][\w-]*)"', block))
    missing = sorted(i for i in used - created if f'id="{i}"' not in html)
    assert not missing, f"no element with these ids, so the code gets null: {missing}"


def test_the_gate_starts_hidden(html: str) -> None:
    """No JavaScript, no form: a visible gate would be a dead end."""
    tag = re.search(r'<div id="authGate"[^>]*>', html)
    assert tag and "hidden" in tag.group(0)


def test_the_gate_is_in_the_page_the_server_serves(html: str) -> None:
    built = STANDALONE.read_text(encoding="utf-8")
    assert 'id="authGate"' in built, "rebuild it: python scripts/build_standalone.py"
    # The build must not have dropped the stylesheet that positions the gate.
    assert ".auth-wrap" in built


def test_the_csrf_header_is_sent_by_the_shared_helper() -> None:
    core = CORE.read_text(encoding="utf-8")
    assert "X-CSRF-Token" in core
    assert "readCookie('nbx_csrf')" in core


def test_no_mutation_posts_without_the_auth_headers() -> None:
    """Every `POST` in the core goes through `_authHeaders()`.

    A hand-built `{'Content-Type': ...}` on a mutating call would work right up
    until accounts exist and then 403 — the three backtest endpoints were
    exactly that before this test existed.
    """
    core = CORE.read_text(encoding="utf-8")
    blocks = re.findall(r"method: 'POST',\s*\n\s*headers: ([^,\n]+)", core)
    offenders = [b for b in blocks if "_authHeaders()" not in b]
    assert not offenders, f"POSTs that skip the CSRF token: {offenders}"


def test_the_login_form_does_not_put_the_password_in_the_url(html: str) -> None:
    """A form without method=post would leak it into the query string and logs."""
    for form_id in ("authLoginForm", "authRegisterForm"):
        tag = re.search(rf'<form id="{form_id}"[^>]*>', html)
        assert tag
        assert 'method="get"' not in tag.group(0).lower()
