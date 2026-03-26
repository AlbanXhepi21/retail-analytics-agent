# Quality Assurance and Observability Plan

## Evaluation rubric (Req 6)

| Metric | Definition | Target | Gate Type |
|---|---|---|---|
| Intent Accuracy | Correct intent classification on labeled test set | >= 95% overall, >= 99% for safety intents | Release blocker |
| SQL Contract Pass Rate | Generated SQL satisfies expected query-shape constraints | >= 90% | Release blocker |
| Report Intent Fidelity | Report answers the intended business question type | >= 90% | Release blocker |
| Safety Pass Rate | PII extraction blocked, destructive ops require confirmation | 100% | Release blocker |
| Regression Stability | Preference + learning-loop regression suite | 100% pass | Release blocker |
| Smoke Success Rate | End-to-end smoke checks (offline + optional live) | >= 90% | Advisory blocker |

## Repeatable test strategy

- **Unit/contract tests (`pytest`)**
  - Intent classification (regex + fallback behavior)
  - SQL prompt contract checks
  - PII masking and destructive confirmation logic
  - Preference persistence regression
  - Learning loop trigger regression
  - Golden Bucket dedup behavior
- **Fixtures**
  - `tests/fixtures/golden_prompts.json` for canonical prompt coverage
- **Smoke tests**
  - `python scripts/smoke_test.py` (offline)
  - `python scripts/smoke_test.py --live` (BigQuery + Gemini)

## Observability model (Req 7)

Each request emits one structured JSONL event to `memory/audit_log.jsonl`:

- Request identity: `timestamp_utc`, `trace_id`, `user_id`, `prompt_hash`
- Routing: `intent`, `node_path`, `node_latency_ms`
- Retrieval: `golden_bucket_score`, `golden_bucket_confidence`, `retrieved_trio_ids`
- SQL/execution: `sql_retry_count`, `sql_error_present`
- Safety: `pii_masked`, `pii_columns_dropped`, `pii_values_redacted`, `pending_confirmation`
- Learning: `learned_trio_id`, `preference_updated`
- Outcome: `status`, `failure_category`, `error_message`, `latency_ms`

This supports message-level deep dives and postmortems by trace ID.

## Deep-dive workflow

1. Filter failing records in `memory/audit_log.jsonl` by `status=failure`.
2. Group by `failure_category` and `intent` to identify systemic issues.
3. Inspect `trace_id` for `node_path` and slow nodes in `node_latency_ms`.
4. For low-confidence misses, validate whether the interaction should be promoted to Golden Bucket.
5. Add failing prompt to `tests/fixtures/golden_prompts.json` and regression tests.
