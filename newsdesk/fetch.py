import logging
import os
import re
import time
from calendar import timegm
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from newsdesk.models import RawEntry, Source

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; ODA-Newsdesk/0.1; personal news aggregator)"
RELIEFWEB_URL = "https://api.reliefweb.int/v2/reports"


@dataclass
class FetchResult:
    source_id: str
    entries: list[RawEntry] = field(default_factory=list)
    status: str = "ok"  # ok | not_modified | error
    error: str | None = None
    etag: str | None = None
    last_modified: str | None = None


def _struct_to_dt(st) -> datetime | None:
    return datetime.fromtimestamp(timegm(st), tz=timezone.utc) if st else None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_feed(content: bytes, source: Source) -> list[RawEntry]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"unparseable feed: {feed.bozo_exception}")
    out = []
    for e in feed.entries:
        link = e.get("link")
        title = e.get("title")
        if not link or not title:
            continue
        publisher = publisher_name = None
        if source.type == "gnews":
            # Titles come as "Headline - Publisher"; strip the suffix. Generic
            # queries credit each item to its own publisher, site: queries to the source.
            item_publisher = (e.get("source") or {}).get("title")
            if item_publisher:
                title = re.sub(rf"\s+-\s+{re.escape(item_publisher)}\s*$", "", title)
                if source.publisher is None:
                    publisher, publisher_name = _slug(item_publisher), item_publisher
        out.append(
            RawEntry(
                source_id=source.id,
                title=title,
                url=link,
                summary="" if source.type == "gnews" else (e.get("summary") or ""),
                published=_struct_to_dt(e.get("published_parsed") or e.get("updated_parsed")),
                publisher=publisher,
                publisher_name=publisher_name,
            )
        )
    return out


def _get(client: httpx.Client, method: str, url: str, retries: int, **kw) -> httpx.Response:
    for attempt in range(retries + 1):
        try:
            r = client.request(method, url, **kw)
            if r.status_code < 500 and r.status_code != 429:
                return r
        except httpx.TransportError:
            if attempt == retries:
                raise
        time.sleep(2**attempt)
    return r


def fetch_rss(client: httpx.Client, source: Source, state: dict, retries: int) -> FetchResult:
    headers = {}
    if state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    r = _get(client, "GET", source.url, retries, headers=headers)
    if r.status_code == 304:
        return FetchResult(source.id, status="not_modified", etag=state.get("etag"), last_modified=state.get("last_modified"))
    r.raise_for_status()
    return FetchResult(
        source.id,
        entries=parse_feed(r.content, source),
        etag=r.headers.get("etag"),
        last_modified=r.headers.get("last-modified"),
    )


def fetch_reliefweb(client: httpx.Client, source: Source, since: datetime, retries: int) -> FetchResult:
    appname = os.environ.get("RELIEFWEB_APPNAME", "oda-newsdesk")
    body = {
        "query": {"value": source.query},
        "filter": {"field": "date.created", "value": {"from": since.strftime("%Y-%m-%dT%H:%M:%S+00:00")}},
        "fields": {"include": ["title", "url_alias", "url", "body", "date.created", "source.shortname", "source.name", "language.code"]},
        "sort": ["date.created:desc"],
        "limit": 100,
    }
    r = _get(client, "POST", RELIEFWEB_URL, retries, params={"appname": appname}, json=body)
    r.raise_for_status()
    entries = []
    for row in r.json().get("data", []):
        f = row["fields"]
        src = (f.get("source") or [{}])[0]
        langs = [l.get("code") for l in f.get("language") or []]
        entries.append(
            RawEntry(
                source_id=source.id,
                title=f["title"],
                url=f.get("url_alias") or f["url"],
                summary=f.get("body", ""),
                published=datetime.fromisoformat(f["date"]["created"]),
                publisher=_slug(src.get("shortname", "reliefweb")),
                publisher_name=src.get("name"),
                lang="nl" if "nl" in langs else "en" if "en" in langs else None,
            )
        )
    return FetchResult(source.id, entries=entries)


def fetch_source(client: httpx.Client, source: Source, state: dict, since: datetime, retries: int) -> FetchResult:
    try:
        if source.type == "reliefweb":
            res = fetch_reliefweb(client, source, since, retries)
        else:
            res = fetch_rss(client, source, state, retries)
    except Exception as exc:  # one bad feed must not break the run
        log.warning("fetch %s failed: %s", source.id, exc)
        return FetchResult(source.id, status="error", error=f"{type(exc).__name__}: {exc}"[:300])
    res.entries = [e for e in res.entries if e.published is None or e.published >= since]
    return res


def fetch_all(sources: list[Source], fetch_state: dict, settings: dict, now: datetime) -> list[FetchResult]:
    cfg = settings["fetch"]
    since = now - timedelta(days=cfg["max_age_days"])
    with httpx.Client(
        timeout=cfg["timeout_s"], follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client, ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(fetch_source, client, s, fetch_state.get(s.id, {}), since, cfg["retries"])
            for s in sources
            if s.enabled
        ]
        return [f.result() for f in futures]
