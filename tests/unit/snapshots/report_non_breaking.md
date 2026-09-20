# Schema drift report

**Verdict:** NON-BREAKING

- Baseline: `base.parquet` (parquet)
- Current: `cur.parquet` (parquet)
- Changes: 1

## Changes

| Path | Change | Severity | Baseline | Current | Rule | Confidence | Reason |
|---|---|---|---|---|---|---|---|
| `note` | column_added | non_breaking | - | VARCHAR | ADD-NULLABLE | 1.00 | Nullable column added |

## Run metadata

- schema_version: 1
- baseline_source: base.parquet
- change_counts: {'non_breaking': 1}
- current_source: cur.parquet
- generated_at: 2026-01-02T03:04:05+00:00
- latency_ms: 0.0
- llm_calls: 0
- run_id: run-1
