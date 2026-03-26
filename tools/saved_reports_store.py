"""Persistent Saved Reports library (JSON file). Used for GDPR-style destructive flows."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_PATH = os.path.join(_REPO_ROOT, "data", "saved_reports.json")


def _path() -> str:
    return os.environ.get("SAVED_REPORTS_PATH", DEFAULT_PATH)


def load_reports() -> List[Dict[str, Any]]:
    path = _path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except FileNotFoundError:
        logger.warning("Saved reports file missing at %s; starting empty", path)
    except json.JSONDecodeError as e:
        logger.error("Invalid JSON in saved reports: %s", e)
    return []


def save_reports(reports: List[Dict[str, Any]]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(reports, f, indent=2, ensure_ascii=True)


def search_reports_matching(query: str) -> List[Dict[str, Any]]:
    """Case-insensitive substring match on title + content."""
    q = (query or "").strip().lower()
    if not q:
        return []
    out: List[Dict[str, Any]] = []
    for r in load_reports():
        title = str(r.get("title", "")).lower()
        content = str(r.get("content", "")).lower()
        if q in title or q in content:
            out.append(r)
    return out


def delete_by_ids(ids: List[str]) -> int:
    """Remove reports whose id is in ids. Returns number removed."""
    if not ids:
        return 0
    want = set(ids)
    current = load_reports()
    n_before = len(current)
    kept = [r for r in current if str(r.get("id", "")) not in want]
    n_removed = n_before - len(kept)
    if n_removed:
        save_reports(kept)
    return n_removed


def extract_client_query_from_message(message: str) -> str | None:
    """Best-effort extraction of 'Client X' from natural language delete requests."""
    text = (message or "").strip()
    if not text:
        return None
    lower = text.lower()
    if not any(k in lower for k in ("delete", "remove", "purge")):
        return None
    if "report" not in lower and "saved" not in lower:
        return None

    patterns = [
        r"(?:delete|remove|purge)\s+(?:all\s+)?(?:saved\s+)?reports?\s+(?:mentioning|about|for|containing|with)\s+[\"']?(.+?)[\"']?\s*$",
        r"(?:delete|remove)\s+(?:reports?\s+)?(?:that\s+)?(?:mention|mentions)\s+[\"']?(.+?)[\"']?\s*$",
        r"(?:gdpr|compliance)\s+(?:purge|delete)\s+(?:for|about)\s+[\"']?(.+?)[\"']?\s*$",
        r"(?:delete|remove)\s+[\"']?(.+?)[\"']?\s+from\s+(?:the\s+)?saved\s+reports?\s*$",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE | re.DOTALL)
        if m:
            q = m.group(1).strip()
            q = re.sub(r'[.!?]+$', "", q).strip()
            if q:
                return q
    return None


__all__ = [
    "load_reports",
    "save_reports",
    "search_reports_matching",
    "delete_by_ids",
    "extract_client_query_from_message",
]
