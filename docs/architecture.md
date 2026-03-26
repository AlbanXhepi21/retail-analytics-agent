# Architecture Diagram

```mermaid
flowchart TD
    User(["👤 Executive / Manager<br/>(CLI Interface)"])

    subgraph CLI["CLI Interface — main.py"]
        Input["User Message Input"]
        AgentCall["agent.invoke(initial_state)"]
        Output["Formatted Report Output"]
    end

    subgraph Graph["LangGraph State Machine — agent/graph.py"]
        direction TB
        START([START]) --> Controller["autonomous_controller<br/>decides next action/tool"]
        Controller -->|"next_action=call_tool"| ToolExec["tool_executor<br/>executes ONE controller-selected tool"]
        Controller -->|"next_action=finish"| END([END])
        ToolExec --> Summarizer["observation_summarizer<br/>summarize tool result → facts/goals"]
        Summarizer --> Controller
    end

    subgraph Tools["Tool Registry (controller-selected)"]
        IC["🔍 intent_classifier"]
        GB["📚 retrieve_golden_bucket"]
        GS["🧠 generate_sql"]
        EX["⚙️ execute_sql<br/>(BigQuery Runner)"]
        PM["🔒 mask_pii"]
        RG["📝 generate_report"]
        PD["⚠️ plan_delete_saved_reports"]
        ED["🗑️ execute_delete_saved_reports"]
    end

    subgraph Stores["Supporting Data Stores"]
        GBStore[("📦 Golden Bucket<br/>data/golden_bucket.json")]
        BQ[("🗄️ BigQuery<br/>bigquery-public-data.thelook_ecommerce")]
        CH[("💬 Chat history<br/>memory/chat_history.json")]
        PC[("🎭 Persona Config<br/>config/persona.json")]
        SQLMEM[("🔧 SQL fix memory<br/>memory/sql_fix_memory.json")]
        PEND[("⏳ Pending destructive<br/>memory/pending_destructive.json")]
        AUDIT[("📜 Audit Log<br/>memory/audit_log.jsonl")]
    end

    subgraph Observability["Observability"]
        TRACE["trace_id + node_path"]
        LAT["node_latency_ms (per-node wrapper)"]
    end

    User --> Input
    Input --> AgentCall
    AgentCall --> Controller
    Controller --> Output

    ToolExec --> IC
    ToolExec --> GB
    ToolExec --> GS
    ToolExec --> EX
    ToolExec --> PM
    ToolExec --> RG
    ToolExec --> PD
    ToolExec --> ED

    GB <-->|"similarity search"| GBStore

    EX --> BQ
    RG --> PC
    PD --> PEND
    ED --> PEND
    ToolExec --> SQLMEM
    CLI --> CH
    CLI --> AUDIT

    Controller --> TRACE
    ToolExec --> LAT

    style Graph fill:#e8f5e9,stroke:#4caf50
    style Tools fill:#f3e5f5,stroke:#9c27b0
    style Stores fill:#e3f2fd,stroke:#2196f3
    style Observability fill:#fce4ec,stroke:#e91e63
    style CLI fill:#fff3e0,stroke:#ff9800
```

## Node Routing Logic

| Intent | Route |
|---|---|
| `analysis` | Golden Bucket → SQL Generator → SQL Executor → PII Masker → Report Generator |
| `out_of_scope` | Reject with helpful examples |

## SQL Self-Correction Loop

```mermaid
flowchart LR
    GB[retrieve_golden_bucket] --> GS[generate_sql]
    GS --> EX[execute_sql]

    EX -->|"sql_error"| GS
    GS -->|"fallback to better examples"| GB

    EX -->|"success"| PM[mask_pii]
    PM --> RG[generate_report]

    EX -->|"empty result (report)"| RG
```

`execute_sql` surfaces BigQuery errors into `sql_error` / `sql_error_signature`, which the controller feeds back into the `generate_sql` prompt for targeted self-correction (syntax/time-window/fallback handling).
