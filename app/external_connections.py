"""
External Connections module for Optimizarr.
Manages Sonarr and Radarr API integrations.

- API keys are encrypted at rest using Fernet symmetric encryption
- Keys are derived from settings.SECRET_KEY via SHA-256 + base64
- GET responses only return masked keys (****XXXX last-4 chars)
- Stash integration is planned but gated behind privacy toggle completion
"""
import base64
import hashlib
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime

from app.config import settings
from app.logger import optimizarr_logger


# ---------------------------------------------------------------------------
# Codec mapping — Sonarr/Radarr mediaInfo.videoCodec → Optimizarr codec names
# ---------------------------------------------------------------------------

CODEC_MAP: Dict[str, str] = {
    # Sonarr/Radarr value  →  Optimizarr codec
    "x264":   "h264",
    "avc":    "h264",
    "h264":   "h264",
    "h.264":  "h264",
    "x265":   "h265",
    "hevc":   "h265",
    "h265":   "h265",
    "h.265":  "h265",
    "av1":    "av1",
    "vp9":    "vp9",
    "vp09":   "vp9",
    "xvid":   "mpeg4",
    "divx":   "mpeg4",
    "mpeg4":  "mpeg4",
    "mpeg-2": "mpeg2",
    "mpeg2":  "mpeg2",
    "wmv":    "wmv",
    "wmv3":   "wmv",
}

