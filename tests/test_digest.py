from datetime import datetime, timedelta, timezone

from newsdesk import digest
from newsdesk.llm import BackendUnavailable, LLMResult
from newsdesk.models import Cluster, Enrichment, Hook, Item
from newsdesk.store import Store

NOW = datetime(2026, 11, 2, 5, 30, tzinfo=timezone.utc)  # a Monday


def item(id, publisher="p", days_ago=1, tracks=("aid",), kind="news", hook=False, **kw):
    t = NOW - timedelta(days=days_ago)
    enrichment = Enrichment(summary_en=f"Summary {id}.", type=kind, tracks=list(tracks),
                            hook=Hook(flag=hook, angle=f"Angle {id}?" if hook else "", why_now="Now" if hook else ""))
    return Item(id=id, url=f"https://x/{id}", title=f"Title {id}", source_id="s", publisher=publisher,
                publisher_name=publisher.upper(), lang="en", published=t, fetched=t, tracks=list(tracks),
                enrichment=enrichment, llm_status="ok", **kw)


def store_with_week(tmp_path) -> Store:
    store = Store(tmp_path)
    agg = item("agg", "farm")
    agg.enrichment.aggregator = True
    store.append_items([
        item("a1", "un"), item("a2", "nos"), item("a3", "tnh"),  # one story
        item("r1", "odi", kind="report"),
        item("h1", "devex", hook=True),
        item("n1", "vv", tracks=("aid", "nl")),
        item("old", "devex", days_ago=10),
        agg,
    ])
    store.save_clusters([Cluster(id="a1", item_ids=["a1", "a2", "a3"], publishers=["un", "nos", "tnh"],
                                 first_seen=NOW, last_seen=NOW, hot_score=2.5, headline="Story headline",
                                 tracks=["aid"])], NOW)
    return store


class FakeBackend:
    name, model = "fake", "fake-1"

    def __init__(self, result):
        self.result = result
        self.user = None

    def complete_json(self, system, user, schema, max_tokens):
        assert schema is digest.SCHEMA
        self.user = user
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_week_label_is_the_week_before_a_monday_run():
    assert digest.week_label(NOW) == "2026-W44"


def test_gather_cites_story_members_once_and_skips_hidden(tmp_path):
    m = digest.gather(store_with_week(tmp_path), NOW)
    assert [s.headline for s in m.stories.values()] == ["Story headline"]
    ids = {i.id for i in m.items.values()}
    assert ids == {"r1", "h1", "n1"}  # story members, old and aggregator items left out


def test_digest_with_llm_keeps_only_known_refs(tmp_path):
    store = store_with_week(tmp_path)
    m = digest.gather(store, NOW)
    ref = {i.id: r for r, i in m.items.items()}
    data = {
        "big_picture": [{"trend": "Cuts spread.", "refs": ["S1", ref["r1"], "I99"]}],
        "hot_stories": [{"ref": "S1", "note": "Watch the budget."}, {"ref": "S7", "note": "Invented."}],
        "nl_corner": [{"text": "Dutch debate.", "refs": [ref["n1"]]}],
        "publications": [{"ref": ref["r1"], "note": "Finds X."}, {"ref": "S1", "note": "Not an item."}],
        "hooks": [{"angle": "What next?", "why_now": "Budget week", "refs": [ref["h1"], "S1"]},
                  {"angle": "Made up?", "why_now": "", "refs": ["I42"]}],
    }
    backend = FakeBackend(LLMResult("ok", data=data, usage={"input_tokens": 10, "output_tokens": 5}))
    path, stats = digest.make_digest(store, NOW, backend)
    text = path.read_text(encoding="utf-8")

    assert path.name == "2026-W44.md" and stats["status"] == "ok"
    assert "S1 |" in backend.user and "hook: Angle h1?" in backend.user
    assert "- Cuts spread. ([Story headline](https://x/a1), [ODI](https://x/r1))" in text
    assert "Invented" not in text and "Made up" not in text and "Not an item" not in text
    assert "_Covered by 3 publishers: UN, NOS, TNH._" in text
    assert "1. **What next?** _Budget week._ Start from: [DEVEX](https://x/h1), [Story headline](https://x/a1)." in text

    [d] = digest.load_digests(tmp_path)
    assert d["week"] == "2026-W44" and d["meta"]["status"] == "ok" and d["meta"]["model"] == "fake-1"
    assert d["title"] == "Week 44: 26 October – 1 November 2026"
    assert not d["markdown"].lstrip().startswith("# ")


def test_digest_falls_back_without_model(tmp_path):
    store = store_with_week(tmp_path)
    for backend in (None, FakeBackend(LLMResult("refused", error="refusal")), FakeBackend(BackendUnavailable("down"))):
        path, stats = digest.make_digest(store, NOW, backend)
        text = path.read_text(encoding="utf-8")
        assert "status: fallback" in text and "without the digest model" in text
        assert "## Big picture" not in text
        assert "[Title r1](https://x/r1)" in text  # reports become notable publications
        assert "**Angle h1?**" in text  # item hooks carried over


def test_markdown_special_characters_are_escaped():
    assert digest._md("a [b] *c* _d_") == r"a \[b\] \*c\* \_d\_"
