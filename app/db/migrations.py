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


# 8-bit encoders whose 10-bit variant is safe to switch to. Every AV1
# hardware decoder handles 10-bit (it's part of AV1 Main profile) and HEVC
# Main10 is supported by essentially every HEVC decoder, so 10-bit costs no
# compatibility and avoids banding (and HDR loss). H.264 is deliberately
# absent: High10 has almost no hardware decode support. If the installed
# HandBrake lacks a _10bit encoder, EncodingJob.select_encoder falls back.
TEN_BIT_UPGRADES = {
    enc: f"{enc}_10bit" for enc in (
        "svt_av1", "x265", "nvenc_h265", "nvenc_av1", "qsv_h265", "qsv_av1",
        "vce_h265", "vce_av1", "vt_h265",
    )
}


def _m4_profiles_to_10bit(cursor):
    for old, new in TEN_BIT_UPGRADES.items():
        cursor.execute("UPDATE profiles SET encoder = ? WHERE encoder = ?", (new, old))
    # 'main10' via custom args was the old way to ask for 10-bit; the
    # _10bit encoder now does it (the flag stays valid, so it's left alone).


def _m5_connection_path_mappings(cursor):
    _add_column(cursor, "external_connections", "path_mappings", "TEXT DEFAULT '[]'")


def _m6_target_quality(cursor):
    _add_column(cursor, "profiles", "target_vmaf", "REAL DEFAULT 0")
    _add_column(cursor, "queue", "tq_quality", "INTEGER")
    _add_column(cursor, "queue", "tq_vmaf", "REAL")


def _m7_profile_max_bitrate(cursor):
    _add_column(cursor, "profiles", "max_bitrate_kbps", "INTEGER DEFAULT 0")


def _m8_queue_conformance_checked(cursor):
    _add_column(cursor, "queue", "conformance_checked", "TEXT")


MIGRATIONS = [
    (1, "users.token_version", _m1_token_version),
    (2, "drop unused sessions/api_keys tables", _m2_drop_unused_auth_tables),
    (3, "profiles.film_grain", _m3_profile_film_grain),
    (4, "move profiles to 10-bit encoders (H.264 stays 8-bit)", _m4_profiles_to_10bit),
    (5, "external_connections.path_mappings", _m5_connection_path_mappings),
    (6, "target-quality (VMAF) encoding", _m6_target_quality),
    (7, "profiles.max_bitrate_kbps", _m7_profile_max_bitrate),
    (8, "queue.conformance_checked", _m8_queue_conformance_checked),
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
