"""Versioned schema migrations (PRAGMA user_version)."""
from app.logger import optimizarr_logger


# ============================================================
# Versioned schema migrations
# ============================================================
# Each entry runs once, in order, and bumps PRAGMA user_version. To change
# the schema, append a new (version, description, function) — never edit or
# reorder a released one. Functions receive the open cursor and run inside
# initialize_database's transaction, so a failing migration rolls back.

def _has_column(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _add_column(cursor, table: str, column: str, decl: str):
    """ADD COLUMN unless present (a DB upgraded by the legacy path may
    already have it)."""
    if not _has_column(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _m1_token_version(cursor):
    _add_column(cursor, "users", "token_version", "INTEGER DEFAULT 0")


def _m2_drop_unused_auth_tables(cursor):
    # Never read or written — auth is stateless JWT + token_version.
    cursor.execute("DROP TABLE IF EXISTS sessions")
    cursor.execute("DROP TABLE IF EXISTS api_keys")


def _m3_profile_film_grain(cursor):
    _add_column(cursor, "profiles", "film_grain", "INTEGER DEFAULT 0")


MIGRATIONS = [
    (1, "users.token_version", _m1_token_version),
    (2, "drop unused sessions/api_keys tables", _m2_drop_unused_auth_tables),
    (3, "profiles.film_grain", _m3_profile_film_grain),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


def _run_migrations(cursor):
    cursor.execute("PRAGMA user_version")
    current = cursor.fetchone()[0]
    if current > SCHEMA_VERSION:
        optimizarr_logger.app_logger.warning(f"  ⚠ Database schema v{current} is newer than this build "
              f"(v{SCHEMA_VERSION}) — was Optimizarr downgraded?")
        return
    for version, description, fn in MIGRATIONS:
        if version <= current:
            continue
        fn(cursor)
        # PRAGMA can't take parameters; version is an int from the list above
        cursor.execute(f"PRAGMA user_version = {int(version)}")
        optimizarr_logger.app_logger.info(f"  ↳ Migration {version}: {description}")
