"""
Media scanner module for Optimizarr.
Recursively scans directories for video files and analyzes them.
Uses ffprobe as primary media info tool (faster, more reliable) with HandBrake fallback.
"""
import json
import subprocess
import shutil
import re
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import os

from app.database import db
from app.logger import optimizarr_logger
from app.handbrake import handbrake_cli


_PIX_DEPTH_RE = re.compile(r'p(\d{2})(?:le|be)?$')

# Transfer characteristics that mean HDR: PQ (HDR10/HDR10+/DV) and HLG.
HDR_TRANSFERS = {'smpte2084', 'arib-std-b67'}


def video_color_info(stream: Dict) -> Dict:
    """Bit depth, colour tags and HDR flags from one ffprobe video stream."""
    pix_fmt = stream.get('pix_fmt') or ''
    depth = 8
    m = _PIX_DEPTH_RE.search(pix_fmt)
    if m:
        depth = int(m.group(1))
    else:
        try:
            depth = int(stream.get('bits_per_raw_sample') or 8)
        except (TypeError, ValueError):
            depth = 8
    transfer = stream.get('color_transfer') or ''
    dovi = any('DOVI' in (sd.get('side_data_type') or '')
               for sd in stream.get('side_data_list') or [])
    return {
        'pix_fmt': pix_fmt,
        'bit_depth': depth,
        'color_primaries': stream.get('color_primaries') or '',
        'color_transfer': transfer,
        'color_space': stream.get('color_space') or '',
        'color_range': stream.get('color_range') or '',
        'hdr': transfer in HDR_TRANSFERS or dovi,
        'dolby_vision': dovi,
    }


# Default minimum source bits-per-pixel worth re-encoding. Calibrated on this
# project's own encode history (1,421 plain AV1 encodes): below 0.03 bpp the
# majority came out the same size or LARGER; above 0.10 almost none did.
DEFAULT_MIN_SOURCE_BPP = 0.03

# Older codecs compress worse, so a low bitrate still leaves room; HEVC/VP9
# are already efficient, so they need more headroom to be worth it.
_CODEC_BPP_FACTOR = {
    'mpeg2': 0.6, 'mpeg4': 0.6, 'wmv': 0.6, 'vc1': 0.8,
    'h264': 1.0, 'h265': 1.5, 'vp9': 1.5,
}


def source_bpp(specs: Dict) -> Optional[float]:
    """Source bits per pixel per frame (overall bitrate), or None if unknown."""
    try:
        bitrate = float(specs.get('bit_rate') or 0)
        w = float(specs.get('width') or 0)
        h = float(specs.get('height') or 0)
        fps = float(specs.get('framerate') or 0)
    except (TypeError, ValueError):
        return None
    if bitrate <= 0 or w <= 0 or h <= 0 or fps <= 0:
        return None
    return bitrate / (w * h * fps)


def low_bitrate_reason(specs: Dict, min_bpp: float) -> Optional[str]:
    """Why re-encoding this source isn't worth it, or None if it is."""
    if min_bpp <= 0:
        return None
    bpp = source_bpp(specs)
    if bpp is None:
        return None
    threshold = min_bpp * _CODEC_BPP_FACTOR.get(specs.get('codec', ''), 1.0)
    if bpp >= threshold:
        return None
    mbps = float(specs.get('bit_rate') or 0) / 1e6
    return (f"Skipped — source is already efficient ({mbps:.1f} Mbps, "
            f"{bpp:.3f} bits/pixel < {threshold:.3f}); re-encoding it usually "
            f"saves nothing")


def profile_targets(profile: Dict) -> Dict:
    """What a profile asks of a file: codec, resolution and framerate
    ceilings, bitrate ceiling and audio codec. Stored on each queue row as
    target_specs and compared by unmet_targets()."""
    return {
        'codec': profile.get('codec'),
        'resolution': profile.get('resolution') or '',
        'framerate': profile.get('framerate') or 0,
        'max_bitrate_kbps': int(profile.get('max_bitrate_kbps') or 0),
        'audio_codec': profile.get('audio_codec'),
    }


# A file's bitrate is the container's overall figure (video + audio), so a
# source only counts as over a video bitrate cap beyond this headroom.
BITRATE_HEADROOM = 1.10


