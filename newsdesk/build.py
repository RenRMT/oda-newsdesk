import json
import shutil
from datetime import datetime, timedelta
from email.utils import format_datetime
from itertools import groupby
from pathlib import Path
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

from newsdesk import SITE_DIR, STATIC_DIR, TEMPLATES_DIR, TRACKS, config
from newsdesk.calendar_events import upcoming
from newsdesk.models import Item
from newsdesk.store import Store

FEED_SIZE = 50


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(["html"]))
    env.filters["date"] = lambda d, fmt="%d %b %Y": d.strftime(fmt)
    env.filters["ago"] = _ago
    return env


def _ago(d: datetime) -> str:
    delta = datetime.now(d.tzinfo) - d
    hours = int(delta.total_seconds() // 3600)
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def _item_json(item: Item) -> dict:
    return item.model_dump(mode="json", exclude_none=True)


def _rss(title: str, link: str, items: list[Item], now: datetime) -> str:
    entries = []
    for i in items[:FEED_SIZE]:
        desc = i.enrichment.summary_en if i.enrichment else i.excerpt
        entries.append(
            "<item>"
            f"<title>{escape(i.title)}</title>"
            f"<link>{escape(i.url)}</link>"
            f"<guid isPermaLink=\"false\">{i.id}</guid>"
            f"<pubDate>{format_datetime(i.published)}</pubDate>"
            f"<source url=\"{escape(i.url)}\">{escape(i.publisher_name)}</source>"
            + "".join(f"<category>{t}</category>" for t in i.tracks)
            + f"<description>{escape(desc)}</description>"
            "</item>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel>'
        f"<title>{escape(title)}</title><link>{escape(link)}</link>"
        f"<description>{escape(title)}</description>"
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"
        + "".join(entries)
        + "</channel></rss>\n"
    )


def _source_health(store: Store, settings: dict) -> list[dict]:
    state = store.load_fetch_state()
    runs = store.load_stats()["runs"]
    last = runs[-1]["sources"] if runs else {}
    week = runs[-21:]  # ~7 days at 3 runs/day
    rows = []
    for src in config.sources():
        s = state.get(src.id, {})
        rows.append(
            {
                "source": src,
                "last": last.get(src.id, {}),
                "new_7d": sum(r["sources"].get(src.id, {}).get("new", 0) for r in week),
                "failures": s.get("failures", 0),
                "last_ok": s.get("last_ok"),
                "error": s.get("last_error"),
                "dead": s.get("failures", 0) >= settings["fetch"]["dead_after_failures"],
            }
        )
    return rows


def build(store: Store, now: datetime, out: Path = SITE_DIR) -> None:
    settings = config.settings()
    tracks = config.tracks()
    items = store.load_items(since=now - timedelta(days=settings["site_window_days"]))
    items.sort(key=lambda i: i.published, reverse=True)
    week = [i for i in items if i.published >= now - timedelta(days=7)]

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(STATIC_DIR, out / "static")
    (out / ".nojekyll").touch()
    if settings["site"].get("cname"):
        (out / "CNAME").write_text(settings["site"]["cname"] + "\n")

    env = _env()
    base = {
        "site": settings["site"],
        "tracks": tracks,
        "now": now,
        "events": upcoming(config.calendar(), now.date()),
    }

    def render(template: str, path: str, **ctx):
        depth = path.count("/")
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        html = env.get_template(template).render(**base, root="../" * depth, **ctx)
        target.write_text(html, encoding="utf-8")

    days = [(d, list(g)) for d, g in groupby(week, key=lambda i: i.published.date())]
    render("index.html", "index.html", items=week, days=days, page="home")
    for t in TRACKS:
        render("track.html", f"track/{t}/index.html", items=[i for i in items if t in i.tracks], track=t, page=t)
    render("about.html", "about/index.html", sources=config.sources(), keywords=config.keywords(), page="about")
    render("stats.html", "stats/index.html", health=_source_health(store, settings), runs=store.load_stats()["runs"][-30:], page="stats")

    (out / "data").mkdir()
    (out / "data" / "recent.json").write_text(json.dumps([_item_json(i) for i in items], ensure_ascii=False), encoding="utf-8")

    link = settings["site"].get("base_url") or ""
    title = settings["site"]["title"]
    (out / "feed.xml").write_text(_rss(title, link, items, now), encoding="utf-8")
    for t in TRACKS:
        (out / f"feed-{t}.xml").write_text(
            _rss(f"{title} – {tracks[t]['label']}", link, [i for i in items if t in i.tracks], now), encoding="utf-8"
        )
