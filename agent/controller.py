"""LangGraph nodes: autonomous controller loop.

controller -> tool_executor -> observation_summarizer -> controller ...
"""

import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict

from pydantic import BaseModel, Field

from agent.tools.registry import tool_registry

logger = logging.getLogger(__name__)

MAX_AGENT_ITERATIONS = 8
MAX_TOOL_RETRIES = 3
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_VERSION = os.environ.get("GEMINI_API_VERSION", "v1")
SQL_FIX_MEMORY_PATH = os.path.join(os.path.dirname(__file__), "..", "memory", "sql_fix_memory.json")

_TRANSIENT_HINTS = [
    "timeout",
    "temporarily",
    "resource_exhausted",
    "quota",
    "rate limit",
    "429",
    "try again",
    "connection reset",
]

_CONFIRM_WORDS = frozenset(
    {
        "confirm",
        "confirmed",
        "yes",
        "y",
        "proceed",
        "ok",
        "delete them",
        "please confirm",
        "go ahead",
    }
)


def _is_confirm_message(text: str) -> bool:
    m = (text or "").strip().lower()
    if not m:
        return False
    if m in _CONFIRM_WORDS:
        return True
    if m.startswith("confirm ") or m.endswith(" confirm"):
        return True
    return False


_ANALYSIS_HINTS = [
    "revenue",
    "sales",
    "top",
    "best",
    "trend",
    "customers",
    "products",
    "orders",
    "spend",
    "germany",
    "country",
]


class ControllerDecision(BaseModel):
    """Structured next-step decision returned by the controller."""

    action_type: str = Field(description="One of: call_tool | ask_user | finish")
    tool_name: str = Field(default="", description="Tool name when action_type=call_tool")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="Reserved for future tool args")
    ask_user_question: str = Field(default="", description="Clarifying question when action_type=ask_user")
    final_answer: str = Field(default="", description="Final answer when action_type=finish")
    max_attempts: int = Field(default=1, description="Per-step retry budget for the chosen tool")
    fallback_tool: str = Field(default="", description="Optional fallback tool when primary tool fails")
    reasoning_brief: str = Field(default="", description="Short rationale for tracing/debugging")


def _tool_registry() -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    return tool_registry()


def _is_transient_error(error_text: str) -> bool:
    lowered = (error_text or "").lower()
    return any(hint in lowered for hint in _TRANSIENT_HINTS)


def _extract_tool_error(tool_output: Dict[str, Any]) -> str:
    if not isinstance(tool_output, dict):
        return ""
    return str(tool_output.get("error_message") or tool_output.get("sql_error") or "").strip()


def _allowed_fallback(requested_tool: str, fallback_tool: str) -> bool:
    """Constrain fallback transitions so tools stay semantically aligned."""
    if not fallback_tool:
        return False
    allowed = {
        "generate_sql": {"retrieve_golden_bucket"},
        "execute_sql": {"generate_sql"},
        "generate_report": {"finish"},
    }
    return fallback_tool in allowed.get(requested_tool, set())


