"""The encode queue."""
import json
from typing import Optional, List, Dict

from app.db.util import _safe_json


class QueueMixin:
    """Database methods: the encode queue."""

    # Queue CRUD
    def add_to_queue(self, skip_if_queued: bool = False, **kwargs) -> Optional[int]:
        """Add a file to the encoding queue.

        Priority is a rank: 1 = first to encode. When not given, the item is
        appended at the end of the queue (MAX(priority) + 1).

        ``skip_if_queued``: check-and-insert atomically (under the write
        lock) and return None if the path is already queued. A separate
        "is it queued?" read followed by an insert let two near-simultaneous
        events (webhook retry + folder watcher) queue the same file twice.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if skip_if_queued:
                cursor.execute("SELECT 1 FROM queue WHERE file_path = ? LIMIT 1",
                               (kwargs.get('file_path'),))
                if cursor.fetchone():
                    return None
            priority = kwargs.get('priority')
            if priority is None:
                cursor.execute("SELECT COALESCE(MAX(priority), 0) + 1 FROM queue")
                priority = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO queue
                (file_path, root_id, profile_id, status, priority, current_specs,
                 target_specs, file_size_bytes, estimated_savings_bytes, upscale_plan,
                 stereo_plan, duration_seconds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('file_path'),
                kwargs.get('root_id'),
                kwargs.get('profile_id'),
                kwargs.get('status', 'pending'),
                priority,
                json.dumps(kwargs.get('current_specs', {})),
                json.dumps(kwargs.get('target_specs', {})),
                kwargs.get('file_size_bytes', 0),
                kwargs.get('estimated_savings_bytes', 0),
                kwargs.get('upscale_plan'),
                kwargs.get('stereo_plan'),
                kwargs.get('duration_seconds', 0),
            ))
            return cursor.lastrowid

    def get_next_pending_item(self, exclude_ids=None) -> Optional[Dict]:
        """The next pending item eligible to encode (rank 1 first).

        Skips items still in retry backoff (retry_after in the future) and
        fetches only ONE row — the encoder no longer loads the whole pending
        backlog just to pick the head. ``exclude_ids`` omits items already
        in flight (so a parallel controller doesn't dispatch the same row
        twice before its job has flipped it to 'processing').
        """
        exclude_ids = [int(i) for i in (exclude_ids or [])]
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            sql = ("SELECT * FROM queue WHERE status = 'pending' "
                   "AND (retry_after IS NULL OR retry_after <= CURRENT_TIMESTAMP)")
            params = []
            if exclude_ids:
                placeholders = ",".join("?" * len(exclude_ids))
                sql += f" AND id NOT IN ({placeholders})"
                params.extend(exclude_ids)
            sql += " ORDER BY priority ASC, created_at LIMIT 1"
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item['current_specs'] = _safe_json(item.get('current_specs'))
            item['target_specs'] = _safe_json(item.get('target_specs'))
            return item

    def count_pending(self) -> int:
        """Total pending items (any backoff state). Lets the encoder tell
        'queue empty' apart from 'everything is waiting on backoff'."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'")
            return cursor.fetchone()[0]

    def get_pending_without_duration(self, limit: int = 8) -> List[Dict]:
        """Pending items whose duration was never probed (duration_seconds = 0).

        Feeds the background backfill so fastest/slowest-first learns real
        encode-time estimates. A -1 sentinel (probed, no usable duration) is
        excluded so dead/corrupt files aren't re-probed forever.
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, file_path FROM queue "
                "WHERE status = 'pending' AND duration_seconds = 0 "
                "ORDER BY priority ASC LIMIT ?", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_queue_items(self, status: Optional[str] = None) -> List[Dict]:
        """Get queue items, optionally filtered by status.

        Ordered by priority rank ascending (1 = first to encode).
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("SELECT * FROM queue WHERE status = ? ORDER BY priority ASC, created_at", (status,))
            else:
                cursor.execute("SELECT * FROM queue ORDER BY priority ASC, created_at")
            
            items = []
            for row in cursor.fetchall():
                item = dict(row)
                # Parse JSON fields (corruption-tolerant — see _safe_json)
                item['current_specs'] = _safe_json(item.get('current_specs'))
                item['target_specs'] = _safe_json(item.get('target_specs'))
                items.append(item)
            return items

    def get_queue_items_paginated(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        sort_field: str = 'priority',
        sort_dir: str = 'asc',
        page: int = 1,
        page_size: int = 50,
    ) -> Dict:
        """Get queue items with server-side filtering, sorting, and pagination.

        Returns dict with keys: items, total, page, page_size, total_pages, counts.
        ``counts`` gives per-status totals across the ENTIRE queue (unfiltered)
        so the summary bar can display accurate totals regardless of page/filter.
        """
        # Whitelist sortable columns to prevent SQL injection.
        # codec/resolution live inside the current_specs JSON blob; resolution
        # sorts by height so 480p < 720p < 1080p < 2160p.
        allowed_sorts = {
            'file': 'file_path',
            'file_path': 'file_path',
            'status': 'status',
            'priority': 'priority',
            'progress': 'progress',
            'size': 'file_size_bytes',
            'file_size_bytes': 'file_size_bytes',
            'savings': 'estimated_savings_bytes',
            'estimated_savings_bytes': 'estimated_savings_bytes',
            'created_at': 'created_at',
            'codec': "json_extract(current_specs, '$.codec')",
            'resolution': "CAST(json_extract(current_specs, '$.height') AS INTEGER)",
            'duration': 'duration_seconds',
            'duration_seconds': 'duration_seconds',
        }
        db_sort_col = allowed_sorts.get(sort_field, 'priority')
        db_sort_dir = 'ASC' if sort_dir.lower() == 'asc' else 'DESC'
        # Secondary sort for deterministic ordering
        secondary = ', created_at ASC' if db_sort_col != 'created_at' else ''

        with self.get_read_connection() as conn:
            cursor = conn.cursor()

            # ── Total counts per status (unfiltered) ──
            cursor.execute(
                "SELECT status, COUNT(*) as cnt FROM queue GROUP BY status"
            )
            counts = {row['status']: row['cnt'] for row in cursor.fetchall()}

            # ── Build WHERE clause ──
            conditions = []
            params = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            if search:
                conditions.append("file_path LIKE ?")
                params.append(f"%{search}%")

            where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            # ── Count matching rows ──
            cursor.execute(f"SELECT COUNT(*) FROM queue {where}", params)
            total = cursor.fetchone()[0]

            # ── Fetch page ──
            offset = (max(1, page) - 1) * page_size
            cursor.execute(
                f"SELECT * FROM queue {where} "
                f"ORDER BY {db_sort_col} {db_sort_dir}{secondary} "
                f"LIMIT ? OFFSET ?",
                params + [page_size, offset]
            )

            items = []
            for row in cursor.fetchall():
                item = dict(row)
                item['current_specs'] = _safe_json(item.get('current_specs'))
                item['target_specs'] = _safe_json(item.get('target_specs'))
                items.append(item)

            total_pages = max(1, -(-total // page_size))  # ceil division

            return {
                'items': items,
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': total_pages,
                'counts': counts,
            }

    def is_queued(self, file_path: str) -> bool:
        """True if ``file_path`` has a queue row (any status). Indexed."""
        with self.get_read_connection() as conn:
            row = conn.execute("SELECT 1 FROM queue WHERE file_path = ? LIMIT 1",
                               (file_path,)).fetchone()
            return row is not None

    def get_queue_file_paths(self) -> set:
        """Every file_path currently in the queue (any status).

        Dedupe helper for scan/watch/webhook/sync — replaces loading and
        JSON-parsing the entire queue just to build a path set.
        """
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT file_path FROM queue")
            return {row[0] for row in cursor.fetchall()}

    def rename_queue_path(self, old_path: str, new_path: str) -> int:
        """Point every row for ``old_path`` at ``new_path`` (the file was
        moved, e.g. by the companion organizer). Returns rows changed."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE queue SET file_path = ? WHERE file_path = ?",
                           (new_path, old_path))
            return cursor.rowcount

    def set_queue_priorities(self, pairs) -> int:
        """Batch-write (item_id, priority) pairs in ONE transaction.

        A full-queue reprioritize used to issue one commit per row — thousands
        of fsyncs under the write lock. Returns the number of rows written.
        """
        pairs = [(int(p), int(i)) for i, p in pairs]
        if not pairs:
            return 0
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "UPDATE queue SET priority = ? WHERE id = ?", pairs)
            return len(pairs)

    def get_queue_item(self, item_id: int) -> Optional[Dict]:
        """Get a single queue item by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM queue WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            item['current_specs'] = _safe_json(item.get('current_specs'))
            item['target_specs'] = _safe_json(item.get('target_specs'))
            return item
    
    def update_queue_item(self, item_id: int, **kwargs):
        """Update a queue item."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Build update query dynamically
            fields = []
            values = []
            for key, value in kwargs.items():
                if key in ['current_specs', 'target_specs'] and isinstance(value, dict):
                    value = json.dumps(value)
                fields.append(f"{key} = ?")
                values.append(value)
            
            if fields:
                values.append(item_id)
                query = f"UPDATE queue SET {', '.join(fields)} WHERE id = ?"
                cursor.execute(query, values)
    
    def delete_queue_item(self, item_id: int) -> bool:
        """Delete a queue item by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM queue WHERE id = ?", (item_id,))
            return cursor.rowcount > 0
    
    def clear_queue(self, status: Optional[str] = None):
        """Clear queue items, optionally filtered by status."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute("DELETE FROM queue WHERE status = ?", (status,))
            else:
                cursor.execute("DELETE FROM queue")

    def reset_stale_processing(self) -> int:
        """Reset leftover 'processing'/'paused' items to 'pending' on startup.

        After an unclean shutdown (crash, kill, Ctrl+C, power loss) the encode
        process is gone but the DB row may still read 'processing' or 'paused'.
        No encode can actually be running at startup, so re-queue these so they
        are picked up again. Does NOT touch retry_count (this isn't a failure).
        Returns the number of rows reset.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE queue SET status = 'pending', progress = 0.0, "
                "phase = NULL, phase_progress = 0.0, phase_eta_seconds = 0, "
                "current_fps = 0, eta_seconds = 0, "
                "paused_reason = NULL WHERE status IN ('processing', 'paused')"
            )
            return cursor.rowcount
