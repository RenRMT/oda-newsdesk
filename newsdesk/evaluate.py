"""Compare LLM backends against hand labels.

`make_sheet` writes a YAML sheet of real items to label in an editor. `run_eval` enriches the
labelled items with one backend (on copies; the store is untouched) and `report` scores the
predictions on the judgments that drive the site: on_topic, tracks, hook and aggregator.
"""

import random
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from newsdesk.enrich import enrich, estimate_cost
from newsdesk.llm import LLMBackend
from newsdesk.models import Item

SHEET_HEADER = """\
# Hand labels for `python -m newsdesk eval`. For each item fill in:
#   on_topic: true/false  - would you want this on the newsdesk at all?
#   tracks:   the tracks that apply (aid, data-ai, nl); pre-filled with the keyword guess
#   hook:     true/false  - would you consider writing a blog post on it now?
#   aggregator: true/false - is the publisher a content farm, reposter or roundup? (optional)
#   note:     optional
# Items with on_topic: null are unlabelled and skipped. Re-running --make-sheet keeps your
# labels and only tops the sheet up to the requested size.
"""
LABELS = ("on_topic", "tracks", "hook", "aggregator", "note")
TRACKS = ("aid", "data-ai", "nl")
# Phrases that signal a chatty or over-cautious small model rather than a summary.
DISCLAIMER = re.compile(r"\b(as an ai|i cannot|i can't|i'm sorry|i am unable|disclaimer|sensitive topic)\b", re.I)


# --- labelling sheet ------------------------------------------------------------

def load_sheet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def sample(pool: list[Item], n: int, seed: int = 1) -> list[Item]:
    """Random sample that over-represents the smaller strata: Dutch-language and data-ai items."""
    rng = random.Random(seed)
    pool = sorted(pool, key=lambda i: i.id)
    rng.shuffle(pool)
    nl = [i for i in pool if i.lang == "nl"]
    data_ai = [i for i in pool if i.lang != "nl" and "data-ai" in i.tracks]
    picked = nl[: round(n * 0.3)] + data_ai[: round(n * 0.25)]
    chosen = {i.id for i in picked}
    picked += [i for i in pool if i.id not in chosen][: n - len(picked)]
    return picked


def make_sheet(items: list[Item], path: Path, n: int) -> tuple[int, int]:
    """Top the sheet at `path` up to `n` items, keeping existing rows. Returns (added, total)."""
    rows = load_sheet(path)
    have = {r["id"] for r in rows}
    pool = [i for i in items if i.id not in have]
    added = [
        {
            "id": i.id,
            "title": i.title,
            "publisher": i.publisher_name,
            "domain": i.publisher_domain or "",
            "lang": i.lang,
            "excerpt": i.excerpt[:400],
            "keyword_tracks": list(i.tracks),
            "on_topic": None,
            "tracks": list(i.tracks),
            "hook": None,
            "aggregator": None,
            "note": "",
        }
        for i in sample(pool, max(0, n - len(rows)))
    ]
    rows += added
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(rows, allow_unicode=True, sort_keys=False, width=110, default_flow_style=None)
    # A blank line between items makes the sheet easier to scan.
    body = body.replace("\n- id:", "\n\n- id:")
    path.write_text(SHEET_HEADER + "\n" + body, encoding="utf-8", newline="\n")
    return len(added), len(rows)


def labelled(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("on_topic") is not None]


# --- running and scoring ----------------------------------------------------------

def run_eval(rows: list[dict], items_by_id: dict[str, Item], backend: LLMBackend, concurrency: int = 1):
    """Enrich copies of the labelled items. Returns (predictions by id, enrich stats, seconds)."""
    copies = []
    for r in rows:
        item = items_by_id.get(r["id"])
        if item is None:
            continue
        # Fresh copy as the enrich step would see it: keyword tracks, no previous result.
        copies.append(item.model_copy(deep=True, update={
            "tracks": r.get("keyword_tracks") or item.tracks, "enrichment": None,
            "llm_status": "skipped", "llm_attempts": 0,
        }))
    start = time.monotonic()
    done, stats = enrich(copies, backend, concurrency)
    return {i.id: i for i in done}, stats, time.monotonic() - start


def predicted_tracks(item: Item) -> set[str]:
    e = item.enrichment
    return set(e.tracks) | ({"nl"} if e.nl_flag else set())