def _sanitize_sql_for_execution(sql: str) -> tuple[str, str]:
    """Best-effort SQL normalization + basic syntax sanity checks."""
    if not sql or not sql.strip():
        return "", "No SQL query generated yet."

    fixed = sql.strip()
    # Common malformed patterns from model output (e.g., "SELECTt1", "FROM`table`").
    for kw in [
        "SELECT",
        "FROM",
        "WHERE",
        "GROUP BY",
        "ORDER BY",
        "LIMIT",
        "JOIN",
        "INNER JOIN",
        "LEFT JOIN",
        "RIGHT JOIN",
        "ON",
    ]:
        compact = kw.replace(" ", r"\s*")
        fixed = re.sub(rf"(?i)\b{compact}(?=`|\w)", f"{kw} ", fixed)

    # Ensure separators are readable for parser.
    fixed = re.sub(r"\s+", " ", fixed).strip()
    # BigQuery-safe rewrite for common invalid month timestamp arithmetic.
    fixed = re.sub(
        r"(?i)TIMESTAMP_SUB\s*\(\s*CURRENT_TIMESTAMP\(\)\s*,\s*INTERVAL\s+(\d+)\s+MONTH\s*\)",
        r"TIMESTAMP(DATE_SUB(CURRENT_DATE(), INTERVAL \1 MONTH))",
        fixed,
    )

    upper = fixed.upper()
    if "SELECT " not in upper or " FROM " not in upper:
        return fixed, "Generated SQL appears malformed (missing SELECT/FROM structure)."
    if re.search(r"(?i)\bSELECT[A-Za-z0-9_`]", fixed):
        return fixed, "Generated SQL appears malformed near SELECT clause."
    if re.search(r"(?i)\bFROM[A-Za-z0-9_`]", fixed):
        return fixed, "Generated SQL appears malformed near FROM clause."
    return fixed, ""


def _sql_error_signature(error_text: str) -> str:
    text = (error_text or "").lower()
    if "timestamp_sub does not support the month date part" in text:
        return "timestamp_sub_month_invalid"
    if "syntax error" in text:
        return "sql_syntax_error"
    if "invalidquery" in text or "invalid query" in text:
        return "invalid_query"
    return "other"


def _load_sql_fix_memory() -> Dict[str, Any]:
    try:
        with open(SQL_FIX_MEMORY_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"errors": {}, "recoveries": {}}


def _save_sql_fix_memory(memory: Dict[str, Any]) -> None:
    try:
        with open(SQL_FIX_MEMORY_PATH, "w") as f:
            json.dump(memory, f, indent=2)
    except Exception as exc:
        logger.warning("Failed to persist SQL fix memory: %s", exc)


def _record_sql_error(signature: str, error_text: str) -> None:
    if not signature or signature == "other":
        return
    memory = _load_sql_fix_memory()
    errors = memory.setdefault("errors", {})
    entry = errors.setdefault(signature, {"count": 0, "last_error": ""})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_error"] = (error_text or "")[:500]
    _save_sql_fix_memory(memory)


def _record_sql_recovery(signature: str) -> None:
    if not signature or signature == "other":
        return
    memory = _load_sql_fix_memory()
    recoveries = memory.setdefault("recoveries", {})
    recoveries[signature] = int(recoveries.get(signature, 0)) + 1
    _save_sql_fix_memory(memory)


def _heuristic_decision(state: Dict[str, Any]) -> ControllerDecision:
    if state.get("report"):
        return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))

    intent = state.get("intent", "")
    if intent == "destructive_saved_reports":
        if state.get("last_tool_name") in ("plan_delete_saved_reports", "execute_delete_saved_reports"):
            return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))
        return ControllerDecision(
            action_type="call_tool",
            tool_name="plan_delete_saved_reports",
            max_attempts=1,
            reasoning_brief="Preview matching saved reports; user must confirm before delete.",
        )

    if not intent:
        return ControllerDecision(
            action_type="call_tool",
            tool_name="intent_classifier",
            max_attempts=1,
            reasoning_brief="Classify request before selecting specialist tools.",
        )

    if intent == "out_of_scope":
        return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))

    if not state.get("retrieved_trios"):
        return ControllerDecision(action_type="call_tool", tool_name="retrieve_golden_bucket", max_attempts=1)
    if not state.get("generated_sql") or state.get("sql_error"):
        return ControllerDecision(
            action_type="call_tool",
            tool_name="generate_sql",
            max_attempts=2,
            fallback_tool="retrieve_golden_bucket",
        )
    if state.get("generated_sql") and (state.get("sql_error") or not state.get("sql_result")):
        return ControllerDecision(
            action_type="call_tool",
            tool_name="execute_sql",
            max_attempts=2,
            fallback_tool="generate_sql",
        )
    if state.get("sql_result") and not state.get("pii_masked") and not state.get("report"):
        return ControllerDecision(action_type="call_tool", tool_name="mask_pii", max_attempts=1)
    if state.get("sql_result") and not state.get("report"):
        return ControllerDecision(action_type="call_tool", tool_name="generate_report", max_attempts=1)
    return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))


