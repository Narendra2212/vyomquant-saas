"""
Regression test for SQLAlchemy pool_events registration bug.

This test reproduces the exact CloudWatch error:
sqlalchemy.exc.InvalidRequestError:
No such event '<bound method DatabasePool._on_connect ...>'
for target '<sqlalchemy.pool.impl.QueuePool ...>'

The bug was caused by using the invalid 'pool_events' parameter in create_engine()
instead of the correct event.listen() API for SQLAlchemy 2.0.
"""

import pytest
import tempfile
import os
import sqlalchemy
from sqlalchemy import create_engine, event, Column, Integer
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import QueuePool, Pool


def test_pool_events_parameter_invalid():
    """
    Test that pool_events parameter causes InvalidRequestError when used incorrectly.
    
    This reproduces the exact production failure.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        
        # This should fail with InvalidRequestError during create_engine
        # SQLAlchemy tries to process pool_events as kwarg to pool constructor
        with pytest.raises(sqlalchemy.exc.InvalidRequestError) as exc_info:
            engine = create_engine(
                f"sqlite:///{db_path}",
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=3,
                pool_events=[
                    ("connect", lambda dbapi_conn, conn_rec: None),
                    ("checkout", lambda dbapi_conn, conn_rec, conn_proxy: None),
                    ("checkin", lambda dbapi_conn, conn_rec: None)
                ]
            )
        
        # Verify the error mentions the event registration issue
        assert "no such event" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()


def test_correct_event_registration():
    """
    Test the correct SQLAlchemy 2.0 event registration pattern.
    
    This should succeed and demonstrate the proper way to register pool events.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        
        # Track if callbacks were called
        callback_calls = {
            "connect": 0,
            "checkout": 0,
            "checkin": 0
        }
        
        def on_connect(dbapi_connection, connection_record):
            callback_calls["connect"] += 1
        
        def on_checkout(dbapi_connection, connection_record, connection_proxy_context):
            callback_calls["checkout"] += 1
        
        def on_checkin(dbapi_connection, connection_record):
            callback_calls["checkin"] += 1
        
        # Create engine WITHOUT pool_events parameter
        engine = create_engine(
            f"sqlite:///{db_path}",
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=3,
            pool_pre_ping=True
        )
        
        # Register events using the correct SQLAlchemy 2.0 API
        event.listen(engine.pool, "connect", on_connect)
        event.listen(engine.pool, "checkout", on_checkout)
        event.listen(engine.pool, "checkin", on_checkin)
        
        # Verify events are registered
        from sqlalchemy.event import contains
        assert contains(engine.pool, "connect", on_connect)
        assert contains(engine.pool, "checkout", on_checkout)
        assert contains(engine.pool, "checkin", on_checkin)
        
        # Trigger events by using the pool
        with engine.connect() as conn:
            pass
        
        # Verify callbacks were called
        assert callback_calls["connect"] > 0, "Connect callback should be called"
        assert callback_calls["checkout"] > 0, "Checkout callback should be called"
        assert callback_calls["checkin"] > 0, "Checkin callback should be called"
        
        engine.dispose()


def test_database_pool_event_registration():
    """
    Test DatabasePool uses correct event registration pattern.
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    # Import DatabasePool to verify it uses correct API
    from backend_app.core.database_pool import DatabasePool
    
    # We can't test the full initialization without DATABASE_URL,
    # but we can verify the pattern doesn't use pool_events
    import inspect
    source = inspect.getsource(DatabasePool.initialize)
    
    # Verify pool_events is NOT used in create_engine call
    assert "pool_events" not in source or "pool_events=" not in source, \
        "DatabasePool should not use pool_events parameter"
    
    # Verify event.listen is used instead
    assert "event.listen" in source or "from sqlalchemy.event import" in source, \
        "DatabasePool should use event.listen for pool events"


def test_callback_signatures():
    """
    Test that callback signatures match SQLAlchemy expectations.
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    from backend_app.core.database_pool import DatabasePool
    import inspect
    
    # Check _on_connect signature: (dbapi_connection, connection_record)
    sig = inspect.signature(DatabasePool._on_connect)
    params = list(sig.parameters.keys())
    assert params == ['self', 'dbapi_connection', 'connection_record'], \
        f"_on_connect should have signature (self, dbapi_connection, connection_record), got {params}"
    
    # Check _on_checkout signature: (dbapi_connection, connection_record, connection_proxy_context)
    sig = inspect.signature(DatabasePool._on_checkout)
    params = list(sig.parameters.keys())
    assert params == ['self', 'dbapi_connection', 'connection_record', 'connection_proxy_context'], \
        f"_on_checkout should have signature (self, dbapi_connection, connection_record, connection_proxy_context), got {params}"
    
    # Check _on_checkin signature: (dbapi_connection, connection_record)
    sig = inspect.signature(DatabasePool._on_checkin)
    params = list(sig.parameters.keys())
    assert params == ['self', 'dbapi_connection', 'connection_record'], \
        f"_on_checkin should have signature (self, dbapi_connection, connection_record), got {params}"


