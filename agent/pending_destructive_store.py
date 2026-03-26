"""Disk persistence for pending Saved Report deletions (per user_id).

Written from plan_delete_saved_reports so CLI cancel/confirm works even if LangGraph's
final invoke() dict omits optional state keys.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "memory", "pending_destructive.json")
)


def pending_path() -> str:
    return os.environ.get("PENDING_DESTRUCTIVE_PATH", _DEFAULT_PATH)


def load_pending(user_id: str) -> Optional[Dict[str, Any]]:
    path = pending_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            entry = data.get(user_id)
            return entry if isinstance(entry, dict) else None
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return None


def save_pending(user_id: str, payload: Dict[str, Any]) -> None:
    path = pending_path()
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data[user_id] = payload
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=True)
        logger.info("Saved pending destructive op for user_id=%s", user_id)
    except Exception as e:
        logger.warning("Failed to persist pending destructive state: %s", e)


def clear_pending(user_id: str) -> None:
    path = pending_path()
    try:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        if user_id not in data:
            return
        data.pop(user_id, None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=True)
        logger.info("Cleared pending destructive op for user_id=%s", user_id)
    except Exception as e:
        logger.warning("Failed to clear pending destructive state: %s", e)


__all__ = ["load_pending", "save_pending", "clear_pending", "pending_path"]
