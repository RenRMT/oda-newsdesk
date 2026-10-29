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


def item(title, lang="en", domain=None):
    return Item(id="x", url="https://x", title=title, source_id="s", publisher="p", publisher_name="P",
                publisher_domain=domain, lang=lang, published=NOW, fetched=NOW)


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


def test_dutch_language_alone_does_not_add_nl():
    # e.g. Belgian or machine-translated pages from a Dutch Google News query
    assert assign_tracks(item("Noodhulp voor winkels in Marseille", lang="nl"), src(tracks=["aid", "nl"]), KW) == ["aid"]
    assert assign_tracks(item("Noodhulp in Nepal", lang="nl"), src(prefilter=False, tracks=["aid", "nl"]), KW) == ["aid", "nl"]
    assert assign_tracks(item("Noodhulp voor Gaza", lang="nl", domain="nos.nl"), src(tracks=["aid", "nl"]), KW) == ["aid", "nl"]
    assert assign_tracks(item("Noodhulp in Nepal", lang="nl", domain="denderjournaal.be"), src(), KW) == ["aid"]


def test_exclusions_from_settings():
    from newsdesk import config

    vacancy = item("Stage lobby en advocacy humanitair, 32 uur per week", lang="nl")
    assert config.excluded(vacancy) == "vacancy/job title" and not config.shown(vacancy)
    blocked = item("Humanitarian update")
    blocked.publisher = "vietnam-vn"
    assert config.excluded(blocked) == "blocked publisher"
    assert config.excluded(item("Humanitarian hiring freeze at WFP")) is None
