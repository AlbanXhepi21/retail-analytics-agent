"""Intent classifier — regex safety gates + LLM-based routing with conversation context."""

import logging
import os
import re
from enum import Enum
from typing import Dict, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DESTRUCTIVE_KEYWORDS = [
    r"\bdelete\b", r"\bremove\b", r"\bpurge\b", r"\berase\b",
    r"\bwipe\b", r"\bdestroy\b", r"\bdrop\b",
]

PII_EXTRACTION_PATTERNS = [
    r"\bemail", r"\bphone\s*number", r"\bmobile\b",
    r"\bstreet\s*address", r"\bpostal\s*code\b",
    r"\bgive me.+phone\b", r"\blist.+phone\b",
    r"\baddress\b.*\bcustomer\b", r"\bcustomer\b.*\baddress\b",
    r"\bcontact\s*(info|information|details)\b",
]

PREFERENCE_PATTERNS = [
    r"\bprefer\s+(tables?|bullets?|bullet\s*points?|list)\b",
    r"\bswitch\s+to\s+(tables?|bullets?|list)\b",
    r"\bi\s+like\s+(tables?|bullets?)\b",
    r"\b(table|bullet|tabular|list)\s+format\b",
    r"\bmore\s+detail\b", r"\bless\s+detail\b",
    r"\bkeep\s+it\s+(short|brief)\b", r"\bconcise\b",
    r"\bin[\s-]depth\b", r"\bdetailed\b.*\breports?\b",
    r"\bsummary\s+(mode|format|level)\b",
    r"\breset\s+(my\s+)?preferences?\b",
]


class IntentType(str, Enum):
    analysis = "analysis"
    schema_question = "schema_question"
    preference = "preference"
    out_of_scope = "out_of_scope"


class IntentClassification(BaseModel):
    intent: IntentType = Field(
        description=(
            "The classified intent. Use 'analysis' for any data/analytics question "
            "(including follow-ups referencing previous queries). "
            "Use 'schema_question' for questions about database structure, tables, or columns. "
            "Use 'preference' when the user wants to change their output format "
            "(tables vs bullets), detail level (brief vs detailed), or reset preferences. "
            "Use 'out_of_scope' for greetings, chitchat, or questions unrelated to "
            "retail data analysis AND not related to output preferences."
        )
    )


CLASSIFIER_SYSTEM_PROMPT = """You are an intent classifier for a retail analytics agent that queries BigQuery.

Classify the user's message into one of these intents:
- "analysis": Any question that requires querying sales, revenue, customer, product, or order data. This INCLUDES follow-up questions that reference a previous analysis (e.g. "what about Germany", "now for Women", "break that down by month", "same but yearly").
- "schema_question": Questions about what tables, columns, or data is available in the database.
- "preference": The user wants to change how reports are displayed — output format (tables, bullet points, lists), detail level (brief/concise, standard, detailed/in-depth), or reset their preferences. Examples: "can you give me tables instead?", "I want shorter reports", "show results as a list", "make reports more detailed".
- "out_of_scope": Greetings, chitchat, or questions unrelated to BOTH retail data analysis AND output preferences.

IMPORTANT: If conversation history is provided and the current message looks like a follow-up (e.g. "what about X", "and for Y", "same thing but Z", "now do it for..."), classify it as "analysis" because the user is continuing a data analysis conversation.

IMPORTANT: Do NOT classify preference/format requests as "out_of_scope". If the user is talking about how they want their output displayed, that is "preference".
"""


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


def classify_intent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Classify intent: regex for safety gates, LLM for routing."""
    msg = state.get("user_message", "").strip()
    msg_lower = msg.lower()
    chat_history = state.get("chat_history", [])
    node_path = ["intent_classifier"]

    if state.get("pending_confirmation"):
        logger.info("Intent: pending_confirmation (continuing confirmation flow)")
        return {
            "intent": "pending_confirmation",
            "node_path": node_path,
        }

    if _matches(msg_lower, DESTRUCTIVE_KEYWORDS):
        logger.info("Intent: destructive (regex gate)")
        return {"intent": "destructive", "node_path": node_path}

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

    if _matches(msg_lower, PREFERENCE_PATTERNS):
        logger.info("Intent: preference (regex gate)")
        return {"intent": "preference", "node_path": node_path}

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0,
            max_retries=1,
            timeout=10,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
        )
        structured_llm = llm.with_structured_output(IntentClassification)

        history_text = _format_history_for_classifier(chat_history)
        user_prompt = ""
        if history_text:
            user_prompt += history_text + "\n\n"
        user_prompt += f"Current message: {msg}"

        result = structured_llm.invoke([
            SystemMessage(content=CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ])

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

        return {"intent": intent, "node_path": node_path}

    except Exception as e:
        logger.warning("LLM classifier failed, falling back to keyword matching: %s", e)
        return _keyword_fallback(msg_lower, node_path)


SCHEMA_KEYWORDS = [
    r"\bwhat tables\b", r"\bwhat columns\b", r"\bdatabase structure\b",
    r"\bschema\b", r"\bwhat data\b", r"\bavailable tables\b",
    r"\bdescribe.+table\b", r"\btable.+structure\b",
    r"\bwhat.+in the database\b",
]

ANALYSIS_KEYWORDS = [
    r"\brevenue\b", r"\bsales\b", r"\bspend\b", r"\bcustomer\b",
    r"\bproduct\b", r"\border\b", r"\btop\b", r"\bbest\b",
    r"\bworst\b", r"\btrend\b", r"\bmonthly\b", r"\bweekly\b",
    r"\byearly\b", r"\baverage\b", r"\btotal\b", r"\bcount\b",
    r"\bcategory\b", r"\bbrand\b", r"\bcountry\b", r"\bregion\b",
    r"\bperformance\b", r"\bunderperforming\b", r"\bgrowth\b",
    r"\bdecline\b", r"\bprofit\b", r"\bmargin\b", r"\bprice\b",
    r"\bquantity\b", r"\binventory\b", r"\bdemographic\b",
    r"\bage group\b", r"\bgender\b", r"\breturn\b", r"\bcancel\b",
    r"\bship\b", r"\bstatus\b", r"\bbreakdown\b", r"\bcompare\b",
    r"\bhow many\b", r"\bhow much\b", r"\bshow me\b",
]


def _keyword_fallback(msg_lower: str, node_path: list) -> Dict[str, Any]:
    """Fallback to regex keywords if the LLM classifier is unavailable."""
    if _matches(msg_lower, PREFERENCE_PATTERNS):
        logger.info("Intent: preference (keyword fallback)")
        return {"intent": "preference", "node_path": node_path}

    if _matches(msg_lower, SCHEMA_KEYWORDS):
        logger.info("Intent: schema_question (keyword fallback)")
        return {"intent": "schema_question", "node_path": node_path}

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
