"""LangGraph autonomous loop — controller-driven tool execution."""

import time
from typing import Dict, Any

from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.controller import (
    autonomous_controller,
    observation_summarizer,
    tool_executor,
)


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
    """Build and compile the autonomous controller loop graph."""
    graph = StateGraph(AgentState)

    graph.add_node("autonomous_controller", _timed_node("autonomous_controller", autonomous_controller))
    graph.add_node("tool_executor", _timed_node("tool_executor", tool_executor))
    graph.add_node("observation_summarizer", _timed_node("observation_summarizer", observation_summarizer))

    graph.add_edge(START, "autonomous_controller")

    def _route_after_controller(state: Dict[str, Any]) -> str:
        action = state.get("next_action", "finish")
        if action == "call_tool":
            return "tool_executor"
        return END

    graph.add_conditional_edges("autonomous_controller", _route_after_controller)
    graph.add_edge("tool_executor", "observation_summarizer")
    graph.add_edge("observation_summarizer", "autonomous_controller")

    return graph.compile()
