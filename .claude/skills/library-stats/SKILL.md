---
name: library-stats
description: Measure Optimizarr's real library from its live database — use when a decision, threshold or code comment needs numbers (growth rates, kept-original reasons, codec mix, target-quality results, encode speed) instead of guesses.
---

# Library stats

The live database is `data/optimizarr.db` (SQLite; gitignored). The app may be running against it, so open it **read-only** every time — a write here edits the user's real queue:

```bash
PYTHONIOENCODING=utf-8 "/c/Python314/python.exe" - <<'EOF'
import sqlite3
c = sqlite3.connect('file:data/optimizarr.db?mode=ro', uri=True)
c.row_factory = sqlite3.Row
# queries here
EOF
```

Get columns from the database itself (`pragma table_info(queue)`), not from memory — migrations add them often. The tables that answer most questions:

- `queue` — one row per file. `status` (pending / processing / completed / kept_original / failed / quarantined / cancelled), `current_specs` and `target_specs` (JSON), `file_size_bytes`, `error_message` (the human reason a file was kept/skipped), `tq_quality` / `tq_vmaf` / `tq_xpsnr`, `content_type`, `notice`.
- `history` — one row per finished encode: `original_size_bytes`, `new_size_bytes`, `encoding_time_seconds`, `codec`, `duration_seconds`, `preprocessed` (1 = upscale/3D).
- `profiles` — what each profile asks for (`codec`, `quality`, `target_vmaf`, `max_bitrate_kbps`, …).
- `settings` — key/value app settings.

Useful starting queries:

```sql
-- why files were kept as the original: messages start with per-file
-- numbers, so group on the rule after "source " (or the skip reason)
SELECT CASE WHEN instr(error_message, '; source ') > 0
            THEN substr(error_message, instr(error_message, '; source ') + 2)
            ELSE substr(error_message, 1, 40) END AS reason, count(*)
  FROM queue WHERE status = 'kept_original' GROUP BY reason ORDER BY 2 DESC;

-- size change of plain encodes, by output codec
SELECT codec, count(*), avg(new_size_bytes * 1.0 / original_size_bytes) AS ratio,
       sum(new_size_bytes > original_size_bytes) AS grew
  FROM history WHERE preprocessed = 0 AND original_size_bytes > 0 GROUP BY codec;

-- source codec mix of the pending queue
SELECT json_extract(current_specs, '$.codec') AS codec, count(*),
       avg(json_extract(current_specs, '$.bit_rate')) / 1e6 AS avg_mbps
  FROM queue WHERE status = 'pending' GROUP BY codec ORDER BY 2 DESC;
```

## Reporting

Give the query alongside the result, with the row count and date range it covers, so a number quoted in a commit or code comment ("measured on this library: …") can be re-run later. Say when a sample is small (the history table can be thin after a reset).
