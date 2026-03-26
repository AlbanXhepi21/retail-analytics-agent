"""Typed state schema flowing through every LangGraph node."""

import operator
from typing import Annotated, TypedDict, Optional, List, Dict, Any


class AgentState(TypedDict, total=False):
    # Input
    user_message: str
    user_id: str
    chat_history: List[Dict[str, str]]  # [{"role": "user"|"assistant", "content": "..."}]

    # Intent classification
    intent: str  # analysis | destructive | schema_question | out_of_scope

    # Golden Bucket
    retrieved_trios: List[Dict[str, Any]]
    golden_bucket_score: float
    golden_bucket_confidence: str

    # SQL generation & execution
    generated_sql: str
    sql_result: Any  # DataFrame serialized as list of dicts
    sql_result_columns: List[str]
    sql_error: str
    sql_retry_count: int

    # PII
    pii_columns_dropped: List[str]
    pii_values_redacted: int
    pii_masked: bool

    # Report
    report: str

    # Destructive ops
    pending_confirmation: Optional[Dict[str, Any]]
    confirmation_result: str

    # Learning loop
    learned_trio_id: str
    preference_updated: bool

    # Observability
    trace_id: str
    node_path: Annotated[list[str], operator.add]
    node_latency_ms: Dict[str, int]
    error_message: str
