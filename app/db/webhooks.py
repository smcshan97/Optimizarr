"""Incoming Sonarr/Radarr webhook dead-letter queue."""
from typing import Optional, List, Dict


class WebhooksMixin:
    """Database methods: incoming Sonarr/Radarr webhook dead-letter queue."""

    # Incoming webhook dead-letter queue
    def add_webhook_event(self, app_type: str, payload_json: str) -> int:
        """Persist an incoming webhook BEFORE processing it."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO webhook_events (app_type, payload_json) VALUES (?, ?)",
                (app_type, payload_json)
            )
            return cursor.lastrowid

    def mark_webhook_processed(self, event_id: int, error: Optional[str] = None):
        """Mark an event as done. ``error`` records a deterministic non-ok
        outcome (bad payload, no profile) — still processed, won't replay."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE webhook_events SET processed_at = CURRENT_TIMESTAMP, "
                "error = ? WHERE id = ?",
                (error, event_id)
            )

    def set_webhook_error(self, event_id: int, error: str):
        """Record a processing exception WITHOUT marking processed — the
        event stays eligible for replay at next startup."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE webhook_events SET error = ? WHERE id = ?",
                (error, event_id)
            )

    def get_unprocessed_webhooks(self, hours: int = 24) -> List[Dict]:
        """Events never successfully processed, received in the last N hours."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM webhook_events "
                "WHERE processed_at IS NULL "
                "AND received_at >= datetime('now', ?) "
                "ORDER BY received_at",
                (f'-{int(hours)} hours',)
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_unprocessed_webhooks(self) -> int:
        """Unprocessed event count (any age) — surfaced on /health."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM webhook_events WHERE processed_at IS NULL"
            )
            return cursor.fetchone()[0]

    def prune_webhook_events(self, days: int = 7) -> int:
        """Delete processed events older than N days. Unprocessed rows are
        kept regardless of age so failures stay visible on /health."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM webhook_events "
                "WHERE processed_at IS NOT NULL "
                "AND received_at < datetime('now', ?)",
                (f'-{int(days)} days',)
            )
            return cursor.rowcount
