"""
RTX Video Super Resolution frame pump — runs INSIDE the isolated VSR runtime
(%LOCALAPPDATA%/Optimizarr/vsr/venv: nvidia-vfx + cupy + numpy), not in the
app's interpreter. Standalone on purpose: it must not import app.*.

    python vsr_worker.py job.json

job.json: {"decode_cmd": [...], "encode_cmd": [...], "in_w", "in_h",
           "out_w", "out_h", "quality", "total_frames"}

decode_cmd must write raw gbrpf32le frames (in_w x in_h) to stdout;
encode_cmd must read raw gbrpf32le frames (out_w x out_h) from stdin. ffmpeg
does every colour/format conversion, so no per-pixel Python work happens
here: planes are only reordered (GBR <-> RGB) and copied to/from the GPU.

Prints "progress <done> <total>" lines; exits non-zero on failure.
"""
import json
import subprocess
import sys

import numpy as np


def main(job_path: str) -> int:
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)

    import cupy as cp
    from nvvfx import VideoSuperRes

    in_w, in_h = job["in_w"], job["in_h"]
    out_w, out_h = job["out_w"], job["out_h"]
    total = int(job.get("total_frames") or 0)
    in_bytes = in_w * in_h * 3 * 4          # 3 float32 planes
    plane_out = out_w * out_h

    vsr = VideoSuperRes(quality=getattr(VideoSuperRes.QualityLevel, job["quality"]))
    vsr.output_width, vsr.output_height = out_w, out_h
    vsr.load()

    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    dec = subprocess.Popen(job["decode_cmd"], stdout=subprocess.PIPE,
                           stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           bufsize=in_bytes * 2, creationflags=creation)
    enc = subprocess.Popen(job["encode_cmd"], stdin=subprocess.PIPE,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           bufsize=plane_out * 12, creationflags=creation)
    done = 0
    try:
        while True:
            buf = dec.stdout.read(in_bytes)
            if not buf:
                break
            if len(buf) < in_bytes:
                print(f"short frame ({len(buf)} of {in_bytes} bytes)", file=sys.stderr)
                break
            gbr = np.frombuffer(buf, dtype=np.float32).reshape(3, in_h, in_w)
            rgb = np.ascontiguousarray(gbr[[2, 0, 1]])
            result = vsr.run(cp.asarray(rgb))
            out = cp.asnumpy(cp.from_dlpack(result.image))  # (3, out_h, out_w) RGB
            # back to G, B, R plane order for gbrpf32le
            enc.stdin.write(out[1].tobytes())
            enc.stdin.write(out[2].tobytes())
            enc.stdin.write(out[0].tobytes())
            done += 1
            if done % 24 == 0:
                print(f"progress {done} {total}", flush=True)
    except BrokenPipeError:
        err = enc.stderr.read().decode("utf-8", "replace")[-800:]
        print(f"encoder exited early: {err}", file=sys.stderr)
        return 1
    finally:
        try:
            enc.stdin.close()
        except Exception:
            pass
    dec_rc = dec.wait()
    enc_rc = enc.wait()
    print(f"progress {done} {total}", flush=True)
    if dec_rc != 0 or enc_rc != 0 or done == 0:
        err = enc.stderr.read().decode("utf-8", "replace")[-800:] if enc.stderr else ""
        print(f"decoder rc={dec_rc} encoder rc={enc_rc} frames={done} {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
