# ADR-0010: Hardware and Server Infrastructure Requirements

[فارسی](../fa/ADR-0010-نیازمندی-زیرساخت-سخت‌افزاری-و-سرور.md) · **English**

- **Status:** Proposed
- **Date:** 2026-02-14
- **Deciders:** Technical Architect, Ops Owner
- **Related ADRs:** 0001, 0002, 0009

---

## 1. Context

The system runs 24/7 unattended: data ingestion (per-candle cycles), TimescaleDB + Redis, the strategy/decision/execution pipeline, and monitoring — plus periodic CPU-heavy backtests (vectorized + event-driven). Infrastructure must be sized for **stability first, not raw power**: signal-to-order latency targets are seconds (candle-close driven), so expensive HPC is unnecessary. This ADR defines tiers per delivery phase (ADR-0001), sizing rationale, network/location constraints specific to Nobitex, and backup/DR.

## 2. Decision — Environment Tiers

### 2.1 Tier 0 — Development / Research Workstation (your own machine)
| Item | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 8–16 cores (backtests are the heaviest workload; vectorized engines scale with cores) |
| RAM | 8 GB | 16–32 GB (vectorbt + pandas across ~100 symbols × 6 timeframes) |
| Disk | 40 GB free | 100 GB NVMe SSD |
| GPU | None | None (ML phase is CPU-feasible; optional small GPU later) |
- Used for: code, backtesting, parameter research, walk-forward analysis. **Never runs live trading.**

### 2.2 Tier 1 — Paper Trading Server (Phase 1 → before live)
| Item | Minimum | Recommended |
|---|---|---|
| VPS | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disk | 40 GB SSD | 60–80 GB NVMe |
| Network | 1 IPv4 static, ~100 Mbps | same |
| SLA | 99% uptime | 99.5%+ |
- Runs: full docker-compose stack (bot, scheduler, postgres+timescale, redis, api, prometheus/grafana).
- Purpose: ≥30 days continuous operation against live data without financial risk.

### 2.3 Tier 2 — Live Production Server (Phase 3+) 
| Item | Minimum | Recommended | Rationale |
|---|---|---|---|
| CPU | 4 vCPU (modern, dedicated) | 6–8 vCPU | headroom for TA batch at candle-close spikes + light re-backtests |
| RAM | 8 GB | 16 GB | DB buffer cache growth; vector layers |
| Disk | 80 GB NVMe SSD | 120 GB NVMe | candle history (indefinite), orderbook snapshots (180d), logs |
| Network | static IPv4, 100 Mbps, low jitter | same | steady REST polling < 1 Mbps average; bursts in backfill |
| Availability | 99.5% | 99.9% + healthchecks/auto-restart | missed cycles = missed entries/stops |
| Power/DR | N/A | snapshot-backup volume, optional second standby node | RTO target < 30 min |
- **No GPU required.** Strategies are candle-polling (1m–1D), not tick-HFT.

### 2.4 Tier 3 — Optional Burst Research Node
- Instead of permanently oversizing production, run heavy historical re-backfilling and grid-search backtests on a **temporary 8–16 core VPS** (hours at a time), or on the Tier-0 workstation. Keeps production lean.

## 3. Sizing Rationale by Component (baseline footprint)

| Component | CPU share | RAM share | Notes |
|---|---|---|---|
| Bot loop (Python 3.12, pandas-ta) | 0.3–1 vCPU spikes | 1–2 GB | short spikes at candle close across tiers |
| TimescaleDB (PostgreSQL) | 0.5–1 vCPU | 2–6 GB | dominant steady consumer; NVMe I/O matters more than cores |
| Redis | negligible | 128–512 MB | hot state, token buckets |
| FastAPI + scheduler | negligible | 512 MB | control plane |
| Prometheus + Grafana | 0.1–0.3 vCPU | 1–2 GB | metrics 15s scrape, 90-day retention |
| System reserve | — | 1–2 GB | OS, journald, ssh |
| **Total** | **~2 vCPU typical / 4 spike** | **~7–12 GB** | explains Tier-1/Tier-2 numbers |

