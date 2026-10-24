"""The fundamentals tab drew six rows of thirty-nine and blamed the wrong provider.

Same defect as the signals list (1.1.2), one tab over, plus a message that named
Nobitex as the source of fundamentals — which it is not: fundamentals come from
CoinGecko/DefiLlama (ADR-0006, ADR-0025). Pin the shape, not the pixels, for the
same reason as in `test_signals_list_limit.py`: the renderer needs the whole
dashboard around it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "nbx-app.js"
PAGE = ROOT / "index.html"


@pytest.fixture(scope="module")
def app() -> str:
    return APP.read_text(encoding="utf-8")


def test_the_fa_list_shares_the_named_limit(app):
    assert "live.ranked.slice(0, live.showAll ? live.ranked.length : SIG_ROW_LIMIT)" in app
    assert not re.search(r"\$\(\s*'faList'\s*\)\.innerHTML = live\.ranked\s*\n\s*\.slice\(0, \d+\)", app), \
        "the fundamentals list still slices on a magic number"


def test_the_fa_list_says_how_many_rows_are_hidden(app):
    assert "const faHidden = Math.max(0, live.ranked.length - SIG_ROW_LIMIT);" in app
    body = app[app.index("const faHidden"):]
    body = body[:body.index("if ($('faPrem'))") if "if ($('faPrem'))" in body else 4000]
    assert "faHidden" in body


def test_no_provider_is_a_different_fact_from_no_answer(app):
    assert "noProvider" in app
    assert "'no-provider'" in app or '"no-provider"' in app
    assert "منبع بنیادی برای این نماد نگاشت نشده" in app
    assert "no fundamental provider is mapped" in app


def test_the_old_message_does_not_accuse_nobitex_any_more(app):
    """The sentence 'the analysis server did not answer' named the exchange."""
    assert "دادهٔ فاندامنتال در دسترس نیست — سرور تحلیل پاسخ نداد" not in app


def test_the_pair_premium_rides_in_the_gates_tooltip(app):
    """The renderer lives in index.html; nbx-app.js only carries the value."""
    page = PAGE.read_text(encoding="utf-8")
    assert "sg.pairPremium" in page, "the tooltip has no line for the premium"
    assert page.count("'sg.pairPremium':") == 2, "one language is missing the key"
    assert "live.pairPremium = sig.pair_premium" in app, "the value never reaches the page"


def test_the_tooltip_line_is_measurement_only(app):
    """A premium that claimed to be applied would be the one lie this series bans."""
    page = PAGE.read_text(encoding="utf-8")
    fa = re.search(r"'sg\.pairPremium':'([^']*)'", page)
    assert fa and ("فقط اندازه‌گیری" in fa.group(1) or "measurement only" in fa.group(1))
