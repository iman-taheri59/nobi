# ADR-0036 — Owner operations center with a fixed allowlist

[فارسی](../fa/ADR-0036-مرکز-عملیات-مالک.md) · **English**

**Status:** Accepted — W3 implemented in 1.3.2; OS helper W3b pending
**Date:** 2026-10-25
**Related:** ADR-0016, ADR-0017, ADR-0035

## Context

The dashboard previously ran only pytest, ruff and mypy and allowed operators. The product now requires all tests, build and deployment checks without a CLI and for owners only. Accepting arbitrary commands or arguments would turn the web app into a remote shell.

## Decision

- The three existing devsuite endpoints require an owner session; API tokens and operators are insufficient.
- Operations come from fixed `Gate` tuples and use only `python` or `npm` runners; there is no `shell=True` or user command text.
- The allowlist covers pytest, ruff, mypy, ESLint, dashboard lint, broker, intrabar, display, adaptive card, icons, preflight parity, build and deploy-check.
- Only one run exists at a time, each gate has a timeout, output is bounded to a tail, and test storage is isolated.
- Build/deploy-check may honestly fail and expose their real output; UI invents no result.

## Acceptance

- operators/viewers/anonymous requests are rejected;
- unknown names return 400 and arbitrary commands cannot be expressed;
- no gate contains shell metacharacters;
- all documented CI/build commands are allowlisted;
- existing UI renders output and state from the same runner.

## Remaining

Restart/system-package installation and destructive restore require a separate non-root OS-policy helper in W3b; they do not run inside the web process.
