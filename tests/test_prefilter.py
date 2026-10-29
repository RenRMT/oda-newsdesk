from datetime import datetime, timezone

import pytest

from newsdesk.models import Item, Source
from newsdesk.prefilter import assign_tracks, compile_keywords

NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
KW = compile_keywords({
    "aid": {"terms": ["humanitarian*", "noodhulp"], "terms_cs": ["ODA"]},
    "data-ai": {"terms": ["artificial intelligence"], "terms_cs": ["AI"]},
    "nl": {"terms": ["dutch", "nederland*"]},
})


def item(title, lang="en"):
    return Item(id="x", url="https://x", title=title, source_id="s", publisher="p", publisher_name="P",
                lang=lang, published=NOW, fetched=NOW)


def src(**kw):
    return Source(id="s", name="S", type="rss", url="https://x", **kw)


@pytest.mark.parametrize("title,source,expected", [
    ("Humanitarian appeal underfunded", src(), ["aid"]),
    ("New AI model released", src(), []),                                  # AI without aid signal
    ("AI tools in humanitarian response", src(), ["aid", "data-ai"]),
    ("Startups embrace AI", src(tracks=["data-ai"]), ["data-ai"]),        # data-ai source
    ("Dutch football results", src(), []),                                 # nl alone is not enough
    ("Dutch humanitarian funding falls", src(), ["aid", "nl"]),
    ("Nederland geeft meer noodhulp", src(), ["aid", "nl"]),
    ("Weekly roundup", src(prefilter=False, tracks=["aid"]), ["aid"]),     # on-topic source
    ("oda is not an acronym here", src(), []),                             # case-sensitive ODA
    ("Said the chair", src(tracks=["data-ai"]), []),                       # no 'ai' inside words
])
def test_assign_tracks(title, source, expected):
    assert assign_tracks(item(title), source, KW) == expected


def test_dutch_language_adds_nl():
    assert assign_tracks(item("Humanitarian update", lang="nl"), src(), KW) == ["aid", "nl"]
