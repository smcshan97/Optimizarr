"""
Optimizarr — Basic Test Suite

Tests the critical-path logic that can run without external tools
(HandBrakeCLI, ffprobe, ffmpeg).  Covers:

  - _estimate_savings: codec/quality/resolution math
  - _needs_encoding: skip-or-encode decision
  - _normalise_codec: codec name mapping
  - Database schema: fresh DB init + migrations
  - Secret key auto-generation
  - Stereo plan JSON structure

Run:  pytest tests/ -v
"""
import json
import os
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scanner():
    """Return a MediaScanner instance (doesn't need HandBrakeCLI)."""
    from app.scanner import MediaScanner
    return MediaScanner()


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh Database instance on a temp file."""
    db_path = str(tmp_path / "test.db")
    # Patch settings.db_path so Database.__init__ uses our temp path
    from app.config import settings
    original = settings.db_path
    settings.db_path = db_path
    try:
        from app.database import Database
        db = Database(db_path=db_path)
        yield db
    finally:
        settings.db_path = original


# ---------------------------------------------------------------------------
# _normalise_codec
# ---------------------------------------------------------------------------

class TestNormaliseCodec:
    def test_h264_variants(self, scanner):
        for raw in ('h264', 'H264', 'h.264', 'avc', 'AVC1', 'x264'):
            assert scanner._normalise_codec(raw) == 'h264', f"Failed for {raw}"

    def test_h265_variants(self, scanner):
        for raw in ('h265', 'H265', 'h.265', 'hevc', 'HEVC', 'x265'):
            assert scanner._normalise_codec(raw) == 'h265', f"Failed for {raw}"

    def test_av1_variants(self, scanner):
        for raw in ('av1', 'AV1', 'av01', 'AV01'):
            assert scanner._normalise_codec(raw) == 'av1', f"Failed for {raw}"

    def test_vp9(self, scanner):
        assert scanner._normalise_codec('vp9') == 'vp9'
        assert scanner._normalise_codec('VP09') == 'vp9'

    def test_legacy_codecs(self, scanner):
        assert scanner._normalise_codec('mpeg4') == 'mpeg4'
        assert scanner._normalise_codec('xvid') == 'mpeg4'
        assert scanner._normalise_codec('DivX') == 'mpeg4'
        assert scanner._normalise_codec('mpeg2video') == 'mpeg2'
        assert scanner._normalise_codec('wmv3') == 'wmv'

    def test_unknown_passthrough(self, scanner):
        assert scanner._normalise_codec('someweirdcodec') == 'someweirdcodec'

    def test_empty_string(self, scanner):
        assert scanner._normalise_codec('') == 'unknown'


# ---------------------------------------------------------------------------
# _needs_encoding
# ---------------------------------------------------------------------------

class TestNeedsEncoding:
    def test_different_codec(self, scanner):
        current = {'codec': 'h264', 'resolution': '1920x1080'}
        target = {'codec': 'av1', 'resolution': ''}
        assert scanner._needs_encoding(current, target) is True

    def test_same_codec_no_resolution(self, scanner):
        current = {'codec': 'av1', 'resolution': '1920x1080'}
        target = {'codec': 'av1', 'resolution': ''}
        assert scanner._needs_encoding(current, target) is False

    def test_same_codec_same_resolution(self, scanner):
        current = {'codec': 'h265', 'resolution': '1920x1080'}
        target = {'codec': 'h265', 'resolution': '1920x1080'}
        assert scanner._needs_encoding(current, target) is False

    def test_downscale_needed(self, scanner):
        current = {'codec': 'h264', 'resolution': '3840x2160'}
        target = {'codec': 'h264', 'resolution': '1920x1080'}
        assert scanner._needs_encoding(current, target) is True

    def test_upscale_not_triggered(self, scanner):
        """Source smaller than target should NOT trigger encode."""
        current = {'codec': 'h264', 'resolution': '1280x720'}
        target = {'codec': 'h264', 'resolution': '1920x1080'}
        assert scanner._needs_encoding(current, target) is False

    def test_unknown_codec_always_encodes(self, scanner):
        current = {'codec': 'unknown', 'resolution': '1920x1080'}
        target = {'codec': 'av1', 'resolution': ''}
        assert scanner._needs_encoding(current, target) is True

    def test_preserve_resolution(self, scanner):
        current = {'codec': 'h264', 'resolution': '1920x1080'}
        target = {'codec': 'h265', 'resolution': 'preserve'}
        assert scanner._needs_encoding(current, target) is True  # codec differs

    def test_aspect_ratio_difference_no_re_encode(self, scanner):
        """1920x800 (letterboxed) vs 1920x1080 target should NOT re-encode
        because source height (800) is smaller than target (1080)."""
        current = {'codec': 'av1', 'resolution': '1920x800'}
        target = {'codec': 'av1', 'resolution': '1920x1080'}
        assert scanner._needs_encoding(current, target) is False


# ---------------------------------------------------------------------------
# _estimate_savings
# ---------------------------------------------------------------------------

class TestEstimateSavings:
    def test_same_codec_zero(self, scanner):
        assert scanner._estimate_savings(1_000_000_000, 'h264', 'h264') == 0

    def test_h264_to_av1_positive(self, scanner):
        savings = scanner._estimate_savings(1_000_000_000, 'h264', 'av1')
        assert savings > 0
        # AV1 should save roughly 40-55% of h264 at default CRF
        assert 300_000_000 < savings < 650_000_000

    def test_h264_to_h265_positive(self, scanner):
        savings = scanner._estimate_savings(1_000_000_000, 'h264', 'h265')
        assert savings > 0
        assert 300_000_000 < savings < 600_000_000

    def test_unknown_pair_zero(self, scanner):
        """Unknown source→target pair should return 0 (no guessing)."""
        assert scanner._estimate_savings(1_000_000_000, 'vp8', 'h264') == 0

    def test_preserve_target_zero(self, scanner):
        assert scanner._estimate_savings(1_000_000_000, 'h264', 'preserve') == 0
        assert scanner._estimate_savings(1_000_000_000, 'h264', '') == 0
        assert scanner._estimate_savings(1_000_000_000, 'h264', None) == 0

    def test_zero_filesize(self, scanner):
        assert scanner._estimate_savings(0, 'h264', 'av1') == 0

    def test_never_negative(self, scanner):
        """Savings should never be negative."""
        for src in ('h264', 'h265', 'mpeg4', 'mpeg2', 'wmv', 'unknown'):
            for tgt in ('av1', 'h265', 'h264'):
                savings = scanner._estimate_savings(500_000_000, src, tgt)
                assert savings >= 0, f"Negative savings for {src}→{tgt}"

    def test_quality_affects_estimate(self, scanner):
        """Lower CRF (higher quality) should reduce estimated savings."""
        base = scanner._estimate_savings(
            1_000_000_000, 'h264', 'av1',
            profile={'quality': 30}
        )
        higher_quality = scanner._estimate_savings(
            1_000_000_000, 'h264', 'av1',
            profile={'quality': 20}
        )
        # Higher quality (lower CRF) should save less
        assert higher_quality < base

    def test_resolution_downscale_increases_savings(self, scanner):
        """Downscaling resolution should increase savings."""
        base = scanner._estimate_savings(
            1_000_000_000, 'h264', 'av1',
            current_specs={'resolution': '3840x2160'},
            profile={'resolution': ''}
        )
        downscaled = scanner._estimate_savings(
            1_000_000_000, 'h264', 'av1',
            current_specs={'resolution': '3840x2160'},
            profile={'resolution': '1920x1080'}
        )
        assert downscaled > base

    def test_resolution_ceiling_never_grows_estimate(self, scanner):
        """A 1080p ceiling leaves a 720p file at 720p (nothing is upscaled),
        so it must not multiply the estimate by the pixel ratio."""
        free = scanner._estimate_savings(
            1_000_000_000, 'h264', 'av1',
            current_specs={'resolution': '1280x720'}, profile={'resolution': ''})
        capped = scanner._estimate_savings(
            1_000_000_000, 'h264', 'av1',
            current_specs={'resolution': '1280x720'}, profile={'resolution': '1920x1080'})
        assert capped == free > 0

    def test_h264_to_av1_matches_measured_ratio(self, scanner):
        """Measured on this library: AV1 encodes land at a median 0.60x
        the source (5,313 encodes, Feb-Oct 2026)."""
        assert scanner._estimate_savings(1_000_000_000, 'h264', 'av1') == 400_000_000

    def test_target_vmaf_profile_ignores_crf(self, scanner):
        """With a VMAF target the quality value is only a search start, not
        the encode's quality, so it must not skew the estimate."""
        a = scanner._estimate_savings(1_000_000_000, 'h264', 'av1',
                                      profile={'quality': 20, 'target_vmaf': 95})
        b = scanner._estimate_savings(1_000_000_000, 'h264', 'av1',
                                      profile={'quality': 40, 'target_vmaf': 95})
        assert a == b == 400_000_000

    def test_bitrate_based_estimate(self, scanner):
        """When bitrate and duration are available, use them."""
        savings = scanner._estimate_savings(
            2_000_000_000, 'h264', 'av1',
            current_specs={
                'bit_rate': 8_000_000,  # 8 Mbps
                'duration': 1800,       # 30 min
            }
        )
        assert savings > 0


# ---------------------------------------------------------------------------
# Database — fresh schema init
# ---------------------------------------------------------------------------

class TestDatabase:
    def test_fresh_init_creates_tables(self, fresh_db):
        """A fresh Database instance should create all required tables."""
        with fresh_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' 
                ORDER BY name
            """)
            tables = {row[0] for row in cursor.fetchall()}

        required = {
            'profiles', 'scan_roots', 'queue', 'history',
            'users', 'settings', 'schedule',
        }
        missing = required - tables
        assert not missing, f"Missing tables: {missing}"
        assert not {'sessions', 'api_keys'} & tables  # dropped by migration 2

    def test_fresh_db_is_at_latest_schema_version(self, fresh_db):
        from app.database import SCHEMA_VERSION
        with fresh_db.get_connection() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def test_migrations_upgrade_old_db_once(self, tmp_path):
        """A pre-migration DB (user_version 0, legacy tables) is upgraded,
        and re-opening it doesn't re-run anything."""
        import sqlite3
        from app.database import Database, SCHEMA_VERSION
        path = str(tmp_path / "old.db")
        Database(db_path=path)
        con = sqlite3.connect(path)
        con.execute("PRAGMA user_version = 0")
        con.execute("CREATE TABLE sessions (id INTEGER)")
        con.commit(); con.close()

        Database(db_path=path)
        con = sqlite3.connect(path)
        assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        cols = {r[1] for r in con.execute("PRAGMA table_info(profiles)")}
        con.close()
        assert 'sessions' not in names and 'film_grain' in cols
        Database(db_path=path)  # idempotent

    def test_migration_moves_profiles_to_10bit_except_h264(self, tmp_path):
        import sqlite3
        from app.database import Database
        path = str(tmp_path / "p.db")
        d = Database(db_path=path)
        for name, codec, enc in (('a', 'av1', 'svt_av1'), ('h', 'h265', 'x265'),
                                 ('n', 'h265', 'nvenc_h265'), ('m', 'h264', 'x264'),
                                 ('g', 'h264', 'nvenc_h264'), ('t', 'av1', 'svt_av1_10bit')):
            d.create_profile(name=name, codec=codec, encoder=enc, quality=28, audio_codec='aac')
        con = sqlite3.connect(path)
        con.execute("PRAGMA user_version = 3")
        con.commit(); con.close()

        d = Database(db_path=path)
        got = {p['name']: p['encoder'] for p in d.get_profiles()}
        assert got == {'a': 'svt_av1_10bit', 'h': 'x265_10bit', 'n': 'nvenc_h265_10bit',
                       'm': 'x264', 'g': 'nvenc_h264', 't': 'svt_av1_10bit'}

    def test_fresh_db_has_must_change_password(self, fresh_db):
        """The must_change_password column should exist on users."""
        with fresh_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(users)")
            columns = {row[1] for row in cursor.fetchall()}
        assert 'must_change_password' in columns

    def test_create_and_get_profile(self, fresh_db):
        """Basic profile CRUD should work on a fresh DB."""
        pid = fresh_db.create_profile(
            name="Test AV1",
            resolution="", framerate=None,
            codec="av1", encoder="svt_av1", quality=28,
            audio_codec="passthrough", container="mkv",
            audio_handling="preserve_all", subtitle_handling="none",
            chapter_markers=True, enable_filters=False,
            hw_accel_enabled=False, preset="8",
            two_pass=False, custom_args=None, is_default=True,
        )
        assert pid > 0
        profile = fresh_db.get_profile(pid)
        assert profile is not None
        assert profile['name'] == "Test AV1"
        assert profile['codec'] == "av1"

    def test_queue_add_and_fetch(self, fresh_db):
        """Queue items can be added and retrieved."""
        # Need a profile and scan root first
        pid = fresh_db.create_profile(
            name="Q Test", resolution="", framerate=None,
            codec="h265", encoder="x265", quality=24,
            audio_codec="aac", container="mkv",
            audio_handling="preserve_all", subtitle_handling="none",
            chapter_markers=False, enable_filters=False,
            hw_accel_enabled=False, preset="medium",
            two_pass=False, custom_args=None, is_default=False,
        )
        rid = fresh_db.create_scan_root(path="/fake/path", profile_id=pid)

        fresh_db.add_to_queue(
            file_path="/fake/path/video.mkv",
            root_id=rid,
            profile_id=pid,
            file_size=500_000_000,
            current_codec="h264",
            current_resolution="1920x1080",
            current_framerate=23.976,
            estimated_savings=200_000_000,
        )

        items = fresh_db.get_queue_items()
        assert len(items) >= 1
        assert items[0]['file_path'] == "/fake/path/video.mkv"
        assert items[0]['status'] == 'pending'


# ---------------------------------------------------------------------------
# Secret key auto-generation
# ---------------------------------------------------------------------------

class TestSecretKeyGeneration:
    def test_auto_generates_key(self, tmp_path):
        """_resolve_secret_key should generate a key when no env var set."""
        from app.config import _resolve_secret_key, _DEFAULT_SECRET, _SECRET_FILE

        # Temporarily redirect _SECRET_FILE to tmp_path
        import app.config as cfg
        original_file = cfg._SECRET_FILE
        cfg._SECRET_FILE = tmp_path / ".secret_key"
        try:
            # Clear env var
            env_backup = os.environ.pop("OPTIMIZARR_SECRET_KEY", None)
            try:
                key = _resolve_secret_key()
                assert key != _DEFAULT_SECRET
                assert len(key) == 64  # hex(32 bytes) = 64 chars
                # Second call should return same key (persisted)
                assert _resolve_secret_key() == key
            finally:
                if env_backup is not None:
                    os.environ["OPTIMIZARR_SECRET_KEY"] = env_backup
        finally:
            cfg._SECRET_FILE = original_file

    @pytest.mark.parametrize("placeholder", [
        "insecure-default-key-change-in-production",
        "dev-secret-key-change-in-production",   # shipped in .env
        "your-secret-key-here-generate-with-openssl-rand-hex-32",  # .env.example
        "CHANGEME",
        "  secret  ",
        "",
    ])
    def test_placeholder_keys_are_rejected(self, tmp_path, placeholder):
        """A placeholder in the env must not be used to sign JWTs."""
        from app.config import _resolve_secret_key

        import app.config as cfg
        original_file = cfg._SECRET_FILE
        cfg._SECRET_FILE = tmp_path / ".secret_key"
        env_backup = os.environ.get("OPTIMIZARR_SECRET_KEY")
        try:
            os.environ["OPTIMIZARR_SECRET_KEY"] = placeholder
            key = _resolve_secret_key()
            assert key.strip().lower() != placeholder.strip().lower()
            assert len(key) == 64
        finally:
            if env_backup is None:
                os.environ.pop("OPTIMIZARR_SECRET_KEY", None)
            else:
                os.environ["OPTIMIZARR_SECRET_KEY"] = env_backup
            cfg._SECRET_FILE = original_file

    def test_real_key_is_honoured(self, tmp_path):
        """A genuine user-supplied key must be passed through untouched."""
        from app.config import _resolve_secret_key

        import app.config as cfg
        original_file = cfg._SECRET_FILE
        cfg._SECRET_FILE = tmp_path / ".secret_key"
        env_backup = os.environ.get("OPTIMIZARR_SECRET_KEY")
        try:
            os.environ["OPTIMIZARR_SECRET_KEY"] = "a" * 64
            assert _resolve_secret_key() == "a" * 64
        finally:
            if env_backup is None:
                os.environ.pop("OPTIMIZARR_SECRET_KEY", None)
            else:
                os.environ["OPTIMIZARR_SECRET_KEY"] = env_backup
            cfg._SECRET_FILE = original_file


# ---------------------------------------------------------------------------
# Stereo plan JSON structure
# ---------------------------------------------------------------------------

class TestStereoPlan:
    def test_stereo_plan_structure(self):
        """Stereo plan JSON should have the required keys."""
        plan = json.dumps({
            "mode": "2d_to_3d",
            "format": "half_sbs",
            "divergence": 2.0,
            "convergence": 0.0,
            "depth_model": "ZoeD_N",
        })
        parsed = json.loads(plan)
        assert parsed["mode"] in ("2d_to_3d", "3d_to_2d")
        assert "format" in parsed
        assert isinstance(parsed["divergence"], (int, float))
        assert isinstance(parsed["convergence"], (int, float))
        assert "depth_model" in parsed

    def test_stereo_plan_none_is_valid(self):
        """stereo_plan=None means no 3D conversion — should be handled."""
        plan = None
        assert plan is None  # No conversion, encoder skips stereo phase


# ---------------------------------------------------------------------------
# Retry logic + startup recovery (Patch 27)
# ---------------------------------------------------------------------------

class TestRetryAndRecovery:
    def test_queue_has_retry_count_column(self, fresh_db):
        """The retry_count column should exist on queue and default to 0."""
        with fresh_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(queue)")
            columns = {row[1] for row in cursor.fetchall()}
        assert 'retry_count' in columns

        qid = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1)
        assert fresh_db.get_queue_item(qid)['retry_count'] == 0

    def test_setting_get_default_and_roundtrip(self, fresh_db):
        """get_setting returns default when unset, and set/get round-trips."""
        assert fresh_db.get_setting('max_retries', '3') == '3'
        assert fresh_db.get_setting('does_not_exist') is None
        fresh_db.set_setting('max_retries', '5')
        assert fresh_db.get_setting('max_retries') == '5'
        # Upsert overwrites rather than duplicating
        fresh_db.set_setting('max_retries', '7')
        assert fresh_db.get_setting('max_retries') == '7'

    def test_reset_stale_processing(self, fresh_db):
        """processing/paused items reset to pending; retry_count preserved."""
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, status='processing')
        b = fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, status='paused')
        c = fresh_db.add_to_queue(file_path="/x/c.mkv", profile_id=1, status='completed')
        fresh_db.update_queue_item(a, retry_count=2)

        reset = fresh_db.reset_stale_processing()
        assert reset == 2  # a + b, not the completed c

        assert fresh_db.get_queue_item(a)['status'] == 'pending'
        assert fresh_db.get_queue_item(b)['status'] == 'pending'
        assert fresh_db.get_queue_item(c)['status'] == 'completed'
        # Recovery is not a failure — retry_count must be left untouched
        assert fresh_db.get_queue_item(a)['retry_count'] == 2


# ---------------------------------------------------------------------------
# Rank-based priority scheme (Patch 29): 1 = first to encode
# ---------------------------------------------------------------------------

class TestPriorityRank:
    def test_new_items_append_at_end(self, fresh_db):
        """Items without explicit priority get sequential ranks 1, 2, 3…"""
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1)
        b = fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1)
        c = fresh_db.add_to_queue(file_path="/x/c.mkv", profile_id=1)
        assert fresh_db.get_queue_item(a)['priority'] == 1
        assert fresh_db.get_queue_item(b)['priority'] == 2
        assert fresh_db.get_queue_item(c)['priority'] == 3

    def test_queue_order_is_rank_ascending(self, fresh_db):
        """get_queue_items returns rank 1 first — the encoder's next job."""
        fresh_db.add_to_queue(file_path="/x/second.mkv", profile_id=1, priority=2)
        fresh_db.add_to_queue(file_path="/x/first.mkv", profile_id=1, priority=1)
        fresh_db.add_to_queue(file_path="/x/third.mkv", profile_id=1, priority=3)

        items = fresh_db.get_queue_items(status='pending')
        order = [i['file_path'] for i in items]
        assert order == ["/x/first.mkv", "/x/second.mkv", "/x/third.mkv"], order

    def test_fresh_db_has_priority_scheme_flag(self, fresh_db):
        """Fresh DBs are flagged so the renumber migration never re-runs."""
        assert fresh_db.get_setting('priority_scheme') == 'rank'

    def test_paginated_sorts_all_columns_both_directions(self, fresh_db):
        """Every UI-sortable column works asc and desc without falling back."""
        a = fresh_db.add_to_queue(
            file_path="/x/a.mkv", profile_id=1, file_size_bytes=100,
            estimated_savings_bytes=10, duration_seconds=60,
            current_specs={'codec': 'h264', 'height': 1080},
        )
        b = fresh_db.add_to_queue(
            file_path="/x/b.mkv", profile_id=1, file_size_bytes=200,
            estimated_savings_bytes=20, duration_seconds=120,
            current_specs={'codec': 'av1', 'height': 720},
        )

        for field, low_first in [
            ('file', a), ('size', a), ('savings', a), ('duration', a),
            ('codec', b),        # 'av1' < 'h264'
            ('resolution', b),   # 720 < 1080
            ('priority', a), ('status', a), ('progress', a), ('created_at', a),
        ]:
            asc = fresh_db.get_queue_items_paginated(sort_field=field, sort_dir='asc')
            desc = fresh_db.get_queue_items_paginated(sort_field=field, sort_dir='desc')
            assert len(asc['items']) == 2 and len(desc['items']) == 2
            # asc and desc must actually differ for fields with distinct values
            if field in ('file', 'size', 'savings', 'duration', 'codec', 'resolution', 'priority'):
                assert asc['items'][0]['id'] != desc['items'][0]['id'], \
                    f"sort direction had no effect for field '{field}'"
                assert asc['items'][0]['id'] == low_first, \
                    f"unexpected asc order for field '{field}'"


# ---------------------------------------------------------------------------
# Encode speed stats + time estimation (Patch 30)
# ---------------------------------------------------------------------------

