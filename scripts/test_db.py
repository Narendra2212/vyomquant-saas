import sys
sys.path.insert(0, '.')

print("0. importing safety_config")
import backend_app.core.safety_config
print("1. importing backend_app.core.database")
try:
    import backend_app.core.database as db
    print("2. imported successfully")
    print("3. db.engine:", db.engine)
    print("4. db.SessionLocal():", db.SessionLocal())
except BaseException as e:
    import traceback
    print("Caught BaseException:", type(e), e)
    traceback.print_exc()