def unmet_targets(specs: Dict, targets: Dict) -> List[str]:
    """Ways a file falls short of its profile, as short human reasons.

    Empty = the file already meets the profile, so the only question left is
    whether re-encoding saves space. Resolution, framerate and bitrate are
    ceilings: a file at or under them meets them (nothing is upscaled or
    frame-doubled to match). Unknown values never count as unmet.
    """
    reasons = []
    codec = specs.get('codec', 'unknown')
    target_codec = targets.get('codec')
    if target_codec and codec not in ('unknown', '', None) and codec != target_codec:
        reasons.append(f"codec {codec} → {target_codec}")
    target_res = targets.get('resolution')
    if target_res and target_res != 'preserve':
        try:
            tgt_h = int(str(target_res).split('x')[1])
            cur_h = int(specs.get('height') or 0) or int(
                str(specs.get('resolution', '')).split('x')[1])
            if cur_h > tgt_h:
                reasons.append(f"{cur_h}p → {tgt_h}p")
        except (ValueError, IndexError):
            pass
    try:
        tgt_fps = float(targets.get('framerate') or 0)
        cur_fps = float(specs.get('framerate') or 0)
        if tgt_fps and cur_fps > tgt_fps + 0.5:
            reasons.append(f"{cur_fps:g} fps → {tgt_fps:g} fps")
    except (TypeError, ValueError):
        pass
    try:
        cap = int(targets.get('max_bitrate_kbps') or 0)
        rate = float(specs.get('bit_rate') or 0) / 1000
        if cap and rate > cap * BITRATE_HEADROOM:
            reasons.append(f"{rate / 1000:.1f} Mbps → ≤{cap / 1000:g} Mbps")
    except (TypeError, ValueError):
        pass
    return reasons


