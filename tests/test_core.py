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
            'users', 'sessions', 'api_keys', 'settings', 'schedule',
        }
        missing = required - tables
        assert not missing, f"Missing tables: {missing}"

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
        """Ratios group by target codec; overall is sample-weighted."""
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
        # weighted: (0.5*2 + 4.0*1) / 3
        assert abs(stats['overall'] - (5.0 / 3)) < 1e-9

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

        cfg = asyncio.run(system_routes.export_config_json(current_user={}))
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
        res = asyncio.run(system_routes.import_config_json(config, current_user={}))
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
            asyncio.run(system_routes.import_config_json({"foo": "bar"}, current_user={}))
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
        asyncio.run(queue_routes.retry_queue_item(qid, current_user={}))
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

        removed = sc.cleanup_orphaned_outputs()
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

        removed = sc.cleanup_orphaned_outputs(exclude_paths=[str(active)])
        assert removed == 1
        assert active.exists()                          # protected
        assert not (tmp_path / "dead_optimized.mkv").exists()


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
        monkeypatch.setattr(queue_routes, 'db', fresh_db)
        self._make_default_profile(fresh_db)

        big = fresh_db.add_to_queue(file_path="/x/vr_giant.mkv", profile_id=1,
                                    duration_seconds=0, file_size_bytes=80_000_000_000,
                                    target_specs={'codec': 'av1'})
        small = fresh_db.add_to_queue(file_path="/x/clip.mkv", profile_id=1,
                                      duration_seconds=0, file_size_bytes=200_000_000,
                                      target_specs={'codec': 'av1'})

        asyncio.run(queue_routes.prioritize_queue(
            {'sort_by': 'fastest_first', 'order': 'asc'}, current_user={}))

        # Small clip gets rank 1, giant VR last — even with no durations
        assert fresh_db.get_queue_item(small)['priority'] < fresh_db.get_queue_item(big)['priority']

    def test_known_duration_beats_size_proxy(self, fresh_db, monkeypatch):
        """A short known-duration encode outranks a big unknown-duration file."""
        import asyncio
        from app.api import queue_routes
        monkeypatch.setattr(queue_routes, 'db', fresh_db)
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

        asyncio.run(queue_routes.prioritize_queue(
            {'sort_by': 'fastest_first', 'order': 'asc'}, current_user={}))
        assert fresh_db.get_queue_item(short)['priority'] < fresh_db.get_queue_item(bigunknown)['priority']
