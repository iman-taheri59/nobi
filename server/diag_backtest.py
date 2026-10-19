"""
手动诊断 بک‌تست — اجرا کنید:
    python -m diag_backtest
خروجی با جزئیات هر متغیری که احتمال می‌رود fail رخ دهد، چاپ می‌کند.
"""
import asyncio
import traceback

from nobitex_client import NobitexClient
from backtest import run_and_save

async def main():
    print("Step 1 · connect client")
    client = NobitexClient()
    await client.connect()
    
    print("Step 2 · fetch candles (BTCIRT/1h × 400)")
    try:
        rows = await client.get_candles("BTCIRT", "1h", 400)
        print("  → ok ·", len(rows), "rows · first ts:", rows[0]["t"])
    except Exception as e:
        print("  → FAIL at fetch")
        traceback.print_exc()
        return
    
    print("Step 3 · run walk-forward backtest")
    cfg = {"initial_equity": 50000, "risk_pct": 0.75, "atr_stop_k": 2.0,
           "max_concurrent":5, "train_bars":240, "eval_bars":120}
    try:
        res = run_and_save("BTCIRT", "1h", rows, cfg)
        print("  → ok · windows:", res["windows"])
        print("  → aggregate:", res["aggregate"])
    except Exception as e:
        print("  → FAIL at backtest engine")
        traceback.print_exc()
    
    await client.aclose()

asyncio.run(main())
