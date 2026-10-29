import logging
import math
import re
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import numpy as np

from newsdesk.enrich import load_prompt
from newsdesk.llm import BackendUnavailable, LLMBackend
from newsdesk.models import Cluster, Item
from newsdesk.normalise import STOPWORDS

log = logging.getLogger(__name__)

HALF_LIFE_H = 48  # decay constant for the hot score: weight × e^(−age_h/48)
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "hook_angle": {"type": "string"},
    },
    "required": ["headline", "summary", "hook_angle"],
    "additionalProperties": False,
}
MAX_TOKENS = 4000
_STOP = set().union(*STOPWORDS.values())


def tokens(item: Item) -> set[str]:
    text = f"{item.title} {item.excerpt[:400]}".lower()
    return {w for w in re.findall(r"[a-zà-ÿ0-9]{4,}", text) if w not in _STOP}


def rare_tokens(corpus: list[Item], max_df: float) -> set[str]:
    """Words in at most `max_df` of the corpus items: names of places, organisations, people."""
    df = Counter(w for i in corpus for w in tokens(i))
    return {w for w, n in df.items() if n <= max_df * len(corpus)}


def cluster_items(
    items: list[Item], vectors: dict[str, np.ndarray], sim_threshold: float, rare: set[str]
) -> list[list[Item]]:
    """Greedy single pass, oldest first: join the most similar cluster whose centroid similarity is
    at least `sim_threshold`, that shares a track, and that shares a rare word (embeddings alone
    merge "hunger crisis deepens in Sudan" with the same headline about Gaza); otherwise start a
    new cluster."""
    groups: list[list[Item]] = []
    sums: list[np.ndarray] = []
    tracks: list[set[str]] = []
    anchors: list[set[str]] = []
    for item in sorted(items, key=lambda i: (i.published, i.id)):
        vec = vectors.get(item.id)
        if vec is None:
            continue
        words = tokens(item) & rare
        best, best_sim = None, sim_threshold
        for k, s in enumerate(sums):
            if not tracks[k] & set(item.tracks) or not anchors[k] & words:
                continue
            sim = float(vec @ s) / max(float(np.linalg.norm(s)), 1e-9)
            if sim >= best_sim:
                best, best_sim = k, sim
        if best is None:
            groups.append([item])
            sums.append(vec.astype(np.float32).copy())
            tracks.append(set(item.tracks))
            anchors.append(words)
        else:
            groups[best].append(item)
            sums[best] += vec
            tracks[best] |= set(item.tracks)
            anchors[best] |= words
    return groups


def hot_score(items: list[Item], now: datetime, weight_for: Callable[[Item], float]) -> float:
    """Sum over distinct publishers of their best weight × recency; repeats from one publisher add nothing."""
    best: dict[str, float] = {}
    for i in items:
        age_h = max((now - i.published).total_seconds() / 3600, 0)
        best[i.publisher] = max(best.get(i.publisher, 0.0), weight_for(i) * math.exp(-age_h / HALF_LIFE_H))
    return round(sum(best.values()), 3)


def velocity_24h(items: list[Item], now: datetime) -> int:
    """Publishers whose first item in the cluster appeared in the last 24 hours."""
    first: dict[str, datetime] = {}
    for i in items:
        first[i.publisher] = min(first.get(i.publisher, i.published), i.published)
    return sum(t >= now - timedelta(hours=24) for t in first.values())


def build_clusters(
    items: list[Item], vectors: dict[str, np.ndarray], rare: set[str], now: datetime, cfg: dict,
    weight_for: Callable[[Item], float], previous: list[Cluster] = (),
) -> list[Cluster]:
    """Clusters covered by at least two publishers, hottest first. LLM fields carry over from
    `previous` while a cluster's membership is unchanged."""
    prev = {frozenset(c.item_ids): c for c in previous}
    out = []
    for group in cluster_items(items, vectors, cfg["sim_threshold"], rare):
        publishers = list(dict.fromkeys(i.publisher for i in group))
        if len(publishers) < 2:
            continue
        c = Cluster(
            id=group[0].id,
            item_ids=[i.id for i in group],
            publishers=publishers,
            first_seen=min(i.published for i in group),
            last_seen=max(i.published for i in group),
            hot_score=hot_score(group, now, weight_for),
            velocity_24h=velocity_24h(group, now),
            tracks=sorted({t for i in group for t in i.tracks}),
        )
        old = prev.get(frozenset(c.item_ids))
        if old and old.llm_status in ("ok", "refused"):
            c.headline, c.summary, c.hook_angle, c.llm_status = old.headline, old.summary, old.hook_angle, old.llm_status
        out.append(c)
    out.sort(key=lambda c: c.hot_score, reverse=True)
    return out


def is_hot(c: Cluster, cfg: dict) -> bool:
    return len(c.publishers) >= cfg["min_sources"]


def needs_description(c: Cluster) -> bool:
    return c.llm_status not in ("ok", "refused")


def user_message(cluster: Cluster, items: dict[str, Item]) -> str:
    lines = [f"{len(cluster.publishers)} publishers, {len(cluster.item_ids)} items:"]
    for iid in cluster.item_ids:
        i = items[iid]
        summary = i.enrichment.summary_en if i.enrichment else i.excerpt[:300]
        lines.append(f"- [{i.published:%Y-%m-%d}] {i.publisher_name}: {i.title}" + (f" — {summary}" if summary else ""))
    return "\n".join(lines)


def describe(clusters: list[Cluster], items: dict[str, Item], backend: LLMBackend, concurrency: int = 1) -> dict:
    """Write headline, summary and hook angle for clusters in place. Returns run stats."""
    system = load_prompt("cluster")
    stats = {"backend": backend.name, "model": backend.model, "attempted": 0,
             "ok": 0, "refused": 0, "invalid": 0, "error": 0,
             "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}

    def run(c: Cluster):
        return backend.complete_json(system, user_message(c, items), SCHEMA, MAX_TOKENS)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [(c, pool.submit(run, c)) for c in clusters]
        for c, fut in futures:
            try:
                result = fut.result()
            except BackendUnavailable as e:
                log.error("LLM backend unavailable, stopping cluster headlines: %s", e)
                stats["unavailable"] = str(e)[:300]
                for _, f in futures:
                    f.cancel()
                break
            stats["attempted"] += 1
            for k, v in result.usage.items():
                stats[k] = stats.get(k, 0) + v
            data = result.data or {}
            if result.status == "ok" and all(isinstance(data.get(k), str) for k in SCHEMA["required"]):
                c.headline, c.summary, c.hook_angle = data["headline"], data["summary"], data["hook_angle"]
                c.llm_status = "ok"
            else:
                c.llm_status = "invalid" if result.status == "ok" else result.status
                log.warning("LLM %s for cluster %s: %s", c.llm_status, c.id, result.error)
            stats[c.llm_status] += 1
    return stats
