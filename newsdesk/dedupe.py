from datetime import datetime, timedelta

from rapidfuzz import fuzz

from newsdesk.models import Item
from newsdesk.normalise import normalise_title


def dedupe(
    candidates: list[Item],
    seen_ids: set[str],
    recent: list[Item],
    now: datetime,
    similarity: int = 92,
    window_days: int = 3,
) -> list[Item]:
    """Drop candidates already seen by id, or whose title near-duplicates a
    recent item (or an earlier candidate in this batch)."""
    cutoff = now - timedelta(days=window_days)
    titles = [normalise_title(i.title) for i in recent if i.published >= cutoff]
    kept: list[Item] = []
    ids = set(seen_ids)
    for item in candidates:
        if item.id in ids:
            continue
        t = normalise_title(item.title)
        if len(t) >= 20 and any(fuzz.token_sort_ratio(t, other) >= similarity for other in titles):
            continue
        kept.append(item)
        ids.add(item.id)
        titles.append(t)
    return kept