class TestEncodeSpeedEstimation:
    def test_speed_stats_empty_history(self, fresh_db):
        """No usable history → no ratios, zero samples."""
        stats = fresh_db.get_encode_speed_stats()
        assert stats['overall'] is None
        assert stats['by_codec'] == {}
        assert stats['samples'] == 0

    def test_speed_stats_per_codec_and_overall(self, fresh_db):
        """Ratios group by target codec; overall is the median of all rows."""
        # h265 (NVENC): 1h video in 30min → ratio 0.5, two samples
        fresh_db.add_history(file_path="/x/a.mkv", encoding_time_seconds=1800,
                             duration_seconds=3600, codec='h265')
        fresh_db.add_history(file_path="/x/b.mkv", encoding_time_seconds=1800,
                             duration_seconds=3600, codec='h265')
        # av1 (software): 1h video in 4h → ratio 4.0, one sample
        fresh_db.add_history(file_path="/x/c.mkv", encoding_time_seconds=14400,
                             duration_seconds=3600, codec='av1')
        # Pre-Patch-25 row (duration unknown) must be excluded
        fresh_db.add_history(file_path="/x/old.mkv", encoding_time_seconds=999,
                             duration_seconds=0, codec='h265')

        stats = fresh_db.get_encode_speed_stats()
        assert stats['samples'] == 3
        assert abs(stats['by_codec']['h265'] - 0.5) < 1e-9
        assert abs(stats['by_codec']['av1'] - 4.0) < 1e-9
        # median of (0.5, 0.5, 4.0)
        assert abs(stats['overall'] - 0.5) < 1e-9

    def test_speed_stats_median_resists_outliers(self, fresh_db):
        """One polluted row (machine slept / legacy wall-clock) must not
        drag the ratio — the old mean went from 2.0 to >1000 here."""
        for i in range(4):
            fresh_db.add_history(file_path=f"/x/{i}.mkv",
                                 encoding_time_seconds=7200,
                                 duration_seconds=3600, codec='av1')
        # 34s clip recorded as taking 2 days (ratio ≈ 5082)
        fresh_db.add_history(file_path="/x/slept.mkv",
                             encoding_time_seconds=172800,
                             duration_seconds=34, codec='av1')
        stats = fresh_db.get_encode_speed_stats()
        assert abs(stats['by_codec']['av1'] - 2.0) < 1e-9

    def test_speed_stats_preprocessed_separate_group(self, fresh_db):
        """Upscale/stereo jobs learn their own ratio and never pollute the
        plain-encode group."""
        fresh_db.add_history(file_path="/x/plain.mkv", encoding_time_seconds=7200,
                             duration_seconds=3600, codec='av1')
        # 10-min video whose upscale+encode took ~42h → ratio 250
        fresh_db.add_history(file_path="/x/up.mkv", encoding_time_seconds=150000,
                             duration_seconds=600, codec='av1', preprocessed=True)
        stats = fresh_db.get_encode_speed_stats()
        assert abs(stats['by_codec']['av1'] - 2.0) < 1e-9
        assert stats['samples'] == 1
        assert abs(stats['preprocessed'] - 250.0) < 1e-9
        assert stats['preprocessed_samples'] == 1

        from app.scanner import estimate_encode_seconds
        # Preprocessed item uses the preprocessed ratio…
        assert estimate_encode_seconds(600, 'av1', stats, preprocessed=True) == 150000
        # …plain item uses the codec ratio
        assert estimate_encode_seconds(600, 'av1', stats) == 1200

    def test_speed_stats_ratio_clamp(self, fresh_db):
        """A plain-group median beyond sanity bounds is clamped (only the
        preprocessed group may legitimately exceed 100×)."""
        fresh_db.add_history(file_path="/x/bad.mkv", encoding_time_seconds=1000000,
                             duration_seconds=100, codec='av1')  # ratio 10000
        stats = fresh_db.get_encode_speed_stats()
        assert stats['by_codec']['av1'] == 100.0

    def test_estimate_encode_seconds(self):
        """Codec match > overall fallback > 1.0× realtime; None for unknown."""
        from app.scanner import estimate_encode_seconds

        stats = {'overall': 2.0, 'by_codec': {'av1': 4.0}, 'samples': 3}
        # Codec-specific ratio wins
        assert estimate_encode_seconds(600, 'av1', stats) == 2400
        # Unknown codec falls back to overall
        assert estimate_encode_seconds(600, 'h265', stats) == 1200
        # No history at all → 1.0× realtime
        assert estimate_encode_seconds(600, 'av1', {'overall': None, 'by_codec': {}}) == 600
        assert estimate_encode_seconds(600, 'av1', None) == 600
        # Unknown duration → None (caller sorts these last)
        assert estimate_encode_seconds(0, 'av1', stats) is None
        assert estimate_encode_seconds(None, 'av1', stats) is None


# ---------------------------------------------------------------------------
# Queue ETA + dashboard encode speed (Patch 31)
# ---------------------------------------------------------------------------

class TestQueueEta:
    def test_pending_duration_by_codec(self, fresh_db):
        """Durations group by target codec; only pending items count."""
        fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, duration_seconds=3600,
                              target_specs={'codec': 'h265'})
        fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, duration_seconds=1800,
                              target_specs={'codec': 'h265'})
        fresh_db.add_to_queue(file_path="/x/c.mkv", profile_id=1, duration_seconds=0,
                              target_specs={'codec': 'h265'})  # unknown duration
        fresh_db.add_to_queue(file_path="/x/d.mkv", profile_id=1, duration_seconds=900,
                              target_specs={'codec': 'av1'})
        fresh_db.add_to_queue(file_path="/x/done.mkv", profile_id=1, duration_seconds=999,
                              target_specs={'codec': 'h265'}, status='completed')

        groups = {g['codec']: g for g in fresh_db.get_pending_duration_by_codec()}
        assert groups['h265']['total_duration'] == 5400  # completed item excluded
        assert groups['h265']['unknown_count'] == 1
        assert groups['av1']['total_duration'] == 900
        assert groups['av1']['unknown_count'] == 0

    def test_attach_eta_envelope(self, fresh_db, monkeypatch):
        """Per-item ETAs use history speed; processing scales by progress."""
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        # History: h265 encodes at ratio 0.5 (2× realtime)
        fresh_db.add_history(file_path="/x/h.mkv", encoding_time_seconds=1800,
                             duration_seconds=3600, codec='h265')

        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, duration_seconds=1800,
                                  target_specs={'codec': 'h265'})
        b = fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, duration_seconds=0,
                                  target_specs={'codec': 'h265'})  # unknown
        c = fresh_db.add_to_queue(file_path="/x/c.mkv", profile_id=1, duration_seconds=3600,
                                  target_specs={'codec': 'h265'}, status='processing')
        fresh_db.update_queue_item(c, progress=50.0)
        # d: processing WITH a live HandBrake ETA — estimator must not clobber it
        d = fresh_db.add_to_queue(file_path="/x/d.mkv", profile_id=1, duration_seconds=3600,
                                  target_specs={'codec': 'h265'}, status='processing')
        fresh_db.update_queue_item(d, progress=10.0, eta_seconds=4321)

        envelope = fresh_db.get_queue_items_paginated(page=1, page_size=50)
        queue_routes._attach_eta(envelope)

        by_id = {i['id']: i for i in envelope['items']}
        assert by_id[a]['eta_seconds'] == 900          # 1800 × 0.5
        assert by_id[b]['eta_seconds'] == 0            # unknown duration → column default
        assert by_id[c]['eta_seconds'] == 900          # 3600 × 0.5 × (1 − 0.5)
        assert by_id[d]['eta_seconds'] == 4321         # real HandBrake ETA preserved
        # Total covers pending only: item a (900s); unknown counted separately
        assert envelope['pending_eta_seconds'] == 900
        assert envelope['pending_eta_unknown'] == 1

    def test_dashboard_avg_speed(self, fresh_db):
        """avg_speed_x averages per-file realtime multiples; old rows excluded."""
        # 2× realtime and 4× realtime → average 3.0×
        fresh_db.add_history(file_path="/x/a.mkv", encoding_time_seconds=1800,
                             duration_seconds=3600, codec='h265')
        fresh_db.add_history(file_path="/x/b.mkv", encoding_time_seconds=900,
                             duration_seconds=3600, codec='h265')
        # Pre-duration-tracking row must not drag the average down
        fresh_db.add_history(file_path="/x/old.mkv", encoding_time_seconds=500,
                             duration_seconds=0, codec='h265')

        totals = fresh_db.get_stats_dashboard(days=30)['totals']
        assert abs(totals['avg_speed_x'] - 3.0) < 1e-9

    def test_dashboard_includes_encode_speed_breakdown(self, fresh_db):
        """Dashboard payload carries per-codec speed ratios for the UI."""
        fresh_db.add_history(file_path="/x/a.mkv", encoding_time_seconds=1800,
                             duration_seconds=3600, codec='h265')
        data = fresh_db.get_stats_dashboard(days=30)
        assert 'encode_speed' in data
        assert abs(data['encode_speed']['by_codec']['h265'] - 0.5) < 1e-9
        assert data['encode_speed']['samples'] == 1


# ---------------------------------------------------------------------------
# Live encode progress parsing (Patch 33)
# ---------------------------------------------------------------------------

class TestHandBrakeProgressParsing:
    def test_full_progress_line(self):
        """Standard line with fps and ETA — all three values extracted."""
        from app.encoder import parse_hb_progress
        line = "Encoding: task 1 of 1, 23.45 % (113.06 fps, avg 102.33 fps, ETA 00h12m34s)"
        pct, fps, eta = parse_hb_progress(line)
        assert pct == 23.45
        assert fps == 113.06
        assert eta == 12 * 60 + 34

    def test_early_line_without_fps(self):
        """First lines of an encode lack the fps/ETA group."""
        from app.encoder import parse_hb_progress
        pct, fps, eta = parse_hb_progress("Encoding: task 1 of 1, 0.12 %")
        assert pct == 0.12
        assert fps is None
        assert eta is None

    def test_multi_hour_eta(self):
        """Real overnight-encode line from logs/handbrake.log."""
        from app.encoder import parse_hb_progress
        line = "Encoding: task 1 of 1, 4.88 % (2.48 fps, avg 2.94 fps, ETA 12h07m19s)"
        pct, fps, eta = parse_hb_progress(line)
        assert pct == 4.88
        assert fps == 2.48
        assert eta == 12 * 3600 + 7 * 60 + 19

    def test_non_progress_lines_ignored(self):
        from app.encoder import parse_hb_progress
        assert parse_hb_progress("Muxing: this may take awhile...") is None
        assert parse_hb_progress("") is None

    def test_queue_has_live_telemetry_columns(self, fresh_db):
        """Migration adds current_fps + eta_seconds with zero defaults."""
        qid = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1)
        item = fresh_db.get_queue_item(qid)
        assert item['current_fps'] == 0
        assert item['eta_seconds'] == 0
        fresh_db.update_queue_item(qid, current_fps=113.06, eta_seconds=754)
        item = fresh_db.get_queue_item(qid)
        assert item['current_fps'] == 113.06
        assert item['eta_seconds'] == 754


# ---------------------------------------------------------------------------
# Webhook dead-letter queue (Patch 34)
# ---------------------------------------------------------------------------

class TestWebhookDeadLetter:
    def test_event_lifecycle(self, fresh_db):
        """Persist → unprocessed → mark processed → no longer replayable."""
        eid = fresh_db.add_webhook_event('radarr', '{"eventType":"Download"}')
        pending = fresh_db.get_unprocessed_webhooks(hours=24)
        assert [e['id'] for e in pending] == [eid]
        assert fresh_db.count_unprocessed_webhooks() == 1

        fresh_db.mark_webhook_processed(eid)
        assert fresh_db.get_unprocessed_webhooks(hours=24) == []
        assert fresh_db.count_unprocessed_webhooks() == 0

    def test_error_keeps_event_replayable(self, fresh_db):
        """set_webhook_error records the failure but leaves it unprocessed."""
        eid = fresh_db.add_webhook_event('sonarr', '{}')
        fresh_db.set_webhook_error(eid, 'db locked')
        pending = fresh_db.get_unprocessed_webhooks(hours=24)
        assert len(pending) == 1
        assert pending[0]['error'] == 'db locked'
        assert pending[0]['processed_at'] is None

    def test_replay_window_excludes_old_events(self, fresh_db):
        """Unprocessed events older than the window are not replayed."""
        old = fresh_db.add_webhook_event('radarr', '{}')
        with fresh_db.get_connection() as conn:
            conn.execute(
                "UPDATE webhook_events SET received_at = datetime('now', '-48 hours') WHERE id = ?",
                (old,)
            )
        fresh = fresh_db.add_webhook_event('radarr', '{}')
        pending = fresh_db.get_unprocessed_webhooks(hours=24)
        assert [e['id'] for e in pending] == [fresh]
        # ...but stale unprocessed events still show on the health count
        assert fresh_db.count_unprocessed_webhooks() == 2

    def test_prune_keeps_unprocessed(self, fresh_db):
        """Prune removes only old PROCESSED rows."""
        done_old = fresh_db.add_webhook_event('radarr', '{}')
        fresh_db.mark_webhook_processed(done_old)
        fail_old = fresh_db.add_webhook_event('radarr', '{}')
        with fresh_db.get_connection() as conn:
            conn.execute(
                "UPDATE webhook_events SET received_at = datetime('now', '-10 days')"
            )
        removed = fresh_db.prune_webhook_events(days=7)
        assert removed == 1
        # The unprocessed one survives regardless of age
        assert fresh_db.count_unprocessed_webhooks() == 1

    def test_process_radarr_payload_queues_file(self, fresh_db, monkeypatch):
        """A Radarr Download payload queues the file; replay is idempotent."""
        from app.api import connection_routes
        monkeypatch.setattr(connection_routes, 'db', fresh_db)
        # Keep the wake-encoder block from touching the real encoder/scheduler
        from app.encoder import encoder_pool
        monkeypatch.setattr(encoder_pool, 'is_running', True)

        fresh_db.create_profile(
            name="WH", resolution="", framerate=None,
            codec="av1", encoder="svt_av1", quality=28,
            audio_codec="passthrough", container="mkv",
            audio_handling="preserve_all", subtitle_handling="none",
            chapter_markers=True, enable_filters=False,
            hw_accel_enabled=False, preset="8",
            two_pass=False, custom_args=None, is_default=True,
        )
        fresh_db.create_scan_root(path="/movies", profile_id=1)
        payload = {
            "eventType": "Download",
            "movieFile": {
                "path": "/movies/Some Movie (2024)/movie.mkv",
                "size": 4_000_000_000,
                "mediaInfo": {"videoCodec": "x264", "videoResolution": "1080p"},
            },
        }

        result = connection_routes.process_webhook_payload('radarr', payload)
        assert result['ok'], result
        items = fresh_db.get_queue_items()
        assert len(items) == 1
        assert items[0]['file_path'] == "/movies/Some Movie (2024)/movie.mkv"

        # Replaying the same payload must not duplicate the queue item
        result2 = connection_routes.process_webhook_payload('radarr', payload)
        assert result2['ok']
        assert 'already in queue' in result2['message'].lower()
        assert len(fresh_db.get_queue_items()) == 1

        # Ignored event types are deterministic non-actions
        result3 = connection_routes.process_webhook_payload('radarr', {"eventType": "Test"})
        assert result3['ok'] and 'ignored' in result3['message'].lower()


class TestWebhookAuth:
    """Webhook receiver requires the shared key and an in-library path."""

    def _setup(self, fresh_db, monkeypatch):
        from app.api import connection_routes
        monkeypatch.setattr(connection_routes, 'db', fresh_db)
        from app.encoder import encoder_pool
        monkeypatch.setattr(encoder_pool, 'is_running', True)
        fresh_db.create_profile(
            name="WH", resolution="", framerate=None,
            codec="av1", encoder="svt_av1", quality=28,
            audio_codec="passthrough", container="mkv",
            audio_handling="preserve_all", subtitle_handling="none",
            chapter_markers=True, enable_filters=False,
            hw_accel_enabled=False, preset="8",
            two_pass=False, custom_args=None, is_default=True,
        )
        return connection_routes

    @staticmethod
    def _payload(path):
        return {"eventType": "Download",
                "movieFile": {"path": path, "size": 1,
                              "mediaInfo": {"videoCodec": "x264"}}}

    def test_missing_or_wrong_key_rejected(self, fresh_db, monkeypatch):
        from fastapi import HTTPException
        cr = self._setup(fresh_db, monkeypatch)
        for bad in (None, '', 'wrong'):
            with pytest.raises(HTTPException) as exc:
                cr.receive_webhook('radarr', self._payload('/m/a.mkv'),
                                   key=bad, x_optimizarr_key=None)
            assert exc.value.status_code == 401
        # Rejected calls must not be persisted for replay
        assert fresh_db.count_unprocessed_webhooks() == 0

    def test_key_via_query_or_header(self, fresh_db, monkeypatch):
        cr = self._setup(fresh_db, monkeypatch)
        fresh_db.create_scan_root(path="/m", profile_id=1)
        secret = cr.get_webhook_secret()
        r1 = cr.receive_webhook('radarr', self._payload('/m/a.mkv'),
                                key=secret, x_optimizarr_key=None)
        r2 = cr.receive_webhook('radarr', self._payload('/m/b.mkv'),
                                key=None, x_optimizarr_key=secret)
        assert r1['ok'] and r2['ok']
        assert len(fresh_db.get_queue_items()) == 2

    def test_secret_is_stable(self, fresh_db, monkeypatch):
        cr = self._setup(fresh_db, monkeypatch)
        assert cr.get_webhook_secret() == cr.get_webhook_secret()
        assert len(cr.get_webhook_secret()) >= 32

    def test_path_outside_scan_roots_refused(self, fresh_db, monkeypatch):
        cr = self._setup(fresh_db, monkeypatch)
        fresh_db.create_scan_root(path="/m", profile_id=1)
        for path in ('/etc/passwd.mkv', '/m2/a.mkv', '/m/../other/a.mkv'):
            assert not fresh_db.is_queued(path)
            res = cr.process_webhook_payload('radarr', self._payload(path))
            assert not res['ok'], path
            assert 'scan root' in res['message']
        assert fresh_db.get_queue_items() == []

    def test_unknown_app_type_rejected(self, fresh_db, monkeypatch):
        from fastapi import HTTPException
        cr = self._setup(fresh_db, monkeypatch)
        with pytest.raises(HTTPException) as exc:
            cr.receive_webhook('evil', {}, key=cr.get_webhook_secret(),
                               x_optimizarr_key=None)
        assert exc.value.status_code == 400


