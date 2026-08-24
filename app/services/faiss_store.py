"""CPU-only FAISS / MiniLM singleton. Falls back to hashing embeddings to avoid OOM."""

from __future__ import annotations

import hashlib
import logging
import math
import os
from threading import Lock

from app.config import get_settings
from app.services.finance_db import FinancialHighlight, list_companies, lookup_ticker

log = logging.getLogger(__name__)

EMBED_DIM = 128
_lock = Lock()
_model = None
_index_vectors: list[tuple[str, list[float]]] = []
_ready = False

MINILM_ID = "paraphrase-multilingual-MiniLM-L12-v2"


def _hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    lowered = (text or "").lower()
    if len(lowered) < 3:
        lowered = f"{lowered}___"
    for i in range(len(lowered) - 2):
        gram = lowered[i : i + 3].encode("utf-8", "ignore")
        digest = hashlib.md5(gram).digest()
        idx = int.from_bytes(digest[:2], "little") % dim
        vec[idx] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


def _load_minilm():
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MINILM_ID, device="cpu")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed on CPU. MiniLM if loaded, otherwise a tiny hashing encoder."""
    global _model
    if _model is not None:
        vectors = _model.encode(texts, batch_size=8, show_progress_bar=False, convert_to_numpy=True)
        return [row.tolist() for row in vectors]
    return [_hash_embed(text) for text in texts]


def warmup_faiss() -> None:
    """Load optional MiniLM + rebuild ticker index. Safe to call once from lifespan."""
    global _model, _index_vectors, _ready
    settings = get_settings()
    with _lock:
        if settings.agent_faiss_enabled and _model is None:
            try:
                _model = _load_minilm()
                log.info("loaded %s on cpu", MINILM_ID)
            except Exception:
                log.exception("MiniLM unavailable; using hashing embeddings")
                _model = None
        companies = list_companies()
        texts = [f"{row.name} {row.ticker} {row.sector} {row.market}" for row in companies]
        vectors = embed_texts(texts) if texts else []
        _index_vectors = [(row.ticker, vec) for row, vec in zip(companies, vectors, strict=False)]
        _ready = True


def search_ticker(query: str, *, k: int = 1) -> FinancialHighlight | None:
    """Hybrid search: exact ticker/name first, then embedding nearest neighbor."""
    direct = lookup_ticker(query)
    if direct is not None:
        return direct
    if not (query or "").strip():
        return None
    with _lock:
        if not _ready:
            warmup_faiss()
        index = list(_index_vectors)
    if not index:
        return None
    qvec = embed_texts([query])[0]
    ranked = sorted(index, key=lambda item: _cosine(qvec, item[1]), reverse=True)
    best = ranked[0] if ranked else None
    if best is None:
        return None
    return lookup_ticker(best[0])


def reset_faiss_for_tests() -> None:
    global _model, _index_vectors, _ready
    with _lock:
        _model = None
        _index_vectors = []
        _ready = False
