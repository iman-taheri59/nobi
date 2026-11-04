# Conditional price and premium alerts

[فارسی](price-alerts.md) · **English**

This guide covers the ADR-0049 backend phase. The graphical bell panel is not implemented yet. Rules belong to the shared account and create no Paper/Live order or risk change.

## Roles and access

- `GET /api/alerts/rules` and `GET /api/alerts/events` are limited to Owner/Operator.
- Creating, changing, or deleting a rule requires that role, a session cookie, and a valid `X-CSRF-Token` header.
- Viewer receives 403. No session returns 401 and a missing/invalid CSRF token returns 403.
- No token, cookie, or Telegram credential is stored in or returned by this API.

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

## Delivery and limits

The trigger is stored in `journal.db` before Telegram is called and is included in that database's backup. `delivery_*` fields distinguish three states:

- `delivery_sent: null` with `delivery_reason: "pending"`: delivery was not recorded, for example after a crash between trigger and sender;
- `delivery_sent: true`: Telegram returned success;
- `delivery_sent: false`: delivery was not confirmed and a bounded reason such as `not-configured`, `backing-off`, or `http-400` was stored.

Telegram error bodies, tokens, and request text are not stored in the event. Failed delivery is not automatically retried, preventing duplicate or ambiguous retries. Order-book-depth alerts, personal watchlists, and the management UI are outside this phase.
