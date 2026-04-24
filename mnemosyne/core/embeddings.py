"""
Mnemosyne Dense Retrieval
Local embedding-based memory retrieval using fastembed (ONNX, no PyTorch).
Falls back to keyword-only if fastembed is unavailable.
"""

import json
import numpy as np
from typing import List, Optional
from functools import lru_cache

# Optional dependency
try:
    from fastembed import TextEmbedding
    _FASTEMBED_AVAILABLE = True
except Exception:
    _FASTEMBED_AVAILABLE = False
    TextEmbedding = None

_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_embedding_model = None


def _get_model() -> Optional[TextEmbedding]:
    """Lazy-load the embedding model."""
    global _embedding_model
    if not _FASTEMBED_AVAILABLE:
        return None
    if _embedding_model is None:
        _embedding_model = TextEmbedding(model_name=_DEFAULT_MODEL)
    return _embedding_model


def available() -> bool:
    """Check if dense retrieval is available."""
    return _FASTEMBED_AVAILABLE and _get_model() is not None


@lru_cache(maxsize=512)
def embed_query(text: str) -> Optional[np.ndarray]:
    """
    Encode a single query text into a dense vector with LRU caching.
    Repeated queries (very common in agent loops) are near-instant.
    """
    model = _get_model()
    if model is None or not text:
        return None
    vectors = list(model.embed([text]))
    if not vectors:
        return None
    return vectors[0].astype(np.float32)


def embed(texts: List[str]) -> Optional[np.ndarray]:
    """
    Encode texts into dense vectors.

    Args:
        texts: List of strings to encode

    Returns:
        Numpy array of shape (n_texts, embedding_dim) or None if unavailable
    """
    if not texts:
        return None
    # Use cached single-query path for common case of 1 text
    if len(texts) == 1:
        v = embed_query(texts[0])
        if v is None:
            return None
        return np.stack([v])
    model = _get_model()
    if model is None:
        return None
    vectors = list(model.embed(texts))
    return np.stack(vectors).astype(np.float32)


def serialize(vec: np.ndarray) -> str:
    """Serialize embedding to JSON string."""
    return json.dumps(vec.tolist())

