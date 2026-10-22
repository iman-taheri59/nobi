"""The threshold card is markup + inline JS in one file, and a rule it must not break (1.0.13).

`server/adaptive.py` measures what a fixed score threshold means on a timeframe and
returns `applied: false`. The dashboard may show that measurement, and that is all it
may do: the whole point of ADR-0020 is that a proposal is never a silent policy. So the
risk this file guards is not a typo in an id — it is a card that shows a better-looking
number without saying it has not been applied.

There is no browser in CI, so these tests pin the wiring that only breaks in one:

* every id the card's script touches exists in the markup (a renamed id means
  `textContent` on `null`, which throws on first click and nowhere else);
* the request goes to the route that exists, with the same candle source as the
  backtest next to it;
* the refusal path renders words, never zeros — an unreachable venue has to look
  unreachable;
* both languages carry the label, because the label is the safety property;
* the page the server actually serves (`nobitex-mobile.html`) contains the card, since
  `index.html` is not what a phone opens.

Version 1.0.14 added the second half of the same rule. A measurement belongs to a pair
(symbol, timeframe, feed), and the three controls sit next to the card. So the pair that
was measured travels with the numbers: when the selection moves, the card says it is
stale and names the drift before it re-measures. Numbers from BTCIRT/1h under a header
that says ETHIRT/4h are the same lie as an unlabelled proposal, only quieter.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HTML = ROOT / "index.html"
STANDALONE = ROOT / "nobitex-mobile.html"

#: Ids the adaptive card's script looks up. Kept as a set so a rename in either
#: file fails here instead of in someone's browser.
CARD_IDS = {
    "adCard", "adBtn", "adBadge", "adSym", "adTf", "adAtr", "adPct", "adRegime",
    "adFactor", "adLong", "adNeutral", "adShare", "adBars", "adWhy", "adState",
}

#: i18n keys the card reads through `t()`. A missing key renders its own name.
CARD_KEYS = {
    "ad.title", "ad.measureOnly", "ad.measure", "ad.atr", "ad.percentile", "ad.regime",
    "ad.factor", "ad.long", "ad.neutral", "ad.share", "ad.bars", "ad.applied",
    "ad.notApplied", "ad.needBackend", "ad.failed", "ad.refused", "ad.clamped",
    "ad.stale",
}


def _page() -> str:
    return HTML.read_text(encoding="utf-8")


def _card_script() -> str:
    """The IIFE for the adaptive card, isolated from the rest of the page."""
    page = _page()
    start = page.index("Adaptive thresholds (ADR-0020)")
    end = page.index("Grid Search heatmap", start)
    return page[start:end]


def test_every_id_the_card_touches_exists_in_the_markup() -> None:
    page = _page()
    for name in CARD_IDS:
        assert f'id="{name}"' in page, f"the markup has no #{name}"
    script = _card_script()
    # Anything looked up by string in the script must be one of the known ids.
    looked_up = set(re.findall(r"(?:getElementById\('|\bset\(')([A-Za-z]+)", script))
    missing = {name for name in looked_up if name.startswith("ad")} - CARD_IDS
    assert not missing, f"the card reads ids the markup does not define: {sorted(missing)}"


def test_the_card_asks_the_route_that_exists_with_the_same_candle_source() -> None:
    script = _card_script()
    assert "/adaptive/thresholds/" in script
    # Measurement and backtest must read one feed: two panels on two sources is
    # how they start disagreeing about the same symbol.
    assert "btSrc" in script, "the card ignores the backtest's candle source"
    compact = script.replace(" ", "")
    assert "&source=${" in compact, "the request does not carry the candle source"
    assert "btSrc" in script
    assert "btSym" in script and "btTf" in script


def test_the_label_says_it_was_not_applied_in_both_languages() -> None:
    page = _page()
    # Persian and English literals both define the key, and the value says
    # "not applied" rather than something softer.
    fa = re.search(r"'ad\.notApplied':'([^']+)'", page)
    en = re.search(r"'ad\.notApplied':'([^']+)'", page[page.index("ad.notApplied"):]) \
        if page.count("'ad.notApplied'") > 1 else None
    assert fa and "اعمال نشد" in fa.group(1)
    occurrences = page.count("'ad.notApplied':")
    assert occurrences == 2, f"expected fa + en definitions, found {occurrences}"
    assert en is not None and "not applied" in en.group(1)
    # And the card shows it, from the same key, not a hardcoded sentence.
    assert "t('ad.notApplied')" in _card_script()
    assert "t('ad.measureOnly')" in _card_script()


def test_every_key_the_card_reads_is_defined_twice() -> None:
    page = _page()
    for key in CARD_KEYS:
        assert page.count(f"'{key}':") == 2, f"{key} is not defined in both languages"


def test_a_refusal_renders_a_named_reason_not_a_zero() -> None:
    script = _card_script()
    refusal = script[script.index("r.ok !== true"):script.index("const v = r.volatility")]
    assert "dash()" in refusal, "the refusal path must clear the numbers"
    assert "t('ad.refused')" in refusal
    assert "r.error" in refusal
    # Nothing in the refusal path may default a missing number to 0.
    assert "|| 0" not in refusal


def test_an_unreachable_backend_is_a_sentence_not_a_blank_card() -> None:
    script = _card_script()
    guard = script[script.index("async function measure()"):script.index("btn.addEventListener")]
    assert "t('ad.needBackend')" in guard
    assert "catch (e)" in guard and "t('ad.failed')" in guard


def test_the_measurement_never_runs_on_boot() -> None:
    """One light request when the lab is opened; nothing on page load."""
    script = _card_script()
    assert "document.querySelectorAll('.navbtn[data-tab=\"backtest\"]')" in script
    assert "setTimeout(measure" in script
    boot = _page()[_page().index("async function start()"):] if "async function start()" in _page() else ""
    assert "adMeasureThresholds" not in boot


def test_the_served_page_contains_the_card() -> None:
    """A phone opens the generated artifact, not index.html."""
    if not STANDALONE.is_file():
        pytest.skip("the generated page is not in this checkout")
    built = STANDALONE.read_text(encoding="utf-8")
    for name in ("adCard", "adBtn", "adaptive/thresholds/"):
        assert name in built, f"the built page is missing {name} — run: npm run build"


#: 1.0.14 — the pair the numbers belong to.
def test_the_card_says_when_the_pair_it_measured_has_moved() -> None:
    script = _card_script()
    assert "const sel = ()" in script, "the card does not read the live selection"
    assert "drift" in script and "adCard" in script
    # The stale label comes from i18n, not from a hardcoded word, and the drift is
    # named as "measured → now" rather than described vaguely.
    assert "t('ad.stale')" in script
    assert "`${asked} → ${now}`" in script
    # The badge must be able to stop saying "measurement only": the chip and the
    # sentence both switch on the same condition.
    assert "text-amber-300 border-amber-300/30" in script


def test_a_moved_selection_re_measures_after_a_debounce() -> None:
    script = _card_script()
    assert "['btSym', 'btTf', 'btSrc']" in script, "the card does not watch the controls"
    assert "addEventListener('change'" in script
    # Debounced, so dragging through a symbol list is one request, not ten.
    assert "clearTimeout(timer)" in script and "setTimeout(() => { timer = null; measure(); }" in script


def test_changing_the_controls_never_fires_a_measurement_nobody_asked_for() -> None:
    """Until the person asks once, the card is silent — it is evidence, not a poller."""
    script = _card_script()
    handler = script[script.index("function onControlChange()"):]
    handler = handler[: handler.index("btn.addEventListener")]
    assert "if (!asked) return;" in handler


def test_the_stale_path_keeps_the_numbers_and_the_named_pair() -> None:
    """Stale numbers stay — they are true for their own pair — but they may not
    wear the current header, and nothing is defaulted to zero."""
    script = _card_script()
    paint = script[script.index("function paint()"):script.index("function render(r)")]
    assert "card.dataset.stale" in paint, "the stale state is not externally observable"
    assert "→" in paint
    assert "|| 0" not in paint
