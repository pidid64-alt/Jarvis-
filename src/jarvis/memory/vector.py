"""Локальные эмбеддинги (fastembed, ONNX на CPU) — бесплатно и приватно.

Модель скачивается один раз при первом использовании; при любой проблеме
(нет сети, нет места) движок деградирует в выключенное состояние, и память
продолжает работать на полнотекстовом поиске (FTS5).
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("jarvis.memory.vector")

# Мультиязычная (ru/en) сверхмалая модель — рассчитана на слабый CPU;
# префиксы "query:"/"passage:" ей не нужны (в отличие от семейства e5).
MODEL_NAME = "minishlab/potion-multilingual-128M"


class EmbeddingEngine:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._model = None
        self._failed = False

    @property
    def ready(self) -> bool:
        return self.enabled and self._ensure_loaded()

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._failed or not self.enabled:
            return False
        try:
            from fastembed import TextEmbedding

            log.info("Загружаю модель эмбеддингов %s (первый раз может качаться)…", MODEL_NAME)
            self._model = TextEmbedding(model_name=MODEL_NAME)
            return True
        except Exception as e:
            self._failed = True
            log.warning("Эмбеддинги недоступны (%s); поиск памяти — только полнотекстовый", e)
            return False

    def embed_documents(self, texts: list[str]) -> list[np.ndarray]:
        if not texts or not self.ready:
            return []
        return [np.asarray(r, dtype=np.float32) for r in self._model.embed(texts)]

    def embed_query(self, text: str) -> np.ndarray | None:
        if not self.ready:
            return None
        return np.asarray(next(iter(self._model.embed([text]))), dtype=np.float32)


def cosine_top_k(
    query: np.ndarray, vectors: list[tuple[int, np.ndarray]], top_k: int
) -> list[tuple[int, float]]:
    """Косинусное сходство «в лоб»: для личной базы (десятки тысяч записей) этого достаточно."""
    if not vectors:
        return []
    q = query / (np.linalg.norm(query) + 1e-9)
    scores = []
    for fact_id, vec in vectors:
        v = vec / (np.linalg.norm(vec) + 1e-9)
        scores.append((fact_id, float(np.dot(q, v))))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]
