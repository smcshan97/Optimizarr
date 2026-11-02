# Optimizarr

Optimizarr's goal is a **uniform library**, not smaller files — see `docs/adr/0001-uniform-library-over-smaller-files.md`. Domain terms are in `GLOSSARY.md`; use them, and read both before changing encode, queue or upscale behaviour.

## Skill triggers

Reach for these project skills (`.claude/skills/`) on your own when the situation fits — the user won't name them. Several can apply to one task.

| Situation | Skill |
|---|---|
| Fixing a bug or failure | `anthropic-skills:diagnosing-bugs` to find the cause, then `tdd`: failing test first |
| Building a feature with testable behaviour | `tdd` |
| Changing when an encode is kept, skipped, replaced or re-queued | `encode-policy` |
| Adding, removing or changing a setting | `optimizarr-setting` |
| A threshold, default or claim needs real numbers | `library-stats` |
| Choosing a preset, quality, encoder, grain or bitrate cap | `encode-experiment` |
| A goal, policy or feature is underspecified | `grill-with-docs` (skip for concrete, small requests) |
| A term is fuzzy, or the user corrects what something means | `domain-modeling`: update `GLOSSARY.md` / add an ADR in the same turn |
| Refactor/cleanup/architecture asked for, or a change sprawled across a hot-spot file | `improve-codebase-architecture` (offer after the task), `codebase-design` for interface shape |
| An idea should be tried cheaply before committing | `prototype` |
| Checking a UI/API change in the real app | `optimizarr-run` |
| Committing, pushing, opening a PR | `optimizarr-ship` |
| Wrap-up after the user corrected a misunderstanding or a mistake repeated | `retro` (top 1–3 suggestions, applied on a yes) |

`.claude/hooks/skill_triggers.py` adds a reminder to the prompt when its wording matches one of these rows; the table still applies when it doesn't.