class MediaScanner:
    """Scans directories for video files and analyzes their specifications."""
    
    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.m4v', '.ts', '.mpg', '.mpeg', '.wmv', '.flv', '.webm'}
    
    def __init__(self):
        self.handbrake_available = self._check_tool([handbrake_cli(), '--version'], "HandBrakeCLI")
        self.ffprobe_available = self._check_tool(['ffprobe', '-version'], "ffprobe")

    def _check_tool(self, cmd: List[str], name: str) -> bool:
        binary = cmd[0]
        if not shutil.which(binary):
            optimizarr_logger.app_logger.warning(f"⚠ {name} not found in PATH")
            return False
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=5,
                           encoding='utf-8', errors='replace')
            optimizarr_logger.app_logger.info(f"✓ {name} is available")
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            optimizarr_logger.app_logger.warning(f"⚠ {name} not available")
            return False
    
    def discover_video_files(self, root_path: str, recursive: bool = True) -> List[str]:
        video_files = []
        root = Path(root_path)
        if not root.exists():
            optimizarr_logger.app_logger.error(f"✗ Path does not exist: {root_path}")
            return []
        if not root.is_dir():
            optimizarr_logger.app_logger.error(f"✗ Path is not a directory: {root_path}")
            return []
        try:
            pattern = root.rglob('*') if recursive else root.glob('*')
            for file_path in pattern:
                if file_path.is_file() and file_path.suffix.lower() in self.VIDEO_EXTENSIONS:
                    video_files.append(str(file_path.absolute()))
        except PermissionError as e:
            optimizarr_logger.app_logger.error(f"✗ Permission denied scanning {root_path}: {e}")
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
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            return self._parse_ffprobe_json(json.loads(result.stdout))
        except subprocess.TimeoutExpired:
            optimizarr_logger.app_logger.warning(f"⚠ ffprobe timeout: {Path(file_path).name}")
        except Exception as e:
            optimizarr_logger.app_logger.warning(f"⚠ ffprobe failed for {Path(file_path).name}: {e}")
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
        seen_video = False
        for stream in data.get('streams', []):
            codec_type = stream.get('codec_type', '')
            if codec_type == 'video':
                # Only the first real video stream counts. Embedded cover art
                # (MKV attachments / MP4 covr) is a 'video' stream too, and
                # used to overwrite the real codec/resolution with a JPEG's.
                if seen_video or (stream.get('disposition') or {}).get('attached_pic'):
                    continue
                seen_video = True
                specs['codec'] = self._normalise_codec(stream.get('codec_name', 'unknown'))
                specs.update(video_color_info(stream))
                w, h = stream.get('width', 0), stream.get('height', 0)
                if w and h:
                    specs['resolution'] = f"{w}x{h}"
                    # Numeric width/height feed the upscale-eligibility check
                    # and the queue's resolution sort (json_extract '$.height')
                    specs['width'] = int(w)
                    specs['height'] = int(h)
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

    def probe_video_color(self, file_path: str) -> Dict:
        """Bit depth / HDR info of the first video stream ({} on failure).

        Cheap single-stream ffprobe used at encode time for queue items whose
        specs came from Sonarr/Radarr (which don't report bit depth/HDR).
        """
        if not self.ffprobe_available:
            return {}
        try:
            result = subprocess.run(
                ['ffprobe', '-v', 'quiet', '-print_format', 'json',
                 '-select_streams', 'V', '-show_streams', file_path],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )
            if result.returncode != 0 or not result.stdout.strip():
                return {}
            for stream in json.loads(result.stdout).get('streams', []):
                if (stream.get('disposition') or {}).get('attached_pic'):
                    continue
                return video_color_info(stream)
        except Exception:
            pass
        return {}

    def _probe_with_handbrake(self, file_path: str) -> Optional[Dict]:
        try:
            result = subprocess.run(
                [handbrake_cli(), '--scan', '--json', '-i', file_path],
                capture_output=True, text=True, timeout=60,
                encoding='utf-8', errors='replace'
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
            optimizarr_logger.app_logger.warning(f"⚠ HandBrake scan timeout: {Path(file_path).name}")
        except Exception as e:
            optimizarr_logger.app_logger.error(f"⚠ HandBrake scan error: {e}")
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
                    specs['width'] = int(w)
                    specs['height'] = int(h)
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
            optimizarr_logger.app_logger.error(f"⚠ Error parsing HandBrake JSON: {e}")
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
            optimizarr_logger.app_logger.error(f"✗ Scan root {root_id} not found")
            return 0
        if not scan_root['enabled']:
            optimizarr_logger.app_logger.warning(f"⚠ Scan root {root_id} is disabled")
            return 0
        optimizarr_logger.app_logger.info(f"Scanning: {scan_root['path']}")
        profile = db.get_profile(scan_root['profile_id'])
        if not profile:
            optimizarr_logger.app_logger.error(f"✗ Profile {scan_root['profile_id']} not found")
            return 0
        # Companion organizer first, so newly queued rows get the final paths.
        try:
            from app.organizer import organize_root
            organized = organize_root(scan_root)
            if organized:
                optimizarr_logger.app_logger.info(
                    f"Organized {organized} video(s) into their own folders")
        except Exception as e:
            optimizarr_logger.app_logger.warning(f"Organizer failed for {scan_root['path']}: {e}")
        video_files = self.discover_video_files(scan_root['path'], recursive=scan_root['recursive'])
        optimizarr_logger.app_logger.info(f"Found {len(video_files)} video files")
        added_count = 0

        # Build a set of already-queued paths ONCE — avoids O(n²) DB round-trips
        existing_paths = db.get_queue_file_paths()

        to_scan = [f for f in video_files if f not in existing_paths]

        # Probe in parallel batches: ffprobe startup + I/O dominates scan time
        # and files are independent. Small batches keep queueing incremental,
        # so encoding can begin while a large library is still scanning.
        from concurrent.futures import ThreadPoolExecutor
        batch_size = 8

        def _probe_batches():
            with ThreadPoolExecutor(max_workers=4) as pool:
                for start in range(0, len(to_scan), batch_size):
                    batch = to_scan[start:start + batch_size]
                    yield from zip(batch, pool.map(self.analyze_file, batch))

        for file_path, current_specs in _probe_batches():
            if not current_specs:
                continue
            # A file with no decodable video stream (corrupt, incomplete, or
            # still downloading) can never encode — queue it quarantined so it
            # doesn't waste an encode slot. Only when a probe tool actually
            # ran; otherwise 'unknown' just means we couldn't look.
            broken = (
                (self.ffprobe_available or self.handbrake_available)
                and current_specs.get('codec', 'unknown') == 'unknown'
                and current_specs.get('resolution', 'unknown') == 'unknown'
            )
            perm_status, _ = self.check_file_permissions(file_path)
            target_specs = profile_targets(profile)
            if not self._needs_encoding(current_specs, target_specs):
                optimizarr_logger.app_logger.info(f"  ⊙ Skipping (already optimized): {Path(file_path).name}")
                continue
            file_size = Path(file_path).stat().st_size

            # ── Upscale plan (scan root settings > profile settings) ─────────
            import json as _json
            upscale_plan = upscale_plan_for(
                upscale_source_for(scan_root, profile), current_specs.get('height', 0))

            # ── Stereo plan ──────────────────────────────────────────────────
            stereo_plan = None
            # Priority: scan_root stereo settings > profile stereo settings
            if scan_root.get('stereo_enabled'):
                stereo_source = scan_root
            elif profile.get('stereo_enabled'):
                stereo_source = profile
            else:
                stereo_source = None

            if stereo_source:
                stereo_plan = _json.dumps({
                    "enabled":     True,
                    "mode":        stereo_source.get('stereo_mode', '2d_to_3d'),
                    "format":      stereo_source.get('stereo_format', 'half_sbs'),
                    "divergence":  stereo_source.get('stereo_divergence', 2.0),
                    "convergence": stereo_source.get('stereo_convergence', 0.5),
                    "depth_model": stereo_source.get('stereo_depth_model', 'Any_V2_S'),
                })

            # ── Savings estimate (AFTER plans so upscale/stereo are factored in) ──
            savings = self._estimate_savings(
                file_size,
                current_specs.get('codec', 'unknown'),
                target_specs.get('codec', 'unknown'),
                current_specs=current_specs,
                profile=profile,
                upscale_plan=upscale_plan,
                stereo_plan=stereo_plan,
            )

            if broken:
                status = 'quarantined'
            elif perm_status == 'ok':
                status = 'pending'
            else:
                status = 'permission_error'
            item_id = db.add_to_queue(
                skip_if_queued=True,
                file_path=file_path, root_id=root_id, profile_id=scan_root['profile_id'],
                status=status,
                current_specs=current_specs, target_specs=target_specs,
                file_size_bytes=file_size, estimated_savings_bytes=savings,
                duration_seconds=current_specs.get('duration', 0),
                upscale_plan=upscale_plan,
                stereo_plan=stereo_plan,
            )
            if item_id is None:  # queued meanwhile by the watcher/a webhook
                continue
            if broken:
                db.update_queue_item(item_id, error_message=(
                    'No decodable video stream found — file is corrupt, '
                    'incomplete, or still downloading'))
            upscale_tag = " 🔼upscale" if upscale_plan else ""
            stereo_tag = " 🎥3D" if stereo_plan else ""
            added_count += 1
            marker = "☣" if broken else "+"
            optimizarr_logger.app_logger.info(f"  {marker} Added: {Path(file_path).name} [{current_specs.get('codec','?')} {current_specs.get('resolution','?')}]{upscale_tag}{stereo_tag}{' (quarantined)' if broken else ''}")
        optimizarr_logger.app_logger.info(f"✓ Added {added_count} files to queue")
        from app.devlog import devlog
        devlog('scan', root=root_id, queued=added_count)
        return added_count

    def _estimate_savings(self, file_size: int, current_codec: str, target_codec: str,
                          current_specs: Dict = None, profile: Dict = None,
                          upscale_plan: str = None, stereo_plan: str = None) -> int:
        """
        Estimate post-encode size change using actual bitrate when available,
        falling back to conservative codec-efficiency ratios.

        Returns positive for savings (file gets smaller) or NEGATIVE for
        size increase (e.g. upscaling 480p→1080p).

        When upscale_plan or stereo_plan are provided, applies resolution
        increase multipliers that can push the estimate negative.
        """
        import json as _json
        if not current_specs:
            current_specs = {}

        # ── Parse plans if provided as JSON strings ──────────────────────
        up_plan = None
        if upscale_plan:
            try:
                up_plan = _json.loads(upscale_plan) if isinstance(upscale_plan, str) else upscale_plan
            except (ValueError, TypeError):
                pass

        st_plan = None
        if stereo_plan:
            try:
                st_plan = _json.loads(stereo_plan) if isinstance(stereo_plan, str) else stereo_plan
            except (ValueError, TypeError):
                pass

        # ── Codec efficiency table: fraction of original bitrate retained ─
        # av1<-h264 is measured on this library: median 0.60x the source
        # over 5,313 encodes (Feb-Oct 2026; whole file, audio included).
        # The other pairs are still textbook guesses.
        EFFICIENCY = {
            ('av1',  'h264'):   0.60,
            ('av1',  'h265'):   0.65,
            ('av1',  'vp9'):    0.75,
            ('av1',  'mpeg4'):  0.35,
            ('av1',  'mpeg2'):  0.25,
            ('av1',  'xvid'):   0.35,
            ('av1',  'wmv'):    0.30,
            ('av1',  'unknown'):0.50,
            ('h265', 'h264'):   0.55,
            ('h265', 'mpeg4'):  0.40,
            ('h265', 'mpeg2'):  0.30,
            ('h265', 'wmv'):    0.35,
            ('h265', 'unknown'):0.55,
            ('h264', 'mpeg4'):  0.60,
            ('h264', 'mpeg2'):  0.50,
            ('h264', 'wmv'):    0.55,
            ('h264', 'unknown'):0.65,
        }

        # Same codec and no upscale/stereo = nothing to estimate
        if current_codec == target_codec and not up_plan and not st_plan:
            return 0
        if target_codec in ('', None, 'preserve') and not up_plan and not st_plan:
            return 0

        ratio = EFFICIENCY.get((target_codec, current_codec))
        if ratio is None:
            # Same generation or unknown pair — start at 1.0 (no codec gain)
            ratio = 1.0

        # ── Quality multiplier: adjust ratio based on CRF vs reference ──
        # Not with a VMAF target: there the quality value is only where the
        # per-file search starts, not what the file is encoded at.
        if (profile and profile.get('quality') is not None
                and not float(profile.get('target_vmaf') or 0)):
            REFERENCE_CRF = {'av1': 30, 'h265': 24, 'h264': 23}
            ref_crf = REFERENCE_CRF.get(target_codec)
            if ref_crf is not None:
                try:
                    quality = float(profile['quality'])
                    slope = 0.03 if target_codec == 'av1' else 0.04
                    quality_mult = 1.0 + (ref_crf - quality) * slope
                    quality_mult = max(0.5, min(quality_mult, 1.5))
                    ratio = ratio * quality_mult
                except (ValueError, TypeError):
                    pass

        # ── Resolution multiplier: scaling changes size proportionally ───
        # The profile resolution is a ceiling: a file above it is scaled
        # down, a file below it stays as it is (AI upscaling is the separate
        # multiplier below), so this can only shrink the estimate.
        if profile and profile.get('resolution') and current_specs.get('resolution'):
            try:
                src_res = current_specs['resolution']
                tgt_res = profile['resolution']
                if tgt_res and tgt_res not in ('', 'preserve') and 'x' in src_res and 'x' in tgt_res:
                    src_pixels = int(src_res.split('x')[0]) * int(src_res.split('x')[1])
                    tgt_pixels = int(tgt_res.split('x')[0]) * int(tgt_res.split('x')[1])
                    if src_pixels > 0:
                        res_mult = min(1.0, tgt_pixels / src_pixels)
                        ratio = ratio * max(0.25, res_mult)
            except (ValueError, IndexError):
                pass

        # ── AI upscale multiplier: upscaling increases resolution ────────
        if up_plan and up_plan.get('enabled'):
            src_h = up_plan.get('source_height', 0)
            tgt_h = up_plan.get('target_height', 0)
            if src_h > 0 and tgt_h > src_h:
                # Pixel count scales quadratically with height (assuming
                # aspect ratio is preserved)
                upscale_mult = (tgt_h / src_h) ** 2
                ratio = ratio * upscale_mult

        # ── Stereo 3D multiplier: SBS/TB doubles the video data ──────────
        if st_plan and st_plan.get('enabled'):
            mode = st_plan.get('mode', '')
            fmt = st_plan.get('format', '')
            if mode == '2d_to_3d':
                # Full SBS/TB = 2× pixels; Half SBS/TB = ~1× pixels but
                # each eye is at half resolution, so output ≈ same size
                if fmt in ('sbs', 'tb'):
                    ratio = ratio * 2.0
                # half_sbs, half_tb: output is same resolution as input
                # but contains two views — roughly same file size

        # Clamp: output can't be less than 10% of input (sanity floor)
        ratio = max(0.10, ratio)

        # ── Use actual bitrate if available ──────────────────────────────
        bitrate_bps   = current_specs.get('bit_rate') or current_specs.get('bitrate', 0)
        duration_secs = current_specs.get('duration', 0)

        if bitrate_bps and duration_secs and bitrate_bps > 0 and duration_secs > 0:
            video_size_bytes = int((bitrate_bps / 8) * duration_secs)
            base_size = min(video_size_bytes, file_size) if file_size > 0 else video_size_bytes
        else:
            base_size = file_size

        if base_size <= 0:
            return 0

        # Savings = base_size × (1 - output_ratio)
        # Positive = file gets smaller, negative = file gets LARGER
        estimated_output = int(base_size * ratio)
        savings = base_size - estimated_output

        # Cap at 90% savings (sanity), but allow unlimited negative (size increase)
        return min(savings, int(base_size * 0.90))

    def _needs_encoding(self, current_specs: Dict, target_specs: Dict) -> bool:
        current_codec = current_specs.get('codec', 'unknown')
        if current_codec == 'unknown':
            return True
        if unmet_targets(current_specs, target_specs):
            return True
        target_codec = target_specs.get('codec')
        if target_codec and current_codec != target_codec:
            return True
        target_res = target_specs.get('resolution')
        if target_res and target_res not in ('', 'preserve', None):
            current_res = current_specs.get('resolution', 'unknown')
            if current_res == 'unknown':
                return True
            # Compare heights rather than exact strings so aspect ratio
            # differences (e.g. 1920x800 vs 1920x1080) don't cause
            # unnecessary re-encodes.  Only queue if the source is
            # meaningfully larger than the target.
            try:
                cur_h = int(current_res.split('x')[1])
                tgt_h = int(target_res.split('x')[1])
                if cur_h > tgt_h:
                    return True  # source taller than target → downscale
            except (ValueError, IndexError):
                # Couldn't parse — fall back to string compare
                if current_res != target_res:
                    return True
        return False
    
    def scan_all_roots(self) -> int:
        scan_roots = db.get_scan_roots(enabled_only=True)
        if not scan_roots:
            optimizarr_logger.app_logger.warning("⚠ No enabled scan roots found")
            return 0
        return sum(self.scan_root(root['id']) for root in scan_roots)


def upscale_source_for(scan_root: Optional[Dict], profile: Optional[Dict]) -> Optional[Dict]:
    """Where upscale settings come from: the scan root when it has upscaling
    on, else the profile when it does, else None."""
    if scan_root and scan_root.get('upscale_enabled'):
        return scan_root
    if profile and profile.get('upscale_enabled'):
        return profile
    return None


def upscale_plan_for(source: Optional[Dict], src_height: int) -> Optional[str]:
    """The upscale plan (JSON) for a source of ``src_height``, or None.

    Only flags sources meaningfully below both the trigger and the target.
    Shared by the scanner, the Sonarr/Radarr paths and the queue check.
    """
    if not source:
        return None
    src_height = src_height or 0
    trigger_below = source.get('upscale_trigger_below', 720)
    target_height = source.get('upscale_target_height', 1080)
    if not (src_height > 0 and src_height < trigger_below
            and src_height < target_height * 0.85):
        return None
    return json.dumps({
        "enabled":       True,
        "upscaler_key":  source.get('upscale_key', 'realesrgan'),
        "model":         source.get('upscale_model', 'realesrgan-x4plus'),
        "factor":        source.get('upscale_factor', 2),
        "source_height": src_height,
        "target_height": target_height,
    })


def probe_declared_video_duration(file_path: str) -> Optional[float]:
    """Duration the VIDEO STREAM itself claims, in seconds, or None.

    Deliberately not the container duration. A container reports the longest
    track it holds, and a subtitle track that runs past the end of the picture
    (routine in WEB-DL mkvs) inflates it by a minute or more. The video
    stream's own duration is a claim about the picture — MKV usually declares
    nothing (None), while MP4/MOV state it exactly.
    """
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'V:0',
             '-show_entries', 'stream=duration', '-of', 'csv=p=0', file_path],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace',
        )
        if result.returncode != 0:
            return None
        value = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ''
        return float(value) if value and value != 'N/A' else None
    except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
        return None


