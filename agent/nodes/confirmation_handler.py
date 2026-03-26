"""Confirmation handler for destructive operations (report deletion)."""

import json
import logging
import os
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

REPORTS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "saved_reports.json")

CONFIRM_WORDS = {"yes", "confirm", "proceed", "do it", "execute", "ok", "y"}
CANCEL_WORDS = {"no", "cancel", "abort", "stop", "nevermind", "n"}


def _load_reports() -> List[Dict[str, Any]]:
    try:
        with open(REPORTS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_reports(reports: List[Dict[str, Any]]) -> None:
    with open(REPORTS_PATH, "w") as f:
        json.dump(reports, f, indent=2)


def _search_reports(keyword: str) -> List[Dict[str, Any]]:
    reports = _load_reports()
    keyword_lower = keyword.lower()
    return [r for r in reports if keyword_lower in r.get("title", "").lower() or keyword_lower in r.get("content", "").lower()]


def _extract_search_term(message: str) -> str:
    """Best-effort extraction of the search target from the user message."""
    msg = message.lower()
    for prefix in ["delete all reports mentioning ", "delete reports about ",
                    "remove all reports mentioning ", "remove reports about ",
                    "delete reports mentioning ", "delete all reports about ",
                    "purge reports about ", "erase reports about ",
                    "delete report ", "remove report "]:
        if msg.startswith(prefix):
            return message[len(prefix):].strip().strip('"').strip("'")
    for kw in ["mentioning ", "about ", "for ", "related to ", "regarding "]:
        if kw in msg:
            idx = msg.index(kw) + len(kw)
            return message[idx:].strip().strip('"').strip("'")
    return message.split()[-1] if message.split() else ""


def handle_destructive_request(state: Dict[str, Any]) -> Dict[str, Any]:
    """Preview matching reports and request confirmation."""
    node_path = ["confirmation_preview"]

    message = state.get("user_message", "")
    search_term = _extract_search_term(message)
    matching = _search_reports(search_term)

    if not matching:
        return {
            "report": f"No saved reports found matching **\"{search_term}\"**. No action needed.",
            "node_path": node_path,
            "pending_confirmation": None,
        }

    report_list = "\n".join(
        f"  - **{r['title']}** (ID: {r['id']})" for r in matching
    )

    preview = (
        f"⚠️  **CONFIRMATION REQUIRED**\n\n"
        f"You have requested to delete **{len(matching)} report(s)** matching: _{search_term}_\n\n"
        f"Reports that will be permanently deleted:\n{report_list}\n\n"
        f"**This action cannot be undone.**\n\n"
        f"Type `confirm` to proceed or `cancel` to abort."
    )

    return {
        "report": preview,
        "pending_confirmation": {
            "action": "delete_reports",
            "search_term": search_term,
            "report_ids": [r["id"] for r in matching],
            "count": len(matching),
        },
        "node_path": node_path,
    }


def handle_confirmation_response(state: Dict[str, Any]) -> Dict[str, Any]:
    """Process the user's confirmation or cancellation."""
    node_path = ["confirmation_executor"]

    message = state.get("user_message", "").strip().lower()
    pending = state.get("pending_confirmation", {})

    if not pending:
        return {
            "report": "No pending operation to confirm.",
            "pending_confirmation": None,
            "node_path": node_path,
        }

    if message in CANCEL_WORDS:
        return {
            "report": "Operation cancelled. No reports were deleted.",
            "pending_confirmation": None,
            "node_path": node_path,
        }

    if message in CONFIRM_WORDS:
        report_ids = set(pending.get("report_ids", []))
        search_term = pending.get("search_term", "")
        reports = _load_reports()
        remaining = [r for r in reports if r["id"] not in report_ids]
        deleted_count = len(reports) - len(remaining)

        _save_reports(remaining)
        logger.info(
            "AUDIT: Deleted %d reports matching '%s'. IDs: %s",
            deleted_count, search_term, report_ids,
        )

        return {
            "report": (
                f"🗑️  **Deletion Complete**\n\n"
                f"Successfully deleted **{deleted_count} report(s)** matching _{search_term}_.\n\n"
                f"Remaining reports in library: **{len(remaining)}**\n\n"
                f"_This action has been logged for compliance purposes._"
            ),
            "pending_confirmation": None,
            "node_path": node_path,
        }

    return {
        "report": (
            "Please type `confirm` to proceed with the deletion, "
            "or `cancel` to abort."
        ),
        "node_path": node_path,
    }