# Request timeout for external API calls (seconds)
_REQUEST_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _get_fernet():
    """Return a Fernet instance keyed from settings.SECRET_KEY."""
    from cryptography.fernet import Fernet
    # SHA-256 of the secret key → 32 bytes → urlsafe base64 → valid Fernet key
    key_bytes = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt a plaintext API key for storage."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(encrypted: str) -> str:
    """Decrypt a stored API key."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


def mask_api_key(plaintext: str) -> str:
    """Return a masked key showing only the last 4 characters."""
    if len(plaintext) <= 4:
        return "****"
    return f"****{plaintext[-4:]}"


# ---------------------------------------------------------------------------
# Connection serialiser — strips encrypted key from API responses
# ---------------------------------------------------------------------------

def public_connection(conn: Dict) -> Dict:
    """
    Return a copy of the connection dict safe for API responses.
    Replaces the raw encrypted key with a masked preview.
    """
    c = dict(conn)
    raw_encrypted = c.pop("api_key_encrypted", "")
    try:
        plaintext = decrypt_api_key(raw_encrypted)
        c["api_key_masked"] = mask_api_key(plaintext)
    except Exception:
        c["api_key_masked"] = "****"
    return c


# ---------------------------------------------------------------------------
# ExternalConnectionManager
# ---------------------------------------------------------------------------

class ExternalConnectionManager:
    """
    Handles all communication with Sonarr / Radarr instances.

    Methods intentionally use synchronous requests so they can be called
    directly from FastAPI background tasks or sync route handlers without
    needing an async HTTP client dependency.
    """

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _headers(self, conn: Dict) -> Dict[str, str]:
        plaintext = decrypt_api_key(conn["api_key_encrypted"])
        return {"X-Api-Key": plaintext, "Accept": "application/json"}

    def _base(self, conn: Dict) -> str:
        """Return the base URL without trailing slash."""
        return conn["base_url"].rstrip("/")

    def _get(self, conn: Dict, path: str, params: Dict = None) -> Any:
        url = f"{self._base(conn)}/api/v3{path}"
        resp = requests.get(
            url,
            headers=self._headers(conn),
            params=params or {},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, conn: Dict, path: str, body: Dict) -> Any:
        url = f"{self._base(conn)}/api/v3{path}"
        resp = requests.post(
            url,
            headers={**self._headers(conn), "Content-Type": "application/json"},
            json=body,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def test_connection(self, conn: Dict) -> Dict:
        """
        Test connectivity by hitting /api/v3/system/status.
        Returns {"ok": True, "app_name": ..., "version": ...} on success.
        """
        try:
            data = self._get(conn, "/system/status")
            return {
                "ok": True,
                "app_name": data.get("appName", conn["app_type"].capitalize()),
                "version": data.get("version", "unknown"),
                "instance_name": data.get("instanceName", ""),
            }
        except requests.exceptions.ConnectionError:
            return {"ok": False, "error": f"Cannot connect to {conn['base_url']} — is it running?"}
        except requests.exceptions.Timeout:
            return {"ok": False, "error": "Connection timed out"}
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                return {"ok": False, "error": "Invalid API key (401 Unauthorized)"}
            return {"ok": False, "error": f"HTTP {e.response.status_code if e.response else 'error'}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def fetch_radarr_library(self, conn: Dict) -> List[Dict]:
        """
        Fetch all movies from Radarr that have a downloaded file.
        Returns a list of dicts suitable for adding to the Optimizarr queue.
        """
        movies = self._get(conn, "/movie")
        items = []
        for movie in movies:
            mf = movie.get("movieFile")
            if not mf:
                continue  # not downloaded yet
            path = mf.get("path", "")
            if not path:
                continue
            media_info = mf.get("mediaInfo", {}) or {}
            raw_codec = media_info.get("videoCodec", "unknown")
            codec = CODEC_MAP.get(raw_codec.lower(), raw_codec.lower() or "unknown")
            resolution = media_info.get("videoResolution", "unknown")
            bitrate = media_info.get("videoBitrate", 0)
            items.append({
                "file_path": path,
                "file_size_bytes": mf.get("size", 0),
                "current_specs": {
                    "codec": codec,
                    "resolution": resolution,
                    "bit_rate": bitrate,
                    "source": "radarr",
                    "radarr_movie_id": movie.get("id"),
                    "title": movie.get("title", ""),
                },
            })
        return items

    def fetch_sonarr_library(self, conn: Dict) -> List[Dict]:
        """
        Fetch all episode files from Sonarr.
        Paginates through all series then fetches episode files per series.
        Returns a list of dicts suitable for adding to the Optimizarr queue.
        """
        series_list = self._get(conn, "/series")
        items = []
        for series in series_list:
            series_id = series.get("id")
            if not series_id:
                continue
            try:
                ep_files = self._get(conn, "/episodefile", params={"seriesId": series_id})
            except Exception as e:
                optimizarr_logger.app_logger.warning(
                    "Sonarr: could not fetch episode files for series %s: %s", series_id, e
                )
                continue
            for ef in ep_files:
                path = ef.get("path", "")
                if not path:
                    continue
                media_info = ef.get("mediaInfo", {}) or {}
                raw_codec = media_info.get("videoCodec", "unknown")
                codec = CODEC_MAP.get(raw_codec.lower(), raw_codec.lower() or "unknown")
                resolution = media_info.get("resolution", "unknown")
                bitrate = media_info.get("videoBitrate", 0)
                items.append({
                    "file_path": path,
                    "file_size_bytes": ef.get("size", 0),
                    "current_specs": {
                        "codec": codec,
                        "resolution": resolution,
                        "bit_rate": bitrate,
                        "source": "sonarr",
                        "sonarr_series_id": series_id,
                        "series_title": series.get("title", ""),
                    },
                })
        return items

    def register_webhook(self, conn: Dict, optimizarr_url: str) -> Dict:
        """
        Register Optimizarr as a webhook notification in Sonarr or Radarr.
        The webhook URL will be: {optimizarr_url}/api/webhooks/{app_type}
        """
        app_type = conn["app_type"]
        webhook_url = f"{optimizarr_url.rstrip('/')}/api/webhooks/{app_type}"

        payload = {
            "name": "Optimizarr",
            "implementation": "Webhook",
            "configContract": "WebhookSettings",
            "fields": [
                {"name": "url", "value": webhook_url},
                {"name": "method", "value": 1},  # 1 = POST
            ],
            "onDownload": True,
            "onUpgrade": True,
            "onRename": False,
            "onDelete": False,
            "tags": [],
        }
        try:
            result = self._post(conn, "/notification", payload)
            return {"ok": True, "webhook_id": result.get("id"), "url": webhook_url}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# Global singleton
connection_manager = ExternalConnectionManager()
