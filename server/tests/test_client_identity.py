# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""Who the exchange thinks we are.

Nobitex reads exactly one thing about the caller that we control: the
`User-Agent` (docs §User Agent). It asks for `TraderBot/XXXXX`, where XXXXX is
a unique name — so the identity is a *name*, not a URL that can rot and not a
version nobody bumps.

These tests exist because the old header was
`TraderBot/1.0 (+https://github.com/your-repo)`: a placeholder URL advertised
to a third party, a version that nothing maintained, and three different
values across five call sites.
"""

from __future__ import annotations

import pathlib

import pytest

from nobitex_client import DEFAULT_UA, NobitexClient, resolve_ua

SERVER = pathlib.Path(__file__).resolve().parents[1]


def test_the_default_names_us_and_claims_nothing_else():
    assert DEFAULT_UA == "TraderBot/nobi"
    assert "http" not in DEFAULT_UA          # a URL here is a claim that rots
    assert DEFAULT_UA.isascii()


def test_the_documented_prefix_is_added_when_a_bare_name_is_given(monkeypatch):
    monkeypatch.delenv("NBX_UA", raising=False)
    assert resolve_ua("nobi-paper") == "TraderBot/nobi-paper"


def test_a_full_value_is_kept_as_written_but_the_prefix_is_normalised(monkeypatch):
    monkeypatch.delenv("NBX_UA", raising=False)
    assert resolve_ua("TraderBot/nobi-live") == "TraderBot/nobi-live"
    assert resolve_ua("traderbot/nobi-live") == "TraderBot/nobi-live"


def test_the_environment_names_the_process(monkeypatch):
    monkeypatch.setenv("NBX_UA", "nobi-paper")
    assert resolve_ua() == "TraderBot/nobi-paper"
    # an explicit argument still wins, so one client can differ from the process
    assert resolve_ua("nobi-live") == "TraderBot/nobi-live"


@pytest.mark.parametrize("bad", ["سلام", "nobi" * 60, "   ", "TraderBot/"])
def test_an_unusable_value_falls_back_to_the_default_instead_of_being_sent(bad, monkeypatch):
    monkeypatch.delenv("NBX_UA", raising=False)
    assert resolve_ua(bad) == DEFAULT_UA


def test_the_fallback_says_so(monkeypatch, caplog):
    monkeypatch.delenv("NBX_UA", raising=False)
    with caplog.at_level("WARNING"):
        resolve_ua("سلام")
    assert any("NBX_UA" in r.message for r in caplog.records)


def test_the_client_sends_the_resolved_identity(monkeypatch):
    monkeypatch.setenv("NBX_UA", "nobi-test")
    c = NobitexClient()
    assert c.ua == "TraderBot/nobi-test"
    assert c._public_headers()["User-Agent"] == c.ua


def test_no_placeholder_repository_is_advertised_by_any_server_module():
    """The old header named a repo that does not exist. Nothing may bring it back.

    Scans the modules that actually serve requests, not the tests — this file
    quotes the old value on purpose.
    """
    # Assembled so this file cannot match itself.
    needle = "your" + "-repo"
    offenders = [
        str(p.relative_to(SERVER.parent))
        for p in SERVER.glob("*.py")
        if needle in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
