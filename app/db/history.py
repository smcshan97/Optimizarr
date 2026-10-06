"""Encode history, speed learning and statistics."""
from typing import List, Dict


class HistoryMixin:
    """Database methods: encode history, speed learning and statistics."""

    # History and Statistics
    def add_history(self, **kwargs) -> int:
        """Add a history record."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO history (file_path, profile_name, original_size_bytes, new_size_bytes,
                                     savings_bytes, encoding_time_seconds, codec, container,
                                     duration_seconds, preprocessed, pre_work_mpf)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('profile_name'),
                kwargs.get('original_size_bytes', 0),
                kwargs.get('new_size_bytes', 0),
                kwargs.get('savings_bytes', 0),
                kwargs.get('encoding_time_seconds', 0),
                kwargs.get('codec'),
                kwargs.get('container'),
                kwargs.get('duration_seconds', 0),
                1 if kwargs.get('preprocessed') else 0,
                kwargs.get('pre_work_mpf', 0) or 0,
            ))
            return cursor.lastrowid
    
    # Sanity bounds for plain-encode speed ratios. History rows written
    # before encode time was measured around the HandBrake phase alone
    # include upscale phases / thermal pauses / sleep (ratios up to ~4900×);
    # the median absorbs most of that, the clamp catches the rest.
    _RATIO_MIN = 0.05
    _RATIO_MAX = 100.0

    def get_encode_speed_stats(self) -> Dict:
        """Encode speed ratios learned from history.

        Ratio = encode seconds per second of video (2.0 means a 1-hour video
        takes ~2 hours). Grouped by target codec since speed differs wildly
        between e.g. NVENC H.265 and software AV1. Uses the MEDIAN of per-row
        ratios, not the mean — a single polluted row (machine slept, legacy
        wall-clock timing) shifts a mean arbitrarily far but barely moves a
        median. Only rows with a known duration count.

        Preprocessed jobs (AI upscale / stereo phases) are their own group:
        their times are genuinely days-long and would poison the plain-encode
        numbers, but as a separate ratio they make slowest-first rank upscale
        items realistically.

        Returns {'overall': float|None, 'by_codec': {codec: float},
                 'samples': int, 'preprocessed': float|None,
                 'preprocessed_samples': int,
                 'pre_seconds_per_mpf': float|None, 'pre_mpf_samples': int}.
        """
        import statistics as _stats
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT codec, COALESCE(preprocessed, 0) AS preprocessed,
                       COALESCE(pre_work_mpf, 0) AS pre_work_mpf,
                       encoding_time_seconds,
                       CAST(encoding_time_seconds AS REAL) / duration_seconds AS ratio
                FROM history
                WHERE encoding_time_seconds > 0 AND duration_seconds > 0
            """)
            plain_by_codec: Dict[str, list] = {}
            plain_all: list = []
            pre_all: list = []
            pre_mpf: list = []
            for row in cursor.fetchall():
                if row['preprocessed']:
                    pre_all.append(row['ratio'])
                    # Seconds of AI work per megapixel-frame — the only figure
                    # that transfers between an upscale of a 480p short and one
                    # of a 4K feature. Duration-based ratios do not.
                    if row['pre_work_mpf'] > 0:
                        pre_mpf.append(row['encoding_time_seconds'] / row['pre_work_mpf'])
                else:
                    plain_by_codec.setdefault(row['codec'] or '', []).append(row['ratio'])
                    plain_all.append(row['ratio'])

        def _clamped_median(ratios):
            return max(self._RATIO_MIN, min(self._RATIO_MAX, _stats.median(ratios)))

        by_codec = {codec: _clamped_median(r) for codec, r in plain_by_codec.items()}
        overall = _clamped_median(plain_all) if plain_all else None
        preprocessed = _stats.median(pre_all) if pre_all else None
        return {
            'overall': overall,
            'by_codec': by_codec,
            'samples': len(plain_all),
            'preprocessed': preprocessed,
            'preprocessed_samples': len(pre_all),
            'pre_seconds_per_mpf': _stats.median(pre_mpf) if pre_mpf else None,
            'pre_mpf_samples': len(pre_mpf),
        }

    def get_pending_duration_by_codec(self) -> List[Dict]:
        """Sum of video durations for pending queue items, per target codec.

        Feeds the total-queue-ETA estimate: each codec group's duration is
        multiplied by that codec's encode speed ratio. ``unknown_count`` is
        the number of items whose duration is unrecorded (no ETA possible).
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COALESCE(json_extract(target_specs, '$.codec'), '') AS codec,
                       CASE WHEN COALESCE(json_extract(upscale_plan, '$.enabled'), 0)
                              OR COALESCE(json_extract(stereo_plan, '$.enabled'), 0)
                            THEN 1 ELSE 0 END AS preprocessed,
                       COALESCE(SUM(CASE WHEN duration_seconds > 0
                           THEN duration_seconds ELSE 0 END), 0) AS total_duration,
                       SUM(CASE WHEN duration_seconds IS NULL OR duration_seconds <= 0
                           THEN 1 ELSE 0 END) AS unknown_count
                FROM queue
                WHERE status = 'pending'
                GROUP BY 1, 2
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_preprocessed_items(self) -> List[Dict]:
        """Pending queue items that carry an AI upscale or stereo phase.

        Their cost is driven by output pixels, not runtime, so the total-queue
        ETA has to price them one at a time instead of summing durations by
        codec the way plain encodes are handled.
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, duration_seconds, current_specs, target_specs,
                       upscale_plan, stereo_plan
                FROM queue
                WHERE status = 'pending'
                  AND (COALESCE(json_extract(upscale_plan, '$.enabled'), 0)
                       OR COALESCE(json_extract(stereo_plan, '$.enabled'), 0))
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_stats_dashboard(self, days: int = 30) -> Dict:
        """Get comprehensive statistics for the dashboard."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            
            # Overall totals
            cursor.execute("""
                SELECT COUNT(*) as total,
                       COALESCE(SUM(original_size_bytes), 0) as total_original,
                       COALESCE(SUM(new_size_bytes), 0) as total_new,
                       COALESCE(SUM(savings_bytes), 0) as total_saved,
                       COALESCE(SUM(encoding_time_seconds), 0) as total_time,
                       COALESCE(AVG(CASE WHEN original_size_bytes > 0
                           THEN (savings_bytes * 100.0 / original_size_bytes) ELSE 0 END), 0) as avg_savings_pct,
                       COALESCE(AVG(encoding_time_seconds), 0) as avg_encode_seconds,
                       COALESCE(AVG(CASE WHEN duration_seconds > 0 AND encoding_time_seconds > 0
                           THEN duration_seconds * 1.0 / encoding_time_seconds END), 0) as avg_speed_x
                FROM history
                WHERE completed_at >= datetime('now', ?)
            """, (f'-{days} days',))
            totals = dict(cursor.fetchone())
            
            # Daily breakdown (last N days)
            cursor.execute("""
                SELECT DATE(completed_at) as date,
                       COUNT(*) as files,
                       COALESCE(SUM(savings_bytes), 0) as saved,
                       COALESCE(SUM(encoding_time_seconds), 0) as time_seconds
                FROM history 
                WHERE completed_at >= datetime('now', ?)
                GROUP BY DATE(completed_at)
                ORDER BY date
            """, (f'-{days} days',))
            daily = [dict(row) for row in cursor.fetchall()]
            
            # Codec breakdown (within selected period)
            cursor.execute("""
                SELECT COALESCE(codec, 'unknown') as codec,
                       COUNT(*) as count,
                       COALESCE(SUM(savings_bytes), 0) as saved
                FROM history
                WHERE completed_at >= datetime('now', ?)
                GROUP BY codec ORDER BY count DESC
            """, (f'-{days} days',))
            codecs = [dict(row) for row in cursor.fetchall()]
            
            # Recent history (last 20)
            cursor.execute("""
                SELECT * FROM history ORDER BY completed_at DESC LIMIT 20
            """)
            recent = [dict(row) for row in cursor.fetchall()]
            
            result = {
                'totals': totals,
                'daily': daily,
                'codecs': codecs,
                'recent': recent,
            }
        # Attached OUTSIDE the with block: get_encode_speed_stats opens its
        # own connection, and get_connection's lock is NOT reentrant —
        # calling it nested deadlocks the whole Database instance.
        # All-time speed ratios (same numbers the ETA estimator uses).
        result['encode_speed'] = self.get_encode_speed_stats()
        return result
    
    def clear_history_file_names(self) -> int:
        """Anonymize file paths in history while preserving all statistical data."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE history SET file_path = 'cleared' WHERE file_path != 'cleared'"
            )
            return cursor.rowcount

    def purge_history(self) -> int:
        """Delete all history records. Stats will reset to zero."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history")
            count = cursor.fetchone()[0]
            cursor.execute("DELETE FROM history")
            return count
