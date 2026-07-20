import asyncio
import httpx

payload = {
  "name": "Untitled Strategy",
  "nodes": [
    {
      "id": "n-0",
      "type": "input",
      "label": "CCXT Asset Feed",
      "params": {
        "symbol": "BTC/USDT",
        "timeframe": "15m",
        "start_date": "2026-05-25",
        "end_date": "2026-06-24"
      }
    },
    {
      "id": "n-1",
      "type": "indicator",
      "label": "RSI",
      "params": {
        "window": 14,
        "source": "close"
      }
    },
    {
      "id": "n-2",
      "type": "logic",
      "label": "GT",
      "params": {
        "operator": "GT",
        "threshold": 70
      }
    },
    {
      "id": "n-3",
      "type": "action",
      "label": "Sell",
      "params": {
        "action": "sell",
        "amount": 1.0,
        "order_type": "market"
      }
    }
  ],
  "edges": [
    { "source": "n-0", "target": "n-1" },
    { "source": "n-1", "target": "n-2" },
    { "source": "n-2", "target": "n-3" }
  ]
}

async def run():
    async with httpx.AsyncClient() as client:
        # Validate
        print("VALIDATE PAYLOAD:")
        print(payload)
        try:
            r_val = await client.post("http://127.0.0.1:8000/api/strategies/validate", json=payload, headers={"Authorization": "Bearer DEV_ADMIN_TOKEN_999"})
            print("\nVALIDATE RESPONSE:")
            print(r_val.status_code)
            print(r_val.json())
        except Exception as e:
            print("Validate failed", e)
            
        print("\nBACKTEST PAYLOAD:")
        print(payload)
        try:
            r_back = await client.post("http://127.0.0.1:8000/api/strategies/backtest", json=payload, headers={"Authorization": "Bearer DEV_ADMIN_TOKEN_999"})
            print("\nBACKTEST RESPONSE:")
            print(r_back.status_code)
            print(r_back.json())
        except Exception as e:
            print("Backtest failed", e)

if __name__ == "__main__":
    asyncio.run(run())
