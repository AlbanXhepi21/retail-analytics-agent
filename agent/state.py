"""Typed state schema flowing through every LangGraph node."""

import operator
from typing import Annotated, TypedDict, Optional, List, Dict, Any


class AgentState(TypedDict, total=False):
    # Input
    user_message: str
    user_id: str
    chat_history: List[Dict[str, str]]  # [{"role": "user"|"assistant", "content": "..."}]

    # Intent classification
    intent: str  # analysis | out_of_scope | destructive_saved_reports

    # Agentic loop control
    next_action: str  # call_tool | ask_user | finish
    controller_decision: Dict[str, Any]
    iterations_used: int
    max_iterations: int
    halt_reason: str
    goals: List[Dict[str, Any]]
    current_goal_id: str

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
    sql_error_signature: str
    sql_error_repeat_count: int

    # PII
    pii_columns_dropped: List[str]
    pii_values_redacted: int
    pii_masked: bool

    # Report
    report: str

    # High-stakes Saved Reports (destructive, confirmation-gated)
    pending_destructive: Dict[str, Any]
    destructive_phase: str
    destructive_deleted_count: int

    # Tool execution trace
    latest_tool_result: Dict[str, Any]
    latest_observation_summary: str
    facts: List[str]
    tool_trace: List[Dict[str, Any]]
    last_tool_name: str
    last_tool_ok: bool
    last_tool_error: str

    # Observability
    trace_id: str
    node_path: Annotated[list[str], operator.add]
    node_latency_ms: Dict[str, int]
    error_message: str
