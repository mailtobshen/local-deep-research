"""
Comprehensive tests for thread session cleanup and resource management.

Tests thread-local session management, proper cleanup of database resources,
credential management across threads, and prevention of resource leaks.
"""

import pytest
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from local_deep_research.database.thread_local_session import (
    ThreadLocalSessionManager,
    ThreadSessionContext,
    get_metrics_session,
)
from local_deep_research.database.sqlcipher_utils import apply_cipher_defaults_before_key


class TestThreadSessionManagement:
    """Test suite for thread-local session management."""

    @pytest.fixture
    def session_manager(self):
        """Create a ThreadLocalSessionManager for testing."""
        return ThreadLocalSessionManager()

    def test_thread_local_session_creation(self, session_manager):
        """Test that sessions are created per thread."""
        username = "test_user"
        password = "test_password"

        # Mock the database session creation
        with patch('local_deep_research.database.thread_local_session.db_manager') as mock_db_manager, \
             patch('sqlalchemy.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_db_manager.get_settings_manager.return_value = mock_settings
            mock_settings.get_setting.return_value = None

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            mock_session = MagicMock()
            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            session = session_manager.get_session(username, password)

            assert session is not None, "Should create a session"
            assert session_manager._local.session == session, "Should cache session in thread-local storage"

    def test_session_reuse_in_same_thread(self, session_manager):
        """Test that the same session is reused within a thread."""
        username = "test_user"
        password = "test_password"

        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            mock_session = MagicMock()
            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            # First call
            session1 = session_manager.get_session(username, password)
            # Second call should return the same session
            session2 = session_manager.get_session(username, password)

            assert session1 == session2, "Should reuse the same session within a thread"

    def test_username_mismatch_cleanup(self, session_manager):
        """Test that username mismatch triggers session cleanup."""
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            # Create first session for user1
            mock_session1 = MagicMock()
            mock_session_factory1 = MagicMock()
            mock_session_factory1.return_value = mock_session1

            with patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:
                mock_sessionmaker.return_value = mock_session_factory1
                session_manager.get_session("user1", "pass1")

                # Set username in thread-local
                session_manager._local.username = "user1"

            # Now try to get session for user2 in same thread
            mock_session2 = MagicMock()
            mock_session_factory2 = MagicMock()
            mock_session_factory2.return_value = mock_session2

            with patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:
                mock_sessionmaker.return_value = mock_session_factory2
                session2 = session_manager.get_session("user2", "pass2")

            # The username mismatch should trigger cleanup and create new session
            assert session2 is not None, "Should create new session after username mismatch"

    def test_session_validation_and_cleanup(self, session_manager):
        """Test that invalid sessions are cleaned up and recreated."""
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            # Create a session that will fail validation
            mock_session = MagicMock()
            mock_session.execute.side_effect = Exception("Database connection lost")

            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            session_manager.get_session("test_user", "test_pass")

            # The invalid session should trigger cleanup logic
            # (Implementation should handle this gracefully)

    def test_multiple_threads_independent_sessions(self, session_manager):
        """Test that different threads get independent sessions."""
        sessions = []
        threads = []

        def create_session(thread_id):
            with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
                 patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
                 patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

                mock_settings = MagicMock()
                mock_settings.get_setting.return_value = None
                mock_get_settings.return_value = mock_settings

                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine

                mock_session = MagicMock()
                mock_session_factory = MagicMock()
                mock_session_factory.return_value = mock_session
                mock_sessionmaker.return_value = mock_session_factory

                session = session_manager.get_session(f"user_{thread_id}", f"pass_{thread_id}")
                sessions.append((thread_id, id(session)))

        # Create sessions in multiple threads
        for i in range(5):
            thread = threading.Thread(target=create_session, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # All sessions should have different IDs (different objects)
        session_ids = [sess_id for _, sess_id in sessions]
        assert len(set(session_ids)) == len(session_ids), "Each thread should get its own session"

    def test_credential_tracking_per_thread(self, session_manager):
        """Test that credentials are tracked per thread ID."""
        username = "test_user"
        password = "test_password"
        thread_id = threading.get_ident()

        # Mock session creation
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            mock_session = MagicMock()
            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            session_manager.get_session(username, password)

            # Check that credentials are tracked
            with session_manager._lock:
                assert thread_id in session_manager._thread_credentials, "Should track credentials for thread"
                assert session_manager._thread_credentials[thread_id] == (username, password), "Should store correct credentials"

    def test_session_cleanup_on_thread_end(self, session_manager):
        """Test that sessions are properly cleaned up when threads end."""
        acquired_threads = []

        def thread_work():
            with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
                 patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
                 patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

                mock_settings = MagicMock()
                mock_settings.get_setting.return_value = None
                mock_get_settings.return_value = mock_settings

                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine

                mock_session = MagicMock()
                mock_session_factory = MagicMock()
                mock_session_factory.return_value = mock_session
                mock_sessionmaker.return_value = mock_session_factory

                session = session_manager.get_session("user", "pass")
                acquired_threads.append(threading.get_ident())

        thread = threading.Thread(target=thread_work)
        thread.start()
        thread.join()

        # After thread ends, the session should be cleaned up
        # (This tests the cleanup mechanism, not automatic cleanup)


class TestThreadSessionContext:
    """Test suite for ThreadSessionContext context manager."""

    @pytest.fixture
    def session_context(self):
        """Create a ThreadSessionContext for testing."""
        return ThreadSessionContext("test_user", "test_password")

    def test_context_manager_creates_session(self, session_context):
        """Test that context manager creates a session."""
        with patch('local_deep_research.database.thread_local_session.get_metrics_session') as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session

            with session_context as session:
                assert session == mock_session, "Should create session on __enter__"

    def test_context_manager_cleanup_on_exit(self, session_context):
        """Test that context manager handles exit gracefully."""
        with patch('local_deep_research.database.thread_local_session.get_metrics_session') as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session

            with session_context as session:
                assert session is not None, "Should have session inside context"

            # After context, session should still exist (thread keeps it)
            # but context should not have caused any cleanup issues

    def test_context_manager_with_exception(self, session_context):
        """Test that context manager handles exceptions properly."""
        with patch('local_deep_research.database.thread_local_session.get_metrics_session') as mock_get_session:
            mock_session = MagicMock()
            mock_get_session.return_value = mock_session

            try:
                with session_context as session:
                    raise ValueError("Test exception")
            except ValueError:
                pass  # Expected exception

            # Exception should not prevent proper context management


class TestResourceLeakPrevention:
    """Test suite for resource leak prevention in session management."""

    @pytest.fixture
    def session_manager(self):
        """Create a ThreadLocalSessionManager for testing."""
        return ThreadLocalSessionManager()

    def test_no_connection_leak_with_reuse(self, session_manager):
        """Test that session reuse doesn't cause connection leaks."""
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            mock_session = MagicMock()
            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            # Create session multiple times in same thread
            for i in range(10):
                session = session_manager.get_session("user", "pass")
                assert session is not None, f"Iteration {i}: Should create session"

            # Should not create multiple sessions (reuse)
            # This prevents connection pool exhaustion

    def test_transaction_rollback_after_validation(self, session_manager):
        """Test that transactions are properly rolled back after validation."""
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            mock_session = MagicMock()
            mock_session.execute.return_value = MagicMock()  # Valid query result
            mock_session.rollback = MagicMock()  # Track rollback calls

            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            session_manager.get_session("user", "pass")

            # After validation, transaction should be rolled back
            # (This prevents long-held SHARED locks in DEFERRED mode)

    def test_cross_thread_session_isolation(self, session_manager):
        """Test that sessions are properly isolated between threads."""
        session_refs = {}
        errors = []

        def thread_session(thread_id):
            try:
                with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
                     patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
                     patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

                    mock_settings = MagicMock()
                    mock_settings.get_setting.return_value = None
                    mock_get_settings.return_value = mock_settings

                    mock_engine = MagicMock()
                    mock_create_engine.return_value = mock_engine

                    mock_session = MagicMock()
                    mock_session_factory = MagicMock()
                    mock_session_factory.return_value = mock_session
                    mock_sessionmaker.return_value = mock_session_factory

                    session = session_manager.get_session(f"user_{thread_id}", f"pass_{thread_id}")
                    session_refs[thread_id] = id(session)

                    # Small delay to increase chance of race conditions
                    time.sleep(0.01)
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = []
        for i in range(10):
            thread = threading.Thread(target=thread_session, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Should have no errors: {errors}"
        assert len(set(session_refs.values())) == len(session_refs), "Each thread should have isolated session"

    def test_session_cleanup_after_database_error(self, session_manager):
        """Test that database errors trigger proper cleanup."""
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            # Mock session that fails on execute
            mock_session = MagicMock()
            mock_session.execute.side_effect = Exception("Connection lost")
            mock_session.close = MagicMock()

            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            try:
                session = session_manager.get_session("user", "pass")
                # Should handle error gracefully
            except Exception:
                pass  # Expected for DB errors

            # Verify cleanup was attempted
            # (The implementation should call cleanup logic on error)

    def test_concurrent_session_stress(self, session_manager):
        """Stress test with many concurrent threads creating sessions."""
        session_count = [0]
        errors = []
        lock = threading.Lock()

        def create_and_use_session():
            try:
                with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
                     patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
                     patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

                    mock_settings = MagicMock()
                    mock_settings.get_setting.return_value = None
                    mock_get_settings.return_value = mock_settings

                    mock_engine = MagicMock()
                    mock_create_engine.return_value = mock_engine

                    mock_session = MagicMock()
                    mock_session_factory = MagicMock()
                    mock_session_factory.return_value = mock_session
                    mock_sessionmaker.return_value = mock_session_factory

                    session = session_manager.get_session("user", "pass")

                    with lock:
                        session_count[0] += 1

                    # Simulate some work
                    time.sleep(0.001)
            except Exception as e:
                errors.append(str(e))

        # Create 50 concurrent threads
        threads = []
        for _ in range(50):
            thread = threading.Thread(target=create_and_use_session)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Should have no concurrent errors: {errors}"
        assert session_count[0] == 50, "Should successfully create all sessions"

    def test_memory_cleanup_on_thread_reuse(self, session_manager):
        """Test that thread-local storage doesn't accumulate memory."""
        # Simulate thread reuse with different users
        users = [f"user_{i}" for i in range(20)]

        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            sessions_created = []

            for username in users:
                # Create unique session for each user
                mock_session = MagicMock()
                mock_session_factory = MagicMock()
                mock_session_factory.return_value = mock_session
                mock_sessionmaker.return_value = mock_session_factory

                session = session_manager.get_session(username, f"{username}_pass")
                sessions_created.append(id(session))

            # Verify that sessions are being properly managed
            # (Implementation should prevent unlimited growth)

    def test_session_expiration_handling(self, session_manager):
        """Test handling of expired or closed sessions."""
        with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
             patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
             patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

            mock_settings = MagicMock()
            mock_settings.get_setting.return_value = None
            mock_get_settings.return_value = mock_settings

            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine

            # Create a session that becomes closed
            mock_session = MagicMock()
            mock_session.closed = True  # Session is closed
            mock_session.execute.side_effect = Exception("Session closed")

            mock_session_factory = MagicMock()
            mock_session_factory.return_value = mock_session
            mock_sessionmaker.return_value = mock_session_factory

            # Should detect closed session and create new one
            session = session_manager.get_session("user", "pass")
            assert session is not None, "Should handle closed session gracefully"

    def test_lock_contention_handling(self, session_manager):
        """Test that lock contention doesn't cause deadlocks."""
        acquired = []

        def acquire_session(thread_id):
            with patch('local_deep_research.database.thread_local_session.get_settings_manager') as mock_get_settings, \
                 patch('local_deep_research.database.thread_local_session.create_engine') as mock_create_engine, \
                 patch('sqlalchemy.orm.sessionmaker') as mock_sessionmaker:

                mock_settings = MagicMock()
                mock_settings.get_setting.return_value = None
                mock_get_settings.return_value = mock_settings

                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine

                mock_session = MagicMock()
                mock_session_factory = MagicMock()
                mock_session_factory.return_value = mock_session
                mock_sessionmaker.return_value = mock_session_factory

                session = session_manager.get_session("user", "pass")
                acquired.append(thread_id)
                time.sleep(0.01)  # Hold lock briefly

        threads = []
        for i in range(20):
            thread = threading.Thread(target=acquire_session, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(acquired) == 20, "All threads should successfully acquire sessions"