def _pr(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def score(rows: list[dict], preds: dict[str, Item]) -> dict:
    """Agreement between labels and predictions, over the items the backend answered."""
    scored = [(r, preds[r["id"]]) for r in rows if r["id"] in preds and preds[r["id"]].enrichment]
    out: dict = {"labelled": len(rows), "answered": len(scored)}
    if not scored:
        return out

    pairs = [(bool(r["on_topic"]), p.enrichment.on_topic) for r, p in scored]
    out["on_topic_accuracy"] = sum(a == b for a, b in pairs) / len(pairs)
    # Off-topic is the minority class and the one the LLM gate exists for, so score that side.
    out["off_topic"] = _pr(sum(not a and not b for a, b in pairs), sum(a and not b for a, b in pairs),
                           sum(not a and b for a, b in pairs))

    both_on = [(r, p) for r, p in scored if r["on_topic"] and p.enrichment.on_topic]
    out["tracks"] = {}
    for t in TRACKS:
        tp = sum(t in (r.get("tracks") or []) and t in predicted_tracks(p) for r, p in both_on)
        fp = sum(t not in (r.get("tracks") or []) and t in predicted_tracks(p) for r, p in both_on)
        fn = sum(t in (r.get("tracks") or []) and t not in predicted_tracks(p) for r, p in both_on)
        out["tracks"][t] = _pr(tp, fp, fn)

    hooks = [(bool(r.get("hook")), p.enrichment.hook.flag) for r, p in scored if r.get("hook") is not None]
    out["hook"] = _pr(sum(a and b for a, b in hooks), sum(not a and b for a, b in hooks),
                      sum(a and not b for a, b in hooks))
    out["hook"]["label_rate"] = sum(a for a, _ in hooks) / len(hooks) if hooks else None
    out["hook"]["pred_rate"] = sum(b for _, b in hooks) / len(hooks) if hooks else None

    aggs = [(bool(r.get("aggregator")), p.enrichment.aggregator) for r, p in scored if r.get("aggregator") is not None]
    out["aggregator"] = _pr(sum(a and b for a, b in aggs), sum(not a and b for a, b in aggs),
                            sum(a and not b for a, b in aggs))
    out["aggregator"]["labelled"] = len(aggs)
    out["aggregator"]["flagged"] = sum(p.enrichment.aggregator for _, p in scored)

    out["disclaimers"] = [p.id for _, p in scored if DISCLAIMER.search(p.enrichment.summary_en)]
    return out


# --- report -------------------------------------------------------------------------

def _fmt(x) -> str:
    return "–" if x is None else f"{x:.2f}"


def report(rows: list[dict], preds: dict[str, Item], stats: dict, seconds: float, pricing: dict, now: datetime) -> str:
    s = score(rows, preds)
    cost = estimate_cost(stats, pricing)
    failures = Counter(p.llm_status for p in preds.values() if p.llm_status != "ok")
    lines = [
        f"# Eval: {stats['backend']} / {stats['model']}",
        "",
        f"_{now:%Y-%m-%d %H:%M} UTC · {s['labelled']} labelled items, {s['answered']} answered · "
        f"{seconds:.0f} s total, {seconds / max(1, stats['attempted']):.1f} s per item · "
        f"{stats.get('input_tokens', 0) + stats.get('cache_read_input_tokens', 0):,} input / "
        f"{stats.get('output_tokens', 0):,} output tokens"
        + (f" · ≈ ${cost:.3f}" if cost is not None else "") + "_",
        "",
    ]
    if stats.get("unavailable"):
        lines += [f"**Backend became unavailable:** {stats['unavailable']}", ""]
    lines += [
        "| Measure | Value |", "|---|---|",
        f"| Refused / invalid / error | {failures['refused']} / {failures['invalid']} / {failures['error']} |",
        f"| Summaries with disclaimers | {len(s.get('disclaimers', []))} |",
    ]
    if s["answered"]:
        ot = s["off_topic"]
        lines += [
            f"| On-topic accuracy | {_fmt(s['on_topic_accuracy'])} |",
            f"| Off-topic precision / recall | {_fmt(ot['precision'])} / {_fmt(ot['recall'])} "
            f"({ot['tp']} caught, {ot['fp']} wrongly dropped, {ot['fn']} missed) |",
        ]
        for t, m in s["tracks"].items():
            lines.append(f"| Track {t} precision / recall | {_fmt(m['precision'])} / {_fmt(m['recall'])} |")
        h = s["hook"]
        lines += [
            f"| Hook precision / recall | {_fmt(h['precision'])} / {_fmt(h['recall'])} |",
            f"| Hook rate (labels / model) | {_fmt(h['label_rate'])} / {_fmt(h['pred_rate'])} |",
        ]
        a = s["aggregator"]
        lines.append(f"| Aggregator flags (precision / recall over {a['labelled']} labelled) | {a['flagged']} "
                     f"({_fmt(a['precision'])} / {_fmt(a['recall'])}) |")

    lines += ["", "## Disagreements", "",
              "On-topic or hook mismatches, and failures. Read the summaries for tone and translation quality.", ""]
    for r in rows:
        p = preds.get(r["id"])
        if p is None:
            continue
        e = p.enrichment
        if e is None:
            lines.append(f"- **{r['title']}** ({r['publisher']}, {r['lang']}): {p.llm_status}")
            continue
        issues = []
        if bool(r["on_topic"]) != e.on_topic:
            issues.append(f"on_topic label {r['on_topic']}, model {e.on_topic}")
        if r.get("hook") is not None and bool(r["hook"]) != e.hook.flag:
            issues.append(f"hook label {r['hook']}, model {e.hook.flag}")
        # Unlabelled aggregator flags are listed too, so their precision can be judged by eye.
        if e.aggregator != bool(r.get("aggregator")) and (e.aggregator or r.get("aggregator") is not None):
            issues.append(f"aggregator label {r.get('aggregator')}, model {e.aggregator}")
        if DISCLAIMER.search(e.summary_en):
            issues.append("disclaimer")
        if issues:
            lines.append(f"- **{r['title']}** ({r['publisher']}, {r['lang']}): {'; '.join(issues)}  \n"
                         f"  {e.summary_en}" + (f"  \n  Angle: {e.hook.angle}" if e.hook.flag else ""))

    lines += ["", "## Sample summaries", ""]
    for r in [r for r in rows if r["lang"] == "nl"][:5] + [r for r in rows if r["lang"] != "nl"][:5]:
        p = preds.get(r["id"])
        if p and p.enrichment:
            lines.append(f"- **{r['title']}** ({r['lang']}): {p.enrichment.summary_en}")
    return "\n".join(lines) + "\n"


def report_path(eval_dir: Path, stats: dict, now: datetime) -> Path:
    slug = re.sub(r"[^a-z0-9.]+", "-", f"{stats['backend']}-{stats['model']}".lower()).strip("-")
    return eval_dir / "reports" / f"{now:%Y-%m-%d-%H%M}-{slug}.md"
