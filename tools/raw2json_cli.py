#!/usr/bin/env python3
"""CLI: convert a raw directory of files into structured JSON (dataset.jsonl).

Usage (double-click safe - no 'import'/'from' at module top beyond stdlib):
    python tools/raw2json_cli.py <root> [--out-dir DIR] [--ext .py,.rst]
                                  [--max-bytes N] [--max-files N]
                                  [--feed [CORPUS_DIR]] [--no-dataset] [--json]

Examples:
    # Convert Code_base into Code_base/_converted/dataset.jsonl
    python tools/raw2json_cli.py Code_base

    # Only Python + RST, and feed the result into the learning corpus
    python tools/raw2json_cli.py Code_base --ext .py,.rst --feed Code_base

Security notes:
    - Read-only scan; never executes or writes inside the scanned tree.
    - Output is written to <root>/_converted unless --out-dir is given.
    - --feed merges into a corpus dir (default Code_base) via sha256 dedup.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# Make the project root importable when run as a script from tools/.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert a raw directory of files into structured JSON.",
    )
    p.add_argument("root", help="directory of raw files to convert")
    p.add_argument("--out-dir", help="output directory (default: <root>/_converted)")
    p.add_argument("--ext", help="comma-separated extension allow-list, e.g. .py,.rst,.md")
    p.add_argument("--max-bytes", type=int, default=512 * 1024,
                   help="max bytes read per file (default 512KB)")
    p.add_argument("--max-files", type=int, default=5000,
                   help="max number of files to scan (default 5000)")
    p.add_argument("--feed", nargs="?", const="Code_base", default=None,
                   help="merge the result into a corpus dir (default: Code_base)")
    p.add_argument("--no-dataset", action="store_true",
                   help="do not write dataset files (in-memory only)")
    p.add_argument("--json", action="store_true", help="emit JSON result to stdout")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Lazy import keeps the CLI runnable without the full captn package if needed.
    try:
        from captn.workers.raw2json import convert_raw_directory
    except ImportError as e:
        sys.stderr.write(f"[raw2json] import error: {e}\n")
        return 3

    if not os.path.isdir(args.root):
        sys.stderr.write(f"[raw2json] not a directory: {args.root}\n")
        return 2

    include_ext = None
    if args.ext:
        include_ext = [e.strip() if e.strip().startswith(".") else "." + e.strip()
                       for e in args.ext.split(",") if e.strip()]

    try:
        result = convert_raw_directory(
            args.root,
            out_dir=args.out_dir,
            include_ext=include_ext,
            max_bytes=args.max_bytes,
            max_files=args.max_files,
            export_dataset=not args.no_dataset,
        )
    except Exception as e:
        sys.stderr.write(f"[raw2json] failed: {type(e).__name__}: {e}\n")
        return 2

    print(f"[raw2json] included={len(result.files_included)} "
          f"skipped={len(result.files_skipped)} errors={len(result.errors)}")
    if result.dataset_jsonl:
        print(f"[raw2json] dataset: {result.dataset_jsonl}")

    feed_msg = ""
    if args.feed:
        try:
            from captn.workers.crawler import merge_dataset_into_corpus
            stats = merge_dataset_into_corpus(result.dataset_jsonl, args.feed)
            feed_msg = (f" | corpus: +{stats['added']} (dup {stats['duplicates']}, "
                        f"now {stats['added'] + stats['existing']})")
        except Exception as e:
            feed_msg = f" | corpus-feed-error: {e}"
    print(f"[raw2json] done{feed_msg}")

    if args.json:
        print(json.dumps({
            "ok": True,
            "root": result.root,
            "included": len(result.files_included),
            "skipped": len(result.files_skipped),
            "errors": len(result.errors),
            "dataset_jsonl": result.dataset_jsonl,
            "manifest": result.manifest,
        }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
