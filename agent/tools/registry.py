"""Tool registry (single source of truth).

The controller must only call tools declared here.
"""

from typing import Any, Callable, Dict

from agent.tools.intent import classify_intent
from agent.tools.retrieval import retrieve_golden_bucket
from agent.tools.sql import generate_sql, execute_sql
from agent.tools.safety import mask_pii
from agent.tools.reporting import generate_report
from agent.tools.saved_reports import execute_delete_saved_reports, plan_delete_saved_reports


def tool_registry() -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    return {
        "intent_classifier": classify_intent,
        "retrieve_golden_bucket": retrieve_golden_bucket,
        "generate_sql": generate_sql,
        "execute_sql": execute_sql,
        "mask_pii": mask_pii,
        "generate_report": generate_report,
        "plan_delete_saved_reports": plan_delete_saved_reports,
        "execute_delete_saved_reports": execute_delete_saved_reports,
    }


__all__ = ["tool_registry"]

