"""Learning Loop — expands the Golden Bucket from successful low-confidence interactions.

When the agent successfully answers a question that had no good Golden Bucket match
(confidence = "low"), we auto-generate a new Trio and add it to the bucket so future
similar questions benefit from the learned pattern.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any

from tools.golden_bucket import GoldenBucketRetriever

logger = logging.getLogger(__name__)

_retriever: GoldenBucketRetriever | None = None

LOW_CONFIDENCE_THRESHOLD = "low"


def _get_retriever() -> GoldenBucketRetriever:
    global _retriever
    if _retriever is None:
        _retriever = GoldenBucketRetriever()
    return _retriever


def maybe_learn(state: Dict[str, Any]) -> Dict[str, Any]:
    """If the interaction was successful but had low golden bucket confidence, save a new trio."""
    node_path = ["learning_loop"]

    confidence = state.get("golden_bucket_confidence", "")
    sql = state.get("generated_sql", "")
    sql_error = state.get("sql_error", "")
    question = state.get("user_message", "")
    report = state.get("report", "")
    result = state.get("sql_result", [])

    should_learn = (
        confidence == LOW_CONFIDENCE_THRESHOLD
        and sql
        and not sql_error
        and result
        and report
    )

    if not should_learn:
        logger.debug(
            "Learning loop skipped: confidence=%s, has_sql=%s, has_error=%s, has_result=%s",
            confidence, bool(sql), bool(sql_error), bool(result),
        )
        return {"node_path": node_path}

    retriever = _get_retriever()
    trio_id = f"trio_auto_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    narrative_preview = report[:200].replace("\n", " ").strip()

    new_trio = {
        "id": trio_id,
        "question": question,
        "sql": sql,
        "report": narrative_preview,
        "source": "auto_learned",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        retriever.add_trio(new_trio)
        logger.info(
            "Learning loop: added new trio '%s' from low-confidence interaction (score was %s)",
            trio_id, state.get("golden_bucket_score", "N/A"),
        )
        return {
            "learned_trio_id": trio_id,
            "node_path": node_path,
        }
    except Exception as e:
        logger.error("Learning loop failed to persist trio: %s", e)
        return {"node_path": node_path}
