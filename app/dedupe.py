"""
Duplicate detection for Optimizarr.

Three-tier detection, all open-source / no extra dependencies:

  1. Exact duplicates — file size + a sampled BLAKE2b "quick hash"
     (start / third / two-thirds / end chunks). Byte-identical copies.
  2. Perceptual duplicates — per-second dHash frame fingerprints computed
     from a tiny grayscale downscale (ffmpeg rawvideo). Survives re-encodes,
     resolution changes, upscales, and mild watermarks.
  3. Containment ("this clip is part of a longer video") — offset voting on
     matching frame hashes (Shazam-style), then Hamming verification of the
     aligned overlap.

Fingerprints are cached in the DB keyed by (path, size, mtime), so repeat
scans only fingerprint new/changed files.
"""
import hashlib
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from app.database import db
from app.logger import optimizarr_logger

# ── Tunables ────────────────────────────────────────────────────────────────
SAMPLE_FPS = 1.0            # one fingerprint frame per second
FRAME_BYTES = 9 * 8         # dHash source: 9x8 grayscale
HAMMING_MATCH = 10          # max differing bits for two frames to "match"
MIN_VOTES = 5               # matching-hash votes needed to consider a pair
MIN_OVERLAP_SECONDS = 20.0  # ignore tiny overlaps (intros, black frames)
MATCH_FRACTION = 0.6        # fraction of overlap frames that must match
COMMON_HASH_MAX_VIDEOS = 25 # hash seen in more videos than this = generic
                            # content (black/white/title cards) — not evidence
MIN_DISTINCT_HASHES = 5     # a video with fewer distinct visual states than
                            # this (static camera, still-image "video") carries
                            # no usable fingerprint — any alignment "matches",
                            # so refuse to claim a perceptual match at all
FFMPEG_TIMEOUT = 1800       # per-file fingerprint budget (seconds)


# ── Fingerprint primitives ──────────────────────────────────────────────────

