# B8 — WebSocket streaming: signal-scan follow-up

**Production baseline: v2.5.6 (reviewed 2026-11-03).** WebSocket support is opt-in via `NBX_WS_URL`; REST remains the default. `server/ws_execution.py` receives candle publications for subscribed symbols and `/api/candles` can merge the latest cached candle. However, signal `rescan()` still fetches candles through REST. ADR-0057 aligns its scheduled loop to bar closes; it does not make the scanner event-driven. The working 2.5.7 HTML-source change does not alter WebSocket or scan behavior.

## Remaining B8 scope

- Subscribe to candle channels for the signal universe, not only symbols subscribed for the existing stream/execution path.
- Coalesce publications and deduplicate by `(timeframe, bar_timestamp)`, so one new closed bar causes at most one universe scan—not a full scan for every roughly 10-second candle update.
- Determine closure from the bar timestamp/next bar and a short grace period. Nobitex documents candle publications at roughly 10-second intervals; the documented sample payload has no explicit closed-bar flag. See the [official candle-channel documentation](https://apidocs.nobitex.ir/websocket/websocket-candle-channel).
- Backfill after reconnect or a detected gap, and retain the current aligned timer plus REST path as a fallback if the WebSocket is disabled or stale.
- Keep scoring, gates, risk rules and journal idempotency unchanged. This is a future data-trigger/source integration, not part of v2.5.6.

The signal scanner has **not** been switched to WebSocket-triggered scans in production. This note records the planned B8 follow-up; it does not authorize or imply a deployment.
