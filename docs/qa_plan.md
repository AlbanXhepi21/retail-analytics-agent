# Quality Assurance and Observability Plan

## How we evaluate before deployment (Req 6 — methodology)

The assignment asks *how* you evaluate the agent and verify that generated reports match user intent. This prototype uses a **layered** approach:

1. **Automated regression (CI):** `pytest` runs on every change — fast, deterministic checks for intent classification, SQL generator contracts, PII masking invariants, and Golden Bucket deduplication. These catch **regressions**, not full semantic quality.
2. **Smoke tests:** `scripts/smoke_test.py` runs canned prompts offline; `--live` exercises Gemini + BigQuery when credentials exist. Use as a **pre-demo or pre-release gate** for end-to-end wiring.
3. **Intent–answer alignment (manual or semi-automated):** Build a **labeled evaluation set** (JSON or spreadsheet): columns for `user_question`, `expected_intent`, `expected_answer_properties` (e.g. “must rank by revenue”, “must not include email”). Score each run **pass/fail** or 1–5. Optionally use an **LLM-as-judge** with a fixed rubric (same model/version pinned) — document its limitations (bias, cost).
4. **Trace-driven QA:** Use `memory/audit_log.jsonl` to replay failures: given `trace_id`, inspect `node_path`, `sql_retry_count`, and `sql_error_present` to distinguish “bad SQL” vs “bad retrieval” vs “out of scope.”
5. **Safety as hard gate:** Any PII leakage or destructive-SQL pattern in tests is a **release blocker** regardless of narrative quality.

What we **do not** claim: a production-grade continuous evaluator or human-in-the-loop labeling pipeline — only the **process** and **hooks** (tests + audit + rubric) that a team would scale.

## Evaluation rubric (Req 6)

| Metric | Definition | Target | Gate Type |
|---|---|---|---|
| Intent Accuracy | Correct intent classification on labeled test set | >= 95% overall, >= 99% for safety intents | Release blocker |
| SQL Contract Pass Rate | Generated SQL satisfies expected query-shape constraints | >= 90% | Release blocker |
| Report Intent Fidelity | Report answers the intended business question (human or LLM-judge on eval set) | >= 90% | Advisory until eval set is automated |
| Safety Pass Rate | PII extraction blocked | 100% | Release blocker |
| Smoke Success Rate | End-to-end smoke checks (offline + optional live) | >= 90% | Advisory blocker |

## Repeatable test strategy

- **Unit/contract tests (`pytest`)**
  - Intent classification (regex + fallback behavior)
  - SQL prompt contract checks
  - PII masking safety
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
- Safety: `pii_masked`, `pii_columns_dropped`, `pii_values_redacted`
- Outcome: `status`, `failure_category`, `error_message`, `latency_ms`

This supports message-level deep dives and postmortems by trace ID.

## Deep-dive workflow

1. Filter failing records in `memory/audit_log.jsonl` by `status=failure`.
2. Group by `failure_category` and `intent` to identify systemic issues.
3. Inspect `trace_id` for `node_path` and slow nodes in `node_latency_ms`.
4. For low-confidence misses, validate whether the interaction should be promoted to Golden Bucket.
5. Add failing prompt to `tests/fixtures/golden_prompts.json` and regression tests.
