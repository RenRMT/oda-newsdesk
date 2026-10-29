import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from newsdesk import DATA_DIR, SITE_DIR
from newsdesk.build import build
from newsdesk.pipeline import cluster_step, enrich_step, ingest, run_all
from newsdesk.store import Store


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="newsdesk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="fetch, filter, score and store new items")
    p_fetch.add_argument("--dry-run", action="store_true", help="print per-source results; write nothing")
    p_enrich = sub.add_parser("enrich", help="LLM-enrich recent relevant items")
    p_cluster = sub.add_parser("cluster", help="group recent items into hot stories")
    p_build = sub.add_parser("build", help="render the static site to site/")
    p_run = sub.add_parser("run", help="fetch + enrich + build")
    for p in (p_run, p_cluster):
        p.add_argument("--no-llm", action="store_true", help="skip LLM calls")
    for p in (p_enrich, p_run, p_cluster):
        p.add_argument("--backend", choices=["anthropic", "ollama"], help="override llm.backend")
    for p in (p_fetch, p_enrich, p_cluster, p_run, p_build):
        p.add_argument("--data-dir", type=Path, default=DATA_DIR)
        p.add_argument("--site-dir", type=Path, default=SITE_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "anthropic", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    store = Store(args.data_dir)

    if args.cmd == "fetch":
        _, run = ingest(store, now, dry_run=args.dry_run)
        if not args.dry_run:
            store.add_run_stats(run)
    elif args.cmd == "enrich":
        run = {"ts": now.isoformat(), "new_items": 0, "sources": {}}
        enrich_step(store, now, run, args.backend)
        store.add_run_stats(run)
    elif args.cmd == "cluster":
        run = {"ts": now.isoformat(), "new_items": 0, "sources": {}}
        cluster_step(store, now, run, not args.no_llm, args.backend)
        store.add_run_stats(run)
    elif args.cmd == "build":
        build(store, now, args.site_dir)
    elif args.cmd == "run":
        run_all(store, now, use_llm=not args.no_llm, backend=args.backend)
        build(store, now, args.site_dir)
