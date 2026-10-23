"""
Sonarr / Radarr / Stash path translation and post-encode refresh.

The *arr apps usually run in Docker, so they name files by container path
(/tv/Show/S01E01.mkv) while Optimizarr sees the host path
(F:\\tv shows\\Show\\S01E01.mkv). Each connection stores remote->local
prefix mappings, the same idea as Sonarr's own "Remote Path Mappings".
Webhook and library-sync paths are translated remote->local; the refresh
after an encode translates local->remote.
"""
import json
import ntpath
import posixpath
import re
import subprocess
import threading
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from app.logger import optimizarr_logger

# Container mounts that never hold media
_NON_MEDIA_MOUNTS = {"/config", "/root/.stash", "/data/config", "/app/config"}


def parse_mappings(conn: Dict) -> List[Tuple[str, str]]:
    """[(remote_prefix, local_prefix), ...], longest remote prefix first."""
    raw = conn.get("path_mappings") or "[]"
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    pairs = [(str(m.get("remote", "")).strip(), str(m.get("local", "")).strip())
             for m in items or [] if isinstance(m, dict)]
    pairs = [(r, l) for r, l in pairs if r and l]
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


def validate_mappings(items) -> str:
    """Normalise user input to the stored JSON form; raises ValueError."""
    if not isinstance(items, list):
        raise ValueError("path_mappings must be a list of {remote, local}")
    out = []
    for m in items:
        if not isinstance(m, dict):
            raise ValueError("each mapping needs 'remote' and 'local'")
        remote, local = str(m.get("remote", "")).strip(), str(m.get("local", "")).strip()
        if not remote or not local:
            raise ValueError("each mapping needs both 'remote' and 'local'")
        out.append({"remote": remote, "local": local})
    return json.dumps(out)


def _is_windows_path(p: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", p)) or p.startswith("\\\\")


def _strip_sep(p: str) -> str:
    return p.rstrip("/\\") or p


def _under(path: str, prefix: str, windows: bool) -> Optional[str]:
    """Remainder of ``path`` below ``prefix`` (no leading separator), or None."""
    prefix = _strip_sep(prefix)
    if windows:
        a, b = path.replace("/", "\\").lower(), prefix.replace("/", "\\").lower()
        if a == b:
            return ""
        if a.startswith(b + "\\"):
            return path.replace("/", "\\")[len(prefix) + 1:]
        return None
    if path == prefix:
        return ""
    if path.startswith(prefix + "/"):
        return path[len(prefix) + 1:]
    return None


def _join(prefix: str, rest: str) -> str:
    if not rest:
        return prefix
    if _is_windows_path(prefix):
        return ntpath.join(_strip_sep(prefix) + "\\", rest.replace("/", "\\"))
    return posixpath.join(_strip_sep(prefix), rest.replace("\\", "/"))


def to_local(conn: Dict, path: str) -> str:
    """Translate an *arr (remote) path to the host path; unchanged if unmapped."""
    for remote, local in parse_mappings(conn):
        rest = _under(path, remote, _is_windows_path(remote))
        if rest is not None:
            return _join(local, rest)
    return path


def to_remote(conn: Dict, path: str) -> Optional[str]:
    """Translate a host path to the *arr's view; None if no mapping covers it
    (an unmapped path is returned as-is only when the connection has no
    mappings at all — i.e. the app sees the same paths we do)."""
    mappings = parse_mappings(conn)
    if not mappings:
        return path
    for remote, local in sorted(mappings, key=lambda p: len(p[1]), reverse=True):
        rest = _under(path, local, _is_windows_path(local))
        if rest is not None:
            return _join(remote, rest)
    return None


# ── Docker detection ──────────────────────────────────────────────────────

def detect_docker_mappings(base_url: str) -> Dict:
    """Find the local Docker container publishing ``base_url``'s port and
    return its media volume mounts as suggested mappings."""
    port = urlsplit(base_url if "://" in base_url else f"http://{base_url}").port
    if not port:
        return {"ok": False, "error": "The URL has no port to match a container by"}
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True,
                            text=True, timeout=15, creationflags=creation)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "error": "Docker isn't available on the Optimizarr host"}
    if ps.returncode != 0:
        return {"ok": False, "error": (ps.stderr or "docker ps failed").strip()[:200]}
    for name in ps.stdout.split():
        r = subprocess.run(["docker", "inspect", name], capture_output=True, text=True,
                           timeout=15, creationflags=creation)
        if r.returncode != 0:
            continue
        try:
            info = json.loads(r.stdout)[0]
        except (ValueError, IndexError):
            continue
        ports = (info.get("NetworkSettings") or {}).get("Ports") or {}
        published = {int(b.get("HostPort")) for binds in ports.values() if binds
                     for b in binds if str(b.get("HostPort", "")).isdigit()}
        if port not in published:
            continue
        mappings = [{"remote": m["Destination"], "local": m["Source"]}
                    for m in info.get("Mounts") or []
                    if m.get("Destination") not in _NON_MEDIA_MOUNTS
                    and not m.get("Destination", "").startswith("/config")]
        return {"ok": True, "container": name, "mappings": mappings}
    return {"ok": False, "error": f"No running container publishes port {port}"}


# ── Refresh after an encode replaced a file ───────────────────────────────

_cache: Dict[Tuple[int, str], Tuple[float, list]] = {}
_CACHE_TTL = 600


def _items(conn: Dict, endpoint: str) -> list:
    from app.external_connections import connection_manager
    key = (conn["id"], endpoint)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL:
        return hit[1]
    items = connection_manager._get(conn, endpoint)
    _cache[key] = (time.monotonic(), items)
    return items


def _owner(items: list, remote_path: str) -> Optional[Dict]:
    """Series/movie whose folder contains ``remote_path`` (deepest wins)."""
    best = None
    for it in items:
        folder = it.get("path") or ""
        if folder and _under(remote_path, folder, _is_windows_path(folder)) is not None:
            if best is None or len(folder) > len(best.get("path", "")):
                best = it
    return best


def refresh_for_path(local_path: str) -> List[str]:
    """Ask every enabled Sonarr/Radarr that owns ``local_path`` to rescan it.

    Keeps their file size/codec/mediainfo current, and lets them re-link a
    file whose extension changed (.mp4 -> .mkv). Returns what was refreshed.
    """
    from app.database import db
    from app.external_connections import connection_manager
    done = []
    for conn in db.get_external_connections():
        app = conn.get("app_type")
        if app not in ("sonarr", "radarr") or not conn.get("enabled"):
            continue
        remote = to_remote(conn, local_path)
        if remote is None:
            continue
        try:
            if app == "sonarr":
                owner = _owner(_items(conn, "/series"), remote)
                body = owner and {"name": "RescanSeries", "seriesId": owner["id"]}
            else:
                owner = _owner(_items(conn, "/movie"), remote)
                body = owner and {"name": "RescanMovie", "movieId": owner["id"]}
            if body:
                connection_manager._post(conn, "/command", body)
                done.append(f"{conn.get('name')}: {owner.get('title')}")
        except Exception as e:
            optimizarr_logger.app_logger.warning(
                "Could not ask %s to rescan %s: %s", conn.get("name"), local_path, e)
    if done:
        optimizarr_logger.app_logger.info("Asked to rescan: %s", "; ".join(done))
    return done


def refresh_in_background(local_path: str):
    threading.Thread(target=refresh_for_path, args=(local_path,), daemon=True,
                     name="ArrRefresh").start()
