from datetime import datetime, timezone

import numpy as np

from newsdesk.embed import VectorCache
from newsdesk.models import Item
from newsdesk.score import Scorer, score_items

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
AXES = {"aid": [1, 0, 0], "tech": [0, 1, 0], "dutch": [0, 0, 1]}


def fake_embed(texts):
    """Map each text onto unit axes by keyword, so similarities are predictable."""
    out = []
    for t in texts:
        v = sum(np.array(vec, dtype=np.float32) for k, vec in AXES.items() if k in t.lower())
        v = v if np.any(v) else np.array([0.1, 0.1, 0.1], dtype=np.float32)
        out.append(v / np.linalg.norm(v))
    return np.stack(out)


class CountingEmbedder:
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += len(texts)
        return fake_embed(texts)


TRACKS = {
    "aid": {"seeds": ["aid seed", "other aid seed"]},
    "data-ai": {"seeds": ["tech seed"]},
    "nl": {"seeds": ["dutch seed"]},
}


def item(id, title):
    return Item(id=id, url=f"https://x/{id}", title=title, source_id="s", publisher="p",
                publisher_name="P", lang="en", published=NOW, fetched=NOW)


def test_scores_per_track_and_overall(tmp_path):
    items = [item("a", "aid story"), item("b", "tech story"), item("c", "weather")]
    emb = CountingEmbedder()
    score_items(items, emb, Scorer(fake_embed, TRACKS), VectorCache(tmp_path / "v.npz"))
    assert items[0].relevance["aid"] == 1.0 and items[0].relevance["data-ai"] == 0.0
    assert items[1].score == 1.0
    assert items[2].score < 0.6


def test_vector_cache_avoids_reembedding(tmp_path):
    path = tmp_path / "v.npz"
    cache = VectorCache(path)
    emb = CountingEmbedder()
    scorer = Scorer(fake_embed, TRACKS)
    score_items([item("a", "aid story")], emb, scorer, cache)
    cache.save({"a"})
    reloaded = VectorCache(path)
    score_items([item("a", "aid story"), item("b", "tech")], emb, scorer, reloaded)
    assert emb.calls == 2  # "a" once, then only "b"


def test_visible_uses_threshold_and_llm_judgment():
    i = item("a", "x")
    assert i.visible(0.4)  # unscored items are shown
    i.relevance = {"aid": 0.3, "data-ai": 0.2, "nl": 0.9}
    assert not i.visible(0.4) and i.visible(0.25)
