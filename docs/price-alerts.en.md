# Conditional price and premium alerts

[فارسی](price-alerts.md) · **English**

This guide covers the ADR-0049 backend and bell management panel. Rules belong to the shared account and create no Paper/Live order or risk change. The UI integration is in local changes after the 2.4.0 tag; it is not a claim of production deployment.

## Roles and access

- `GET /api/alerts/rules` and `GET /api/alerts/events` are limited to Owner/Operator.
- Creating, changing, or deleting a rule requires that role, a session cookie, and a valid `X-CSRF-Token` header.
- Viewer receives 403. No session returns 401 and a missing/invalid CSRF token returns 403.
- No token, cookie, or Telegram credential is stored in or returned by this API.

## Bell management panel

- The bell in the canonical `index.html` is connected to `nbx-alerts.js`. Assigning a role or loading the page makes no alert request. Rules/events are read only when an Owner/Operator opens the panel or clicks Refresh; there is no separate UI polling loop.
- Viewer and anonymous users see only an access explanation and send no request to `/api/alerts/*`, even when opening the panel. Mutations consume the existing session cookie and CSRF header. A role change invalidates cached data and counts, and a late reply is discarded after the panel closes or the identity changes.
- The panel provides price/premium creation, enable/disable, and two-step deletion. Selecting premium locks the symbol to USDTIRT. Deleting a rule retains its historical event snapshots in the event list.
- Baseline/arm/disarm state, last value and observation time, previous→new crossing values, and `sent/failed/pending` delivery are displayed separately. The blue badge is only the enabled-rule count after a successful read, not an unread-event count or a new-notification claim; the previous fixed red dot was removed.

## Rule and evaluation

Example creation body:

```json
{
  "symbol": "BTCIRT",
  "metric": "last",
  "operator": "above",
  "threshold": 100000
}
```

`metric` is `last` or `premium_pct`. A premium rule requires `symbol: "USDTIRT"` and uses the BTCIRT, BTCUSDT, and USDTIRT markets. `operator` is `above` or `below`. A price threshold is positive; a premium threshold ranges from -100 to 100 percent.

The first fresh observation only establishes a baseline. A false→true crossing stores an alert; staying on the same side or replaying a timestamp does not. Missing, invalid, future, or stale data disarms the rule, and the first healthy observation after an outage is again only a baseline.

With no enabled rule, the scheduler sends no additional exchange request. With an enabled rule, it reuses the canonical `/market/stats` loader. Defaults are `NBX_ALERT_TICK_S=30` and `NBX_ALERT_MAX_AGE_S=90`.

## API routes

| Method and route | Purpose |
|---|---|
| `POST /api/alerts/rules` | Create a rule; returns 201 |
| `GET /api/alerts/rules?limit=200` | List rules |
| `PATCH /api/alerts/rules/{id}` with `{"enabled": false}` | Disable/enable; re-enabling requires a new baseline |
| `DELETE /api/alerts/rules/{id}` | Delete a rule while retaining historical event snapshots |
| `GET /api/alerts/events?limit=50` | Trigger events, newest first |
| `GET /api/alerts/events?rule_id={id}` | Filter events for a rule that still exists |

An unknown id returns 404. An invalid body or rule contract returns 422.

## Local UI checks

```bash
npm run test:price-alerts
python scripts/price_alerts_layout_test.py
python scripts/price_alerts_e2e_test.py
```

The first command performs offline DOM checks against the canonical `index.html` source. The second exercises 16 Chromium layouts with local fixtures and every non-fixture request blocked; neither uses a real session, Telegram, or an order route.

The third is the real integration: a scratch FastAPI instance on loopback with temporary SQLite databases, a controlled exchange client, and a controlled Telegram sender. Owner, Operator, and Viewer log in through the real session forms; Viewer is proven to receive 403 and to make zero alert requests; rules are created, toggled, and deleted only through the bell with the real CSRF contract; the scheduler is driven with deterministic timestamps through `baseline → armed → crossing → exactly one event`, timestamp replay, a sender crash after the committed event (`pending`), stale evidence older than `ALERT_MAX_AGE_S`, and a fresh baseline after the outage. The server is then restarted with its process caches cleared and the canonical page served again, proving that rules and retained history come from SQLite. The 40 checks also assert no Paper/Live/risk/execution/order mutation and no server request outside loopback. No test-only endpoint exists; everything goes through product routes.

## Delivery and limits

The trigger is stored in `journal.db` before Telegram is called and is included in that database's backup. `delivery_*` fields distinguish three states:

- `delivery_sent: null` with `delivery_reason: "pending"`: delivery was not recorded, for example after a crash between trigger and sender;
- `delivery_sent: true`: Telegram returned success;
- `delivery_sent: false`: delivery was not confirmed and a bounded reason such as `not-configured`, `backing-off`, or `http-400` was stored.

Telegram error bodies, tokens, and request text are not stored in the event. Failed delivery is not automatically retried, preventing duplicate or ambiguous retries. Order-book-depth alerts and personal watchlists remain unimplemented. The bell panel only consumes the API; green UI tests do not prove real Telegram delivery, soak evidence, or production readiness.
