"""
core/database.py - Database engine with connection pooling.

STEP 7: OPTIMIZE DB CONNECTIONS

Updated to use connection pooling for better performance under load.
See core/database_pool.py for full implementation details.
"""

import logging
import os
import backend_app.core.safety_config

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
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    
    def get_db():
        """FastAPI yield dependency for getting DB sessions."""
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def get_db_context():
        """Context manager for direct 'with' statement usage."""
        db = SessionLocal()
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

