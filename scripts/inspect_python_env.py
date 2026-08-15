import sys
print("Python executable:", sys.executable)
print("Python version:", sys.version)

for mod in ["os", "re", "json", "asyncio", "uuid", "decimal", "datetime", "hashlib", "dataclasses", "typing", "sqlalchemy", "psycopg2"]:
    try:
        __import__(mod)
        print(f"  {mod}: OK")
    except Exception as e:
        print(f"  {mod}: FAILED ({e})")
