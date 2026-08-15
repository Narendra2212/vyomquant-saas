import sys
import traceback

sys.path.insert(0, '.')

with open("import_trace.log", "w", encoding="utf-8") as f:
    f.write("Starting import trace...\n")
    try:
        f.write("1. importing backend_app.core.safety_config\n")
        f.flush()
        import backend_app.core.safety_config
        f.write("2. importing backend_app.core.database\n")
        f.flush()
        import backend_app.core.database
        f.write("3. importing backend_app.core.metrics\n")
        f.flush()
        import backend_app.core.metrics
        f.write("4. importing backend_app.core.models.execution_record\n")
        f.flush()
        import backend_app.core.models.execution_record
        f.write("5. importing backend_app.core.execution_engine\n")
        f.flush()
        from backend_app.core.execution_engine import ExecutionEngine
        f.write("6. importing backend_app.backend.exchange_executor\n")
        f.flush()
        from backend_app.backend.exchange_executor import ExchangeExecutor
        f.write("7. testing all routers individually:\n")
        f.flush()
        routers_to_test = [
            "admin", "analytics", "auth", "billing", "dashboard", "distributed_execution",
            "exchange", "health", "health_websocket", "library", "market", "metrics", "notifications",
            "orders", "portfolio", "referral", "risk", "security", "signals", "strategies", 
            "strategy_operations", "support", "user", "dag_tasks"
        ]
        for r_name in routers_to_test:
            f.write(f"   testing router: {r_name}...\n")
            f.flush()
            __import__(f"backend_app.routers.{r_name}")
            f.write(f"   router {r_name}: OK\n")
            f.flush()

        f.write("8. importing backend_app.main (FastAPI application)\n")
        f.flush()
        import backend_app.main as main_app
        f.write("9. ALL PRODUCTION BACKEND IMPORTS AND FASTAPI APP LOADED SUCCESSFULLY!\n")
        f.flush()
    except BaseException as e:
        f.write(f"EXCEPTION: {type(e).__name__}: {e}\n")
        traceback.print_exc(file=f)
        f.flush()
print("Trace script completed. Check import_trace.log")
