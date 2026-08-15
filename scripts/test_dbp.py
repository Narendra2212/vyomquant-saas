import sys
sys.path.insert(0, '.')

print("Step 1: import backend_app.core.safety_config")
import backend_app.core.safety_config
print("Step 2: import backend_app.core.database_pool")
import backend_app.core.database_pool as dbp
print("Step 3: get_db_pool()")
pool = dbp.get_db_pool()
print("Step 4: pool.engine:", pool.engine)
print("Step 5: session:", pool.get_session())
print("ALL SUCCESS")
