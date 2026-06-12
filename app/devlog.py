"""Developer event log — compact, token-cheap session forensics.

One JSON line per significant event in logs/devlog.jsonl, written with
short keys so a full day of runs reads in a few hundred tokens. Built for
"show Claude the last 24h at session start" — read the file directly, or
run the summarizer:

    python -m app.devlog 24      # aggregate the last 24 hours

Event vocabulary (keep it small and stable):
    start / stop          — app lifecycle (v=version, rec=recovered items)
    api_err / api_5xx     — unhandled exception / 5xx response (p=path)
    enc_start             — encode begins (id, f=basename, c=target codec)
    enc_done              — success (id, f, s=encode secs, sv=bytes saved)
    enc_retry / enc_fail  — failure handling (id, n=retry count, err)
    enc_cancel            — manual stop (id)
    throttle              — resource pause/resume (a=action, why)
    scan                  — library scan finished (root, queued)
    watch_queue           — folder watch auto-queued files (n, path)

Never raises — a broken devlog must not break the app.
"""
import json
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

_LOCK = threading.Lock()
_FILE = Path("logs") / "devlog.jsonl"
_MAX_BYTES = 1_000_000  # rotate to .1 past this; one backup kept
_TS_FMT = "%m-%d %H:%M:%S"


def devlog(ev: str, **fields):
    """Append one compact event line. Silently ignores all errors."""
    try:
        rec = {"t": datetime.now().strftime(_TS_FMT), "e": ev}
        for k, v in fields.items():
            if v is not None:
                rec[k] = v
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            _FILE.parent.mkdir(parents=True, exist_ok=True)
            if _FILE.exists() and _FILE.stat().st_size > _MAX_BYTES:
                bak = _FILE.with_suffix(".jsonl.1")
                if bak.exists():
                    bak.unlink()
                _FILE.rename(bak)
            with open(_FILE, "a", encoding="utf-8", errors="replace") as f:
                f.write(line + "\n")
    except Exception:
        pass


def read_events(hours: float = 24.0):
    """Return events from the last N hours (oldest first). Never raises."""
    events = []
    now = datetime.now()
    cutoff = now - timedelta(hours=hours)
    for path in (_FILE.with_suffix(".jsonl.1"), _FILE):
        try:
            if not path.exists():
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rec = json.loads(raw)
                        # Timestamps are year-less; assume current year and
                        # roll back one year if that lands in the future
                        # (December logs read in January).
                        ts = datetime.strptime(rec.get("t", ""), _TS_FMT).replace(year=now.year)
                        if ts > now + timedelta(days=1):
                            ts = ts.replace(year=now.year - 1)
                        if ts >= cutoff:
                            events.append(rec)
                    except (ValueError, json.JSONDecodeError):
                        continue
        except Exception:
            continue
    return events


def summarize(hours: float = 24.0) -> str:
    """Aggregate the last N hours into a compact human/Claude-readable digest."""
    events = read_events(hours)
    if not events:
        return f"No devlog events in the last {hours:g}h."

    lines = [f"=== devlog: last {hours:g}h — {len(events)} events ==="]

    # Counts per event type
    counts = {}
    for rec in events:
        counts[rec.get("e", "?")] = counts.get(rec.get("e", "?"), 0) + 1
    lines.append("counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    # Encode outcomes
    done = [r for r in events if r.get("e") == "enc_done"]
    if done:
        total_s = sum(r.get("s", 0) for r in done)
        total_sv = sum(r.get("sv", 0) for r in done)
        lines.append(f"encodes: {len(done)} done, {total_s/3600:.1f}h encoding, "
                     f"{total_sv/1e9:.2f} GB saved")

    # Errors, deduplicated with counts
    errs = {}
    for rec in events:
        if rec.get("e") in ("api_err", "api_5xx", "enc_fail", "enc_retry"):
            key = f"[{rec['e']}] {rec.get('p', '')}{rec.get('err', '')[:90]}"
            errs[key] = errs.get(key, 0) + 1
    if errs:
        lines.append("-- problems (deduped) --")
        for key, n in sorted(errs.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {n}× {key}")
    else:
        lines.append("no errors recorded")

    # Tail: most recent events verbatim
    lines.append("-- last 15 events --")
    for rec in events[-15:]:
        lines.append("  " + json.dumps(rec, ensure_ascii=False, separators=(",", ":")))

    return "\n".join(lines)


if __name__ == "__main__":
    hrs = float(sys.argv[1]) if len(sys.argv) > 1 else 24.0
    print(summarize(hrs))
