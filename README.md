# Retail Analytics Agent

A conversational data analysis assistant for retail executives, built with **LangGraph**, **Gemini 2.5 Flash**, and **BigQuery**.

Non-technical managers can ask plain English questions and receive analyst-grade reports — powered by a hybrid of SQL generation and a curated "Golden Knowledge" base of expert analyst patterns.

---

## Features

| Capability | Implementation |
|---|---|
| Natural language → SQL | Gemini 2.5 Flash with schema context |
| Hybrid Intelligence | Golden Bucket (expert Trio retrieval via TF-IDF) |
| PII Masking | Column + pattern-based redaction (always-on) |
| Destructive Op Safety | 2-step confirmation flow for report deletion |
| Self-Correction | SQL retry loop (max 3 attempts, exponential backoff) |
| User Preference Learning | Detects & persists format preferences from conversation |
| System Learning Loop | Auto-expands Golden Bucket from successful interactions |
| Persona Management | Runtime-editable tone/instructions (no redeploy) |
| Observability | trace_id, node_path, per-node latency, structured audit log |

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/retail-analytics-agent
cd retail-analytics-agent
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env and fill in:
#   GOOGLE_API_KEY   — from https://aistudio.google.com/
#   GCP_PROJECT_ID   — your GCP project ID
```

### 3. Authenticate with GCP

```bash
gcloud auth application-default login
```

### 4. Run

```bash
python main.py

# With a specific user profile (affects output format):
python main.py --user manager_a
python main.py --user manager_b

# With debug output (shows node path, trace, retries):
LOG_LEVEL=DEBUG python main.py
```

### Docker (optional — easy for reviewers)

Prerequisites: [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2.

**Important:** `docker compose run --rm agent` uses only `.env` (Gemini works via `GOOGLE_API_KEY`). **BigQuery** needs GCP credentials **inside** the container — use step 2 (gcloud mount or service account). Otherwise you will see “default credentials were not found”.

1. **Configure** — same as above: `cp .env.example .env` and set `GOOGLE_API_KEY` and `GCP_PROJECT_ID`.

2. **BigQuery authentication inside the container** — pick **one**:

   | Method | Command |
   |--------|---------|
   | **A. gcloud ADC** (you already ran `gcloud auth application-default login` on the host) | `docker compose -f docker-compose.yml -f docker-compose.gcloud.yml run --rm agent` |
   | **B. Service account JSON** — download a key to `./gcp-sa.json` (file is gitignored) | `docker compose -f docker-compose.yml -f docker-compose.sa.yml run --rm agent` |

   On **Windows**, if the gcloud mount fails, set `GCLOUD_CONFIG` in `.env` to your user gcloud folder (see `.env.example`).

3. **Run the CLI** (interactive — use `-it`):

   ```bash
   docker compose build
   docker compose run --rm agent
   ```

   With a specific profile:

   ```bash
   docker compose run --rm agent python main.py --user manager_a
   ```

4. **Smoke test in Docker**:

   ```bash
   docker compose run --rm agent python scripts/smoke_test.py
   docker compose run --rm agent python scripts/smoke_test.py --live
   ```

   For `--live` / full analysis, use compose **+** `docker-compose.gcloud.yml` or `docker-compose.sa.yml` so BigQuery credentials exist in the container.

---

## Example Questions

```
Who are the top 10 customers by total spend?
What is the monthly revenue trend this year?
Which product categories generate the most revenue?
What is the order status breakdown?
Which age group spends the most?
What tables are available in the database?

# Preference changes (persisted per user):
I prefer bullet points
Switch to table format
Keep it brief
More detail

# Destructive operations (requires confirmation):
Delete all reports mentioning Acme Corp
```

---

## Project Structure

```
retail-analytics-agent/
├── agent/
│   ├── graph.py              # LangGraph state machine
│   ├── state.py              # Typed state schema
│   └── nodes/
│       ├── intent_classifier.py
│       ├── golden_bucket_retriever.py
│       ├── schema_handler.py
│       ├── sql_generator.py
│       ├── sql_executor.py
│       ├── pii_masker.py
│       ├── report_generator.py
│       ├── confirmation_handler.py
│       ├── preference_handler.py   # Detect & persist user prefs
│       └── learning_loop.py        # Auto-expand Golden Bucket
├── tools/
│   ├── bq_client.py          # BigQuery runner + schema context
│   └── golden_bucket.py      # TF-IDF Trio retrieval
├── config/
│   └── persona.json          # Editable agent persona (no redeploy)
├── data/
│   ├── golden_bucket.json    # Expert analyst Trios
│   └── saved_reports.json    # Reports library (for deletion demo)
├── memory/
│   ├── user_prefs.json       # Per-user format preferences
│   ├── chat_history.json     # Short-term chat (per user)
│   └── audit_log.jsonl       # Structured observability events
├── docs/
│   ├── HLD.md                # Full technical design document
│   ├── architecture.md       # Mermaid architecture diagram
│   └── qa_plan.md            # QA metrics & test strategy
├── scripts/
│   └── smoke_test.py         # Hybrid smoke checks
├── tests/                    # pytest suite
├── Dockerfile
├── docker-compose.yml        # Base image + volumes
├── docker-compose.gcloud.yml # Optional: mount host gcloud ADC
├── docker-compose.sa.yml     # Optional: mount service account JSON
├── main.py                   # CLI entry point
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Documentation

- **[Full HLD & Technical Design](docs/HLD.md)** — Architecture decisions, data flow, all 8 requirements
- **[Architecture Diagram](docs/architecture.md)** — Mermaid flowchart
- **[QA + Observability Plan](docs/qa_plan.md)** — Metrics, gates, test strategy, and deep-dive flow

---

## Quality Assurance Commands

```bash
# Deterministic test suite (offline)
pytest -q

# Smoke tests (offline)
python scripts/smoke_test.py

# Smoke tests with live BigQuery + Gemini checks
python scripts/smoke_test.py --live
```

The smoke script loads `.env` from the repo root (same as `main.py`). If `GOOGLE_API_KEY` and `GCP_PROJECT_ID` are not set, the **analysis** case is **skipped** (exit code 0) so you can run quick checks without credentials; add both keys to `.env` to exercise the full analysis path.

The agent also writes structured request-level audit events to:

```bash
memory/audit_log.jsonl
```

Use these records for postmortems and trend dashboards (intent drift, retry spikes, slow nodes, safety events).

---

## Prototype Requirements Implemented

The prototype implements **3 of the optional requirements**:

1. **Safety & PII Masking** — Three-layer PII protection (intent gate + prompt rules + post-query masker). The masker drops PII columns and regex-scans all string values. Fails closed on error.

2. **High-Stakes Oversight** — 2-step confirmation flow for destructive operations. No deletion ever executes on the first message. Supports confirm/cancel words and logs all deletions for audit.

3. **Resilience & Graceful Error Handling** — SQL self-correction loop (max 3 retries with exponential backoff). Handles syntax errors, empty results, LLM failures, and BQ transient errors. PII masker fails closed. Report generator falls back to raw data on LLM failure.

---

## Changing the Agent's Tone

Edit `config/persona.json` — no restart or redeployment needed:

```json
{
  "tone": "casual",
  "instructions": "Keep it short and punchy. Executives are busy.",
  "max_rows_in_report": 10
}
```

---

## Dataset

Uses `bigquery-public-data.thelook_ecommerce` — a public Google dataset.
Tables: `orders`, `order_items`, `products`, `users`
Free tier: 1TB/month compute — more than sufficient.
