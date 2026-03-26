"""SQL Generator — uses Gemini + schema context + Golden Bucket examples."""

import logging
import os
from typing import Dict, Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
MAX_SQL_RETRIES = 3

_UNRECOVERABLE_KEYWORDS = [
    "api_key_invalid", "api key not valid",
    "resource_exhausted", "quota exceeded",
    "permission_denied", "forbidden",
    "invalid_argument",
    "not_found", "no longer available",
]

class SQLQuery(BaseModel):
    sql: str = Field(description="The BigQuery SQL query without markdown fences, explanation, or comments")


def _schema_context() -> str:
    try:
        from tools.bq_client import TABLE_SCHEMA_CONTEXT
        return TABLE_SCHEMA_CONTEXT
    except Exception:
        return (
            "Dataset: bigquery-public-data.thelook_ecommerce\n"
            "Tables: orders, order_items, products, users"
        )


SQL_SYSTEM_PROMPT = f"""You are a BigQuery SQL expert for a retail analytics platform.

{_schema_context()}

CRITICAL RULES:
1. NEVER select email, phone, street_address, postal_code, latitude, or longitude.
2. Always use fully qualified table names with backticks: `bigquery-public-data.thelook_ecommerce.<table>`.
3. Use sale_price from order_items for revenue (NOT retail_price from products).
4. Exclude Cancelled and Returned orders unless specifically asked about them.
5. Use proper BigQuery SQL syntax (FORMAT_TIMESTAMP, EXTRACT, etc.).
6. Add LIMIT if the user doesn't specify one (default LIMIT 20 for lists).
7. If the user references previous questions (e.g. "same thing but for Women", "break that down by month"), use the conversation history to resolve what they mean.
"""


def _format_history(chat_history: list) -> str:
    if not chat_history:
        return ""
    lines = ["\nRecent conversation history (for resolving references like 'same thing', 'those', 'that category', etc.):"]
    for msg in chat_history:
        role = "User" if msg["role"] == "user" else "Assistant"
        content = msg["content"][:200]
        lines.append(f"  {role}: {content}")
    return "\n".join(lines)


def _build_prompt(question: str, trios: list, chat_history: list, sql_error: str = "") -> str:
    parts = []

    history_text = _format_history(chat_history)
    if history_text:
        parts.append(history_text)

    parts.append(f"\nCurrent user question: {question}")

    if trios:
        parts.append("\nHere are similar analyst-verified queries for reference:")
        for i, trio in enumerate(trios):
            if trio.get("sql"):
                parts.append(
                    f"\nExample {i+1}:\n"
                    f"  Question: {trio['question']}\n"
                    f"  SQL: {trio['sql']}"
                )

    if sql_error:
        parts.append(
            f"\n⚠️ The previous SQL attempt failed with this error:\n{sql_error}\n"
            "Fix the error and generate a corrected query."
        )

    parts.append("\nGenerate the SQL query:")
    return "\n".join(parts)


def generate_sql(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a BigQuery SQL query using Gemini with schema + Golden Bucket context."""
    node_path = ["sql_generator"]

    question = state.get("user_message", "")
    trios = state.get("retrieved_trios", [])
    chat_history = state.get("chat_history", [])
    sql_error = state.get("sql_error", "")
    retry_count = state.get("sql_retry_count", 0)

    logger.info("Generating SQL (attempt %d)", retry_count + 1)

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.1,
            max_retries=1,
            timeout=30,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
        )
        structured_llm = llm.with_structured_output(SQLQuery)

        prompt = _build_prompt(question, trios, chat_history, sql_error)
        response = structured_llm.invoke([
            SystemMessage(content=SQL_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])

        sql = response.sql.strip()
        logger.info("Generated SQL: %s", sql[:200])

        return {
            "generated_sql": sql,
            "sql_error": "",
            "node_path": node_path,
        }

    except Exception as e:
        error_lower = str(e).lower()
        is_unrecoverable = any(kw in error_lower for kw in _UNRECOVERABLE_KEYWORDS)

        if is_unrecoverable:
            logger.error("Unrecoverable LLM error (skipping retries): %s", e)
            return {
                "generated_sql": "",
                "sql_error": f"LLM error: {e}",
                "sql_retry_count": MAX_SQL_RETRIES,
                "node_path": node_path,
                "error_message": f"SQL generation failed: {e}",
            }

        logger.error("SQL generation LLM call failed: %s", e)
        return {
            "generated_sql": "",
            "sql_error": f"LLM error: {e}",
            "sql_retry_count": retry_count + 1,
            "node_path": node_path,
            "error_message": f"SQL generation failed: {e}",
        }
