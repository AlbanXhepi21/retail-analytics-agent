"""LangGraph state machine — wires all nodes into an explicit DAG with conditional routing."""

import logging
import time
from typing import Dict, Any

from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.nodes.intent_classifier import classify_intent
from agent.nodes.golden_bucket_retriever import retrieve_golden_bucket
from agent.nodes.schema_handler import handle_schema_question
from agent.nodes.sql_generator import generate_sql
from agent.nodes.sql_executor import execute_sql, MAX_SQL_RETRIES
from agent.nodes.pii_masker import mask_pii
from agent.nodes.report_generator import generate_report
from agent.nodes.confirmation_handler import (
    handle_destructive_request,
    handle_confirmation_response,
)
from agent.nodes.preference_handler import handle_preference_update
from agent.nodes.learning_loop import maybe_learn

logger = logging.getLogger(__name__)


def _route_after_intent(state: Dict[str, Any]) -> str:
    intent = state.get("intent", "out_of_scope")
    if intent == "analysis":
        return "golden_bucket_retriever"
    if intent == "schema_question":
        return "schema_handler"
    if intent == "destructive":
        return "confirmation_preview"
    if intent == "pending_confirmation":
        return "confirmation_executor"
    if intent == "preference":
        return "preference_handler"
    return END  # out_of_scope — report is already set by intent_classifier


def _route_after_sql_execution(state: Dict[str, Any]) -> str:
    sql_error = state.get("sql_error", "")
    retry_count = state.get("sql_retry_count", 0)

    if sql_error and retry_count < MAX_SQL_RETRIES:
        logger.info("SQL error detected, routing back to sql_generator (retry %d)", retry_count)
        return "sql_generator"
    if sql_error:
        logger.warning("Max SQL retries reached, giving up")
        return END
    return "pii_masker"


def _timed_node(node_name: str, node_func):
    """Wrap a node and collect per-node latency in milliseconds."""
    def _wrapped(state: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        result = node_func(state)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        existing = dict(state.get("node_latency_ms", {}))
        existing[node_name] = elapsed_ms
        if isinstance(result, dict):
            result["node_latency_ms"] = existing
            return result
        return {"node_latency_ms": existing}
    return _wrapped


def build_graph() -> StateGraph:
    """Build and compile the LangGraph agent."""
    graph = StateGraph(AgentState)

    graph.add_node("intent_classifier", _timed_node("intent_classifier", classify_intent))
    graph.add_node("golden_bucket_retriever", _timed_node("golden_bucket_retriever", retrieve_golden_bucket))
    graph.add_node("schema_handler", _timed_node("schema_handler", handle_schema_question))
    graph.add_node("sql_generator", _timed_node("sql_generator", generate_sql))
    graph.add_node("sql_executor", _timed_node("sql_executor", execute_sql))
    graph.add_node("pii_masker", _timed_node("pii_masker", mask_pii))
    graph.add_node("report_generator", _timed_node("report_generator", generate_report))
    graph.add_node("confirmation_preview", _timed_node("confirmation_preview", handle_destructive_request))
    graph.add_node("confirmation_executor", _timed_node("confirmation_executor", handle_confirmation_response))
    graph.add_node("preference_handler", _timed_node("preference_handler", handle_preference_update))
    graph.add_node("learning_loop", _timed_node("learning_loop", maybe_learn))

    graph.add_edge(START, "intent_classifier")

    graph.add_conditional_edges("intent_classifier", _route_after_intent)

    graph.add_edge("golden_bucket_retriever", "sql_generator")
    graph.add_edge("sql_generator", "sql_executor")
    graph.add_conditional_edges("sql_executor", _route_after_sql_execution)
    graph.add_edge("pii_masker", "report_generator")
    graph.add_edge("report_generator", "learning_loop")
    graph.add_edge("learning_loop", END)

    graph.add_edge("schema_handler", END)
    graph.add_edge("confirmation_preview", END)
    graph.add_edge("confirmation_executor", END)
    graph.add_edge("preference_handler", END)

    return graph.compile()
