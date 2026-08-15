import os, sys, re
sys.path.insert(0, '.')

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

db_url = os.getenv('DATABASE_URL', 'NOT_SET')
redis_url = os.getenv('REDIS_URL', 'NOT_SET')

safe_db = re.sub(r'://[^@]*@', '://<redacted>@', db_url)
safe_redis = re.sub(r'://[^@]*@', '://<redacted>@', redis_url)

is_pg = 'postgresql' in db_url or 'postgres' in db_url
is_sqlite = 'sqlite' in db_url

print(f"DATABASE_URL type: {'postgresql' if is_pg else 'sqlite' if is_sqlite else 'OTHER'}")
print(f"DATABASE_URL (redacted): {safe_db}")
print(f"REDIS_URL (redacted): {safe_redis}")
print(f"REDIS_HOST env: {os.getenv('REDIS_HOST', 'not_set')}")
print(f"REDIS_PORT env: {os.getenv('REDIS_PORT', 'not_set')}")
print(f"ENV mode: {os.getenv('ENV', 'not_set')}")
print(f"VYOMQUANT_MODE: {os.getenv('VYOMQUANT_MODE', 'not_set')}")
print(f"DB_POOL_SIZE: {os.getenv('DB_POOL_SIZE', '5')}")
print(f"DB_MAX_OVERFLOW: {os.getenv('DB_MAX_OVERFLOW', '3')}")
