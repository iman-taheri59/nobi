# Analysis and review reports — English index

This file indexes the English versions of the project/live-site analysis reports produced during the 2026-11-02 review cycle.

| English report | Persian original | Scope |
|---|---|---|
| [nobi-review.en.md](nobi-review.en.md) | [nobi-review.md](nobi-review.md) | Repository/code review of `nobiTrader` at `2.1.8`, with offline reproductions. |
| [capoy-review.en.md](capoy-review.en.md) | [capoy-review.md](capoy-review.md) | Public, unauthenticated review of `https://capoy.ir`. |
| [capoy-auth-review.en.md](capoy-auth-review.en.md) | [capoy-auth-review.md](capoy-auth-review.md) | Local isolated auth/role/CSRF/session review. |
| [capoy-owner-probe-guide.en.md](capoy-owner-probe-guide.en.md) | [capoy-owner-probe-guide.md](capoy-owner-probe-guide.md) | Owner-run, read-only live probe guide. |
| [nobi-desk-queue-review.en.md](nobi-desk-queue-review.en.md) | [nobi-desk-queue-review.md](nobi-desk-queue-review.md) | Approval-queue `execution-rr-too-low` analysis and HOTIRT arithmetic. |
| [capoy-owner-probe-analysis.en.md](capoy-owner-probe-analysis.en.md) | [capoy-owner-probe-analysis.md](capoy-owner-probe-analysis.md) | Analysis of the uploaded live owner-probe JSON from `capoy.ir`. |

## Repository analysis catalogues

| English catalogue | Persian original | Scope |
|---|---|---|
| [Technical-analysis capabilities](docs/analysis/technical-analysis-capabilities.en.md) | [فارسی](docs/analysis/technical-analysis-capabilities.md) | TA indicator catalogue and the implementation-status table reconciled with `server/ta_engine.py`. |
| [Fundamental-analysis capabilities](docs/analysis/fundamental-analysis-capabilities.en.md) | [فارسی](docs/analysis/fundamental-analysis-capabilities.md) | FA metric catalogue and the implementation-status table reconciled with `server/fa_engine.py`. |

Notes:

- These reports are historical analyses. Unless explicitly stated otherwise, they describe the reviewed `2.1.8` state.
- Local remediation for cookies/security headers, global drift cohorts, and the approval-queue execution preview was implemented in `2.1.9`; comment/docstring Englishization, the English analysis catalogues, and these English report versions are tracked in `2.1.10`.
- The older date-stamped reviews under `docs/reviews/` remain untranslated snapshots by design; this index covers the current review-cycle reports and the two analysis catalogues.
- No report authorizes deployment, real orders, or Live/Auto changes. Live `capoy.ir` changes only after the operator installs the updated configuration/application and restarts/reloads the relevant services.