def probe_last_video_pts(file_path: str, start_seconds: float = 0.0,
                         timeout: int = 300) -> Optional[float]:
    """Timestamp of the last video packet at or after ``start_seconds``.

    Demuxes (does not decode) from ``start_seconds`` to the end of the file,
    so it answers "does this file actually contain picture past time T?" for
    a few seconds of I/O rather than a full pass.

    Returns the largest packet timestamp found, 0.0 when the window held no
    video packets at all, or None when the probe could not be run — the
    caller must treat None as "unknown", never as "no content".
    """
    start = max(0.0, start_seconds)
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'V:0',
             '-show_entries', 'packet=pts_time', '-of', 'csv=p=0',
             '-read_intervals', f'{start:.3f}%+#100000000', file_path],
            capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace',
        )
        if result.returncode != 0:
            return None
        last = 0.0
        for line in result.stdout.splitlines():
            line = line.strip().rstrip(',')
            if not line or line == 'N/A':
                continue
            try:
                last = max(last, float(line))
            except ValueError:
                continue
        return last
    except (subprocess.TimeoutExpired, OSError):
        return None


def estimate_encode_seconds(duration_seconds, target_codec, speed_stats,
                            preprocessed: bool = False) -> Optional[float]:
    """Estimate wall-clock encode time for a video.

    duration × speed ratio (encode seconds per video second), using the
    learned median for the target codec, falling back to the overall
    median across all codecs, then to 1.0× realtime when there's no
    history at all. Returns None when the video duration is unknown —
    callers should sort those last rather than guessing.

    ``preprocessed`` selects the (much larger) ratio learned from past AI
    upscale/stereo jobs. Kept for callers that only have a duration to work
    with; anything holding a whole queue item should use
    ``estimate_item_seconds`` instead, which prices the AI phase by output
    pixels rather than runtime.
    """
    if not duration_seconds or duration_seconds <= 0:
        return None
    ratio = None
    if speed_stats:
        if preprocessed:
            ratio = speed_stats.get('preprocessed')
        if ratio is None:
            ratio = (speed_stats.get('by_codec') or {}).get(target_codec)
        if ratio is None:
            ratio = speed_stats.get('overall')
    if ratio is None or ratio <= 0:
        ratio = 1.0
    return duration_seconds * ratio


