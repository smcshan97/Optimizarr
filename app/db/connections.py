"""Sonarr / Radarr / Stash connections."""
from typing import Optional, List, Dict


class ConnectionsMixin:
    """Database methods: sonarr / Radarr / Stash connections."""

    # External Connection CRUD

    def create_external_connection(self, **kwargs) -> int:
        """Create a new external connection. API key must already be encrypted."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO external_connections
                    (name, app_type, base_url, api_key_encrypted, enabled,
                     show_in_stats, sync_interval_hours)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs["name"],
                kwargs["app_type"],
                kwargs["base_url"].rstrip("/"),
                kwargs["api_key_encrypted"],
                kwargs.get("enabled", True),
                kwargs.get("show_in_stats", True),
                kwargs.get("sync_interval_hours", 0),
            ))
            return cursor.lastrowid

    def get_external_connections(self) -> List[Dict]:
        """Get all external connections."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM external_connections ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def get_external_connection(self, conn_id: int) -> Optional[Dict]:
        """Get a single external connection by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM external_connections WHERE id = ?", (conn_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_external_connection(self, conn_id: int, **kwargs) -> bool:
        """Update an external connection."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            allowed = [
                "name", "app_type", "base_url", "api_key_encrypted",
                "enabled", "show_in_stats", "last_tested", "last_synced",
                "sync_interval_hours",
            ]
            updates, values = [], []
            for field in allowed:
                if field in kwargs:
                    val = kwargs[field]
                    if field == "base_url" and val:
                        val = val.rstrip("/")
                    updates.append(f"{field} = ?")
                    values.append(val)
            if not updates:
                return False
            values.append(conn_id)
            cursor.execute(
                f"UPDATE external_connections SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            return cursor.rowcount > 0

    def delete_external_connection(self, conn_id: int) -> bool:
        """Delete an external connection and unlink any scan roots pointing to it."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Unlink scan roots first
            cursor.execute(
                "UPDATE scan_roots SET external_connection_id = NULL "
                "WHERE external_connection_id = ?",
                (conn_id,),
            )
            cursor.execute("DELETE FROM external_connections WHERE id = ?", (conn_id,))
            return cursor.rowcount > 0
