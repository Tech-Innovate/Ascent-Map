from __future__ import annotations

import argparse
from pathlib import Path

from .config import project_paths
from .db import Database
from .prospect.pipeline import evaluate_all, ingest_maps
from .prospect.report import latest_business_report, render_json, render_text


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", type=Path, help="Ascent Map repository root")
    parser.add_argument("--db", type=Path, default=Path(".ascent-map/ascent-map.duckdb"), help="DuckDB file")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ascent-map", description="Ascent Map local Prospect Intelligence pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-db", help="Create/update the local DuckDB schema")
    _common(init)

    ingest = sub.add_parser("ingest-maps", help="Ingest google-maps-scraper JSON/JSONL")
    _common(ingest)
    ingest.add_argument("source", type=Path)

    evaluate = sub.add_parser("evaluate", help="Resolve evidence, derive signals, and evaluate services/channels")
    _common(evaluate)
    evaluate.add_argument("--business-id")

    show = sub.add_parser("show", help="Show latest local prospect evaluation")
    _common(show)
    show.add_argument("--business-id")
    show.add_argument("--json", action="store_true", dest="as_json")

    run = sub.add_parser("run", help="Initialize, ingest, evaluate, and show in one command")
    _common(run)
    run.add_argument("source", type=Path)
    run.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _context(args):
    paths = project_paths(args.project_root)
    db_path = args.db if args.db.is_absolute() else paths.root / args.db
    return paths, Database(db_path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths, db = _context(args)

    if args.command == "init-db":
        db.initialize(paths.schema)
        print(f"Initialized {db.path}")
        return 0
    if args.command == "ingest-maps":
        db.initialize(paths.schema)
        stats = ingest_maps(db, paths, args.source)
        print(f"Ingested {stats['records']} record(s), {stats['businesses']} business(es), {stats['evidence']} new evidence item(s)")
        return 0
    if args.command == "evaluate":
        db.initialize(paths.schema)
        results = evaluate_all(db, paths, args.business_id)
        print(f"Evaluated {len(results)} business(es)")
        return 0
    if args.command == "show":
        reports = latest_business_report(db, args.business_id)
        print(render_json(reports) if args.as_json else render_text(reports), end="")
        return 0
    if args.command == "run":
        db.initialize(paths.schema)
        stats = ingest_maps(db, paths, args.source)
        evaluate_all(db, paths)
        reports = latest_business_report(db)
        print(render_json(reports) if args.as_json else render_text(reports), end="")
        print(f"Processed {stats['records']} input record(s)")
        return 0
    return 2