# Fallback cost of AI upscaling when nothing has been learned yet: seconds of
# GPU time per output megapixel-frame. Real-ESRGAN ncnn on a mid-range card
# runs roughly 1s for a ~1MP output frame. Only the ORDER it produces matters
# for sorting; the learned median from history replaces it after the first
# completed upscale.
DEFAULT_UPSCALE_SECONDS_PER_MPF = 1.0

# Fallback multiplier for stereo-only (no upscale) jobs with no history: depth
# estimation is expensive but nothing like a 4× upscale.
STEREO_FALLBACK_MULTIPLIER = 20.0

# Megapixel-frames per second of video for a "typical" upscale job: 640×480
# source, ×2, 24fps → 24 × (1280·960 / 1e6). Used to convert the OLD
# duration-based preprocessed ratio into a per-megapixel-frame figure, so a
# library that has only ever learned "upscales run at N× realtime" keeps that
# hard-won magnitude while still ranking jobs by the pixels they actually
# push. Superseded as soon as one upscale completes under the new accounting.
REFERENCE_MPF_PER_VIDEO_SECOND = 29.5


def _seconds_per_mpf(speed_stats) -> float:
    """Learned seconds of AI time per output megapixel-frame.

    Prefers the directly measured median. Falls back to rescaling the legacy
    duration-based preprocessed ratio, then to a built-in constant.
    """
    stats = speed_stats or {}
    measured = stats.get('pre_seconds_per_mpf')
    if measured and measured > 0:
        return measured
    ratio = stats.get('preprocessed')
    if ratio and ratio > 0:
        return ratio / REFERENCE_MPF_PER_VIDEO_SECOND
    return DEFAULT_UPSCALE_SECONDS_PER_MPF


