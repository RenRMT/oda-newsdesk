import logging
from collections import Counter
from datetime import datetime, timedelta

from newsdesk import ROOT, config
from newsdesk.dedupe import dedupe
from newsdesk.embed import Embedder, VectorCache
from newsdesk.enrich import enrich, estimate_cost, select_for_enrichment
from newsdesk.fetch import fetch_all
from newsdesk.llm import BackendUnavailable, get_backend
from newsdesk.models import Item
from newsdesk.normalise import normalise
from newsdesk.prefilter import assign_tracks
from newsdesk.score import Scorer, score_items
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


def ingest(store: Store, now: datetime, dry_run: bool = False) -> tuple[list[Item], dict]:
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

    _score(store, new_items, now, settings)
    threshold = settings["relevance_threshold"]
    new_by_source = Counter(i.source_id for i in new_items)
    above_by_source = Counter(i.source_id for i in new_items if (i.score or 0) >= config.threshold_for(i))
    for sid, row in per_source.items():
        row["new"] = new_by_source.get(sid, 0)
        row["above_threshold"] = above_by_source.get(sid, 0)

    run = {"ts": now.isoformat(), "new_items": len(new_items), "sources": per_source}
    if dry_run:
        _print_dry_run(per_source, sources, new_items, threshold)
        return new_items, run

    store.append_items(new_items)
    for item in new_items:
        seen[item.id] = now.isoformat()
    store.save_seen(seen, now, dd["seen_retention_days"])
    update_fetch_state(fetch_state, results, now)
    store.save_fetch_state(fetch_state)
    log.info("ingested %d new items (%d above threshold) from %d sources",
             len(new_items), sum(above_by_source.values()), len(results))
    return new_items, run


def _score(store: Store, items: list[Item], now: datetime, settings: dict) -> None:
    emb_cfg = settings["embedding"]
    cache_dir = ROOT / emb_cfg["cache_dir"]
    embedder = Embedder(emb_cfg["model"], cache_dir)
    cache = VectorCache(cache_dir / "vectors.npz")
    score_items(items, embedder, Scorer(embedder.embed, config.tracks()), cache)
    keep = {i.id for i in store.load_items(since=now - timedelta(days=14))} | {i.id for i in items}
    cache.save(keep)


def enrich_step(store: Store, now: datetime, run: dict, backend: str | None = None) -> None:
    settings = config.settings()
    llm_cfg = settings["llm"]
    recent = store.load_items(since=now - timedelta(days=llm_cfg["retry_window_days"]))
    todo = select_for_enrichment(recent, now, config.threshold_for, llm_cfg)
    if not todo:
        log.info("nothing to enrich")
        return
    try:
        llm = get_backend(llm_cfg, "enrich", backend)
    except BackendUnavailable as e:
        log.warning("LLM enrichment skipped: %s", e)
        run["llm"] = {"unavailable": str(e)[:300]}
        return
    done, stats = enrich(todo, llm, llm_cfg["concurrency"].get(llm.name, 1))
    stats["cost_usd"] = estimate_cost(stats, llm_cfg["pricing"])
    store.append_items(done)
    run["llm"] = stats
    log.info("enriched %d items: %s", len(done), {k: stats[k] for k in ("ok", "refused", "invalid", "error")})


def run_all(store: Store, now: datetime, use_llm: bool = True, backend: str | None = None) -> None:
    _, run = ingest(store, now)
    try:
        if use_llm:
            enrich_step(store, now, run, backend)
    finally:
        store.add_run_stats(run)  # new items are already stored; always record the run


def _print_dry_run(per_source: dict, sources: dict, new_items: list[Item], threshold: float) -> None:
    print(f"{'source':28} {'status':13} {'fetched':>7} {'keyword':>7} {'new':>5} {'>thr':>5}  error")
    for sid, row in sorted(per_source.items()):
        print(
            f"{sid:28} {row['status']:13} {row['fetched']:7} {row['relevant']:7} {row['new']:5}"
            f" {row['above_threshold']:5}  {row.get('error', '')[:70]}"
        )
    ranked = sorted(new_items, key=lambda i: i.score or 0, reverse=True)
    print(f"\n{len(new_items)} new items; thresholds {threshold}. Top and bottom by relevance:")
    for item in ranked[:10] + ranked[-10:]:
        print(f"  {item.score:.2f} [{','.join(item.tracks)}] {item.publisher_name}: {item.title[:90]}")
