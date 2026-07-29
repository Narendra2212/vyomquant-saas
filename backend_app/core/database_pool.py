"""
core/database_pool.py — DATABASE CONNECTION POOLING

STEP 7: OPTIMIZE DB CONNECTIONS

GOAL: Prevent DB connection exhaustion for 500 users (≈150 active)

CONFIGURATION:
  - Pool size: 20-50 connections
  - Pool pre-ping: Verify connections before use
  - Max overflow: 10 additional connections for bursts
  - Pool recycle: 3600s (prevent stale connections)
  - Pool timeout: 30s (wait for available connection)

FEATURES:
  - Connection pooling for SQLAlchemy
  - Async connection pooling for asyncpg
  - Health checks and monitoring
  - Automatic connection recycling

USAGE:
    from backend_app.core.database_pool import get_db_pool, get_async_db_pool
    
    # Sync usage
    with get_db_pool().connect() as conn:
        result = conn.execute(query)
    
    # Async usage
    async with get_async_db_pool().acquire() as conn:
        result = await conn.fetch(query)
"""

import logging
import os
from contextlib import asynccontextmanager, contextmanager
from typing import Optional
from backend_app.core.safety_config import get_vyomquant_mode

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import QueuePool

# Base class for SQLAlchemy models
Base = declarative_base()

# Async support
try:
    import asyncpg
#     from databases import Database
    ASYNC_AVAILABLE = True
except ImportError:
    ASYNC_AVAILABLE = False

logger = logging.getLogger("DatabasePool")

# =============================================================================
# CONFIGURATION
# =============================================================================

# Pool settings (calibrated against Supabase Pooler ceiling = 200 connections)
# At max capacity (10 API replicas x 2 workers = 20 processes):
# 20 API processes x (5 pool + 3 overflow = 8) = 160 connections
# 6 background workers x (2 pool + 1 overflow = 3) = 18 connections
# Total Worst-Case Peak = 178 connections (< 200 limit, leaving 11% safety margin)
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "3"))
POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))  # 1 hour
POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

# Database URL - default to SQLite for development
_env_mode = get_vyomquant_mode("safe").lower()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    if _env_mode in ["paper", "live"]:
        raise RuntimeError("DATABASE_URL must be provided in paper/live mode. SQLite fallback disabled.")
    DATABASE_URL = "sqlite:///./algo22.db"

# Convert Supabase URL to PostgreSQL if needed
if DATABASE_URL.startswith("https://"):
    # Supabase URL format: https://project.ref.supabase.co
    # Convert to PostgreSQL: postgresql://user:pass@host:port/db
    supabase_key = os.getenv("SUPABASE_KEY", "")
    # Use Supabase PostgreSQL connection string
    DATABASE_URL = os.getenv(
        "SUPABASE_DB_URL",
        "postgresql://postgres:postgres@localhost:5432/postgres"
    )
    
    # Priority 3: Database Connection Pooling
    # Enforce connection pooler port (6543) instead of direct connection (5432)
    # for Supabase-hosted Postgres instances to prevent connection limits.
    if "supabase.com:5432" in DATABASE_URL or "supabase.co:5432" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace(":5432", ":6543")
        # Ensure pool_mode is set if not already present
        if "?" not in DATABASE_URL:
            DATABASE_URL += "?pgbouncer=true&pool_mode=transaction"
        elif "pgbouncer" not in DATABASE_URL:
            DATABASE_URL += "&pgbouncer=true&pool_mode=transaction"

# =============================================================================
# SYNC CONNECTION POOL (SQLAlchemy)
# =============================================================================

