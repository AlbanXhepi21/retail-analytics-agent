"""PII Masker — strips personal data from query results before report generation.

Two-layer approach:
  1. Column name matching: drops columns named email, phone, address, etc.
  2. Pattern scanning: regex scan for email/phone patterns in remaining string values.
"""

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

PII_COLUMN_NAMES = {
    "email", "phone", "mobile", "telephone", "cell",
    "street_address", "address", "postal_code", "zip_code", "zipcode",
    "ssn", "social_security", "credit_card", "card_number",
    "latitude", "longitude", "lat", "lng", "lon",
}

PII_COLUMN_KEYWORDS = {
    "email", "phone", "mobile", "telephone", "cell",
    "address", "postal", "zip", "latitude", "longitude", "lat", "lng", "lon",
}

EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}")


def _is_pii_column(column_name: str) -> bool:
    lowered = (column_name or "").lower()
    if lowered in PII_COLUMN_NAMES:
        return True
    tokens = re.split(r"[^a-z0-9]+", lowered)
    return any(t in PII_COLUMN_KEYWORDS for t in tokens if t)


def mask_pii(state: Dict[str, Any]) -> Dict[str, Any]:
    """Strip PII from SQL results. Fail-closed: on error, return empty result."""
    node_path = ["pii_masker"]

    result = state.get("sql_result", [])
    columns = list(state.get("sql_result_columns", []))

    if not result:
        return {
            "pii_masked": False,
            "pii_columns_dropped": [],
            "pii_values_redacted": 0,
            "node_path": node_path,
        }

    try:
        dropped_columns = [col for col in columns if _is_pii_column(col)]
        if dropped_columns:
            drop_set = set(dropped_columns)
            columns = [col for col in columns if col not in drop_set]
            result = [{k: v for k, v in row.items() if k not in drop_set} for row in result]
            logger.warning("Dropped PII columns: %s", dropped_columns)

        redacted_count = 0
        for row in result:
            for key in list(row.keys()):
                val = row[key]
                if isinstance(val, str):
                    if EMAIL_PATTERN.search(val):
                        row[key] = EMAIL_PATTERN.sub("[EMAIL REDACTED]", val)
                        redacted_count += 1
                    if PHONE_PATTERN.search(str(row[key])):
                        candidate = PHONE_PATTERN.sub("[PHONE REDACTED]", str(row[key]))
                        if "[PHONE REDACTED]" in candidate and not candidate.replace("[PHONE REDACTED]", "").strip().replace("$", "").replace(",", "").replace(".", "").isdigit():
                            row[key] = candidate
                            redacted_count += 1

        masked = bool(dropped_columns or redacted_count)
        if masked:
            logger.info("PII masking applied: %d columns dropped, %d values redacted", len(dropped_columns), redacted_count)

        return {
            "sql_result": result,
            "sql_result_columns": columns,
            "pii_masked": masked,
            "pii_columns_dropped": dropped_columns,
            "pii_values_redacted": redacted_count,
            "node_path": node_path,
        }

    except Exception as e:
        logger.error("PII masker failed — failing closed (no data exposed): %s", e)
        return {
            "sql_result": [],
            "sql_result_columns": [],
            "pii_masked": True,
            "pii_columns_dropped": [],
            "pii_values_redacted": 0,
            "node_path": node_path,
            "error_message": f"PII masker error (data withheld for safety): {e}",
        }
