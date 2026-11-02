---
name: encode-experiment
description: Benchmark Optimizarr encode settings on real files — use when choosing a preset, quality/CRF, encoder, film grain or bitrate cap, or when checking a size/speed/quality claim, by encoding samples and scoring VMAF and XPSNR.
---

# Encode experiment

Measure on the user's own files, through the app's own code paths, so results match what the encoder will really do.

## Steps

1. **Pick sources.** 3–6 real files that span the question (e.g. lean H.264, Blu-ray remux, animation, grainy film). Find candidates with the `library-stats` skill; say why each was chosen.
2. **Cut samples** with `app.target_quality.cut_samples(source, work_dir, duration, register)` into a scratchpad folder — never next to the media. These stream-copy keyframe-aligned 10 s clips that double as exact VMAF references. `register` can be `lambda p: None`.
3. **Encode each variant** with the real command: build an `EncodingJob` the way `TestProfileConformance._cmd` in `tests/test_core.py` does (profile dict + `job.build_command(input_override=..., output_override=..., quality_override=..., video_only=True)`), change only the variable under test, and time each run.
4. **Score** with `app.target_quality.quality_scores(out, ref, width, height, register)` → (VMAF, XPSNR dB). It pairs frames by order on purpose (see its docstring).
5. **Report** one table per source: variant · size % of sample · encode time · VMAF · XPSNR, then the recommendation and what it trades. Note the HandBrake/SVT version (`app.handbrake.handbrake_version()`) and CPU, since presets shift between versions.

Done when every variant has been run on every source and the table has no gaps (a failed encode is a row saying so).

## Reading results

- Compare at **equal quality**, not equal CRF: a faster preset at the same CRF lands as a bigger file, so either match VMAF via `target_quality.search` or report size and VMAF together.
- A big VMAF/XPSNR disagreement suggests sharpening or filtering is inflating VMAF; flag it.
- Samples are video-only; audio isn't part of the size figure.

Clean up the scratchpad samples afterwards — they are multi-GB for remuxes.