class DatabasePool:
    """
    STEP 7: Database connection pool for sync operations.
    
    Uses SQLAlchemy's QueuePool for efficient connection management.
    """
    
    _instance: Optional['DatabasePool'] = None
    _engine: Optional[Engine] = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self, database_url: Optional[str] = None):
        """Initialize the connection pool."""
        if self._initialized:
            return
        
        url = database_url or DATABASE_URL
        
        # SQLite doesn't need pooling, use basic config
        if "sqlite" in url.lower():
            self._engine = create_engine(
                url,
                connect_args={"check_same_thread": False}
            )
            logger.info("[DB Pool] SQLite engine created (no pooling needed)")
        else:
            # PostgreSQL/MySQL with connection pooling
            self._engine = create_engine(
                url,
                poolclass=QueuePool,
                pool_size=POOL_SIZE,
                max_overflow=MAX_OVERFLOW,
                pool_pre_ping=True,
                pool_recycle=3600,
                pool_timeout=30,
            )
            logger.info(
                f"[DB Pool] Engine created: size={POOL_SIZE}, "
                f"overflow={MAX_OVERFLOW}"
            )
            
        self._session_factory = sessionmaker(bind=self._engine)
        self._initialized = True
    
    def close(self):
        """Close all connections in the pool."""
        if self._engine:
            self._engine.dispose()
            logger.info("[DB Pool] Engine disposed")
            self._initialized = False
    
    @property
    def engine(self) -> Engine:
        """Get the SQLAlchemy engine."""
        if not self._initialized:
            self.initialize()
        return self._engine
    
    def get_session(self):
        """Get a database session from the pool."""
        if not self._initialized:
            self.initialize()
        
        return self._session_factory()
    
    @contextmanager
    def connection(self):
        """Context manager for database connections."""
        conn = self._engine.connect()
        try:
            yield conn
        finally:
            conn.close()
    
    def get_pool_status(self) -> dict:
        """Get current pool status."""
        if not self._engine or "sqlite" in str(self._engine.url):
            return {"type": "sqlite", "pooling": False}
        
        pool = self._engine.pool
        return {
            "type": "QueuePool",
            "size": POOL_SIZE,
            "overflow": MAX_OVERFLOW,
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "total": pool.size(),
        }
    
    def dispose(self):
        """Dispose of all connections in the pool."""
        if self._engine:
            self._engine.dispose()
            logger.info("[DB Pool] All connections disposed")


# Global singleton
db_pool = DatabasePool()


def get_db_pool() -> DatabasePool:
    """Get the global database pool instance."""
    return db_pool


def validate_pool_capacity(
    max_api_replicas: int = 10,
    workers_per_replica: int = 2,
    pool_size: int = POOL_SIZE,
    max_overflow: int = MAX_OVERFLOW,
    background_worker_processes: int = 6,
    worker_pool_size: int = 2,
    worker_max_overflow: int = 1,
    supabase_pooler_limit: int = 200,
) -> dict:
    """
    Explicitly validates total database connection pool capacity against Supabase pooler ceiling.
    
    Formula:
      API_Processes = max_api_replicas * workers_per_replica
      API_Max_Conn = API_Processes * (pool_size + max_overflow)
      Worker_Max_Conn = background_worker_processes * (worker_pool_size + worker_max_overflow)
      Total_Max_Conn = API_Max_Conn + Worker_Max_Conn
    """
    api_processes = max_api_replicas * workers_per_replica
    api_max_conn = api_processes * (pool_size + max_overflow)
    worker_max_conn = background_worker_processes * (worker_pool_size + worker_max_overflow)
    total_max_conn = api_max_conn + worker_max_conn
    headroom = supabase_pooler_limit - total_max_conn
    safety_margin_pct = (headroom / supabase_pooler_limit) * 100
    is_safe = total_max_conn <= supabase_pooler_limit

    status = {
        "max_api_replicas": max_api_replicas,
        "workers_per_replica": workers_per_replica,
        "api_processes": api_processes,
        "api_max_conn_per_proc": pool_size + max_overflow,
        "total_api_conn": api_max_conn,
        "background_worker_count": background_worker_processes,
        "total_worker_conn": worker_max_conn,
        "total_max_connections": total_max_conn,
        "supabase_pooler_limit": supabase_pooler_limit,
        "headroom": headroom,
        "safety_margin_pct": round(safety_margin_pct, 2),
        "is_safe": is_safe,
    }
    if not is_safe:
        logger.warning(
            f"CRITICAL: Database connection capacity ({total_max_conn}) "
            f"exceeds Supabase pooler ceiling ({supabase_pooler_limit})!"
        )
    else:
        logger.info(
            f"[DB Pool Audit] Total max connections: {total_max_conn}/{supabase_pooler_limit} "
            f"(Headroom: {headroom}, Margin: {round(safety_margin_pct, 1)}%)"
        )
    return status



# =============================================================================
# ASYNC CONNECTION POOL (asyncpg)
# =============================================================================

