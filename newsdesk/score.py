import numpy as np

from newsdesk.models import Item


def item_text(item: Item) -> str:
    return f"{item.title}. {item.excerpt[:400]}".strip()


class Scorer:
    """Relevance per track = highest cosine similarity to any of the track's seed sentences."""

    def __init__(self, embed, tracks_cfg: dict):
        self.tracks = list(tracks_cfg)
        self.seeds = {t: embed([s for s in cfg["seeds"]]) for t, cfg in tracks_cfg.items()}

    def score(self, vecs: np.ndarray) -> list[dict[str, float]]:
        if len(vecs) == 0:
            return []
        sims = {t: (vecs @ seeds.T).max(axis=1) for t, seeds in self.seeds.items()}
        return [{t: round(float(sims[t][i]), 3) for t in self.tracks} for i in range(len(vecs))]


def score_items(items: list[Item], embedder, scorer: Scorer | None, cache) -> dict[str, np.ndarray]:
    """Set item.relevance in place (unless scorer is None); returns id -> vector for all items."""
    known = cache.get([i.id for i in items])
    todo = [i for i in items if i.id not in known]
    if todo:
        vecs = embedder.embed([item_text(i) for i in todo])
        cache.put([i.id for i in todo], vecs)
        known.update(zip((i.id for i in todo), vecs))
    if items and scorer:
        matrix = np.stack([known[i.id] for i in items])
        for item, rel in zip(items, scorer.score(matrix)):
            item.relevance = rel
    return known
