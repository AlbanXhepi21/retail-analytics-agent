"""Report Generator — creates formatted analyst reports using Gemini."""

import json
import logging
import os
from typing import Dict, Any, List

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class AnalystReport(BaseModel):
    narrative: str = Field(description="A concise 2-3 sentence analyst summary of the data")
    key_insights: List[str] = Field(description="2-3 key data-driven insights")
    recommendation: str = Field(default="", description="An optional actionable recommendation based on the data")


PERSONA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "persona.json")
PREFS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "user_prefs.json")


def _load_persona() -> Dict[str, Any]:
    try:
        with open(PERSONA_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"tone": "professional", "instructions": "Be concise and data-driven."}


def _load_user_prefs(user_id: str) -> Dict[str, str]:
    try:
        with open(PREFS_PATH, "r") as f:
            all_prefs = json.load(f)
        return all_prefs.get(user_id, all_prefs.get("default", {}))
    except Exception:
        return {"output_format": "table", "detail_level": "standard"}


def _format_data_as_table(result: list, columns: list, max_rows: int = 20) -> str:
    if not result:
        return "_No data_"
    display = result[:max_rows]
    header = "| " + " | ".join(str(c) for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for row in display:
        vals = []
        for c in columns:
            v = row.get(c, "")
            if isinstance(v, float):
                vals.append(f"{v:,.2f}")
            else:
                vals.append(str(v))
        rows.append("| " + " | ".join(vals) + " |")
    table = "\n".join([header, sep] + rows)
    if len(result) > max_rows:
        table += f"\n\n_Showing {max_rows} of {len(result)} rows._"
    return table


def _format_data_as_bullets(result: list, columns: list, max_rows: int = 20) -> str:
    if not result:
        return "_No data_"
    display = result[:max_rows]
    lines = []
    for row in display:
        parts = []
        for c in columns:
            v = row.get(c, "")
            if isinstance(v, float):
                parts.append(f"**{c}**: {v:,.2f}")
            else:
                parts.append(f"**{c}**: {v}")
        lines.append("- " + ", ".join(parts))
    text = "\n".join(lines)
    if len(result) > max_rows:
        text += f"\n\n_Showing {max_rows} of {len(result)} rows._"
    return text


def generate_report(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate a formatted report with narrative and data."""
    node_path = ["report_generator"]

    result = state.get("sql_result", [])
    columns = state.get("sql_result_columns", [])
    question = state.get("user_message", "")
    user_id = state.get("user_id", "default")
    trios = state.get("retrieved_trios", [])
    chat_history = state.get("chat_history", [])

    if state.get("report"):
        return {"node_path": node_path}

    persona = _load_persona()
    prefs = _load_user_prefs(user_id)
    output_format = prefs.get("output_format", "table")
    detail_level = prefs.get("detail_level", "standard")
    max_rows = persona.get("max_rows_in_report", 20)

    if detail_level == "summary":
        max_rows = min(max_rows, 5)
    elif detail_level == "detailed":
        max_rows = min(max_rows * 2, 50)

    if output_format == "bullets":
        data_text = _format_data_as_bullets(result, columns, max_rows)
    else:
        data_text = _format_data_as_table(result, columns, max_rows)

    trio_report = ""
    if trios:
        trio_report = trios[0].get("report", "")

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.3,
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
        )
        structured_llm = llm.with_structured_output(AnalystReport)

        detail_instructions = {
            "summary": "Keep it very brief — 1-2 sentences max, only top-level takeaway.",
            "standard": "Provide a concise 2-3 sentence summary with key insights.",
            "detailed": "Provide a thorough analysis with 3-5 insights, comparisons, and context.",
        }
        system_prompt = (
            f"You are a retail data analyst. Tone: {persona['tone']}.\n"
            f"{persona['instructions']}\n"
            f"Detail level: {detail_instructions.get(detail_level, detail_instructions['standard'])}\n\n"
            "Generate an analyst report based on the data below. "
            "Do NOT repeat the raw data — the data table/bullets are already shown to the user."
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
        user_prompt += (
            f"Current user question: {question}\n\n"
            f"Data ({len(result)} rows):\n{data_text}\n"
        )
        if trio_report:
            user_prompt += f"\nReference analyst report style:\n{trio_report}\n"
        user_prompt += "\nAnalyze the data:"

        analysis = structured_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])

        narrative = analysis.narrative
        insights_md = "\n".join(f"- {insight}" for insight in analysis.key_insights)
        recommendation_md = f"\n\n**Recommendation:** {analysis.recommendation}" if analysis.recommendation else ""

    except Exception as e:
        logger.error("Report generation LLM failed, using fallback: %s", e)
        narrative = "_Automated narrative unavailable. Raw data shown below._"
        insights_md = ""
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