class TestAtomicQueueDedupe:
    def test_skip_if_queued(self, fresh_db):
        first = fresh_db.add_to_queue(skip_if_queued=True, file_path='/a.mkv',
                                      root_id=1, profile_id=1)
        second = fresh_db.add_to_queue(skip_if_queued=True, file_path='/a.mkv',
                                       root_id=1, profile_id=1)
        assert first is not None and second is None
        assert fresh_db.is_queued('/a.mkv') and not fresh_db.is_queued('/b.mkv')
        assert len(fresh_db.get_queue_items()) == 1

    def test_concurrent_inserts_queue_once(self, fresh_db):
        import threading
        results = []
        def add():
            results.append(fresh_db.add_to_queue(
                skip_if_queued=True, file_path='/race.mkv', root_id=1, profile_id=1))
        threads = [threading.Thread(target=add) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert sum(r is not None for r in results) == 1
        assert len(fresh_db.get_queue_items()) == 1


class TestTargetQuality:
    @staticmethod
    def _curve(q):
        # Monotonic like real data: VMAF falls, size falls as q rises
        return 100 - (q - 10) * 0.6, 2.0 - q * 0.04

    def test_search_finds_largest_passing_quality(self):
        from app.target_quality import search
        calls = []

        def ev(q):
            calls.append(q)
            return self._curve(q)
        r = search(ev, 15, 55, 30, target=90)
        # 100-(q-10)*0.6 >= 90  ->  q <= 26.67  ->  26
        assert r['quality'] == 26 and r['met_target']
        assert len(calls) <= 6

    def test_search_unreachable_target_stops_when_already_bigger(self):
        from app.target_quality import search
        calls = []

        def ev(q):
            calls.append(q)
            return 50.0, 1.3        # never reaches target, already bigger
        r = search(ev, 15, 55, 30, target=95)
        assert not r['met_target'] and calls == [30]

    def test_search_all_pass_goes_to_max(self):
        from app.target_quality import search
        r = search(lambda q: (99.0, 0.3), 15, 55, 30, target=90)
        assert r['quality'] == 55

    def test_ranges_and_samples(self):
        from app.target_quality import quality_range, sample_starts
        assert quality_range('svt_av1_10bit') == (15, 55)
        assert quality_range('nvenc_h265_10bit') == (15, 40)
        assert quality_range('vt_h265') is None          # inverted scale: unsupported
        s = sample_starts(100, 3, 10)
        assert s == [22.5, 45.0, 67.5]

    def test_sample_command_is_video_only_uncropped(self, fresh_db, monkeypatch):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        monkeypatch.setattr(enc, 'detect_hardware_acceleration', lambda: {'encoder_names': []})
        qid = fresh_db.add_to_queue(file_path='/x/a.mkv', root_id=1, profile_id=1,
                                    current_specs={'bit_depth': 8}, target_specs={})
        job = enc.EncodingJob(qid, {'name': 'p', 'codec': 'av1', 'encoder': 'svt_av1_10bit',
                                    'quality': 30, 'container': 'mkv', 'chapter_markers': True})
        cmd = job.build_command(input_override='/t/s.mkv', output_override='/t/o.mkv',
                                quality_override=24, video_only=True)
        assert cmd[cmd.index('-o') + 1] == '/t/o.mkv'
        assert cmd[cmd.index('--quality') + 1] == '24'
        assert cmd[cmd.index('--audio') + 1] == 'none'
        assert cmd[cmd.index('--crop-mode') + 1] == 'none'
        assert '--markers' not in cmd
        job._tq_quality = 27                       # chosen value used for the real encode
        assert job.build_command()[job.build_command().index('--quality') + 1] == '27'


class TestLowBitrateSkip:
    SPECS = {'codec': 'h264', 'width': 1920, 'height': 1080, 'framerate': 24.0}

    def test_bpp_and_codec_scaling(self):
        from app.scanner import source_bpp, low_bitrate_reason
        low = dict(self.SPECS, bit_rate=1_200_000)       # ~0.024 bpp
        high = dict(self.SPECS, bit_rate=8_000_000)      # ~0.16 bpp
        assert round(source_bpp(low), 3) == 0.024
        assert low_bitrate_reason(low, 0.03).startswith('Skipped')
        assert low_bitrate_reason(high, 0.03) is None
        assert low_bitrate_reason(low, 0) is None                       # disabled
        mid = dict(self.SPECS, bit_rate=2_000_000)                       # ~0.040 bpp
        assert low_bitrate_reason(mid, 0.03) is None                    # h264: fine
        assert low_bitrate_reason(dict(mid, codec='h265'), 0.03)        # hevc needs 0.045
        assert low_bitrate_reason({'codec': 'h264'}, 0.03) is None      # unknown -> encode

    def _job(self, fresh_db, monkeypatch, specs, upscale=None):
        import json
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        qid = fresh_db.add_to_queue(file_path='/x/a.mkv', root_id=1, profile_id=1,
                                    current_specs=specs, target_specs={},
                                    upscale_plan=json.dumps(upscale) if upscale else None)
        return enc.EncodingJob(qid, {'name': 'p', 'codec': 'av1', 'encoder': 'svt_av1_10bit',
                                     'quality': 30, 'container': 'mkv'}), qid

    def test_encode_start_skips_low_bitrate(self, fresh_db, monkeypatch):
        """Only a source that already MEETS the profile (here: already AV1)
        is skipped for being efficient."""
        import app.encoder as enc
        monkeypatch.setattr(enc, 'preflight_source_check', lambda p: None)
        job, qid = self._job(fresh_db, monkeypatch, dict(self.SPECS, codec='av1', bit_rate=900_000))
        assert job.start() is False
        item = fresh_db.get_queue_item(qid)
        assert item['status'] == 'kept_original' and 'already efficient' in item['error_message']

    def test_wrong_codec_low_bitrate_still_converts(self, fresh_db, monkeypatch):
        """An H.264 source under an AV1 profile doesn't meet it — the
        efficiency skip must not keep it as H.264."""
        import app.encoder as enc
        monkeypatch.setattr(enc, 'preflight_source_check', lambda p: None)
        job, qid = self._job(fresh_db, monkeypatch, dict(self.SPECS, bit_rate=900_000))
        job._refresh_stale_specs = lambda p: None
        job._unmet = None
        assert job._unmet_targets() == ['codec h264 → av1']
        assert enc.EncodingJob._low_bitrate_skip_reason(job)  # efficient, but…
        job._unmet = job._unmet_targets()
        assert (None if job._unmet else job._low_bitrate_skip_reason()) is None

    def test_upscale_jobs_exempt_and_setting_respected(self, fresh_db, monkeypatch):
        specs = dict(self.SPECS, bit_rate=900_000)
        job, _ = self._job(fresh_db, monkeypatch, specs, upscale={'enabled': True})
        assert job._low_bitrate_skip_reason() is None
        fresh_db.set_setting('min_source_bpp', '0')
        job, _ = self._job(fresh_db, monkeypatch, specs)
        assert job._low_bitrate_skip_reason() is None


class TestArrPaths:
    CONN = {'id': 1, 'name': 'Sonarr', 'app_type': 'sonarr', 'enabled': 1,
            'path_mappings': '[{"remote": "/tv", "local": "F:\\\\tv shows"},'
                             ' {"remote": "/downloads", "local": "D:\\\\Downloads"}]'}

    def test_remote_to_local_and_back(self):
        from app.arr_paths import to_local, to_remote
        assert to_local(self.CONN, '/tv/Show/S01/e1.mkv') == 'F:\\tv shows\\Show\\S01\\e1.mkv'
        assert to_local(self.CONN, '/tvx/a.mkv') == '/tvx/a.mkv'     # prefix must be whole
        assert to_remote(self.CONN, 'F:\\TV Shows\\Show\\e1.mkv') == '/tv/Show/e1.mkv'  # case-insensitive
        assert to_remote(self.CONN, 'E:\\other\\x.mkv') is None
        assert to_remote({'path_mappings': '[]'}, 'E:\\x.mkv') == 'E:\\x.mkv'

    def test_validate_mappings(self):
        import json
        from app.arr_paths import validate_mappings
        assert json.loads(validate_mappings([{'remote': ' /tv ', 'local': 'F:\\tv'}])) == \
            [{'remote': '/tv', 'local': 'F:\\tv'}]
        with pytest.raises(ValueError):
            validate_mappings([{'remote': '/tv'}])

    def test_webhook_path_is_translated(self, fresh_db, monkeypatch):
        import json
        from app.api import connection_routes as cr
        monkeypatch.setattr(cr, 'db', fresh_db)
        from app.encoder import encoder_pool
        monkeypatch.setattr(encoder_pool, 'is_running', True)
        fresh_db.create_profile(name="P", codec="av1", encoder="svt_av1_10bit",
                                quality=30, audio_codec="aac", is_default=True)
        fresh_db.create_scan_root(path="F:\\tv shows", profile_id=1)
        fresh_db.create_external_connection(
            name='Sonarr', app_type='sonarr', base_url='http://localhost:8989',
            api_key_encrypted='x', path_mappings=json.dumps(
                [{'remote': '/tv', 'local': 'F:\\tv shows'}]))
        res = cr.process_webhook_payload('sonarr', {
            'eventType': 'Download',
            'episodeFile': {'path': '/tv/Show/S01E01.mkv', 'size': 1,
                            'mediaInfo': {'videoCodec': 'x264'}}})
        assert res['ok'], res
        assert fresh_db.is_queued('F:\\tv shows\\Show\\S01E01.mkv')

    def test_refresh_rescans_owning_series(self, fresh_db, monkeypatch):
        import json
        import app.arr_paths as ap
        from app.external_connections import connection_manager
        monkeypatch.setattr('app.database.db', fresh_db)
        fresh_db.create_external_connection(
            name='Sonarr', app_type='sonarr', base_url='http://x', api_key_encrypted='k',
            path_mappings=json.dumps([{'remote': '/tv', 'local': 'F:\\tv shows'}]))
        ap._cache.clear()
        monkeypatch.setattr(connection_manager, '_get', lambda c, ep, params=None: [
            {'id': 7, 'title': 'Show', 'path': '/tv/Show'},
            {'id': 8, 'title': 'Show 2', 'path': '/tv/Show 2'}])
        posts = []
        monkeypatch.setattr(connection_manager, '_post', lambda c, ep, body: posts.append((ep, body)))
        done = ap.refresh_for_path('F:\\tv shows\\Show 2\\S01E01.mkv')
        assert posts == [('/command', {'name': 'RescanSeries', 'seriesId': 8})]
        assert done == ['Sonarr: Show 2']
        posts.clear()
        assert ap.refresh_for_path('E:\\unrelated\\x.mkv') == [] and posts == []


class TestWebhookKeyUrls:
    def test_with_webhook_key_replaces_existing(self):
        from app.external_connections import with_webhook_key, redact_webhook_key
        url = with_webhook_key('http://h:5757/api/webhooks/sonarr?key=old&x=1', 'NEW')
        assert 'key=NEW' in url and 'key=old' not in url and 'x=1' in url
        assert redact_webhook_key(url).count('****') == 1
        assert 'NEW' not in redact_webhook_key(url)

    def test_upgrade_updates_only_our_webhooks(self, monkeypatch):
        from app.external_connections import ExternalConnectionManager
        mgr = ExternalConnectionManager()
        notes = [
            {"id": 1, "name": "Optimizarr", "implementation": "Webhook",
             "fields": [{"name": "url", "value": "http://h:5757/api/webhooks/sonarr"}]},
            {"id": 2, "name": "Discord", "implementation": "Discord", "fields": []},
            {"id": 3, "name": "Other", "implementation": "Webhook",
             "fields": [{"name": "url", "value": "http://elsewhere/hook"}]},
        ]
        puts = []
        monkeypatch.setattr(mgr, '_get', lambda conn, path, params=None: notes)
        monkeypatch.setattr(mgr, '_put', lambda conn, path, body: puts.append((path, body)) or body)
        n = mgr.upgrade_webhook_secret({"app_type": "sonarr"}, 'SEC')
        assert n == 1
        assert puts[0][0] == '/notification/1'
        assert 'key=SEC' in puts[0][1]['fields'][0]['value']
        # Second run is a no-op once the key is present
        assert mgr.upgrade_webhook_secret({"app_type": "sonarr"}, 'SEC') == 0


class TestAuthHardening:
    """Login throttling, token revocation and stream-token scoping."""

    def _user(self, fresh_db, monkeypatch, password='correct-horse'):
        import app.auth as auth_mod
        monkeypatch.setattr(auth_mod, 'db', fresh_db)
        uid = fresh_db.create_user('u', auth_mod.auth.hash_password(password), is_admin=True)
        return auth_mod, fresh_db.get_user_by_id(uid)

    def test_rate_limiter_locks_after_max_failures(self):
        from app.auth import LoginRateLimiter
        rl = LoginRateLimiter(max_failures=3, window=60)
        for _ in range(2):
            rl.record_failure('ip')
        assert rl.retry_after('ip') == 0
        rl.record_failure('ip')
        assert rl.retry_after('ip') > 0
        assert rl.retry_after('other-ip') == 0
        rl.reset('ip')
        assert rl.retry_after('ip') == 0

    def test_login_route_throttles(self, fresh_db, monkeypatch):
        from fastapi import HTTPException
        from app.api import auth_routes
        from app.api.models import LoginRequest
        from app.auth import LoginRateLimiter
        auth_mod, _ = self._user(fresh_db, monkeypatch)
        monkeypatch.setattr(auth_routes, 'login_limiter', LoginRateLimiter(max_failures=2))

        class _Req:
            class client:
                host = '10.0.0.9'
        bad = LoginRequest(username='u', password='nope')
        for _ in range(2):
            with pytest.raises(HTTPException) as exc:
                auth_routes.login(bad, _Req())
            assert exc.value.status_code == 401
        # Even the right password is refused while locked out
        with pytest.raises(HTTPException) as exc:
            auth_routes.login(LoginRequest(username='u', password='correct-horse'), _Req())
        assert exc.value.status_code == 429

    def test_password_change_revokes_old_tokens(self, fresh_db, monkeypatch):
        auth_mod, user = self._user(fresh_db, monkeypatch)
        a = auth_mod.auth
        old = a.create_access_token(user['id'], 'u', True, user['token_version'])
        assert a.user_from_token(old)['id'] == user['id']
        fresh_db.set_user_password(user['id'], a.hash_password('another-pass'))
        assert a.user_from_token(old) is None
        user = fresh_db.get_user_by_id(user['id'])
        new = a.create_access_token(user['id'], 'u', True, user['token_version'])
        assert a.user_from_token(new) is not None

    def test_stream_token_scope_is_separate(self, fresh_db, monkeypatch):
        auth_mod, user = self._user(fresh_db, monkeypatch)
        a = auth_mod.auth
        stream = a.create_stream_token(user)
        api = a.create_access_token(user['id'], 'u', True, user['token_version'])
        assert a.user_from_token(stream, scope='stream') is not None
        assert a.user_from_token(stream) is None            # not an API token
        assert a.user_from_token(api, scope='stream') is None  # not a stream token

    def test_weak_default_admin_must_change_password(self, fresh_db, monkeypatch):
        import app.auth as auth_mod
        monkeypatch.setattr(auth_mod, 'db', fresh_db)
        monkeypatch.setattr(auth_mod.settings, 'admin_username', 'boot')
        monkeypatch.setattr(auth_mod.settings, 'admin_password', 'admin')
        auth_mod.auth.create_default_admin()
        assert fresh_db.get_user_by_username('boot')['must_change_password']

    def test_strong_admin_password_not_flagged(self, fresh_db, monkeypatch):
        import app.auth as auth_mod
        monkeypatch.setattr(auth_mod, 'db', fresh_db)
        monkeypatch.setattr(auth_mod.settings, 'admin_username', 'boot2')
        monkeypatch.setattr(auth_mod.settings, 'admin_password', 'a-long-unique-pass')
        auth_mod.auth.create_default_admin()
        assert not fresh_db.get_user_by_username('boot2')['must_change_password']


# ---------------------------------------------------------------------------
# Permission-error re-check (Patch 35)
# ---------------------------------------------------------------------------

class TestPermissionRecheck:
    def test_accessible_file_flips_to_pending(self, fresh_db, tmp_path, monkeypatch):
        """A previously blocked file that's readable now goes pending."""
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        f = tmp_path / "movie.mkv"
        f.write_bytes(b"x")
        qid = fresh_db.add_to_queue(file_path=str(f), profile_id=1,
                                    status='permission_error')

        item = fresh_db.get_queue_item(qid)
        assert queue_routes._recheck_permission_item(item) is True
        refreshed = fresh_db.get_queue_item(qid)
        assert refreshed['status'] == 'pending'
        assert refreshed['permission_status'] == 'ok'
        assert refreshed['error_message'] is None

    def test_missing_file_stays_blocked_with_reason(self, fresh_db, tmp_path, monkeypatch):
        """A still-missing file keeps permission_error and records WHY."""
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        missing = str(tmp_path / "gone.mkv")
        qid = fresh_db.add_to_queue(file_path=missing, profile_id=1,
                                    status='permission_error')

        item = fresh_db.get_queue_item(qid)
        assert queue_routes._recheck_permission_item(item) is False
        refreshed = fresh_db.get_queue_item(qid)
        assert refreshed['status'] == 'permission_error'
        assert refreshed['permission_status'] == 'not_found'
        assert 'does not exist' in (refreshed['permission_message'] or '')


# ---------------------------------------------------------------------------
# Health page completion (Patch 36)
# ---------------------------------------------------------------------------

class TestHealthHelpers:
    def test_tool_version_probe_and_cache(self):
        """_get_tool_version returns the first version line and caches it."""
        import sys
        from app.api import system_routes
        system_routes._tool_versions.pop('_test_py', None)

        v = system_routes._get_tool_version([sys.executable, '--version'], '_test_py')
        assert v and v.startswith('Python 3')
        # Cached: a bogus command with the same key must return the cached value
        v2 = system_routes._get_tool_version(['definitely-not-a-real-binary'], '_test_py')
        assert v2 == v

    def test_tool_version_failure_returns_none(self):
        from app.api import system_routes
        system_routes._tool_versions.pop('_test_missing', None)
        v = system_routes._get_tool_version(['definitely-not-a-real-binary'], '_test_missing')
        assert v is None

    def test_compute_pending_eta(self, fresh_db, monkeypatch):
        """Shared ETA helper totals pending durations × codec speed ratios."""
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        # h265 history: 2× realtime (ratio 0.5)
        fresh_db.add_history(file_path="/x/h.mkv", encoding_time_seconds=1800,
                             duration_seconds=3600, codec='h265')
        fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, duration_seconds=7200,
                              target_specs={'codec': 'h265'})
        fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, duration_seconds=0,
                              target_specs={'codec': 'h265'})  # unknown

        eta, unknown = queue_routes.compute_pending_eta()
        assert eta == 3600   # 7200s of video at 2× realtime
        assert unknown == 1


# ---------------------------------------------------------------------------
# Auto-sync interval (Patch 37)
# ---------------------------------------------------------------------------

class TestAutoSyncInterval:
    def test_sync_due_logic(self):
        """_sync_due fires only for enabled connections past their interval."""
        from app.scheduler import _sync_due
        from datetime import datetime, timedelta
        now = datetime(2026, 6, 12, 12, 0, 0)

        # Off (interval 0) — never due
        assert _sync_due({'enabled': True, 'sync_interval_hours': 0,
                          'last_synced': None}, now) is False
        # Disabled connection — never due even with an interval
        assert _sync_due({'enabled': False, 'sync_interval_hours': 6,
                          'last_synced': None}, now) is False
        # Enabled + interval + never synced — due now
        assert _sync_due({'enabled': True, 'sync_interval_hours': 6,
                          'last_synced': None}, now) is True
        # Synced 7h ago, 6h interval — due
        old = (now - timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')
        assert _sync_due({'enabled': True, 'sync_interval_hours': 6,
                          'last_synced': old}, now) is True
        # Synced 2h ago, 6h interval — NOT due
        recent = (now - timedelta(hours=2)).strftime('%Y-%m-%d %H:%M:%S')
        assert _sync_due({'enabled': True, 'sync_interval_hours': 6,
                          'last_synced': recent}, now) is False
        # Garbage timestamp — treated as due (fail-safe)
        assert _sync_due({'enabled': True, 'sync_interval_hours': 6,
                          'last_synced': 'not-a-date'}, now) is True

    def test_connection_persists_sync_interval(self, fresh_db):
        """Migration + CRUD round-trip the sync_interval_hours column."""
        cid = fresh_db.create_external_connection(
            name="Radarr", app_type="radarr", base_url="http://x:7878",
            api_key_encrypted="enc", sync_interval_hours=12,
        )
        assert fresh_db.get_external_connection(cid)['sync_interval_hours'] == 12
        fresh_db.update_external_connection(cid, sync_interval_hours=24)
        assert fresh_db.get_external_connection(cid)['sync_interval_hours'] == 24
        # Default when unspecified is 0 (off)
        cid2 = fresh_db.create_external_connection(
            name="Sonarr", app_type="sonarr", base_url="http://y:8989",
            api_key_encrypted="enc",
        )
        assert fresh_db.get_external_connection(cid2)['sync_interval_hours'] == 0


# ---------------------------------------------------------------------------
# JSON config export / import (Patch 38)
# ---------------------------------------------------------------------------

class TestConfigExportImport:
    def _make_profile(self, db, name, is_default=False):
        return db.create_profile(
            name=name, resolution="", framerate=None, codec="av1",
            encoder="svt_av1", quality=28, audio_codec="passthrough",
            container="mkv", audio_handling="preserve_all",
            subtitle_handling="none", chapter_markers=True,
            enable_filters=False, hw_accel_enabled=False, preset="8",
            two_pass=False, custom_args=None, is_default=is_default,
        )

    def test_export_shape(self, fresh_db, monkeypatch):
        """Export carries profiles/scan_roots/settings; scan roots use
        profile NAME; connections carry no api key."""
        import asyncio
        from app.api import system_routes
        monkeypatch.setattr(system_routes, 'db', fresh_db)

        pid = self._make_profile(fresh_db, "Movies AV1", is_default=True)
        fresh_db.create_scan_root(path="/media/movies", profile_id=pid,
                                  library_type="movie")
        fresh_db.set_setting('max_retries', '5')
        fresh_db.create_external_connection(
            name="R", app_type="radarr", base_url="http://x:7878",
            api_key_encrypted="SECRET-ENCRYPTED", sync_interval_hours=6,
        )

        cfg = system_routes.export_config_json(current_user={})
        assert cfg["optimizarr_config"] is True
        assert cfg["profiles"][0]["name"] == "Movies AV1"
        assert "id" not in cfg["profiles"][0]
        assert cfg["scan_roots"][0]["profile_name"] == "Movies AV1"
        assert "external_connection_id" not in cfg["scan_roots"][0]
        assert cfg["settings"]["max_retries"] == "5"
        # Connections: metadata only, NO key field of any kind
        conn = cfg["connections"][0]
        assert conn["name"] == "R" and conn["sync_interval_hours"] == 6
        assert not any("key" in k.lower() for k in conn)
        blob = json.dumps(cfg)
        assert "SECRET-ENCRYPTED" not in blob

    def test_import_merges_non_destructively(self, fresh_db, monkeypatch):
        """Import adds new items, skips existing by name/path, applies
        settings, and never imports connections."""
        import asyncio
        from app.api import system_routes
        monkeypatch.setattr(system_routes, 'db', fresh_db)

        # Pre-existing default profile that must NOT be displaced
        self._make_profile(fresh_db, "Existing", is_default=True)
        fresh_db.create_scan_root(path="/old", profile_id=1, library_type="custom")

        config = {
            "optimizarr_config": True,
            "profiles": [
                {"name": "Existing", "codec": "h265", "quality": 24},   # dup → skip
                {"name": "New HEVC", "codec": "h265", "encoder": "x265",
                 "quality": 22, "audio_codec": "aac",
                 "is_default": True},                          # added, but flag dropped
            ],
            "scan_roots": [
                {"path": "/old", "profile_name": "Existing"},  # dup → skip
                {"path": "/new", "profile_name": "New HEVC", "library_type": "tv_show"},
            ],
            "settings": {"max_retries": "9"},
            "connections": [{"name": "R", "app_type": "radarr"}],
        }
        res = system_routes.import_config_json(config, current_user={})
        s = res["summary"]
        assert s["profiles_added"] == 1 and s["profiles_skipped"] == 1
        assert s["scan_roots_added"] == 1 and s["scan_roots_skipped"] == 1
        assert s["settings_applied"] == 1
        assert s["connections_skipped"] == 1

        # New profile exists but did NOT steal the default flag
        names = {p["name"]: p for p in fresh_db.get_profiles()}
        assert "New HEVC" in names
        assert names["New HEVC"]["is_default"] == 0
        assert names["Existing"]["is_default"] == 1
        # New scan root resolved to the imported profile by name
        roots = {r["path"]: r for r in fresh_db.get_scan_roots()}
        assert roots["/new"]["profile_id"] == names["New HEVC"]["id"]
        assert fresh_db.get_setting("max_retries") == "9"
        # No connections were created
        assert fresh_db.get_external_connections() == []

    def test_import_rejects_foreign_json(self, fresh_db, monkeypatch):
        import asyncio
        from fastapi import HTTPException
        from app.api import system_routes
        monkeypatch.setattr(system_routes, 'db', fresh_db)
        with pytest.raises(HTTPException) as ei:
            system_routes.import_config_json({"foo": "bar"}, current_user={})
        assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# Header-stats audit: SQL counts + corruption tolerance (frozen-UI fix)
# ---------------------------------------------------------------------------

class TestStatsAndResilience:
    def test_collect_stats_counts_in_sql(self, fresh_db, monkeypatch):
        """_collect_stats returns status counts + history savings."""
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, status='pending')
        fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, status='pending')
        fresh_db.add_to_queue(file_path="/x/c.mkv", profile_id=1, status='processing')
        fresh_db.add_to_queue(file_path="/x/d.mkv", profile_id=1, status='completed')
        fresh_db.add_history(file_path="/x/d.mkv", savings_bytes=1_000_000_000)

        s = queue_routes._collect_stats()
        assert s['queue_pending'] == 2
        assert s['queue_processing'] == 1
        assert s['queue_completed'] == 1
        assert s['total_files_processed'] == 1
        assert s['total_space_saved_bytes'] == 1_000_000_000
        assert s['total_space_saved_gb'] == round(1_000_000_000 / (1024**3), 2)

    def test_corrupt_specs_does_not_crash_queue_read(self, fresh_db):
        """A malformed current_specs cell must not break the whole listing —
        the row is returned with specs=None instead of raising."""
        good = fresh_db.add_to_queue(file_path="/x/good.mkv", profile_id=1,
                                     current_specs={'codec': 'h264'})
        bad = fresh_db.add_to_queue(file_path="/x/bad.mkv", profile_id=1)
        # Corrupt the JSON cell directly
        with fresh_db.get_connection() as conn:
            conn.execute("UPDATE queue SET current_specs = ? WHERE id = ?",
                         ('{not valid json', bad))

        items = fresh_db.get_queue_items()          # must not raise
        assert len(items) == 2
        by_id = {i['id']: i for i in items}
        assert by_id[good]['current_specs'] == {'codec': 'h264'}
        assert by_id[bad]['current_specs'] is None  # tolerated, not fatal

        # Single-item and paginated reads tolerate it too
        assert fresh_db.get_queue_item(bad)['current_specs'] is None
        env = fresh_db.get_queue_items_paginated(page=1, page_size=50)
        assert env['total'] == 2

    def test_reads_are_lock_free(self, fresh_db):
        """A read must NOT block while a write holds the global lock.

        WAL allows concurrent readers; serializing them behind _write_lock is
        what let a busy encoder freeze the UI. With the old locked reads this
        would deadlock (non-reentrant lock held by another party); now it
        returns immediately.
        """
        import threading
        fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1)

        result = {}
        fresh_db._write_lock.acquire()   # simulate a write in progress
        try:
            def reader():
                result['items'] = fresh_db.get_queue_items()
                result['stats'] = fresh_db.get_queue_item(1)
            t = threading.Thread(target=reader)
            t.start()
            t.join(timeout=5)
            assert not t.is_alive(), "read blocked on the write lock (still serialized)"
            assert len(result['items']) == 1
        finally:
            fresh_db._write_lock.release()

    def test_writes_still_serialize(self, fresh_db):
        """Writes keep taking the lock (default write=True)."""
        # While the lock is held, a write must wait — prove the write path
        # still acquires it (a non-blocking acquire by another holder fails).
        assert fresh_db._write_lock.acquire(blocking=False) is True
        try:
            import threading
            done = []
            def writer():
                fresh_db.set_setting('x', '1')   # write path → must block on lock
                done.append(True)
            t = threading.Thread(target=writer)
            t.start()
            t.join(timeout=1)
            assert done == [], "write did not wait for the lock"
        finally:
            fresh_db._write_lock.release()
        t.join(timeout=5)
        assert fresh_db.get_setting('x') == '1'   # completed once lock freed