- Backtest runs are **time-boxed and off-production** by policy (see Tier 3).

## 4. Network, Location, and Latency (Nobitex-specific)

1. **Location preference:** a datacenter with strong, stable connectivity to Nobitex (`apiv2.nobitex.ir`) — typically a VPS hosted in Tehran (e.g., Iranian DCs) or a reliably peered EU VPS. Signal cadence is minutes-scale, so both are operationally fine; Tehran VPS gives lowest latency and straightforward access, EU VPS gives easier international tooling (Docker mirrors, OS updates without mirrors) — choose based on reliability experience.
2. **Static IP is required** for API-key binding / allowlisting features in the Nobitex panel and for Telegram webhook/API STABILITY.
3. **Time sync:** `chrony`/NTP with < 2s drift — the Ed25519 signature header `Nobitex-Timestamp` rejects skewed requests.
4. **Outbound-only policy:** host egress where feasible; no public DB/Redis ports; firewall (UFW) allows only 22 (SSH, IP-limited) + 443 (if exposing dashboards, preferably behind auth).
5. **Monitoring from outside:** heartbeat TCP/HTTPS + Telegram alerter; consider a tiny second **monitoring probe** (even free tier) so a full outage of the main node still notifies.

## 5. Storage Estimates

| Dataset | Growth | 1-year estimate |
|---|---|---|
| Candles (all symbols × ≤6 TFs) | ~a few MB/symbol/year | < 2 GB |
| Orderbook snapshots (top symbols, sampled) | moderate | 5–15 GB |
| Logs / metrics | 30–90d rotations | 2–6 GB |
| Backups (daily DB dump + config) | rolling | 30+ GB on separate volume/host |
→ Tier-2's 120 GB NVMe provides ample margin.

## 6. Backup & Disaster Recovery

- **Daily**: `pg_dump` (custom format) + config export, encrypted, rsync'd to a **different host** (home NAS or secondary VPS storage). Retention: 14 daily + 8 weekly.
- **Weekly**: full server snapshot (provider feature).
- **RTO target < 30 minutes**: documented restore runbook — spin up clean VPS from snapshot volume → restore DB → docker-compose up → resume; bot's startup reconciliation (ADR-0009) rebuilds order state from the exchange, so the DB is best-effort rather than strictly authoritative for open orders.
- **Config-as-code**: secrets re-injected from password manager; no manual snowflake steps.

## 7. Options Comparison

| Option | Verdict | Reason |
|---|---|---|
| All-in-one 4vCPU/16GB VPS (docker-compose) | ✅ Recommended v1 | fits all components with headroom, simple ops |
| Managed cloud DB + app split | Later | higher cost/complexity; consider at multi-bot scale |
| Home server + UPS | ❌ for live | residential power/internet risk unacceptable with capital at risk (fine as backup target) |
| Per-service micro VMs | ❌ v1 | cost/ops overhead without benefit |
| GPU server | ❌ | no workload needs it in phases 0–3 |

## 8. Cost Estimate (indicative)

| Tier | Indicative monthly cost |
|---|---|
| Tier 0 (existing machine) | — |
| Tier 1 paper VPS (2–4 vCPU / 4–8 GB / 60 GB) | roughly a mid-range VPS (≈ $10–20/mo EU equivalent / domestic Iran VPS at equivalent RR levels) |
| Tier 2 production (4–6 vCPU / 16 GB / 120 GB NVMe) | ≈ $25–40/mo EU equivalent or domestic counterpart |
| Tier 3 burst (8–16 cores by the hour) | negligible (billed per hour of use) |
| Monitoring probe (optional) | free-tier capable |

Deploy in stages: pay only Tier-1 until paper gates pass; upgrade to Tier-2 at go-live.

## 9. Consequences

**Positive:** production stays cheap (< cost of one decent trade per month), scaling is additive (RAM/CPU bump or burst node), DR is scripted, and network choices respect Nobitex's realities.

**Negative:** single-node production is a concentrated failure point — mitigated by snapshots, runbook, external probe, and the bot's idempotent restart/reconciliation design; full HA active-active is deferred (unjustified at this scale).
