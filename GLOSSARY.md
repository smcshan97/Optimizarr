# Optimizarr

Optimizarr re-encodes a media library so every file ends up on its profile: one codec, within set ceilings, at one quality level.

## Library and profiles

**Library**:
The set of media folders Optimizarr manages, each tied to a profile.
_Avoid_: collection, media dir

**Profile**:
What a file should be: target codec, resolution/framerate/bitrate ceilings, audio handling and a quality level.
_Avoid_: preset (that is the encoder's speed setting), template

**Uniformity**:
The goal of Optimizarr: every file in the library on its profile at the profile's quality. Smaller files are a side benefit, never the target.
_Avoid_: optimization (ambiguous — it was read as "make files smaller"), compression

**On-profile**:
A file that already has the profile's codec and sits within all of its ceilings.
_Avoid_: compliant, done, meets targets

**Off-profile**:
A file that misses its profile in at least one way: wrong codec, or above a resolution, framerate or bitrate ceiling.
_Avoid_: non-conforming, unmet

**Ceiling**:
A profile limit a file must not exceed (resolution, framerate, bitrate). Files below a ceiling already meet it; nothing is raised to reach one.
_Avoid_: target resolution, minimum

**Quality level**:
How good the profile wants every file to look, either a fixed encoder quality or a target VMAF score.
_Avoid_: CRF (one encoder's scale), bitrate

## Encode outcomes

**Conversion**:
Re-encoding an off-profile file onto its profile. Kept even when the result is bigger.
_Avoid_: transcode, migration

**Savings encode**:
Re-encoding an on-profile file only to make it smaller. Kept only when it saves at least the minimum savings.
_Avoid_: optimization, shrink

**Kept original**:
The outcome where the new encode was discarded and the source left untouched.
_Avoid_: skipped (a file can be skipped before encoding), rejected, failed

**Skipped**:
The outcome where a file was judged not worth encoding before any full encode ran.
_Avoid_: kept original, ignored

**Quarantined**:
A file taken out of rotation because the source itself is damaged or unreadable.
_Avoid_: failed, broken

## Enhancement

**Upscale plan**:
The decision to enlarge a low-resolution file with an AI upscaler before it is encoded, including engine, model and target height.
_Avoid_: upscale job, enhancement

**3D plan**:
The decision to convert a 2D file to stereoscopic 3D before encoding.
_Avoid_: stereo job

**Content type**:
Whether a file is animated 2D, animated 3D or live action, used to pick an upscaler.
_Avoid_: genre, category

## Upkeep

**Queue check**:
The automatic background pass that keeps the queue matching the files on disk and the current profiles.
_Avoid_: rescan, maintenance button
