import hashlib
import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from newsdesk.models import Item, RawEntry, Source

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "s_cid"}
STOPWORDS = {
    "nl": {"de", "het", "een", "en", "van", "voor", "niet", "met", "zijn", "wordt", "naar", "ook", "bij", "dat", "die", "op"},
    "en": {"the", "and", "of", "for", "with", "is", "are", "to", "in", "on", "that", "this", "by", "from"},
    "fr": {"le", "la", "les", "des", "du", "et", "une", "pour", "est", "dans", "sur", "au", "aux", "d", "l"},
    "es": {"el", "los", "las", "del", "y", "para", "con", "por", "una", "que", "en", "la"},
}
EXCERPT_LEN = 1000


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = re.sub(r"/amp/?$", "", parts.path)
    if path != "/":
        path = path.rstrip("/")
    host = parts.netloc.lower()
    if host.startswith("amp."):
        host = host[4:]
    return urlunsplit((parts.scheme.lower() or "https", host, path, urlencode(query), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, n: int = EXCERPT_LEN) -> str:
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut + "…"


def detect_lang(text: str, default: str) -> str:
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    scores = {lang: sum(w in sw for w in words) for lang, sw in STOPWORDS.items()}
    best = max(scores, key=lambda lang: (scores[lang], lang == default))
    if scores[best] < 3:
        return default
    return best


def to_utc(dt: datetime | None, fallback: datetime) -> datetime:
    if dt is None:
        return fallback
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    # Feeds sometimes carry future dates; clamp to fetch time.
    return min(dt, fallback)


def normalise(entry: RawEntry, source: Source, now: datetime) -> Item:
    title = strip_html(entry.title)
    excerpt = truncate(strip_html(entry.summary))
    if excerpt == title:
        excerpt = ""
    url = canonical_url(entry.url)
    return Item(
        id=item_id(url),
        url=url,
        title=title,
        source_id=source.id,
        publisher=entry.publisher or source.publisher_id,
        publisher_name=entry.publisher_name or source.name,
        lang=entry.lang or detect_lang(f"{title} {excerpt}", source.lang),
        published=to_utc(entry.published, now),
        fetched=now,
        excerpt=excerpt,
    )


def normalise_title(title: str) -> str:
    return re.sub(r"[^\w ]", "", title.lower()).strip()
