# Architecture Diagram

```mermaid
flowchart TD
    User(["👤 Executive / Manager<br/>(CLI Interface)"])

    subgraph CLI["CLI Interface — main.py"]
        Input["User Message Input"]
        Output["Formatted Report Output"]
    end

    subgraph Graph["LangGraph Agent — agent/graph.py"]
        direction TB
        IC["🔍 Intent Classifier<br/>Analysis | Schema | Destructive | Preference | Out-of-Scope"]

        subgraph AnalysisPath["Analysis Path"]
            GB["📚 Golden Bucket Retriever<br/>(TF-IDF similarity search)"]
            SG["🧠 SQL Generator<br/>(Gemini 2.5 Flash)"]
            SE["⚙️ SQL Executor<br/>(BigQuery Runner)"]
            SC{"SQL Error?<br/>Retry < 3?"}
            PM["🔒 PII Masker<br/>(column + regex redaction)"]
            RG["📝 Report Generator<br/>(Gemini 2.5 Flash)"]
            LL["🔄 Learning Loop<br/>(auto-expand Golden Bucket)"]
        end

        subgraph SafetyPath["Safety & Preference Paths"]
            OOS["🚫 Out of Scope Handler"]
            SH["📋 Schema Handler"]
            CH1["⚠️ Confirmation Preview<br/>(show what will be deleted)"]
            CH2["✅ Confirmation Executor<br/>(delete only after confirm)"]
            PH["⚙️ Preference Handler<br/>(detect & persist user prefs)"]
        end
    end

    subgraph Storage["Supporting Services"]
        GBStore[("📦 Golden Bucket<br/>data/golden_bucket.json<br/>— Prototype: JSON + TF-IDF<br/>— Production: Pinecone / pgvector")]
        BQ[("🗄️ BigQuery<br/>bigquery-public-data<br/>.thelook_ecommerce")]
        UP[("👤 User Preferences<br/>memory/user_prefs.json<br/>— output format<br/>— detail level")]
        PC[("🎭 Persona Config<br/>config/persona.json<br/>— tone / instructions<br/>— editable without redeploy")]
        SR[("📁 Saved Reports<br/>data/saved_reports.json<br/>— GDPR deletion target")]
    end

    subgraph Observability["Observability Layer"]
        LOG["📋 Structured Logging<br/>trace_id | node_path<br/>latency | retry_count"]
        PROD["📊 Production: LangSmith<br/>or Cloud Monitoring"]
    end

    User --> Input
    Input --> IC

    IC -->|"analysis"| GB
    IC -->|"schema_question"| SH
    IC -->|"out_of_scope"| OOS
    IC -->|"destructive"| CH1
    IC -->|"pending_confirmation"| CH2
    IC -->|"preference"| PH

    GB --> SG
    SG --> SE
    SE --> SC
    SC -->|"error + retries left"| SG
    SC -->|"success"| PM
    SC -->|"max retries hit"| Output
    PM --> RG
    RG --> LL

    SH --> Output
    OOS --> Output
    CH1 --> Output
    CH2 --> Output
    PH --> Output
    LL --> Output

    Output --> User

    GB <-->|"similarity search"| GBStore
    SE <-->|"execute SQL"| BQ
    SH <-->|"schema lookup"| GBStore
    RG <-->|"load preferences"| UP
    RG <-->|"load persona"| PC
    LL <-->|"add learned trio"| GBStore
    PH <-->|"persist preferences"| UP
    CH1 <-->|"search reports"| SR
    CH2 <-->|"delete reports"| SR

    Graph --> LOG
    LOG -.->|"production"| PROD

    style AnalysisPath fill:#e8f5e9,stroke:#4caf50
    style SafetyPath fill:#fff3e0,stroke:#ff9800
    style Storage fill:#e3f2fd,stroke:#2196f3
    style Observability fill:#fce4ec,stroke:#e91e63
    style CLI fill:#f3e5f5,stroke:#9c27b0
```

## Node Routing Logic

| Intent | Route |
|---|---|
| `analysis` | Golden Bucket → SQL Generator → SQL Executor → PII Masker → Report Generator → Learning Loop |
| `schema_question` | Schema Handler (direct from Golden Bucket) |
| `destructive` | Confirmation Preview (2-step flow) |
| `pending_confirmation` | Confirmation Executor (process yes/no) |
| `preference` | Preference Handler (detect changes, persist, confirm) |
| `out_of_scope` | Reject with helpful examples |

## SQL Self-Correction Loop

```mermaid
flowchart LR
    A[SQL Generator] --> B[SQL Executor]
    B -->|"Error + retry < 3"| A
    B -->|"Success"| C[PII Masker]
    B -->|"Max retries"| D[Graceful Error Message]
```

The error message from BigQuery is injected back into the SQL generation prompt, allowing Gemini to self-correct based on the specific error (syntax, table not found, etc.).
