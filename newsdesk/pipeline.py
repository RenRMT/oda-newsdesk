import logging
from collections import Counter
from datetime import datetime, timedelta

from newsdesk import config
from newsdesk.dedupe import dedupe
from newsdesk.fetch import fetch_all
from newsdesk.models import Item
from newsdesk.normalise import normalise
from newsdesk.prefilter import assign_tracks
from newsdesk.store import Store

log = logging.getLogger(__name__)


def update_fetch_state(state: dict, results, now: datetime) -> None:
    for res in results:
        s = state.setdefault(res.source_id, {"failures": 0})
        if res.status == "error":
            s["failures"] = s.get("failures", 0) + 1
            s["last_error"] = res.error
        else:
            s["failures"] = 0
            s["last_ok"] = now.isoformat()
            s.pop("last_error", None)
            s["etag"] = res.etag
            s["last_modified"] = res.last_modified
        s["last_status"] = res.status


def ingest(store: Store, now: datetime, dry_run: bool = False) -> list[Item]:
    settings = config.settings()
    sources = {s.id: s for s in config.sources()}
    fetch_state = store.load_fetch_state()
    if dry_run:
        # Ignore conditional-GET state so a dry run always shows real content.
        fetch_state = {}

    results = fetch_all(list(sources.values()), fetch_state, settings, now)

    candidates: list[Item] = []
    per_source: dict[str, dict] = {}
    for res in results:
        src = sources[res.source_id]
        kept = 0
        for entry in res.entries:
            item = normalise(entry, src, now)
            if not item.title or item.lang not in settings["languages"]:
                continue
            item.tracks = assign_tracks(item, src)
            if item.tracks:
                candidates.append(item)
                kept += 1
        per_source[res.source_id] = {
            "status": res.status,
            "fetched": len(res.entries),
            "relevant": kept,
            **({"error": res.error} if res.error else {}),
        }

    dd = settings["dedupe"]
    recent = store.load_items(since=now - timedelta(days=dd["title_window_days"]))
    seen = store.load_seen()
    new_items = dedupe(candidates, set(seen), recent, now, dd["title_similarity"], dd["title_window_days"])

    new_by_source = Counter(i.source_id for i in new_items)
    for sid, row in per_source.items():
        row["new"] = new_by_source.get(sid, 0)

    if dry_run:
        _print_dry_run(per_source, sources, new_items)
        return new_items

    store.append_items(new_items)
    for item in new_items:
        seen[item.id] = now.isoformat()
    store.save_seen(seen, now, dd["seen_retention_days"])
    update_fetch_state(fetch_state, results, now)
    store.save_fetch_state(fetch_state)
    store.add_run_stats({"ts": now.isoformat(), "new_items": len(new_items), "sources": per_source})
    log.info("ingested %d new items from %d sources", len(new_items), len(results))
    return new_items


def _print_dry_run(per_source: dict, sources: dict, new_items: list[Item]) -> None:
    print(f"{'source':28} {'status':13} {'fetched':>7} {'relevant':>8} {'new':>5}  error")
    for sid, row in sorted(per_source.items()):
        print(
            f"{sid:28} {row['status']:13} {row['fetched']:7} {row['relevant']:8} {row['new']:5}  {row.get('error', '')[:80]}"
        )
    print(f"\n{len(new_items)} new items. Sample:")
    for item in new_items[:15]:
        print(f"  [{','.join(item.tracks)}] {item.publisher_name}: {item.title[:100]}")
