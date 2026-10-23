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
    # 1.0.15 — the A/B evidence line
    "adEvBtn", "adEv", "adEvWindows", "adEvPf", "adEvDelta", "adEvVerdict", "adEvWhy",
}

#: i18n keys the card reads through `t()`. A missing key renders its own name.
CARD_KEYS = {
    "ad.title", "ad.measureOnly", "ad.measure", "ad.atr", "ad.percentile", "ad.regime",
    "ad.factor", "ad.long", "ad.neutral", "ad.share", "ad.bars", "ad.applied",
    "ad.notApplied", "ad.needBackend", "ad.failed", "ad.refused", "ad.clamped",
    "ad.stale",
    "ad.dataStale", "ad.dataStaleWhy", "ad.dataStaleNone",
    # 1.0.15
    "ad.evidence", "ad.evTitle", "ad.evWindows", "ad.evPf", "ad.evDelta", "ad.evIdle",
    "ad.evVerdictAdaptive", "ad.evVerdictBaseline", "ad.evInconclusive", "ad.evNoPromote",
    "ad.evStale", "ad.evFailed",
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


#: 1.0.15 — the A/B evidence line.
def test_the_evidence_line_keeps_the_sample_next_to_the_verdict() -> None:
    script = _card_script()
    # The *second* assignment is the one that renders a response; the first is
    # the "clear to a dash" path.
    windows = script[script.rindex("evSet('adEvWindows'"):]
    windows = windows[:windows.index("\n")]
    # Window count *and* the window geometry, from the response, not a constant.
    for field in ("w.count", "w.train_bars", "w.eval_bars"):
        assert field in windows, f"the evidence line drops {field} — a verdict without n"
    assert "t('ad.evWindows')" in windows


def test_the_evidence_line_never_claims_application_or_certification() -> None:
    script = _card_script()
    block = script[script.index("function renderEvidence(r)"):script.index("async function evidence()")]
    assert "t('ad.notApplied')" in block, "the A/B line stopped saying it was not applied"
    assert "r.candles_source !== 'nobitex'" in block and "t('ad.evNoPromote')" in block, \
        "a reference feed could now look like certification"
    # Every verdict label is named in both languages and says "evidence only" or
    # "inconclusive"; none of them reads as an instruction.
    page = _page()
    for key in ("ad.evVerdictAdaptive", "ad.evVerdictBaseline"):
        assert page.count(f"'{key}':") == 2
    fa_slice = page[page.index("'ad.evVerdictAdaptive':"):][:120]
    en_slice = page[page.rindex("'ad.evVerdictAdaptive':"):][:160]
    assert "فقط شاهد" in fa_slice, fa_slice
    assert "evidence only" in en_slice, en_slice


def test_a_refused_evidence_run_clears_the_numbers() -> None:
    script = _card_script()
    block = script[script.index("function renderEvidence(r)"):script.index("async function evidence()")]
    refusal = block[block.index("r.ok !== true"):block.index("const b = r.baseline")]
    assert "evClear(false)" in refusal, "a refusal must not leave the previous run on screen"
    assert "r.error" in refusal
    assert "|| 0" not in refusal


def test_the_evidence_run_needs_a_click_and_its_panel_is_stale_with_the_pair() -> None:
    script = _card_script()
    assert "evBtn.addEventListener('click', evidence)" in script
    boot = _page()
    if "async function start()" in boot:
        start_block = boot[boot.index("async function start()"):]
        assert "adEvidenceRun" not in start_block, "the A/B run fires on boot"
    handler = script[script.index("function onControlChange()"):script.index("btn.addEventListener")]
    assert "t('ad.evStale')" in handler, "the verdict survives a change of pair"


#: 1.0.18 — old candles are a different failure from a wrong pair.
def test_stale_data_is_named_separately_from_a_stale_pair() -> None:
    script = _card_script()
    paint = script[script.index("function paint()"):script.index("function render(r)")]
    # Two distinct flags on the element: a card that only knows "stale" cannot
    # tell a user whether to re-select or re-fetch.
    assert "card.dataset.stale" in paint and "card.dataset.dataStale" in paint
    assert "t('ad.dataStale')" in paint
    # The sentence itself is built in dataNote(); paint() must consume it rather
    # than re-derive a second, drifting version.
    assert "dataNote()" in paint
    # The wrong pair outranks old candles, and both are mentioned when both apply.
    assert paint.index("if (drift)") < paint.index("if (data)")


def test_the_data_staleness_carries_its_arithmetic() -> None:
    script = _card_script()
    note = script[script.index("function dataNote()"):script.index("function paint()")]
    assert "{bars}" in note and "age_bars" in note, "the stale sentence lost its number"
    assert "fmtNum" in note, "the age is not formatted like the rest of the page"
    assert "no-candles" in note, "an empty series has no words of its own"
    page = _page()
    for lang_key in ("'ad.dataStaleWhy':",):
        assert page.count(lang_key) == 2
    assert "{bars}" in page[page.index("'ad.dataStaleWhy':"):][:80]


def test_freshness_is_read_from_every_answer_and_cleared_by_the_next() -> None:
    script = _card_script()
    assert "fresh = (r && r.freshness) || null;" in script
    # A response without the block must leave the card in its plain state rather
    # than guessing "fresh".
    assert "if (!fresh || !fresh.stale) return '';" in script


#: 1.0.23 — the scan card must prefer the number the server measured per symbol.
def test_the_scan_card_prefers_the_servers_per_symbol_answer() -> None:
    page = (Path(__file__).resolve().parents[2] / "index.html").read_text(encoding="utf-8")
    fn = page[page.index("function sgFreshNow()"):page.index("function sgRenderFresh(f)")]
    # The server measured every symbol and the list on screen is its answer.
    assert "l.scanFreshness" in fn or "live.scanFreshness" in fn
    # ...with the browser engine's own scan as the fallback, not as the first word.
    assert fn.index("scanFreshness") < fn.index("freshness()")
    # And the chip names the worst symbol instead of averaging it away.
    chip = page[page.index("function sgRenderFresh(f)"):page.index("function renderSignals()")]
    assert "fresh.worst" in chip and "sg.dataStaleWorst" in chip
    assert page.count("'sg.dataStaleWorst':") == 2
    assert page.count("'sg.dataStaleMore':") == 2
    # The response is kept where the card can read it.
    app = (Path(__file__).resolve().parents[2] / "nbx-app.js").read_text(encoding="utf-8")
    assert "live.scanFreshness = sig.freshness || null;" in app
