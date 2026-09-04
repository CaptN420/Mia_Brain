#!/usr/bin/env python3
"""dataset_tools — Stats, échantillonnage et recherche sur datasets .jsonl.

Usage:
    python tools/dataset_tools.py stats --file dataset.jsonl
    python tools/dataset_tools.py sample --file dataset.jsonl --n 10
    python tools/dataset_tools.py search --file dataset.jsonl --query "async"
    python tools/dataset_tools.py fields --file dataset.jsonl
    python tools/dataset_tools.py dedup --file dataset.jsonl --output deduped.jsonl
    python tools/dataset_tools.py filter --file dataset.jsonl --min-lines 10 --output filtered.jsonl

Déterministe, zéro LLM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_jsonl(path: str) -> List[Dict[str, Any]]:
    records = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"⚠️  Ligne {i}: JSON invalide — ignorée ({e})", file=sys.stderr)
    return records


# ═══════════════════════════════════════════════════════════════
# 1. STATS
# ═══════════════════════════════════════════════════════════════

def cmd_stats(args):
    records = _load_jsonl(args.file)
    if not records:
        print(json.dumps({"error": "Dataset vide"}, indent=2))
        return

    # Shape
    fields = set()
    field_types: Dict[str, set] = {}
    for rec in records:
        for k, v in rec.items():
            fields.add(k)
            if k not in field_types:
                field_types[k] = set()
            field_types[k].add(type(v).__name__)

    # Content stats
    total_chars = sum(len(json.dumps(r, ensure_ascii=False)) for r in records)
    avg_chars = total_chars / len(records) if records else 0

    # Missing values
    missing: Dict[str, int] = {}
    for k in sorted(fields):
        missing[k] = sum(1 for r in records if k not in r or r[k] is None)

    # Source / domain breakdown if those fields exist
    source_breakdown = Counter()
    domain_breakdown = Counter()
    for r in records:
        if "source" in r:
            source_breakdown[str(r["source"])] += 1
        if "domain" in r:
            domain_breakdown[str(r["domain"])] += 1

    # Longest / shortest records
    lengths = [(i, len(json.dumps(r, ensure_ascii=False))) for i, r in enumerate(records)]
    lengths.sort(key=lambda x: x[1], reverse=True)

    result = {
        "filename": args.file,
        "total_records": len(records),
        "total_chars": total_chars,
        "avg_chars_per_record": round(avg_chars, 1),
        "fields": sorted(fields),
        "field_types": {k: sorted(v) for k, v in field_types.items()},
        "field_missing": missing,
        "top_sources": source_breakdown.most_common(10),
        "top_domains": domain_breakdown.most_common(10),
        "top_longest": [
            {
                "index": idx,
                "size_chars": size,
                "preview": str(records[idx].get("code", records[idx].get("source", "")))[:120]
            }
            for idx, size in lengths[:5]
        ],
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# 2. SAMPLE
# ═══════════════════════════════════════════════════════════════

def cmd_sample(args):
    records = _load_jsonl(args.file)
    if not records:
        print("Dataset vide", file=sys.stderr)
        return
    n = min(args.n, len(records))
    sample = records[:n] if args.head else records[-n:]
    for rec in sample:
        print(json.dumps(rec, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# 3. SEARCH (full-text dans tous les champs)
# ═══════════════════════════════════════════════════════════════

def cmd_search(args):
    records = _load_jsonl(args.file)
    query = args.query.lower()
    matches = []
    for rec in records:
        haystack = json.dumps(rec, ensure_ascii=False).lower()
        if query in haystack:
            matches.append(rec)

    for rec in matches[: args.limit]:
        print(json.dumps(rec, ensure_ascii=False))

    if len(matches) > args.limit:
        print(f"... et {len(matches) - args.limit} résultats supplémentaires", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════
# 4. FIELDS — liste et exemples
# ═══════════════════════════════════════════════════════════════

def cmd_fields(args):
    records = _load_jsonl(args.file)
    if not records:
        print("Dataset vide", file=sys.stderr)
        return
    example = records[0]
    print(f"Total records: {len(records)}")
    print(f"Fields ({len(example)}):")
    for k, v in example.items():
        val_str = json.dumps(v, ensure_ascii=False)
        if len(val_str) > 200:
            val_str = val_str[:200] + "..."
        print(f"  {k:30s} ({type(v).__name__:10s}) = {val_str}")


# ═══════════════════════════════════════════════════════════════
# 5. DEDUP — déduplication par hash de contenu
# ═══════════════════════════════════════════════════════════════

def cmd_dedup(args):
    records = _load_jsonl(args.file)
    seen = set()
    deduped = []
    duplicates = 0
    for rec in records:
        # Hash on serialised content (canonical JSON, sorted keys)
        blob = json.dumps(rec, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256(blob.encode()).hexdigest()
        if h in seen:
            duplicates += 1
        else:
            seen.add(h)
            deduped.append(rec)

    if args.output:
        with open(args.output, "w") as f:
            for rec in deduped:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"✅ {len(deduped)} lignes (supprimé {duplicates} doublons) → {args.output}")
    else:
        print(f"Total: {len(records)}, Deduped: {len(deduped)}, Duplicates: {duplicates}")


# ═══════════════════════════════════════════════════════════════
# 6. FILTER — filtre par taille de code
# ═══════════════════════════════════════════════════════════════

def cmd_filter(args):
    records = _load_jsonl(args.file)
    filtered = []
    for rec in records:
        # Check multiple common keys for line count
        code = rec.get("code", rec.get("source", rec.get("content", "")))
        lines = code.splitlines() if isinstance(code, str) else []
        if len(lines) >= args.min_lines:
            filtered.append(rec)

    if args.max_lines is not None:
        before = len(filtered)
        filtered = [r for r in filtered if
                    len((r.get("code", r.get("source", r.get("content", "")))).splitlines())
                    <= args.max_lines]

    if args.output:
        with open(args.output, "w") as f:
            for rec in filtered:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"✅ {len(filtered)} lignes → {args.output}")
    else:
        print(f"Total: {len(records)}, Filtré ({args.min_lines}+ lignes): {len(filtered)}")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def register_cli(subparsers):
    """Register dataset as a CLI subcommand."""
    p = subparsers.add_parser("dataset", help="Outils pour datasets JSONL (stats, search, dedup)")
    sub = p.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="Statistiques du dataset")
    p_stats.add_argument("--file", "-f", required=True)
    p_stats.set_defaults(func=cmd_stats)

    p_sample = sub.add_parser("sample", help="Échantillonner N lignes")
    p_sample.add_argument("--file", "-f", required=True)
    p_sample.add_argument("--n", type=int, default=10)
    p_sample.add_argument("--head", action="store_true", help="Début du fichier (défaut: fin)")
    p_sample.set_defaults(func=cmd_sample)

    p_search = sub.add_parser("search", help="Recherche full-text")
    p_search.add_argument("--file", "-f", required=True)
    p_search.add_argument("--query", "-q", required=True)
    p_search.add_argument("--limit", "-l", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    p_fields = sub.add_parser("fields", help="Liste des champs avec exemple")
    p_fields.add_argument("--file", "-f", required=True)
    p_fields.set_defaults(func=cmd_fields)

    p_dedup = sub.add_parser("dedup", help="Déduplication par hash")
    p_dedup.add_argument("--file", "-f", required=True)
    p_dedup.add_argument("--output", "-o", required=True)
    p_dedup.set_defaults(func=cmd_dedup)

    p_filter = sub.add_parser("filter", help="Filtre par lignes de code")
    p_filter.add_argument("--file", "-f", required=True)
    p_filter.add_argument("--min-lines", type=int, default=5)
    p_filter.add_argument("--max-lines", type=int, default=None)
    p_filter.add_argument("--output", "-o", required=True)
    p_filter.set_defaults(func=cmd_filter)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Outils pour datasets JSONL")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()