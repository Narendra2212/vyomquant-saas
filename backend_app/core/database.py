"""
core/database.py - Database engine with connection pooling.

STEP 7: OPTIMIZE DB CONNECTIONS

Updated to use connection pooling for better performance under load.
See core/database_pool.py for full implementation details.

STEP 8: TRANSACTION ISOLATION LEVEL STRATEGY
- SERIALIZABLE for financial transactions (money-critical operations)
- READ COMMITTED for general operations (better performance)
- Transaction type-based isolation level selection
"""

import logging
import os
import backend_app.core.safety_config
from contextlib import contextmanager
from enum import Enum
from typing import Optional

# Task 13.21. `IsolationLevelUnavailable` is re-exported on purpose: a caller of
# `get_financial_db_context` needs to be able to catch it without knowing that the
# mechanism lives in a second module.
from backend_app.core.db_isolation import (  # noqa: F401
    IsolationLevelUnavailable,
    isolated_session,
)

# Import from new pooling module
try:
    from backend_app.core.database_pool import get_db as _get_db_pool
    from backend_app.core.database_pool import get_db_context as _get_db_context
    from backend_app.core.database_pool import get_db_pool

    # Re-export for backward compatibility
    get_db = _get_db_pool
    get_db_context = _get_db_context
    POOLING_AVAILABLE = True
except ImportError:
    POOLING_AVAILABLE = False
    logging.warning("[Database] Connection pooling not available, using fallback")


class TransactionType(Enum):
    """Transaction types for isolation level selection."""
    FINANCIAL = "financial"  # SERIALIZABLE - money-critical operations
    GENERAL = "general"      # READ COMMITTED - general operations
    READ_ONLY = "read_only"   # READ COMMITTED - read operations


def get_isolation_level(transaction_type: TransactionType = TransactionType.GENERAL) -> str:
    """
    Get appropriate isolation level based on transaction type.
    
    Args:
        transaction_type: Type of transaction
        
    Returns:
        Isolation level string for SQLAlchemy
    """
    if transaction_type == TransactionType.FINANCIAL:
        # SERIALIZABLE for financial transactions - maximum consistency
        return "SERIALIZABLE"
    elif transaction_type == TransactionType.READ_ONLY:
        # READ COMMITTED for read operations - better performance
        return "READ COMMITTED"
    else:
        # READ COMMITTED for general operations - balance of consistency and performance
        return "READ COMMITTED"

# Fallback for compatibility (if pooling module fails)
if not POOLING_AVAILABLE:
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.ext.declarative import declarative_base
    from sqlalchemy.orm import sessionmaker

    # Use SQLite as a minimal default (won't be used in production)
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./algo22.db")
    
    # STEP 7: Add connection pooling configuration
    if "sqlite" in DATABASE_URL.lower():
        # SQLite doesn't benefit much from pooling
        engine = create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False}
        )
    else:
        # PostgreSQL with connection pooling (calibrated against Supabase 200 pooler limit)
        POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
        MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "3"))
        POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "3600"))
        
        engine = create_engine(
            DATABASE_URL,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_timeout=POOL_TIMEOUT,
            pool_recycle=POOL_RECYCLE,
            pool_pre_ping=True,  # Verify connections before use
            pool_use_lifo=True,   # Reuse most recent connections
            echo=False,
        )
        logging.info(
            f"[Database] Pool configured: size={POOL_SIZE}, "
            f"overflow={MAX_OVERFLOW}, timeout={POOL_TIMEOUT}s"
        )
    
    # FIN-CRITICAL-001 FIX: Set isolation level based on transaction type
    # SERIALIZABLE for financial transactions, READ COMMITTED for general operations
    # This prevents race conditions for money-critical operations while maintaining performance
    # Note: isolation_level is set at transaction level via get_transactional_session()
    SessionLocal = sessionmaker(
        autocommit=False, 
        autoflush=False, 
        bind=engine
    )
    
    # Task 13.21: SessionLocalFinancial and SessionLocalReadOnly used to be defined here.
    # Both were `sessionmaker(bind=engine)` with no isolation level of any kind -- byte for
    # byte the same object as SessionLocal above, under names that promised otherwise. The
    # level is now applied per session by `db_isolation.isolated_session`, which is where
    # it has to be applied (it is a connection-checkout option, not a factory setting), so
    # the two look-alike factories are deleted rather than repaired.
    
    Base = declarative_base()
    
    def _financial_session():
        """A session verified to be at SERIALIZABLE, or an exception.

        This is the fallback-path twin of `DatabasePool.get_transactional_session`.

        Both this path and the pooled one used to issue a raw f-string
        `SET TRANSACTION ISOLATION LEVEL ...` as a statement, inside
        `except Exception: logger.warning(...)`. SQLAlchemy 2.x rejects a bare `str`
        with `ArgumentError` before it reaches a server, so the statement never ran and
        the caller got the server default -- `read committed`, measured against
        production -- while the code read as though SERIALIZABLE were in force.

        The `"sqlite" not in str(engine.url)` guard that used to wrap it is gone: the
        execution option is dialect-aware, and SQLite's dialect accepts SERIALIZABLE
        (it is SQLite's own default), so the SQLite lane keeps working without the
        control being skipped behind a dialect test.
        """
        return isolated_session(
            SessionLocal, engine, get_isolation_level(TransactionType.FINANCIAL)
        )
    
    def _general_session():
        """A plain session for GENERAL and READ_ONLY work.

        Deliberately NOT routed through `isolated_session`. `get_isolation_level` maps
        both of these to READ COMMITTED, which is already PostgreSQL's default, so
        requesting it explicitly would buy nothing -- and would break the SQLite lane
        outright, because SQLite's dialect rejects READ COMMITTED (`ArgumentError`),
        which `isolated_session` correctly surfaces as `IsolationLevelUnavailable`.
        Asking for nothing and getting the default is the honest encoding of
        "READ COMMITTED" here; the control that was advertised and absent was
        FINANCIAL = SERIALIZABLE, and that is the one now enforced.
        """
        return SessionLocal()
    
    def get_db(transaction_type: TransactionType = TransactionType.GENERAL):
        """FastAPI yield dependency for DB sessions at the isolation level requested.

        A FINANCIAL session that cannot be proven to be SERIALIZABLE raises
        `IsolationLevelUnavailable` out of the dependency rather than yielding a
        downgraded session. FastAPI turns that into a 500, which is the correct outcome:
        a money-critical request must not be served at the wrong isolation level.
        """
        if transaction_type == TransactionType.FINANCIAL:
            db = _financial_session()
        else:
            db = _general_session()
        
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def get_db_context(transaction_type: TransactionType = TransactionType.GENERAL):
        """Context manager for direct 'with' statement usage, at the level requested.

        As with `get_db`, a FINANCIAL session that cannot be proven to be SERIALIZABLE
        raises before the block is entered instead of running the block degraded.
        """
        if transaction_type == TransactionType.FINANCIAL:
            db = _financial_session()
        else:
            db = _general_session()
        
        try:
            yield db
        finally:
            db.close()