# ---------------------------------------------------------------------------
# Retry backoff (Patch 39)
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    def test_next_pending_skips_backoff(self, fresh_db):
        """get_next_pending_item ignores items whose retry_after is future,
        returns eligible ones, and orders by rank."""
        # a: in backoff (future) — must be skipped
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, priority=1)
        fresh_db.update_queue_item(a, retry_after="2999-01-01 00:00:00")
        # b: eligible, rank 3
        b = fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, priority=3)
        # c: eligible, rank 2 — should win on priority ASC
        c = fresh_db.add_to_queue(file_path="/x/c.mkv", profile_id=1, priority=2)

        nxt = fresh_db.get_next_pending_item()
        assert nxt['id'] == c, "should pick lowest-rank eligible item"

        # Past retry_after counts as eligible
        fresh_db.update_queue_item(a, retry_after="2000-01-01 00:00:00")
        fresh_db.update_queue_item(c, status='completed')
        fresh_db.update_queue_item(b, status='completed')
        assert fresh_db.get_next_pending_item()['id'] == a

    def test_count_pending_vs_eligible(self, fresh_db):
        """count_pending sees backed-off items even when none are eligible —
        lets the encoder distinguish 'empty' from 'all waiting'."""
        x = fresh_db.add_to_queue(file_path="/x/x.mkv", profile_id=1)
        fresh_db.update_queue_item(x, retry_after="2999-01-01 00:00:00")
        assert fresh_db.count_pending() == 1
        assert fresh_db.get_next_pending_item() is None

    def test_handle_failure_sets_exponential_backoff(self, fresh_db, monkeypatch):
        """_handle_failure re-queues with an increasing retry_after delay."""
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        fresh_db.set_setting('max_retries', '3')

        qid = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1,
                                    status='processing')
        prof = {'name': 'P', 'codec': 'av1', 'container': 'mkv'}
        job = enc.EncodingJob.__new__(enc.EncodingJob)
        job.queue_item_id = qid
        job.queue_item = {'file_path': '/x/a.mkv'}
        job.profile = prof

        # First failure → pending, retry_count 1, retry_after ~60s out
        status = job._handle_failure("boom")
        assert status == 'pending'
        item = fresh_db.get_queue_item(qid)
        assert item['retry_count'] == 1
        assert item['retry_after'] is not None
        assert fresh_db.get_next_pending_item() is None  # gated by backoff

        # Exhaust retries → permanent failure, no further backoff scheduling
        fresh_db.update_queue_item(qid, retry_count=3, status='processing')
        assert job._handle_failure("boom again") == 'failed'
        assert fresh_db.get_queue_item(qid)['status'] == 'failed'

    def test_manual_retry_clears_backoff(self, fresh_db, monkeypatch):
        """Manual retry is immediate — clears retry_after + counter."""
        import asyncio
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        qid = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1,
                                    status='failed')
        fresh_db.update_queue_item(qid, retry_count=3,
                                   retry_after="2999-01-01 00:00:00")
        queue_routes.retry_queue_item(qid, current_user={})
        item = fresh_db.get_queue_item(qid)
        assert item['status'] == 'pending'
        assert item['retry_count'] == 0
        assert item['retry_after'] is None
        assert fresh_db.get_next_pending_item()['id'] == qid  # eligible now


# ---------------------------------------------------------------------------
# Scheduler safety — the weekend bug (Patch 41)
# ---------------------------------------------------------------------------

class _FakePool:
    def __init__(self, running=True):
        self.is_running = running
        self.stop_calls = []
    def stop(self, mark_cancelled=True, graceful=False):
        self.stop_calls.append({'graceful': graceful})
        self.is_running = False
    def process_queue(self):
        self.is_running = True


class TestSchedulerSafety:
    def _mgr(self):
        from app.scheduler import ScheduleManager
        return ScheduleManager()

    def test_disabled_schedule_never_stops_encoding(self, monkeypatch):
        """THE weekend bug: a disabled schedule must not stop a running encode."""
        import app.encoder as enc
        fake = _FakePool(running=True)
        monkeypatch.setattr(enc, 'encoder_pool', fake)

        mgr = self._mgr()
        mgr.is_enabled = False
        mgr.schedule_config = {'enabled': False}
        mgr.check_and_trigger()
        assert fake.stop_calls == [], "disabled scheduler must be inert"
        assert fake.is_running is True

    def test_manual_override_blocks_autostop(self, monkeypatch):
        """A manual start (override) survives the window boundary."""
        import app.encoder as enc
        fake = _FakePool(running=True)
        monkeypatch.setattr(enc, 'encoder_pool', fake)

        mgr = self._mgr()
        mgr.is_enabled = True
        mgr.manual_override = True
        mgr.schedule_config = {'enabled': True, 'days_of_week': '0,1,2,3,4,5,6',
                               'start_time': '22:00', 'end_time': '06:00'}
        mgr.check_and_trigger()
        assert fake.stop_calls == []

    def test_save_disable_tears_down_cron_job(self, fresh_db, monkeypatch):
        """Disabling the schedule must REMOVE the every-minute checker."""
        import app.scheduler as sched
        monkeypatch.setattr(sched, 'db', fresh_db)
        mgr = self._mgr()
        mgr.scheduler.start()
        try:
            mgr.load_schedule()
            mgr.save_schedule({'enabled': True, 'days_of_week': '0,1,2,3,4,5,6',
                               'start_time': '22:00', 'end_time': '06:00'})
            assert mgr.scheduler.get_job('schedule_check') is not None
            # The bug: disabling left this job alive
            mgr.save_schedule({'enabled': False})
            assert mgr.scheduler.get_job('schedule_check') is None
        finally:
            mgr.scheduler.shutdown(wait=False)

    def test_save_clears_manual_override(self, fresh_db, monkeypatch):
        """Saving the schedule hands control back to the scheduler."""
        import app.scheduler as sched
        monkeypatch.setattr(sched, 'db', fresh_db)
        mgr = self._mgr()
        mgr.scheduler.start()
        try:
            mgr.manual_override = True
            mgr.load_schedule()
            mgr.save_schedule({'enabled': False})
            assert mgr.manual_override is False
        finally:
            mgr.scheduler.shutdown(wait=False)

    def test_finish_before_stop_persists(self, fresh_db, monkeypatch):
        import app.scheduler as sched
        monkeypatch.setattr(sched, 'db', fresh_db)
        mgr = self._mgr()
        mgr.scheduler.start()
        try:
            mgr.load_schedule()
            mgr.save_schedule({'enabled': True, 'finish_before_stop': True,
                               'start_time': '22:00', 'end_time': '06:00'})
            cfg = mgr.load_schedule()
            assert cfg['finish_before_stop'] is True
        finally:
            mgr.scheduler.shutdown(wait=False)

    def test_graceful_stop_leaves_active_job_running(self):
        """graceful=True stops new work but doesn't kill the active encode."""
        from app.encoder import EncoderPool
        killed = []
        class FakeJob:
            def stop(self, **k):
                killed.append(True)
        pool = EncoderPool()
        pool.is_running = True
        pool.active_jobs = [FakeJob()]

        pool.stop(graceful=True)
        assert pool.is_running is False        # stops taking new jobs
        assert killed == []                    # current encode left alone

        pool.is_running = True
        pool.stop(graceful=False)              # hard stop DOES kill
        assert killed == [True]

    def test_should_encode_now(self):
        """Disabled = always OK to encode; enabled = within-window only."""
        mgr = self._mgr()
        mgr.is_enabled = False
        assert mgr.should_encode_now() is True   # no restriction when off

        # Enabled, window that excludes 'now' → not permitted
        now = __import__('datetime').datetime.now()
        far = (now.hour + 3) % 24
        mgr.is_enabled = True
        mgr.schedule_config = {
            'enabled': True, 'days_of_week': '0,1,2,3,4,5,6',
            'start_time': f'{far:02d}:00', 'end_time': f'{(far + 1) % 24:02d}:00',
            'use_windows_rest_hours': False,
        }
        assert mgr.should_encode_now() is False

        # Enabled, window that includes 'now' → permitted
        mgr.schedule_config['start_time'] = '00:00'
        mgr.schedule_config['end_time'] = '23:59'
        assert mgr.should_encode_now() is True


# ---------------------------------------------------------------------------
# Encoder single-loop concurrency guard (Patch 42)
# ---------------------------------------------------------------------------

class TestEncoderSingleLoop:
    def test_duplicate_start_is_rejected(self):
        """A second process_queue while one is 'running' returns immediately."""
        from app.encoder import EncoderPool
        pool = EncoderPool()
        pool.is_running = True            # simulate a loop already active
        pool.process_queue()              # must no-op, not run the loop
        assert pool.is_running is True    # untouched (didn't enter try/finally)

    def test_only_one_concurrent_loop(self, monkeypatch):
        """Bursty triggers (webhooks/boot/scheduler) spawn one loop, not many.

        This is the bug behind 'a bunch of HandBrakes running, stop didn't
        work': N callers each passed `if not is_running` and started a loop.
        """
        import threading, time
        import app.encoder as enc
        # Don't let the loop's finally sweep the real filesystem
        monkeypatch.setattr('app.scanner.cleanup_orphaned_outputs', lambda *a, **k: 0)
        pool = enc.EncoderPool()

        gate = threading.Event()
        entered = []
        def fake_next():
            entered.append(1)
            gate.wait(timeout=3)   # hold the loop body open
            return None
        monkeypatch.setattr(enc.db, 'get_next_pending_item', fake_next)
        monkeypatch.setattr(enc.db, 'count_pending', lambda: 0)

        threads = [threading.Thread(target=pool.process_queue) for _ in range(6)]
        for t in threads:
            t.start()
        time.sleep(0.5)            # let all six race the guard

        assert pool.is_running is True
        assert len(entered) == 1, f"{len(entered)} loops ran — must be exactly 1"

        gate.set()                 # release the held loop
        for t in threads:
            t.join(timeout=5)
        assert pool.is_running is False   # finally reset the slot

    def test_loop_resets_running_on_exit(self, monkeypatch):
        """is_running is always cleared on loop exit (no wedged 'running')."""
        import app.encoder as enc
        pool = enc.EncoderPool()
        monkeypatch.setattr(enc.db, 'get_next_pending_item', lambda: None)
        monkeypatch.setattr(enc.db, 'count_pending', lambda: 0)
        monkeypatch.setattr('app.notifications.notify_queue_empty', lambda: None)
        monkeypatch.setattr('app.scanner.cleanup_orphaned_outputs', lambda *a, **k: 0)
        pool.process_queue()              # empty queue → runs once, breaks
        assert pool.is_running is False


# ---------------------------------------------------------------------------
# Orphaned _optimized temp cleanup
# ---------------------------------------------------------------------------

class TestTempCleanup:
    def test_removes_only_optimized_files(self, fresh_db, tmp_path, monkeypatch):
        import app.scanner as sc
        monkeypatch.setattr(sc, 'db', fresh_db)

        # A scan root with a real source file + an orphaned _optimized temp
        (tmp_path / "movie.mkv").write_bytes(b"source")
        (tmp_path / "movie_optimized.mkv").write_bytes(b"partial")
        (tmp_path / "show_optimized.mp4").write_bytes(b"partial2")
        (tmp_path / "notes.txt").write_text("keep", encoding="utf-8")
        fresh_db.create_scan_root(path=str(tmp_path), profile_id=1)

        removed = sc.cleanup_orphaned_outputs(min_age_seconds=0)
        assert removed == 2
        assert (tmp_path / "movie.mkv").exists()       # source kept
        assert (tmp_path / "notes.txt").exists()       # non-video kept
        assert not (tmp_path / "movie_optimized.mkv").exists()
        assert not (tmp_path / "show_optimized.mp4").exists()

    def test_exclude_paths_protects_active_temp(self, fresh_db, tmp_path, monkeypatch):
        """An in-flight temp passed in exclude_paths is NOT deleted."""
        import app.scanner as sc
        monkeypatch.setattr(sc, 'db', fresh_db)

        active = tmp_path / "active_optimized.mkv"
        active.write_bytes(b"encoding now")
        (tmp_path / "dead_optimized.mkv").write_bytes(b"orphan")
        fresh_db.create_scan_root(path=str(tmp_path), profile_id=1)

        removed = sc.cleanup_orphaned_outputs(exclude_paths=[str(active)], min_age_seconds=0)
        assert removed == 1
        assert active.exists()                          # protected
        assert not (tmp_path / "dead_optimized.mkv").exists()

    def test_recent_files_are_skipped(self, fresh_db, tmp_path, monkeypatch):
        """A freshly written temp (active encode) is not deleted; an old one is."""
        import os, time
        import app.scanner as sc
        monkeypatch.setattr(sc, 'db', fresh_db)

        fresh = tmp_path / "fresh_optimized.mkv"
        fresh.write_bytes(b"being encoded right now")
        old = tmp_path / "old_optimized.mkv"
        old.write_bytes(b"crashed run")
        # Backdate the old file's mtime well past the age threshold
        past = time.time() - 3600
        os.utime(old, (past, past))
        fresh_db.create_scan_root(path=str(tmp_path), profile_id=1)

        removed = sc.cleanup_orphaned_outputs(min_age_seconds=60)
        assert removed == 1
        assert fresh.exists()              # recent → skipped (could be live)
        assert not old.exists()            # old orphan → removed


# ---------------------------------------------------------------------------
# Temperature-gated concurrency (Patch 43)
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_get_next_pending_excludes_inflight(self, fresh_db):
        """A parallel controller won't be handed an already-dispatched item."""
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, priority=1)
        b = fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, priority=2)
        assert fresh_db.get_next_pending_item()['id'] == a
        # With 'a' in flight, the next eligible is 'b'
        assert fresh_db.get_next_pending_item(exclude_ids=[a])['id'] == b
        assert fresh_db.get_next_pending_item(exclude_ids=[a, b]) is None

    def test_effective_concurrency_default_is_one(self):
        from app.encoder import EncoderPool
        pool = EncoderPool()
        assert pool._effective_concurrency(1) == 1   # never engages temp logic

    def test_effective_concurrency_drops_when_hot(self, monkeypatch):
        """Hot GPU/CPU forces concurrency back to 1; cool allows the max."""
        import app.encoder as enc
        pool = enc.EncoderPool()
        monkeypatch.setattr(pool, '_load_resource_settings',
                            lambda: {'gpu_temp_threshold': 83.0, 'cpu_temp_threshold': 85.0})

        # Hot GPU (within 10°C of the 83° pause threshold) → 1
        monkeypatch.setattr(enc.resource_monitor, 'get_all_resources',
                            lambda: {'gpu': {'temperature_c': 80.0}, 'cpu': {'temperature_c': 50.0}})
        pool._effective_cache = None
        assert pool._effective_concurrency(3) == 1

        # Cool → full max
        monkeypatch.setattr(enc.resource_monitor, 'get_all_resources',
                            lambda: {'gpu': {'temperature_c': 60.0}, 'cpu': {'temperature_c': 50.0}})
        pool._effective_cache = None
        pool._last_temp_check = 0.0
        assert pool._effective_concurrency(3) == 3

    def test_effective_concurrency_conservative_on_error(self, monkeypatch):
        """If temps can't be read, fall back to 1 (don't pile on encodes)."""
        import app.encoder as enc
        pool = enc.EncoderPool()
        monkeypatch.setattr(pool, '_load_resource_settings',
                            lambda: {'gpu_temp_threshold': 83.0, 'cpu_temp_threshold': 85.0})
        def boom():
            raise RuntimeError("pynvml hung")
        monkeypatch.setattr(enc.resource_monitor, 'get_all_resources', boom)
        pool._effective_cache = None
        assert pool._effective_concurrency(4) == 1

    def test_parallel_dispatches_up_to_effective(self, monkeypatch):
        """_run_parallel launches up to `effective` jobs, no item dispatched twice."""
        import threading, time
        import app.encoder as enc
        pool = enc.EncoderPool()
        pool.is_running = True

        # Two pending items; effective concurrency pinned to 2
        items = {1: {'id': 1, 'profile_id': 1}, 2: {'id': 2, 'profile_id': 1}}
        remaining = dict(items)
        def fake_next(exclude_ids=None):
            ex = set(exclude_ids or [])
            for i in sorted(remaining):
                if i not in ex:
                    return remaining[i]
            return None
        monkeypatch.setattr(enc.db, 'get_next_pending_item', fake_next)
        monkeypatch.setattr(enc.db, 'get_profile', lambda pid: {'name': 'P', 'codec': 'av1', 'container': 'mkv'})
        monkeypatch.setattr(enc.db, 'count_pending', lambda: len(remaining))
        monkeypatch.setattr(pool, '_load_resource_settings', lambda: {})
        monkeypatch.setattr(pool, '_effective_concurrency', lambda mc: 2)

        started = []
        gate = threading.Event()
        class FakeJob:
            def __init__(self, qid, profile, limits):
                self.queue_item_id = qid
            def start(self):
                started.append(self.queue_item_id)
                gate.wait(timeout=3)
                remaining.pop(self.queue_item_id, None)
        monkeypatch.setattr(enc, 'EncodingJob', FakeJob)

        t = threading.Thread(target=pool._run_parallel, args=(2,), daemon=True)
        t.start()
        time.sleep(0.5)
        # Both items dispatched concurrently, each exactly once
        assert sorted(started) == [1, 2]
        assert len(pool.active_jobs) == 2
        gate.set()
        pool.is_running = False
        t.join(timeout=5)


# ---------------------------------------------------------------------------
# Fastest/slowest prioritization with size fallback (Patch 43)
# ---------------------------------------------------------------------------

class TestFastestFirstFallback:
    def _make_default_profile(self, db):
        return db.create_profile(
            name="P", resolution="", framerate=None, codec="av1",
            encoder="svt_av1", quality=28, audio_codec="passthrough",
            container="mkv", audio_handling="preserve_all",
            subtitle_handling="none", chapter_markers=True,
            enable_filters=False, hw_accel_enabled=False, preset="8",
            two_pass=False, custom_args=None, is_default=True,
        )

    def test_unknown_duration_orders_by_size(self, fresh_db, monkeypatch):
        """Fastest-first ranks a huge no-duration file AFTER a small clip."""
        import asyncio
        from app.api import queue_routes
        from app import queue_maintenance
        monkeypatch.setattr(queue_routes, 'db', fresh_db)
        monkeypatch.setattr(queue_maintenance, 'db', fresh_db)
        self._make_default_profile(fresh_db)

        big = fresh_db.add_to_queue(file_path="/x/vr_giant.mkv", profile_id=1,
                                    duration_seconds=0, file_size_bytes=80_000_000_000,
                                    target_specs={'codec': 'av1'})
        small = fresh_db.add_to_queue(file_path="/x/clip.mkv", profile_id=1,
                                      duration_seconds=0, file_size_bytes=200_000_000,
                                      target_specs={'codec': 'av1'})

        queue_routes.prioritize_queue(
            {'sort_by': 'fastest_first', 'order': 'asc'}, current_user={})

        # Small clip gets rank 1, giant VR last — even with no durations
        assert fresh_db.get_queue_item(small)['priority'] < fresh_db.get_queue_item(big)['priority']

    def test_known_duration_beats_size_proxy(self, fresh_db, monkeypatch):
        """A short known-duration encode outranks a big unknown-duration file."""
        import asyncio
        from app.api import queue_routes
        from app import queue_maintenance
        monkeypatch.setattr(queue_routes, 'db', fresh_db)
        monkeypatch.setattr(queue_maintenance, 'db', fresh_db)
        self._make_default_profile(fresh_db)
        # Learned speed: av1 ~1x realtime
        fresh_db.add_history(file_path="/x/h.mkv", encoding_time_seconds=600,
                             duration_seconds=600, codec='av1')

        short = fresh_db.add_to_queue(file_path="/x/short.mkv", profile_id=1,
                                      duration_seconds=900, file_size_bytes=500_000_000,
                                      target_specs={'codec': 'av1'})   # ~15 min
        bigunknown = fresh_db.add_to_queue(file_path="/x/big.mkv", profile_id=1,
                                           duration_seconds=0, file_size_bytes=60_000_000_000,
                                           target_specs={'codec': 'av1'})

        queue_routes.prioritize_queue(
            {'sort_by': 'fastest_first', 'order': 'asc'}, current_user={})
        assert fresh_db.get_queue_item(short)['priority'] < fresh_db.get_queue_item(bigunknown)['priority']


# ---------------------------------------------------------------------------
# Scan-root path-exists detection (Patch 44)
# ---------------------------------------------------------------------------

class TestScanRootPathExists:
    def test_annotate_path_exists(self, tmp_path):
        from app.api.scan_routes import _annotate_path_exists
        present = _annotate_path_exists({'path': str(tmp_path)})
        missing = _annotate_path_exists({'path': str(tmp_path / "gone_drive_X")})
        assert present['path_exists'] is True
        assert missing['path_exists'] is False


# ---------------------------------------------------------------------------
# Start-where-you-left-off intent (Patch 45)
# ---------------------------------------------------------------------------

class TestAutostartIntent:
    def test_start_and_stop_persist_intent(self, fresh_db, monkeypatch):
        import asyncio
        from fastapi import BackgroundTasks
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        # Manual Start records intent=true (background task is only queued)
        monkeypatch.setattr(queue_routes.encoder_pool, 'is_running', False)
        queue_routes.start_encoding(BackgroundTasks(), current_user={})
        assert fresh_db.get_setting('encoder_autostart') == 'true'

        # Manual Stop records intent=false
        monkeypatch.setattr(queue_routes.encoder_pool, 'is_running', True)
        monkeypatch.setattr(queue_routes.encoder_pool, 'stop', lambda *a, **k: None)
        queue_routes.stop_encoding(current_user={})
        assert fresh_db.get_setting('encoder_autostart') == 'false'

    def test_default_intent_is_resume(self, fresh_db):
        # Unset → default 'true' so a fresh/unattended box resumes on boot
        assert fresh_db.get_setting('encoder_autostart', 'true') == 'true'


# ---------------------------------------------------------------------------
# Duration backfill picker (Patch 46)
# ---------------------------------------------------------------------------

class TestDurationBackfill:
    def test_picks_only_unprobed_pending(self, fresh_db):
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, duration_seconds=0)
        fresh_db.add_to_queue(file_path="/x/known.mkv", profile_id=1, duration_seconds=600)
        fresh_db.add_to_queue(file_path="/x/done.mkv", profile_id=1,
                              duration_seconds=0, status='completed')

        picks = fresh_db.get_pending_without_duration(limit=8)
        assert [p['id'] for p in picks] == [a]   # only the pending, unprobed one

    def test_sentinel_excludes_from_reprobe(self, fresh_db):
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, duration_seconds=0)
        # Probed but no usable duration → -1 sentinel; must not come back
        fresh_db.update_queue_item(a, duration_seconds=-1)
        assert fresh_db.get_pending_without_duration() == []
        # And -1 is still treated as "unknown" by the estimator
        from app.scanner import estimate_encode_seconds
        assert estimate_encode_seconds(-1, 'av1', {'overall': 2.0, 'by_codec': {}}) is None


# ---------------------------------------------------------------------------
# Audit fixes (2026-07): probe height, resolution-string parsing,
# batch priorities, dedupe helper, throttler verdict caching
# ---------------------------------------------------------------------------

class TestProbeHeight:
    """The upscale-eligibility check and the queue's resolution sort read a
    numeric current_specs['height'] — the parsers must populate it."""

    def test_ffprobe_parser_stores_width_and_height(self, scanner):
        specs = scanner._parse_ffprobe_json({
            "format": {"duration": "10.0", "bit_rate": "1000"},
            "streams": [{"codec_type": "video", "codec_name": "hevc",
                         "width": 1920, "height": 800,
                         "r_frame_rate": "24000/1001"}],
        })
        assert specs["width"] == 1920
        assert specs["height"] == 800
        assert specs["resolution"] == "1920x800"

    def test_handbrake_parser_stores_width_and_height(self, scanner):
        specs = scanner._parse_handbrake_json({
            "TitleList": [{
                "VideoCodec": "h264",
                "Geometry": {"Width": 1280, "Height": 720},
                "FrameRate": {"Num": 24, "Den": 1},
                "AudioList": [],
            }]
        })
        assert specs["width"] == 1280
        assert specs["height"] == 720


class TestParseHeight:
    """Sonarr/Radarr resolution strings → numeric height for upscale checks."""

    def test_common_forms(self):
        from app.external_connections import parse_height
        assert parse_height("1920x1080") == 1080
        assert parse_height("1080p") == 1080
        assert parse_height("720") == 720
        assert parse_height(2160) == 2160

    def test_unparseable_returns_zero(self):
        from app.external_connections import parse_height
        assert parse_height("") == 0
        assert parse_height(None) == 0
        assert parse_height("unknown") == 0


class TestBatchQueueHelpers:
    def test_set_queue_priorities_batch(self, fresh_db):
        pid = fresh_db.create_profile(
            name="bp", codec="av1", encoder="svt_av1", quality=30,
            audio_codec="aac")
        ids = [fresh_db.add_to_queue(file_path=f"/m/f{i}.mkv", profile_id=pid)
               for i in range(4)]
        # Reverse the order in one batch
        n = fresh_db.set_queue_priorities(
            (iid, rank + 1) for rank, iid in enumerate(reversed(ids)))
        assert n == 4
        ordered = [i["id"] for i in fresh_db.get_queue_items()]
        assert ordered == list(reversed(ids))

    def test_set_queue_priorities_empty(self, fresh_db):
        assert fresh_db.set_queue_priorities([]) == 0

    def test_get_queue_file_paths(self, fresh_db):
        pid = fresh_db.create_profile(
            name="fp", codec="av1", encoder="svt_av1", quality=30,
            audio_codec="aac")
        fresh_db.add_to_queue(file_path="/m/a.mkv", profile_id=pid)
        fresh_db.add_to_queue(file_path="/m/b.mkv", profile_id=pid,
                              status="completed")
        assert fresh_db.get_queue_file_paths() == {"/m/a.mkv", "/m/b.mkv"}

    def test_queue_indexes_exist(self, fresh_db):
        with fresh_db.get_read_connection() as conn:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        assert "idx_queue_status_priority" in names
        assert "idx_queue_file_path" in names


