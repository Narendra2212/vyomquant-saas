"""
probe_real_infra.py — Real infrastructure connectivity probe.
Reports actual availability. No substitution allowed.
"""
import os, sys, socket, time
sys.path.insert(0, '.')

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# ─── PostgreSQL probe ────────────────────────────────────────────────────────
print("=" * 60)
print("POSTGRESQL PROBE")
print("=" * 60)

is_pg = any(x in DATABASE_URL for x in ["postgresql", "postgres"])
if not is_pg:
    print(f"DATABASE_URL type: sqlite (not PostgreSQL)")
    print("PostgreSQL: UNAVAILABLE")
    print("Reason: DATABASE_URL is sqlite:///./test.db — no real PostgreSQL configured in .env")
    pg_available = False
else:
    # Try actual connection
    import re
    try:
        import sqlalchemy
        from sqlalchemy import text, create_engine
        print(f"Attempting PostgreSQL connection...")
        engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1 as ping")).fetchone()
            print(f"  SELECT 1: {result[0]}")
            conn.execute(text("BEGIN"))
            conn.execute(text("ROLLBACK"))
            print("  ROLLBACK: OK")
        print("PostgreSQL: VERIFIED")
        pg_available = True
    except Exception as e:
        err = str(e)
        if "Connection refused" in err or "connect failed" in err.lower():
            reason = "connection refused"
        elif "password" in err.lower() or "authentication" in err.lower():
            reason = "authentication failure"
        elif "timeout" in err.lower():
            reason = "timeout"
        elif "ssl" in err.lower():
            reason = "SSL failure"
        elif "does not exist" in err.lower():
            reason = "database unavailable"
        else:
            reason = f"other: {err[:120]}"
        print(f"PostgreSQL: UNAVAILABLE")
        print(f"Reason: {reason}")
        pg_available = False

# ─── Redis probe ─────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("REDIS PROBE")
print("=" * 60)

# First: raw TCP
from urllib.parse import urlparse
parsed = urlparse(REDIS_URL)
host = parsed.hostname or "localhost"
port = parsed.port or 6379
print(f"Target: {host}:{port}")

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    s.connect((host, port))
    s.close()
    tcp_ok = True
    print(f"  TCP connection: OK")
except Exception as e:
    tcp_ok = False
    print(f"  TCP connection: FAILED ({e})")

redis_available = False
if tcp_ok:
    try:
        import redis as redis_sync
        r = redis_sync.Redis.from_url(REDIS_URL, socket_connect_timeout=5, socket_timeout=5)
        pong = r.ping()
        print(f"  PING: {pong}")

        # SET / GET / DEL
        r.set("__probe_test__", "hello_world", ex=10)
        val = r.get("__probe_test__")
        assert val == b"hello_world", f"GET mismatch: {val}"
        r.delete("__probe_test__")
        print("  SET/GET/DEL: OK")

        # SET NX EX
        ok = r.set("__probe_setnx__", "1", nx=True, ex=10)
        ok2 = r.set("__probe_setnx__", "2", nx=True, ex=10)
        assert ok is True, "First NX should succeed"
        assert ok2 is None, "Second NX should fail"
        r.delete("__probe_setnx__")
        print("  SET NX EX: OK")

        # INCRBYFLOAT
        r.delete("__probe_incr__")
        for i in range(10):
            r.incrbyfloat("__probe_incr__", 0.1)
        val_f = float(r.get("__probe_incr__"))
        expected = 1.0
        drift = abs(val_f - expected)
        r.delete("__probe_incr__")
        assert drift < 1e-9, f"INCRBYFLOAT drift: {drift}"
        print(f"  INCRBYFLOAT (10x 0.1): {val_f:.10f} drift={drift:.2e} OK")

        # TTL
        r.set("__probe_ttl__", "1", ex=5)
        ttl = r.ttl("__probe_ttl__")
        r.delete("__probe_ttl__")
        assert ttl > 0, f"TTL should be > 0, got {ttl}"
        print(f"  TTL: {ttl}s OK")

        # HASH
        r.hset("__probe_hash__", mapping={"a": "1", "b": "2"})
        hval = r.hget("__probe_hash__", "a")
        r.delete("__probe_hash__")
        assert hval == b"1"
        print("  HASH SET/GET: OK")

        # LISTS
        r.rpush("__probe_list__", "x", "y", "z")
        l = r.lrange("__probe_list__", 0, -1)
        r.delete("__probe_list__")
        assert l == [b"x", b"y", b"z"]
        print("  LIST RPUSH/LRANGE: OK")

        # SORTED SET
        r.zadd("__probe_zset__", {"a": 1.0, "b": 2.0})
        members = r.zrange("__probe_zset__", 0, -1)
        r.delete("__probe_zset__")
        assert members == [b"a", b"b"]
        print("  ZSET ZADD/ZRANGE: OK")

        # SETS
        r.sadd("__probe_set__", "m1", "m2", "m3")
        members_set = r.smembers("__probe_set__")
        r.delete("__probe_set__")
        assert len(members_set) == 3
        print("  SADD/SMEMBERS: OK")

        redis_available = True
        print("Redis: VERIFIED")

    except AssertionError as ae:
        print(f"Redis: UNAVAILABLE (assertion: {ae})")
    except Exception as e:
        err = str(e)
        if "Connection refused" in err:
            reason = "connection refused"
        elif "auth" in err.lower() or "noauth" in err.lower():
            reason = "authentication failure"
        elif "timeout" in err.lower():
            reason = "timeout"
        else:
            reason = f"other: {err[:120]}"
        print(f"Redis: UNAVAILABLE")
        print(f"Reason: {reason}")
else:
    print("Redis: UNAVAILABLE")
    print("Reason: TCP connection failed — Redis not running on localhost:6379")

print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)
print(f"PostgreSQL: {'VERIFIED' if pg_available else 'UNAVAILABLE'}")
print(f"Redis:      {'VERIFIED' if redis_available else 'UNAVAILABLE'}")
