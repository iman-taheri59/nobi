# Out-of-sample evidence — the matrix and the archive

[فارسی](evidence.md) · **English**

This page explains what "evidence" means in this repository, where it is stored, and
**what it cannot prove**. The raw numbers live in `docs/evidence/`, not in prose, so that
nobody can quote a table without also picking up its provenance label.

## 1) The question

ADR-0021 asks: **"would a higher score threshold have been better out-of-sample?"** The
answer comes from a paired comparison (`server/adaptive_evidence.py`): identical windows,
identical parameter grid, identical costs, and **one variable** (`long_min`/`neutral_min`).

## 2) Running it

```bash
# reference feed (at a development desk; can never certify a strategy)
python3 scripts/evidence_matrix.py --factor 1.25

# the venue — the only run that can carry weight in an ADR-0021 decision
python3 scripts/evidence_matrix.py --source nobitex --symbols BTCIRT ETHIRT SOLIRT XRPIRT
```

Every run writes one file: `docs/evidence/adaptive-evidence-YYYY-MM-DD.json`.

## 3) What each file says about itself

| Field | Meaning |
|---|---|
| `provenance.label` | `REFERENCE_DATA` or `VENUE_DATA` |
| `provenance.promotable` | `true` only with Nobitex candles; never for the reference feed |
| `provenance.applied` | always `false` — this tool applies nothing |
| `cells[].verdict` | one of `favours-adaptive` / `favours-baseline` / `inconclusive` |
| `cells[].applied` / `promotable` | always `false` |

The rule the tests enforce: in a file stamped `promotable: false`, **no cell may carry a
decision-grade verdict** — all of them must stay `inconclusive`
(`server/tests/test_evidence_matrix.py`). The word `PASS` is deliberately absent from these
files too: in this repository it belongs to the promotion gate, and a stray "PASS" would be
read as a certification.

## 4) The run recorded today (2026-10-22)

- **Source:** the reference feed (`REFERENCE_DATA`) — Nobitex is unreachable from this
  sandbox (`api.nobitex.ir` did not answer), so the authoritative run could not happen here.
- **Matrix:** 4 symbols × 3 timeframes = 12 cells, 900 candles, tested factor 1.25.
- **Result:** 12 of 12 cells answered; **12 `inconclusive`** — zero verdicts. The reason is
  the same everywhere: one arm had fewer than 30 out-of-sample trades.
- **What this run *does* say:** the method works end to end, and the higher bar reduced the
  trade count in every cell — what a stricter threshold is supposed to do.
- **What this run *does not* say:** anything about which bar is better. On the reference feed
  and this much history a verdict is not available, and that is the correct output.

## 5) What it takes to get a verdict

1. Run with `--source nobitex` (from a host inside Iran, or your own VPN endpoint).
2. More history: at least 4 windows so the 30-trade sample can fill (`--bars 1500` and up).
3. Review the result in ADR-0021 — and if the verdict does favour the higher bar, the change
   is **still** a documented act: a version, a CHANGELOG entry, and the ADR's status moving
   from proposed to accepted. Not an environment variable.

> Until then, no code path writes `LONG_SCORE_MIN`, and
> `test_nothing_is_ever_applied_and_no_promotion_is_offered` keeps it that way.
