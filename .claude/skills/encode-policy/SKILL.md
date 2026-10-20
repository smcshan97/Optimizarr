---
name: encode-policy
description: Optimizarr keep/reject rules — use before changing when an encode replaces its source, gets skipped, is kept as the original, or is re-queued (min savings, conversions, low-bpp skip, target-quality size checks, conformance sweep).
---

# Encode policy

## The goal

**Uniformity**: every file in the library ends on its profile — one codec, within the resolution/framerate/bitrate ceilings, at the profile's quality level. Smaller files are a side benefit, never the target. Judge every rule change against this goal; when a rule would leave a file off-profile or encode it worse than its neighbours just to save space, the rule is wrong.

Two kinds of source, two questions:

- **Off-profile** (`scanner.unmet_targets` non-empty): convert it at the profile's quality, whatever the size. Bound size with the profile's `max_bitrate_kbps`, not with growth relative to the source. Only `encoder.CONVERSION_SANITY_RATIO` (an implausible blow-up = broken encode) rejects one.
- **On-profile** (`unmet_targets` empty): already uniform, so the only question is savings. Replace it only when the encode is ≥ `min_savings_percent` smaller; the low-bits-per-pixel pre-flight skip applies here only.

Upscale/3D jobs (`self._preprocessed`) are exempt from size rules — growth is their point.

## Where the rules live

Find each by grepping the name; read the surrounding comment, which records the measured reason.

- `scanner.unmet_targets`, `profile_targets`, `BITRATE_HEADROOM` — what "off-profile" means.
- `encoder.py` — pre-flight low-bpp skip (`_low_bitrate_skip_reason`), the target-quality skip after the search, and the post-encode "Keep the original if the encode isn't worth it" block.
- `target_quality.search(convert=...)` — conversions never stop the search early for size.
- `queue_maintenance.recheck_finished` + `CONFORMANCE_POLICY` — re-queues finished files that are off-profile.

## Checklist for a rule change

Work through every item; the change is done when each is ticked or explicitly not applicable.

1. Every place in "Where the rules live" that encodes the old rule is updated (grep the old threshold/setting name across `app/`, `web/`, `tests/`).
2. **Bump `CONFORMANCE_POLICY`** in `queue_maintenance.py` (with a one-line note of what changed) when the change would now accept files that were kept as the original before — otherwise those files are never retried.
3. User-facing text matches: the explanatory paragraphs in `web/templates/index.html` (Settings → encoding rules) and the `error_message` strings shown on kept/skipped rows.
4. Any setting added or removed is wired through all its layers — follow the `optimizarr-setting` skill.
5. Tests cover both sides of each threshold (kept vs. rejected), in `tests/test_core.py` near `test_conversion_kept_when_bigger` / `TestConversionSearch`.
6. Numbers quoted in comments ("measured on this library…") come from real data — use the `library-stats` skill rather than estimating.
