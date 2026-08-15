import sys
import traceback

sys.path.insert(0, '.')

print("1. importing os, time...")
import os, time, random, asyncio
print("2. importing safety_config...")
from backend_app.core.safety_config import get_vyomquant_mode
print("3. importing sqlalchemy...")
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool
print("4. testing asyncpg import...")
try:
    import asyncpg
    print("   asyncpg imported successfully")
except Exception as e:
    print("   asyncpg import error:", e)

print("5. testing database_pool get_db_pool...")
try:
    import backend_app.core.database_pool as dbp
    print("   dbp imported successfully, engine:", dbp.get_db_pool().engine)
except Exception as e:
    traceback.print_exc()