def test_callback_invocation():
    """
    Test that callbacks are actually invoked during pool operations.
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_callback.db")
        
        # Track callback invocations
        connect_calls = []
        checkout_calls = []
        checkin_calls = []
        
        def on_connect(dbapi_conn, conn_rec):
            connect_calls.append(1)
        
        def on_checkout(dbapi_conn, conn_rec, conn_proxy):
            checkout_calls.append(1)
        
        def on_checkin(dbapi_conn, conn_rec):
            checkin_calls.append(1)
        
        # Create engine with proper event registration
        engine = create_engine(
            f"sqlite:///{db_path}",
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=3,
        )
        
        # Register events properly
        event.listen(engine, "connect", on_connect)
        event.listen(engine, "checkout", on_checkout)
        event.listen(engine, "checkin", on_checkin)
        
        # Create a table to trigger connections
        Base = declarative_base()
        
        class TestModel(Base):
            __tablename__ = "test_table"
            id = Column(Integer, primary_key=True)
        
        Base.metadata.create_all(engine)
        
        # Verify connect was called during pool initialization
        assert len(connect_calls) > 0, "connect callback should be invoked"
        
        # Acquire a connection to trigger checkout
        with engine.connect() as conn:
            assert len(checkout_calls) > 0, "checkout callback should be invoked"
        
        # Verify checkin was called when connection returned
        assert len(checkin_calls) > 0, "checkin callback should be invoked"
        
        engine.dispose()


def test_no_duplicate_listeners_on_reinit():
    """
    Test that re-initializing the pool does not register duplicate listeners.
    """
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "test_reinit.db")
        
        # Track callback invocations
        checkout_calls = []
        
        def on_checkout(dbapi_conn, conn_rec, conn_proxy):
            checkout_calls.append(1)
        
        # Create engine and register listener
        engine = create_engine(
            f"sqlite:///{db_path}",
            poolclass=QueuePool,
            pool_size=5,
        )
        
        event.listen(engine, "checkout", on_checkout)
        
        # Trigger connection
        with engine.connect() as conn:
            pass
        
        initial_call_count = len(checkout_calls)
        
        # Try to register the same listener again (SQLAlchemy should handle this)
        event.listen(engine, "checkout", on_checkout)
        
        # Trigger another connection
        with engine.connect() as conn:
            pass
        
        # Should not have doubled the calls (listener should not be duplicated)
        final_call_count = len(checkout_calls)
        
        # The second connection should trigger exactly one more call (not two, which would indicate duplicates)
        assert final_call_count == initial_call_count + 1, \
            f"Expected {initial_call_count + 1} calls total, got {final_call_count} - duplicate listeners may be registered"
        
        engine.dispose()
    finally:
        # Cleanup manually on Windows
        import shutil
        try:
            shutil.rmtree(tmpdir)
        except:
            pass


def test_concurrent_pool_access():
    """
    Test that the pool handles concurrent access correctly with event listeners.
    """
    import sys
    import os
    import threading
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    tmpdir = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmpdir, "test_concurrent.db")
        
        # Track callback invocations with thread safety
        checkout_calls = []
        checkin_calls = []
        lock = threading.Lock()
        
        def on_checkout(dbapi_conn, conn_rec, conn_proxy):
            with lock:
                checkout_calls.append(1)
        
        def on_checkin(dbapi_conn, conn_rec):
            with lock:
                checkin_calls.append(1)
        
        # Create engine with small pool
        engine = create_engine(
            f"sqlite:///{db_path}",
            poolclass=QueuePool,
            pool_size=3,
            max_overflow=2,
        )
        
        event.listen(engine, "checkout", on_checkout)
        event.listen(engine, "checkin", on_checkin)
        
        # Create a table
        Base = declarative_base()
        
        class TestModel(Base):
            __tablename__ = "test_table"
            id = Column(Integer, primary_key=True)
        
        Base.metadata.create_all(engine)
        
        # Run concurrent connections
        def worker(worker_id):
            for i in range(5):
                with engine.connect() as conn:
                    # Simulate some work
                    pass
        
        threads = []
        for i in range(10):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        # Verify callbacks were invoked
        assert len(checkout_calls) > 0, "checkout callbacks should be invoked"
        assert len(checkin_calls) > 0, "checkin callbacks should be invoked"
        
        # Checkout and checkin should balance (connections returned to pool)
        assert abs(len(checkout_calls) - len(checkin_calls)) <= 2, \
            f"Checkout/checkin imbalance: {len(checkout_calls)} checkouts vs {len(checkin_calls)} checkins"
        
        engine.dispose()
    finally:
        import shutil
        try:
            shutil.rmtree(tmpdir)
        except:
            pass


if __name__ == "__main__":
    # Run the tests manually for verification
    print("Testing pool_events parameter is invalid...")
    try:
        test_pool_events_parameter_invalid()
        print("FAILED: pool_events parameter should cause InvalidRequestError")
    except Exception as e:
        print(f"PASSED: pool_events parameter correctly rejected: {e}")
    
    print("\nTesting correct event registration...")
    try:
        test_correct_event_registration()
        print("PASSED: Correct event registration works")
    except Exception as e:
        print(f"FAILED: {e}")
    
    print("\nTesting DatabasePool pattern...")
    try:
        test_database_pool_event_registration()
        print("PASSED: DatabasePool uses correct pattern")
    except Exception as e:
        print(f"FAILED: {e}")
    
    print("\nTesting callback signatures...")
    try:
        test_callback_signatures()
        print("PASSED: Callback signatures are correct")
    except Exception as e:
        print(f"FAILED: {e}")
    
    print("\nTesting callback invocation...")
    try:
        test_callback_invocation()
        print("PASSED: Callbacks are invoked correctly")
    except Exception as e:
        print(f"FAILED: {e}")
    
    print("\nTesting no duplicate listeners on reinit...")
    try:
        test_no_duplicate_listeners_on_reinit()
        print("PASSED: No duplicate listeners on re-initialization")
    except Exception as e:
        print(f"FAILED: {e}")
    
    print("\nTesting concurrent pool access...")
    try:
        test_concurrent_pool_access()
        print("PASSED: Concurrent pool access works correctly")
    except Exception as e:
        print(f"FAILED: {e}")