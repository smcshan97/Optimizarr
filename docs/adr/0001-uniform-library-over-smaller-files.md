# Uniform library over smaller files

Optimizarr's goal is uniformity (every file on its profile's codec, ceilings and quality level), not the smallest possible files. So a conversion is kept even when it comes out bigger, and its quality is never lowered to keep it the same size; the profile's bitrate ceiling is the only size bound, and only an implausible blow-up (more than 3x the source) is discarded as a broken encode. Minimum savings applies only to savings encodes of files already on-profile.

## Considered Options

- **"Don't grow" (rejected).** Lowering a conversion's quality until it was no bigger than the source, plus a 10% growth allowance. It left the leanest sources (low-bitrate H.264, the median queued file) either unconverted or encoded visibly worse than the rest of the library, which defeats the point.

## Consequences

Converting a library of lean sources can use more disk, not less. Users who need a size bound set the profile's bitrate ceiling. Changing this rule means bumping `CONFORMANCE_POLICY` so files kept under the old rule are retried (see the `encode-policy` skill).
