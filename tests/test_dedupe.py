from datetime import datetime, timedelta, timezone

from newsdesk.dedupe import dedupe
from newsdesk.models import Item

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def make(id, title, days_ago=0):
    t = NOW - timedelta(days=days_ago)
    return Item(id=id, url=f"https://x.org/{id}", title=title, source_id="s", publisher="p",
                publisher_name="P", lang="en", published=t, fetched=t)


def test_drops_seen_ids():
    assert dedupe([make("a", "Something new happened in the aid sector")], {"a"}, [], NOW) == []


def test_drops_fuzzy_title_match_against_recent():
    recent = [make("r", "Netherlands cuts development aid budget by €2.4 billion", 1)]
    cand = [make("c", "Netherlands cuts development aid budget by 2.4 billion")]
    assert dedupe(cand, set(), recent, NOW) == []


def test_keeps_old_lookalike_outside_window():
    recent = [make("r", "Netherlands cuts development aid budget by €2.4 billion", 10)]
    cand = [make("c", "Netherlands cuts development aid budget by €2.4 billion")]
    assert len(dedupe(cand, set(), recent, NOW)) == 1


def test_dedupes_within_batch_and_keeps_distinct():
    cand = [
        make("a", "Kabinet bezuinigt fors op ontwikkelingssamenwerking"),
        make("b", "Kabinet bezuinigt fors op ontwikkelingssamenwerking!"),
        make("c", "WFP warns of famine in Sudan as funding dries up"),
    ]
    assert [i.id for i in dedupe(cand, set(), [], NOW)] == ["a", "c"]


def test_short_titles_not_fuzzy_matched():
    cand = [make("a", "Editorial"), make("b", "Editorial")]
    assert len(dedupe(cand, set(), [], NOW)) == 2