class TestThrottlerVerdictCache:
    """Rate-limited should_pause_encoding() calls must repeat the last
    verdict — returning (False, '') resumed paused encodes while hot."""

    def _make(self, should_pause):
        from app.resources import ResourceThrottler

        class _FakeMonitor:
            def __init__(self):
                self.calls = 0

            def check_thresholds(self, **kw):
                self.calls += 1
                return {"should_pause": should_pause,
                        "reasons": ["GPU temp 90C > 83C"] if should_pause else []}

        t = ResourceThrottler(_FakeMonitor())
        t.check_interval = 9999   # everything after the first call is limited
        t.last_check = 0
        return t

    def test_hot_verdict_persists_when_rate_limited(self):
        t = self._make(should_pause=True)
        first = t.should_pause_encoding()
        assert first == (True, "GPU temp 90C > 83C")
        assert t.should_pause_encoding() == first      # limited → repeat
        assert t.monitor.calls == 1                    # no re-probe

    def test_cool_verdict_persists_when_rate_limited(self):
        t = self._make(should_pause=False)
        assert t.should_pause_encoding() == (False, "")
        assert t.should_pause_encoding() == (False, "")
        assert t.monitor.calls == 1


# ---------------------------------------------------------------------------
# Audit fixes round 2: route ordering, seed dedupe, stereo crop filters
# ---------------------------------------------------------------------------

class TestProfileExportRouteOrder:
    def test_export_registered_before_id_param(self):
        """GET /profiles/export must be matched before /profiles/{profile_id},
        otherwise 'export' is coerced to an int path param and 422s."""
        from app.api.profile_routes import router
        paths = [r.path for r in router.routes if "GET" in getattr(r, "methods", set())]
        assert "/profiles/export" in paths
        assert paths.index("/profiles/export") < paths.index("/profiles/{profile_id}")


class TestDefaultProfilesShared:
    def test_single_source_of_truth(self):
        from app.main import DEFAULT_PROFILES
        assert len(DEFAULT_PROFILES) == 5
        names = {p["name"] for p in DEFAULT_PROFILES}
        assert len(names) == 5
        # Exactly one built-in default
        assert sum(1 for p in DEFAULT_PROFILES if p["is_default"]) == 1


class TestStereoCropFilters:
    def test_half_formats_restore_aspect(self):
        """Half-SBS/TB store each eye anamorphic — 2D extraction must scale
        the cropped eye back to the original aspect ratio."""
        from app.stereo import STEREO_FORMATS
        assert "scale=2*iw:ih" in STEREO_FORMATS["half_sbs"]["crop_filter"]
        assert "scale=iw:2*ih" in STEREO_FORMATS["half_tb"]["crop_filter"]
        # Full formats: each eye is already full resolution — crop only
        assert "scale" not in STEREO_FORMATS["sbs"]["crop_filter"]
        assert "scale" not in STEREO_FORMATS["tb"]["crop_filter"]


class TestPreflightSourceCheck:
    """preflight_source_check gates every encode: missing files are dropped
    from the queue, unreadable/incomplete files are quarantined."""

    def test_missing_file(self, tmp_path):
        from app.encoder import preflight_source_check
        assert preflight_source_check(str(tmp_path / "gone.mkv")) == 'missing'

    def test_empty_file(self, tmp_path):
        from app.encoder import preflight_source_check
        f = tmp_path / "empty.mkv"
        f.touch()
        result = preflight_source_check(str(f))
        assert result is not None and result != 'missing'

    def test_unprobeable_file_quarantined(self, tmp_path, monkeypatch):
        """A file with content but no decodable video stream → quarantine reason."""
        from app import encoder as enc
        from app.scanner import scanner as real_scanner
        f = tmp_path / "preallocated.mkv"
        f.write_bytes(b"\x00" * 4096)  # torrent-preallocated zeros
        monkeypatch.setattr(real_scanner, 'ffprobe_available', True)
        monkeypatch.setattr(
            real_scanner, 'analyze_file',
            lambda p: {'codec': 'unknown', 'resolution': 'unknown'})
        result = enc.preflight_source_check(str(f))
        assert result is not None and result != 'missing'

    def test_valid_video_passes(self, tmp_path, monkeypatch):
        from app import encoder as enc
        from app.scanner import scanner as real_scanner
        f = tmp_path / "ok.mkv"
        f.write_bytes(b"\x1aE\xdf\xa3" + b"x" * 100)
        monkeypatch.setattr(real_scanner, 'ffprobe_available', True)
        monkeypatch.setattr(
            real_scanner, 'analyze_file',
            lambda p: {'codec': 'h264', 'resolution': '1920x1080'})
        assert enc.preflight_source_check(str(f)) is None

    def test_no_probe_tools_passes_through(self, tmp_path, monkeypatch):
        """Without ffprobe/HandBrake we can't validate — let HandBrake try."""
        from app import encoder as enc
        from app.scanner import scanner as real_scanner
        f = tmp_path / "unknown.mkv"
        f.write_bytes(b"\x00" * 4096)
        monkeypatch.setattr(real_scanner, 'ffprobe_available', False)
        monkeypatch.setattr(real_scanner, 'handbrake_available', False)
        assert enc.preflight_source_check(str(f)) is None


class TestHandBrakeJsonProgress:
    """--json structured progress: primary parsing path (Patch: hb-json)."""

    def _feed_lines(self, lines):
        from app.encoder import HBJsonStream
        stream = HBJsonStream()
        blocks = []
        for line in lines:
            block = stream.feed(line)
            if block:
                blocks.append(block)
        return blocks

    def test_multiline_progress_block(self):
        from app.encoder import parse_hb_json_progress
        blocks = self._feed_lines([
            'Progress: {',
            '    "State": "WORKING",',
            '    "Working": {',
            '        "Progress": 0.2345,',
            '        "Rate": 113.06,',
            '        "RateAvg": 102.33,',
            '        "ETASeconds": 754',
            '    }',
            '}',
        ])
        assert len(blocks) == 1
        label, data = blocks[0]
        assert label == 'Progress'
        pct, fps, eta = parse_hb_json_progress(label, data)
        assert abs(pct - 23.45) < 1e-9
        assert fps == 113.06
        assert eta == 754

    def test_muxing_maps_to_100(self):
        from app.encoder import parse_hb_json_progress
        assert parse_hb_json_progress('Progress', {'State': 'MUXING'}) == (100.0, None, None)

    def test_non_progress_blocks_and_noise_ignored(self):
        from app.encoder import parse_hb_json_progress
        blocks = self._feed_lines([
            'some human readable log line',
            'Version: {',
            '    "Name": "HandBrake" ',
            '}',
            '[09:00:00] scan: unrecognized file type',
        ])
        assert len(blocks) == 1
        assert blocks[0][0] == 'Version'
        assert parse_hb_json_progress(blocks[0][0], blocks[0][1]) is None

    def test_braces_inside_strings(self):
        blocks = self._feed_lines([
            'Progress: { "State": "WORKING", "Note": "weird { brace }",',
            '  "Working": { "Progress": 0.5 } }',
        ])
        assert len(blocks) == 1
        assert blocks[0][1]['Note'] == 'weird { brace }'


_HB_HELP_SAMPLE = """
   -e, --encoder <string>  Select video encoder:
                               svt_av1
                               svt_av1_10bit
                               x264
                               nvenc_h264
                               x265
                               x265_10bit
                               nvenc_h265
                               nvenc_h265_10bit
       --encoder-preset <string>
       --enable-qsv-decoding   Use QSV decoding
"""


class TestEncoderSelection:
    """10-bit/HDR upgrade and HandBrake-list-driven hardware encoders."""

    def _job(self, fresh_db, monkeypatch, profile, source_info, names, vendors=None):
        import app.encoder as enc
        hw = {'nvidia': False, 'intel': False, 'amd': False, 'apple': False,
              'encoder_names': names}
        hw.update(vendors or {})
        monkeypatch.setattr(enc, 'detect_hardware_acceleration', lambda: hw)
        monkeypatch.setattr(enc, 'db', fresh_db)
        item_id = fresh_db.add_to_queue(file_path='/x/in.mkv', root_id=1, profile_id=1,
                                        current_specs=source_info, target_specs={})
        base = {'name': 'p', 'codec': 'av1', 'encoder': 'svt_av1', 'quality': 30,
                'container': 'mkv', 'hw_accel_enabled': False}
        base.update(profile)
        return enc.EncodingJob(item_id, base)

    def test_parse_encoder_list(self):
        from app.encoder import parse_hb_encoder_list
        names = parse_hb_encoder_list(_HB_HELP_SAMPLE)
        assert names[0] == 'svt_av1' and 'nvenc_h265_10bit' in names
        assert '--encoder-preset' not in ' '.join(names)
        assert parse_hb_encoder_list('no list here') == []

    def test_qsv_option_text_is_not_intel_hardware(self, monkeypatch):
        import app.handbrake as hb
        class R:
            stdout = _HB_HELP_SAMPLE
            stderr = ''
        monkeypatch.setattr(hb, '_hw_accel_cache', None)
        monkeypatch.setattr(hb.subprocess, 'run', lambda *a, **k: R())
        hw = hb.detect_hardware_acceleration()
        monkeypatch.setattr(hb, '_hw_accel_cache', None)
        assert hw['nvidia'] and not hw['intel']
        assert hw['details']['nvidia'] == {'h264': True, 'h265': True, 'av1': False}

    def test_hdr_source_upgrades_to_10bit(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {}, {'bit_depth': 10, 'hdr': True},
                        ['svt_av1', 'svt_av1_10bit'])
        assert job.select_encoder() == 'svt_av1_10bit'

    def test_8bit_source_keeps_8bit_profile(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {}, {'bit_depth': 8, 'hdr': False},
                        ['svt_av1', 'svt_av1_10bit'])
        assert job.select_encoder() == 'svt_av1'

    def test_10bit_profile_falls_back_when_unavailable(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {'encoder': 'svt_av1_10bit'},
                        {'bit_depth': 8}, ['svt_av1'])
        assert job.select_encoder() == 'svt_av1'

    def test_h264_never_upgraded(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {'codec': 'h264', 'encoder': 'x264'},
                        {'bit_depth': 10, 'hdr': True}, ['x264', 'x264_10bit'])
        assert job.select_encoder() == 'x264'

    def test_hw_av1_only_when_handbrake_offers_it(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {'hw_accel_enabled': True},
                        {'bit_depth': 8}, ['svt_av1', 'nvenc_h265'], {'nvidia': True})
        assert job.select_encoder() == 'svt_av1'  # no nvenc_av1 on this GPU
        job = self._job(fresh_db, monkeypatch, {'hw_accel_enabled': True},
                        {'bit_depth': 10, 'hdr': True},
                        ['svt_av1', 'nvenc_av1', 'nvenc_av1_10bit'], {'nvidia': True})
        assert job.select_encoder() == 'nvenc_av1_10bit'

    def test_film_grain_svt_av1(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {'film_grain': 10, 'two_pass': True},
                        {'bit_depth': 8}, ['svt_av1', 'svt_av1_10bit'])
        cmd = job.build_command()
        i = cmd.index('--encopts')
        assert cmd[i + 1] == 'film-grain=10:film-grain-denoise=0'
        assert '--multi-pass' not in cmd  # no-op for constant quality

    def test_film_grain_x265_tune_skips_crashing_presets(self, fresh_db, monkeypatch):
        prof = {'codec': 'h265', 'encoder': 'x265', 'film_grain': 5, 'preset': 'slow'}
        cmd = self._job(fresh_db, monkeypatch, prof, {'bit_depth': 8}, ['x265']).build_command()
        assert cmd[cmd.index('--encoder-tune') + 1] == 'grain'
        import app.encoder as enc
        prof['preset'] = 'ultrafast'
        monkeypatch.setattr(enc, 'handbrake_version', lambda: (1, 10, 2))
        cmd = self._job(fresh_db, monkeypatch, prof, {'bit_depth': 8}, ['x265']).build_command()
        assert '--encoder-tune' not in cmd          # crashes HandBrake 1.10
        monkeypatch.setattr(enc, 'handbrake_version', lambda: (1, 11, 2))
        cmd = self._job(fresh_db, monkeypatch, prof, {'bit_depth': 8}, ['x265']).build_command()
        assert cmd[cmd.index('--encoder-tune') + 1] == 'grain'  # fixed in 1.11.2

    def test_film_grain_persists(self, fresh_db):
        pid = fresh_db.create_profile(
            name="G", resolution="", framerate=None, codec="av1",
            encoder="svt_av1_10bit", quality=30, audio_codec="aac", film_grain=12)
        assert fresh_db.get_profile(pid)['film_grain'] == 12
        fresh_db.update_profile(pid, film_grain=0)
        assert fresh_db.get_profile(pid)['film_grain'] == 0

    def test_hw_unknown_list_keeps_conservative_codecs(self, fresh_db, monkeypatch):
        job = self._job(fresh_db, monkeypatch, {'hw_accel_enabled': True},
                        {'bit_depth': 8}, [], {'nvidia': True})
        assert job.select_encoder() == 'svt_av1'
        job = self._job(fresh_db, monkeypatch,
                        {'hw_accel_enabled': True, 'codec': 'h265', 'encoder': 'x265'},
                        {'bit_depth': 8}, [], {'nvidia': True})
        assert job.select_encoder() == 'nvenc_h265'


class TestVideoColorInfo:
    def test_pix_fmt_depth_and_hdr(self):
        from app.scanner import video_color_info
        info = video_color_info({'pix_fmt': 'yuv420p10le', 'color_transfer': 'smpte2084',
                                 'color_primaries': 'bt2020'})
        assert info['bit_depth'] == 10 and info['hdr'] and not info['dolby_vision']
        assert video_color_info({'pix_fmt': 'yuv420p'})['bit_depth'] == 8
        assert video_color_info({'pix_fmt': 'yuv420p', 'color_transfer': 'arib-std-b67'})['hdr']

    def test_dolby_vision_side_data(self):
        from app.scanner import video_color_info
        info = video_color_info({'pix_fmt': 'yuv420p10le', 'side_data_list': [
            {'side_data_type': 'DOVI configuration record'}]})
        assert info['hdr'] and info['dolby_vision']

    def test_cover_art_ignored(self, scanner):
        specs = scanner._parse_ffprobe_json({'streams': [
            {'codec_type': 'video', 'codec_name': 'hevc', 'width': 3840, 'height': 2160,
             'pix_fmt': 'yuv420p10le', 'color_transfer': 'smpte2084'},
            {'codec_type': 'video', 'codec_name': 'mjpeg', 'width': 600, 'height': 900,
             'disposition': {'attached_pic': 1}},
        ], 'format': {}})
        assert specs['codec'] == 'h265' and specs['height'] == 2160 and specs['hdr']


class TestBuildCommandFlags:
    """--json always present; NVDEC gated on the profile hw-accel flag."""

    def _job(self, fresh_db, hw_accel):
        from app import encoder as enc
        item_id = fresh_db.add_to_queue(
            file_path="/x/in.mkv", root_id=1, profile_id=1,
            current_specs={}, target_specs={})
        profile = {
            'name': 'p', 'codec': 'av1', 'encoder': 'svt_av1', 'quality': 30,
            'container': 'mkv', 'hw_accel_enabled': hw_accel,
        }
        import app.encoder as encoder_mod
        orig_db = encoder_mod.db
        encoder_mod.db = fresh_db
        try:
            job = enc.EncodingJob(item_id, profile)
        finally:
            encoder_mod.db = orig_db
        return job

    def test_json_flag_always_present(self, fresh_db):
        job = self._job(fresh_db, hw_accel=False)
        cmd = job.build_command()
        assert '--json' in cmd
        assert '--enable-hw-decoding' not in cmd

    def test_nvdec_when_nvidia_and_hw_accel(self, fresh_db, monkeypatch):
        import app.encoder as encoder_mod
        monkeypatch.setattr(encoder_mod, 'detect_hardware_acceleration',
                            lambda: {'nvidia': True, 'intel': False,
                                     'amd': False, 'apple': False})
        job = self._job(fresh_db, hw_accel=True)
        cmd = job.build_command()
        i = cmd.index('--enable-hw-decoding')
        assert cmd[i + 1] == 'nvdec'


class TestDedupeMatching:
    """Duplicate-detection math: dHash, exact groups, near-dupes, containment."""

    def _rand_hashes(self, rng, n):
        return [rng.getrandbits(64) for _ in range(n)]

    def _flip_bits(self, h, rng, nbits):
        for _ in range(nbits):
            h ^= (1 << rng.randrange(64))
        return h

    def test_dhash_frame_gradient(self):
        from app.dedupe import dhash_frame
        # Row of ascending brightness → every comparison true → all ones
        frame = bytes(range(0, 72))
        assert dhash_frame(frame) == (1 << 64) - 1
        # Flat frame → all zeros
        assert dhash_frame(bytes([128] * 72)) == 0

    def test_hamming(self):
        from app.dedupe import hamming
        assert hamming(0b1010, 0b1010) == 0
        assert hamming(0b1010, 0b0101) == 4

    def test_exact_group_via_quick_hash(self):
        from app.dedupe import match_library
        import random
        rng = random.Random(42)
        h = self._rand_hashes(rng, 60)
        videos = [
            {'path': 'a.mkv', 'size': 1000, 'quick_hash': 'QH1', 'hashes': h},
            {'path': 'b.mkv', 'size': 1000, 'quick_hash': 'QH1', 'hashes': h},
            {'path': 'c.mkv', 'size': 999,  'quick_hash': 'QH2', 'hashes': self._rand_hashes(rng, 60)},
        ]
        matches = match_library(videos)
        exact = [m for m in matches if m['kind'] == 'exact']
        assert len(exact) == 1
        assert {exact[0]['file_a'], exact[0]['file_b']} == {'a.mkv', 'b.mkv'}

    def test_reencode_duplicate_detected(self):
        """Same video, different encode: identical length, hashes differ by a
        few bits per frame → 'duplicate'."""
        from app.dedupe import match_library
        import random
        rng = random.Random(7)
        original = self._rand_hashes(rng, 120)
        reencode = [self._flip_bits(h, rng, 3) if i % 3 else h
                    for i, h in enumerate(original)]
        videos = [
            {'path': 'orig.mkv', 'size': 5000, 'quick_hash': 'A', 'hashes': original},
            {'path': 'reenc.mp4', 'size': 3000, 'quick_hash': 'B', 'hashes': reencode},
        ]
        matches = match_library(videos)
        assert len(matches) == 1
        assert matches[0]['kind'] == 'duplicate'
        assert matches[0]['score'] > 90

    def test_clip_containment_with_offset(self):
        """A 60s clip cut from 10:00 into a 20-min video → 'clip' at ~600s."""
        from app.dedupe import match_library
        import random
        rng = random.Random(99)
        full = self._rand_hashes(rng, 1200)
        clip = list(full[600:660])
        videos = [
            {'path': 'full.mkv', 'size': 9000, 'quick_hash': 'F', 'hashes': full},
            {'path': 'clip.mp4', 'size': 500,  'quick_hash': 'C', 'hashes': clip},
        ]
        matches = match_library(videos)
        assert len(matches) == 1
        m = matches[0]
        assert m['kind'] == 'clip'
        assert m['file_a'] == 'full.mkv' and m['file_b'] == 'clip.mp4'
        assert abs(m['offset_seconds'] - 600) <= 2
        assert m['overlap_seconds'] >= 55

    def test_unrelated_videos_no_match(self):
        from app.dedupe import match_library
        import random
        rng = random.Random(5)
        videos = [
            {'path': 'x.mkv', 'size': 100, 'quick_hash': 'X', 'hashes': self._rand_hashes(rng, 300)},
            {'path': 'y.mkv', 'size': 200, 'quick_hash': 'Y', 'hashes': self._rand_hashes(rng, 300)},
        ]
        assert match_library(videos) == []

    def test_common_hashes_not_evidence(self):
        """A hash appearing across most of the library (black frames) must
        not create matches between otherwise unrelated videos."""
        from app.dedupe import match_library, COMMON_HASH_MAX_VIDEOS
        import random
        rng = random.Random(3)
        black = 0  # flat black frame dhash
        videos = []
        for i in range(COMMON_HASH_MAX_VIDEOS + 2):
            hs = self._rand_hashes(rng, 100)
            # sprinkle black frames through every video
            for j in range(0, 100, 10):
                hs[j] = black
            videos.append({'path': f'v{i}.mkv', 'size': i + 1,
                           'quick_hash': f'q{i}', 'hashes': hs})
        assert match_library(videos) == []

    def test_pack_unpack_roundtrip(self):
        from app.dedupe import pack_hashes, unpack_hashes
        import random
        rng = random.Random(1)
        hs = [rng.getrandbits(64) for _ in range(50)]
        assert unpack_hashes(pack_hashes(hs)) == hs

    def test_low_entropy_videos_not_matched(self):
        """Near-static footage (a handful of distinct frames) matches at any
        offset, so no perceptual match may be claimed — real regression:
        a static test pattern reported a bogus 'clip at 0s'."""
        from app.dedupe import match_library
        # Two different static videos cycling the same 3 visual states
        cycle = [0xAAAA, 0xBBBB, 0xCCCC]
        a = [cycle[i % 3] for i in range(180)]
        b = [cycle[i % 3] for i in range(45)]
        videos = [
            {'path': 'static_a.mkv', 'size': 10, 'quick_hash': 'A', 'hashes': a},
            {'path': 'static_b.mkv', 'size': 20, 'quick_hash': 'B', 'hashes': b},
        ]
        assert match_library(videos) == []

    def test_exact_still_found_for_low_entropy(self):
        """The entropy guard must not suppress byte-identical duplicates —
        those are proven by the hash, not by frame content."""
        from app.dedupe import match_library
        flat = [0x1111] * 100
        videos = [
            {'path': 'a.mkv', 'size': 500, 'quick_hash': 'SAME', 'hashes': flat},
            {'path': 'b.mkv', 'size': 500, 'quick_hash': 'SAME', 'hashes': flat},
        ]
        matches = match_library(videos)
        assert len(matches) == 1 and matches[0]['kind'] == 'exact'

    def test_offset_prefers_voted_alignment_on_ties(self):
        """When several alignments score equally, report the voted offset —
        not the edge of the refinement window."""
        from app.dedupe import match_library
        import random
        rng = random.Random(11)
        full = [rng.getrandbits(64) for _ in range(400)]
        # Repeat a segment so ±1 shifts also align well
        full[100:120] = full[80:100]
        clip = list(full[80:140])
        videos = [
            {'path': 'full.mkv', 'size': 900, 'quick_hash': 'F', 'hashes': full},
            {'path': 'clip.mkv', 'size': 90, 'quick_hash': 'C', 'hashes': clip},
        ]
        matches = match_library(videos)
        assert len(matches) == 1
        assert abs(matches[0]['offset_seconds'] - 80) <= 1

    def test_quick_hash_identifies_identical_files(self, tmp_path):
        """Sampled hash: identical content matches, single-byte change doesn't."""
        from app.dedupe import quick_hash
        payload = bytes(range(256)) * 5000
        a = tmp_path / "a.mkv"; a.write_bytes(payload)
        b = tmp_path / "b.mkv"; b.write_bytes(payload)
        assert quick_hash(str(a)) == quick_hash(str(b))

        # Differs at the very start (sampled region)
        c = tmp_path / "c.mkv"; c.write_bytes(b'\xff' + payload[1:])
        assert quick_hash(str(c)) != quick_hash(str(a))

        # Same bytes, different length → different hash (size is mixed in)
        d = tmp_path / "d.mkv"; d.write_bytes(payload + b'\x00')
        assert quick_hash(str(d)) != quick_hash(str(a))

    def test_quick_hash_missing_file(self, tmp_path):
        from app.dedupe import quick_hash
        assert quick_hash(str(tmp_path / "nope.mkv")) is None


