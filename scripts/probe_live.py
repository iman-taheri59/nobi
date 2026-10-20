#!/usr/bin/env python3
"""probe_live.py — verify the dashboard against a REAL Nobitex feed.

Every other test in this repo runs where the exchange is unreachable, so it can
only prove one half of the contract ("when data is missing, say so"). This
script exists to check the other half on a machine that **can** reach
`apiv2.nobitex.ir`:

    cd server && python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
    python3 scripts/probe_live.py

For every panel on all ten tabs it answers one question: is this showing data
that really came from the exchange, is it honestly saying it cannot, or is it
blank with no explanation? The third answer is always a bug.

Two invariants, checked everywhere:

1. **Never blank.**  A panel either carries values or carries a sentence with
   `data-unavailable` (or equivalent error wording). "—" alone is not a
   sentence; it is a bug that survived three sprints of screenshots.
2. **Never fabricate.** When the exchange is down, a *market* panel that shows
   a number is worse than a blank one: it is a lie. The probe treats that as a
   failure, not a success.

Exit codes:  0 = verified    1 = problems found    2 = no upstream (only the
error-state half could be verified — re-run where Nobitex is reachable).

    --selftest   prove the classifiers can tell DATA / EXPLAINED / BLANK apart
                 (needs no server and no network)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# What each panel is supposed to do
# --------------------------------------------------------------------------- #
# kind:
#   market  — value comes from the exchange; DATA when live, EXPLAINED when down
#   server  — comes from our own backend (paper book, risk caps, error log)
#   config  — static server config (weights, hashes); always DATA
#   optional— legitimately empty (no open positions, no trades yet) but must
#             still say why rather than show a bare "—"
PANELS: dict[str, list[tuple[str, str, str]]] = {
    "overview": [
        ("kEquity", "server", "portfolio value"),
        ("kToday", "server", "today P&L"),
        ("kPf", "optional", "paper profit factor"),
        ("lastPrice", "market", "last traded price"),
        ("lastChg", "market", "24h change"),
        ("candleChart", "market", "candlestick chart"),
        ("obRows", "market", "order book"),
        ("equityChart", "server", "equity curve"),
        ("allocBox", "optional", "allocation donut"),
        ("posBody", "optional", "open positions"),
    ],
    "signals": [
        ("sigList", "market", "ranked signals"),
        ("sgFaCoverage", "optional", "fundamental coverage"),
    ],
    "fa": [
        ("faAvg", "market", "market FA score"),
        ("faVetoes", "market", "active vetoes"),
        ("faPrem", "market", "USDTIRT premium"),
        ("faList", "market", "asset scores"),
        ("newsFeed", "optional", "event feed"),
    ],
    "orders": [
        ("ordBody", "optional", "order lifecycle"),
    ],
    "performance": [
        ("perfBody", "optional", "recent signals journal"),
        ("hzCards", "optional", "horizon hit-rates"),
        ("famBody", "optional", "family attribution"),
        ("msiBadge", "optional", "model signal score"),
        ("pHit", "optional", "hit-rate KPI"),
    ],
    "desk": [
        ("mPrice", "market", "ticket price"),
        ("pNotional", "market", "ticket notional"),
        ("capWarn", "optional", "ticket risk note"),
        ("queueList", "optional", "approval queue"),
        ("auditBody", "optional", "desk audit trail"),
    ],
    "risk": [
        ("rkEq", "server", "paper equity"),
        ("rkRpt", "market", "risk per trade"),
        ("rkAtr", "market", "ATR(14)"),
        ("rkDist", "market", "stop distance"),
        ("capCards", "server", "risk caps"),
        ("killList", "server", "kill switches"),
        ("auBody", "server", "control audit trail"),
    ],
    "backtest": [
        ("btGate", "optional", "promotion gate"),
        ("btChart", "optional", "equity curve"),
        ("btBody", "optional", "walk-forward windows"),
    ],
    "errors": [
        ("errBody", "server", "server error log"),
        ("errTotal", "server", "error total"),
        ("errStatus", "server", "log status line"),
    ],
    "settings": [
        ("setHash", "config", "config hash"),
        ("setTaWeights", "config", "TA weights"),
        ("setReadonly", "config", "read-only banner"),
    ],
}

TABS = list(PANELS)

# Persian / Arabic-Indic digits → ASCII, then strip separators.
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
# Text that is nothing but a number, a sign and a unit: "۰", "−۱٫۰۰R", "+۱٫۹۰%".
_NUM_ONLY = re.compile(
    r"^[\s\u2212+\-]*[0-9\u06F0-\u06F9][0-9\u06F0-\u06F9\u066B\u066C.,٫%٪ـ ]*$"
)

_ERROR_WORDS = re.compile(r"در دسترس نیست|امکان‌پذیر نیست|ذهنی|unreachable|unavailable", re.I)


def as_number(text: str) -> float | None:
    """Parse a UI number that may use Persian digits and ٬ separators."""
    if not text:
        return None
    s = text.translate(_DIGITS).replace("٬", "").replace(",", "").replace("−", "-")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def http_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "nobi-probe/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# In-page classifier (shared by the probe and the selftest)
# --------------------------------------------------------------------------- #
CLASSIFY_JS = r"""
(ids) => ids.map((id) => {
  const el = document.getElementById(id);
  if (!el) return {id, missing: true};
  const cs = getComputedStyle(el);
  const visible = el.offsetParent !== null && cs.display !== 'none'
                  && cs.visibility !== 'hidden';
  // <input>/<select> carry their content in .value, not in textContent — the
  // desk ticket is exactly that case and read as "blank" otherwise.
  const isField = /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
  const txt = (isField ? (el.value || '') : (el.innerText || el.textContent || ''))
              .replace(/\s+/g, ' ').trim();
  // A marker still in the DOM but hidden behind display:none is not something a
  // reader sees, so it must not count — a stale marker left behind in a hidden
  // note would otherwise excuse a panel that is in fact showing data.
  const shown = (n) => {
    const cs2 = getComputedStyle(n);
    return n.offsetParent !== null && cs2.display !== 'none' && cs2.visibility !== 'hidden';
  };
  const own = Array.from(el.querySelectorAll('[data-unavailable]')).filter(shown).length
              + ((el.hasAttribute('data-unavailable') && shown(el)) ? 1 : 0);
  // A card that says "Nobitex unreachable" once explains every tile in it —
  // that is how a reader sees it too.
  const tab = el.closest('.tab-page');
  const tabUn = tab ? Array.from(tab.querySelectorAll('[data-unavailable]')).filter(shown).length : 0;
  const card0 = el.closest('.glass');
  // A panel hidden *because* its card switched to the outage view is not blank:
  // the visible sentence beside it is the explanation.
  const cardDown = card0 ? (Array.from(card0.querySelectorAll('[data-unavailable]'))
                            .some(n => { const c2 = getComputedStyle(n);
                              return n.offsetParent !== null && c2.display !== 'none'; })) : false;
  const card = card0;
  const inCard = card ? (Array.from(card.querySelectorAll('[data-unavailable]')).filter(shown).length
                         + ((card.hasAttribute('data-unavailable') && shown(card)) ? 1 : 0)) : 0;
  // A chart is a <canvas>/<svg> with pixels in it, not text. Reading innerText
  // alone made every chart look blank even while it was drawing.
  const cv = el.querySelector('canvas, svg');
  let drawn = false;
  if (cv) {
    const r = cv.getBoundingClientRect();
    drawn = r.width > 40 && r.height > 40;
  }
  const rows = el.querySelectorAll('tr').length || el.children.length;
  const digits = (txt.match(/[0-9\u06F0-\u06F9]/g) || []).length;
  const dashOnly = txt.length > 0 && /^[\s—–\-\u2212٪%/۰-۹]+$/.test(txt);
  return {id, missing: false, visible, txt: txt.slice(0, 100), len: txt.length,
          isField, un: own, inCard, tabUn, rows, digits, dashOnly, drawn, cardDown};
})
"""


def classify(rec: dict, kind: str = "market") -> tuple[str, str]:
    """Turn one raw panel reading into (verdict, detail).

    Order matters. The explicit `data-unavailable` marker is the page's own
    contract and always wins; then real digits (an error log legitimately
    contains the word "unavailable", a table of numbers is data); only then do
    em-dashes and prose get a say.
    """
    if rec.get("missing"):
        return "MISSING", "element not in the DOM"
    if not rec.get("visible"):
        if rec.get("cardDown"):
            return "EXPLAINED", "hidden behind the card's outage notice"
    txt = rec.get("txt") or ""
    digits = rec.get("digits", 0)
    # Our own backend / static config: a card may carry an "unavailable" note for
    # a *sibling* tile (e.g. the sizing card explains its ATR rows), so a tile
    # that prints its own numbers is populated regardless.
    if kind in ("server", "config") and digits >= 2:
        return "DATA", txt[:70]
    if rec.get("un") or rec.get("inCard"):
        return "EXPLAINED", txt[:70]
    if kind == "optional" and rec.get("tabUn") and digits < 2:
        return "EXPLAINED", txt[:70]
    # A chart is a canvas with pixels, not text. Checked after the outage marker:
    # an empty chart still has axes drawn, and those pixels are not data.
    if rec.get("drawn"):
        return "DATA", "chart canvas has pixels"
    # A tile whose entire content is a number is showing a number — "۰" in an
    # error counter is data, not an empty state. The two-digit rule above needs
    # at least two digits to be safe against stray digits in prose; this does
    # not, because there is no prose.
    if _NUM_ONLY.match(txt):
        return "DATA", txt[:70]
    if digits >= 2:
        return "DATA", txt[:70]
    if rec.get("dashOnly") or rec.get("len", 0) == 0:
        return "BLANK", txt[:70] or "empty"
    if rec.get("len", 0) < 200 and _ERROR_WORDS.search(txt):
        return "EXPLAINED", txt[:70]
    # A sentence that does not mention the exchange is an empty state, not an
    # outage notice: "no backtest has been run yet" is true either way and must
    # not be mistaken for evidence that the outage was explained.
    return "EMPTY", txt[:70]


def judge(kind: str, verdict: str, live: bool) -> tuple[bool, str]:
    """Is this verdict acceptable for a panel of this kind, in this state?

    The contract has two halves and they fail in opposite directions:

    * exchange down → every panel must say so, in words. A number here is
      worse than a blank: it is either fabricated or a stale cache passed off
      as current, and this project spent a whole sprint deleting exactly that.
    * exchange up  → no panel may still claim the exchange is unreachable.
      A leftover banner is not cosmetic; it tells the reader the data is
      missing while it sits right there.
    """
    if verdict == "BLANK":
        return False, "blank — no value and no explanation"
    if verdict == "MISSING":
        return False, "panel is missing from the page"

    # A stale outage notice is a bug in *either* direction, whatever the panel.
    if live and verdict == "EXPLAINED":
        return False, "exchange is live but the panel still says it is unreachable"

    if kind == "market":
        if live:
            if verdict == "DATA":
                return True, ""
            return False, f"market panel shows no data while the exchange is live ({verdict})"
        if verdict == "EXPLAINED":
            return True, "explained (upstream down)"
        if verdict == "DATA":
            return False, ("shows a number while the exchange is unreachable — "
                           "fabricated or stale, and unlabelled")
        return False, ("hides the outage behind an empty-state message — "
                       "say the exchange is unreachable")

    if kind in ("server", "config"):
        if verdict in ("DATA", "TEXT", "EMPTY"):
            return True, ""
        return False, f"{kind} panel explains itself only by blaming the exchange ({verdict})"

    # optional
    if verdict in ("DATA", "TEXT", "EMPTY"):
        return True, ""
    if verdict == "EXPLAINED" and not live:
        return True, "explained (upstream down)"
    return False, f"unexpected {verdict}"


# --------------------------------------------------------------------------- #
# Selftest — prove the classifier is not a no-op
# --------------------------------------------------------------------------- #
def selftest(_url: str | None = None) -> int:
    """Feed the classifier known-bad and known-good panels and check its answers."""
    from playwright.sync_api import sync_playwright

    root = Path(__file__).resolve().parents[1]
    target = (root / "nobitex-mobile.html").as_uri()
    cases = [
        ("—", "BLANK", "a bare em-dash — the exact bug that shipped for weeks"),
        ("", "BLANK", "a completely empty panel"),
        ('<div data-unavailable="1">سرور نوبیتکس در دسترس نیست</div>', "EXPLAINED",
         "an honest explanation"),
        ("۸۸٬۴۱۲٬۰۰۰", "DATA", "a real price written with Persian digits"),
        ("1,234.56", "DATA", "a plain ASCII number"),
        ("در حال بارگذاری", "EMPTY",
         "an empty-state sentence that does not claim an outage"),
        ("هیچ خطایی ثبت نشده است", "EMPTY", "good news, not an outage notice"),
        ("۰", "DATA", "a single-digit counter is still a number"),
    ]
    failures = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(target, wait_until="load")
        pg.wait_for_timeout(2000)
        for html, want, why in cases:
            shown = pg.evaluate(
                """(html) => {
                    const el = document.getElementById('lastPrice');
                    // The page itself may be in its offline state and the card
                    // around this chip may carry an outage marker. That marker
                    // would decide every case before the content is even read,
                    // so clear the neighbourhood first: this tests the
                    // classifier, not the page's current mood.
                    const card = el.closest('.glass');
                    if (card) {
                      card.removeAttribute('data-unavailable');
                      card.querySelectorAll('[data-unavailable]')
                          .forEach(n => n.removeAttribute('data-unavailable'));
                    }
                    el.setAttribute('class', 'chip num');
                    el.innerHTML = html;
                    return el.textContent.trim();
                }""", html)
            got, detail = _classify_element(pg, "lastPrice")
            ok = got == want
            failures += 0 if ok else 1
            print(f"  [{'PASS' if ok else 'FAIL'}] want {want:<9} got {got:<9} "
                  f"({shown[:24]!r}) — {why}")
            if not ok:
                print(f"         classifier detail: {detail}")
        b.close()
    print("\nclassifier selftest:", "PASS" if not failures else f"FAIL ({failures})")
    return 0 if not failures else 1


def _classify_element(pg, element_id: str) -> tuple[str, str]:
    rec = pg.evaluate(CLASSIFY_JS, [element_id])[0]
    return classify(rec, "market")


# --------------------------------------------------------------------------- #
# The probe
# --------------------------------------------------------------------------- #
def preflight(base: str) -> dict:
    """Ask the backend what it knows before touching the browser."""
    out: dict = {"base": base, "reachable": False, "upstream_live": False}
    try:
        health = http_json(f"{base}/api/health")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["reachable"] = True
    out["health"] = health

    def get(path: str) -> dict:
        try:
            return http_json(f"{base}{path}", timeout=60)
        except Exception as e:                                    # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}", "detail": str(e)[:120]}

    uni = get("/api/universe")
    sig = get("/api/signals?tf=15m")
    can = get("/api/candles/BTCIRT?tf=15m&count=120")
    out["universe"] = uni
    out["signals"] = sig
    out["candles"] = can
    out["upstream_live"] = bool(
        not uni.get("unavailable") and (uni.get("symbols") or [])
        and not sig.get("unavailable") and (sig.get("scored") or [])
        and (can.get("rows") or [])
    )
    return out


def run_probe(base: str, wait_s: float, json_path: str | None, md_path: str | None) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed:\n"
              "  pip install playwright && python -m playwright install chromium",
              file=sys.stderr)
        return 2

    pf = preflight(base)
    print("Nobi live probe ·", base)
    if not pf["reachable"]:
        print(f"  server unreachable: {pf.get('error')}")
        print("  start it first: cd server && "
              "python3 -m uvicorn main:app --host 0.0.0.0 --port 8000")
        return 2

    live = pf["upstream_live"]
    uni_n = len((pf["universe"] or {}).get("symbols") or [])
    sig_n = len((pf["signals"] or {}).get("scored") or [])
    can_n = len((pf["candles"] or {}).get("rows") or [])
    if live:
        print(f"  upstream: LIVE · universe {uni_n} · scored {sig_n} · candles {can_n}")
    else:
        detail = ((pf["universe"] or {}).get("detail")
                  or (pf["signals"] or {}).get("detail") or "no data")
        print(f"  upstream: DOWN ({str(detail)[:80]})")
        print("  → verifying the error-state half only. Re-run where Nobitex is reachable.")

    rows: list[dict] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 950})
        js_errors: list[str] = []
        pg.on("pageerror", lambda e: js_errors.append(str(e)[:160]))
        pg.goto(base, wait_until="load")

        # Wait for the first scan to settle instead of guessing a delay.
        for _ in range(int(wait_s * 2)):
            done = pg.evaluate(
                "() => !!(window.live && window.live.lastScanAt)")
            if done:
                break
            pg.wait_for_timeout(500)
        pg.wait_for_timeout(1500)

        for tab in TABS:
            try:
                pg.click(f'[data-tab="{tab}"]', timeout=5000)
            except Exception:                                     # noqa: BLE001
                rows.append({"tab": tab, "panel": "(tab)", "kind": "-",
                             "verdict": "MISSING", "ok": False,
                             "detail": "tab button could not be clicked"})
                continue
            pg.wait_for_timeout(700)
            ids = [pid for pid, _k, _d in PANELS[tab]]
            for (pid, kind, label), rec in zip(PANELS[tab], pg.evaluate(CLASSIFY_JS, ids)):
                verdict, detail = classify(rec, kind)
                ok, why = judge(kind, verdict, live)
                rows.append({"tab": tab, "panel": pid, "label": label, "kind": kind,
                             "verdict": verdict, "ok": ok,
                             "detail": why or detail})

        cross = cross_checks(pg, pf) if live else []
        b.close()

    # ---------------- report ----------------
    print()
    print(f"{'tab':<12}{'panel':<16}{'expect':<9}{'actual':<11}{'':<4}detail")
    print("─" * 96)
    for r in rows:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{r['tab']:<12}{r['panel']:<16}{r['kind']:<9}{r['verdict']:<11}{mark} {r['detail'][:52]}")

    if cross:
        print()
        print("cross-checks (UI value vs API value)")
        print("─" * 96)
        for c in cross:
            print(f"{'OK  ' if c['ok'] else 'FAIL'} {c['what']:<34} UI={c['ui']:<18} API={c['api']}")

    bad = [r for r in rows if not r["ok"]] + [c for c in cross if not c["ok"]]
    if js_errors:
        print()
        print(f"JS errors: {len(js_errors)}")
        for e in js_errors[:5]:
            print("  ", e)

    print()
    if bad:
        print(f"FAIL — {len(bad)} problem(s)")
        code = 1
    elif live:
        print(f"PASS — {len(rows)} panels verified against a live feed "
              f"({len(cross)} cross-checked against the API)")
        code = 0
    else:
        print(f"PASS (error-state only) — {len(rows)} panels all explain themselves "
              f"when the exchange is unreachable")
        code = 2

    if json_path:
        Path(json_path).write_text(json.dumps({
            "base": base, "when": datetime.now(timezone.utc).isoformat(),
            "upstream_live": live, "panels": rows, "cross_checks": cross,
            "js_errors": js_errors,
            "preflight": {k: pf.get(k) for k in ("upstream_live",)},
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print("json →", json_path)

    if md_path:
        Path(md_path).write_text(render_md(base, live, rows, cross, js_errors),
                                 encoding="utf-8")
        print("md   →", md_path)
    return code


def cross_checks(pg, pf: dict) -> list[dict]:
    """Compare what the UI prints with what the API returned.

    This is the only assertion that can distinguish a real number from a
    convincing fake: both are just text on screen until they are compared.
    """
    out: list[dict] = []

    def add(what, ui, api, ok):
        out.append({"what": what, "ui": ui, "api": api, "ok": ok})

    # 1. last price on the chart card vs the last candle close
    try:
        ui_price = as_number(pg.evaluate(
            "() => (document.getElementById('lastPrice')||{}).textContent || ''"))
        rows = (pf["candles"] or {}).get("rows") or []
        api_price = float(rows[-1]["c"]) if rows else None
        ok = (ui_price is not None and api_price is not None
              and abs(ui_price - api_price) / max(api_price, 1) < 0.01)
        add("BTCIRT last price", f"{ui_price}", f"{api_price}", ok)
    except Exception as e:                                        # noqa: BLE001
        add("BTCIRT last price", "?", "?", False)
        out[-1]["detail"] = str(e)[:80]

    # 2. portfolio value vs the paper book
    try:
        ui_eq = as_number(pg.evaluate(
            "() => (document.getElementById('kEquity')||{}).textContent || ''"))
        api_eq = float((pf.get("health") or {}).get("stats", {}).get("equity") or 0) or None
        if api_eq is None:
            rs = http_json(f"{pf['base']}/api/risk/state", timeout=30)
            api_eq = float(rs.get("equity") or 0) or None
        ok = ui_eq is not None and api_eq is not None and abs(ui_eq - api_eq) < 1
        add("paper equity", f"{ui_eq}", f"{api_eq}", ok)
    except Exception as e:                                        # noqa: BLE001
        add("paper equity", "?", "?", False)
        out[-1]["detail"] = str(e)[:80]

    # 3. the signal list must be a subset of what the API actually scored
    try:
        ui_syms = pg.evaluate(
            "() => [...document.querySelectorAll('#sigList .sig-row')]"
            ".map(r => r.getAttribute('data-sig-sym')).filter(Boolean)")
        api_syms = {s["symbol"] for s in ((pf["signals"] or {}).get("scored") or [])}
        extra = [s for s in ui_syms if s not in api_syms]
        ok = bool(ui_syms) and not extra
        add("signal rows ⊆ API scored", f"{len(ui_syms)} rows",
            f"{len(api_syms)} scored" + (f"; not in API: {extra[:3]}" if extra else ""), ok)
    except Exception as e:                                        # noqa: BLE001
        add("signal rows ⊆ API scored", "?", "?", False)
        out[-1]["detail"] = str(e)[:80]

    return out


def render_md(base, live, rows, cross, js_errors) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# گزارش پروب داده زنده — {when}", "",
        f"آدرس: `{base}` · وضعیت upstream: **{'زنده' if live else 'در دسترس نیست'}**", "",
    ]
    if not live:
        lines += ["> این اجرا فقط نیمهٔ «خطا» را بررسی کرده است. برای بررسی مسیر دادهٔ واقعی، "
                  "اسکریپت را روی ماشینی با دسترسی به نوبیتکس اجرا کنید.", ""]
    lines += ["| تب | پنل | انتظار | نتیجه | وضعیت |", "|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['tab']} | {r['panel']} | {r['kind']} | {r['verdict']} | "
                     f"{'OK' if r['ok'] else '**FAIL**'} |")
    if cross:
        lines += ["", "## مقایسهٔ UI با API", "", "| چه چیزی | UI | API | وضعیت |", "|---|---|---|---|"]
        for c in cross:
            lines.append(f"| {c['what']} | {c['ui']} | {c['api']} | "
                         f"{'OK' if c['ok'] else '**FAIL**'} |")
    if js_errors:
        lines += ["", f"## خطاهای JS ({len(js_errors)})", ""] + [f"- `{e}`" for e in js_errors[:10]]
    bad = [r for r in rows if not r["ok"]]
    lines += ["", "## جمع‌بندی", "",
              f"- پنل‌های بررسی‌شده: {len(rows)}",
              f"- مشکل‌دار: {len(bad)}"
              + (": " + ", ".join(f"`{r['tab']}/{r['panel']}`" for r in bad) if bad else "")]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--wait", type=float, default=30, help="seconds to wait for the first scan")
    ap.add_argument("--json", dest="json_path")
    ap.add_argument("--md", dest="md_path")
    ap.add_argument("--selftest", action="store_true",
                    help="verify the DATA/EXPLAINED/BLANK classifier (no network needed)")
    a = ap.parse_args()
    if a.selftest:
        return selftest(None if a.url.startswith("http") else a.url)
    return run_probe(a.url.rstrip("/"), a.wait, a.json_path, a.md_path)


if __name__ == "__main__":
    sys.exit(main())
