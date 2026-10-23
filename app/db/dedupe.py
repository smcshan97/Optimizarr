"""Perceptual fingerprints and duplicate matches."""
from typing import Optional, List, Dict


class DedupeMixin:
    """Database methods: perceptual fingerprints and duplicate matches."""

    # ── Duplicate detection ─────────────────────────────────────────────
    def get_fingerprint(self, file_path: str) -> Optional[Dict]:
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM fingerprints WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_fingerprints(self) -> List[Dict]:
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM fingerprints")
            return [dict(r) for r in cursor.fetchall()]

    def upsert_fingerprint(self, file_path: str, **kwargs):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO fingerprints
                    (file_path, file_size, mtime, quick_hash, frame_hashes,
                     duration, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_size = excluded.file_size,
                    mtime = excluded.mtime,
                    quick_hash = excluded.quick_hash,
                    frame_hashes = excluded.frame_hashes,
                    duration = excluded.duration,
                    error = excluded.error
            """, (
                file_path,
                kwargs.get('file_size', 0),
                kwargs.get('mtime', 0),
                kwargs.get('quick_hash'),
                kwargs.get('frame_hashes'),
                kwargs.get('duration', 0),
                kwargs.get('error'),
            ))

    def delete_fingerprint(self, file_path: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM fingerprints WHERE file_path = ?", (file_path,))
            cursor.execute(
                "DELETE FROM duplicate_matches WHERE file_a = ? OR file_b = ?",
                (file_path, file_path))

    def replace_duplicate_matches(self, matches: List[Dict]):
        """Atomically swap in a fresh set of match results."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM duplicate_matches")
            cursor.executemany("""
                INSERT OR REPLACE INTO duplicate_matches
                    (file_a, file_b, kind, score, offset_seconds, overlap_seconds)
                VALUES (?, ?, ?, ?, ?, ?)
            """, [
                (m['file_a'], m['file_b'], m['kind'], m.get('score', 0),
                 m.get('offset_seconds', 0), m.get('overlap_seconds', 0))
                for m in matches
            ])

    def get_duplicate_matches(self) -> List[Dict]:
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM duplicate_matches
                ORDER BY file_a, score DESC
            """)
            return [dict(r) for r in cursor.fetchall()]

    def delete_queue_items_by_path(self, file_path: str) -> int:
        """Remove all queue rows referencing a file (used after deletion)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM queue WHERE file_path = ?", (file_path,))
            return cursor.rowcount
