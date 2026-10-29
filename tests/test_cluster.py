from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from newsdesk.cluster import (
    build_clusters, cluster_items, describe, hot_score, is_hot, rare_tokens, velocity_24h,
)
from newsdesk.llm import BackendUnavailable, LLMResult
from newsdesk.models import Cluster, Item

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
CFG = {"sim_threshold": 0.7, "min_sources": 3}


def item(id, title, publisher, hours_ago=0, tracks=("aid",), source_id="s"):
    t = NOW - timedelta(hours=hours_ago)
    return Item(id=id, url=f"https://x/{id}", title=title, source_id=source_id, publisher=publisher,
                publisher_name=publisher.upper(), lang="en", published=t, fetched=t, tracks=list(tracks))


def unit(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


GAZA = [
    item("g1", "WFP cuts Gaza food aid", "un", 30),
    item("g2", "Gaza: WFP rations cut for half a million", "nos", 20),
    item("g3", "WFP Gaza cuts explained", "tnh", 10),
    item("g4", "More on WFP Gaza cuts", "tnh", 5),  # same publisher again
]
SUDAN = item("s1", "Sudan hunger deepens as rations cut", "dabanga", 8)
VECS = {"g1": unit(1, 0.1, 0), "g2": unit(1, 0, 0.1), "g3": unit(1, 0.05, 0.05), "g4": unit(1, 0.1, 0.1),
        "s1": unit(1, 0.2, 0)}  # s1 is close in embedding space but shares no rare word
RARE = {"gaza", "sudan"}


def test_multi_outlet_story_is_one_hot_cluster():
    clusters = build_clusters(GAZA + [SUDAN], VECS, RARE, NOW, CFG, lambda i: 1.0)
    assert len(clusters) == 1
    c = clusters[0]
    assert c.id == "g1" and c.item_ids == ["g1", "g2", "g3", "g4"]
    assert c.publishers == ["un", "nos", "tnh"] and is_hot(c, CFG)


def test_rare_word_and_shared_track_required():
    groups = cluster_items(GAZA[:2] + [SUDAN], VECS, 0.7, RARE)
    assert sorted(len(g) for g in groups) == [1, 2]
    other_track = item("g5", "Gaza WFP data", "devex", 1, tracks=("data-ai",))
    groups = cluster_items(GAZA[:1] + [other_track], VECS | {"g5": VECS["g1"]}, 0.7, RARE)
    assert len(groups) == 2


def test_dissimilar_vectors_split():
    far = item("g5", "Gaza ceasefire talks", "aj", 1)
    groups = cluster_items(GAZA[:1] + [far], VECS | {"g5": unit(0, 1, 0)}, 0.7, RARE)
    assert len(groups) == 2


def test_hot_score_counts_distinct_publishers_once():
    fresh = [item("a", "x", "p1"), item("b", "x", "p2")]
    assert hot_score(fresh, NOW, lambda i: 1.0) == 2.0
    dup = fresh + [item("c", "x", "p1"), item("d", "x", "p1", 1)]
    assert hot_score(dup, NOW, lambda i: 1.0) == 2.0
    # a publisher's best item counts; older items decay; weight scales
    old = [item("a", "x", "p1", 48)]
    assert hot_score(old, NOW, lambda i: 1.0) == pytest.approx(np.exp(-1), abs=1e-3)
    assert hot_score(fresh, NOW, lambda i: 0.5) == 1.0


def test_velocity_counts_publishers_new_in_24h():
    items = [item("a", "x", "p1", 30), item("b", "x", "p1", 2), item("c", "x", "p2", 3), item("d", "x", "p3", 23)]
    assert velocity_24h(items, NOW) == 2  # p1 first appeared 30h ago


def test_rare_tokens_by_document_frequency():
    corpus = [item(str(n), "Humanitarian funding update", "p") for n in range(19)] + [item("x", "Gaza update", "p")]
    rare = rare_tokens(corpus, 0.05)
    assert "gaza" in rare and "humanitarian" not in rare and "update" not in rare


def test_llm_fields_carry_over_only_while_membership_unchanged():
    first = build_clusters(GAZA[:3], VECS, RARE, NOW, CFG, lambda i: 1.0)
    first[0].headline, first[0].llm_status = "WFP cuts Gaza aid", "ok"
    same = build_clusters(GAZA[:3], VECS, RARE, NOW, CFG, lambda i: 1.0, first)
    assert same[0].headline == "WFP cuts Gaza aid" and same[0].llm_status == "ok"
    grown = build_clusters(GAZA, VECS, RARE, NOW, CFG, lambda i: 1.0, first)
    assert grown[0].headline == "" and grown[0].llm_status == "skipped"


class FakeBackend:
    name, model = "fake", "fake-1"

    def __init__(self, results):
        self.results = list(results)

    def complete_json(self, system, user, schema, max_tokens):
        assert "publishers" in user and "WFP" in user
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_describe_sets_fields_and_handles_failures():
    by_id = {i.id: i for i in GAZA}
    clusters = [Cluster(id=str(n), item_ids=["g1", "g2"], publishers=["un", "nos"], first_seen=NOW, last_seen=NOW)
                for n in range(4)]
    backend = FakeBackend([
        LLMResult("ok", data={"headline": "H", "summary": "S", "hook_angle": "A?"}, usage={"input_tokens": 10}),
        LLMResult("ok", data={"headline": "H"}),
        LLMResult("refused", error="refusal"),
        BackendUnavailable("no key"),
    ])
    stats = describe(clusters, by_id, backend)
    assert [c.llm_status for c in clusters] == ["ok", "invalid", "refused", "skipped"]
    assert clusters[0].headline == "H" and clusters[0].hook_angle == "A?"
    assert stats["attempted"] == 3 and stats["input_tokens"] == 10 and "unavailable" in stats
