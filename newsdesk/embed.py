import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class Embedder:
    """Multilingual sentence embeddings via fastembed (ONNX, CPU). Vectors are L2-normalised."""

    def __init__(self, model_name: str, cache_dir: Path):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name, cache_dir=str(self.cache_dir / "fastembed"))
        vecs = np.array(list(self._model.embed(texts)), dtype=np.float32)
        return vecs / np.linalg.norm(vecs, axis=1, keepdims=True).clip(min=1e-9)


class VectorCache:
    """Item vectors keyed by item id, kept in one .npz outside git (persisted by actions/cache)."""

    def __init__(self, path: Path):
        self.path = path
        self.vectors: dict[str, np.ndarray] = {}
        if path.exists():
            with np.load(path) as data:
                self.vectors = dict(zip(data["ids"].tolist(), data["vecs"]))

    def get(self, ids: list[str]) -> dict[str, np.ndarray]:
        return {i: self.vectors[i] for i in ids if i in self.vectors}

    def put(self, ids: list[str], vecs: np.ndarray) -> None:
        self.vectors.update(zip(ids, vecs))

    def save(self, keep_ids: set[str]) -> None:
        self.vectors = {k: v for k, v in self.vectors.items() if k in keep_ids}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ids = list(self.vectors)
        vecs = np.stack([self.vectors[i] for i in ids]) if ids else np.zeros((0, 0), dtype=np.float32)
        np.savez(self.path, ids=np.array(ids), vecs=vecs)
