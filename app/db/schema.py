"""Schema creation and the legacy add-if-missing upgrade path."""

from app.logger import optimizarr_logger
from app.db.migrations import _run_migrations


class SchemaMixin:
    """Database methods: schema creation and the legacy add-if-missing upgrade path."""

    def initialize_database(self):
        """Create database schema if it doesn't exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Profiles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    resolution TEXT,
                    framerate INTEGER,
                    codec TEXT NOT NULL,
                    encoder TEXT NOT NULL,
                    quality INTEGER NOT NULL,
                    audio_codec TEXT NOT NULL,
                    container TEXT DEFAULT 'mkv',
                    audio_handling TEXT DEFAULT 'preserve_all',
                    subtitle_handling TEXT DEFAULT 'none',
                    enable_filters BOOLEAN DEFAULT 0,
                    chapter_markers BOOLEAN DEFAULT 1,
                    hw_accel_enabled BOOLEAN DEFAULT 0,
                    preset TEXT,
                    two_pass BOOLEAN DEFAULT 0,
                    custom_args TEXT,
                    is_default BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Scan roots table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scan_roots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    profile_id INTEGER,
                    library_type TEXT DEFAULT 'custom',
                    enabled BOOLEAN DEFAULT 1,
                    recursive BOOLEAN DEFAULT 1,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                )
            """)
            
            # Queue table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    root_id INTEGER,
                    profile_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 0,
                    current_specs TEXT,
                    target_specs TEXT,
                    file_size_bytes INTEGER,
                    estimated_savings_bytes INTEGER,
                    progress REAL DEFAULT 0.0,
                    current_cpu_percent REAL DEFAULT 0.0,
                    current_memory_mb REAL DEFAULT 0.0,
                    permission_status TEXT,
                    permission_message TEXT,
                    paused_reason TEXT,
                    error_message TEXT,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (root_id) REFERENCES scan_roots(id),
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                )
            """)
            
            # Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    email TEXT,
                    is_admin BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP
                )
            """)
            
            # Schedule table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enabled BOOLEAN DEFAULT 0,
                    days_of_week TEXT DEFAULT '0,1,2,3,4,5,6',
                    start_time TEXT DEFAULT '22:00',
                    end_time TEXT DEFAULT '06:00',
                    max_concurrent_jobs INTEGER DEFAULT 1
                )
            """)
            
            # Settings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # History table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    profile_name TEXT,
                    original_size_bytes INTEGER,
                    new_size_bytes INTEGER,
                    savings_bytes INTEGER,
                    encoding_time_seconds INTEGER,
                    codec TEXT,
                    container TEXT,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Folder watches table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS folder_watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    profile_id INTEGER NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    recursive BOOLEAN DEFAULT 1,
                    auto_queue BOOLEAN DEFAULT 1,
                    extensions TEXT DEFAULT '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg',
                    last_check TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (profile_id) REFERENCES profiles(id)
                )
            """)
            
            # Incoming webhook dead-letter table — every received webhook is
            # persisted BEFORE processing so events survive restarts/crashes.
            # processed_at NULL = not yet (or unsuccessfully) processed.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_type TEXT NOT NULL,
                    payload_json TEXT,
                    received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    error TEXT
                )
            """)

            # Notification webhooks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    webhook_url TEXT NOT NULL,
                    webhook_type TEXT DEFAULT 'discord',
                    events TEXT DEFAULT '["encode_complete","encode_failed","queue_empty"]',
                    enabled BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Insert default schedule if none exists
            cursor.execute("SELECT COUNT(*) FROM schedule")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO schedule (enabled, days_of_week, start_time, end_time)
                    VALUES (0, '0,1,2,3,4,5,6', '22:00', '06:00')
                """)
            
            # ---- MIGRATIONS for existing databases ----
            # Add 'container' column to profiles if missing
            cursor.execute("PRAGMA table_info(profiles)")
            profile_columns = [col[1] for col in cursor.fetchall()]
            if 'container' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN container TEXT DEFAULT 'mkv'")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'container' column to profiles")
            
            # Phase 2 migrations
            if 'audio_handling' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN audio_handling TEXT DEFAULT 'preserve_all'")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'audio_handling' column to profiles")
            if 'subtitle_handling' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN subtitle_handling TEXT DEFAULT 'none'")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'subtitle_handling' column to profiles")
            if 'enable_filters' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN enable_filters BOOLEAN DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'enable_filters' column to profiles")
            if 'chapter_markers' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN chapter_markers BOOLEAN DEFAULT 1")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'chapter_markers' column to profiles")
            if 'hw_accel_enabled' not in profile_columns:
                cursor.execute("ALTER TABLE profiles ADD COLUMN hw_accel_enabled BOOLEAN DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'hw_accel_enabled' column to profiles")
            
            # Add columns to scan_roots if missing
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_columns = [col[1] for col in cursor.fetchall()]
            if 'library_type' not in root_columns:
                cursor.execute("ALTER TABLE scan_roots ADD COLUMN library_type TEXT DEFAULT 'custom'")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'library_type' column to scan_roots")
            if 'show_in_stats' not in root_columns:
                cursor.execute("ALTER TABLE scan_roots ADD COLUMN show_in_stats BOOLEAN DEFAULT 1")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'show_in_stats' column to scan_roots")
            
            # History table migrations
            cursor.execute("PRAGMA table_info(history)")
            history_columns = [col[1] for col in cursor.fetchall()]
            if 'codec' not in history_columns:
                cursor.execute("ALTER TABLE history ADD COLUMN codec TEXT")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'codec' column to history")
            if 'container' not in history_columns:
                cursor.execute("ALTER TABLE history ADD COLUMN container TEXT")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'container' column to history")
            
            # External connections table (Sonarr / Radarr / Stash)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS external_connections (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    app_type        TEXT NOT NULL,
                    base_url        TEXT NOT NULL,
                    api_key_encrypted TEXT NOT NULL,
                    enabled         BOOLEAN DEFAULT 1,
                    show_in_stats   BOOLEAN DEFAULT 1,
                    last_tested     TIMESTAMP,
                    last_synced     TIMESTAMP,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # scan_roots: link to an external connection (optional)
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_columns_check = [col[1] for col in cursor.fetchall()]
            if 'external_connection_id' not in root_columns_check:
                cursor.execute(
                    "ALTER TABLE scan_roots ADD COLUMN external_connection_id INTEGER"
                )
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'external_connection_id' to scan_roots")

            # scan_roots: AI upscale settings per library
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_cols = [col[1] for col in cursor.fetchall()]
            upscale_migrations = [
                ("upscale_enabled",       "BOOLEAN DEFAULT 0"),
                ("upscale_trigger_below", "INTEGER DEFAULT 720"),
                ("upscale_target_height", "INTEGER DEFAULT 1080"),
                ("upscale_model",         "TEXT DEFAULT 'realesrgan-x4plus'"),
                ("upscale_factor",        "INTEGER DEFAULT 2"),
                ("upscale_key",           "TEXT DEFAULT 'realesrgan'"),
            ]
            for col_name, col_def in upscale_migrations:
                if col_name not in root_cols:
                    cursor.execute(
                        f"ALTER TABLE scan_roots ADD COLUMN {col_name} {col_def}"
                    )
                    optimizarr_logger.app_logger.info(f"  ↳ Migrated: added '{col_name}' to scan_roots")

            # queue: upscale_plan stores the per-item upscale JSON
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols = [col[1] for col in cursor.fetchall()]
            if 'upscale_plan' not in queue_cols:
                cursor.execute("ALTER TABLE queue ADD COLUMN upscale_plan TEXT")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'upscale_plan' to queue")

            # profiles: AI upscale settings per profile
            cursor.execute("PRAGMA table_info(profiles)")
            profile_cols = [col[1] for col in cursor.fetchall()]
            profile_upscale_migrations = [
                ("upscale_enabled",       "BOOLEAN DEFAULT 0"),
                ("upscale_trigger_below", "INTEGER DEFAULT 720"),
                ("upscale_target_height", "INTEGER DEFAULT 1080"),
                ("upscale_model",         "TEXT DEFAULT 'realesrgan-x4plus'"),
                ("upscale_factor",        "INTEGER DEFAULT 2"),
                ("upscale_key",           "TEXT DEFAULT 'realesrgan'"),
            ]
            for col_name, col_def in profile_upscale_migrations:
                if col_name not in profile_cols:
                    cursor.execute(f"ALTER TABLE profiles ADD COLUMN {col_name} {col_def}")
                    optimizarr_logger.app_logger.info(f"  ↳ Migrated: added '{col_name}' to profiles")

            # profiles: 3D / stereo conversion settings per profile
            cursor.execute("PRAGMA table_info(profiles)")
            profile_cols_stereo = [col[1] for col in cursor.fetchall()]
            profile_stereo_migrations = [
                ("stereo_enabled",     "BOOLEAN DEFAULT 0"),
                ("stereo_mode",        "TEXT DEFAULT '2d_to_3d'"),
                ("stereo_format",      "TEXT DEFAULT 'half_sbs'"),
                ("stereo_divergence",  "REAL DEFAULT 2.0"),
                ("stereo_convergence", "REAL DEFAULT 0.5"),
                ("stereo_depth_model", "TEXT DEFAULT 'Any_V2_S'"),
            ]
            for col_name, col_def in profile_stereo_migrations:
                if col_name not in profile_cols_stereo:
                    cursor.execute(f"ALTER TABLE profiles ADD COLUMN {col_name} {col_def}")
                    optimizarr_logger.app_logger.info(f"  ↳ Migrated: added '{col_name}' to profiles")

            # scan_roots: 3D / stereo conversion settings per library
            cursor.execute("PRAGMA table_info(scan_roots)")
            root_cols_stereo = [col[1] for col in cursor.fetchall()]
            scanroot_stereo_migrations = [
                ("stereo_enabled",     "BOOLEAN DEFAULT 0"),
                ("stereo_mode",        "TEXT DEFAULT '2d_to_3d'"),
                ("stereo_format",      "TEXT DEFAULT 'half_sbs'"),
                ("stereo_divergence",  "REAL DEFAULT 2.0"),
                ("stereo_convergence", "REAL DEFAULT 0.5"),
                ("stereo_depth_model", "TEXT DEFAULT 'Any_V2_S'"),
            ]
            for col_name, col_def in scanroot_stereo_migrations:
                if col_name not in root_cols_stereo:
                    cursor.execute(f"ALTER TABLE scan_roots ADD COLUMN {col_name} {col_def}")
                    optimizarr_logger.app_logger.info(f"  ↳ Migrated: added '{col_name}' to scan_roots")

            # queue: stereo_plan stores the per-item stereo conversion JSON
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_stereo = [col[1] for col in cursor.fetchall()]
            if 'stereo_plan' not in queue_cols_stereo:
                cursor.execute("ALTER TABLE queue ADD COLUMN stereo_plan TEXT")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'stereo_plan' to queue")

            # duration_seconds — real video duration for accurate ETA + savings
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_dur = [col[1] for col in cursor.fetchall()]
            if 'duration_seconds' not in queue_cols_dur:
                cursor.execute("ALTER TABLE queue ADD COLUMN duration_seconds REAL DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'duration_seconds' to queue")

            cursor.execute("PRAGMA table_info(history)")
            history_cols = [col[1] for col in cursor.fetchall()]
            if 'duration_seconds' not in history_cols:
                cursor.execute("ALTER TABLE history ADD COLUMN duration_seconds REAL DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'duration_seconds' to history")

            # Duplicate detection: perceptual fingerprints + match results
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS fingerprints (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path    TEXT UNIQUE NOT NULL,
                    file_size    INTEGER,
                    mtime        REAL,
                    quick_hash   TEXT,
                    frame_hashes BLOB,
                    duration     REAL DEFAULT 0,
                    error        TEXT,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS duplicate_matches (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_a          TEXT NOT NULL,
                    file_b          TEXT NOT NULL,
                    kind            TEXT NOT NULL,
                    score           REAL DEFAULT 0,
                    offset_seconds  REAL DEFAULT 0,
                    overlap_seconds REAL DEFAULT 0,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_a, file_b)
                )
            """)

            # preprocessed — job included an upscale/stereo phase, so its
            # encode time is dominated by AI work, not HandBrake. Kept as a
            # separate speed-learning group so day-long upscale jobs don't
            # poison the plain-encode averages.
            if 'preprocessed' not in history_cols:
                cursor.execute("ALTER TABLE history ADD COLUMN preprocessed INTEGER DEFAULT 0")
                # Backfill: legacy rows recorded wall-clock time including AI
                # phases. No plain encode runs slower than 100× realtime, so
                # anything above that was an upscale/stereo job (or slept
                # through) — reclassify instead of letting it poison the
                # plain-encode speed group.
                cursor.execute("""
                    UPDATE history SET preprocessed = 1
                    WHERE encoding_time_seconds > 0 AND duration_seconds > 0
                      AND (CAST(encoding_time_seconds AS REAL) / duration_seconds) > 100
                """)
                optimizarr_logger.app_logger.info(f"  ↳ Migrated: added 'preprocessed' to history "
                      f"({cursor.rowcount} legacy row(s) reclassified)")

            # retry_count — automatic retry attempts for failed encodes
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_retry = [col[1] for col in cursor.fetchall()]
            if 'retry_count' not in queue_cols_retry:
                cursor.execute("ALTER TABLE queue ADD COLUMN retry_count INTEGER DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'retry_count' to queue")

            # Live encode telemetry parsed from HandBrakeCLI output (Patch 33)
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_live = [col[1] for col in cursor.fetchall()]
            if 'current_fps' not in queue_cols_live:
                cursor.execute("ALTER TABLE queue ADD COLUMN current_fps REAL DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'current_fps' to queue")
            if 'eta_seconds' not in queue_cols_live:
                cursor.execute("ALTER TABLE queue ADD COLUMN eta_seconds INTEGER DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'eta_seconds' to queue")

            # retry_after — backoff gate; a re-queued failure isn't eligible to
            # run again until this UTC timestamp (NULL = eligible now) (Patch 39)
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_ra = [col[1] for col in cursor.fetchall()]
            if 'retry_after' not in queue_cols_ra:
                cursor.execute("ALTER TABLE queue ADD COLUMN retry_after TIMESTAMP")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'retry_after' to queue")

            # Pipeline phase telemetry. A job is stereo → upscale → encode →
            # finalize; each phase has its own progress and ETA. Without this
            # the UI showed one undifferentiated bar, so a multi-hour upscale
            # and a stalled finalize were indistinguishable from a live encode.
            cursor.execute("PRAGMA table_info(queue)")
            queue_cols_phase = [col[1] for col in cursor.fetchall()]
            if 'phase' not in queue_cols_phase:
                cursor.execute("ALTER TABLE queue ADD COLUMN phase TEXT")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'phase' to queue")
            if 'phase_progress' not in queue_cols_phase:
                cursor.execute(
                    "ALTER TABLE queue ADD COLUMN phase_progress REAL DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'phase_progress' to queue")
            if 'phase_eta_seconds' not in queue_cols_phase:
                cursor.execute(
                    "ALTER TABLE queue ADD COLUMN phase_eta_seconds INTEGER DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'phase_eta_seconds' to queue")

            # pre_work_mpf — megapixel-frames of AI work an upscale job did
            # (output frames × output megapixels). Upscale cost tracks pixels
            # pushed through the model, NOT runtime: a 10-minute 4K ×2 job
            # dwarfs a 2-hour 480p ×2 one. Learning seconds-per-megapixel-frame
            # from this makes fastest/slowest-first rank upscales correctly.
            cursor.execute("PRAGMA table_info(history)")
            history_cols_mpf = [col[1] for col in cursor.fetchall()]
            if 'pre_work_mpf' not in history_cols_mpf:
                cursor.execute(
                    "ALTER TABLE history ADD COLUMN pre_work_mpf REAL DEFAULT 0")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'pre_work_mpf' to history")

            # Auto-sync interval for external connections (Patch 37); 0 = off
            cursor.execute("PRAGMA table_info(external_connections)")
            conn_cols = [col[1] for col in cursor.fetchall()]
            if 'sync_interval_hours' not in conn_cols:
                cursor.execute(
                    "ALTER TABLE external_connections "
                    "ADD COLUMN sync_interval_hours INTEGER DEFAULT 0"
                )
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'sync_interval_hours' to external_connections")

            # folder_watches.scan_root_id — links a watch to its library (scan root).
            # Folder watching is now toggled per-library via the eye toggle.
            cursor.execute("PRAGMA table_info(folder_watches)")
            fw_cols = [col[1] for col in cursor.fetchall()]
            if 'scan_root_id' not in fw_cols:
                cursor.execute("ALTER TABLE folder_watches ADD COLUMN scan_root_id INTEGER")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'scan_root_id' to folder_watches")
                # Best-effort: link existing watches to a scan root with the same path
                cursor.execute("""
                    UPDATE folder_watches
                    SET scan_root_id = (
                        SELECT sr.id FROM scan_roots sr WHERE sr.path = folder_watches.path
                    )
                    WHERE scan_root_id IS NULL
                """)

            # Priority scheme: rank-based (1 = first to encode, ascending).
            # One-time renumber of the old "higher number = sooner" scheme,
            # preserving each item's existing position in the queue.
            cursor.execute("SELECT value FROM settings WHERE key = 'priority_scheme'")
            scheme_row = cursor.fetchone()
            if not scheme_row or scheme_row[0] != 'rank':
                cursor.execute("""
                    UPDATE queue SET priority = (
                        SELECT rn FROM (
                            SELECT id, ROW_NUMBER() OVER (
                                ORDER BY priority DESC, created_at
                            ) AS rn FROM queue
                        ) ranked WHERE ranked.id = queue.id
                    )
                """)
                cursor.execute("""
                    INSERT INTO settings (key, value, updated_at)
                    VALUES ('priority_scheme', 'rank', CURRENT_TIMESTAMP)
                    ON CONFLICT(key) DO UPDATE SET
                        value = 'rank', updated_at = CURRENT_TIMESTAMP
                """)
                optimizarr_logger.app_logger.info("  ↳ Migrated: queue priorities renumbered to rank scheme (1 = first)")

            # Security: must_change_password flag for default admin
            try:
                cursor.execute("SELECT must_change_password FROM users LIMIT 1")
            except Exception:
                cursor.execute("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0")
                # Flag the default admin to force a password change
                cursor.execute("UPDATE users SET must_change_password = 1 WHERE username = 'admin'")
                optimizarr_logger.app_logger.info("  ↳ Migrated: added 'must_change_password' to users")

            # Everything above is the legacy "add it if it's missing" upgrade
            # path, kept because existing databases are in every state it
            # handles. New schema changes go in MIGRATIONS (bottom of file).
            _run_migrations(cursor)

            # Enforce single default profile — keep only the lowest-id one
            cursor.execute("SELECT id FROM profiles WHERE is_default = 1 ORDER BY id")
            default_rows = cursor.fetchall()
            if len(default_rows) > 1:
                keep_id = default_rows[0][0]
                cursor.execute("UPDATE profiles SET is_default = 0 WHERE is_default = 1 AND id != ?", (keep_id,))
                optimizarr_logger.app_logger.info(f"  ↳ Migrated: enforced single default profile (kept id={keep_id})")

            # Indexes — the queue is scanned constantly (next-pending poll every
            # second while encoding, status counts on every dashboard poll,
            # file-path dedupe on every scan/webhook/sync).
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_status_priority "
                "ON queue(status, priority)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_file_path "
                "ON queue(file_path)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_completed_at "
                "ON history(completed_at)")

            optimizarr_logger.app_logger.info("✓ Database schema initialized")
