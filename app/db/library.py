"""Scan roots (libraries) and their folder watches."""
from typing import Optional, List, Dict


class LibraryMixin:
    """Database methods: scan roots (libraries) and their folder watches."""

    # Scan Root CRUD
    def create_scan_root(self, path: str, profile_id: int, library_type: str = "custom",
                        enabled: bool = True, recursive: bool = True, show_in_stats: bool = True,
                        upscale_enabled: bool = False, upscale_trigger_below: int = 720,
                        upscale_target_height: int = 1080, upscale_model: str = "realesrgan-x4plus",
                        upscale_factor: int = 2, upscale_key: str = "realesrgan",
                        stereo_enabled: bool = False, stereo_mode: str = "2d_to_3d",
                        stereo_format: str = "half_sbs", stereo_divergence: float = 2.0,
                        stereo_convergence: float = 0.5, stereo_depth_model: str = "Any_V2_S") -> int:
        """Create a new scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO scan_roots
                    (path, profile_id, library_type, enabled, recursive, show_in_stats,
                     upscale_enabled, upscale_trigger_below, upscale_target_height,
                     upscale_model, upscale_factor, upscale_key,
                     stereo_enabled, stereo_mode, stereo_format,
                     stereo_divergence, stereo_convergence, stereo_depth_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (path, profile_id, library_type, enabled, recursive, show_in_stats,
                  upscale_enabled, upscale_trigger_below, upscale_target_height,
                  upscale_model, upscale_factor, upscale_key,
                  stereo_enabled, stereo_mode, stereo_format,
                  stereo_divergence, stereo_convergence, stereo_depth_model))
            return cursor.lastrowid
    
    # Joins the linked folder watch so callers see watch_enabled / watch_id.
    _SCAN_ROOT_SELECT = """
        SELECT sr.*,
               fw.id AS watch_id,
               CASE WHEN fw.id IS NOT NULL AND fw.enabled = 1 THEN 1 ELSE 0 END AS watch_enabled
        FROM scan_roots sr
        LEFT JOIN folder_watches fw ON fw.scan_root_id = sr.id
    """

    def get_scan_roots(self, enabled_only: bool = False) -> List[Dict]:
        """Get all scan roots (including linked folder-watch state)."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            query = self._SCAN_ROOT_SELECT
            if enabled_only:
                query += " WHERE sr.enabled = 1"
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def get_scan_root(self, root_id: int) -> Optional[Dict]:
        """Get a specific scan root by ID (including linked folder-watch state)."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(self._SCAN_ROOT_SELECT + " WHERE sr.id = ?", (root_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_scan_root(self, root_id: int, path: str = None, profile_id: int = None,
                        library_type: str = None, enabled: bool = None, recursive: bool = None,
                        show_in_stats: bool = None, upscale_enabled: bool = None,
                        upscale_trigger_below: int = None, upscale_target_height: int = None,
                        upscale_model: str = None, upscale_factor: int = None,
                        upscale_key: str = None, stereo_enabled: bool = None,
                        stereo_mode: str = None, stereo_format: str = None,
                        stereo_divergence: float = None, stereo_convergence: float = None,
                        stereo_depth_model: str = None) -> bool:
        """Update a scan root."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            values = []

            field_map = {
                "path":                   path,
                "profile_id":             profile_id,
                "library_type":           library_type,
                "enabled":                enabled,
                "recursive":              recursive,
                "show_in_stats":          show_in_stats,
                "upscale_enabled":        upscale_enabled,
                "upscale_trigger_below":  upscale_trigger_below,
                "upscale_target_height":  upscale_target_height,
                "upscale_model":          upscale_model,
                "upscale_factor":         upscale_factor,
                "upscale_key":            upscale_key,
                "stereo_enabled":         stereo_enabled,
                "stereo_mode":            stereo_mode,
                "stereo_format":          stereo_format,
                "stereo_divergence":      stereo_divergence,
                "stereo_convergence":     stereo_convergence,
                "stereo_depth_model":     stereo_depth_model,
            }
            for col, val in field_map.items():
                if val is not None:
                    updates.append(f"{col} = ?")
                    values.append(val)

            if not updates:
                return False

            values.append(root_id)
            cursor.execute(
                f"UPDATE scan_roots SET {', '.join(updates)} WHERE id = ?", values
            )
            return cursor.rowcount > 0
    
    def delete_scan_root(self, root_id: int) -> bool:
        """Delete a scan root by ID. Also removes any linked folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM folder_watches WHERE scan_root_id = ?", (root_id,))
            cursor.execute("DELETE FROM scan_roots WHERE id = ?", (root_id,))
            return cursor.rowcount > 0

    # Folder Watch CRUD
    def create_folder_watch(self, path: str, profile_id: int, **kwargs) -> int:
        """Create a new folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO folder_watches (path, profile_id, enabled, recursive, auto_queue, extensions, scan_root_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                path, profile_id,
                kwargs.get('enabled', True),
                kwargs.get('recursive', True),
                kwargs.get('auto_queue', True),
                kwargs.get('extensions', '.mkv,.mp4,.avi,.mov,.wmv,.flv,.webm,.m4v,.ts,.mpg,.mpeg'),
                kwargs.get('scan_root_id'),
            ))
            return cursor.lastrowid

    def get_folder_watch_by_scan_root(self, scan_root_id: int) -> Optional[Dict]:
        """Get the folder watch linked to a scan root, if any."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM folder_watches WHERE scan_root_id = ? LIMIT 1",
                (scan_root_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_folder_watches(self, enabled_only: bool = False) -> List[Dict]:
        """Get all folder watches."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            if enabled_only:
                cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id WHERE fw.enabled = 1")
            else:
                cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id")
            return [dict(row) for row in cursor.fetchall()]
    
    def get_folder_watch(self, watch_id: int) -> Optional[Dict]:
        """Get a folder watch by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT fw.*, p.name as profile_name FROM folder_watches fw LEFT JOIN profiles p ON fw.profile_id = p.id WHERE fw.id = ?", (watch_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_folder_watch(self, watch_id: int, **kwargs) -> bool:
        """Update a folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            updates = []
            values = []
            for field in ['path', 'profile_id', 'enabled', 'recursive', 'auto_queue', 'extensions', 'last_check']:
                if field in kwargs:
                    updates.append(f"{field} = ?")
                    values.append(kwargs[field])
            if not updates:
                return False
            values.append(watch_id)
            cursor.execute(f"UPDATE folder_watches SET {', '.join(updates)} WHERE id = ?", values)
            return cursor.rowcount > 0
    
    def delete_folder_watch(self, watch_id: int) -> bool:
        """Delete a folder watch."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM folder_watches WHERE id = ?", (watch_id,))
            return cursor.rowcount > 0