class TestDedupeRoutesRegistered:
    def test_routes_present(self):
        from app.api.routes import router
        paths = {r.path for r in router.routes}
        assert "/duplicates" in paths
        assert "/duplicates/scan" in paths
        assert "/duplicates/scan/cancel" in paths
        assert "/duplicates/status" in paths
        assert "/duplicates/delete-file" in paths


# ---------------------------------------------------------------------------
# Destination collisions — a container change must never eat a bystander
# ---------------------------------------------------------------------------

class TestDestinationCollision:
    def _job(self, enc, fresh_db, src, container='mkv'):
        qid = fresh_db.add_to_queue(file_path=str(src), profile_id=1,
                                    status='processing')
        job = enc.EncodingJob.__new__(enc.EncodingJob)
        job.queue_item_id = qid
        job.queue_item = {'file_path': str(src), 'current_specs': {},
                          'duration_seconds': 0}
        job.profile = {'name': 'P', 'codec': 'av1', 'container': container}
        job._preprocessed = False
        job._unmet = []
        job._upscale_work_mpf = 0.0
        job._paused_seconds = 0
        job._paused_at_monotonic = None
        job._hb_started_monotonic = None
        job._job_started_monotonic = None
        return job, qid

    def test_finalize_refuses_to_clobber_existing_destination(
            self, fresh_db, monkeypatch, tmp_path):
        """source.mp4 -> .mkv when a DIFFERENT .mkv already holds the name:
        the item fails, and BOTH the original and the bystander survive.

        The old order (unlink source, then rename) deleted the original first,
        so the failed rename left only the _optimized temp — which the orphan
        sweeper then removed too, losing the file outright.
        """
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)

        src = tmp_path / "movie.mp4"
        src.write_bytes(b"original" * 100)
        bystander = tmp_path / "movie.mkv"
        bystander.write_bytes(b"someone elses file")
        temp_out = tmp_path / "movie_optimized.mkv"
        temp_out.write_bytes(b"encoded")

        job, qid = self._job(enc, fresh_db, src)
        job._finalize_encoding()

        assert src.exists(), "original must survive a destination collision"
        assert bystander.read_bytes() == b"someone elses file"
        assert not temp_out.exists(), "our redundant output is cleaned up"
        item = fresh_db.get_queue_item(qid)
        assert item['status'] == 'failed'
        assert 'already exists' in item['error_message']

    def test_finalize_keeps_original_when_encode_is_bigger(
            self, fresh_db, monkeypatch, tmp_path):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        src = tmp_path / "show.mkv"
        src.write_bytes(b"o" * 1000)
        out = tmp_path / "show_optimized.mkv"
        out.write_bytes(b"e" * 1200)
        job, qid = self._job(enc, fresh_db, src)
        job._finalize_encoding()
        assert src.read_bytes() == b"o" * 1000 and not out.exists()
        item = fresh_db.get_queue_item(qid)
        assert item['status'] == 'kept_original'
        assert '+20.0%' in item['error_message']
        # Not recorded as a completed encode (no history row)
        with fresh_db.get_read_connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0

    def test_finalize_threshold_and_upscale_exemption(
            self, fresh_db, monkeypatch, tmp_path):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        fresh_db.set_setting('min_savings_percent', '10')
        src = tmp_path / "a.mkv"
        src.write_bytes(b"o" * 1000)
        (tmp_path / "a_optimized.mkv").write_bytes(b"e" * 950)   # only 5% smaller
        job, qid = self._job(enc, fresh_db, src)
        job._finalize_encoding()
        assert fresh_db.get_queue_item(qid)['status'] == 'kept_original'

        src2 = tmp_path / "b.mkv"
        src2.write_bytes(b"o" * 1000)
        (tmp_path / "b_optimized.mkv").write_bytes(b"e" * 3000)  # upscaled: bigger is fine
        job, qid = self._job(enc, fresh_db, src2)
        job._preprocessed = True
        job._finalize_encoding()
        assert fresh_db.get_queue_item(qid)['status'] == 'completed'
        assert src2.read_bytes() == b"e" * 3000

    def test_finalize_replaces_source_when_container_unchanged(
            self, fresh_db, monkeypatch, tmp_path):
        """.mkv -> .mkv still overwrites its own source — that is the normal
        path and must not be caught by the collision guard."""
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)

        src = tmp_path / "movie.mkv"
        src.write_bytes(b"original" * 100)
        (tmp_path / "movie_optimized.mkv").write_bytes(b"encoded")

        job, qid = self._job(enc, fresh_db, src)
        job._finalize_encoding()

        assert src.read_bytes() == b"encoded"
        assert not (tmp_path / "movie_optimized.mkv").exists()
        assert fresh_db.get_queue_item(qid)['status'] == 'completed'

    def test_finalize_moves_then_deletes_on_container_change(
            self, fresh_db, monkeypatch, tmp_path):
        """.mp4 -> .mkv with a free destination: encode lands, source goes."""
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)

        src = tmp_path / "movie.mp4"
        src.write_bytes(b"original" * 100)
        (tmp_path / "movie_optimized.mkv").write_bytes(b"encoded")

        job, qid = self._job(enc, fresh_db, src)
        job._finalize_encoding()

        assert not src.exists()
        assert (tmp_path / "movie.mkv").read_bytes() == b"encoded"
        assert fresh_db.get_queue_item(qid)['status'] == 'completed'


# ---------------------------------------------------------------------------
# Upscale cost model — AI time scales with output pixels, not runtime
# ---------------------------------------------------------------------------

class TestVideo2X:
    def test_parse_progress_takes_last_record(self):
        from app.video2x import parse_progress
        text = ("\rframe=10/100 (10.00%); fps=5.0; elapsed=00:00:02; remaining=00:00:18\x1b[K"
                "\rframe=25/100 (25.00%); fps=6.2; elapsed=00:00:04; remaining=00:01:05\x1b[K")
        rec = parse_progress(text)
        assert rec == {'frame': 25, 'total': 100, 'percent': 25.0, 'fps': 6.2, 'remaining': 65}
        assert parse_progress('no progress here') is None

    def test_model_mapping(self):
        from app.video2x import video2x_model
        assert video2x_model('realesrgan', 'realesrgan-x4plus') == (
            'realesrgan', '--realesrgan-model', 'realesrgan-plus')
        assert video2x_model('realcugan', 'models-se')[2] == 'models-se'
        assert video2x_model('waifu2x', 'models-cunet') is None

    def test_command_shape(self):
        from app.video2x import build_command
        cmd = build_command('v2x', 'in.mkv', 'out.mkv', 'realesrgan', 'realesr-animevideov3', 2)
        assert cmd[:5] == ['v2x', '-i', 'in.mkv', '-o', 'out.mkv']
        assert cmd[cmd.index('-p') + 1] == 'realesrgan' and cmd[cmd.index('-s') + 1] == '2'
        assert cmd[cmd.index('-c') + 1] == 'libx264'

    def _fake_v2x(self, tmp_path, exit_code, finished=True):
        import sys
        script = tmp_path / 'fake.py'
        script.write_text(
            "import sys\n"
            "a = sys.argv[1:]\n"
            "open(a[a.index('-o') + 1], 'wb').write(b'x')\n"
            "sys.stdout.write('\\rframe=10/10 (100.00%); fps=1.0; elapsed=00:00:01; remaining=00:00:00')\n"
            + ("print('Output written to: x')\n" if finished else "")
            + f"sys.exit({exit_code})\n", encoding='utf-8')
        return [sys.executable, str(script)]

    def _run(self, monkeypatch, tmp_path, cmd_prefix, src_frames, out_frames):
        import app.video2x as v2x
        monkeypatch.setattr(v2x, 'build_command',
                            lambda b, i, o, *a: cmd_prefix + ['-i', i, '-o', o])
        counts = {'in.mkv': src_frames, 'out.mkv': out_frames}
        monkeypatch.setattr(v2x, 'video_packet_count', lambda p: counts[p.split('\\')[-1].split('/')[-1]])
        return v2x.run_video2x('ignored.exe', 'in.mkv', str(tmp_path / 'out.mkv'),
                               'realesrgan', 'realesr-animevideov3', 2,
                               progress=lambda *a: None, register=lambda p: None)

    def test_crash_after_finish_accepted_when_frames_complete(self, monkeypatch, tmp_path):
        cmd = self._fake_v2x(tmp_path, 1)
        assert self._run(monkeypatch, tmp_path, cmd, 1000, 998) is True

    def test_crash_after_finish_rejected_when_frames_missing(self, monkeypatch, tmp_path):
        cmd = self._fake_v2x(tmp_path, 1)
        assert self._run(monkeypatch, tmp_path, cmd, 1000, 900) is False

    def test_crash_before_finish_rejected(self, monkeypatch, tmp_path):
        cmd = self._fake_v2x(tmp_path, 1, finished=False)
        assert self._run(monkeypatch, tmp_path, cmd, 1000, 1000) is False

    def test_pipeline_prefers_video2x_and_reports_target_size(self, monkeypatch, tmp_path):
        import app.upscaler as up
        import app.video2x as v2x
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        monkeypatch.setattr(up, '_find_binary', lambda key: 'video2x.exe' if key == 'video2x' else 'ncnn.exe')
        monkeypatch.setattr(up, '_get_video_info', lambda p: {
            'width': 640, 'height': 360, 'fps': 24.0, 'duration': 10, 'vfr': False, 'hdr': False})
        monkeypatch.setattr(up, '_check_disk_space', lambda *a: True)
        calls = {}

        def fake_run(binary, inp, out, key, model, scale, progress, register):
            calls['scale'] = scale
            progress(50.0, 10)
            open(out, 'wb').write(b'video')
            return True
        monkeypatch.setattr(v2x, 'run_video2x', fake_run)
        info = {}
        out = up.run_upscale_pipeline(str(src), 'realesrgan', 'realesrgan-x4plus', 2,
                                      result_info=info)
        try:
            assert out and out.endswith('_upscaled_v2x.mkv')
            assert calls['scale'] == 4                   # x4plus is 4x-only
            assert info['target_size'] == (1280, 720)    # HandBrake scales to x2
        finally:
            up.cleanup_upscale_workdir(out)

    def test_build_command_size_override(self, fresh_db, monkeypatch):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        monkeypatch.setattr(enc, 'detect_hardware_acceleration', lambda: {'encoder_names': []})
        qid = fresh_db.add_to_queue(file_path='/x/in.mkv', root_id=1, profile_id=1,
                                    current_specs={'bit_depth': 8}, target_specs={})
        job = enc.EncodingJob(qid, {'name': 'p', 'codec': 'av1', 'encoder': 'svt_av1',
                                    'quality': 30, 'container': 'mkv'})
        cmd = job.build_command(input_override='/tmp/up.mkv', size_override=(1280, 720))
        assert cmd[cmd.index('--width') + 1] == '1280'
        assert cmd[cmd.index('--height') + 1] == '720'


class TestRtxVsr:
    def test_not_installed_means_no_binary(self, monkeypatch, tmp_path):
        from app import rtx_vsr
        import app.upscaler as up
        monkeypatch.setattr(rtx_vsr, 'runtime_dir', lambda: tmp_path / 'vsr')
        assert rtx_vsr.installed_info() is None
        assert up._find_binary('rtxvsr') is None

    def test_installed_marker_needs_python(self, monkeypatch, tmp_path):
        import json
        from app import rtx_vsr
        monkeypatch.setattr(rtx_vsr, 'runtime_dir', lambda: tmp_path)
        (tmp_path / 'installed.json').write_text(json.dumps({'gpu': 'x'}))
        assert rtx_vsr.installed_info() is None          # marker but no venv
        py = rtx_vsr.runtime_python()
        py.parent.mkdir(parents=True)
        py.write_bytes(b'')
        assert rtx_vsr.installed_info() == {'gpu': 'x'}

    def test_registered_as_upscaler_with_quality_models(self):
        from app.upscaler import UPSCALERS
        from app.rtx_vsr import QUALITY_LEVELS
        assert UPSCALERS['rtxvsr']['models'] == QUALITY_LEVELS
        # A runtime, not a GitHub binary: the update checker must skip it
        assert UPSCALERS['rtxvsr']['runtime'] == 'rtxvsr'
        assert 'github_owner' not in UPSCALERS['rtxvsr']

    def test_pipeline_routes_to_vsr(self, monkeypatch, tmp_path):
        import app.upscaler as up
        import app.rtx_vsr as vsr
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        monkeypatch.setattr(up, '_find_binary', lambda key: 'python.exe')
        monkeypatch.setattr(up, '_get_video_info', lambda p: {
            'width': 720, 'height': 480, 'fps': 23.976, 'duration': 10, 'vfr': False,
            'hdr': False, 'color_space': 'smpte170m'})
        monkeypatch.setattr(up, '_check_disk_space', lambda *a: True)
        seen = {}

        def fake(inp, out, **kw):
            seen.update(kw)
            open(out, 'wb').write(b'v')
            return True
        monkeypatch.setattr(vsr, 'run_vsr', fake)
        out = up.run_upscale_pipeline(str(src), 'rtxvsr', 'HIGH', 2)
        try:
            assert out.endswith('_upscaled_vsr.mkv')
            assert (seen['out_w'], seen['out_h'], seen['quality']) == (1440, 960, 'HIGH')
            assert 'color_primaries=smpte170m' in seen['setparams']
        finally:
            up.cleanup_upscale_workdir(out)

    def test_pipeline_rejects_factor_over_4(self, monkeypatch, tmp_path):
        import app.upscaler as up
        src = tmp_path / 'in.mkv'
        src.write_bytes(b'x')
        monkeypatch.setattr(up, '_find_binary', lambda key: 'python.exe')
        monkeypatch.setattr(up, '_get_video_info', lambda p: {
            'width': 320, 'height': 240, 'fps': 24.0, 'duration': 1, 'hdr': False})
        assert up.run_upscale_pipeline(str(src), 'rtxvsr', 'HIGH', 8) is None


class TestUpscaleModelScale:
    def test_x4_only_models_run_at_4(self):
        from app.upscaler import model_scale_for
        assert model_scale_for('realesrgan', 'realesrgan-x4plus', 2) == 4
        assert model_scale_for('realesrgan', 'realesrgan-x4plus', 4) == 4
        assert model_scale_for('realesrgan', 'realesr-animevideov3', 2) == 2
        assert model_scale_for('realesrgan', 'realesr-animevideov3', 3) == 3

    def test_falls_back_to_largest_scale(self):
        from app.upscaler import model_scale_for
        assert model_scale_for('realcugan', 'models-nose', 4) == 2
        assert model_scale_for('realcugan', 'models-pro', 4) == 3

    def test_unknown_model_uses_requested(self):
        from app.upscaler import model_scale_for
        assert model_scale_for('realesrgan', 'custom-model', 2) == 2

    def test_color_matrix_defaults(self):
        from app.upscaler import _color_matrix, _color_setparams
        assert _color_matrix({'color_space': 'bt709', 'height': 480}) == 'bt709'
        assert _color_matrix({'height': 1080}) == 'bt709'
        assert _color_matrix({'height': 480}) == 'smpte170m'
        assert _color_matrix({'color_space': 'bt2020nc'}) == 'bt2020'
        # Unknown tags fall back to the matrix's standard (never PNG's sRGB)
        assert _color_setparams({'color_primaries': 'unknown'}, 'bt709') == (
            'setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv')
        assert _color_setparams({}, 'smpte170m') == (
            'setparams=colorspace=smpte170m:color_primaries=smpte170m:color_trc=smpte170m:range=tv')
        assert 'color_primaries=bt470bg' in _color_setparams(
            {'color_primaries': 'bt470bg', 'color_space': 'bt470bg'}, 'bt470')


class TestUpscaleCostModel:
    SD = {'width': 640, 'height': 480, 'duration': 7200, 'framerate': 24}
    UHD = {'width': 3840, 'height': 2160, 'duration': 600, 'framerate': 60}

    def _item(self, specs, factor=None, stereo=False):
        item = {'duration_seconds': specs['duration'], 'current_specs': specs,
                'target_specs': {'codec': 'av1'}}
        if factor:
            item['upscale_plan'] = json.dumps({'enabled': True, 'factor': factor})
        if stereo:
            item['stereo_plan'] = json.dumps({'enabled': True})
        return item

    def test_work_mpf_scales_with_pixels_and_frames(self):
        from app.scanner import upscale_work_mpf

        plan = json.dumps({'enabled': True, 'factor': 2})
        # 7200s x 24fps x (1280*960 / 1e6) MP
        assert abs(upscale_work_mpf(self.SD, plan) - 7200 * 24 * 1.2288) < 1
        # Doubling the factor quadruples the work
        plan4 = json.dumps({'enabled': True, 'factor': 4})
        assert abs(upscale_work_mpf(self.SD, plan4)
                   / upscale_work_mpf(self.SD, plan) - 4.0) < 1e-6
        # Disabled plan or unknown dimensions -> no work
        assert upscale_work_mpf(self.SD, json.dumps({'enabled': False})) == 0.0
        assert upscale_work_mpf({}, plan) == 0.0

    def test_short_4k_upscale_outranks_long_sd_upscale(self):
        """The bug this replaces: a duration-based ratio ranked a 2-hour SD
        upscale as far slower than a 10-minute 4K one. It is not."""
        from app.scanner import estimate_item_seconds

        stats = {'overall': 2.0, 'by_codec': {'av1': 2.0}, 'samples': 5}
        sd = estimate_item_seconds(self._item(self.SD, factor=2), stats)
        uhd = estimate_item_seconds(self._item(self.UHD, factor=2), stats)
        assert uhd > sd

    def test_upscale_outranks_plain_encode_with_no_history(self):
        """A fresh install has no preprocessed history; an upscale must still
        sort as far more expensive than a plain encode of the same file."""
        from app.scanner import estimate_item_seconds

        stats = {'overall': 2.0, 'by_codec': {'av1': 2.0}, 'samples': 5}
        plain = estimate_item_seconds(self._item(self.SD), stats)
        upscaled = estimate_item_seconds(self._item(self.SD, factor=2), stats)
        assert plain == 7200 * 2.0
        # ~59h of GPU work vs 4h of encoding for the same 2-hour file
        assert upscaled > plain * 10

    def test_measured_mpf_beats_legacy_ratio(self):
        """Once real megapixel-frame samples exist they replace the rescaled
        duration ratio."""
        from app.scanner import _seconds_per_mpf, REFERENCE_MPF_PER_VIDEO_SECOND

        assert _seconds_per_mpf({'pre_seconds_per_mpf': 3.0,
                                 'preprocessed': 2000.0}) == 3.0
        # No measured samples -> rescale the legacy ratio
        assert abs(_seconds_per_mpf({'preprocessed': 295.0})
                   - 295.0 / REFERENCE_MPF_PER_VIDEO_SECOND) < 1e-9
        # Nothing learned at all -> built-in constant
        assert _seconds_per_mpf({}) > 0

    def test_history_learns_seconds_per_megapixel_frame(self, fresh_db):
        from app.scanner import upscale_work_mpf

        work = upscale_work_mpf(self.SD, json.dumps({'enabled': True, 'factor': 2}))
        fresh_db.add_history(file_path="/x/up.mkv",
                             encoding_time_seconds=int(work * 2),
                             duration_seconds=7200, codec='av1',
                             preprocessed=True, pre_work_mpf=work)
        stats = fresh_db.get_encode_speed_stats()
        assert stats['pre_mpf_samples'] == 1
        assert abs(stats['pre_seconds_per_mpf'] - 2.0) < 0.01

    def test_unknown_duration_still_returns_none(self):
        from app.scanner import estimate_item_seconds

        stats = {'overall': 2.0, 'by_codec': {}, 'samples': 1}
        assert estimate_item_seconds(
            {'duration_seconds': 0, 'current_specs': {}}, stats) is None

    def test_stereo_only_job_costs_more_than_plain(self):
        from app.scanner import estimate_item_seconds

        stats = {'overall': 2.0, 'by_codec': {'av1': 2.0}, 'samples': 5}
        plain = estimate_item_seconds(self._item(self.SD), stats)
        stereo = estimate_item_seconds(self._item(self.SD, stereo=True), stats)
        assert stereo > plain


# ---------------------------------------------------------------------------
# Short-output diagnosis — a container's duration is not its picture's length
# ---------------------------------------------------------------------------

class TestShortOutputDiagnosis:
    """Verified against three real failures in the user's library:

      Boondocks WEB-DL mkv  container 1324s, video ends 1266s, output 1266s
      Boondocks WEB-DL mkv  container 1318s, video ends 1270s, output 1270s
      FamilyTherapy mp4     video stream declares 1285s, data stops at 1030s
    """

    def _job(self):
        import app.encoder as enc
        return enc.EncodingJob.__new__(enc.EncodingJob)

    def _patch(self, monkeypatch, last_pts, declared):
        import app.scanner as scanner
        monkeypatch.setattr(scanner, 'probe_last_video_pts',
                            lambda p, start_seconds=0.0, timeout=300: last_pts)
        monkeypatch.setattr(scanner, 'probe_declared_video_duration',
                            lambda p: declared)

    def test_subtitle_tail_is_not_truncation(self, monkeypatch):
        """The mkv case: container says 1324s because a subtitle track runs
        past the picture. Video really ends at 1266s and so does the output —
        the encode is complete and must be accepted."""
        self._patch(monkeypatch, last_pts=1266.1, declared=None)
        verdict, msg = self._job()._diagnose_short_output(
            Path("/x/ep.mkv"), out_duration=1266.0, source_duration=1324.0,
            tolerance=26.5)
        assert verdict == 'ok'
        assert '1266' in msg

    def test_damaged_source_is_quarantined_not_blamed_on_the_encode(
            self, monkeypatch):
        """The mp4 case: the VIDEO STREAM itself declares 1285s but the file
        holds picture only to 1030s. No encode can ever be whole."""
        self._patch(monkeypatch, last_pts=1030.4, declared=1284.5)
        verdict, msg = self._job()._diagnose_short_output(
            Path("/x/broken.mp4"), out_duration=1030.0, source_duration=1284.6,
            tolerance=25.7)
        assert verdict == 'bad_source'
        assert 're-download' in msg.lower()

    def test_real_truncation_still_fails(self, monkeypatch):
        """HandBrake stopping at 10 minutes on a 22-minute source must still
        keep the original — this is the case the check exists for."""
        self._patch(monkeypatch, last_pts=1270.0, declared=None)
        verdict, msg = self._job()._diagnose_short_output(
            Path("/x/ep.mkv"), out_duration=600.0, source_duration=1318.0,
            tolerance=26.4)
        assert verdict == 'truncated'
        assert 'stopped' in msg

    def test_unverifiable_source_is_treated_as_truncation(self, monkeypatch):
        """When the source can't be re-probed we must not gamble with the
        only copy — fall back to keeping the original."""
        self._patch(monkeypatch, last_pts=None, declared=None)
        verdict, _ = self._job()._diagnose_short_output(
            Path("/x/ep.mkv"), out_duration=600.0, source_duration=1318.0,
            tolerance=26.4)
        assert verdict == 'truncated'

    def test_declared_duration_within_tolerance_is_not_damage(self, monkeypatch):
        """An mp4 whose stream duration is a rounding-error longer than its
        last packet is normal, not a damaged file."""
        self._patch(monkeypatch, last_pts=1030.4, declared=1032.0)
        verdict, _ = self._job()._diagnose_short_output(
            Path("/x/ok.mp4"), out_duration=1030.0, source_duration=1060.0,
            tolerance=21.2)
        assert verdict == 'ok'


