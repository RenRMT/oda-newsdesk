import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from newsdesk import DATA_DIR, SITE_DIR
from newsdesk.build import build
from newsdesk.pipeline import ingest
from newsdesk.store import Store


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="newsdesk")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="fetch, filter and store new items")
    p_fetch.add_argument("--dry-run", action="store_true", help="print per-source results; write nothing")
    sub.add_parser("build", help="render the static site to site/")
    p_run = sub.add_parser("run", help="fetch + build")
    p_run.add_argument("--no-llm", action="store_true", help="skip LLM enrichment (phase 2+)")
    for p in (p_fetch, p_run, sub.choices["build"]):
        p.add_argument("--data-dir", type=Path, default=DATA_DIR)
        p.add_argument("--site-dir", type=Path, default=SITE_DIR)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    store = Store(args.data_dir)

    if args.cmd == "fetch":
        ingest(store, now, dry_run=args.dry_run)
    elif args.cmd == "build":
        build(store, now, args.site_dir)
    elif args.cmd == "run":
        ingest(store, now)
        build(store, now, args.site_dir)
