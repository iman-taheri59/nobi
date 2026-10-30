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
    assert "rows.slice(0,faView.showAll?rows.length:SIG_ROW_LIMIT)" in app
    assert "const FA_REQUEST_BUDGET=6;" in app
    assert "missing.slice(0,FA_REQUEST_BUDGET)" in app


def test_the_fa_list_says_how_many_rows_are_hidden(app):
    assert "if(rows.length>SIG_ROW_LIMIT)" in app
    assert "inFa('sg.showAll').replace('{n}',faNum(rows.length))" in app
    assert "faView.showAll=!faView.showAll;renderFaPreview();" in app


def test_no_provider_is_a_different_fact_from_no_answer(app):
    assert "f.source==='no-provider' || profile==='none' ? 'unmapped'" in app
    assert "'not-requested'" in app and "'failed'" in app
    assert "unmapped; default excluded from mean" in app
    assert "not proof of provider outage" in app


def test_snapshot_coverage_does_not_requery_or_relabel_fresh(app):
    assert "faRecord(x.symbol,x.fa,'scan-snapshot')" in app
    assert "provider receipt unverified" in app
    assert "source age unknown" in app
    assert "server-labelled live" in app.lower()


def test_mean_and_veto_have_distinct_denominators(app):
    assert "faNumber(r.score) && r.state!=='unmapped'" in app
    assert "rows.filter(r=>r.vetoes!==null)" in app
    assert "reason instances" in app
    assert "rows.length-evaluated.length" in app


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