class AsyncDatabasePool:
    """
    STEP 7: Async database connection pool for high-concurrency operations.
    
    Uses asyncpg for PostgreSQL with async support.
    """
    
    _instance: Optional['AsyncDatabasePool'] = None
    _pool = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    async def initialize(self, database_url: Optional[str] = None):
        """Initialize the async connection pool."""
        if self._initialized or not ASYNC_AVAILABLE:
            return
        
        url = database_url or DATABASE_URL
        
        # Skip for SQLite
        if "sqlite" in url.lower():
            logger.info("[Async DB Pool] SQLite not supported for async pool")
            return
        
        try:
            # Parse connection string
            # postgresql://user:pass@host:port/db
            self._pool = await asyncpg.create_pool(
                url,
                min_size=POOL_SIZE // 2,  # Minimum connections
                max_size=POOL_SIZE + MAX_OVERFLOW,  # Maximum connections
                max_inactive_time=POOL_RECYCLE,
                command_timeout=60,
                server_settings={
                    'jit': 'off',
                    'application_name': 'aerora_backend'
                }
            )
            logger.info(
                f"[Async DB Pool] Created: min={POOL_SIZE // 2}, "
                f"max={POOL_SIZE + MAX_OVERFLOW}"
            )
            self._initialized = True
        except Exception as e:
            logger.error(f"[Async DB Pool] Failed to initialize: {e}")
            raise
    
    async def close(self):
        """Close the async pool."""
        if self._pool:
            await self._pool.close()
            logger.info("[Async DB Pool] Closed")
    
    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        if not self._initialized:
            await self.initialize()
        
        async with self._pool.acquire() as connection:
            yield connection
    
    async def fetch(self, query: str, *args):
        """Execute a fetch query."""
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)
    
    async def fetchrow(self, query: str, *args):
        """Execute a fetchrow query."""
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)
    
    async def execute(self, query: str, *args):
        """Execute a query."""
        async with self.acquire() as conn:
            return await conn.execute(query, *args)
    
    async def executemany(self, query: str, args_list):
        """Execute a query with multiple parameters."""
        async with self.acquire() as conn:
            return await conn.executemany(query, args_list)
    
    def get_pool_status(self) -> dict:
        """Get current pool status."""
        if not self._pool:
            return {"initialized": False}
        
        return {
            "initialized": True,
            "size": self._pool.get_size(),
            "idle_size": self._pool.get_idle_size(),
            "max_size": POOL_SIZE + MAX_OVERFLOW,
        }


# Global singleton
async_db_pool = AsyncDatabasePool()


async def get_async_db_pool() -> AsyncDatabasePool:
    """Get the global async database pool instance."""
    if not async_db_pool._initialized:
        await async_db_pool.initialize()
    return async_db_pool


# =============================================================================
# MIGRATION FROM OLD DATABASE.PY
# =============================================================================

def get_db_session():
    """
    Legacy compatibility: Get a database session.
    
    Replaces the old SessionLocal() usage.
    """
    return db_pool.get_session()


def get_db():
    """
    FastAPI dependency for database sessions.
    
    Plain generator function so inspect.isgeneratorfunction(get_db) returns True.
    FastAPI manages context entry/exit automatically per request.
    """
    session = db_pool.get_session()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def get_db_context():
    """
    Context manager for direct 'with' statement usage outside FastAPI Depends().
    """
    session = db_pool.get_session()
    try:
        yield session
    finally:
        session.close()


# =============================================================================
# HEALTH CHECK
# =============================================================================

async def check_database_health() -> dict:
    """Check database connectivity and pool health."""
    health = {
        "status": "unknown",
        "sync_pool": db_pool.get_pool_status(),
        "async_pool": async_db_pool.get_pool_status(),
    }
    
    try:
        from sqlalchemy import text
        # Test sync connection
        with db_pool.connection() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        
        health["status"] = "healthy"
        health["sync_connection"] = True
        
        # Test async connection if available
        if ASYNC_AVAILABLE and async_db_pool._initialized:
            async with async_db_pool.acquire() as conn:
                await conn.fetch("SELECT 1")
            health["async_connection"] = True
        
    except Exception as e:
        health["status"] = "unhealthy"
        health["error"] = str(e)
    
    return health


# =============================================================================
# INITIALIZATION
# =============================================================================

def initialize_pools():
    """Initialize database pools on startup."""
    db_pool.initialize()
    logger.info("[DB Pool] Initialization complete")


async def initialize_async_pools():
    """Initialize async database pools on startup."""
    if ASYNC_AVAILABLE:
        await async_db_pool.initialize()


# Auto-initialize sync pool
initialize_pools()
