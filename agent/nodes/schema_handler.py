"""Handles schema/database structure questions using Golden Bucket."""

import logging
from typing import Dict, Any

from tools.golden_bucket import GoldenBucketRetriever

logger = logging.getLogger(__name__)

SCHEMA_TRIO_ID = "trio_006"

_retriever: GoldenBucketRetriever | None = None


def _get_retriever() -> GoldenBucketRetriever:
    global _retriever
    if _retriever is None:
        _retriever = GoldenBucketRetriever()
    return _retriever


def handle_schema_question(state: Dict[str, Any]) -> Dict[str, Any]:
    """Answer database structure questions from the Golden Bucket directly."""
    node_path = ["schema_handler"]

    retriever = _get_retriever()
    for trio in retriever.trios:
        if trio.get("id") == SCHEMA_TRIO_ID:
            logger.info("Returning schema info from Golden Bucket trio_006")
            return {"report": trio["report"], "node_path": node_path}

    return {
        "report": (
            "The database contains the following tables: "
            "**orders**, **order_items**, **products**, and **users**. "
            "Ask me a specific question and I'll query the data for you."
        ),
        "node_path": node_path,
    }
