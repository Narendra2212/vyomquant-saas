import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()
EMAIL = os.getenv("TEST_USER_EMAIL")
PASSWORD = os.getenv("TEST_USER_PASSWORD")
URL_BASE = "http://127.0.0.1:8019/api"


async def create_and_deploy(client, headers, name, symbol, timeframe, indicators):
    # 1. Create the Blueprint
    payload = {
        "name": name,
        "symbol": symbol,
        "timeframe": timeframe,
        "indicators": indicators,
        "exchange_id": "binance",
        "buy_logic": {"type": "crossover", "indicator": indicators[0]},
        "sell_logic": {"type": "crossunder", "indicator": indicators[0]},
        "risk": {"max_drawdown": 5.0},
    }

    print(f"🛠️ Building Blueprint: {name} on {symbol}...")
    resp = await client.post(f"{URL_BASE}/strategies/", json=payload, headers=headers)

    if resp.status_code != 200:
        print(f"❌ Failed to create {name}: {resp.text}")
        return

    strategy_id = resp.json()["id"]
    print(f"✅ Created {name} [ID: {strategy_id}]")

    # 2. Deploy the Bot to the FleetManager
    print(f"🚀 Deploying Bot: {name}...")
    deploy_resp = await client.post(
        f"{URL_BASE}/strategies/{strategy_id}/deploy", json={}, headers=headers
    )

    if deploy_resp.status_code == 200:
        print(f"🟢 Bot {name} deployed successfully!")
    else:
        print(f"🔴 Deployment failed for {name}: {deploy_resp.text}")


async def main():
    async with httpx.AsyncClient() as client:
        # Auth
        print("🔐 Authenticating...")
        login = await client.post(
            f"{URL_BASE}/auth/signin", json={"email": EMAIL, "password": PASSWORD}
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # The 3 Distinct Strategies
        strategies = [
            ("RSI Scalper", "BTC/USDT", "5m", ["RSI_14"]),
            ("MACD Trend", "ETH/USDT", "1h", ["MACD_12_26_9"]),
            ("Bollinger Breakout", "SOL/USDT", "15m", ["BBands_20_2"]),
        ]

        # Deploy them concurrently
        tasks = []
        for strat in strategies:
            tasks.append(
                create_and_deploy(
                    client, headers, strat[0], strat[1], strat[2], strat[3]
                )
            )

        await asyncio.gather(*tasks)
        print(
            "\n🎉 Modularity Test Complete! Check Terminal 2 logs for FleetManager activity."
        )


if __name__ == "__main__":
    asyncio.run(main())