def _make_goal(goal_id: str, goal_type: str, description: str, required: bool = True) -> Dict[str, Any]:
    return {
        "id": goal_id,
        "type": goal_type,
        "description": description,
        "required": required,
        "status": "pending",
    }


def _plan_goals(user_message: str) -> list[Dict[str, Any]]:
    goals: list[Dict[str, Any]] = []
    goals.append(_make_goal("goal_analysis", "analysis", "Answer the analytics request"))
    return goals


def _sync_current_goal(goals: list[Dict[str, Any]]) -> str:
    for g in goals:
        if g.get("status") == "pending":
            return str(g.get("id"))
    return ""


def _required_goals_done(goals: list[Dict[str, Any]]) -> bool:
    for g in goals:
        if g.get("required", True) and g.get("status") not in {"done", "blocked"}:
            return False
    return True


def _enforce_analysis_progression(state: Dict[str, Any], decision: ControllerDecision) -> ControllerDecision:
    """Hard guardrails so analysis flows forward and does not loop randomly."""
    message = (state.get("user_message") or "").lower()
    analysis_signal = (
        state.get("intent") == "analysis"
        or bool(state.get("retrieved_trios"))
        or bool(state.get("generated_sql"))
        or bool(state.get("sql_result"))
        or any(h in message for h in _ANALYSIS_HINTS)
    )
    if not analysis_signal:
        return decision

    if state.get("report"):
        return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))

    # If we already have query results, move to reporting.
    if state.get("sql_result"):
        return ControllerDecision(action_type="call_tool", tool_name="generate_report", max_attempts=1)

    # If SQL exists but no result yet, execute it next.
    if state.get("generated_sql"):
        return ControllerDecision(
            action_type="call_tool",
            tool_name="execute_sql",
            max_attempts=2,
            fallback_tool="generate_sql",
        )

    # No SQL yet -> generate with available context.
    if state.get("retrieved_trios"):
        return ControllerDecision(
            action_type="call_tool",
            tool_name="generate_sql",
            max_attempts=2,
            fallback_tool="retrieve_golden_bucket",
        )

    # First analysis step: retrieve examples/context.
    return ControllerDecision(action_type="call_tool", tool_name="retrieve_golden_bucket", max_attempts=1)


def _enforce_destructive_progression(state: Dict[str, Any], decision: ControllerDecision) -> ControllerDecision:
    if state.get("report"):
        return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))
    if state.get("last_tool_name") == "plan_delete_saved_reports":
        return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))
    if state.get("last_tool_name") == "execute_delete_saved_reports":
        return ControllerDecision(action_type="finish", final_answer=state.get("report", ""))
    return ControllerDecision(
        action_type="call_tool",
        tool_name="plan_delete_saved_reports",
        max_attempts=1,
        reasoning_brief="Preview matches; confirmation required before execution.",
    )


def _enforce_goal_progression(state: Dict[str, Any], decision: ControllerDecision) -> ControllerDecision:
    goals = list(state.get("goals", []))
    current_goal_id = state.get("current_goal_id", "")
    if not goals:
        return decision
    current = next((g for g in goals if g.get("id") == current_goal_id), None)
    if not current or current.get("status") != "pending":
        return decision

    goal_type = current.get("type")
    if goal_type == "destructive":
        return _enforce_destructive_progression(state, decision)
    if goal_type == "analysis":
        return _enforce_analysis_progression(state, decision)
    return decision


