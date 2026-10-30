import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path

from newsdesk import DATA_DIR, SITE_DIR, config, digest, evaluate
from newsdesk.build import build
from newsdesk.llm import BackendUnavailable, backend_name, get_backend
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
    p_eval = sub.add_parser("eval", help="score an LLM backend against hand labels in data/eval/labels.yaml")
    p_eval.add_argument("--make-sheet", type=int, metavar="N", help="top the labelling sheet up to N items and exit")
    p_eval.add_argument("--model", help="override the configured enrich model")
    p_eval.add_argument("--limit", type=int, help="only the first N labelled items")
    p_digest = sub.add_parser("digest", help="write this week's digest to data/digests/ and rebuild the site")
    p_info = sub.add_parser("backend-info", help="print the resolved backend and model for a role (for CI)")
    p_info.add_argument("--role", choices=["enrich", "digest"], default="enrich")
    for p in (p_run, p_cluster, p_digest):
        p.add_argument("--no-llm", action="store_true", help="skip LLM calls")
    for p in (p_enrich, p_run, p_cluster, p_eval, p_info, p_digest):
        p.add_argument("--backend", choices=["anthropic", "ollama"], help="override llm.backend")
    for p in (p_fetch, p_enrich, p_cluster, p_run, p_build, p_eval, p_digest):
        p.add_argument("--data-dir", type=Path, default=DATA_DIR)
        p.add_argument("--site-dir", type=Path, default=SITE_DIR)
    args = parser.parse_args(argv)

    if args.cmd == "backend-info":
        llm_cfg = config.settings()["llm"]
        name = backend_name(llm_cfg, args.role, args.backend)
        print(f"name={name}\nmodel={llm_cfg['models'][name][args.role]}")
        return

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
    elif args.cmd == "eval":
        _eval(store, now, args)
    elif args.cmd == "digest":
        _digest(store, now, args)


def _digest(store: Store, now: datetime, args) -> None:
    llm = None
    if not args.no_llm:
        try:
            llm = get_backend(config.settings()["llm"], "digest", args.backend)
        except BackendUnavailable as e:
            logging.warning("digest model unavailable, writing a plain digest: %s", e)
    path, stats = digest.make_digest(store, now, llm)
    store.add_run_stats({"ts": now.isoformat(), "new_items": 0, "sources": {}, "digest": stats})
    print(f"digest written to {path} ({stats['status']})")
    build(store, now, args.site_dir)


def _eval(store: Store, now: datetime, args) -> None:
    eval_dir = args.data_dir / "eval"
    sheet = eval_dir / "labels.yaml"
    items = store.load_items()
    if args.make_sheet:
        # The same pool the enrich step sees: relevant, not excluded. LLM off-topic verdicts stay in.
        pool = [i for i in items if not config.excluded(i) and (i.score or 0) >= config.threshold_for(i)]
        added, total = evaluate.make_sheet(pool, sheet, args.make_sheet)
        print(f"added {added} items to {sheet} ({total} in total, {len(evaluate.labelled(evaluate.load_sheet(sheet)))} labelled)")
        return
    rows = evaluate.labelled(evaluate.load_sheet(sheet))[: args.limit]
    if not rows:
        raise SystemExit(f"no labelled items in {sheet}; run `eval --make-sheet 60` and fill it in")
    llm_cfg = config.settings()["llm"]
    try:
        llm = get_backend(llm_cfg, "enrich", args.backend, args.model)
    except BackendUnavailable as e:
        raise SystemExit(f"backend unavailable: {e}")
    preds, stats, seconds = evaluate.run_eval(rows, {i.id: i for i in items}, llm,
                                             llm_cfg["concurrency"].get(llm.name, 1))
    text = evaluate.report(rows, preds, stats, seconds, llm_cfg["pricing"], now)
    path = evaluate.report_path(eval_dir, stats, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    print(f"report written to {path}")
