"""Weekly digest: one LLM call over the week's stories, top items and hooks, rendered as Markdown.

The model answers in JSON and cites material by reference (S1, I4); the Markdown, including every
link, is rendered here from those references, so the digest can only link to items it was given.
Without a model, or when the call fails, a plain digest is built from the same material.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from newsdesk import TRACKS, config
from newsdesk.calendar_events import upcoming
from newsdesk.enrich import estimate_cost, load_prompt
from newsdesk.llm import BackendUnavailable, LLMBackend
from newsdesk.models import Cluster, Item
from newsdesk.store import Store

log = logging.getLogger(__name__)

MAX_STORIES = 10
ITEMS_PER_TRACK = 25
MAX_HOOK_ITEMS = 30
CALENDAR_DAYS = 28
MAX_TOKENS = 16000  # includes thinking

_REFS = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "big_picture": {"type": "array", "items": {
            "type": "object", "properties": {"trend": {"type": "string"}, "refs": _REFS},
            "required": ["trend", "refs"], "additionalProperties": False}},
        "hot_stories": {"type": "array", "items": {
            "type": "object", "properties": {"ref": {"type": "string"}, "note": {"type": "string"}},
            "required": ["ref", "note"], "additionalProperties": False}},
        "nl_corner": {"type": "array", "items": {
            "type": "object", "properties": {"text": {"type": "string"}, "refs": _REFS},
            "required": ["text", "refs"], "additionalProperties": False}},
        "publications": {"type": "array", "items": {
            "type": "object", "properties": {"ref": {"type": "string"}, "note": {"type": "string"}},
            "required": ["ref", "note"], "additionalProperties": False}},
        "hooks": {"type": "array", "items": {
            "type": "object",
            "properties": {"angle": {"type": "string"}, "why_now": {"type": "string"}, "refs": _REFS},
            "required": ["angle", "why_now", "refs"], "additionalProperties": False}},
    },
    "required": ["big_picture", "hot_stories", "nl_corner", "publications", "hooks"],
    "additionalProperties": False,
}
LIMITS = {"big_picture": 5, "hot_stories": 5, "nl_corner": 5, "publications": 6, "hooks": 5}


@dataclass
class Story:
    cluster: Cluster
    members: list[Item]

    @property
    def headline(self) -> str:
        return self.cluster.headline or self.members[0].title

    @property
    def publishers(self) -> list[str]:
        return list(dict.fromkeys(i.publisher_name for i in self.members))


@dataclass
class Material:
    week: str
    start: datetime
    end: datetime
    stories: dict[str, Story] = field(default_factory=dict)  # "S1" -> story
    items: dict[str, Item] = field(default_factory=dict)  # "I1" -> item
    events: list[dict] = field(default_factory=list)
    llm_failures: dict = field(default_factory=dict)
    item_count: int = 0
    publisher_count: int = 0


def week_label(now: datetime) -> str:
    """ISO week the digest covers: the week that ends on the Sunday before a Monday run."""
    y, w, _ = (now - timedelta(days=1)).isocalendar()
    return f"{y}-W{w:02d}"


def _rank(i: Item) -> tuple:
    hook = bool(i.enrichment and i.enrichment.hook.flag)
    return (hook, i.enrichment is not None, i.score or 0)


def gather(store: Store, now: datetime) -> Material:
    settings = config.settings()
    start = now - timedelta(days=7)
    week = [i for i in store.load_items(since=start) if i.published >= start and config.shown(i)]
    by_id = {i.id: i for i in week}
    m = Material(week_label(now), start, now, item_count=len(week),
                 publisher_count=len({i.publisher for i in week}))

    stories = []
    for c in store.load_clusters():
        members = sorted((by_id[i] for i in c.item_ids if i in by_id), key=lambda i: i.published)
        if len({i.publisher for i in members}) >= 2:
            stories.append(Story(c, members))
    stories.sort(key=lambda s: s.cluster.hot_score, reverse=True)
    m.stories = {f"S{n}": s for n, s in enumerate(stories[:MAX_STORIES], 1)}

    # Story members are cited through their story, so they are not repeated as items.
    in_stories = {i.id for s in m.stories.values() for i in s.members}
    ranked = sorted((i for i in week if i.id not in in_stories), key=_rank, reverse=True)
    chosen: dict[str, Item] = {}
    for t in TRACKS:
        for i in [i for i in ranked if t in i.tracks][:ITEMS_PER_TRACK]:
            chosen[i.id] = i
    for i in [i for i in ranked if i.enrichment and i.enrichment.hook.flag][:MAX_HOOK_ITEMS]:
        chosen[i.id] = i
    ordered = sorted(chosen.values(), key=lambda i: i.published)
    m.items = {f"I{n}": i for n, i in enumerate(ordered, 1)}

    m.events = upcoming(config.calendar(), now.date(), days=CALENDAR_DAYS)
    m.llm_failures = _failures(store, start, week, settings)
    return m


def _failures(store: Store, start: datetime, week: list[Item], settings: dict) -> dict:
    out = {"refused": 0, "invalid": 0, "error": 0, "unavailable_runs": 0, "runs": 0}
    for r in store.load_stats()["runs"]:
        if r["ts"] < start.isoformat():
            continue
        out["runs"] += 1
        llm = r.get("llm") or {}
        if "unavailable" in llm:
            out["unavailable_runs"] += 1
        for k in ("refused", "invalid", "error"):
            out[k] += llm.get(k, 0)
    out["not_enriched"] = sum(i.enrichment is None for i in week)
    return out


# --- LLM ------------------------------------------------------------------------------

def _summary(i: Item) -> str:
    text = i.enrichment.summary_en if i.enrichment else i.excerpt
    return re.sub(r"\s+", " ", text or "").strip()[:300]


def user_message(m: Material) -> str:
    lines = [f"Week {m.week}: {m.start:%Y-%m-%d} to {m.end:%Y-%m-%d}", "", "## Stories covered by several publishers"]
    for ref, s in m.stories.items():
        lines.append(f"{ref} | {len(s.publishers)} publishers ({', '.join(s.publishers)}) | "
                     f"tracks: {', '.join(s.cluster.tracks)} | {s.headline}"
                     + (f" | {s.cluster.summary}" if s.cluster.summary else ""))
    if not m.stories:
        lines.append("(none this week)")
    lines += ["", "## Items"]
    for ref, i in m.items.items():
        e = i.enrichment
        parts = [ref, f"{i.published:%Y-%m-%d}", i.publisher_name, f"tracks: {', '.join(i.tracks)}"]
        if e:
            parts += [f"type: {e.type}"] + ([f"region: {e.region}"] if e.region else [])
        parts.append(i.title + (f" — {_summary(i)}" if _summary(i) else ""))
        if e and e.hook.flag:
            parts.append(f"hook: {e.hook.angle} ({e.hook.why_now})")
        lines.append(" | ".join(parts))
    lines += ["", "## Coming up"]
    lines += [f"- {e['when']:%Y-%m-%d}: {e['name']}" for e in m.events] or ["(nothing in the calendar)"]
    return "\n".join(lines)


def clean(data: dict, m: Material) -> dict:
    """Keep only entries that cite references from the material; cap each section."""
    known = set(m.stories) | set(m.items)

    def refs(rs) -> list[str]:
        return list(dict.fromkeys(r.strip() for r in rs or [] if isinstance(r, str) and r.strip() in known))

    out = {
        "big_picture": [{"trend": e["trend"], "refs": refs(e.get("refs"))}
                        for e in data.get("big_picture", []) if e.get("trend")],
        "hot_stories": [e for e in data.get("hot_stories", []) if e.get("ref") in m.stories],
        "nl_corner": [{"text": e["text"], "refs": refs(e.get("refs"))}
                      for e in data.get("nl_corner", []) if e.get("text")],
        "publications": [e for e in data.get("publications", []) if e.get("ref") in m.items],
        "hooks": [{**e, "refs": refs(e.get("refs"))} for e in data.get("hooks", [])
                  if e.get("angle") and refs(e.get("refs"))],
    }
    return {k: v[: LIMITS[k]] for k, v in out.items()}


def write_with_llm(m: Material, backend: LLMBackend) -> tuple[dict | None, dict]:
    stats = {"backend": backend.name, "model": backend.model}
    try:
        result = backend.complete_json(load_prompt("digest"), user_message(m), SCHEMA, MAX_TOKENS)
    except BackendUnavailable as e:
        log.warning("digest model unavailable: %s", e)
        return None, stats | {"status": "unavailable", "error": str(e)[:300]}
    stats |= result.usage | {"status": result.status}
    stats["cost_usd"] = estimate_cost(stats, config.settings()["llm"]["pricing"])
    if result.status != "ok":
        log.warning("digest LLM %s: %s", result.status, result.error)
        return None, stats | {"error": result.error}
    sections = clean(result.data or {}, m)
    if not (sections["big_picture"] or sections["hooks"]):
        return None, stats | {"status": "invalid", "error": "no usable sections"}
    return sections, stats


def fallback(m: Material) -> dict:
    """A plain digest from the material alone, for weeks without a working model."""
    items = list(m.items.items())
    hooks = sorted(((r, i) for r, i in items if i.enrichment and i.enrichment.hook.flag),
                   key=lambda p: p[1].score or 0, reverse=True)
    pubs = [(r, i) for r, i in items if i.enrichment and i.enrichment.type in ("report", "policy")]
    nl = sorted(((r, i) for r, i in items if "nl" in i.tracks), key=lambda p: _rank(p[1]), reverse=True)
    return {
        "big_picture": [],
        "hot_stories": [{"ref": r, "note": s.cluster.summary} for r, s in list(m.stories.items())[:5]],
        "nl_corner": [{"text": i.enrichment.summary_en if i.enrichment else i.title, "refs": [r]} for r, i in nl[:5]],
        "publications": [{"ref": r, "note": i.enrichment.summary_en} for r, i in pubs[:6]],
        "hooks": [{"angle": i.enrichment.hook.angle, "why_now": i.enrichment.hook.why_now, "refs": [r]}
                  for r, i in hooks[:5]],
    }


# --- Markdown ---------------------------------------------------------------------------

def _md(text: str) -> str:
    return re.sub(r"([\[\]*_`])", r"\\\1", text)


def _cite(ref: str, m: Material) -> str:
    if ref in m.stories:
        s = m.stories[ref]
        return f"[{_md(s.headline)}]({s.members[0].url})"
    i = m.items[ref]
    return f"[{_md(i.publisher_name)}]({i.url})"


def _cites(refs: list[str], m: Material) -> str:
    return f" ({', '.join(_cite(r, m) for r in refs)})" if refs else ""


def _period(m: Material) -> str:
    a, b = m.start.date(), (m.end - timedelta(days=1)).date()
    if a.month == b.month:
        return f"{a.day}–{b.day} {b:%B %Y}"
    return f"{a.day} {a:%B} – {b.day} {b:%B %Y}"


def render(m: Material, sections: dict, meta: dict) -> str:
    front = {"week": m.week, "start": m.start.date().isoformat(), "end": m.end.date().isoformat(),
             "generated": meta["generated"], "status": meta["status"], "model": meta.get("model", "")}
    out = ["---", yaml.safe_dump(front, sort_keys=False, allow_unicode=True).strip(), "---", "",
           f"# Week {int(m.week.split('W')[1])}: {_period(m)}", ""]
    if meta["status"] != "ok":
        out += ["_This week's digest was put together without the digest model, so it has no trend overview "
                "and repeats the per-item summaries._", ""]

    if sections["big_picture"]:
        out += ["## Big picture", ""]
        out += [f"- {_md(e['trend'])}{_cites(e['refs'], m)}" for e in sections["big_picture"]] + [""]

    if sections["hot_stories"]:
        out += ["## Hot stories", ""]
        for e in sections["hot_stories"]:
            s = m.stories[e["ref"]]
            note = f" {_md(e['note'])}" if e.get("note") else ""
            out.append(f"- **{_cite(e['ref'], m)}**{note} _Covered by {len(s.publishers)} publishers: "
                       f"{_md(', '.join(s.publishers))}._")
        out.append("")

    if sections["nl_corner"]:
        out += ["## NL corner", ""]
        out += [f"- {_md(e['text'])}{_cites(e['refs'], m)}" for e in sections["nl_corner"]] + [""]

    if sections["publications"]:
        out += ["## Notable publications", ""]
        for e in sections["publications"]:
            i = m.items[e["ref"]]
            out.append(f"- **[{_md(i.title)}]({i.url})**, {_md(i.publisher_name)}."
                       + (f" {_md(e['note'])}" if e.get("note") else ""))
        out.append("")

    if sections["hooks"]:
        out += ["## Blog hooks", ""]
        for n, e in enumerate(sections["hooks"], 1):
            why = f" _{_md(e['why_now'])}._" if e.get("why_now") else ""
            out.append(f"{n}. **{_md(e['angle'])}**{why} Start from: {', '.join(_cite(r, m) for r in e['refs'])}.")
        out.append("")

    if m.events:
        out += ["## Coming up", ""]
        for e in m.events:
            when = ("~" if e.get("approx") else "") + f"{e['when']:%d %b}"
            out.append(f"- {when}: {_md(e['name'])} (in {e['in_days']} days)" if e["in_days"] else f"- {when}: {_md(e['name'])} (today)")
        out.append("")

    f = m.llm_failures
    note = f"{m.item_count} items from {m.publisher_count} publishers this week."
    if f.get("not_enriched"):
        note += f" {f['not_enriched']} items had no model summary"
        reasons = [f"{f[k]} {k}" for k in ("refused", "invalid", "error") if f.get(k)]
        if f.get("unavailable_runs"):
            reasons.append(f"model unavailable in {f['unavailable_runs']} of {f['runs']} runs")
        note += f" ({', '.join(reasons)})." if reasons else "."
    out += ["---", "", f"_{note}_", ""]
    return "\n".join(out)


def make_digest(store: Store, now: datetime, backend: LLMBackend | None) -> tuple[Path, dict]:
    """Write data/digests/<week>.md and return its path and run stats."""
    m = gather(store, now)
    sections, stats = (write_with_llm(m, backend) if backend else (None, {"status": "skipped"}))
    meta = {"generated": now.isoformat(), "status": "ok" if sections else "fallback",
            "model": stats.get("model", "") if sections else ""}
    text = render(m, sections or fallback(m), meta)
    path = store.dir / "digests" / f"{m.week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    stats |= {"week": m.week, "stories": len(m.stories), "items": len(m.items)}
    return path, stats


# --- reading back (site) ------------------------------------------------------------------

def load_digests(data_dir: Path) -> list[dict]:
    """All digests, newest first: front matter plus the Markdown body."""
    out = []
    for path in sorted((data_dir / "digests").glob("*.md"), reverse=True):
        text = path.read_text(encoding="utf-8")
        meta, body = {}, text
        if text.startswith("---\n"):
            head, _, body = text[4:].partition("\n---\n")
            meta = yaml.safe_load(head) or {}
        title = next((l[2:] for l in body.splitlines() if l.startswith("# ")), path.stem)
        body = re.sub(r"^# .*\n", "", body.lstrip(), count=1)
        out.append({"week": meta.get("week", path.stem), "title": title, "meta": meta, "markdown": body})
    return out