def _plan_dict(raw):
    """Parse an upscale/stereo plan (JSON string or dict); {} when absent."""
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}


def upscale_work_mpf(specs, upscale_plan) -> float:
    """Megapixel-frames of AI work an upscale plan implies.

    ``frames × output_megapixels``. This — not runtime — is what an upscaler
    actually charges for: every output pixel goes through the model once. A
    10-minute 4K ×2 job is far more work than a 2-hour 480p ×2 one, which a
    duration-based estimate gets exactly backwards.

    Returns 0.0 when the plan is off or the source dimensions are unknown.
    """
    plan = _plan_dict(upscale_plan)
    if not plan.get('enabled'):
        return 0.0
    specs = _plan_dict(specs)
    width = float(specs.get('width') or 0)
    height = float(specs.get('height') or 0)
    duration = float(specs.get('duration') or 0)
    if width <= 0 or height <= 0 or duration <= 0:
        return 0.0
    # Missing framerate is common on odd containers; 24fps under-counts a
    # 60fps source rather than inventing a wild number.
    fps = float(specs.get('framerate') or 0) or 24.0
    factor = float(plan.get('factor') or 2)
    out_megapixels = (width * factor) * (height * factor) / 1_000_000.0
    return duration * fps * out_megapixels


def estimate_item_seconds(item, speed_stats) -> Optional[float]:
    """Total estimated wall-clock seconds for a queue item, all phases.

    Plain encode: duration × the learned ratio for its target codec.
    Upscale job:  AI time (megapixel-frames × learned seconds-per-MPF) PLUS
                  the HandBrake encode that follows it — the upscale is the
                  dominant term but the encode is not free, and it runs on the
                  UPSCALED frames, so it is scaled by the pixel growth.
    Stereo job:   the learned preprocessed ratio, or a flat multiplier.

    Returns None when the duration is unknown — callers decide where to sort
    those rather than being handed a fabricated number.
    """
    specs = item.get('current_specs')
    if isinstance(specs, str):
        specs = _plan_dict(specs)
    specs = specs if isinstance(specs, dict) else {}

    duration = float(item.get('duration_seconds') or specs.get('duration') or 0)
    if duration <= 0:
        return None

    target = item.get('target_specs')
    if isinstance(target, str):
        target = _plan_dict(target)
    codec = target.get('codec') if isinstance(target, dict) else None

    upscale_plan = _plan_dict(item.get('upscale_plan'))
    stereo_plan = _plan_dict(item.get('stereo_plan'))
    has_upscale = bool(upscale_plan.get('enabled'))
    has_stereo = bool(stereo_plan.get('enabled'))

    if not has_upscale and not has_stereo:
        return estimate_encode_seconds(duration, codec, speed_stats)

    # HandBrake still has to run afterwards, on bigger frames.
    encode_part = estimate_encode_seconds(duration, codec, speed_stats) or 0.0
    factor = float(upscale_plan.get('factor') or 1) if has_upscale else 1.0
    encode_part *= max(1.0, factor ** 2)

    ai_part = 0.0
    if has_upscale:
        work = upscale_work_mpf(specs, upscale_plan)
        if work > 0:
            ai_part = work * _seconds_per_mpf(speed_stats)
        else:
            # No dimensions recorded — fall back to the duration-based
            # preprocessed ratio so the item still outranks plain encodes.
            ratio = (speed_stats or {}).get('preprocessed')
            ai_part = duration * (ratio if ratio and ratio > 0
                                  else STEREO_FALLBACK_MULTIPLIER)
    if has_stereo:
        ratio = (speed_stats or {}).get('preprocessed')
        ai_part += duration * (ratio if ratio and ratio > 0
                               else STEREO_FALLBACK_MULTIPLIER)

    return ai_part + encode_part


