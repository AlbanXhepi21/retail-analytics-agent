"""Preference handler — detects and persists user format preferences from conversation."""

import json
import logging
import os
import re
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

PREFS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "user_prefs.json")

FORMAT_PATTERNS = {
    "table": [
        r"\bprefer\s+tables?\b", r"\buse\s+tables?\b", r"\bshow\s+(me\s+)?tables?\b",
        r"\bswitch\s+to\s+tables?\b", r"\btable\s+format\b", r"\bi\s+like\s+tables?\b",
        r"\btabular\b",
    ],
    "bullets": [
        r"\bprefer\s+bullets?\b", r"\buse\s+bullets?\b", r"\bshow\s+(me\s+)?bullets?\b",
        r"\bswitch\s+to\s+bullets?\b", r"\bbullet\s+(point\s+)?format\b",
        r"\bi\s+like\s+bullets?\b", r"\bbullet\s*points?\b", r"\blist\s+format\b",
    ],
}

DETAIL_PATTERNS = {
    "summary": [
        r"\bkeep\s+it\s+(short|brief)\b", r"\bsummary\s+(mode|format|level)\b",
        r"\bless\s+detail\b", r"\bjust\s+the\s+(highlights|summary)\b",
        r"\bconcise\b",
    ],
    "detailed": [
        r"\bmore\s+detail\b", r"\bdetailed\b", r"\bin[\s-]depth\b",
        r"\bgive\s+me\s+everything\b", r"\bfull\s+(analysis|detail|report)\b",
        r"\bthorough\b",
    ],
    "standard": [
        r"\bstandard\b", r"\bnormal\b", r"\bdefault\s+(format|detail|level)\b",
        r"\breset\s+(my\s+)?preferences?\b",
    ],
}

FORMAT_KEYWORDS = {
    "table": {"table", "tables", "tabular", "grid"},
    "bullets": {"bullet", "bullets", "bulletpoint", "bulletpoints", "list", "lists"},
}

DETAIL_KEYWORDS = {
    "summary": {"short", "shorter", "brief", "briefer", "concise", "compact", "summary"},
    "detailed": {"detail", "detailed", "longer", "thorough", "depth", "extensive", "everything"},
    "standard": {"standard", "normal", "default", "reset"},
}


def _load_all_prefs() -> Dict[str, Any]:
    try:
        with open(PREFS_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"default": {"output_format": "table", "detail_level": "standard"}}


def _save_all_prefs(prefs: Dict[str, Any]) -> None:
    with open(PREFS_PATH, "w") as f:
        json.dump(prefs, f, indent=2)


def detect_preference(message: str) -> Optional[Dict[str, str]]:
    """Check if a message contains a preference-setting request. Returns changes or None."""
    msg_lower = message.lower()
    changes = {}

    for fmt, patterns in FORMAT_PATTERNS.items():
        if any(re.search(p, msg_lower) for p in patterns):
            changes["output_format"] = fmt
            break

    if "output_format" not in changes:
        words = re.findall(r"[a-z]+", msg_lower)
        best_fmt = None
        best_pos = -1
        for fmt, keywords in FORMAT_KEYWORDS.items():
            for i, w in enumerate(words):
                if w in keywords and i > best_pos:
                    best_fmt = fmt
                    best_pos = i
        if best_fmt:
            changes["output_format"] = best_fmt

    for level, patterns in DETAIL_PATTERNS.items():
        if any(re.search(p, msg_lower) for p in patterns):
            changes["detail_level"] = level
            break

    if "detail_level" not in changes:
        words = re.findall(r"[a-z]+", msg_lower)
        best_level = None
        best_pos = -1
        for level, keywords in DETAIL_KEYWORDS.items():
            for i, w in enumerate(words):
                if w in keywords and i > best_pos:
                    best_level = level
                    best_pos = i
        if best_level:
            changes["detail_level"] = best_level

    return changes if changes else None


def handle_preference_update(state: Dict[str, Any]) -> Dict[str, Any]:
    """Detect preference changes in the user message, persist them, and confirm."""
    node_path = ["preference_handler"]
    user_id = state.get("user_id", "default")
    message = state.get("user_message", "")

    changes = detect_preference(message)
    if not changes:
        return {
            "report": "I didn't catch a specific preference. You can say things like:\n"
                      "- \"I prefer bullet points\"\n"
                      "- \"Switch to table format\"\n"
                      "- \"More detail\" or \"Keep it brief\"",
            "node_path": node_path,
        }

    all_prefs = _load_all_prefs()
    user_prefs = all_prefs.get(user_id, all_prefs.get("default", {}).copy())

    old_prefs = user_prefs.copy()
    user_prefs.update(changes)
    all_prefs[user_id] = user_prefs
    _save_all_prefs(all_prefs)

    logger.info("Updated preferences for user '%s': %s -> %s", user_id, old_prefs, user_prefs)

    change_descriptions = []
    if "output_format" in changes:
        change_descriptions.append(f"output format → **{changes['output_format']}**")
    if "detail_level" in changes:
        change_descriptions.append(f"detail level → **{changes['detail_level']}**")

    return {
        "report": (
            f"Preferences updated for **{user_id}**:\n"
            + "\n".join(f"- {d}" for d in change_descriptions)
            + "\n\nYour future reports will reflect these changes."
        ),
        "preference_updated": True,
        "node_path": node_path,
    }
