"""
Media scanner module for Optimizarr.
Recursively scans directories for video files and analyzes them.
Uses ffprobe as primary media info tool (faster, more reliable) with HandBrake fallback.
"""
import json
import subprocess
import shutil
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import os

from app.database import db


class MediaScanner:
    """Scans directories for video files and analyzes their specifications."""
    
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.ts', '.mpg', '.mpeg', '.wmv', '.flv', '.webm'}
    
    def __init__(self):
        self.handbrake_available = self._check_tool(['HandBrakeCLI', '--version'], "HandBrakeCLI")
        self.ffprobe_available = self._check_tool(['ffprobe', '-version'], "ffprobe")

    def _check_tool(self, cmd: List[str], name: str) -> bool:
        binary = cmd[0]
        if not shutil.which(binary):
            print(f"⚠ {name} not found in PATH")
            return False
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            print(f"✓ {name} is available")
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            print(f"⚠ {name} not available")
            return False
    
    def discover_video_files(self, root_path: str, recursive: bool = True) -> List[str]:
        video_files = []
        root = Path(root_path)
        if not root.exists():
            print(f"✗ Path does not exist: {root_path}")
            return []
        if not root.is_dir():
            print(f"✗ Path is not a directory: {root_path}")
            return []
        try:
            pattern = root.rglob('*') if recursive else root.glob('*')
            for file_path in pattern:
                if file_path.is_file() and file_path.suffix.lower() in self.VIDEO_EXTENSIONS:
                    video_files.append(str(file_path.absolute()))
        except PermissionError as e:
            print(f"✗ Permission denied scanning {root_path}: {e}")
        return sorted(video_files)
    
    def analyze_file(self, file_path: str) -> Optional[Dict]:
        """Analyze a video file. Uses ffprobe (primary) then HandBrake (fallback)."""
        if self.ffprobe_available:
            result = self._probe_with_ffprobe(file_path)
            if result and result.get('codec', 'unknown') != 'unknown':
                return result
        if self.handbrake_available:
            result = self._probe_with_handbrake(file_path)
            if result:
                return result
        return self._get_basic_file_info(file_path)

    def probe_file(self, file_path: str) -> Optional[Dict]:
        """Public alias for analyze_file — re-probe a single file and return its specs dict."""
        return self.analyze_file(file_path)

    def _probe_with_ffprobe(self, file_path: str) -> Optional[Dict]:
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-show_streams', '-show_format', file_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            return self._parse_ffprobe_json(json.loads(result.stdout))
        except subprocess.TimeoutExpired:
            print(f"⚠ ffprobe timeout: {Path(file_path).name}")
        except Exception as e:
            print(f"⚠ ffprobe failed for {Path(file_path).name}: {e}")
        return None

    def _parse_ffprobe_json(self, data: Dict) -> Dict:
        specs = {'codec': 'unknown', 'resolution': 'unknown', 'framerate': 0,
                 'audio_tracks': [], 'duration': 0, 'bit_rate': 0}
        fmt = data.get('format', {})
        try:
            specs['duration'] = float(fmt.get('duration', 0))
            specs['bit_rate'] = int(fmt.get('bit_rate', 0))
        except (ValueError, TypeError):
            pass
        for stream in data.get('streams', []):
            codec_type = stream.get('codec_type', '')
            if codec_type == 'video':
                specs['codec'] = self._normalise_codec(stream.get('codec_name', 'unknown'))
                w, h = stream.get('width', 0), stream.get('height', 0)
                if w and h:
                    specs['resolution'] = f"{w}x{h}"
                for fr_key in ('r_frame_rate', 'avg_frame_rate'):
                    fr_str = stream.get(fr_key, '')
                    if fr_str and fr_str not in ('0/0', '0', ''):
                        try:
                            if '/' in fr_str:
                                num, den = fr_str.split('/')
                                den_int = int(den)
                                if den_int > 0:
                                    fps = round(int(num) / den_int, 3)
                                    if fps > 0:
                                        specs['framerate'] = fps
                                        break
                            else:
                                fps = float(fr_str)
                                if fps > 0:
                                    specs['framerate'] = fps
                                    break
                        except (ValueError, ZeroDivisionError):
                            continue
            elif codec_type == 'audio':
                tags = stream.get('tags', {}) or {}
                specs['audio_tracks'].append({
                    'codec': stream.get('codec_name', 'unknown'),
                    'language': tags.get('language', tags.get('lang', 'und')),
                    'channels': stream.get('channels', 0),
                    'sample_rate': stream.get('sample_rate', ''),
                })
        return specs

    def _probe_with_handbrake(self, file_path: str) -> Optional[Dict]:
        try:
            result = subprocess.run(
                ['HandBrakeCLI', '--scan', '--json', '-i', file_path],
                capture_output=True, text=True, timeout=60
            )
            stderr = result.stderr or ''
            json_match = re.search(r'\{\s*"JSON Title Set"', stderr)
            if not json_match:
                return None
            json_candidate = stderr[json_match.start():]
            data = None
            depth, in_string, escape_next = 0, False, False
            for i, ch in enumerate(json_candidate):
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\' and in_string:
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                if not in_string:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            try:
                                data = json.loads(json_candidate[:i + 1])
                            except json.JSONDecodeError:
                                pass
                            break
            if data:
                title_set = data.get('JSON Title Set', data)
                return self._parse_handbrake_json(title_set)
        except subprocess.TimeoutExpired:
            print(f"⚠ HandBrake scan timeout: {Path(file_path).name}")
        except Exception as e:
            print(f"⚠ HandBrake scan error: {e}")
        return None

    def _parse_handbrake_json(self, data: Dict) -> Dict:
        specs = {'codec': 'unknown', 'resolution': 'unknown', 'framerate': 0, 'audio_tracks': []}
        try:
            title_list = data.get('TitleList', [])
            if not title_list:
                return specs
            title = title_list[0]
            raw_codec = title.get('VideoCodec', '').lower()
            if raw_codec:
                specs['codec'] = self._normalise_codec(raw_codec)
            geo = title.get('Geometry', {})
            if geo:
                w, h = geo.get('Width', 0), geo.get('Height', 0)
                if w and h:
                    specs['resolution'] = f"{w}x{h}"
            fr = title.get('FrameRate', {})
            if isinstance(fr, dict):
                num, den = fr.get('Num', 0), fr.get('Den', 1)
                if den and den > 0:
                    specs['framerate'] = round(num / den, 3)
            elif isinstance(fr, (int, float)):
                specs['framerate'] = float(fr)
            for audio in title.get('AudioList', []):
                specs['audio_tracks'].append({
                    'codec': audio.get('CodecName', 'unknown'),
                    'language': audio.get('Language', 'und'),
                })
        except Exception as e:
            print(f"⚠ Error parsing HandBrake JSON: {e}")
        return specs

    def _normalise_codec(self, raw: str) -> str:
        raw = raw.lower()
        if 'av1' in raw or 'av01' in raw:
            return 'av1'
        if 'hevc' in raw or 'h265' in raw or 'h.265' in raw or 'x265' in raw:
            return 'h265'
        if 'avc' in raw or 'h264' in raw or 'h.264' in raw or 'x264' in raw:
            return 'h264'
        if 'vp9' in raw or 'vp09' in raw:
            return 'vp9'
        if 'vp8' in raw:
            return 'vp8'
        if 'mpeg4' in raw or 'xvid' in raw or 'divx' in raw:
            return 'mpeg4'
        if 'mpeg2' in raw or 'mpeg-2' in raw:
            return 'mpeg2'
        if 'wmv' in raw:
            return 'wmv'
        return raw if raw else 'unknown'

    def _get_basic_file_info(self, file_path: str) -> Dict:
        path = Path(file_path)
        return {
            'codec': 'unknown', 'resolution': 'unknown', 'framerate': 0,
            'audio_tracks': [], 'file_size': path.stat().st_size if path.exists() else 0
        }
    
    def check_file_permissions(self, file_path: str) -> Tuple[str, str]:
        path = Path(file_path)
        if not path.exists():
            return 'not_found', f"File does not exist: {file_path}"
        if not os.access(file_path, os.R_OK):
            return 'no_read', f"No read permission: {file_path}"
        if not os.access(path.parent, os.W_OK):
            return 'no_write', f"No write permission on directory: {path.parent}"
        return 'ok', 'File permissions OK'
    
    def scan_root(self, root_id: int) -> int:
        scan_roots = db.get_scan_roots()
        scan_root = next((r for r in scan_roots if r['id'] == root_id), None)
        if not scan_root:
            print(f"✗ Scan root {root_id} not found")
            return 0
        if not scan_root['enabled']:
            print(f"⚠ Scan root {root_id} is disabled")
            return 0
        print(f"Scanning: {scan_root['path']}")
        profile = db.get_profile(scan_root['profile_id'])
        if not profile:
            print(f"✗ Profile {scan_root['profile_id']} not found")
            return 0
        video_files = self.discover_video_files(scan_root['path'], recursive=scan_root['recursive'])
        print(f"Found {len(video_files)} video files")
        added_count = 0
        for file_path in video_files:
            existing = db.get_queue_items()
            if any(item['file_path'] == file_path for item in existing):
                continue
            perm_status, _ = self.check_file_permissions(file_path)
            current_specs = self.analyze_file(file_path)
            if not current_specs:
                continue
            target_specs = {
                'codec': profile['codec'],
                'resolution': profile['resolution'],
                'framerate': profile['framerate'],
                'audio_codec': profile['audio_codec']
            }
            if not self._needs_encoding(current_specs, target_specs):
                print(f"  ⊙ Skipping (already optimized): {Path(file_path).name}")
                continue
            file_size = Path(file_path).stat().st_size
            savings = self._estimate_savings(file_size, current_specs.get('codec', 'unknown'), target_specs.get('codec', 'unknown'))
            db.add_to_queue(
                file_path=file_path, root_id=root_id, profile_id=scan_root['profile_id'],
                status='pending' if perm_status == 'ok' else 'permission_error',
                current_specs=current_specs, target_specs=target_specs,
                file_size_bytes=file_size, estimated_savings_bytes=savings
            )
            added_count += 1
            print(f"  + Added: {Path(file_path).name} [{current_specs.get('codec','?')} {current_specs.get('resolution','?')}]")
        print(f"✓ Added {added_count} files to queue")
        return added_count

    def _estimate_savings(self, file_size: int, current_codec: str, target_codec: str) -> int:
        if target_codec == 'av1' and current_codec not in ('av1',):
            return int(file_size * 0.50)
        if target_codec == 'h265' and current_codec in ('h264', 'mpeg4', 'mpeg2', 'xvid', 'wmv'):
            return int(file_size * 0.40)
        if target_codec == 'h264' and current_codec in ('mpeg4', 'mpeg2', 'wmv'):
            return int(file_size * 0.30)
        return 0

    def _needs_encoding(self, current_specs: Dict, target_specs: Dict) -> bool:
        current_codec = current_specs.get('codec', 'unknown')
        if current_codec == 'unknown':
            return True
        target_codec = target_specs.get('codec')
        if target_codec and current_codec != target_codec:
            return True
        target_res = target_specs.get('resolution')
        if target_res and target_res not in ('', 'preserve', None):
            current_res = current_specs.get('resolution', 'unknown')
            if current_res != 'unknown' and current_res != target_res:
                return True
        return False
    
    def scan_all_roots(self) -> int:
        scan_roots = db.get_scan_roots(enabled_only=True)
        if not scan_roots:
            print("⚠ No enabled scan roots found")
            return 0
        return sum(self.scan_root(root['id']) for root in scan_roots)


# Global scanner instance
scanner = MediaScanner()
