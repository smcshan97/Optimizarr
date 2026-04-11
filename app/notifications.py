"""
Outgoing notification webhooks for Optimizarr.

Fires on: encode_complete, encode_failed, queue_empty.
Supports Discord, Slack, and generic JSON webhooks.
"""
import json
import threading
from typing import Dict, List, Optional
from datetime import datetime

try:
    import requests
except ImportError:
    requests = None

from app.database import db


def _format_size(bytes_val: int) -> str:
    """Human-readable file size."""
    if bytes_val <= 0:
        return "0 B"
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} PB"


def _get_enabled_webhooks(event: str) -> List[Dict]:
    """Fetch all enabled notification webhooks that listen for this event."""
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, name, webhook_url, webhook_type, events FROM notifications WHERE enabled = 1"
            )
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description]
        webhooks = []
        for row in rows:
            w = dict(zip(cols, row))
            events = json.loads(w.get('events', '[]')) if w.get('events') else []
            if event in events:
                webhooks.append(w)
        return webhooks
    except Exception:
        return []


def _send_webhook(webhook: Dict, payload: Dict):
    """Send a payload to a webhook URL. Runs in a background thread."""
    if not requests:
        print("⚠ 'requests' library not installed — cannot send notifications")
        return

    url = webhook.get('webhook_url', '')
    wtype = webhook.get('webhook_type', 'generic')

    try:
        if wtype == 'discord':
            body = _format_discord(payload)
        elif wtype == 'slack':
            body = _format_slack(payload)
        else:
            body = payload

        resp = requests.post(url, json=body, timeout=10)
        if resp.status_code >= 400:
            print(f"⚠ Webhook '{webhook.get('name', '?')}' returned {resp.status_code}")
    except Exception as e:
        print(f"⚠ Webhook '{webhook.get('name', '?')}' failed: {e}")


def _format_discord(payload: Dict) -> Dict:
    """Format payload as a Discord embed message."""
    event = payload.get('event', 'unknown')
    color_map = {
        'encode_complete': 0x2ecc71,  # green
        'encode_failed':   0xe74c3c,  # red
        'queue_empty':     0x3498db,  # blue
    }
    embed = {
        "title": payload.get('title', 'Optimizarr'),
        "description": payload.get('message', ''),
        "color": color_map.get(event, 0x95a5a6),
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "Optimizarr"},
    }
    fields = payload.get('fields')
    if fields:
        embed["fields"] = [
            {"name": k, "value": str(v), "inline": True}
            for k, v in fields.items()
        ]
    return {"embeds": [embed]}


def _format_slack(payload: Dict) -> Dict:
    """Format payload as a Slack incoming webhook message."""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": payload.get('title', 'Optimizarr')}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": payload.get('message', '')}
        },
    ]
    fields = payload.get('fields')
    if fields:
        field_block = {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{k}:*\n{v}"}
                for k, v in fields.items()
            ]
        }
        blocks.append(field_block)
    return {"blocks": blocks}


# -------------------------------------------------------------------
# Public API — call these from encoder.py / process_queue
# -------------------------------------------------------------------

def notify_encode_complete(file_path: str, original_size: int, new_size: int,
                           savings: int, encoding_time: float, profile_name: str):
    """Fire encode_complete notifications."""
    savings_pct = (savings / original_size * 100) if original_size > 0 else 0
    filename = file_path.replace('\\', '/').split('/')[-1]

    payload = {
        'event': 'encode_complete',
        'title': '✅ Encode Complete',
        'message': f"**{filename}** encoded successfully.",
        'fields': {
            'Profile': profile_name,
            'Original': _format_size(original_size),
            'New Size': _format_size(new_size),
            'Saved': f"{_format_size(savings)} ({savings_pct:.1f}%)",
            'Time': f"{encoding_time:.0f}s",
        }
    }
    _fire('encode_complete', payload)


def notify_encode_failed(file_path: str, error: str, profile_name: str = ''):
    """Fire encode_failed notifications."""
    filename = file_path.replace('\\', '/').split('/')[-1]
    payload = {
        'event': 'encode_failed',
        'title': '❌ Encode Failed',
        'message': f"**{filename}** failed to encode.",
        'fields': {
            'Profile': profile_name or 'Unknown',
            'Error': error[:200],
        }
    }
    _fire('encode_failed', payload)


def notify_queue_empty():
    """Fire queue_empty notification."""
    payload = {
        'event': 'queue_empty',
        'title': '🏁 Queue Empty',
        'message': 'All encoding jobs have finished. The queue is empty.',
    }
    _fire('queue_empty', payload)


def _fire(event: str, payload: Dict):
    """Send payload to all enabled webhooks for this event (non-blocking)."""
    webhooks = _get_enabled_webhooks(event)
    for wh in webhooks:
        t = threading.Thread(target=_send_webhook, args=(wh, payload), daemon=True)
        t.start()
