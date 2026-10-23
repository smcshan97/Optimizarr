"""
Database module for Optimizarr.
Handles SQLite connections; schema and CRUD live in the app.db mixins.
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from app.config import settings
from app.db.util import _safe_json  # noqa: F401  (re-exported)
from app.db.migrations import MIGRATIONS, SCHEMA_VERSION, _run_migrations  # noqa: F401
from app.db.schema import SchemaMixin
from app.db.profiles import ProfilesMixin
from app.db.library import LibraryMixin
from app.db.queue import QueueMixin
from app.db.accounts import AccountsMixin
from app.db.webhooks import WebhooksMixin
from app.db.dedupe import DedupeMixin
from app.db.history import HistoryMixin
from app.db.connections import ConnectionsMixin


class Database(SchemaMixin, ProfilesMixin, LibraryMixin, QueueMixin,
               AccountsMixin, WebhooksMixin, DedupeMixin, HistoryMixin,
               ConnectionsMixin):
    """SQLite database manager for Optimizarr.

    Connection handling lives here; the per-domain methods live in the
    app.db mixins (schema, profiles, library, queue, accounts, webhooks,
    dedupe, history, connections)."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.db_path
        self._write_lock = __import__('threading').Lock()
        
        # CRITICAL: Ensure directory exists before trying to connect
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Enable WAL mode once at startup for concurrent read/write safety
        _conn = sqlite3.connect(self.db_path)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout = 5000")
        _conn.close()
        
        self.initialize_database()
    
    @contextmanager
    def get_connection(self, write: bool = True):
        """Context manager for a database connection.

        WAL mode (set at init) allows unlimited concurrent readers alongside
        the single writer, so READS take no lock — serializing them behind
        the global write-lock was what let a busy encoder freeze the whole
        UI (an async handler blocking the event loop on lock.acquire()).

        ``write`` defaults to True so any caller that doesn't opt out keeps
        the original serialized-write behavior — a missed call site is merely
        unoptimized, never unsafe. Read-only callers pass ``write=False``
        (or use :meth:`get_read_connection`).
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        conn.execute("PRAGMA busy_timeout = 5000")
        if write:
            self._write_lock.acquire()
        try:
            yield conn
            if write:
                conn.commit()
        except Exception as e:
            if write:
                conn.rollback()
            raise e
        finally:
            if write:
                self._write_lock.release()
            conn.close()

    def get_read_connection(self):
        """Lock-free read-only connection (see :meth:`get_connection`)."""
        return self.get_connection(write=False)


# Global database instance
db = Database()
