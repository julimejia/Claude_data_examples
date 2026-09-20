# Schema drift report

**Verdict:** BREAKING

- Baseline: `base.parquet` (parquet)
- Current: `cur.parquet` (parquet)
- Changes: 1

## Changes

| Path | Change | Severity | Baseline | Current | Rule | Confidence | Reason |
|---|---|---|---|---|---|---|---|
| `id` | type_changed | breaking | BIGINT NOT NULL | VARCHAR NOT NULL | TYPE-CATEGORY | 1.00 | Type category changed: BIGINT -> VARCHAR |

## Run metadata

- schema_version: 1
- baseline_source: base.parquet
- change_counts: {'breaking': 1}
- current_source: cur.parquet
- generated_at: 2026-01-02T03:04:05+00:00
- latency_ms: 0.0
- llm_calls: 0
- run_id: run-1