def cleanup_orphaned_outputs(exclude_paths=None, min_age_seconds: float = 60.0) -> int:
    """Delete leftover ``*_optimized.*`` temp files across enabled scan roots.

    The encoder writes ``{stem}_optimized{ext}`` then renames it to the final
    name on success, so any surviving ``_optimized`` file is a partial output
    from a killed or crashed encode.

    Files modified within ``min_age_seconds`` are SKIPPED — a live encode's
    temp is seconds old, a genuine orphan is hours/days old. That makes this
    safe to run in the background even while an encode is in progress (an
    in-flight temp won't be deleted out from under HandBrake). ``exclude_paths``
    additionally protects specific known-active temps.

    Returns the number of files removed.
    """
    exclude = {str(Path(p)) for p in (exclude_paths or [])}
    now = time.time()
    removed = 0
    for root in db.get_scan_roots(enabled_only=True):
        base = Path(root['path'])
        if not base.exists():
            continue
        recursive = bool(root.get('recursive', True))
        try:
            pattern = base.rglob('*') if recursive else base.glob('*')
            for f in pattern:
                try:
                    if not (f.is_file()
                            and f.suffix.lower() in MediaScanner.VIDEO_EXTENSIONS
                            and f.stem.endswith('_optimized')
                            and str(f) not in exclude):
                        continue
                    # Skip anything written recently — likely an active temp
                    if now - f.stat().st_mtime < min_age_seconds:
                        continue
                    f.unlink()
                    removed += 1
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            continue
    if removed:
        try:
            from app.logger import optimizarr_logger
            optimizarr_logger.app_logger.info(
                "Cleaned %d orphaned _optimized file(s)", removed)
        except Exception:
            pass
    return removed


