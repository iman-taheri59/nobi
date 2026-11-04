# ADR-0049 — Conditional price and premium alerts without a trading path

[فارسی](../fa/ADR-0049-هشدار-شرطی-قیمت.md) · **English**

**Status:** Accepted for the backend phase; the graphical interface is a later phase
**Date:** 2026-11-04
**Related:** ADR-0003, ADR-0011, ADR-0014, ADR-0018, and ADR-0041

## Context

Telegram currently delivers operational events and digests, while the page's alerts button is not a general alert engine. Roadmap item A5 calls for price/premium alerts, but connecting a timer directly to Telegram without freshness rules and durable state could produce repeated, late, or fabricated alerts.

## Decision

A rule contains `symbol`, `metric` (`last` or `premium_pct`), direction (`above`/`below`), and a threshold. Rules and events live in the existing `journal.db`, so the current backup/restore path includes them. Reads are limited to Owner/Operator. Creating, enabling/disabling, or deleting a rule requires an Operator/Owner session plus CSRF. Viewer receives no new operational privilege.

Evaluation uses only the current `/market/stats` snapshot and source timestamps. The additional exchange poll runs only while at least one rule is enabled; no enabled rule means no new request. Direct prices are retained for every returned market while the public universe response remains top-60. USDTIRT premium uses the three reference markets and their oldest timestamp.

The first fresh observation arms a rule without notifying. A trigger requires a false-to-true crossing; remaining above/below the threshold does not repeat it. Missing, invalid, future, or stale data disarms the rule. Recovery from an outage establishes a new baseline instead of emitting a late alert. Repeated or older timestamps cannot create a second event, and event insertion plus rule-state transition is atomic.

Trigger and delivery are separate facts. The event is stored before Telegram is called; then `sent` and a bounded reason with no external error body are recorded. A crash or delivery failure never causes a blind retry or duplicate message. Deleting a rule retains historical events with their rule snapshot.

This phase creates no Paper/Live order, risk change, execution-mode change, or Viewer privilege. The bell button and full management UI will consume this API in a later phase.

## Consequences

- Stale data can deliberately mean “no alert”; that is preferable to an untrustworthy alert.
- Failed delivery is visible on the event but is not automatically retried. Any retry policy needs its own ADR and explicit idempotency semantics.
- Rules belong to the shared account, not to per-user watchlists.
