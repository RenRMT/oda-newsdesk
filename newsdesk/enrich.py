import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from pydantic import ValidationError

from newsdesk import ROOT
from newsdesk.llm import BackendUnavailable, LLMBackend, LLMResult
from newsdesk.models import Enrichment, Item

log = logging.getLogger(__name__)

TRACK_ENUM = ["aid", "data-ai", "nl"]
SCHEMA = {
    "type": "object",
    "properties": {
        "on_topic": {"type": "boolean"},
        "tracks": {"type": "array", "items": {"type": "string", "enum": TRACK_ENUM}},
        "summary_en": {"type": "string"},
        "type": {"type": "string", "enum": ["news", "report", "policy", "opinion", "event"]},
        "region": {"type": "string"},
        "nl_flag": {"type": "boolean"},
        "hook": {
            "type": "object",
            "properties": {
                "flag": {"type": "boolean"},
                "angle": {"type": "string"},
                "why_now": {"type": "string"},
            },
            "required": ["flag", "angle", "why_now"],
            "additionalProperties": False,
        },
    },
    "required": ["on_topic", "tracks", "summary_en", "type", "region", "nl_flag", "hook"],
    "additionalProperties": False,
}
MAX_TOKENS = 4000  # includes thinking tokens at low effort


def load_prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def user_message(item: Item) -> str:
    return "\n".join(
        [
            f"Title: {item.title}",
            f"Publisher: {item.publisher_name}",
            f"Published: {item.published:%Y-%m-%d}",
            f"Language: {item.lang}",
            f"Keyword tracks: {', '.join(item.tracks)}",
            f"Excerpt: {item.excerpt or '(none)'}",
        ]
    )


def select_for_enrichment(
    items: list[Item], now: datetime, threshold_for: Callable[[Item], float], cfg: dict
) -> list[Item]:
    """Recent, relevant items not yet enriched (or failed transiently), best first, capped."""
    cutoff = now - timedelta(days=cfg["retry_window_days"])
    pool = [
        i
        for i in items
        if i.llm_status in ("skipped", "error", "invalid")
        and i.llm_attempts < cfg["max_attempts"]
        and i.fetched >= cutoff
        and (i.score or 0) >= threshold_for(i)
    ]
    pool.sort(key=lambda i: (i.llm_attempts, -(i.score or 0)))
    return pool[: cfg["max_items_per_run"]]


def apply_result(item: Item, result: LLMResult, backend: str) -> None:
    item.llm_attempts += 1
    item.backend = backend
    if result.status != "ok":
        item.llm_status = result.status
        return
    try:
        enrichment = Enrichment.model_validate(result.data)
    except ValidationError:
        item.llm_status = "invalid"
        return
    item.enrichment = enrichment
    item.llm_status = "ok"
    if enrichment.on_topic and enrichment.tracks:
        tracks = set(enrichment.tracks)
        if enrichment.nl_flag:
            tracks.add("nl")
        if tracks & {"aid", "data-ai"}:
            item.tracks = sorted(tracks)


def enrich(items: list[Item], backend: LLMBackend, concurrency: int = 1) -> tuple[list[Item], dict]:
    """Enrich items in place. Returns (items that were attempted, run stats)."""
    system = load_prompt("enrich")
    stats = {"backend": backend.name, "model": backend.model, "attempted": 0,
             "ok": 0, "refused": 0, "invalid": 0, "error": 0,
             "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
    done: list[Item] = []

    def run(item: Item) -> LLMResult:
        return backend.complete_json(system, user_message(item), SCHEMA, MAX_TOKENS)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = [(item, pool.submit(run, item)) for item in items]
        for item, fut in futures:
            try:
                result = fut.result()
            except BackendUnavailable as e:
                log.error("LLM backend unavailable, stopping enrichment: %s", e)
                stats["unavailable"] = str(e)[:300]
                for f in (f for _, f in futures):
                    f.cancel()
                break
            apply_result(item, result, backend.name)
            stats["attempted"] += 1
            stats[item.llm_status] += 1
            for k, v in result.usage.items():
                stats[k] = stats.get(k, 0) + v
            if result.status != "ok":
                log.warning("LLM %s for %s: %s", result.status, item.id, result.error)
            done.append(item)
    return done, stats


def estimate_cost(stats: dict, pricing: dict) -> float | None:
    price = pricing.get(stats.get("model"))
    if not price:
        return None
    cached = stats.get("cache_read_input_tokens", 0)
    uncached = stats.get("input_tokens", 0)
    return round((uncached * price[0] + cached * price[0] * 0.1 + stats.get("output_tokens", 0) * price[1]) / 1e6, 4)
