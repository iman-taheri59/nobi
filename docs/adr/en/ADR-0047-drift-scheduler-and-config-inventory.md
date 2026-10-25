# ADR-0047 — Drift scheduler and configuration inventory

[فارسی](../fa/ADR-0047-زمان‌بند-drift-و-فهرست-پیکربندی.md) · **English**

**Status:** Accepted for 1.8.0  
**Date:** 2026-10-25

Drift scheduling is disabled by default and owner-controlled. Allowed intervals are 6, 12 and 24 hours; a startup or delayed process performs at most one due run and schedules the next interval, never replaying a backlog. The task is supervised by the server lifespan, refuses concurrent runs and records last run/error state. It performs analysis only and cannot alter configuration, risk controls or orders.

The singleton `drift_schedule` row persists enabled state, interval, window, horizon, cohort mode, actor, timestamps and last error. Configuration inventory compares signal counts per `config_hash` across equal baseline/current windows and labels continuity as continued, new or missing. Schedule mutation is owner-only with CSRF and audit; authenticated readers may inspect schedule and inventory.
