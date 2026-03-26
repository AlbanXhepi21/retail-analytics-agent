"""Shared cancel / confirm phrase detection for CLI and tests (no LangGraph import)."""

CANCEL_WORDS = frozenset(
    {
        "cancel",
        "no",
        "n",
        "nope",
        "abort",
        "stop",
        "don't",
        "dont",
        "nevermind",
        "never mind",
    }
)
_CANCEL_TYPOS = frozenset({"cancle", "cacnel", "cnacel", "cancell", "canecl"})


def is_cancel_message(text: str) -> bool:
    m = (text or "").strip().lower().rstrip(".,!?")
    return m in CANCEL_WORDS or m in _CANCEL_TYPOS


__all__ = ["CANCEL_WORDS", "is_cancel_message"]