def autonomous_controller(state: Dict[str, Any]) -> Dict[str, Any]:
    """Decide the next action (tool call, ask user, or finish)."""
    node_path = ["autonomous_controller"]
    iterations_used = int(state.get("iterations_used", 0))
    max_iterations = int(state.get("max_iterations", MAX_AGENT_ITERATIONS))
    available_tools = sorted(_tool_registry().keys())

    if iterations_used >= max_iterations:
        fallback_report = state.get("report") or (
            "I reached the step budget while trying to complete this request. "
            "Please narrow the scope or provide a bit more detail."
        )
        return {
            "next_action": "finish",
            "halt_reason": "budget_exhausted",
            "report": fallback_report,
            "controller_decision": {"action_type": "finish", "final_answer": fallback_report},
            "node_path": node_path,
        }

    goals = list(state.get("goals", []))
    if not goals:
        pending = state.get("pending_destructive")
        msg_raw = state.get("user_message", "")
        if pending and _is_confirm_message(msg_raw):
            goals = [_make_goal("goal_destructive", "destructive", "Execute confirmed saved-report deletion")]
            return {
                "goals": goals,
                "current_goal_id": "goal_destructive",
                "intent": "destructive_saved_reports",
                "next_action": "call_tool",
                "controller_decision": {
                    "action_type": "call_tool",
                    "tool_name": "execute_delete_saved_reports",
                    "max_attempts": 1,
                },
                "iterations_used": iterations_used + 1,
                "node_path": node_path,
            }
        goals = _plan_goals(state.get("user_message", ""))
        current_goal_id = _sync_current_goal(goals)
        return {
            "goals": goals,
            "current_goal_id": current_goal_id,
            "next_action": "call_tool",
            "controller_decision": {"action_type": "call_tool", "tool_name": "intent_classifier", "max_attempts": 1},
            "iterations_used": iterations_used + 1,
            "node_path": node_path,
        }

    # Circuit breaker for repeated known SQL errors.
    if state.get("sql_error_signature") == "timestamp_sub_month_invalid" and int(
        state.get("sql_error_repeat_count", 0)
    ) >= 2:
        report = (
            "I ran into the same SQL time-window error repeatedly while handling the last-12-month filter. "
            "Please retry now; I have adjusted the query strategy to use DATE-based month arithmetic for BigQuery."
        )
        return {
            "next_action": "finish",
            "halt_reason": "repeated_sql_error",
            "report": report,
            "controller_decision": {"action_type": "finish", "final_answer": report},
            "node_path": node_path,
        }

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            temperature=0,
            max_retries=1,
            timeout=15,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            model_kwargs={"api_version": GEMINI_API_VERSION},
        )
        structured_llm = llm.with_structured_output(ControllerDecision)

        latest_observation = state.get("latest_observation_summary", "")
        tool_trace = state.get("tool_trace", [])[-4:]
        prompt = (
            "You are the main controller for a retail analytics agent.\n"
            "Choose exactly one next action.\n\n"
            f"Available tools: {', '.join(available_tools)}\n"
            "Policy:\n"
            "- Use call_tool for work.\n"
            "- For Saved Reports deletion: use plan_delete_saved_reports first (preview only). "
            "Use execute_delete_saved_reports only after the user explicitly confirmed in a follow-up message (pending state).\n"
            "- Use ask_user only when required information is missing.\n"
            "- Use finish when the answer is ready.\n"
            "- Keep max_attempts small (1-2, up to 3).\n"
            "- Set fallback_tool when helpful.\n\n"
            f"User message: {state.get('user_message', '')}\n"
            f"Current intent: {state.get('intent', '')}\n"
            f"Pending destructive op: {bool(state.get('pending_destructive'))}\n"
            f"Current sql_error: {state.get('sql_error', '')}\n"
            f"Latest observation summary: {latest_observation}\n"
            f"Recent tool trace (compact): {json.dumps(tool_trace, ensure_ascii=True)}\n"
        )

        decision = structured_llm.invoke(
            [
                SystemMessage(
                    content=(
                        "Return a single valid decision. Never invent tool names. "
                        "Prefer progress with tools over long reasoning."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
    except Exception as exc:
        logger.warning("Controller LLM failed, using heuristic policy: %s", exc)
        decision = _heuristic_decision(state)

    action = (decision.action_type or "").strip().lower()
    if action not in {"call_tool", "ask_user", "finish"}:
        decision = _heuristic_decision(state)
        action = decision.action_type

    if action == "call_tool" and decision.tool_name not in available_tools:
        logger.warning("Controller picked unknown tool '%s', falling back heuristic", decision.tool_name)
        decision = _heuristic_decision(state)
        action = decision.action_type

    # Ensure goal-first progression even if controller LLM drifts.
    decision = _enforce_goal_progression(state, decision)
    action = decision.action_type

    result: Dict[str, Any] = {
        "next_action": action,
        "controller_decision": decision.model_dump(),
        "iterations_used": iterations_used + 1,
        "node_path": node_path,
    }
    if action == "ask_user":
        question = decision.ask_user_question or "Could you clarify your request a bit more?"
        result["report"] = question
        result["halt_reason"] = "needs_user_input"
    if action == "finish":
        if not _required_goals_done(goals):
            decision = _enforce_goal_progression(state, _heuristic_decision(state))
            result["next_action"] = decision.action_type
            result["controller_decision"] = decision.model_dump()
            return result
        # Preserve richer tool-generated reports if already available (avoid LLM paraphrase overwriting).
        current_report = state.get("report") or ""
        if not current_report and isinstance(state.get("latest_tool_result"), dict):
            current_report = str(state["latest_tool_result"].get("report") or "")
        result["report"] = current_report or decision.final_answer
        result["halt_reason"] = "finished"
    return result


def tool_executor(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute exactly one controller-selected tool with retry and fallback logic."""
    node_path = ["tool_executor"]
    decision = state.get("controller_decision", {}) or {}
    tool_name = decision.get("tool_name", "")
    max_attempts = max(1, min(int(decision.get("max_attempts", 1)), MAX_TOOL_RETRIES))
    fallback_tool = decision.get("fallback_tool", "")

    tools = _tool_registry()
    tool_fn = tools.get(tool_name)
    if not tool_fn:
        return {
            "last_tool_name": tool_name,
            "last_tool_ok": False,
            "last_tool_error": f"Unknown tool: {tool_name}",
            "latest_tool_result": {},
            "node_path": node_path,
        }

    latest_result: Dict[str, Any] = {}
    last_error = ""
    ran_attempts = 0

    for attempt in range(1, max_attempts + 1):
        ran_attempts = attempt
        try:
            state_for_tool = dict(state)
            if tool_name == "execute_sql":
                fixed_sql, sql_issue = _sanitize_sql_for_execution(state.get("generated_sql", ""))
                state_for_tool["generated_sql"] = fixed_sql
                if sql_issue:
                    latest_result = {"sql_error": sql_issue, "error_message": sql_issue}
                    last_error = sql_issue
                    break
            latest_result = tool_fn(state_for_tool)
            last_error = _extract_tool_error(latest_result)
            if not last_error:
                break
            if attempt < max_attempts and _is_transient_error(last_error):
                time.sleep(0.3 * attempt)
                continue
            break
        except Exception as exc:
            last_error = str(exc)
            latest_result = {"error_message": last_error}
            if attempt < max_attempts and _is_transient_error(last_error):
                time.sleep(0.3 * attempt)
                continue
            break

    used_tool = tool_name
    can_fallback = (
        last_error
        and fallback_tool
        and fallback_tool in tools
        and fallback_tool != tool_name
        and _allowed_fallback(tool_name, fallback_tool)
    )
    if can_fallback:
        logger.info("Primary tool '%s' failed, attempting fallback '%s'", tool_name, fallback_tool)
        used_tool = fallback_tool
        try:
            latest_result = tools[fallback_tool](state)
            last_error = _extract_tool_error(latest_result)
        except Exception as exc:
            last_error = str(exc)
            latest_result = {"error_message": last_error}

    tool_trace = list(state.get("tool_trace", []))
    tool_trace.append(
        {
            "tool": used_tool,
            "requested_tool": tool_name,
            "attempts": ran_attempts,
            "ok": not bool(last_error),
            "error": (last_error or "")[:240],
        }
    )

    response: Dict[str, Any] = {
        "last_tool_name": used_tool,
        "last_tool_ok": not bool(last_error),
        "last_tool_error": last_error,
        "latest_tool_result": latest_result,
        "tool_trace": tool_trace,
        "node_path": node_path,
    }
    if last_error:
        sig = _sql_error_signature(last_error)
        prev_sig = state.get("sql_error_signature")
        prev_count = int(state.get("sql_error_repeat_count", 0))
        repeat_count = prev_count + 1 if sig == prev_sig else 1
        response["sql_error_signature"] = sig
        response["sql_error_repeat_count"] = repeat_count
        _record_sql_error(sig, last_error)
    else:
        previous_signature = str(state.get("sql_error_signature") or "")
        if tool_name == "execute_sql" and previous_signature:
            _record_sql_recovery(previous_signature)
        response["sql_error_signature"] = ""
        response["sql_error_repeat_count"] = 0
    # Prevent non-report tools from replacing analysis answers mid-flow.
    destructive_ok = state.get("intent") == "destructive_saved_reports" and used_tool in (
        "plan_delete_saved_reports",
        "execute_delete_saved_reports",
    )
    if (
        not destructive_ok
        and (state.get("intent") == "analysis" or bool(state.get("sql_result")) or bool(state.get("generated_sql")))
        and used_tool != "generate_report"
        and isinstance(latest_result, dict)
        and "report" in latest_result
    ):
        latest_result = dict(latest_result)
        latest_result.pop("report", None)
    if isinstance(latest_result, dict):
        response.update(latest_result)
    return response


def observation_summarizer(state: Dict[str, Any]) -> Dict[str, Any]:
    """Compress the latest observation into short facts for controller context."""
    node_path = ["observation_summarizer"]
    tool_name = state.get("last_tool_name", "")
    ok = bool(state.get("last_tool_ok"))
    last_error = state.get("last_tool_error", "")
    latest_result = state.get("latest_tool_result", {}) or {}

    summary_parts = [f"tool={tool_name}", f"ok={ok}"]
    if last_error:
        summary_parts.append(f"error={last_error[:120]}")
    if isinstance(latest_result, dict):
        if "intent" in latest_result:
            summary_parts.append(f"intent={latest_result.get('intent')}")
        if "golden_bucket_confidence" in latest_result:
            summary_parts.append(f"gb_confidence={latest_result.get('golden_bucket_confidence')}")
        if "sql_result" in latest_result:
            rows = len(latest_result.get("sql_result") or [])
            summary_parts.append(f"rows={rows}")
        if latest_result.get("report"):
            summary_parts.append("report_ready=true")

    observation_summary = "; ".join(summary_parts)
    facts = list(state.get("facts", []))
    facts.append(observation_summary)

    goals = list(state.get("goals", []))
    report_text = str(state.get("report") or "")
    intent = state.get("intent", "")
    if not intent and isinstance(latest_result, dict) and latest_result.get("intent"):
        intent = str(latest_result.get("intent", ""))
    if intent == "destructive_saved_reports" and not any(g.get("id") == "goal_destructive" for g in goals):
        goals = [_make_goal("goal_destructive", "destructive", "Delete saved reports with confirmation")]
    for g in goals:
        if g.get("status") != "pending":
            continue
        if g.get("type") == "analysis" and report_text:
            g["status"] = "done"
        if g.get("type") == "destructive" and report_text:
            g["status"] = "done"
    current_goal_id = _sync_current_goal(goals)

    return {
        "latest_observation_summary": observation_summary,
        "facts": facts[-30:],
        "goals": goals,
        "current_goal_id": current_goal_id,
        "node_path": node_path,
    }


__all__ = ["autonomous_controller", "tool_executor", "observation_summarizer"]
