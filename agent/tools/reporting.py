"""Reporting tool: turn results into an analyst report.

Minimal build:
- persona-driven tone/instructions
- markdown table output
"""

import json
import logging
import os
from typing import Dict, Any, List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_VERSION = os.environ.get("GEMINI_API_VERSION", "v1")


class AnalystReport(BaseModel):
    narrative: str = Field(description="A concise 2-3 sentence analyst summary of the data")
    key_insights: List[str] = Field(description="2-3 key data-driven insights")
    recommendation: str = Field(default="", description="An optional actionable recommendation based on the data")


PERSONA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "persona.json")


def _load_persona() -> Dict[str, Any]:
    try:
        with open(PERSONA_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"tone": "professional", "instructions": "Be concise and data-driven."}


def _pretty_column_name(name: str, all_columns: list | None = None) -> str:
    text = str(name or "").replace("_", " ").strip()
    if not text:
        return "Field"
    lowered = text.lower()
    cols = {str(c).lower() for c in (all_columns or [])}
    if lowered == "id":
        if "first_name" in cols or "last_name" in cols:
            return "Customer ID"
        if "product_id" in cols or "category" in cols or "product_title" in cols:
            return "Product ID"
        return "ID"
    return text[:1].upper() + text[1:]


def _format_data_as_table(result: list, columns: list, max_rows: int = 20) -> str:
    if not result:
        return "_No data_"
    display = result[:max_rows]
    pretty_columns = [_pretty_column_name(c, columns) for c in columns]
    header = "| " + " | ".join(pretty_columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for row in display:
        vals = []
        for c in columns:
            v = row.get(c, "")
            if v is None:
                vals.append("N/A")
            elif isinstance(v, float):
                vals.append(f"{v:,.2f}")
            else:
                vals.append(str(v))
        rows.append("| " + " | ".join(vals) + " |")
    table = "\n".join([header, sep] + rows)
    if len(result) > max_rows:
        table += f"\n\n_Showing {max_rows} of {len(result)} rows._"
    return table


def _pick_metric_column(columns: list) -> str:
    priority = ["total_revenue", "revenue", "total_spend", "sales", "amount", "count"]
    lowered = {str(c).lower(): c for c in columns}
    for key in priority:
        if key in lowered:
            return lowered[key]
    return ""


def _deterministic_fallback_summary(question: str, result: list, columns: list) -> tuple[str, str]:
    if not result:
        return (
            "No matching records were found for this request.",
            "- Try broadening the date range or filters.\n- If helpful, I can suggest alternate cuts of this metric.",
        )

    row_count = len(result)
    metric_col = _pick_metric_column(columns)
    if metric_col:
        top_row = result[0]
        metric_value = top_row.get(metric_col)
        metric_pretty = _pretty_column_name(metric_col)
        entity_cols = [c for c in columns if c != metric_col][:3]
        entity_value = ", ".join(
            str(top_row.get(c, "N/A")) for c in entity_cols if top_row.get(c) is not None
        )
        narrative = (
            f"Showing {row_count} result rows for your request. "
            f"The leading record is {entity_value or 'the first entity'} with {metric_pretty.lower()} "
            f"of {metric_value:,.2f}."
            if isinstance(metric_value, (int, float))
            else f"Showing {row_count} result rows for your request. The first row appears to be the top match."
        )
        insights = [
            f"- The table is sorted so the top rows represent the highest values by {_pretty_column_name(metric_col).lower()}.",
            "- Use filters (country, timeframe, segment) to focus this list for decision-making.",
        ]
        return narrative, "\n".join(insights)

    return (
        f"Showing {row_count} result rows for your request.",
        "- The first rows are typically the strongest matches.\n- Add filters to narrow this view to the segment you care about.",
    )


def generate_report(state: Dict[str, Any]) -> Dict[str, Any]:
    node_path = ["report_generator"]

    result = state.get("sql_result", [])
    columns = state.get("sql_result_columns", [])
    question = state.get("user_message", "")
    trios = state.get("retrieved_trios", [])
    chat_history = state.get("chat_history", [])

    if state.get("report"):
        return {"node_path": node_path}

    persona = _load_persona()
    max_rows = int(persona.get("max_rows_in_report", 20))
    data_text = _format_data_as_table(result, columns, max_rows)

    trio_report = trios[0].get("report", "") if trios else ""

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatGoogleGenerativeAI(
            model=GEMINI_MODEL,
            temperature=0.3,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            model_kwargs={"api_version": GEMINI_API_VERSION},
        )
        structured_llm = llm.with_structured_output(AnalystReport)

        system_prompt = (
            f"You are a retail data analyst. Tone: {persona.get('tone', 'professional')}.\n"
            f"{persona.get('instructions', 'Be concise and data-driven.')}\n\n"
            "Generate an analyst report based on the data below. "
            "Do NOT repeat the raw data — the data table is already shown to the user."
        )

        history_context = ""
        if chat_history:
            recent = chat_history[-4:]
            history_lines = []
            for msg in recent:
                role = "User" if msg["role"] == "user" else "Assistant"
                history_lines.append(f"  {role}: {msg['content'][:150]}")
            history_context = "\nRecent conversation:\n" + "\n".join(history_lines) + "\n"

        user_prompt = ""
        if history_context:
            user_prompt += history_context + "\n"
        user_prompt += f"Current user question: {question}\n\nData ({len(result)} rows):\n{data_text}\n"
        if trio_report:
            user_prompt += f"\nReference analyst report style:\n{trio_report}\n"
        user_prompt += "\nAnalyze the data:"

        analysis = structured_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )

        narrative = analysis.narrative
        insights_md = "\n".join(f"- {insight}" for insight in analysis.key_insights)
        recommendation_md = (
            f"\n\n**Recommendation:** {analysis.recommendation}" if analysis.recommendation else ""
        )

    except Exception as e:
        logger.error("Report generation LLM failed, using fallback: %s", e)
        narrative, insights_md = _deterministic_fallback_summary(question, result, columns)
        recommendation_md = ""

    header = persona.get("report_header", "## Retail Analytics Report")
    sign_off = persona.get("sign_off", "")
    pii_note = ""
    if state.get("pii_masked"):
        pii_note = "\n> ⚠️ _Some personal data was redacted from this report for privacy compliance._\n"

    report = (
        f"{header}\n\n"
        f"**Your Question:** _{question}_\n"
        f"{pii_note}\n"
        f"### Analysis\n{narrative}\n\n"
        f"### Key Insights\n{insights_md}{recommendation_md}\n\n"
        f"### Data\n{data_text}\n\n"
        f"---\n_{sign_off}_"
    )

    return {"report": report, "node_path": node_path}


__all__ = ["generate_report"]

