import asyncio
import time

from dotenv import load_dotenv

from backend_app.backend.telemetry_engine import TelemetryEngine

load_dotenv('d:/aerora_quant_backend_updated_final1/backend_app/.env')


async def test_perf():
    tel = TelemetryEngine()
    await tel.connect()
    print("Testing performance query...")
    uid = "52384fe1-c1dd-4540-89e4-5c36dd8a2bf8"
    days = 30
    query = (
        "SELECT "  # nosec: B608
        "COUNT(*) as total_trades, "
        "SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades, "
        "SUM(pnl) as total_pnl, "
        "AVG(pnl) as avg_pnl, "
        "STDDEV(pnl) as pnl_stddev "
        "FROM executions "
        f"WHERE user_id = '{uid}' "
        f"AND timestamp > dateadd('d', -{days}, now());"
    )  # nosec: B608
    start = time.time()
    try:
        res = await tel.execute_query(query)
        print("Time:", time.time() - start)
        print("Result:", res)
    except Exception:
        import traceback
        traceback.print_exc()

asyncio.run(test_perf())
