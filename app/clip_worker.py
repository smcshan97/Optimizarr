"""CLIP content classifier (see app.content_classifier) — runs inside the
isolated runtime %LOCALAPPDATA%/Optimizarr/clip/venv (onnxruntime, numpy,
tokenizers) with Xenova/clip-vit-base-patch32 quantized ONNX models
(vision 89 MB + text 65 MB). Standalone on purpose: must not import app.*.

    python clip_worker.py <models_dir> <files.json>

Prints one JSON line per file: {"file", "probs": {"2d","3d","live"}, "seconds"}.

Measured 2026-10-13 on this library (8 frames/file, CPU, ~3.8 s/file):
animated (2d+3d) vs live accuracy 91.2% on 80 folder-labelled files; deciding
only when the winning side's probability >= 0.60 covered 85% of files at
98.5% accuracy. 20/20 movie-folder titles classified plausibly (Shrek 2 and
Encanto 3d, FernGully and Scooby-Doo 2d, Cats & Dogs live). VR180 fisheye
frames and photoreal CGI are the weak spots.
"""
import json
import os
import subprocess
import sys
import time

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

FRAMES = 8
SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32).reshape(3, 1, 1)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32).reshape(3, 1, 1)
PROMPTS = {
    "2d": ["a screenshot from an anime", "a frame from a 2D cartoon",
           "hand-drawn animation", "an anime style illustration", "a hentai anime frame"],
    "3d": ["a 3D CGI render", "a frame from a 3D animated video",
           "a computer-generated 3D character", "a video game cutscene", "a Blender 3D animation"],
    "live": ["a photo of a real person", "a frame from a live-action video",
             "a still from a movie filmed with a camera", "amateur video footage",
             "a frame from a real porn video"],
}
CREATE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _session(path):
    """CPU session; OPTIMIZARR_CLIP_THREADS caps threads so classification
    stays gentle next to a running encode."""
    opts = ort.SessionOptions()
    threads = int(os.environ.get("OPTIMIZARR_CLIP_THREADS", "0") or 0)
    if threads > 0:
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
    return ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])


def text_embeddings(models):
    tok = Tokenizer.from_file(f"{models}/tokenizer.json")
    sess = _session(f"{models}/text_model_quantized.onnx")
    classes, embs = [], []
    for cls, prompts in PROMPTS.items():
        vecs = []
        for p in prompts:
            ids = np.array([tok.encode(p).ids], np.int64)
            v = sess.run(None, {"input_ids": ids})[0][0]
            vecs.append(v / np.linalg.norm(v))
        m = np.mean(vecs, axis=0)
        classes.append(cls)
        embs.append(m / np.linalg.norm(m))
    return classes, np.stack(embs)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, creationflags=CREATE)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def frame(path, t):
    vf = f"scale={SIZE}:{SIZE}:force_original_aspect_ratio=increase,crop={SIZE}:{SIZE}"
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-map", "0:V:0",
                        "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, creationflags=CREATE)
    if len(r.stdout) != SIZE * SIZE * 3:
        return None
    img = np.frombuffer(r.stdout, np.uint8).reshape(SIZE, SIZE, 3).transpose(2, 0, 1)
    return (img.astype(np.float32) / 255.0 - MEAN) / STD


def main(models, files_json):
    classes, temb = text_embeddings(models)
    vis = _session(f"{models}/vision_model_quantized.onnx")
    for path in json.load(open(files_json, encoding="utf-8")):
        t0 = time.time()
        d = duration(path)
        if d <= 0:
            print(json.dumps({"file": path, "error": "no duration"}), flush=True)
            continue
        imgs = [f for f in (frame(path, d * (i + 1) / (FRAMES + 1)) for i in range(FRAMES)) if f is not None]
        if not imgs:
            print(json.dumps({"file": path, "error": "no frames"}), flush=True)
            continue
        emb = vis.run(None, {"pixel_values": np.stack(imgs)})[0]
        emb = emb / np.linalg.norm(emb, axis=1, keepdims=True)
        logits = 100.0 * emb @ temb.T
        p = np.exp(logits - logits.max(axis=1, keepdims=True))
        p = p / p.sum(axis=1, keepdims=True)
        probs = p.mean(axis=0)
        print(json.dumps({"file": path, "probs": dict(zip(classes, map(float, probs))),
                          "frames": len(imgs), "seconds": round(time.time() - t0, 2)}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
