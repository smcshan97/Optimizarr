"""Content classifier routes: install/status/settings and the Settings quiz."""
import json
import random
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import get_current_user, get_current_admin_user
from app.database import db

router = APIRouter()

_install_state = {"status": "idle", "progress": 0, "message": ""}
_STRIPS = Path(tempfile.gettempdir()) / "optimizarr_quiz"
_UPSCALER_CHOICES = ("realcugan:models-pro", "realcugan:models-se", "rtxvsr:ULTRA",
                     "rtxvsr:HIGHBITRATE_ULTRA", "upscayl:upscayl-lite-4x")


def _probs(raw):
    try:
        return (json.loads(raw) if isinstance(raw, str) else (raw or {})).get("probs") or {}
    except ValueError:
        return {}


def _coverage():
    with db.get_read_connection() as conn:
        rows = conn.execute(
            "SELECT COALESCE(content_type, 'unclassified'), COUNT(*) FROM queue "
            "WHERE status = 'pending' GROUP BY 1").fetchall()
        labels = conn.execute("SELECT COUNT(*) FROM content_labels").fetchone()[0]
    counts = {r[0]: r[1] for r in rows}
    return {"pending": sum(counts.values()), "by_type": counts, "labels": labels}


@router.get("/classifier/status")
def classifier_status(current_user: dict = Depends(get_current_user)):
    from app import content_classifier as cc
    from app.engine_health import load as health
    return {
        "installed": cc.installed_info(),
        "enabled": cc.enabled(),
        "detection_setting": db.get_setting("content_detection", "true") == "true",
        "threshold": cc.threshold(),
        "auto_upscaler_animated": db.get_setting("auto_upscaler_animated", cc.DEFAULT_AUTO["animated"]),
        "auto_upscaler_live": db.get_setting("auto_upscaler_live", cc.DEFAULT_AUTO["live"]),
        "auto_film_grain_live": int(db.get_setting("auto_film_grain_live", "0") or 0),
        "upscaler_choices": list(_UPSCALER_CHOICES),
        "install_state": _install_state,
        "coverage": _coverage(),
        "self_test": health().get("classifier"),
    }


@router.post("/classifier/install")
def classifier_install(current_user: dict = Depends(get_current_admin_user)):
    from app import content_classifier as cc
    if _install_state.get("status") == "downloading":
        return _install_state

    def work():
        _install_state.update(status="downloading", progress=0, message="Starting…", error=None)
        try:
            cc.install(_install_state)
            _install_state.update(status="installed", progress=100,
                                  message=f"✓ Content classifier ready ({cc.MODEL_VERSION})")
            from app.engine_health import test_in_background
            test_in_background(["classifier"])
        except Exception as e:
            _install_state.update(status="error", error=str(e), message=f"Install failed: {e}")

    threading.Thread(target=work, daemon=True, name="classifier-install").start()
    return _install_state


def _redecide_all():
    """Re-apply the threshold to every model-classified row (from stored probs)."""
    from app import content_classifier as cc
    with db.get_read_connection() as conn:
        rows = conn.execute("SELECT id, content_probs FROM queue WHERE content_probs IS NOT NULL").fetchall()
    for qid, raw in rows:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if rec.get("source") == "user" or not rec.get("probs"):
            continue
        db.update_queue_item(qid, content_type=cc.decide(rec["probs"])[0])


@router.post("/classifier/settings")
def classifier_settings(data: dict, current_user: dict = Depends(get_current_admin_user)):
    if "enabled" in data:
        db.set_setting("content_detection", "true" if data["enabled"] else "false")
    for side in ("animated", "live"):
        key = f"auto_upscaler_{side}"
        if key in data:
            if data[key] not in _UPSCALER_CHOICES:
                raise HTTPException(status_code=400, detail=f"Unknown upscaler choice: {data[key]}")
            db.set_setting(key, data[key])
    if "auto_film_grain_live" in data:
        try:
            grain = max(0, min(50, int(data["auto_film_grain_live"])))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Film grain must be 0-50")
        db.set_setting("auto_film_grain_live", str(grain))
    if "threshold" in data:
        try:
            th = max(0.34, min(0.99, float(data["threshold"])))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Threshold must be a number")
        db.set_setting("content_threshold", f"{th:g}")
        _redecide_all()
    return {"message": "Content detection settings saved"}


# ── 50-question check ─────────────────────────────────────────────────────

@router.get("/classifier/quiz")
def quiz_questions(count: int = 50, current_user: dict = Depends(get_current_user)):
    """Classified files the user hasn't labelled yet: half the ones the model
    is least sure about (most informative), half random across its answers.
    The model's answer is NOT sent, so it can't bias the user."""
    from app import content_classifier as cc
    count = max(5, min(100, count))
    with db.get_read_connection() as conn:
        rows = conn.execute(
            "SELECT q.id, q.file_path, q.content_probs FROM queue q "
            "WHERE q.content_probs IS NOT NULL AND q.file_path NOT IN "
            "(SELECT file_path FROM content_labels)").fetchall()
    scored = []
    for qid, path, raw in rows:
        p = _probs(raw)
        if p and Path(path).exists():
            scored.append((cc.decide(p, 0.0)[1], qid, path))
    scored.sort()
    hard = scored[: count // 2]
    rest = scored[count // 2:]
    random.shuffle(rest)
    picked = hard + rest[: count - len(hard)]
    random.shuffle(picked)
    return {"questions": [{"id": qid, "name": Path(path).name} for _, qid, path in picked],
            "available": len(scored)}


@router.get("/classifier/quiz/{queue_id}/strip")
def quiz_strip(queue_id: int, current_user: dict = Depends(get_current_user)):
    from app import content_classifier as cc
    item = db.get_queue_item(queue_id)
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    _STRIPS.mkdir(parents=True, exist_ok=True)
    out = _STRIPS / f"q{queue_id}.jpg"
    if not out.exists() and not cc.quiz_strip(item["file_path"], out):
        raise HTTPException(status_code=500, detail="Couldn't extract frames from this file")
    return FileResponse(str(out), media_type="image/jpeg")


@router.post("/classifier/quiz/answer")
def quiz_answer(data: dict, current_user: dict = Depends(get_current_user)):
    """Record the user's label; it also becomes this file's content type."""
    from app import content_classifier as cc
    label = (data or {}).get("label")
    item = db.get_queue_item(int((data or {}).get("queue_id") or 0))
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if label not in cc.CLASSES + ("skip",):
        raise HTTPException(status_code=400, detail="label must be 2d, 3d, live or skip")
    probs = _probs(item.get("content_probs"))
    model = cc.decide(probs)[0] if probs else None
    if label != "skip":
        with db.get_connection() as conn:
            conn.execute("INSERT INTO content_labels (queue_id, file_path, label, probs) VALUES (?,?,?,?)",
                         (item["id"], item["file_path"], label, json.dumps(probs)))
        db.update_queue_item(item["id"], content_type=label,
                             content_probs=cc.record(probs, source="user"))
    return {"model": model, "model_probs": probs,
            "agrees": None if label == "skip" or model is None
            else (cc.is_animated(label) == cc.is_animated(model) if model != "uncertain" else None)}


@router.get("/classifier/quiz/summary")
def quiz_summary(current_user: dict = Depends(get_current_user)):
    from app import content_classifier as cc
    with db.get_read_connection() as conn:
        rows = conn.execute("SELECT label, probs FROM content_labels").fetchall()
    labels = []
    for label, raw in rows:
        try:
            labels.append((label, json.loads(raw or "{}")))
        except ValueError:
            continue
    return {**cc.calibration(labels), "threshold": cc.threshold()}