# ---------------------------------------------------------------------------
# "Show in folder" — opens the file manager on the HOST, never a client path
# ---------------------------------------------------------------------------

class TestRevealInFolder:
    def test_refuses_paths_with_illegal_characters(self):
        """The Windows arm builds a command STRING, so a quote in the path
        would be a way out of it. No legal Windows path has one."""
        from app.api.filesystem import reveal_path

        for bad in ['C:\\a"b\\x.mkv', 'C:\\a\nb\\x.mkv', 'C:\\a\x00b\\x.mkv']:
            result = reveal_path(bad)
            assert result['ok'] is False
            assert 'illegal' in result['detail']

    def test_missing_folder_reports_instead_of_launching(self, tmp_path):
        from app.api.filesystem import reveal_path

        result = reveal_path(str(tmp_path / "gone" / "nothing.mkv"))
        assert result['ok'] is False
        assert 'no longer exists' in result['detail']

    def test_empty_path_is_rejected(self):
        from app.api.filesystem import reveal_path

        assert reveal_path('')['ok'] is False
        assert reveal_path('   ')['ok'] is False

    def test_endpoint_uses_the_stored_path_not_a_client_supplied_one(
            self, fresh_db, monkeypatch):
        """The route takes an item id and reads the path from the queue row,
        so there is no path parameter for a caller to aim elsewhere."""
        import asyncio
        from app.api import queue_routes, filesystem
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        seen = []

        def _fake_reveal(path):
            seen.append(path)
            return {'ok': True, 'folder': '/media/tv', 'detail': 'Opened tv'}

        monkeypatch.setattr(filesystem, 'reveal_path', _fake_reveal)

        qid = fresh_db.add_to_queue(file_path="/media/tv/ep.mkv", profile_id=1)
        result = asyncio.run(queue_routes.reveal_queue_item(qid, current_user={}))

        assert seen == ["/media/tv/ep.mkv"]
        assert result['success'] is True
        assert result['path'] == "/media/tv/ep.mkv"
        assert result['folder'] == '/media/tv'

    def test_endpoint_404s_for_unknown_item(self, fresh_db, monkeypatch):
        import asyncio
        from fastapi import HTTPException
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(queue_routes.reveal_queue_item(999999, current_user={}))
        assert exc.value.status_code == 404

    def test_failure_still_returns_the_path_for_the_clipboard_fallback(
            self, fresh_db, monkeypatch):
        """A headless host (or a browser on another device) can't be shown a
        window — the UI copies the path instead, so it must come back."""
        import asyncio
        from app.api import queue_routes, filesystem
        monkeypatch.setattr(queue_routes, 'db', fresh_db)
        monkeypatch.setattr(filesystem, 'reveal_path', lambda path: {
            'ok': False, 'folder': '/media/tv',
            'detail': 'No file manager available on the Optimizarr host'})

        qid = fresh_db.add_to_queue(file_path="/media/tv/ep.mkv", profile_id=1)
        result = asyncio.run(queue_routes.reveal_queue_item(qid, current_user={}))

        assert result['success'] is False
        assert result['folder'] == '/media/tv'
        assert result['path'] == "/media/tv/ep.mkv"


class TestStaleUpscalePlan:
    """A 1080p MKV with a 960x540 PNG cover was queued as "960x540 png" and
    sent into a ~45-hour x2 AI upscale to 4K. The encoder re-probes first."""

    PLAN = {"enabled": True, "factor": 2, "source_height": 540, "target_height": 1080}

    def _check(self, monkeypatch, height):
        import app.encoder as enc
        import app.upscaler as up
        monkeypatch.setattr(up, '_get_video_info', lambda p: {"height": height})
        job = enc.EncodingJob.__new__(enc.EncodingJob)
        return job._source_already_at_target("/x/DS14.mkv", self.PLAN)

    def test_source_already_at_target_skips(self, monkeypatch):
        assert self._check(monkeypatch, 1080) is True

    def test_low_res_source_still_upscales(self, monkeypatch):
        assert self._check(monkeypatch, 540) is False

    def test_unprobeable_source_keeps_plan(self, monkeypatch):
        assert self._check(monkeypatch, 0) is False

    def test_probe_ignores_cover_art(self):
        import inspect
        import app.upscaler as up
        import app.video2x as v2x
        # Capital V = video streams that aren't attached pictures
        assert '"V:0"' in inspect.getsource(up._get_video_info)
        assert '"V:0"' in inspect.getsource(v2x.video_packet_count)




