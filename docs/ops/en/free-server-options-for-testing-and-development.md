# Free Server and Service Options for Testing and Development

> Companion to ADR-0010. Status: figures verified February 2026.
> **Short answer:** yes — development, testing, and paper trading can run almost entirely for free. Free servers are NOT recommended for live trading with real capital.

---

## 1. Free VPS Comparison (2026 state)

| Provider | Free resources | Duration | Key caveats |
|---|---|---|---|
| **Oracle Cloud (Always Free)** ⭐ | **ARM Ampere A1: 2 OCPU / 12 GB RAM** + 2 AMD micro VMs (1 GB each) + **200 GB** storage + 10 TB/mo egress | Always free | Card required; ARM allowance halved June 2026 (was 4 OCPU/24 GB); idle instances reclaimable; capacity scarcity when creating VMs |
| **Google Cloud** | 1 × e2-micro (~1 GB RAM, shared CPU) + 30 GB disk | Always free | US regions only; tiny egress (1 GB) |
| **AWS** | ~$100–$200 credits for new accounts | ~6 months | 12-month free tier removed for newer accounts; credits only |
| **Azure** | Initial credit + some services | 12 months | Temporary like AWS |
| **Render / Railway / Fly.io** | Small services with usage caps | Ongoing | **Sleep/spin-down on inactivity → unsuitable for the 24/7 scheduler** |

### Fit for this project
- Tier-1 (paper trading, ADR-0010) needs 2 vCPU / 4 GB → **Oracle Free (2 OCPU / 12 GB) covers it with headroom** and is the only truly operational free option.
- GCP e2-micro with 1 GB RAM can't host the whole stack (Postgres + Redis + Python); fine only as a monitoring probe.
- No permanently free Iranian (domestic) VPS exists; domestic providers offer only short trials.

## 2. Recommended Free Stack by Component

| Project component | Free option | Notes |
|---|---|---|
| **Main node (bot + scheduler + DB + Redis + Grafana)** | Oracle Cloud Always Free (docker-compose) | Full Tier-1 stack on one ARM machine |
| Cloud dev environment | GitHub Codespaces (monthly free hours) | Supplements/replaces local machine |
| CI/CD | GitHub Actions (free minutes) | lint, tests, adapter contract tests |
| Heavy backtests / research | Google Colab / Kaggle Notebooks (free) | Effectively makes ADR-0010 Tier 3 free |
| Managed Postgres (optional) | Neon / Supabase free tier | Light dev use; Supabase also supports the TimescaleDB extension — but keep the DB next to the bot in production |
| Managed Redis (optional) | Upstash free tier | Light dev; daily cap insufficient for operations |
| Off-host monitoring probe | UptimeRobot / Healthchecks.io (free) | Bot heartbeat + outage alert for the main node |
| Backup storage | Personal storage / free cloud (~15 GB) | Target for daily DB dumps |

## 3. Project-Specific Caveats (read carefully)

1. **ARM64 architecture:** Oracle A1 is ARM. All core dependencies (Python, pandas, numpy, Redis, TimescaleDB, Grafana) ship ARM64 builds — no blocking issues; compile `TA-Lib` from source. Run an ARM Docker-build smoke test before committing to the platform.
2. **Nobitex geography:** all free VPSs are located outside Iran. Before relying on them, test connectivity to `apiv2.nobitex.ir` from the chosen node (latency + stability + behavior at peak hours). For minute-scale trading cycles, overseas latency is usually acceptable, but measure with a simple `/market/stats` script over several days first.
3. **Static IP & reclaim:** Oracle may reclaim idle instances, and ephemeral IPs change on stop/start. For testing this is fine; use a **Reserved Public IP** (within the free allowance) if you bind a Nobitex API key to an IP — only for test/paper keys anyway.
4. **Key security policy:** **never** place real TRADE-scoped keys on free services. Test keys with micro balances or paper mode only.
5. **24/7 reliability:** free PaaS (Render/Railway) sleeps on inactivity and corrupts candle scheduling → only Oracle-style always-on VPS for the bot; other free services for auxiliary parts.
6. **Backups:** even in free environments, ADR-0009 holds — daily DB dump to an off-node destination (free cloud storage or home NAS), since free machines carry no persistence guarantee.

## 4. Decision & Operational Recommendation

- ✅ **Development and testing (Tier 0/1):** fully feasible for free. Recommended combo: **Oracle Free as main node + GitHub Actions for CI + Colab/Kaggle for backtests + UptimeRobot probe.**
- ✅ **Paper trading (≥30 days on live data):** feasible on Oracle Free; produces the evidence needed for the live-promotion gate.
- ⚠️ **Before go-live:** final latency/stability check from the free location; if unsatisfactory, only the production server (≈$25–40/mo) goes paid; the rest can stay free.
- ❌ **Live trading with real capital on a free server:** not recommended (reclaim risk, no SLA, idle policies, key security). A paid VPS costs less than one execution failure.

## 5. Free Setup Checklist (Quick Start)

1. Create an Oracle Cloud account → launch VM.Standard.A1 (2 OCPU / 12 GB) → Ubuntu ARM64.
2. Assign a free Reserved IP.
3. Install Docker + docker-compose; clone repo; run `config` in `paper` mode.
4. Firewall: SSH from your IP only + free egress; no public DB/Redis ports.
5. First of all: run a Nobitex connectivity health script (hourly for 72 hours) and record results.
6. Point a free probe (UptimeRobot) at the bot's `/health` endpoint.
7. Set up a daily backup cron to an off-node destination.
