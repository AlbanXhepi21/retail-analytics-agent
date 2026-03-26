"""Intent tool: classify a user message into routing intent.

Kept as a "tool" callable so the controller can run it as a step.
"""

import logging
import os
import re
from enum import Enum
from typing import Dict, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_VERSION = os.environ.get("GEMINI_API_VERSION", "v1")

PII_EXTRACTION_PATTERNS = [
    r"\bemail",
    r"\bphone\s*number",
    r"\bmobile\b",
    r"\bstreet\s*address",
    r"\bpostal\s*code\b",
    r"\bgive me.+phone\b",
    r"\blist.+phone\b",
    r"\baddress\b.*\bcustomer\b",
    r"\bcustomer\b.*\baddress\b",
    r"\bcontact\s*(info|information|details)\b",
]


class IntentType(str, Enum):
    analysis = "analysis"
    out_of_scope = "out_of_scope"
    destructive_saved_reports = "destructive_saved_reports"


class IntentClassification(BaseModel):
    intent: IntentType = Field(
        description=(
            "The classified intent. Use 'analysis' for any data/analytics question "
            "(including follow-ups referencing previous queries). "
            "Use 'out_of_scope' for greetings, chitchat, or questions unrelated to "
            "retail data analysis."
        )
    )


CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier for a retail analytics agent that queries BigQuery and manages a Saved Reports library (local JSON, not the warehouse).

Classify the user's message into one of these intents:
- "analysis": Any question that requires querying sales, revenue, customer, product, or order data. This INCLUDES follow-up questions that reference a previous analysis (e.g. "what about Germany", "now for Women", "break that down by month", "same but yearly").
- "destructive_saved_reports": The user wants to DELETE, REMOVE, or PURGE entries from the Saved Reports library (e.g. GDPR: "delete all reports mentioning Client X", "remove saved reports about Acme"). This is NOT about SQL or BigQuery — only about the saved report documents.
- "out_of_scope": Greetings, chitchat, or questions unrelated to retail data analysis or saved-report deletion.

