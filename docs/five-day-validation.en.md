# Preparing a five-day validation window — 28 October 2026

[فارسی](five-day-validation.md)

## Evidence, not assumptions

The user's production service is v1.10.4. All three reported systemd units are
active; the inspected run had NRestarts=0, 3.2 GiB available RAM and disk use of
12%/1%. The latest health report has 624 successful requests out of 627 and a
DISABLED scheduled scan loop with zero runs. These are process-lifetime counters,
not daily availability or profitability. NTP reports synchronized. The earlier
+492-second browser/public-health difference has not been conclusively attributed
to client clock skew versus a cached public response.

Five proposals sourced from 07:15 candles expired at 07:30. Their zero-TTL
creation was reproduced with both reported clocks. The backend/frontend fix
and tests are provided in 2.0.0; installation on the user's server remains a
separate, verified operation.

## 1. Opt into scans independent of a browser

Upload the standalone `scripts/background_scan.py` as
`/root/nobi-background-scan.py` on the existing server. Run as root:

```bash
python3 /root/nobi-background-scan.py --apply --confirm ENABLE-BACKGROUND-SCAN
```

This briefly restarts **nobi**, not the operating system. It changes ONLY
`NBX_SCAN_LOOP_S=300` and `NBX_SCAN_LOOP_TF=15m` in the standard environment
file, first making a private backup under `/root/`. No databases, positions,
logs, execution modes, risk caps or Live settings are reset. It refuses a
non-semi default, auto overrides, enabled Live configuration or an unreadable/
disabled journal. Initial apply/verification failure restores the prior environment
contents and attempts a service restart; inspect health after any failure.

The interval is 300 seconds BETWEEN cycles; scan duration is additional. This is
a conservative starting choice, not proof that the API budget is sufficient.
Watch refusals, 429s, scan durations and candle freshness. Reapplying an already
effective identical configuration does not restart the service again.

After success, close every dashboard tab on desktop AND mobile. Wait about
15–20 minutes (longer if scans are slow), then run:

```bash
python3 /root/nobi-background-scan.py --check
```

This observes health/mode and aggregated journal counts with SQLite `mode=ro`;
it deliberately never calls `/api/signals`, which could itself trigger scans.
Output excludes keys, cookies, prices and account balances. Send the printed
JSON, NOT the private `.env` backup. The baseline is stored privately in
`/root/nobi-background-scan-baseline.json`.

Require advancing scheduled runs AND completed durable cycles, and advancing
journal evidence across source-candle boundaries. Runs alone are insufficient.
v1.10.4 health lacks scheduled-scan freshness detail; the local update adds it.
The newest single journal row does not prove freshness for every market.

## 2. Upgrade queue code only after release

Do not install the queue fix by replacing HTML alone: frontend and backend must
upgrade together using the verified 2.0.0 release. Old QUEUE submissions without
server approval metadata deliberately fail closed; reload old tabs after the
upgrade. Ordinary manual-ticket and preview contracts retain their behavior.

Tests cover closed/open candles, timestamp units, stale and wrong-timeframe data,
fast/slow browser clocks, suspended tabs, refresh without renewal, changed mode/
signal, expiry during price fetching and duplicate/concurrent/reloaded approvals.

## 3. Start the formal window only after verification

Keep previous data as a preliminary period with unknown coverage. Never erase
logs to make a baseline look clean. Record an explicit UTC start only after
browser-independent scanning, fresh eligible samples, durable evidence and the
queue/Paper workflow have been verified. The activation tool intentionally NEVER
announces the formal five-day window has started.

At least five days/30 qualified observations is a project evidence gate, not
proof of profitability or permission for Live trading. Counterfactual signal
outcomes and actual Paper trades must be reported separately. Service reliability
and strategy quality are separate evaluations too.

Scans now exclude unfinished candles BEFORE scoring, so a partial candle is not
relabelled as a completed-candle signal merely because time passed. Score formulae
are unchanged, but rankings may differ when the prior input included a forming bar.
