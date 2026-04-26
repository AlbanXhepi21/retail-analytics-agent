"""
AgentByte version of the Text-to-SQL agent (minimal first pass).

This file intentionally contains only:
- the main AgentByte agent
- one SQL execution tool
- the prompt
"""

import json
import logging

from sqlalchemy import create_engine, text
from sqlmodel import Session

from agentbyte import Agent, serve
from agentbyte.llm import AzureOpenAIChatCompletionClient
from agentbyte.microwebui import create_app
from agentbyte.tools import FunctionTool
from genailib.dbs.models.database_models import get_schema_description_text
from genailib.gateways.aws.secretmanager import (
    PydanticSecretFetcher,
    SecretsManagerClient,
)
from genailib.settings.core import AppSettings, ProcDBSettings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""
You are an expert SQL assistant for procurement contract analytics.

You MUST always use the tool `run_analytics_query` to answer.

Rules:
1. Only write SELECT queries.
2. Use exact column names from the schema.
3. Return business-friendly answers.
4. Never expose internal IDs unless user explicitly asks.
5. If result is empty, explain clearly and suggest a better filter.

Database schema:
{get_schema_description_text()}
"""


def run_analytics_query(sql_query: str) -> str:
    """
    Execute a read-only SQL query against procurement contract tables.
    """
    upper_query = sql_query.strip().upper()
    if not upper_query.startswith("SELECT"):
        return "Query validation failed: only SELECT queries are allowed."

    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]
    for keyword in forbidden:
        if keyword in upper_query:
            return f"Query validation failed: forbidden keyword detected ({keyword})."

    app_settings = AppSettings()
    if app_settings.ENVIRONMENT == "local":
        proc_settings = ProcDBSettings()
    else:
        secret_client = SecretsManagerClient(region_name=app_settings.AWS_REGION)
        proc_settings = secret_client.get_secret(
            app_settings.PROCUREMENT_DB_SECRETSMANAGER_SECRET_ID,
            PydanticSecretFetcher(ProcDBSettings),
        )

    engine = create_engine(
        proc_settings.database_url,
        pool_size=5,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )

    cleaned_query = sql_query.rstrip().rstrip(";")
    final_query = f"{cleaned_query} LIMIT 100"

    try:
        with Session(engine) as session:
            session.execute(text("SET LOCAL statement_timeout = '30s'"))
            result = session.execute(text(final_query))
            rows_raw = result.fetchall()
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in rows_raw]

        if not rows:
            return "Query executed successfully but returned no rows."

        return json.dumps(
            {
                "row_count": len(rows),
                "rows": rows,
            },
            default=str,
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        logger.exception("Database query failed")
        return f"Database error: {exc}"


from agentbyte.llm.azure.settings import AzureOpenAISettings, AzureServicePrincipalSettings

azure_service = AzureOpenAISettings()
azure_principal = AzureServicePrincipalSettings()
model_client = AzureOpenAIChatCompletionClient.from_certificate(
    service_settings=azure_service,
    principal_settings=azure_principal,
    model="gpt-4.1-mini",
    api_version="2024-10-21",
    config={"temperature": 0.2, "max_tokens": 1200},
)

text_to_sql_agent = Agent(
    name="text_to_sql_agent",
    description="Procurement contract SQL analytics assistant",
    instructions=SYSTEM_PROMPT,
    model_client=model_client,
    tools=[FunctionTool(run_analytics_query)],
)

app = create_app(entities=[text_to_sql_agent])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve(
        entities=[text_to_sql_agent],
        port=8081,
        auto_open=True,
    )