IMPORTANT: If conversation history is provided and the current message looks like a follow-up to analytics (e.g. "what about X"), classify as "analysis". Destructive saved-report requests are usually explicit about deleting/removing "reports" or "saved reports".
"""


def _destructive_keyword(msg_lower: str) -> bool:
    """Heuristic: delete/remove saved reports (library), not BigQuery."""
    if not re.search(r"\b(delete|remove|purge)\b", msg_lower):
        return False
    if not re.search(r"\b(report|reports|saved)\b", msg_lower):
        return False
    return True


def _matches(text: str, patterns: list) -> bool:
    return any(re.search(p, text) for p in patterns)


def _format_history_for_classifier(chat_history: list) -> str:
    if not chat_history:
        return ""
    recent = chat_history[-4:]
    lines = []
    for msg in recent:
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role}: {msg['content'][:150]}")
    return "\nRecent conversation:\n" + "\n".join(lines)


ANALYSIS_KEYWORDS = [
    r"\brevenue\b",
    r"\bsales\b",
    r"\bspend\b",
    r"\bcustomer\b",
    r"\bproduct\b",
    r"\border\b",
    r"\btop\b",
    r"\bbest\b",
    r"\bworst\b",
    r"\btrend\b",
    r"\bmonthly\b",
    r"\bweekly\b",
    r"\byearly\b",
    r"\baverage\b",
    r"\btotal\b",
    r"\bcount\b",
    r"\bcategory\b",
    r"\bbrand\b",
    r"\bcountry\b",
    r"\bregion\b",
    r"\bperformance\b",
    r"\bunderperforming\b",
    r"\bgrowth\b",
    r"\bdecline\b",
    r"\bprofit\b",
    r"\bmargin\b",
    r"\bprice\b",
    r"\bquantity\b",
    r"\binventory\b",
    r"\bdemographic\b",
    r"\bage group\b",
    r"\bgender\b",
    r"\breturn\b",
    r"\bcancel\b",
    r"\bship\b",
    r"\bstatus\b",
    r"\bbreakdown\b",
    r"\bcompare\b",
    r"\bhow many\b",
    r"\bhow much\b",
    r"\bshow me\b",
]


def _keyword_fallback(msg_lower: str, node_path: list) -> Dict[str, Any]:
    if _destructive_keyword(msg_lower):
        logger.info("Intent: destructive_saved_reports (keyword fallback)")
        return {"intent": "destructive_saved_reports", "node_path": node_path}
    if _matches(msg_lower, ANALYSIS_KEYWORDS):
        logger.info("Intent: analysis (keyword fallback)")
        return {"intent": "analysis", "node_path": node_path}
    logger.info("Intent: out_of_scope (keyword fallback)")
    return {
        "intent": "out_of_scope",
        "node_path": node_path,
        "report": (
            "I'm a retail analytics assistant and can only answer data "
            "analysis questions about sales, customers, products, and orders.\n\n"
            "**Example questions I can help with:**\n"
            "- Who are the top customers by spend?\n"
            "- What is the monthly revenue trend?\n"
            "- Which product categories sell the most?\n"
            "- What is the order status breakdown?"
        ),
    }


def classify_intent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Classify intent: regex for safety gates, then LLM for routing."""
    msg = state.get("user_message", "").strip()
    msg_lower = msg.lower()
    chat_history = state.get("chat_history", [])
    node_path = ["intent_classifier"]

    if _matches(msg_lower, PII_EXTRACTION_PATTERNS):
        logger.info("Intent: out_of_scope (PII extraction attempt, regex gate)")
        return {
            "intent": "out_of_scope",
            "node_path": node_path,
            "report": (
                "I'm unable to provide customer personal information such as "
                "emails, phone numbers, or addresses. I can help you with "
                "aggregated analytics — try asking about revenue, product "
                "performance, or customer segments instead."
            ),
        }

    if _destructive_keyword(msg_lower):
        logger.info("Intent: destructive_saved_reports (regex gate)")
        return {"intent": "destructive_saved_reports", "node_path": node_path}

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            temperature=0,
            max_retries=1,
            timeout=10,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            model_kwargs={"api_version": GEMINI_API_VERSION},
        )
        structured_llm = llm.with_structured_output(IntentClassification)

        history_text = _format_history_for_classifier(chat_history)
        user_prompt = ""
        if history_text:
            user_prompt += history_text + "\n\n"
        user_prompt += f"Current message: {msg}"

        result = structured_llm.invoke(
            [
                SystemMessage(content=CLASSIFIER_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ]
        )

        intent = result.intent.value
        logger.info("Intent: %s (LLM classified)", intent)

        if intent == "out_of_scope":
            return {
                "intent": "out_of_scope",
                "node_path": node_path,
                "report": (
                    "I'm a retail analytics assistant and can only answer data "
                    "analysis questions about sales, customers, products, and orders.\n\n"
                    "**Example questions I can help with:**\n"
                    "- Who are the top customers by spend?\n"
                    "- What is the monthly revenue trend?\n"
                    "- Which product categories sell the most?\n"
                    "- What is the order status breakdown?"
                ),
            }

        if intent == "destructive_saved_reports":
            return {"intent": "destructive_saved_reports", "node_path": node_path}

        return {"intent": intent, "node_path": node_path}

    except Exception as e:
        logger.warning("LLM classifier failed, falling back to keyword matching: %s", e)
        return _keyword_fallback(msg_lower, node_path)


def is_destructive_saved_reports_message(message: str) -> bool:
    """True if the message targets the Saved Reports library for deletion (heuristic)."""
    return _destructive_keyword((message or "").strip().lower())


__all__ = ["classify_intent", "is_destructive_saved_reports_message"]

