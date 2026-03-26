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
| Self-Correction | SQL retry loop (max 3 attempts, exponential backoff) |
| Persona Management | Runtime-editable tone/instructions (no redeploy) |
| Observability | trace_id, node_path, per-node latency, structured audit log |
| High-stakes Saved Reports | Delete-by-phrase with **preview → confirm → execute**; state in `memory/pending_destructive.json` |

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

# With a specific user id (separate chat history file key):
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
```

**Saved Reports (destructive — two-step):** first ask, for example, `Delete all saved reports mentioning Acme Corp`. The agent lists matches and asks for confirmation. Reply **`confirm`** (or **`cancel`**) on the next line. Nothing is deleted until you confirm.

---

## Project Structure

```
retail-analytics-agent/
├── agent/
│   ├── graph.py              # LangGraph state machine
│   ├── state.py              # Typed state schema
│   ├── controller.py         # Controller loop nodes (controller / tool_executor / summarizer)
│   └── tools/                # Tool pool + registry (intent, SQL, PII, report, saved reports delete)
├── tools/
│   ├── bq_client.py          # BigQuery runner + schema context
│   ├── golden_bucket.py      # TF-IDF Trio retrieval
│   └── saved_reports_store.py  # Saved Reports JSON persistence + search/delete
├── config/
│   └── persona.json          # Editable agent persona (no redeploy)
├── data/
│   ├── golden_bucket.json    # Expert analyst Trios
│   └── saved_reports.json    # Saved Reports library (GDPR-style delete via confirmation flow)
├── memory/
│   ├── chat_history.json     # Short-term chat (per user)
│   ├── pending_destructive.json  # Awaiting confirmation for Saved Report deletions
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

## Prototype requirements (assignment deliverable 3)

The brief asks the prototype to implement **at least two** of these five capabilities:

1. Safety & PII Masking  
2. High-Stakes Oversight (Saved Reports / destructive ops + confirmation)  
3. Resilience & Graceful Error Handling  
4. Quality Assurance  
5. Observability  

**This codebase implements four of the five:** **(1) Safety & PII Masking**, **(2) High-Stakes Oversight** (Saved Reports in `data/saved_reports.json`; `plan_delete_saved_reports` shows a preview; user must reply **`confirm`**; `execute_delete_saved_reports` runs only after confirmation; pending payload stored per user in `memory/pending_destructive.json`), **(3) Resilience & Graceful Error Handling**, and **(5) Observability**.

**(4) Quality Assurance** is documented as a **pre-deployment methodology** in `docs/qa_plan.md` (pytest, smoke tests, rubric); there is no separate automated “intent vs report” judge in code.

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
