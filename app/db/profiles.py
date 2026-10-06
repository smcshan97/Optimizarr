"""Encoding profiles, including JSON preset export/import."""
from typing import Optional, List, Dict


class ProfilesMixin:
    """Database methods: encoding profiles, including JSON preset export/import."""

    # Profile CRUD
    def create_profile(self, **kwargs) -> int:
        """Create a new encoding profile. Enforces only one default at a time."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # If this new profile is being set as default, clear any existing default first
            if kwargs.get('is_default', False):
                cursor.execute("UPDATE profiles SET is_default = 0 WHERE is_default = 1")
            cursor.execute("""
                INSERT INTO profiles 
                (name, resolution, framerate, codec, encoder, quality, audio_codec, 
                 container, audio_handling, subtitle_handling, enable_filters,
                 chapter_markers, hw_accel_enabled, preset, two_pass, custom_args, is_default,
                 upscale_enabled, upscale_trigger_below, upscale_target_height,
                 upscale_model, upscale_factor, upscale_key,
                 stereo_enabled, stereo_mode, stereo_format,
                 stereo_divergence, stereo_convergence, stereo_depth_model, film_grain)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('name'),
                kwargs.get('resolution'),
                kwargs.get('framerate'),
                kwargs.get('codec'),
                kwargs.get('encoder'),
                kwargs.get('quality'),
                kwargs.get('audio_codec'),
                kwargs.get('container', 'mkv'),
                kwargs.get('audio_handling', 'preserve_all'),
                kwargs.get('subtitle_handling', 'none'),
                kwargs.get('enable_filters', False),
                kwargs.get('chapter_markers', True),
                kwargs.get('hw_accel_enabled', False),
                kwargs.get('preset'),
                kwargs.get('two_pass', False),
                kwargs.get('custom_args'),
                kwargs.get('is_default', False),
                kwargs.get('upscale_enabled', False),
                kwargs.get('upscale_trigger_below', 720),
                kwargs.get('upscale_target_height', 1080),
                kwargs.get('upscale_model', 'realesrgan-x4plus'),
                kwargs.get('upscale_factor', 2),
                kwargs.get('upscale_key', 'realesrgan'),
                kwargs.get('stereo_enabled', False),
                kwargs.get('stereo_mode', '2d_to_3d'),
                kwargs.get('stereo_format', 'half_sbs'),
                kwargs.get('stereo_divergence', 2.0),
                kwargs.get('stereo_convergence', 0.5),
                kwargs.get('stereo_depth_model', 'Any_V2_S'),
                kwargs.get('film_grain', 0) or 0,
            ))
            return cursor.lastrowid
    
    def update_profile(self, profile_id: int, **kwargs) -> bool:
        """Update an existing encoding profile. Enforces only one default at a time."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # If setting this profile as default, clear all others first
            if kwargs.get('is_default', False):
                cursor.execute("UPDATE profiles SET is_default = 0 WHERE is_default = 1 AND id != ?", (profile_id,))
            
            updates = []
            values = []
            
            allowed_fields = [
                'name', 'resolution', 'framerate', 'codec', 'encoder',
                'quality', 'audio_codec', 'container', 'audio_handling',
                'subtitle_handling', 'enable_filters', 'chapter_markers',
                'hw_accel_enabled', 'preset', 'two_pass', 'custom_args', 'is_default',
                'upscale_enabled', 'upscale_trigger_below', 'upscale_target_height',
                'upscale_model', 'upscale_factor', 'upscale_key',
                'stereo_enabled', 'stereo_mode', 'stereo_format',
                'stereo_divergence', 'stereo_convergence', 'stereo_depth_model',
                'film_grain',
            ]
            
            for field in allowed_fields:
                if field in kwargs:
                    updates.append(f"{field} = ?")
                    values.append(kwargs[field])
            
            if not updates:
                return False
            
            values.append(profile_id)
            query = f"UPDATE profiles SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, values)
            return cursor.rowcount > 0
    
    def get_profiles(self) -> List[Dict]:
        """Get all encoding profiles."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM profiles ORDER BY name")
            return [dict(row) for row in cursor.fetchall()]

    def get_profile(self, profile_id: int) -> Optional[Dict]:
        """Get a specific profile by ID."""
        with self.get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def delete_profile(self, profile_id: int) -> bool:
        """Delete a profile by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
            return cursor.rowcount > 0

    # Preset export/import
    def export_profiles(self) -> List[Dict]:
        """Export all profiles as a list of dicts for JSON export."""
        profiles = self.get_profiles()
        # Strip internal fields
        for p in profiles:
            p.pop('id', None)
            p.pop('created_at', None)
        return profiles
    
    def import_profiles(self, profiles: List[Dict]) -> Dict:
        """Import profiles from a list of dicts. Returns stats."""
        imported = 0
        skipped = 0
        errors = []
        existing_names = {ep['name'] for ep in self.get_profiles()}
        for p in profiles:
            try:
                if p.get('name') in existing_names:
                    skipped += 1
                    continue
                self.create_profile(**p)
                existing_names.add(p.get('name'))
                imported += 1
            except Exception as e:
                errors.append(f"{p.get('name', '?')}: {str(e)}")
        return {'imported': imported, 'skipped': skipped, 'errors': errors}
