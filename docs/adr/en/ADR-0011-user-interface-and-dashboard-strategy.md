# ADR-0011: User Interface and Dashboard Strategy

[فارسی](../fa/ADR-0011-راهبرد-رابط-کاربری-و-داشبورد.md) · **English**

- **Status:** Accepted (amended)
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Product Owner
- **Related ADRs:** 0001, 0002, 0008, 0009
- **Interactive prototype:** `index.html` (RTL Persian mockup, single-file)

---

## 1. Context

The bot trades autonomously, but humans must be able to (a) understand system state in seconds, (b) audit why a trade happened, and (c) intervene safely under stress (kill switches, panic stop). The UI is a **control room**, not an analysis lab: monitoring-first, control-second. It must be usable on desktop and mobile, support Persian (RTL) as the primary locale with English as secondary, and never expose unsafe actions accidentally.

## 2. Decision — Three Interface Surfaces

### 2.1 Surface A — Web Control Room (primary)
- **Stack:** FastAPI backend (already planned, ADR-0002) + **React SPA** (Vite, TypeScript) served statically behind the API; charts via **TradingView Lightweight Charts** (candles, equity) and **ECharts** (donuts, gauges); TailwindCSS for styling.
- **Realtime:** Server-Sent Events (SSE) for equity/position/order updates (simpler & more robust than WS for mostly one-way dashboard push; REST for actions).
- **i18n:** `fa` (RTL, Vazirmatn font) default; `en` optional. All timestamps dual-shown (Jalali + Unix).
- **RBAC-lite:** single-operator token auth in v1; IP allowlist / SSH-tunnel-only exposure option; every control action requires re-confirmation (see 2.4).

### 2.2 Surface B — Telegram Bot (field ops)
- Alerts (fills, halts, breakers, daily digest) + a **restricted command set**: `/status`, `/positions`, `/pnl today`, `/pause`, `/resume`, `/panic` (two-step).
- User-ID allowlist; sensitive actions echoed to the dashboard audit log.

### 2.3 Surface C — Grafana (infra only)
- Machine/API metrics, latency, error rates — deliberately **not** the trading UI.

## 3. Pages (Information Architecture)

| Page | Content | Design intent |
|---|---|---|
| **1. Command Center** | Equity curve, today/total PnL, open risk heat, win rate, PF, mode badge (PAPER/LIVE), open positions with live PnL, exposure allocation donut | 5-second glanceability: green/amber/red semantics |
| **2. Signals Explorer** | Ranked candidates with composite score bars + breakdown (TA/FA/LIQ), MTA alignment flags, per-symbol mini candle chart, "why-this-trade" expandable evidence | full auditability of ranking (ADR-0007) |
| **3. Fundamentals** | FA scores per asset, active vetoes (red flags) with evidence links, news feed with severity (hack/delist/unlock/...), USDTIRT premium trend | transparency of slow layer (ADR-0006) |
| **4. Orders & Trades** | Live order-state machine view (`PLANNED→...→CLOSED`), fills with slippage vs mid, history filters | reconcile UI ↔ exchange state |
| **5. Risk Controls** | Portfolio caps with progress bars, kill-switch status (daily/weekly/consecutive), per-trade sizing preview, **Panic hold-to-confirm button (3s)** | safety actions guarded, never one click |
| **6. Backtest Lab** | Run list by config hash, metric cards (PF, MDD, Sharpe...), walk-forward chart, parameter sensitivity heatmap, paper-vs-backtest comparison | research ↔ live feedback loop |
| **7. Settings** | Weights YAML viewer with diff/history, feature flags, provider health (staleness badges), API-key status (scopes shown, never the key) | config transparency without editing risk |

## 4. UX Principles

1. **State before beauty:** mode (PAPER/LIVE/PAUSED) and kill-switch armed state are always visible in the header.
2. **No accidental danger:** destructive actions are hold-to-confirm with haptic-style progress; two-step on Telegram.
3. **Numbers first:** tabular-nums typography, Persian digits, thousand separators, consistent sign coloring (profit=emerald, loss=red — neutral for zero).
4. **Dark trading theme:** deep-zinc background, neon accents, high contrast for night monitoring.
5. **Mobile-first alerts:** every critical state reachable ≤ 2 taps from lock screen via Telegram deep links to dashboard pages.
6. **Skeleton + stale badges:** any data older than N seconds shows a staleness chip — never silently wrong UI.

## 5. Security Constraints

- No public exposure by default: dashboard binds to localhost/SSH tunnel or private network; optional reverse proxy with auth.
- API keys never rendered; only scope labels (`READ`, `TRADE`, `WITHDRAW: NO`).
- Audit log page records every control action (who/when/what/result).
- Session token with short expiry + refresh; CSRF tokens for mutations.

## 6. Alternatives Considered

| Option | Verdict |
|---|---|
| Grafana as main UI | Rejected: great metrics, poor domain controls/audit UX |
| Telegram-only | Rejected: no rich views; keep as companion surface |
| Admin template (Metronic etc.) | Rejected: generic look, heavy deps; custom is leaner |
| HTMX/server-rendered | Accepted as Phase-1 fallback if SPA build effort must be deferred |
| Jupyter/Streamlit | Rejected: research tooling, not a control room |

## 7. Deliverables & Phasing

- **Phase 1:** prototype (`index.html` mockup) → FastAPI + SSE + Command Center + Risk Controls (operations-critical first).
- **Phase 2:** Signals/FA explorers, Orders page.
- **Phase 3:** Backtest Lab, Settings diffing, Telegram deep links, en locale.

## 8. Consequences

**Positive:** fast situational awareness, audit-grade explainability, safe intervention patterns, RTL/Persian-native UX.
**Negative:** SPA adds build tooling/CI surface — mitigated by phasing and the HTMX fallback.

---

## Implementation amendment — 2026-10-20: the information architecture as it stands

§3 defined seven pages; the dashboard has **ten** tabs today. The three below were added
later and should be read as part of this ADR:

| New tab | Why | Design note |
|---|---|---|
| **Performance report** (`performance`) | ADR-0012 needed its own home for MSI, hit rate, IC and calibration | with no sample, each tile says so instead of "—" |
| **Manual desk** (`desk`) | ADR-0013 defined hybrid execution and needed a human entry point | ticket + TTL approval queue |
| **Error log** (`errors`) | grid and aggregation errors were invisible without a log | each of its four buttons has a visible effect |

Two further changes not in the original text:

- **Settings is read-only** except for browser-local preferences (language, ranges).
  No key or token is ever written from the UI — that is a boundary, not a gap.
- **Mobile navigation** covers all ten tabs; the settings tab used to be unreachable
  on a phone.

ADR-0014 governs all of these: every panel has a number, a sentence, or "nothing to
show" — a bare "—" is never acceptable.
