import asyncio
import websockets
import time

WS_URL = "ws://127.0.0.1:8062/ws/telemetry"
CONCURRENT_CLIENTS = 500


async def simulate_bot(bot_id):
    try:
        async with websockets.connect(WS_URL) as ws:
            for i in range(10):  # Send 10 messages per bot
                await ws.send(f"Bot-{bot_id}-Tick-{i}")
                await ws.recv()
                await asyncio.sleep(0.05)  # HFT speed simulation
    except Exception:
        pass


async def main():
    print(f"🔥 Launching Firehose: {CONCURRENT_CLIENTS} bots connecting...")
    start = time.time()
    await asyncio.gather(*[simulate_bot(i) for i in range(CONCURRENT_CLIENTS)])
    print(
        f"✅ Test Complete: {CONCURRENT_CLIENTS} bots handled in {time.time() - start:.2f}s"
    )


if __name__ == "__main__":
    asyncio.run(main())
