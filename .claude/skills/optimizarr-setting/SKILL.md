---
name: optimizarr-setting
description: Add, rename, change the default of, or remove an Optimizarr app setting — use whenever a value is read with db.get_setting or shown on the Settings page, so every layer stays wired.
---

# Optimizarr setting

A setting is a key in the `settings` table, read with `db.get_setting(key, default)`. It crosses five layers, and a half-wired one fails silently (the page shows a default, or a save is dropped). Trace an existing one end to end first — `min_savings_percent` is a clean example: `grep -rn "min_savings_percent\|minSavingsPercent" app web tests`.

## Layers

Every item below is touched, or noted as not applicable, before the change is done.

1. **Reader** — a small function next to its use (e.g. `encoder.min_savings_percent()`) that parses, **clamps**, and falls back on bad values. Default and clamp range here are the single source of truth.
2. **API** — `app/api/system_routes.py`: the GET returns it, the POST accepts it, clamping with the *same* range and default as the reader. Pick the right group (`/settings/encoding`, `/settings/resources`, …).
3. **Page** — `web/templates/index.html`: the input (`id` in camelCase, `min`/`max` matching the clamp) plus the explanation text. Write the text for the user: what happens to their files at this value.
4. **Load/save JS** — the matching `web/static/js/app/*.js` (encoding settings are in `queue.js`): one line filling the input on load, one line in the save body, same default.
5. **Tests** — `tests/test_core.py`: the behaviour at a non-default value (`fresh_db.set_setting(key, ...)`), and the clamp for an out-of-range value when it matters.

When **removing** a setting: delete it from all five layers, then grep the key and the camelCase id across `app web tests` until nothing is left. The stored row in users' databases is harmless; leave it.

## Prefer no setting

The user prefers things that work automatically over knobs and buttons. Before adding a setting, ask whether a fixed, measured default (see `library-stats`) or a rule derived from the profile would do — a setting is for a genuine preference that differs between users, not for a value nobody has measured yet. Background checks run as timed jobs, not manual buttons.