import threading as _threading

_backfill_stop = _threading.Event()


def _duration_backfill_loop():
    """Gently ffprobe pending items missing a duration so fastest/slowest-first
    learns real estimates. Light I/O (header reads), paced so it doesn't
    compete with active encoding. Unprobeable files get a -1 sentinel so they
    aren't retried forever."""
    while not _backfill_stop.is_set():
        try:
            items = db.get_pending_without_duration(limit=8)
            if not items:
                _backfill_stop.wait(300)   # nothing to do — recheck in 5 min
                continue
            done = 0
            for it in items:
                if _backfill_stop.is_set():
                    break
                try:
                    specs = scanner.analyze_file(it['file_path'])
                    dur = float((specs or {}).get('duration', 0) or 0)
                except Exception:
                    dur = 0.0
                db.update_queue_item(it['id'], duration_seconds=dur if dur > 0 else -1)
                done += 1
                _backfill_stop.wait(1.5)   # gentle pacing
            if done:
                try:
                    from app.devlog import devlog
                    devlog('dur_backfill', n=done)
                except Exception:
                    pass
            _backfill_stop.wait(5)
        except Exception:
            _backfill_stop.wait(60)


def start_duration_backfill():
    """Launch the background duration-backfill worker (idempotent-ish)."""
    _backfill_stop.clear()
    _threading.Thread(target=_duration_backfill_loop, daemon=True,
                      name="DurationBackfill").start()


# Global scanner instance
scanner = MediaScanner()
