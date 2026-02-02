"""
Media scanner module for Optimizarr.
Recursively scans directories for video files and analyzes them with HandBrakeCLI.
"""
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import os

from app.database import db


class MediaScanner:
    """Scans directories for video files and analyzes their specifications."""
    
    # Supported video file extensions
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.ts', '.mpg', '.mpeg', '.wmv', '.flv', '.webm'}
    
    def __init__(self):
        self.handbrake_available = self._check_handbrake()
    
    def _check_handbrake(self) -> bool:
        """Check if HandBrakeCLI is available."""
        try:
            result = subprocess.run(
                ['HandBrakeCLI', '--version'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                print("✓ HandBrakeCLI is available")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("⚠ HandBrakeCLI not found - scanning will be limited")
        
        return False
    
    def discover_video_files(self, root_path: str, recursive: bool = True) -> List[str]:
        """
        Discover video files in a directory.
        
        Args:
            root_path: Directory to scan
            recursive: Whether to scan subdirectories
            
        Returns:
            List of absolute file paths
        """
        video_files = []
        root = Path(root_path)
        
        if not root.exists():
            print(f"✗ Path does not exist: {root_path}")
            return []
        
        if not root.is_dir():
            print(f"✗ Path is not a directory: {root_path}")
            return []
        
        try:
            if recursive:
                # Recursively find all video files
                for file_path in root.rglob('*'):
                    if file_path.is_file() and file_path.suffix.lower() in self.VIDEO_EXTENSIONS:
                        video_files.append(str(file_path.absolute()))
            else:
                # Only scan top-level directory
                for file_path in root.glob('*'):
                    if file_path.is_file() and file_path.suffix.lower() in self.VIDEO_EXTENSIONS:
                        video_files.append(str(file_path.absolute()))
        except PermissionError as e:
            print(f"✗ Permission denied scanning {root_path}: {e}")
        
        return sorted(video_files)
    
    def analyze_file(self, file_path: str) -> Optional[Dict]:
        """
        Analyze a video file using HandBrakeCLI.
        
        Args:
            file_path: Path to video file
            
        Returns:
            Dictionary with video specs or None if analysis fails
        """
        if not self.handbrake_available:
            # Return basic info without HandBrake
            return self._get_basic_file_info(file_path)
        
        try:
            # Run HandBrakeCLI with --scan --json
            result = subprocess.run(
                ['HandBrakeCLI', '--scan', '--json', '-i', file_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            # HandBrake outputs JSON to stderr
            if result.stderr:
                # Find JSON in output
                lines = result.stderr.split('\n')
                json_str = None
                
                for i, line in enumerate(lines):
                    if '"JSON Title Set"' in line or line.strip().startswith('{'):
                        # Found JSON start, capture until end
                        json_lines = []
                        for j in range(i, len(lines)):
                            json_lines.append(lines[j])
                            if lines[j].strip() == '}':
                                break
                        json_str = '\n'.join(json_lines)
                        break
                
                if json_str:
                    try:
                        data = json.loads(json_str)
                        return self._parse_handbrake_json(data)
                    except json.JSONDecodeError:
                        pass
            
            # Fallback to basic info
            return self._get_basic_file_info(file_path)
            
        except subprocess.TimeoutExpired:
            print(f"⚠ Timeout analyzing {file_path}")
            return self._get_basic_file_info(file_path)
        except Exception as e:
            print(f"✗ Error analyzing {file_path}: {e}")
            return None
    
    def _parse_handbrake_json(self, data: Dict) -> Dict:
        """Parse HandBrake JSON output to extract video specs."""
        specs = {
            'codec': 'unknown',
            'resolution': 'unknown',
            'framerate': 0,
            'audio_tracks': []
        }
        
        try:
            if 'TitleList' in data and len(data['TitleList']) > 0:
                title = data['TitleList'][0]
                
                # Video codec
                if 'VideoCodec' in title:
                    codec = title['VideoCodec'].lower()
                    if 'av1' in codec or 'av01' in codec:
                        specs['codec'] = 'av1'
                    elif 'hevc' in codec or 'h265' in codec or 'h.265' in codec:
                        specs['codec'] = 'h265'
                    elif 'avc' in codec or 'h264' in codec or 'h.264' in codec:
                        specs['codec'] = 'h264'
                    else:
                        specs['codec'] = codec
                
                # Resolution
                if 'Geometry' in title:
                    width = title['Geometry'].get('Width', 0)
                    height = title['Geometry'].get('Height', 0)
                    specs['resolution'] = f"{width}x{height}"
                
                # Framerate
                if 'FrameRate' in title:
                    specs['framerate'] = title['FrameRate']
                
                # Audio tracks
                if 'AudioList' in title:
                    for audio in title['AudioList']:
                        specs['audio_tracks'].append({
                            'codec': audio.get('CodecName', 'unknown'),
                            'language': audio.get('Language', 'unknown')
                        })
        except Exception as e:
            print(f"⚠ Error parsing HandBrake JSON: {e}")
        
        return specs
    
    def _get_basic_file_info(self, file_path: str) -> Dict:
        """Get basic file information without HandBrake."""
        path = Path(file_path)
        
        return {
            'codec': 'unknown',
            'resolution': 'unknown',
            'framerate': 0,
            'audio_tracks': [],
            'file_size': path.stat().st_size if path.exists() else 0
        }
    
    def check_file_permissions(self, file_path: str) -> Tuple[str, str]:
        """
        Check if file can be read and written.
        
        Returns:
            Tuple of (status, message)
            status: 'ok', 'no_read', 'no_write', 'not_found'
        """
        path = Path(file_path)
        
        if not path.exists():
            return 'not_found', f"File does not exist: {file_path}"
        
        if not os.access(file_path, os.R_OK):
            return 'no_read', f"No read permission: {file_path}"
        
        # Check write permission on parent directory
        if not os.access(path.parent, os.W_OK):
            return 'no_write', f"No write permission on directory: {path.parent}"
        
        return 'ok', 'File permissions OK'
    
    def scan_root(self, root_id: int) -> int:
        """
        Scan a configured scan root and add files to queue.
        
        Args:
            root_id: ID of scan root to scan
            
        Returns:
            Number of files added to queue
        """
        scan_roots = db.get_scan_roots()
        scan_root = next((r for r in scan_roots if r['id'] == root_id), None)
        
        if not scan_root:
            print(f"✗ Scan root {root_id} not found")
            return 0
        
        if not scan_root['enabled']:
            print(f"⚠ Scan root {root_id} is disabled")
            return 0
        
        print(f"Scanning: {scan_root['path']}")
        
        # Get profile
        profile = db.get_profile(scan_root['profile_id'])
        if not profile:
            print(f"✗ Profile {scan_root['profile_id']} not found")
            return 0
        
        # Discover video files
        video_files = self.discover_video_files(
            scan_root['path'],
            recursive=scan_root['recursive']
        )
        
        print(f"Found {len(video_files)} video files")
        
        # Analyze and queue files
        added_count = 0
        
        for file_path in video_files:
            # Check if already in queue
            existing = db.get_queue_items()
            if any(item['file_path'] == file_path for item in existing):
                continue
            
            # Check permissions
            perm_status, perm_message = self.check_file_permissions(file_path)
            
            # Analyze file
            current_specs = self.analyze_file(file_path)
            
            if not current_specs:
                continue
            
            # Build target specs from profile
            target_specs = {
                'codec': profile['codec'],
                'resolution': profile['resolution'],
                'framerate': profile['framerate'],
                'audio_codec': profile['audio_codec']
            }
            
            # Check if file needs encoding
            needs_encoding = self._needs_encoding(current_specs, target_specs)
            
            if not needs_encoding:
                print(f"  ⊙ Skipping (already optimized): {Path(file_path).name}")
                continue
            
            # Estimate savings (rough estimate: AV1 = 50% of H264, H265 = 60%)
            file_size = Path(file_path).stat().st_size
            savings = 0
            
            if target_specs['codec'] == 'av1' and current_specs['codec'] != 'av1':
                savings = int(file_size * 0.5)
            elif target_specs['codec'] == 'h265' and current_specs['codec'] == 'h264':
                savings = int(file_size * 0.4)
            
            # Add to queue
            db.add_to_queue(
                file_path=file_path,
                root_id=root_id,
                profile_id=scan_root['profile_id'],
                status='pending' if perm_status == 'ok' else 'permission_error',
                current_specs=current_specs,
                target_specs=target_specs,
                file_size_bytes=file_size,
                estimated_savings_bytes=savings
            )
            
            added_count += 1
            print(f"  + Added to queue: {Path(file_path).name}")
        
        print(f"✓ Added {added_count} files to queue")
        return added_count
    
    def _needs_encoding(self, current_specs: Dict, target_specs: Dict) -> bool:
        """
        Determine if a file needs encoding based on current vs target specs.
        """
        # Check codec
        if current_specs.get('codec') != target_specs.get('codec'):
            return True
        
        # Check resolution if specified
        if target_specs.get('resolution') and current_specs.get('resolution') != target_specs.get('resolution'):
            return True
        
        # File matches target specs
        return False
    
    def scan_all_roots(self) -> int:
        """
        Scan all enabled scan roots.
        
        Returns:
            Total number of files added to queue
        """
        scan_roots = db.get_scan_roots(enabled_only=True)
        
        if not scan_roots:
            print("⚠ No enabled scan roots found")
            return 0
        
        total_added = 0
        
        for root in scan_roots:
            added = self.scan_root(root['id'])
            total_added += added
        
        return total_added


# Global scanner instance
scanner = MediaScanner()
