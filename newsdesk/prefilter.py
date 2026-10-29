import re
from functools import cache

from newsdesk import config
from newsdesk.models import Item, Source


def _pattern(term: str) -> str:
    if term.endswith("*"):
        return r"\b" + re.escape(term[:-1]) + r"\w*"
    return r"\b" + re.escape(term) + r"\b"


def compile_keywords(kw: dict) -> dict[str, tuple[re.Pattern | None, re.Pattern | None]]:
    out = {}
    for track, spec in kw.items():
        ci = spec.get("terms") or []
        cs = spec.get("terms_cs") or []
        out[track] = (
            re.compile("|".join(map(_pattern, ci)), re.I) if ci else None,
            re.compile("|".join(map(_pattern, cs))) if cs else None,
        )
    return out


@cache
def _compiled():
    return compile_keywords(config.keywords())


def matches(text: str, compiled=None) -> set[str]:
    compiled = compiled or _compiled()
    hits = set()
    for track, (ci, cs) in compiled.items():
        if (ci and ci.search(text)) or (cs and cs.search(text)):
            hits.add(track)
    return hits


def assign_tracks(item: Item, source: Source, compiled=None) -> list[str]:
    """Return tracks for an item; an empty list means the item is filtered out.

    data-ai counts only alongside an aid signal (or a data-ai source), and nl
    only alongside aid or data-ai, so generic tech or Dutch news is excluded.
    """
    hits = matches(f"{item.title} {item.excerpt}", compiled)
    if not source.prefilter:
        tracks = set(source.tracks)
    else:
        tracks = set()
        if "aid" in hits:
            tracks.add("aid")
        if "data-ai" in hits and ("aid" in hits or "data-ai" in source.tracks):
            tracks.add("data-ai")
    if not tracks & {"aid", "data-ai"}:
        return []
    if "nl" in hits or item.lang == "nl" or "nl" in source.tracks:
        tracks.add("nl")
    if "data-ai" in hits and "aid" in tracks:
        tracks.add("data-ai")
    return sorted(tracks)
