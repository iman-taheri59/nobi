# گزارش پروب داده زنده — 2026-10-20 05:10 UTC

آدرس: `http://127.0.0.1:8010` · وضعیت upstream: **زنده**

| تب | پنل | انتظار | نتیجه | وضعیت |
|---|---|---|---|---|
| overview | kEquity | server | DATA | OK |
| overview | kToday | server | DATA | OK |
| overview | kPf | optional | EMPTY | OK |
| overview | lastPrice | market | DATA | OK |
| overview | lastChg | market | DATA | OK |
| overview | candleChart | market | DATA | OK |
| overview | obRows | market | DATA | OK |
| overview | obStats | market | DATA | OK |
| overview | equityChart | server | DATA | OK |
| overview | allocBox | optional | DATA | OK |
| overview | posBody | optional | EMPTY | OK |
| signals | sigList | market | DATA | OK |
| signals | sgFaCoverage | optional | DATA | OK |
| fa | faAvg | market | DATA | OK |
| fa | faVetoes | market | DATA | OK |
| fa | faPrem | market | DATA | OK |
| fa | faList | market | DATA | OK |
| fa | newsFeed | optional | EMPTY | OK |
| orders | ordBody | optional | EMPTY | OK |
| performance | perfBody | optional | EMPTY | OK |
| performance | hzCards | optional | EMPTY | OK |
| performance | famBody | optional | EMPTY | OK |
| performance | msiBadge | optional | EMPTY | OK |
| performance | pHit | optional | EMPTY | OK |
| desk | mPrice | market | DATA | OK |
| desk | pNotional | market | DATA | OK |
| desk | capWarn | optional | EMPTY | OK |
| desk | queueList | optional | EMPTY | OK |
| desk | auditBody | optional | EMPTY | OK |
| risk | rkEq | server | DATA | OK |
| risk | rkRpt | market | DATA | OK |
| risk | rkAtr | market | DATA | OK |
| risk | rkDist | market | DATA | OK |
| risk | capCards | server | DATA | OK |
| risk | killList | server | DATA | OK |
| risk | auBody | server | EMPTY | OK |
| backtest | btGate | optional | EMPTY | OK |
| backtest | btChart | optional | EMPTY | OK |
| backtest | btBody | optional | EMPTY | OK |
| errors | errBody | server | EMPTY | OK |
| errors | errTotal | server | DATA | OK |
| errors | errStatus | server | EMPTY | OK |
| settings | setHash | config | DATA | OK |
| settings | setTaWeights | config | DATA | OK |
| settings | setReadonly | config | EMPTY | OK |

## مقایسهٔ UI با API

| چه چیزی | UI | API | وضعیت |
|---|---|---|---|
| BTCIRT last price | 88751881937.0 | 88751826522.62979 | OK |
| paper equity | 100000.0 | 100000.0 | OK |
| signal rows ⊆ API scored | 9 rows | 9 scored | OK |

## جمع‌بندی

- پنل‌های بررسی‌شده: 45
- مشکل‌دار: 0
