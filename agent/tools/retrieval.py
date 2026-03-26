"""Retrieval tool: find matching Golden Bucket trios for a question."""

import logging
from typing import Dict, Any

from tools.golden_bucket import GoldenBucketRetriever

logger = logging.getLogger(__name__)

_retriever: GoldenBucketRetriever | None = None


def _get_retriever() -> GoldenBucketRetriever:
    global _retriever
    if _retriever is None:
        _retriever = GoldenBucketRetriever()
    return _retriever


def retrieve_golden_bucket(state: Dict[str, Any]) -> Dict[str, Any]:
    question = state.get("user_message", "")
    node_path = ["golden_bucket_retriever"]

    retriever = _get_retriever()
    trios, best_score = retriever.retrieve(question, top_k=2)
    confidence = retriever.get_confidence_level(best_score)

    logger.info(
        "Golden Bucket: best_score=%.3f confidence=%s matched=%d",
        best_score,
        confidence,
        len(trios),
    )

    return {
        "retrieved_trios": trios,
        "golden_bucket_score": best_score,
        "golden_bucket_confidence": confidence,
        "node_path": node_path,
    }


__all__ = ["retrieve_golden_bucket"]

