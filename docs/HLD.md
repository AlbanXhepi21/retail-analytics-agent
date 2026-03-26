# Retail Analytics Agent — High-Level Design (HLD)

**Version:** 1.0
**Stack:** Python · LangGraph · Gemini 2.5 Flash · BigQuery

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Technology Decisions](#3-technology-decisions)
4. [Component Breakdown](#4-component-breakdown)
5. [Data Flow — End-to-End](#5-data-flow--end-to-end)
6. [Requirement Deep-Dives](#6-requirement-deep-dives)
7. [Error Handling & Fallback Strategies](#7-error-handling--fallback-strategies)
8. [Setup Instructions](#8-setup-instructions)
9. [Example Run](#9-example-run)

---

## 1. System Overview

The Retail Analytics Agent is a conversational data analysis assistant for non-technical retail executives. It translates natural language questions into BigQuery SQL, executes them against live transaction data, and returns formatted analyst-grade reports.

**Key design goals:**
- Non-technical users can ask complex questions in plain English
- Answers are informed by historical analyst logic (Golden Bucket), not just raw SQL generation
- PII is never exposed in output regardless of what SQL retrieves
- Destructive operations require explicit confirmation
- The system self-corrects on SQL errors before giving up
- Tone and persona are configurable by non-developers at runtime

---

## 2. Architecture Diagram

See [`docs/architecture.md`](architecture.md) for the full Mermaid diagram.

**High-level flow:**

```
User Message
    → Intent Classifier
        ├── Out of scope        → Reject politely
        ├── Schema question     → Golden Bucket direct answer
        ├── Destructive op      → Confirmation flow (2-step)
        ├── Preference change   → Preference Handler (persist & confirm)
        └── Analysis question
                → Golden Bucket Retriever (find similar past Trios)
                → SQL Generator (Gemini + schema + Trio examples)
                → SQL Executor (BigQuery)
                    ├── Error → Self-correct loop (max 3 retries)
                    └── Success → PII Masker → Report Generator
                                    → Learning Loop (auto-expand Golden Bucket)
                                    → Output
```

---

## 3. Technology Decisions

### LangGraph (agent orchestration)

LangGraph models the agent as an explicit state machine with typed state, conditional edges, and cycles (for the SQL retry loop). This is critical for a production agent because:
- Every node transition is auditable (the `node_path` in state tracks the full execution path)
- Retry loops are first-class constructs — no messy imperative loops
- State is serializable — enables pause/resume for async confirmation flows
- Easy to add new nodes (e.g. a caching layer) without touching existing logic

**Alternative considered:** LangChain AgentExecutor — rejected because it uses a black-box ReAct loop that's harder to control and debug.

### Gemini 2.5 Flash (LLM)

- Fast (~1-2s) and cost-efficient — critical for SQL generation which may retry
- 1M token context window comfortably holds schema + Golden Bucket examples + conversation history
- Free tier (Google AI Studio) sufficient for development and demo

**Temperature:** 0.1 for SQL generation (determinism) and 0.3 for report generation (some creativity in narrative).

**Production alternative:** Gemini 1.5 Pro for complex multi-table queries; Flash for simple lookups. Route based on query complexity detected by the intent classifier.

### BigQuery (database)

Dictated by the assignment. The `BigQueryRunner` class wraps all BQ interactions behind a clean interface, making it swappable. Query safety is enforced at the IAM level (`roles/bigquery.dataViewer`).

### TF-IDF Cosine Similarity (Golden Bucket retrieval — prototype)

Zero infrastructure, pure Python, no API keys. Sufficient for 8-50 Trios.

**Production replacement:** Sentence embeddings (e.g. `text-embedding-004` from Google) stored in Pinecone or pgvector. Same interface — just swap the `GoldenBucketRetriever` internals.

### JSON files (preferences, persona, saved reports — prototype)

Simple, readable, no database needed, easy to demonstrate "non-developer editable."

**Production replacement:**
- User preferences → Firestore or Redis
- Persona config → Firestore (with a simple admin web UI)
- Saved reports → Firestore or PostgreSQL

---

## 4. Component Breakdown

### `agent/state.py` — AgentState

The single source of truth flowing through every node. TypedDict with full type annotations.

| Field | Purpose |
|---|---|
| `intent` | Classified intent (analysis / destructive / schema / preference / out_of_scope) |
| `retrieved_trios` | Matching Golden Bucket entries |
| `generated_sql` | Current SQL candidate |
| `sql_error` | Last BigQuery error (triggers retry) |
| `sql_retry_count` | Guards against infinite retry loops |
| `pii_masked` | Whether masking was applied |
| `pending_confirmation` | Destructive op awaiting user confirmation |
| `learned_trio_id` | ID of auto-learned trio (if learning loop triggered) |
| `preference_updated` | Whether user preferences were changed this turn |
| `trace_id` | Unique ID per interaction for observability |
| `node_path` | List of nodes visited — full execution trace |

### `agent/nodes/intent_classifier.py`

Runs before any LLM call — a cheap regex + keyword gate. This prevents:
- Wasting LLM tokens on out-of-scope questions
- PII extraction attempts ("give me all customer emails")
- Accidental destructive ops without confirmation

Classified intents: `DESTRUCTIVE`, `SCHEMA_QUESTION`, `ANALYSIS`, `PREFERENCE`, `OUT_OF_SCOPE`, `PENDING_CONFIRMATION`.

### `agent/nodes/sql_generator.py`

Builds a prompt containing:
1. Full schema context (all 4 tables, column names, types, rules)
2. Up to 2 Golden Bucket Trios as few-shot examples
3. Previous SQL error (if retrying)
4. The user's question

Critical rules injected into every prompt: never SELECT PII fields, use fully qualified table names, use `sale_price` for revenue, exclude Cancelled/Returned by default.

### `agent/nodes/sql_executor.py`

Wraps BigQuery execution with:
- Retry counter guard — stops at `MAX_RETRIES=3`
- Exponential backoff — `1.5^retry_count` seconds between attempts
- Empty result handling — returns a helpful message, not an error
- Error passthrough — sets `sql_error` in state, which routes back to `sql_generator`

### `agent/nodes/pii_masker.py`

Two-layer protection:
1. **Column name matching** — drops any column named `email`, `phone`, `address`, etc.
2. **Pattern scanning** — regex scan of all string columns for email/phone patterns

Runs after SQL execution and before report generation. Even if the LLM generates SQL that selects `users.email`, the masker strips it. **Fails closed:** on any error, returns empty result rather than exposing PII.

### `agent/nodes/report_generator.py`

Generates the final report using:
- Persona config (tone, instructions) loaded from `config/persona.json` at runtime
- User preferences loaded from `memory/user_prefs.json`
- Golden Bucket report style as a reference
- The masked DataFrame formatted as markdown table or bullets

Fallback: if LLM fails, shows the raw data table with no narrative.

### `agent/nodes/confirmation_handler.py`

Strict 2-step destructive operation flow. Turn 1: preview matching reports and request confirmation. Turn 2: execute or cancel based on user response. Confirmation words: yes/confirm/proceed/ok. Cancel words: no/cancel/abort.

### `agent/nodes/preference_handler.py`

Detects preference-setting messages via regex patterns (e.g., "I prefer bullet points", "switch to table format", "keep it brief"). Parses the requested change into `output_format` and/or `detail_level`, updates `memory/user_prefs.json` for the current user, and confirms the change. Future reports automatically reflect the new preferences.

### `agent/nodes/learning_loop.py`

Runs after every successful report generation. If the Golden Bucket confidence was "low" (< 0.50) and the query succeeded, creates a new Trio from the interaction (question → SQL → report excerpt) and adds it to `data/golden_bucket.json` via `add_trio()`. Tagged with `source: "auto_learned"` for auditability.

### `tools/golden_bucket.py`

TF-IDF cosine similarity retriever. Similarity thresholds:
- `>= 0.70` → High confidence → Use Trio SQL as strong reference
- `0.50–0.69` → Medium confidence → Use as partial context
- `< 0.50` → Low confidence → Pure LLM generation from schema

---

## 5. Data Flow — End-to-End

**Example: "Who are our top customers by spend?"**

```
1. main.py receives input → builds AgentState with trace_id

2. intent_classifier:
   → matches "customers", "spend" keywords → intent = ANALYSIS

3. golden_bucket_retriever:
   → TF-IDF similarity against 8 Trios
   → trio_001 ("top 10 customers by total spend") scores ~0.87
   → returns [trio_001, trio_005]

4. sql_generator:
   → builds prompt with schema + trio_001 SQL as few-shot example
   → Gemini generates SQL (joins orders + order_items + users)

5. sql_executor:
   → runs SQL against BigQuery
   → returns DataFrame: 10 rows × 5 columns, no error

6. pii_masker:
   → scans columns: id, first_name, last_name, total_spend, total_orders
   → no PII column names or patterns detected

7. report_generator:
   → loads persona.json (tone: professional)
   → loads user_prefs for current user
   → formats DataFrame as markdown table
   → calls Gemini for narrative + insights

8. main.py:
   → prints formatted report to terminal
   → logs trace_id, node_path, latency
```

---

## 6. Requirement Deep-Dives

### Req 1 — Hybrid Intelligence

The agent uses two sources of intelligence in every query:

**Source 1 — Schema context:** Full table structure injected into every SQL generation prompt.

**Source 2 — Golden Bucket:** Before generating SQL, the agent retrieves the most similar past Trios. These become few-shot examples, teaching the LLM analyst-verified JOINs, filters, and report styles.

**Updating the Golden Bucket over time:**

1. **Human seeding (initial):** Analysts write 20-50 high-quality Trios covering core use cases.
2. **Automatic candidate generation:** When a user question has no good match (similarity < 0.50) AND the agent successfully answers it, the system creates a candidate Trio in a "pending review" queue.
3. **Human review & promotion:** An analyst reviews candidates weekly. Good ones are promoted to the Golden Bucket.
4. **Feedback-based prioritization:** High-rated interactions (user thumbs up) skip to front of review queue.

The `GoldenBucketRetriever.add_trio()` method provides the programmatic interface for adding new Trios.

### Req 2 — Safety & PII Masking

Three layers of protection:

1. **Intent classifier gate:** Catches direct PII extraction attempts ("give me customer emails") before any SQL is generated.
2. **SQL generator prompt rules:** System prompt instructs: "NEVER select email, phone, or any PII fields."
3. **PII masker node (hard guarantee):** Drops PII-named columns, regex-scans remaining strings. **Cannot be bypassed.** Fails closed on error — returns empty result rather than expose PII.

### Req 3 — High-Stakes Oversight

2-step confirmation flow:
- Turn 1: Preview matching reports, list what will be deleted, request `confirm`.
- Turn 2: Execute deletion only on explicit confirmation. Cancel on `no`/`cancel`.
- State persistence: `pending_confirmation` dict is carried between conversation turns in `main.py`.
- Audit trail: Every deletion is logged with target, count, and timestamp.

### Req 4 — Continuous Improvement (Learning Loop)

**User level — Preference Learning:**

The agent detects and persists user preferences from natural conversation:

1. **Regex detection:** The intent classifier recognizes preference-setting messages ("I prefer bullet points", "switch to table format", "keep it brief", "more detail") via `PREFERENCE_PATTERNS` and routes to the `preference_handler` node.
2. **Persistence:** `preference_handler` parses the requested change, updates `memory/user_prefs.json` for the current `user_id`, and confirms the change.
3. **Runtime application:** The report generator loads preferences fresh on every request. Both `output_format` (table vs bullets) and `detail_level` (summary, standard, detailed) affect output:
   - `detail_level` adjusts max rows shown (summary: 5, standard: 20, detailed: up to 50) and the LLM prompt depth.
   - `output_format` switches between markdown table and bullet-point formatting.
4. **Pre-seeded defaults:** New users inherit from `default` profile. Managers can start with `--user manager_a` to load pre-configured preferences.

**System level — Golden Bucket Auto-Expansion:**

The `learning_loop` node runs after every successful report generation:

1. **Trigger condition:** The interaction had `golden_bucket_confidence == "low"` (similarity < 0.50), meaning no good existing Trio matched, AND the SQL executed successfully with results.
2. **Trio creation:** The node creates a new Trio (question → SQL → report excerpt) with `source: "auto_learned"` and a timestamped ID.
3. **Persistence:** Calls `GoldenBucketRetriever.add_trio()` which persists to `data/golden_bucket.json` and rebuilds the TF-IDF index immediately.
4. **Effect:** Future similar questions will match the learned Trio with high confidence, producing better SQL without retries.

```
report_generator → learning_loop → END
                      ↓
              (if low confidence + success)
                      ↓
              add_trio() → golden_bucket.json reindexed
```

Production enhancement: add a `pending_review` status field to auto-learned trios and require human approval before they influence retrieval scoring.

### Req 5 — Resilience & Graceful Error Handling

SQL self-correction loop implemented as a LangGraph cycle:

```
sql_generator → sql_executor
                    ↓ error
              sql_generator (error injected into prompt)
                    ↓ error
              sql_generator (attempt 3)
                    ↓ error
              graceful exit message
```

Max retries: 3. Backoff: `1.5^n` seconds. Error types handled: SQL syntax, table not found, empty results, LLM API failure, rate limits, quota exhaustion, PII masker failure (fail closed), report generation failure (raw data fallback).

Cost inflation prevention: max 3 LLM calls for SQL per query, cheapest model for SQL, TF-IDF retrieval costs zero LLM tokens, intent classification uses regex (zero cost).

### Req 6 — Quality Assurance

**Pre-deployment strategy (implemented + documented in `docs/qa_plan.md`):**

1. **Deterministic unit/contract tests (pytest):**
   - intent classification
   - SQL prompt contract assertions
   - PII masking and destructive confirmation safety checks
   - preference handling + learning loop regressions
   - Golden Bucket dedup regression
2. **Golden prompt fixture set:** canonical prompts and expected behavior in `tests/fixtures/golden_prompts.json`.
3. **Hybrid smoke tests:** offline smoke checks always + optional live BigQuery/Gemini smoke checks (`scripts/smoke_test.py --live`).
4. **Release gates:** block release if any safety regression fails or threshold metrics are below target.

**Evaluation rubric and thresholds:**

| Metric | Target |
|---|---|
| Intent Accuracy | >= 95% overall; >= 99% on safety intents |
| SQL Contract Pass Rate | >= 90% |
| Report Intent Fidelity | >= 90% |
| Safety Pass Rate | 100% |
| Preference/Learning Regressions | 100% pass |
| Smoke Success Rate | >= 90% |

### Req 7 — Observability

The agent writes one structured JSON event per request into `memory/audit_log.jsonl`.

**Tracked fields per interaction:**

| Metric | How Tracked |
|---|---|
| `trace_id` | UUID per interaction, included in logs and audit events |
| `node_path` | Ordered list of visited nodes |
| `node_latency_ms` | Per-node latency map captured by graph wrappers |
| `latency_ms` | End-to-end request duration |
| `sql_retry_count` | SQL retry count |
| `golden_bucket_score` / `golden_bucket_confidence` | Knowledge coverage signal |
| `retrieved_trio_ids` | Golden Bucket retrieval provenance |
| `pii_masked` / `pii_columns_dropped` / `pii_values_redacted` | Safety outcomes |
| `learned_trio_id` | Learning loop output signal |
| `status` / `failure_category` / `error_message` | Failure diagnosis fields |

**Deep-dive workflow:**
1. Filter failed events by `status=failure`.
2. Group by `failure_category`, `intent`, and `node_path`.
3. Trace slow nodes using `node_latency_ms`.
4. Add recurring failures to fixtures and regression tests.

**Production extension:** export these events to LangSmith/Cloud Logging and create alerting on retry spikes, failure-rate spikes, and safety anomalies.

### Req 8 — Agility / Persona Management

`config/persona.json` controls tone, instructions, report headers, sign-off text, and max rows. The report generator loads this file at runtime on every request — no restart needed.

**Production:** Store in Firestore with a simple internal web form. Changes take effect on the next request. Version field enables rollback.

---

## 7. Error Handling & Fallback Strategies

| Scenario | Detection | Response |
|---|---|---|
| SQL syntax error | BigQuery exception | Inject error into next prompt, retry up to 3x |
| SQL returns empty | `df.empty == True` | "No data found" message with suggestions |
| Max retries exceeded | `retry_count >= 3` | "Unable to generate query, try rephrasing" |
| LLM rate limit (429) | HTTP 429 from Gemini API | Exponential backoff, retry up to 3x |
| LLM API down | Network/timeout exception | Graceful error message, no crash |
| BigQuery quota exceeded | BQ exception with quota message | "Service temporarily unavailable" message |
| PII masker failure | Exception in masker node | **Fail closed:** empty result, log error |
| Report generation failure | Exception in report node | Fallback to raw markdown table |
| No Golden Bucket match | Similarity < 0.50 | Pure LLM generation from schema only |
| Destructive op without confirmation | `pending_confirmation` check | Preview shown, execution blocked |
| Out-of-scope question | Intent classifier | Polite rejection with example questions |

---

## 8. Setup Instructions

### Prerequisites
- Python 3.11+
- Google Cloud account (free tier sufficient)
- Google AI Studio account (for Gemini API key)

### Step 1 — Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/retail-analytics-agent
cd retail-analytics-agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 2 — Configure environment

```bash
cp .env.example .env
# Edit .env:
#   GOOGLE_API_KEY   — from https://aistudio.google.com/
#   GCP_PROJECT_ID   — your GCP project ID
```

### Step 3 — Configure BigQuery access

```bash
gcloud auth application-default login
```

The agent uses `bigquery-public-data.thelook_ecommerce` which is publicly accessible.

### Step 4 — Run

```bash
python main.py
# With a specific user profile:
python main.py --user manager_a
```

**Docker (optional):** see `README.md` — `docker compose build` then `docker compose run --rm agent`, with BigQuery credentials via **either** `docker-compose.gcloud.yml` (host ADC) **or** `docker-compose.sa.yml` (service account JSON).

### Step 5 — Debug mode

```bash
LOG_LEVEL=DEBUG python main.py
# Shows: node_path, trace_id, retry_count, latency, pii_masked
```

---

## 9. Example Run

```
╔══════════════════════════════════════════════════════╗
║        Retail Analytics Agent  v1.0                  ║
║        Powered by Gemini 2.5 Flash + BigQuery        ║
╚══════════════════════════════════════════════════════╝

You: Who are the top 10 customers by total spend?

────────────────────────────────────────────────────────
  📊 Agent Response
────────────────────────────────────────────────────────

## Retail Analytics Report

**Your Question:** _Who are the top 10 customers by total spend?_

### Analysis
The top 10 customers account for approximately 8% of total revenue,
with an average lifetime value above $3,000. The leading customer
shows high engagement with 20+ orders, suggesting loyalty rather
than one-time large purchases.

**Recommendation:** Prioritize retention outreach for these accounts.

### Data
| id    | first_name | last_name | total_spend | total_orders |
|-------|------------|-----------|-------------|--------------|
| 48291 | Sarah      | Mitchell  | 4,821.50    | 23           |
| 12847 | James      | Rodriguez | 4,102.75    | 18           |
| ...   | ...        | ...       | ...         | ...          |

---
_Data sourced from live transaction database._

────────────────────────────────────────────────────────

You: Delete all reports mentioning Acme Corp

────────────────────────────────────────────────────────
  📊 Agent Response
────────────────────────────────────────────────────────

⚠️  **CONFIRMATION REQUIRED**

You have requested to delete **2 report(s)** matching: _Acme Corp_

Reports that will be permanently deleted:
  - **Q1 2024 Revenue Report - Client Acme Corp** (ID: report_001)
  - **Top Customers Analysis - Including Client Acme Corp** (ID: report_002)

**This action cannot be undone.**

Type `confirm` to proceed or `cancel` to abort.

────────────────────────────────────────────────────────

You: confirm

────────────────────────────────────────────────────────
  📊 Agent Response
────────────────────────────────────────────────────────

🗑️  **Deletion Complete**

Successfully deleted **2 report(s)** matching _Acme Corp_.

Remaining reports in library: **2**

_This action has been logged for compliance purposes._

────────────────────────────────────────────────────────
```