# Re-export Base for models
if POOLING_AVAILABLE:
    from backend_app.core.database_pool import Base

# Export engine and SessionLocal for direct access
if POOLING_AVAILABLE:
    from backend_app.core.database_pool import get_db_pool

    def SessionLocal():
        """Get a DB session from the pool."""
        return get_db_pool().get_session()

    def __getattr__(name):
        if name == "engine":
            return get_db_pool().engine
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
else:
    # Already defined above in fallback
    pass


@contextmanager
def get_financial_db_context(isolation_level: Optional[str] = None):
    """The reachable entry point for money-critical work: a session VERIFIED to be at
    SERIALIZABLE.

    WHY THIS EXISTS. Before task 13.21 `TransactionType.FINANCIAL` had nowhere to go.
    The two FINANCIAL branches in this module live under `if not POOLING_AVAILABLE`, and
    `POOLING_AVAILABLE` is true in every configuration that imports at all -- so the
    exported `get_db` is `database_pool`'s, whose signature takes no arguments and which
    cannot be asked for an isolation level. `DatabasePool.get_transactional_session` had
    no callers anywhere in the tree. The control was therefore advertised by an enum,
    asserted by a test, and had no reachable caller. This function is the reachable one.

    It is a context manager rather than a FastAPI dependency because the existing
    FINANCIAL-capable `get_db` is unreachable and the pooled `get_db` takes no
    arguments; adding a parameter to the pooled dependency would change the signature
    every router in the tree depends on. A caller that wants it as a dependency can wrap
    it in one line.

    Args:
        isolation_level: overrides the default, which is
            `get_isolation_level(TransactionType.FINANCIAL)` -- SERIALIZABLE.

    Yields:
        A session whose connection reported the requested level back.

    Raises:
        IsolationLevelUnavailable: before the block is entered, if the level cannot be
            applied or cannot be proven. The block does not run degraded.
    """
    level = isolation_level or get_isolation_level(TransactionType.FINANCIAL)
    if POOLING_AVAILABLE:
        session = get_db_pool().get_transactional_session(level)
    else:
        session = isolated_session(SessionLocal, engine, level)
    try:
        yield session
    finally:
        session.close()


# Health check function
async def check_db_health():
    """Check database health including pool status."""
    try:
        from backend_app.core.database_pool import check_database_health
        return await check_database_health()
    except ImportError:
        # Fallback health check
        try:
            from sqlalchemy import text
            with get_db_context() as session:
                session.execute(text("SELECT 1"))
            return {"status": "healthy", "pooling": False}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}


logger = logging.getLogger("Database")
logger.info("[Database] Module loaded with connection pooling support")

