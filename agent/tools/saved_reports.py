"""High-stakes Saved Reports tools: plan deletion (preview) and execute after confirmation."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from agent.pending_destructive_store import clear_pending, save_pending
from tools.saved_reports_store import (
    delete_by_ids,
    extract_client_query_from_message,
    search_reports_matching,
)

logger = logging.getLogger(__name__)


def plan_delete_saved_reports(state: Dict[str, Any]) -> Dict[str, Any]:
    """Find reports matching the user query; return preview text and pending payload (no deletes)."""
    node_path = ["plan_delete_saved_reports"]
    uid = str(state.get("user_id", "default"))
    raw = (state.get("user_message") or "").strip()
    query = extract_client_query_from_message(raw)
    if not query:
        return {
            "node_path": node_path,
            "report": (
                "I can delete **saved reports** that mention a specific client or phrase. "
                "Example: *Delete all saved reports mentioning Acme Corp*\n\n"
                "Please say which name or phrase to match."
            ),
        }

    matches: List[Dict[str, Any]] = search_reports_matching(query)
    if not matches:
        return {
            "node_path": node_path,
            "report": (
                f"No saved reports matched **{query}**. Nothing to delete.\n\n"
                "You can list what is in the library by asking what saved reports exist."
            ),
        }

    lines = []
    ids: List[str] = []
    for r in matches:
        rid = str(r.get("id", ""))
        title = str(r.get("title", ""))
        ids.append(rid)
        lines.append(f"- **{title}** (`{rid}`)")

    preview = "\n".join(lines)
    pending: Dict[str, Any] = {
        "user_id": uid,
        "query": query,
        "report_ids": ids,
        "titles": [str(r.get("title", "")) for r in matches],
    }

    report = (
        "⚠️ **CONFIRMATION REQUIRED — Destructive action**\n\n"
        f"You asked to delete saved reports matching: **{query}**\n\n"
        f"The following **{len(ids)}** report(s) would be **permanently removed** "
        "from the Saved Reports library (this does not affect BigQuery data):\n\n"
        f"{preview}\n\n"
        "**This cannot be undone.**\n\n"
        "Reply with **`confirm`** to proceed or **`cancel`** to abort."
    )

    logger.info("Planned deletion for user=%s query=%s count=%s", uid, query, len(ids))

    save_pending(uid, pending)

    return {
        "node_path": node_path,
        "report": report,
        "pending_destructive": pending,
        "destructive_phase": "awaiting_confirmation",
    }


def execute_delete_saved_reports(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute deletion only when `pending_destructive` matches current user (set after confirm)."""
    node_path = ["execute_delete_saved_reports"]
    uid = str(state.get("user_id", "default"))
    pending = state.get("pending_destructive") or {}
    if pending.get("user_id") != uid:
        return {
            "node_path": node_path,
            "report": (
                "There is no pending deletion for your session, or the confirmation expired. "
                "Ask again to delete saved reports if you still need to."
            ),
            "pending_destructive": None,
        }

    ids: List[str] = list(pending.get("report_ids") or [])
    if not ids:
        clear_pending(uid)
        return {
            "node_path": node_path,
            "report": "No report IDs were pending; nothing was deleted.",
            "pending_destructive": None,
        }

    n = delete_by_ids(ids)
    clear_pending(uid)
    q = str(pending.get("query", ""))
    report = (
        "🗑️ **Deletion complete**\n\n"
        f"Removed **{n}** saved report(s) matching **{q}**.\n\n"
        "_This action has been logged for compliance._"
    )
    logger.info("Executed deletion user=%s removed=%s", uid, n)

    return {
        "node_path": node_path,
        "report": report,
        "pending_destructive": None,
        "destructive_phase": "completed",
        "destructive_deleted_count": n,
    }


__all__ = ["plan_delete_saved_reports", "execute_delete_saved_reports"]
