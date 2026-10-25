# ADR-0035 — Web setup wizard and owner operations center

[فارسی](../fa/ADR-0035-ویزارد-نصب-و-مرکز-عملیات.md) · **English**

**Status:** Accepted — W1 implemented in 1.3.0; W2/W3 pending
**Date:** 2026-10-25

## Context

The current install needs CLI access to create the owner and run tests/deployment tasks. The goal is to remove coding requirements for end users without turning the web app into a remote shell.

## Decision

Work proceeds in three stages:

1. **W1 — first run:** with no users, the server creates a strong one-time token in a 0600 file and prints it to the console. The web wizard uses it to create the owner and establish a session. Claim removes the token and permanently closes bootstrap.
2. **W2 — installation settings:** data paths, mode, scan loop and reverse-proxy correctness are validated in UI and written to a non-secret settings file; secrets remain in environment/secret stores.
3. **W3 — owner operations center:** every repository task runs only from a fixed allowlist, without a shell and with schema-validated arguments. Output is redacted, bounded, timed out and audited. Arbitrary commands are never accepted.

## W1 security requirements

- public status reports only `setup_required`, never token/path;
- claim works only while `auth.any_users()==false`;
- token uses `compare_digest` and rate limiting;
- token file is 0600 and deleted after success;
- concurrent claims create only one owner;
- existing auth password policy applies;
- success establishes session and CSRF cookies;
- token is absent from DB, audit, response and post-claim logs.

## Outside W1

System package installation, systemd/nginx writes and test execution belong to W2/W3. The app never runs as root; privileged work will use a separate constrained helper.