def quick_hash(file_path: str) -> Optional[str]:
    """Sampled BLAKE2b over size + 4×1MiB chunks — exact-duplicate evidence.

    Reading 4 MiB instead of whole multi-GB files makes this near-instant;
    collision between different real media files of identical size is
    effectively impossible.
    """
    p = Path(file_path)
    try:
        size = p.stat().st_size
        h = hashlib.blake2b(str(size).encode(), digest_size=16)
        chunk = 1024 * 1024
        with open(p, 'rb') as f:
            for pos in {0, max(0, size // 3), max(0, 2 * size // 3),
                        max(0, size - chunk)}:
                f.seek(pos)
                h.update(f.read(chunk))
        return h.hexdigest()
    except OSError:
        return None


def dhash_frame(gray_9x8: bytes) -> int:
    """64-bit difference hash of one 9x8 grayscale frame.

    Bit = whether each pixel is brighter than its right neighbour. Robust to
    scaling, re-encoding, and brightness shifts; cheap enough for pure Python.
    """
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits = (bits << 1) | (1 if gray_9x8[row + x] < gray_9x8[row + x + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def compute_frame_hashes(file_path: str) -> Optional[List[int]]:
    """Per-second dHash sequence via ffmpeg (tiny grayscale rawvideo pipe).

    Returns None when the file can't be decoded.
    """
    # Notes on flags, from measurement on this hardware:
    #   -an -sn      skip audio/subtitle decode entirely (free win)
    #   NO -hwaccel  NVDEC measured 2.5× SLOWER here — GPU→CPU readback
    #                dominates when the output is a 9x8 frame
    #   fps filter   full decode is required; keyframe-only sampling
    #                (-skip_frame nokey) is ~2.4× faster but keyframe
    #                positions differ between encodes of the same video,
    #                which would break source↔re-encode matching
    cmd = [
        'ffmpeg', '-v', 'error', '-i', file_path, '-an', '-sn',
        '-vf', f'fps={SAMPLE_FPS},scale=9:8:flags=area,format=gray',
        '-f', 'rawvideo', '-',
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
    except (subprocess.TimeoutExpired, OSError):
        return None
    data = result.stdout
    if result.returncode != 0 or len(data) < FRAME_BYTES:
        return None
    n = len(data) // FRAME_BYTES
    return [dhash_frame(data[i * FRAME_BYTES:(i + 1) * FRAME_BYTES])
            for i in range(n)]


def pack_hashes(hashes: List[int]) -> bytes:
    return b''.join(h.to_bytes(8, 'big') for h in hashes)


def unpack_hashes(blob: bytes) -> List[int]:
    return [int.from_bytes(blob[i:i + 8], 'big')
            for i in range(0, len(blob) - len(blob) % 8, 8)]


# ── Matching ────────────────────────────────────────────────────────────────

def _verify_alignment(ha: List[int], hb: List[int], offset: int):
    """Compare b against a at the given offset (b[j] ↔ a[j+offset]).

    Returns (overlap_frames, matched_frames, start_j) or None.
    """
    start_j = max(0, -offset)
    end_j = min(len(hb), len(ha) - offset)
    overlap = end_j - start_j
    if overlap <= 0:
        return None
    matched = 0
    for j in range(start_j, end_j):
        if hamming(hb[j], ha[j + offset]) <= HAMMING_MATCH:
            matched += 1
    return overlap, matched, start_j


def match_library(videos: List[Dict]) -> List[Dict]:
    """Find duplicate / containment relationships across the library.

    ``videos``: [{path, size, quick_hash, hashes: [int]}]. Returns match
    dicts: {file_a, file_b, kind, score, offset_seconds, overlap_seconds}
    where file_a is always the longer (or kept) file and kind is one of
    'exact', 'duplicate', 'near', 'clip', 'partial'.
    """
    matches: List[Dict] = []
    paired = set()  # frozenset index pairs already reported

    # ── Tier 1: exact byte duplicates ───────────────────────────────────────
    by_qh: Dict[tuple, List[int]] = {}
    for i, v in enumerate(videos):
        if v.get('quick_hash') and v.get('size'):
            by_qh.setdefault((v['size'], v['quick_hash']), []).append(i)
    for idxs in by_qh.values():
        if len(idxs) < 2:
            continue
        idxs.sort(key=lambda i: videos[i]['path'])
        keep = idxs[0]
        for other in idxs[1:]:
            dur = len(videos[other].get('hashes') or []) / SAMPLE_FPS
            matches.append({
                'file_a': videos[keep]['path'], 'file_b': videos[other]['path'],
                'kind': 'exact', 'score': 100.0,
                'offset_seconds': 0.0, 'overlap_seconds': dur,
            })
            paired.add(frozenset((keep, other)))

    # ── Tier 2/3: inverted index on frame hashes (runs collapsed) ──────────
    index: Dict[int, List[tuple]] = {}
    distinct_counts: Dict[int, int] = {}
    for i, v in enumerate(videos):
        prev = None
        seen = set()
        for pos, h in enumerate(v.get('hashes') or []):
            if h == prev:
                continue  # static scenes: one entry per run
            prev = h
            seen.add(h)
            index.setdefault(h, []).append((i, pos))
        # UNIQUE hash values, not runs: a pattern cycling between 4 frames
        # produces many runs but carries only 4 frames' worth of signal.
        distinct_counts[i] = len(seen)

    votes: Dict[tuple, Dict[int, int]] = {}
    for h, occs in index.items():
        vids: Dict[int, List[int]] = {}
        for i, pos in occs:
            vids.setdefault(i, []).append(pos)
        if len(vids) < 2 or len(vids) > COMMON_HASH_MAX_VIDEOS:
            continue
        vid_ids = sorted(vids)
        for x in range(len(vid_ids)):
            for y in range(x + 1, len(vid_ids)):
                a, b = vid_ids[x], vid_ids[y]
                # canonical: a = longer video
                if len(videos[a].get('hashes') or []) < len(videos[b].get('hashes') or []):
                    a, b = b, a
                if frozenset((a, b)) in paired:
                    continue
                # cap positions per video so one repeated frame can't spam
                for pa in vids[a][:4]:
                    for pb in vids[b][:4]:
                        offs = votes.setdefault((a, b), {})
                        offs[pa - pb] = offs.get(pa - pb, 0) + 1

    for (a, b), offs in votes.items():
        if frozenset((a, b)) in paired:
            continue
        # Entropy guard: near-static footage matches at essentially any
        # offset, so a "match" between such files is meaningless. (Exact
        # byte duplicates are still caught by tier 1 above.)
        if min(distinct_counts.get(a, 0), distinct_counts.get(b, 0)) < MIN_DISTINCT_HASHES:
            continue
        offset, n = max(offs.items(), key=lambda kv: kv[1])
        # Adaptive evidence bar: a very short clip physically can't produce
        # MIN_VOTES distinct matching frames — require at least half its
        # distinct hashes instead (floor of 2).
        required = min(MIN_VOTES, max(2, distinct_counts.get(b, 0) // 2))
        if n < required:
            continue
        ha = videos[a].get('hashes') or []
        hb = videos[b].get('hashes') or []
        # Refine: best alignment within ±2s of the voted offset. Ties are
        # broken toward the VOTED offset — offset voting is the strongest
        # evidence we have, and self-similar footage (slow scenes, test
        # patterns) otherwise matches equally well at several shifts and
        # would report the window edge as the match position.
        candidates = []
        for o in range(offset - 2, offset + 3):
            r = _verify_alignment(ha, hb, o)
            if r:
                candidates.append((r[1], -abs(o - offset), o, r))
        if not candidates:
            continue
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        best = (candidates[0][2], candidates[0][3])
        o, (overlap, matched, start_j) = best
        overlap_sec = overlap / SAMPLE_FPS
        frac = matched / overlap
        min_len = min(len(ha), len(hb))
        if frac < MATCH_FRACTION:
            continue
        if overlap_sec < min(MIN_OVERLAP_SECONDS, 0.8 * min_len / SAMPLE_FPS):
            continue

        coverage_b = overlap / len(hb) if hb else 0
        if len(hb) >= 0.9 * len(ha) and coverage_b >= 0.85:
            kind = 'duplicate'
        elif coverage_b >= 0.7:
            kind = 'clip' if len(hb) < 0.8 * len(ha) else 'near'
        else:
            kind = 'partial'

        paired.add(frozenset((a, b)))
        matches.append({
            'file_a': videos[a]['path'], 'file_b': videos[b]['path'],
            'kind': kind, 'score': round(frac * 100, 1),
            'offset_seconds': (start_j + o) / SAMPLE_FPS,
            'overlap_seconds': round(overlap_sec, 1),
        })

    return matches


# ── Background scan worker ──────────────────────────────────────────────────

_scan_lock = threading.Lock()
_scan_cancel = threading.Event()
_scan_state: Dict = {'status': 'idle', 'phase': None, 'done': 0, 'total': 0,
                     'message': '', 'matches': 0, 'started_at': None}


def get_scan_status() -> Dict:
    return dict(_scan_state)


def start_scan(mode: str = 'full') -> Dict:
    """Launch a scan in a background thread.

    mode='quick': hash-only pass — finds byte-identical duplicates. Costs
        ~0.15s per file (a few minutes for a large library).
    mode='full':  also fingerprints video content, finding re-encodes and
        clips contained in longer videos. Costs ~25× realtime of video
        (days for a large library), but is cached and incremental after.
    """
    with _scan_lock:
        if _scan_state['status'] == 'running':
            return dict(_scan_state)
        _scan_cancel.clear()
        _scan_state.update(status='running', phase='discovering', done=0,
                           total=0, message='Discovering video files…',
                           matches=0, started_at=time.time(),
                           mode=('quick' if mode == 'quick' else 'full'))
    t = threading.Thread(target=_scan_worker, args=(mode,), daemon=True,
                         name='dedupe-scan')
    t.start()
    return dict(_scan_state)


def cancel_scan() -> Dict:
    """Request cancellation. Fingerprints already computed are kept, so a
    cancelled scan resumes from where it stopped next time."""
    if _scan_state.get('status') == 'running':
        _scan_cancel.set()
        _scan_state.update(message='Cancelling…')
    return dict(_scan_state)


def _scan_worker(mode: str = 'full'):
    quick = (mode == 'quick')
    try:
        from app.scanner import scanner
        files: List[str] = []
        for root in db.get_scan_roots(enabled_only=True):
            files.extend(scanner.discover_video_files(
                root['path'], recursive=bool(root.get('recursive', True))))
        files = sorted(set(files))
        _scan_state.update(phase='hashing' if quick else 'fingerprinting',
                           total=len(files))

        # Work out what still needs doing. A row is reusable when the file is
        # unchanged AND it already holds what this mode needs — a quick scan
        # stores no frame hashes, so a later full scan must still fingerprint
        # those files rather than treating them as cached.
        todo = []
        for path in files:
            try:
                st = Path(path).stat()
            except OSError:
                continue
            existing = db.get_fingerprint(path)
            unchanged = (existing
                         and existing['file_size'] == st.st_size
                         and abs((existing['mtime'] or 0) - st.st_mtime) < 1)
            if unchanged:
                if quick and existing['quick_hash']:
                    continue
                # Full mode: skip only if content was already fingerprinted,
                # or the file is known-undecodable (don't retry forever).
                if not quick and (existing['frame_hashes'] is not None
                                  or existing['error'] == 'undecodable'):
                    continue
            todo.append((path, st.st_size, st.st_mtime))

        _scan_state.update(total=len(files), done=len(files) - len(todo),
                           message=f'{len(todo)} file(s) to process…')

        # Sequential on purpose. Measured on this box (8-core i7-9700K, warm
        # cache, 4 real files / 23 min of video):
        #     1 worker 54.9s · 2 workers 58.4s · 3 workers 61.4s · 4 workers 63.6s
        # ffmpeg already multi-threads its decode, so extra concurrent
        # processes only contend — and running one at a time also leaves more
        # CPU for an active encode. Don't "optimize" this into a thread pool.
        done_n = len(files) - len(todo)
        for path, size, mtime in todo:
            if _scan_cancel.is_set():
                break
            _scan_state.update(done=done_n, message=Path(path).name[:80])
            qh = quick_hash(path)
            if quick:
                # Hash-only: keep any frame hashes an earlier full scan stored
                prev = db.get_fingerprint(path) or {}
                db.upsert_fingerprint(
                    file_path=path, file_size=size, mtime=mtime, quick_hash=qh,
                    frame_hashes=prev.get('frame_hashes'),
                    duration=prev.get('duration', 0), error=None,
                )
            else:
                hashes = compute_frame_hashes(path)
                db.upsert_fingerprint(
                    file_path=path, file_size=size, mtime=mtime,
                    quick_hash=qh,
                    frame_hashes=pack_hashes(hashes) if hashes else None,
                    duration=(len(hashes) / SAMPLE_FPS) if hashes else 0,
                    error=None if hashes else 'undecodable',
                )
            done_n += 1
            _scan_state.update(done=done_n)

        cancelled = _scan_cancel.is_set()
        processed = _scan_state['done']

        # Always match on whatever fingerprints exist — a cancelled scan's
        # partial results are still worth showing, and everything computed
        # so far is cached for the next run.
        _scan_state.update(phase='matching', message='Matching fingerprints…')
        rows = db.get_all_fingerprints()
        live = {str(f) for f in files}
        videos = []
        for r in rows:
            if r['file_path'] not in live:
                continue  # stale fingerprint for a removed file
            videos.append({
                'path': r['file_path'],
                'size': r['file_size'],
                'quick_hash': r['quick_hash'],
                'hashes': unpack_hashes(r['frame_hashes']) if r['frame_hashes'] else [],
            })
        matches = match_library(videos)
        db.replace_duplicate_matches(matches)

        if cancelled:
            msg = (f'Stopped after {processed}/{len(files)} files — '
                   f'{len(matches)} match(es) from what was scanned '
                   f'(progress kept; scanning again resumes)')
        elif quick:
            msg = (f'Quick scan done — {len(matches)} exact duplicate(s). '
                   f'Run a full scan to also find re-encodes and clips.')
        else:
            msg = f'Found {len(matches)} duplicate relationship(s)'
        _scan_state.update(status='cancelled' if cancelled else 'complete',
                           phase='done', matches=len(matches), message=msg)
        optimizarr_logger.app_logger.info(
            "Dedupe %s scan %s: %d files, %d matches", mode,
            'cancelled' if cancelled else 'complete', len(files), len(matches))
    except Exception as e:
        _scan_state.update(status='error', message=str(e)[:300])
        optimizarr_logger.app_logger.error("Dedupe scan failed: %s", e)
