"""SQL Executor tool — runs generated SQL against BigQuery with error handling."""

import logging
import os
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)

MAX_SQL_RETRIES = 3

_bq_client = None


def _get_client():
    global _bq_client
    if _bq_client is None:
        from tools.bq_client import BigQueryRunner

        _bq_client = BigQueryRunner(project_id=os.environ.get("GCP_PROJECT_ID"))
    return _bq_client


def execute_sql(state: Dict[str, Any]) -> Dict[str, Any]:
    node_path = ["sql_executor"]

    sql = state.get("generated_sql", "")
    retry_count = state.get("sql_retry_count", 0)

    if not sql:
        existing_error = state.get("sql_error", "")
        return {
            "sql_error": existing_error or "No SQL query to execute",
            "sql_retry_count": retry_count + 1,
            "node_path": node_path,
        }

    logger.info("Executing SQL (retry_count=%d)", retry_count)

    try:
        client = _get_client()
        df = client.execute_query(sql)

        if df.empty:
            logger.info("Query returned empty result set")
            return {
                "sql_result": [],
                "sql_result_columns": list(df.columns),
                "sql_error": "",
                "sql_retry_count": retry_count,
                "node_path": node_path,
                "report": (
                    "The query executed successfully but returned no results. "
                    "This might mean the data doesn't exist for the specified "
                    "criteria. Try broadening your question or adjusting the "
                    "time range."
                ),
            }

        result = df.to_dict(orient="records")
        columns = list(df.columns)
        logger.info("SQL execution successful: %d rows, %d columns", len(result), len(columns))

        return {
            "sql_result": result,
            "sql_result_columns": columns,
            "sql_error": "",
            "sql_retry_count": retry_count,
            "node_path": node_path,
        }

    except Exception as e:
        error_msg = str(e)
        error_lower = error_msg.lower()

        infra_errors = [
            "credentials were not found",
            "could not automatically determine credentials",
            "permission denied",
            "access denied",
        ]
        is_infra = any(kw in error_lower for kw in infra_errors)

        if is_infra:
            logger.error("Unrecoverable infrastructure error (skipping retries): %s", error_msg)
            return {"sql_error": error_msg, "sql_retry_count": MAX_SQL_RETRIES, "node_path": node_path}

        new_retry = retry_count + 1
        logger.warning("SQL execution failed (attempt %d/%d): %s", new_retry, MAX_SQL_RETRIES, error_msg)

        if new_retry < MAX_SQL_RETRIES:
            delay = 1.5**new_retry
            logger.info("Will retry after %.1fs backoff", delay)
            time.sleep(delay)

        return {"sql_error": error_msg, "sql_retry_count": new_retry, "node_path": node_path}


__all__ = ["execute_sql", "MAX_SQL_RETRIES"]

