"""Golden Bucket retriever — finds the most relevant analyst Trios for a user question.

Prototype uses TF-IDF cosine similarity (zero infrastructure, no API keys).
Production would replace internals with sentence embeddings + vector DB.
"""

import json
import logging
import math
import os
import re
import hashlib
from typing import List, Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "golden_bucket.json")

HIGH_CONFIDENCE = 0.70
MEDIUM_CONFIDENCE = 0.50
DEDUP_QUESTION_SIMILARITY = 0.92
QUESTION_STOPWORDS = {"what", "is", "the", "for", "this", "show", "me", "a", "an", "of", "by", "to", "in"}


def _normalize_sql(sql: str) -> str:
    return " ".join((sql or "").strip().lower().split())


def _fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _normalize_question(question: str) -> str:
    tokens = [t for t in _tokenize(question) if t not in QUESTION_STOPWORDS]
    return " ".join(tokens)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_vocab(docs: List[List[str]]) -> Dict[str, int]:
    vocab: Dict[str, int] = {}
    idx = 0
    for tokens in docs:
        for t in tokens:
            if t not in vocab:
                vocab[t] = idx
                idx += 1
    return vocab


def _tf(tokens: List[str], vocab: Dict[str, int]) -> List[float]:
    vec = [0.0] * len(vocab)
    for t in tokens:
        if t in vocab:
            vec[vocab[t]] += 1.0
    total = sum(vec) or 1.0
    return [v / total for v in vec]


def _idf(docs: List[List[str]], vocab: Dict[str, int]) -> List[float]:
    n = len(docs)
    idf_vec = [0.0] * len(vocab)
    for term, idx in vocab.items():
        df = sum(1 for d in docs if term in d)
        idf_vec[idx] = math.log((n + 1) / (df + 1)) + 1
    return idf_vec


def _tfidf(tokens: List[str], vocab: Dict[str, int], idf_vec: List[float]) -> List[float]:
    tf_vec = _tf(tokens, vocab)
    return [tf_vec[i] * idf_vec[i] for i in range(len(vocab))]


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-10
    nb = math.sqrt(sum(x * x for x in b)) or 1e-10
    return dot / (na * nb)


class GoldenBucketRetriever:
    """Retrieves the most similar analyst Trios for a given user question."""

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path or DATA_PATH
        self.trios: List[Dict[str, Any]] = []
        self._doc_tokens: List[List[str]] = []
        self._vocab: Dict[str, int] = {}
        self._idf_vec: List[float] = []
        self._doc_vecs: List[List[float]] = []
        self._load()

    def _load(self):
        try:
            with open(self.data_path, "r") as f:
                self.trios = json.load(f)
            self._doc_tokens = [_tokenize(t["question"]) for t in self.trios]
            self._vocab = _build_vocab(self._doc_tokens)
            self._idf_vec = _idf(self._doc_tokens, self._vocab)
            self._doc_vecs = [
                _tfidf(tokens, self._vocab, self._idf_vec) for tokens in self._doc_tokens
            ]
            logger.info("Loaded %d Golden Bucket trios", len(self.trios))
        except FileNotFoundError:
            logger.warning("Golden Bucket file not found at %s", self.data_path)
            self.trios = []
        except Exception as e:
            logger.error("Failed to load Golden Bucket: %s", e)
            self.trios = []

    def retrieve(self, question: str, top_k: int = 3) -> Tuple[List[Dict[str, Any]], float]:
        """Return top-k matching Trios and the best similarity score."""
        if not self.trios:
            return [], 0.0

        q_tokens = _tokenize(question)
        q_vec = _tfidf(q_tokens, self._vocab, self._idf_vec)

        scored = []
        for i, doc_vec in enumerate(self._doc_vecs):
            score = _cosine(q_vec, doc_vec)
            scored.append((score, self.trios[i]))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]
        best_score = top[0][0] if top else 0.0
        return [item[1] for item in top], best_score

    def get_confidence_level(self, score: float) -> str:
        if score >= HIGH_CONFIDENCE:
            return "high"
        if score >= MEDIUM_CONFIDENCE:
            return "medium"
        return "low"

    def add_trio(self, trio: Dict[str, Any]) -> None:
        """Add a new Trio and persist to disk, deduplicating near-identical entries."""
        trio.setdefault("schema_version", "1.1")
        trio.setdefault("provenance", trio.get("source", "manual"))
        trio_question = trio.get("question", "")
        trio_sql = trio.get("sql") or ""

        trio["question_fingerprint"] = _fingerprint(_normalize_question(trio_question))
        trio["sql_fingerprint"] = _fingerprint(_normalize_sql(trio_sql))

        existing_idx = self._find_duplicate_index(trio)
        if existing_idx is not None:
            existing = self.trios[existing_idx]
            existing["last_seen_at"] = trio.get("created_at")
            existing["duplicate_hits"] = int(existing.get("duplicate_hits", 0)) + 1
            logger.info("Detected duplicate trio; updated metadata for: %s", existing.get("id", "unknown"))
        else:
            self.trios.append(trio)
        try:
            with open(self.data_path, "w") as f:
                json.dump(self.trios, f, indent=2)
            self._load()
            logger.info("Added new trio: %s", trio.get("id", "unknown"))
        except Exception as e:
            logger.error("Failed to persist new trio: %s", e)

    def _find_duplicate_index(self, trio: Dict[str, Any]) -> Optional[int]:
        q_fp = trio.get("question_fingerprint")
        sql_fp = trio.get("sql_fingerprint")
        q_vec = _tfidf(_tokenize(trio.get("question", "")), self._vocab, self._idf_vec) if self.trios else []

        for idx, existing in enumerate(self.trios):
            existing_q_fp = existing.get("question_fingerprint")
            if not existing_q_fp:
                existing_q_fp = _fingerprint(_normalize_question(existing.get("question", "")))
            if existing_q_fp == q_fp:
                return idx
            if existing.get("sql_fingerprint") == sql_fp and sql_fp != _fingerprint(""):
                return idx
            if q_vec and idx < len(self._doc_vecs):
                if _cosine(q_vec, self._doc_vecs[idx]) >= DEDUP_QUESTION_SIMILARITY:
                    return idx
        return None