class TestQueueMaintenance:
    """Background queue upkeep: vanished files, duplicates, stale specs,
    settings drift, quarantine re-tries, remembered sort."""

    PROFILE = {"codec": "av1", "resolution": "", "audio_codec": "copy",
               "upscale_enabled": 0}

    def _setup(self, fresh_db, monkeypatch, profile=None):
        from app import queue_maintenance as qm
        monkeypatch.setattr(qm, 'db', fresh_db)
        prof = dict(self.PROFILE, **(profile or {}))
        monkeypatch.setattr(fresh_db, 'get_profile', lambda pid: prof)
        return qm, prof

    class _FakeScanner:
        def __init__(self, specs):
            self.specs = specs
            self.calls = 0

        def analyze_file(self, path):
            self.calls += 1
            return dict(self.specs)

        def _needs_encoding(self, cur, tgt):
            return cur.get('codec') != tgt.get('codec')

        def _estimate_savings(self, size, *a, **k):
            return size // 2

    def _file(self, tmp_path, name, size=10):
        f = tmp_path / name
        f.write_bytes(b"x" * size)
        return str(f)

    def _add(self, db, path, **kw):
        kw.setdefault('profile_id', 1)
        kw.setdefault('file_size_bytes', 10)
        kw.setdefault('current_specs', {'codec': 'h264', 'height': 1080})
        from app.scanner import profile_targets
        kw.setdefault('target_specs', profile_targets(self.PROFILE))
        return db.add_to_queue(file_path=path, **kw)

    def test_missing_file_removed_any_waiting_status(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        gone = self._add(fresh_db, str(tmp_path / "gone.mkv"))
        failed = self._add(fresh_db, str(tmp_path / "gone2.mkv"), status='failed')
        kept = self._add(fresh_db, self._file(tmp_path, "here.mkv"))
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert counts['removed_missing'] == 2
        assert fresh_db.get_queue_item(gone) is None
        assert fresh_db.get_queue_item(failed) is None
        assert fresh_db.get_queue_item(kept) is not None

    def test_stale_savings_estimate_recomputed(self, fresh_db, monkeypatch, tmp_path):
        """A row saved under an older estimator gets the current estimate
        even when nothing else about it changed."""
        qm, _ = self._setup(fresh_db, monkeypatch)
        item = self._add(fresh_db, self._file(tmp_path, "e.mkv"),
                         estimated_savings_bytes=-999)
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert fresh_db.get_queue_item(item)['estimated_savings_bytes'] == 5
        assert counts['updated'] == counts['targets_synced'] == 0

    def test_offline_drive_keeps_rows(self, fresh_db, monkeypatch, tmp_path):
        """Scan root unreachable (unplugged drive) — not proof of deletion."""
        qm, _ = self._setup(fresh_db, monkeypatch)
        root = fresh_db.create_scan_root(str(tmp_path / "offline"), 1)
        item = self._add(fresh_db, str(tmp_path / "offline" / "show" / "a.mkv"), root_id=root)
        qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert fresh_db.get_queue_item(item) is not None

    def test_deleted_subfolder_on_live_drive_removed(self, fresh_db, monkeypatch, tmp_path):
        """A whole folder moved/deleted while its scan root is online."""
        qm, _ = self._setup(fresh_db, monkeypatch)
        (tmp_path / "lib").mkdir()
        root = fresh_db.create_scan_root(str(tmp_path / "lib"), 1)
        item = self._add(fresh_db, str(tmp_path / "lib" / "gone folder" / "a.mkv"), root_id=root)
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert counts['removed_missing'] == 1
        assert fresh_db.get_queue_item(item) is None

    def test_duplicate_spelling_removed(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        path = self._file(tmp_path, "dup.mkv")
        first = self._add(fresh_db, path, priority=1)
        other = self._add(fresh_db, str(tmp_path / "." / "dup.mkv"), priority=2)
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert counts['duplicates_removed'] == 1
        assert fresh_db.get_queue_item(first) is not None
        assert fresh_db.get_queue_item(other) is None

    def test_unchanged_file_not_reprobed(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        self._add(fresh_db, self._file(tmp_path, "a.mkv"))
        sc = self._FakeScanner({'codec': 'h264'})
        counts = qm.revalidate_queue(scanner=sc)
        assert sc.calls == 0
        assert not any(counts.values())

    def test_cover_art_specs_reprobed_and_upscale_dropped(self, fresh_db, monkeypatch, tmp_path):
        """The DS14 case: queued as 960x540 png, really 1080p."""
        import json
        qm, _ = self._setup(fresh_db, monkeypatch, {
            "upscale_enabled": 1, "upscale_key": "realesrgan", "upscale_model": "realesrgan-x4plus",
            "upscale_factor": 2, "upscale_trigger_below": 720, "upscale_target_height": 1080})
        plan = {"enabled": True, "upscaler_key": "realesrgan", "model": "realesrgan-x4plus",
                "factor": 2, "source_height": 540, "target_height": 1080}
        item = self._add(fresh_db, self._file(tmp_path, "DS14.mkv"),
                         current_specs={'codec': 'png', 'height': 540},
                         upscale_plan=json.dumps(plan))
        sc = self._FakeScanner({'codec': 'h264', 'height': 1080, 'duration': 632.4})
        counts = qm.revalidate_queue(scanner=sc)
        row = fresh_db.get_queue_item(item)
        assert counts['updated'] == 1
        assert not row['upscale_plan']
        assert row['duration_seconds'] == 632.4

    def test_changed_file_already_optimized_removed(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        item = self._add(fresh_db, self._file(tmp_path, "b.mkv", size=20))
        counts = qm.revalidate_queue(scanner=self._FakeScanner({'codec': 'av1'}))
        assert counts['removed_optimized'] == 1
        assert fresh_db.get_queue_item(item) is None

    def test_profile_codec_change_follows(self, fresh_db, monkeypatch, tmp_path):
        import json
        qm, prof = self._setup(fresh_db, monkeypatch)
        item = self._add(fresh_db, self._file(tmp_path, "c.mkv"))
        prof['codec'] = 'h265'
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert counts['targets_synced'] == 1
        ts = fresh_db.get_queue_item(item)['target_specs']
        assert (json.loads(ts) if isinstance(ts, str) else ts)['codec'] == 'h265'
        # Profile now targets the codec the file already has -> nothing to do
        prof['codec'] = 'h264'
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert counts['removed_optimized'] == 1

    def test_plan_follows_engine_trigger_and_toggle(self, fresh_db, monkeypatch, tmp_path):
        import json
        qm, prof = self._setup(fresh_db, monkeypatch, {
            "upscale_enabled": 1, "upscale_key": "rtxvsr", "upscale_model": "ULTRA",
            "upscale_factor": 2, "upscale_trigger_below": 720, "upscale_target_height": 1080})
        old = {"enabled": True, "upscaler_key": "realesrgan", "model": "realesrgan-x4plus",
               "factor": 2, "source_height": 480, "target_height": 1080}
        low = self._add(fresh_db, self._file(tmp_path, "low.mkv"),
                        current_specs={'codec': 'h264', 'height': 480},
                        upscale_plan=json.dumps(old))
        mid = self._add(fresh_db, self._file(tmp_path, "mid.mkv"),
                        current_specs={'codec': 'h264', 'height': 720})
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        plan = json.loads(fresh_db.get_queue_item(low)['upscale_plan'])
        assert counts['plans_synced'] == 1
        assert (plan['upscaler_key'], plan['model']) == ('rtxvsr', 'ULTRA')
        assert not fresh_db.get_queue_item(mid)['upscale_plan']
        # Trigger raised to 800p: the 720p item now qualifies
        prof['upscale_trigger_below'] = 800
        qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert json.loads(fresh_db.get_queue_item(mid)['upscale_plan'])['source_height'] == 720
        # Upscaling turned off -> plans dropped
        prof['upscale_enabled'] = 0
        qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert not fresh_db.get_queue_item(low)['upscale_plan']
        assert not fresh_db.get_queue_item(mid)['upscale_plan']

    def test_quarantined_file_rechecked(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        item = self._add(fresh_db, self._file(tmp_path, "q.mkv"), status='quarantined')

        def _recheck(it):
            fresh_db.update_queue_item(it['id'], status='pending')
            return 'pending'
        monkeypatch.setattr(qm, 'recheck_quarantined_item', _recheck)
        counts = qm.revalidate_queue(scanner=self._FakeScanner({}))
        assert counts['unquarantined'] == 1
        assert fresh_db.get_queue_item(item)['status'] == 'pending'

    def test_after_encode_records_status_and_reapplies_sort(self, fresh_db, monkeypatch):
        qm, _ = self._setup(fresh_db, monkeypatch)
        a = fresh_db.add_to_queue(file_path="/x/a.mkv", profile_id=1, file_size_bytes=5)
        qm.set_auto_sort('file_size', 'desc')
        assert qm.get_auto_sort() == ('file_size', 'desc')
        qm.apply_sort('file_size', 'desc')
        # A bigger file arrives after the sort; the next pass re-ranks it
        b = fresh_db.add_to_queue(file_path="/x/b.mkv", profile_id=1, file_size_bytes=50)
        monkeypatch.setattr(qm, 'revalidate_queue', lambda scanner=None: {'updated': 2})
        qm.after_encode()
        assert fresh_db.get_queue_item(b)['priority'] < fresh_db.get_queue_item(a)['priority']
        status = qm.last_check()
        assert status['counts'] == {'updated': 2} and status['at'] > 0

    def test_interval_setting(self, fresh_db, monkeypatch):
        qm, _ = self._setup(fresh_db, monkeypatch)
        assert qm.interval_minutes() == 15
        fresh_db.set_setting(qm.INTERVAL_SETTING, '0')
        assert qm.interval_minutes() == 0
        fresh_db.set_setting(qm.INTERVAL_SETTING, 'junk')
        assert qm.interval_minutes() == 15

    def test_default_and_manual_reorder_clear_auto_sort(self, fresh_db, monkeypatch):
        qm, _ = self._setup(fresh_db, monkeypatch)
        qm.set_auto_sort('slowest_first', 'desc')
        qm.set_auto_sort('default')
        assert qm.get_auto_sort() is None
        qm.set_auto_sort('fastest_first', 'asc')
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)
        queue_routes.reorder_queue({'items': [{'id': 1, 'priority': 1}]}, current_user={})
        assert qm.get_auto_sort() is None


class TestUpscalePlanRule:
    def test_shared_rule(self):
        import json
        from app.scanner import upscale_plan_for, upscale_source_for
        root = {"upscale_enabled": 1, "upscale_key": "rtxvsr", "upscale_model": "ULTRA",
                "upscale_trigger_below": 720, "upscale_target_height": 1080}
        prof = {"upscale_enabled": 1, "upscale_key": "waifu2x"}
        assert upscale_source_for(root, prof) is root
        assert upscale_source_for({"upscale_enabled": 0}, prof) is prof
        assert upscale_source_for(None, {}) is None
        assert json.loads(upscale_plan_for(root, 480))['upscaler_key'] == 'rtxvsr'
        assert upscale_plan_for(root, 720) is None      # not below trigger
        assert upscale_plan_for(root, 0) is None        # unknown height
        assert upscale_plan_for(None, 480) is None


class TestUpscaylModels:
    """Upscayl's release ships only the binary; models come from its app repo."""

    class _Resp:
        def __init__(self, body=b"", json_body=None):
            self.body, self._json = body, json_body
            self.headers = {}

        def raise_for_status(self):
            pass

        def json(self):
            return self._json

        def iter_content(self, chunk_size=65536):
            yield self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _patch(self, monkeypatch, sizes, bodies, calls):
        import app.upscaler as up

        def fake_get(url, **kw):
            calls.append(url)
            if url == up.UPSCALERS["upscayl"]["models_api"]:
                return self._Resp(json_body=[{"name": n, "size": s} for n, s in sizes.items()])
            return self._Resp(bodies[url.rsplit("/", 1)[1]])
        monkeypatch.setattr(up.requests, "get", fake_get)
        monkeypatch.setitem(up.UPSCALERS["upscayl"], "models", ["m1"])
        return up

    def test_downloads_and_skips_complete_files(self, monkeypatch, tmp_path):
        calls = []
        up = self._patch(monkeypatch, {"m1.param": 3, "m1.bin": 5},
                         {"m1.param": b"abc", "m1.bin": b"12345"}, calls)
        up._download_models("upscayl", tmp_path, {})
        assert (tmp_path / "m1.bin").read_bytes() == b"12345"
        calls.clear()
        up._download_models("upscayl", tmp_path, {})
        assert not any(c.endswith((".param", ".bin")) for c in calls)

    def test_truncated_model_rejected(self, monkeypatch, tmp_path):
        import pytest
        up = self._patch(monkeypatch, {"m1.param": 3, "m1.bin": 99},
                         {"m1.param": b"abc", "m1.bin": b"short"}, [])
        with pytest.raises(RuntimeError, match="truncated"):
            up._download_models("upscayl", tmp_path, {})
        assert not (tmp_path / "m1.bin").exists()

    def test_ui_lists_every_model(self):
        from pathlib import Path
        import app.upscaler as up
        js = (Path(__file__).parent.parent / "web/static/js/app/upscale-stereo.js").read_text(encoding="utf-8")
        for m in up.UPSCALERS["upscayl"]["models"]:
            assert f"'{m}'" in js


class TestVideo2xSourceCodecs:
    def test_av1_goes_to_frame_pipeline(self):
        from app.video2x import supports_source
        assert not supports_source("av1")
        assert not supports_source("AV1")
        assert supports_source("h264")
        assert supports_source("")


class TestCoverArtNeverPicked:
    """Every probe/extract that reads a SOURCE's video must skip attached
    pictures (capital V) — a cover image as stream 0 was read as the video."""

    def test_source_video_selectors(self):
        import inspect
        import app.scanner as sc
        import app.target_quality as tq
        import app.rtx_vsr as vsr
        import app.upscaler as up
        assert "'V:0'" in inspect.getsource(sc.probe_declared_video_duration)
        assert "'V:0'" in inspect.getsource(sc.probe_last_video_pts)
        assert '"0:V:0"' in inspect.getsource(tq.cut_samples)
        assert '"0:V:0"' in inspect.getsource(vsr.run_vsr).split("encode_cmd")[0]
        assert '"0:V:0"' in inspect.getsource(up.run_upscale_pipeline).split("extract_cmd")[1][:400]

    def test_encoder_reprobes_cover_art_specs(self, monkeypatch, tmp_path):
        import app.encoder as enc
        import app.scanner as sc
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x" * 10)
        job = enc.EncodingJob.__new__(enc.EncodingJob)
        job.queue_item_id = 1
        job.queue_item = {'current_specs': {'codec': 'png', 'height': 540}, 'file_size_bytes': 10}
        job._source_info = {'bit_depth': 8}
        monkeypatch.setattr(sc.scanner, 'analyze_file',
                            lambda p: {'codec': 'hevc', 'height': 2160, 'bit_depth': 10,
                                       'hdr': True, 'duration': 60.0})
        updates = {}
        monkeypatch.setattr(enc.db, 'update_queue_item', lambda i, **kw: updates.update(kw))
        job._refresh_stale_specs(str(f))
        assert job.queue_item['current_specs']['bit_depth'] == 10
        assert job._source_info is None and updates['duration_seconds'] == 60.0


class TestProfileConformance:
    """Optimizing means meeting the profile (codec, resolution, framerate,
    bitrate) — not only getting smaller."""

    TARGETS = {'codec': 'av1', 'resolution': '1920x1080', 'framerate': 30,
               'max_bitrate_kbps': 8000, 'audio_codec': 'opus'}

    def test_unmet_targets(self):
        from app.scanner import unmet_targets
        ok = {'codec': 'av1', 'height': 1080, 'framerate': 29.97, 'bit_rate': 6_000_000}
        assert unmet_targets(ok, self.TARGETS) == []
        assert unmet_targets(dict(ok, codec='h264'), self.TARGETS) == ['codec h264 → av1']
        assert unmet_targets(dict(ok, height=2160), self.TARGETS) == ['2160p → 1080p']
        assert unmet_targets(dict(ok, framerate=60), self.TARGETS) == ['60 fps → 30 fps']
        assert unmet_targets(dict(ok, bit_rate=20_000_000), self.TARGETS) == ['20.0 Mbps → ≤8 Mbps']

    def test_ceilings_and_unknowns_never_unmet(self):
        from app.scanner import unmet_targets
        low = {'codec': 'av1', 'height': 480, 'framerate': 24, 'bit_rate': 8_500_000}
        assert unmet_targets(low, self.TARGETS) == []          # under ceilings; audio headroom
        assert unmet_targets({'codec': 'unknown'}, self.TARGETS) == []
        assert unmet_targets({'codec': 'av1'}, {'codec': 'av1'}) == []

    def test_scanner_queues_framerate_and_bitrate_overages(self):
        from app.scanner import scanner
        av1 = {'codec': 'av1', 'resolution': '1920x1080', 'height': 1080,
               'framerate': 60, 'bit_rate': 5_000_000}
        assert scanner._needs_encoding(av1, self.TARGETS)
        assert not scanner._needs_encoding(dict(av1, framerate=30), self.TARGETS)

    def _cmd(self, monkeypatch, **profile):
        import app.encoder as enc
        job = enc.EncodingJob.__new__(enc.EncodingJob)
        job.queue_item = {'file_path': '/x/a.mkv'}
        job.profile = {'name': 'p', 'codec': 'av1', 'quality': 30, 'container': 'mkv', **profile}
        monkeypatch.setattr(job, 'select_encoder', lambda: profile.get('encoder', 'svt_av1_10bit'),
                            raising=False)
        monkeypatch.setattr(enc.EncodingJob, '_build_audio_args', lambda self: [])
        monkeypatch.setattr(enc.EncodingJob, '_build_subtitle_args', lambda self: [])
        return job.build_command()

    def test_resolution_and_framerate_are_ceilings(self, monkeypatch):
        cmd = self._cmd(monkeypatch, resolution='1920x1080', framerate=30)
        assert cmd[cmd.index('--maxWidth') + 1] == '1920'
        assert cmd[cmd.index('--maxHeight') + 1] == '1080'
        assert '--width' not in cmd and '--height' not in cmd   # upscaled small sources
        assert '--pfr' in cmd and cmd[cmd.index('--rate') + 1] == '30'

    def test_bitrate_cap_merged_with_grain(self, monkeypatch):
        cmd = self._cmd(monkeypatch, max_bitrate_kbps=8000, film_grain=8)
        assert cmd.count('--encopts') == 1
        assert cmd[cmd.index('--encopts') + 1] == 'film-grain=8:film-grain-denoise=0:mbr=8000'
        x265 = self._cmd(monkeypatch, codec='h265', encoder='x265_10bit', max_bitrate_kbps=5000)
        assert x265[x265.index('--encopts') + 1] == 'vbv-maxrate=5000:vbv-bufsize=10000'
        nvenc = self._cmd(monkeypatch, encoder='nvenc_h265', max_bitrate_kbps=5000)
        assert '--encopts' not in nvenc

    def _finalize(self, fresh_db, monkeypatch, tmp_path, new_bytes, unmet):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        src = tmp_path / "ep.mkv"
        src.write_bytes(b"o" * 1000)
        out = tmp_path / "ep_optimized.mkv"
        out.write_bytes(b"e" * new_bytes)
        job, qid = TestDestinationCollision._job(None, enc, fresh_db, src)
        job._unmet = unmet
        job._finalize_encoding()
        return src, fresh_db.get_queue_item(qid)

    def test_conversion_kept_when_bigger(self, fresh_db, monkeypatch, tmp_path):
        """Uniform library: a lean H.264 that grows 80% to reach AV1 at the
        profile's quality is still converted."""
        src, item = self._finalize(fresh_db, monkeypatch, tmp_path, 1800, ['codec h264 → av1'])
        assert item['status'] == 'completed'
        assert src.read_bytes() == b"e" * 1800

    def test_conversion_rejected_only_when_implausible(self, fresh_db, monkeypatch, tmp_path):
        src, item = self._finalize(fresh_db, monkeypatch, tmp_path, 3100, ['codec h264 → av1'])
        assert item['status'] == 'kept_original'
        assert "doesn't meet the profile (codec h264 → av1)" in item['error_message']
        assert src.read_bytes() == b"o" * 1000

    def test_conforming_source_still_needs_savings(self, fresh_db, monkeypatch, tmp_path):
        src, item = self._finalize(fresh_db, monkeypatch, tmp_path, 980, [])
        assert item['status'] == 'kept_original'
        assert 'already meets the profile' in item['error_message']


class TestStaleQualitySearch:
    """A keep/reject policy change invalidates quality searches cached on
    waiting rows (they were chosen under the old rule)."""

    def test_policy_change_clears_cached_search_once(self, fresh_db, monkeypatch, tmp_path):
        from app import queue_maintenance as qm
        monkeypatch.setattr(qm, 'db', fresh_db)
        f = tmp_path / "a.mkv"
        f.write_bytes(b"x")
        waiting = fresh_db.add_to_queue(file_path=str(f), profile_id=1, status='pending')
        done = fresh_db.add_to_queue(file_path=str(tmp_path / "b.mkv"), profile_id=1,
                                     status='completed')
        for qid in (waiting, done):
            fresh_db.update_queue_item(qid, tq_quality=34, tq_vmaf=92.3)
        assert qm.reset_stale_quality_searches() == 1
        assert fresh_db.get_queue_item(waiting)['tq_quality'] is None
        assert fresh_db.get_queue_item(done)['tq_quality'] == 34     # history untouched
        fresh_db.update_queue_item(waiting, tq_quality=39)
        assert qm.reset_stale_quality_searches() == 0                 # once per policy
        assert fresh_db.get_queue_item(waiting)['tq_quality'] == 39


class TestFinishedConformanceSweep:
    PROFILE = {"codec": "av1", "resolution": "", "framerate": 30, "audio_codec": "copy",
               "max_bitrate_kbps": 0, "upscale_enabled": 0}

    def _setup(self, fresh_db, monkeypatch):
        from app import queue_maintenance as qm
        monkeypatch.setattr(qm, 'db', fresh_db)
        prof = dict(self.PROFILE)
        monkeypatch.setattr(fresh_db, 'get_profile', lambda pid: prof)
        return qm, prof

    def _scanner(self, specs):
        return TestQueueMaintenance._FakeScanner(specs)

    def _finished(self, fresh_db, tmp_path, name, status='completed', **kw):
        f = tmp_path / name
        f.write_bytes(b"x" * 10)
        return fresh_db.add_to_queue(file_path=str(f), profile_id=1, status=status, **kw), f

    def test_off_profile_requeued_once(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        item, _ = self._finished(fresh_db, tmp_path, "a.mkv", status='kept_original',
                                 error_message='Kept original — needs ≥5% savings')
        sc = self._scanner({'codec': 'h264', 'height': 1080, 'framerate': 24})
        assert qm.recheck_finished(sc, {}) == 1
        row = fresh_db.get_queue_item(item)
        assert row['status'] == 'pending' and row['error_message'] is None
        # It encodes, but is kept as the original again (grew too much):
        fresh_db.update_queue_item(item, status='kept_original')
        assert qm.recheck_finished(sc, {}) == 0          # no loop
        assert sc.calls == 1

    def test_on_profile_stamped_not_reprobed(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        item, _ = self._finished(fresh_db, tmp_path, "b.mkv")
        sc = self._scanner({'codec': 'av1', 'height': 1080, 'framerate': 30})
        assert qm.recheck_finished(sc, {}) == 0
        assert fresh_db.get_queue_item(item)['status'] == 'completed'
        qm.recheck_finished(sc, {})
        assert sc.calls == 1

    def test_policy_change_retries_kept_conversion(self, fresh_db, monkeypatch, tmp_path):
        """A conversion kept as the original under the old 10% growth cap
        (stamped without a policy) is re-queued once under the new rule."""
        import json
        from app.scanner import profile_targets
        qm, prof = self._setup(fresh_db, monkeypatch)
        old = json.dumps(profile_targets(prof), sort_keys=True)
        item, _ = self._finished(fresh_db, tmp_path, "k.mkv", status='kept_original')
        fresh_db.update_queue_item(item, conformance_checked=old)
        sc = self._scanner({'codec': 'h264', 'height': 1080, 'framerate': 24})
        assert qm.recheck_finished(sc, {}) == 1
        assert fresh_db.get_queue_item(item)['status'] == 'pending'

    def test_profile_change_rechecks(self, fresh_db, monkeypatch, tmp_path):
        """60 fps AV1 met a no-framerate profile; capping at 30 re-queues it."""
        qm, prof = self._setup(fresh_db, monkeypatch)
        prof['framerate'] = 0
        item, _ = self._finished(fresh_db, tmp_path, "c.mkv")
        sc = self._scanner({'codec': 'av1', 'height': 1080, 'framerate': 60})
        assert qm.recheck_finished(sc, {}) == 0
        prof['framerate'] = 30
        assert qm.recheck_finished(sc, {}) == 1
        assert fresh_db.get_queue_item(item)['status'] == 'pending'

    def test_follows_container_change(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        item = fresh_db.add_to_queue(file_path=str(tmp_path / "d.mp4"), profile_id=1,
                                     status='completed')
        (tmp_path / "d.mkv").write_bytes(b"x" * 10)
        sc = self._scanner({'codec': 'av1', 'height': 1080, 'framerate': 60})
        assert qm.recheck_finished(sc, {}) == 1
        assert fresh_db.get_queue_item(item)['file_path'] == str(tmp_path / "d.mkv")

    def test_budget(self, fresh_db, monkeypatch, tmp_path):
        qm, _ = self._setup(fresh_db, monkeypatch)
        for i in range(5):
            self._finished(fresh_db, tmp_path, f"e{i}.mkv")
        sc = self._scanner({'codec': 'av1', 'height': 1080, 'framerate': 30})
        qm.recheck_finished(sc, {}, budget=2)
        assert sc.calls == 2


class TestConversionSearch:
    """Conversions get the profile's quality target whatever the size."""

    @staticmethod
    def _curve(q):
        # Lean source: VMAF 95 needs q<=26, where the output is 160% of the
        # source; size drops ~8 points per q step.
        return (99 - q * 0.15, max(0.3, 1.6 - (q - 26) * 0.08), 40 - q * 0.2)

    def test_conversion_meets_target_even_when_bigger(self):
        from app.target_quality import search
        r = search(self._curve, 15, 55, 28, target=95.0, convert=True, max_iter=8)
        assert r['met_target'] and r['quality'] == 26
        assert r['size_ratio'] > 1.0
        assert r['xpsnr'] == self._curve(26)[2]

    def test_no_convert_stops_early_for_a_skip(self):
        """Source already meets its profile: a candidate that misses VMAF
        while already bigger ends the search — the file will be kept as the
        original, so more encodes would be wasted."""
        from app.target_quality import search
        r = search(self._curve, 15, 55, 28, target=95.0, max_iter=8)
        assert not r['met_target']
        assert r['size_ratio'] > 1.0 and list(r['evaluations']) == [28]

    @staticmethod
    def _measured(q):
        # Shape of a real search on this library (animated 1080p, Oct 20):
        # VMAF 96.90 @ q28, 95.33 @ q38, 95.12 @ q39, 94.91 @ q40, 94.35 @ q42.
        pts = {28: 96.90, 35: 95.95, 38: 95.33, 39: 95.12, 40: 94.91, 42: 94.35}
        if q in pts:
            v = pts[q]
        elif q < 28:
            v = 96.90 + (28 - q) * 0.15
        elif q > 42:
            v = 94.35 - (q - 42) * 0.3
        else:
            lo = max(k for k in pts if k < q)
            hi = min(k for k in pts if k > q)
            v = pts[lo] + (pts[hi] - pts[lo]) * (q - lo) / (hi - lo)
        return v, 0.3 + (55 - q) * 0.02, 39.0

    def test_interpolation_needs_few_encodes(self):
        """From the profile's q28 the old bisection took 6 encodes on this
        curve; interpolating toward the crossing finds q39 in fewer (the
        curve bends, so a cold start still overshoots once)."""
        from app.target_quality import search
        calls = []

        def ev(q):
            calls.append(q)
            return self._measured(q)
        r = search(ev, 15, 55, 28, target=95.0, convert=True)
        assert r['quality'] == 39 and r['met_target']
        assert len(calls) <= 5

    def test_good_start_needs_two_encodes(self):
        """Started where similar files ended (q39): confirm it and its
        neighbour, nothing else."""
        from app.target_quality import search
        calls = []

        def ev(q):
            calls.append(q)
            return self._measured(q)
        r = search(ev, 15, 55, 39, target=95.0, convert=True)
        assert r['quality'] == 39 and sorted(calls) == [39, 40]

    def test_unreachable_target_goes_straight_to_best_quality(self):
        """A source that can't reach the target at the start jumps toward the
        best quality instead of halving down step by step."""
        from app.target_quality import search
        calls = []

        def ev(q):
            calls.append(q)
            return 88.0 - (q - 15) * 0.2, 1.4, 35.0
        r = search(ev, 15, 55, 28, target=95.0, convert=True)
        assert r['quality'] == 15 and not r['met_target']
        assert len(calls) <= 3


class TestLearnedStartQuality:
    """The quality search starts where similar finished files ended."""

    def _rows(self, db, tmp_path, qualities, profile_id=2, content_type=None):
        for i, q in enumerate(qualities):
            qid = db.add_to_queue(file_path=str(tmp_path / f"{profile_id}{content_type}{i}.mkv"),
                                  profile_id=profile_id, status='completed')
            db.update_queue_item(qid, tq_quality=q, content_type=content_type)

    def test_median_of_same_content_type(self, fresh_db, tmp_path):
        from app.encoder import learned_start_quality
        self._rows(fresh_db, tmp_path, [38, 39, 39, 40, 41], content_type='2d')
        self._rows(fresh_db, tmp_path, [28, 29, 30, 30, 31], content_type='live')
        assert learned_start_quality(fresh_db, 2, '2d', default=28) == 39
        assert learned_start_quality(fresh_db, 2, 'live', default=28) == 30

    def test_falls_back_to_profile_then_default(self, fresh_db, tmp_path):
        from app.encoder import learned_start_quality
        self._rows(fresh_db, tmp_path, [33, 34, 35, 36, 37], content_type='live')
        # too few '3d' rows -> whole profile; other profile -> default
        assert learned_start_quality(fresh_db, 2, '3d', default=28) == 35
        assert learned_start_quality(fresh_db, 9, 'live', default=28) == 28

    def test_scores_parsed_from_one_pass(self, monkeypatch):
        import app.target_quality as tq

        class P:
            def communicate(self, timeout=None):
                return "", "[Parsed_libvmaf_7] VMAF score: 93.51\nXPSNR  y: 38.1234 u: 40.0"
        monkeypatch.setattr(tq.subprocess, 'Popen', lambda *a, **k: P())
        assert tq.quality_scores('d', 'r', 1920, 1080, lambda p: None) == (93.51, 38.1234)


class TestPresetDefault:
    def test_svt_auto_preset_is_6(self, monkeypatch):
        cmd = TestProfileConformance._cmd(None, monkeypatch)
        assert cmd[cmd.index('--encoder-preset') + 1] == '6'
        cmd = TestProfileConformance._cmd(None, monkeypatch, preset='4')
        assert cmd[cmd.index('--encoder-preset') + 1] == '4'
        x265 = TestProfileConformance._cmd(None, monkeypatch, codec='h265', encoder='x265_10bit')
        assert '--encoder-preset' not in x265            # HandBrake's own default


class TestFallbackNotices:
    def test_notice_saved_on_completion(self, fresh_db, monkeypatch, tmp_path):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        src = tmp_path / "n.mkv"
        src.write_bytes(b"o" * 1000)
        (tmp_path / "n_optimized.mkv").write_bytes(b"e" * 500)
        job, qid = TestDestinationCollision._job(None, enc, fresh_db, src)
        job._note("AI upscale (rtxvsr ULTRA) failed — encoded without upscaling; see the log")
        job._note("AI upscale (rtxvsr ULTRA) failed — encoded without upscaling; see the log")
        job._finalize_encoding()
        row = fresh_db.get_queue_item(qid)
        assert row['status'] == 'completed'
        assert row['notice'].count('AI upscale') == 1

    def test_no_notice_when_clean(self, fresh_db, monkeypatch, tmp_path):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        src = tmp_path / "c.mkv"
        src.write_bytes(b"o" * 1000)
        (tmp_path / "c_optimized.mkv").write_bytes(b"e" * 500)
        job, qid = TestDestinationCollision._job(None, enc, fresh_db, src)
        job._finalize_encoding()
        assert fresh_db.get_queue_item(qid)['notice'] is None


class TestEngineHealth:
    def test_output_checks(self, monkeypatch, tmp_path):
        import app.engine_health as eh
        f = tmp_path / "o.mkv"
        f.write_bytes(b"x")
        monkeypatch.setattr(eh, '_probe', lambda p: {'width': 852, 'height': 480, 'frames': 48})
        assert eh._check_output(str(f), 852, 480) is None
        assert 'no output' in eh._check_output(None, 852, 480)
        monkeypatch.setattr(eh, '_probe', lambda p: {'width': 852, 'height': 480, 'frames': 3})
        assert 'frames' in eh._check_output(str(f), 852, 480)
        monkeypatch.setattr(eh, '_probe', lambda p: {'width': 426, 'height': 240, 'frames': 48})
        assert '426x240' in eh._check_output(str(f), 852, 480)

    def test_failure_notifies_once_and_surfaces_when_in_use(self, fresh_db, monkeypatch, tmp_path):
        import app.engine_health as eh
        monkeypatch.setattr(eh, 'db', fresh_db)
        monkeypatch.setattr(eh, 'make_clip', lambda work: tmp_path / "clip.mkv")
        monkeypatch.setattr(eh, 'engines_to_test', lambda only=None: ['rtxvsr'])
        monkeypatch.setattr(eh, '_test_upscaler', lambda key, clip: 'produced no output')
        sent = []
        monkeypatch.setattr(eh, '_notify_failure', lambda k, m: sent.append(k))
        eh.run_self_test()
        eh.run_self_test()
        assert sent == ['rtxvsr']                       # only on the pass->fail edge
        assert eh.load()['rtxvsr']['ok'] is False
        monkeypatch.setattr(eh, 'engines_in_use', lambda: ['handbrake', 'rtxvsr'])
        assert eh.problems_in_use() == [{'engine': 'rtxvsr', 'message': 'produced no output'}]


class TestConnectionSave:
    """Radarr 'hit save and got nada': missing cryptography + hidden errors."""

    def test_normalize_base_url(self):
        from app.external_connections import normalize_base_url as n
        assert n("localhost:7878") == "http://localhost:7878"
        assert n(" http://localhost:7878/ ") == "http://localhost:7878"
        assert n("http://localhost:7878/api/v3") == "http://localhost:7878"
        assert n("https://stash.lan/graphql") == "https://stash.lan"
        assert n("http://nas:7878/radarr") == "http://nas:7878/radarr"   # URL base kept

    def test_missing_cryptography_is_a_readable_error(self, monkeypatch):
        import builtins
        import pytest
        from fastapi import HTTPException
        from app.api import connection_routes
        real_import = builtins.__import__

        def no_crypto(name, *a, **k):
            if name.startswith("cryptography"):
                raise ImportError(name)
            return real_import(name, *a, **k)
        monkeypatch.setattr(builtins, "__import__", no_crypto)
        with pytest.raises(HTTPException) as e:
            connection_routes.create_connection(
                {"app_type": "radarr", "base_url": "localhost:7878", "api_key": "k", "name": "R"},
                current_user={})
        assert e.value.status_code == 500
        assert "pip install" in e.value.detail and "cryptography" in e.value.detail

    def test_test_endpoint_saves_nothing(self, fresh_db, monkeypatch):
        import pytest
        pytest.importorskip("cryptography")
        from app.api import connection_routes
        from app.external_connections import connection_manager
        monkeypatch.setattr(connection_routes, "db", fresh_db)
        seen = {}

        def fake_test(conn):
            seen.update(conn)
            return {"ok": True, "app_name": "Radarr", "version": "6"}
        monkeypatch.setattr(connection_manager, "test_connection", fake_test)
        r = connection_routes.test_connection_settings(
            {"app_type": "radarr", "base_url": "localhost:7878", "api_key": "k"}, current_user={})
        assert r["ok"] and seen["base_url"] == "http://localhost:7878"
        assert fresh_db.get_external_connections() == []

    def test_http_error_message_not_masked(self, monkeypatch):
        """A requests Response is falsy for 4xx/5xx — 404 used to read 'HTTP error'."""
        import requests
        from app.external_connections import connection_manager
        resp = requests.Response()
        resp.status_code = 404

        def raise_404(conn, path, params=None):
            raise requests.exceptions.HTTPError(response=resp)
        monkeypatch.setattr(connection_manager, "_get", raise_404)
        r = connection_manager.test_connection({"app_type": "radarr", "base_url": "x"})
        assert "404" in r["error"] and "address only" in r["error"]

    def test_requirements_check(self, tmp_path):
        from app.requirements_check import missing_requirements
        req = tmp_path / "requirements.txt"
        req.write_text("# comment\nrequests>=2\nsurely-not-a-real-pkg==1.0  # x\n", encoding="utf-8")
        assert missing_requirements(req) == ["surely-not-a-real-pkg==1.0"]


class TestContentClassifier:
    def test_decide(self):
        from app.content_classifier import decide
        assert decide({'2d': 0.6, '3d': 0.2, 'live': 0.2}, 0.6) == ('2d', 0.8)
        assert decide({'2d': 0.2, '3d': 0.5, 'live': 0.3}, 0.6)[0] == '3d'
        assert decide({'2d': 0.1, '3d': 0.1, 'live': 0.8}, 0.6) == ('live', 0.8)
        assert decide({'2d': 0.2, '3d': 0.25, 'live': 0.55}, 0.6)[0] == 'uncertain'

    def test_calibration_recommends_lowest_reliable_threshold(self):
        from app.content_classifier import calibration
        sure = [('live', {'2d': .05, '3d': .05, 'live': .9})] * 20 + \
               [('2d', {'2d': .8, '3d': .1, 'live': .1})] * 15
        shaky = [('live', {'2d': .3, '3d': .27, 'live': .43})] * 3   # model wrong, unsure
        c = calibration(sure + shaky)
        assert c['labeled'] == 38
        row50 = next(r for r in c['table'] if r['threshold'] == 0.5)
        assert row50['accuracy'] < 0.97                               # the shaky ones count
        assert c['recommended'] == 0.6                                # they drop out at 0.60
        assert c['confusion']['live']['2d'] == 3                      # 2d .30 > 3d .27

    def test_auto_choice_and_fallback(self, monkeypatch):
        import app.content_classifier as cc
        installed = {'realcugan', 'rtxvsr', 'upscayl'}
        monkeypatch.setattr(cc, '_installed', lambda k: k in installed)
        monkeypatch.setattr(cc, '_setting', lambda k, d: d)
        assert cc.auto_choice('3d') == ('realcugan', 'models-pro')
        assert cc.auto_choice('live') == ('rtxvsr', 'ULTRA')
        assert cc.auto_choice('uncertain') == ('rtxvsr', 'ULTRA')
        installed.discard('rtxvsr')                                   # no RTX GPU
        assert cc.auto_choice('live') == ('upscayl', 'upscayl-lite-4x')

    def test_classify_pending_tags_rows(self, fresh_db, monkeypatch, tmp_path):
        import app.content_classifier as cc
        from app import queue_maintenance as qm
        monkeypatch.setattr(qm, 'db', fresh_db)
        monkeypatch.setattr(cc, 'enabled', lambda: True)
        monkeypatch.setattr(cc, 'threshold', lambda: 0.6)
        f1, f2 = tmp_path / 'a.mkv', tmp_path / 'b.mkv'
        f1.write_bytes(b'x'); f2.write_bytes(b'x')
        a = fresh_db.add_to_queue(file_path=str(f1), profile_id=1)
        b = fresh_db.add_to_queue(file_path=str(f2), profile_id=1)
        monkeypatch.setattr(cc, 'classify', lambda paths, timeout=1800: {
            str(f1): {'probs': {'2d': .7, '3d': .1, 'live': .2}},
            str(f2): {'error': 'no frames'}})
        assert qm.classify_pending() == 1
        assert fresh_db.get_queue_item(a)['content_type'] == '2d'
        assert fresh_db.get_queue_item(b)['content_type'] is None
        # already classified with this model version -> skipped next pass
        monkeypatch.setattr(cc, 'classify', lambda paths, timeout=1800: {p: {'error': 'x'} for p in paths})
        assert qm.classify_pending() == 0

    def test_quiz_answer_overrides_and_is_kept(self, fresh_db, monkeypatch):
        import json
        import app.content_classifier as cc
        from app.api import classifier_routes as cr
        from app import queue_maintenance as qm
        monkeypatch.setattr(cr, 'db', fresh_db)
        monkeypatch.setattr(qm, 'db', fresh_db)
        monkeypatch.setattr(cc, 'threshold', lambda: 0.6)
        probs = {'2d': .2, '3d': .45, 'live': .35}
        q = fresh_db.add_to_queue(file_path='/x/vr.mkv', profile_id=1)
        fresh_db.update_queue_item(q, content_type='3d', content_probs=cc.record(probs))
        r = cr.quiz_answer({'queue_id': q, 'label': 'live'}, current_user={})
        assert r['model'] == '3d' and r['agrees'] is False
        row = fresh_db.get_queue_item(q)
        assert row['content_type'] == 'live'
        assert json.loads(row['content_probs'])['source'] == 'user'
        s = cr.quiz_summary(current_user={})
        assert s['labeled'] == 1
        # a user label survives the background pass and threshold changes
        monkeypatch.setattr(cc, 'enabled', lambda: True)
        assert qm.classify_pending() == 0
        cr._redecide_all()
        assert fresh_db.get_queue_item(q)['content_type'] == 'live'

    def test_live_action_film_grain_setting(self, fresh_db, monkeypatch):
        import app.encoder as enc
        monkeypatch.setattr(enc, 'db', fresh_db)
        fresh_db.set_setting('auto_film_grain_live', '8')

        def cmd_for(ctype, profile_grain=0):
            job = enc.EncodingJob.__new__(enc.EncodingJob)
            job.queue_item = {'file_path': '/x/a.mkv', 'content_type': ctype}
            job.profile = {'name': 'p', 'codec': 'av1', 'quality': 30, 'container': 'mkv',
                           'film_grain': profile_grain}
            monkeypatch.setattr(job, 'select_encoder', lambda: 'svt_av1_10bit', raising=False)
            monkeypatch.setattr(enc.EncodingJob, '_build_audio_args', lambda self: [])
            monkeypatch.setattr(enc.EncodingJob, '_build_subtitle_args', lambda self: [])
            c = job.build_command()
            return c[c.index('--encopts') + 1] if '--encopts' in c else ''
        assert 'film-grain=8' in cmd_for('live')
        assert cmd_for('2d') == ''
        assert cmd_for('uncertain') == ''
        assert 'film-grain=3' in cmd_for('live', profile_grain=3)    # profile wins
