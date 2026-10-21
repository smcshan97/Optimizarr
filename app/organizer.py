"""Companion-file organizer: give loose videos with companions a folder.

A companion file shares its video's name — `Movie.funscript`,
`Movie.en.srt`, `Movie.nfo`, `Movie-poster.jpg` next to `Movie.mp4`. Players
find them by name, so they must travel with the video. For libraries with
"Organize companion files" on, every scan moves each loose video that has
companions, and those companions, into a folder named after the video:

    Movie.mp4, Movie.funscript   ->   Movie/Movie.mp4, Movie/Movie.funscript

Left alone: videos already in a folder of their own name, videos with no
companions, a video that is encoding, libraries Sonarr/Radarr manage (they
track paths themselves), and any group where a file would land on an
existing name — nothing is ever overwritten.
"""
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.database import db
from app.logger import optimizarr_logger

# Kodi/Plex artwork named "<video>-<kind>.<ext>". Other "<video>-..." names
# are left alone: "Movie-2.funscript" is far more likely another video's.
ART_KINDS = ("poster", "fanart", "thumb", "banner", "landscape", "clearlogo",
             "clearart", "disc", "logo")


def _video_extensions() -> set:
    from app.scanner import MediaScanner
    return {e.lower() for e in MediaScanner.VIDEO_EXTENSIONS}


def _belongs_to(name: str, stem: str) -> bool:
    n, s = name.lower(), stem.lower()
    if n.startswith(s + "."):
        return True
    return any(n.startswith(f"{s}-{kind}.") for kind in ART_KINDS)


def _groups(names: Iterable[str], video_exts: set) -> Dict[str, Tuple[List[str], List[str]]]:
    """stem -> (videos, companions) for the files of one folder, keeping only
    stems that have companions. Each companion goes to the longest stem it
    matches, so Movie.Part2.funscript belongs to Movie.Part2.mp4, and a
    video is never another video's companion."""
    names = list(names)
    videos: Dict[str, List[str]] = {}
    for n in names:
        if Path(n).suffix.lower() in video_exts:
            videos.setdefault(Path(n).stem, []).append(n)
    stems = sorted(videos, key=len, reverse=True)
    companions: Dict[str, List[str]] = {s: [] for s in stems}
    for n in names:
        if Path(n).suffix.lower() in video_exts:
            continue
        for s in stems:
            if _belongs_to(n, s):
                companions[s].append(n)
                break
    return {s: (videos[s], companions[s]) for s in stems if companions[s]}


def _move_group(folder: Path, stem: str, files: List[str]) -> bool:
    """Move ``files`` from ``folder`` into ``folder/stem``. All or nothing:
    any clash skips the group, a failed move puts back what already moved."""
    target = folder / stem
    if target.exists() and not target.is_dir():
        return False
    if any((target / f).exists() for f in files):
        return False
    created = not target.exists()
    target.mkdir(exist_ok=True)
    done = []
    try:
        for f in files:
            os.rename(folder / f, target / f)
            done.append(f)
    except OSError as e:
        for f in reversed(done):
            try:
                os.rename(target / f, folder / f)
            except OSError:
                pass
        if created:
            try:
                target.rmdir()
            except OSError:
                pass
        optimizarr_logger.app_logger.warning("Organizer: couldn't move %s: %s", stem, e)
        return False
    return True


def organize_tree(root: str, recursive: bool = True,
                  busy: Optional[Iterable[str]] = None) -> List[Tuple[str, str]]:
    """Organize every folder under ``root`` (just ``root`` when not
    recursive). Returns (old, new) paths of each video moved. ``busy``:
    video paths that must not move (encoding right now)."""
    video_exts = _video_extensions()
    busy_keys = {os.path.normcase(os.path.abspath(p)) for p in (busy or ())}
    if recursive:
        folders = [(Path(dp), files) for dp, _, files in os.walk(root)]
    else:
        try:
            folders = [(Path(root), [e.name for e in os.scandir(root) if e.is_file()])]
        except OSError:
            folders = []
    moved: List[Tuple[str, str]] = []
    for folder, files in folders:          # snapshot taken before any move
        for stem, (videos, companions) in _groups(files, video_exts).items():
            if folder.name.lower() == stem.lower():
                continue                    # already in its own folder
            if not stem.strip() or stem != stem.rstrip(". "):
                continue                    # not a valid Windows folder name
            if any(os.path.normcase(os.path.abspath(folder / v)) in busy_keys for v in videos):
                continue
            if _move_group(folder, stem, videos + companions):
                for v in videos:
                    moved.append((str(folder / v), str(folder / stem / v)))
                optimizarr_logger.app_logger.info(
                    "Organizer: moved %s and %d companion file(s) into %s",
                    ", ".join(videos), len(companions), folder / stem)
    return moved


def organize_root(scan_root: Dict) -> int:
    """Run the organizer for one library if it's switched on. Queue rows
    follow their moved files. Returns the number of videos moved."""
    if not scan_root.get("organize_companions") or scan_root.get("external_connection_id"):
        return 0
    busy = [i["file_path"] for status in ("processing", "paused")
            for i in db.get_queue_items(status=status)]
    moved = organize_tree(scan_root["path"], recursive=bool(scan_root.get("recursive", True)),
                          busy=busy)
    for old, new in moved:
        db.rename_queue_path(old, new)
    return len(moved)
