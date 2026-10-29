from pathlib import Path

from newsdesk.fetch import parse_feed
from newsdesk.models import Source

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_rss():
    src = Source(id="tnh", name="TNH", type="rss", url="https://x")
    entries = parse_feed((FIXTURES / "rss.xml").read_bytes(), src)
    assert len(entries) == 2
    assert entries[0].title == "Aid cuts deepen crisis"
    assert entries[0].published.year == 2026
    assert entries[0].publisher is None


def test_parse_gnews_generic_takes_item_publisher():
    src = Source(id="gn", name="GN", type="gnews", q="aid")
    e = parse_feed((FIXTURES / "gnews.xml").read_bytes(), src)[0]
    assert e.title == "Netherlands slashes aid budget"
    assert e.publisher == "nrc" and e.publisher_name == "NRC" and e.publisher_url == "https://www.nrc.nl"
    assert e.summary == ""


def test_parse_gnews_site_query_keeps_source_publisher():
    src = Source(id="devex", name="Devex", type="gnews", q="site:devex.com", publisher="devex")
    e = parse_feed((FIXTURES / "gnews.xml").read_bytes(), src)[0]
    assert e.title == "Netherlands slashes aid budget"
    assert e.publisher is None  # normalise falls back to source.publisher_id
