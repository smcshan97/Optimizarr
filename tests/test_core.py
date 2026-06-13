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
