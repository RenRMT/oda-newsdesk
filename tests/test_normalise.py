from datetime import datetime, timedelta, timezone

from newsdesk.models import RawEntry, Source
from newsdesk.normalise import canonical_url, detect_lang, item_id, normalise, strip_html, to_utc

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def test_canonical_url_strips_tracking_and_amp():
    assert (
        canonical_url("HTTPS://Amp.Example.org/news/story/amp/?utm_source=x&id=3&fbclid=y#top")
        == "https://example.org/news/story?id=3"
    )


def test_canonical_url_keeps_root_slash():
    assert canonical_url("https://example.org/") == "https://example.org/"


def test_item_id_stable_across_tracking_variants():
    assert item_id("https://example.org/a?utm_medium=rss") == item_id("https://example.org/a/")


def test_strip_html():
    assert strip_html("<p>Aid &amp; <b>relief</b></p><script>x()</script>") == "Aid & relief"


def test_detect_lang():
    assert detect_lang("Het kabinet bezuinigt op de ontwikkelingshulp voor het komende jaar", "en") == "nl"
    assert detect_lang("The government cuts the aid budget for the coming year", "nl") == "en"
    assert detect_lang("Le cash, une réponse rapide pour les familles déplacées de la région", "en") == "fr"
    assert detect_lang("Short", "nl") == "nl"


def test_to_utc_clamps_future_dates():
    assert to_utc(NOW + timedelta(days=2), NOW) == NOW
    assert to_utc(None, NOW) == NOW


def test_normalise_uses_entry_publisher_override():
    src = Source(id="gn", name="Google News", type="gnews", q="aid")
    entry = RawEntry(source_id="gn", title="Aid cuts bite", url="https://x.org/a", publisher="devex", publisher_name="Devex")
    item = normalise(entry, src, NOW)
    assert item.publisher == "devex" and item.publisher_name == "Devex"


def test_gnews_url_built_from_query():
    src = Source(id="gn", name="GN", type="gnews", q="site:devex.com", lang="nl")
    assert src.url.startswith("https://news.google.com/rss/search?q=site%3Adevex.com+when%3A14d&hl=nl")
    assert src.publisher_id == "gn"
