#!/usr/bin/env python3
"""corpus_tools — Recherche, stats, et extraction de patrons depuis Code_base/.

Usage:
    python tools/corpus_tools.py search --query "thread safe" --dir Code_base
    python tools/corpus_tools.py search --query "async def" --ext .py --limit 20
    python tools/corpus_tools.py stats --dir Code_base
    python tools/corpus_tools.py patterns --min-frequency 3 --dir Code_base
    python tools/corpus_tools.py list-files --dir Code_base
    python tools/corpus_tools.py sample --dir Code_base --n 5

Déterministe, zéro LLM (grep + regex + compteurs).
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set


EXCLUDED_DIRS = {".git", "__pycache__", "venv", ".venv", "env",
                 "node_modules", "migrations", "session", "archive",
                 "backups", "_converted", "generated", "mirror",
                 "dist", "build", ".pytest_cache", ".ci"}


def _walk(dir_path: str, ext: str = ".py") -> List[str]:
    """Walk a directory and return matching file paths."""
    files = []
    for root, dirs, fnames in os.walk(dir_path):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for fn in fnames:
            if fn.endswith(ext):
                files.append(os.path.join(root, fn))
    return files


# ═══════════════════════════════════════════════════════════════
# 1. SEARCH — full-text regex
# ═══════════════════════════════════════════════════════════════

def cmd_search(args):
    query = args.query
    pattern = re.compile(query, re.I | re.MULTILINE)
    files = _walk(args.dir, args.ext)
    matches = []

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        for m in pattern.finditer(content):
            line_start = content.rfind("\n", 0, m.start()) + 1
            line_no = content[:line_start].count("\n") + 1
            context_start = max(0, m.start() - 60)
            context_end = min(len(content), m.end() + 60)
            snippet = content[context_start:context_end].replace("\n", " ")
            rel = os.path.relpath(fp, args.dir)
            matches.append({
                "file": rel,
                "line": line_no,
                "snippet": snippet.strip(),
            })

    matches = matches[: args.limit]
    print(f"🔍 '{query}' — {len(matches)} résultats")
    print()
    for m in matches:
        print(f"  📄 {m['file']}:{m['line']}")
        print(f"     …{m['snippet']}…")
        print()


# ═══════════════════════════════════════════════════════════════
# 2. STATS
# ═══════════════════════════════════════════════════════════════

def cmd_stats(args):
    all_files = []
    for ext in (".py", ".rst", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".sh", ".bat"):
        all_files.extend(_walk(args.dir, ext))

    if not all_files:
        print(f"Aucun fichier trouvé dans {args.dir}")
        return

    # Group by extension
    ext_counts = Counter(os.path.splitext(f)[1] for f in all_files)

    total_size = sum(os.path.getsize(f) for f in all_files)
    total_lines = 0
    py_files = [f for f in all_files if f.endswith(".py")]
    py_lines = 0
    py_classes = 0
    py_functions = 0
    py_imports: Counter[str] = Counter()
    key_patterns: Counter[str] = Counter()

    # Analyse Python files for deeper stats
    import re as _re
    for fp in py_files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        lines = content.splitlines()
        py_lines += len(lines)

        py_classes += len(_re.findall(r"^\s*class\s+\w+", content, _re.MULTILINE))
        py_functions += len(_re.findall(r"^\s*(?:async\s+)?def\s+\w+", content, _re.MULTILINE))

        # Top-level imports (rough)
        for imp in _re.finditer(r"^(?:from|import)\s+(\w+)", content, _re.MULTILINE):
            py_imports[imp.group(1)] += 1

        # Key patterns
        if _re.search(r"class\s+\w+Worker", content):
            key_patterns["Worker"] += 1
        if _re.search(r"class\s+\w+Thinker", content):
            key_patterns["Thinker"] += 1
        if _re.search(r"class\s+\w+Orchestrator", content):
            key_patterns["Orchestrator"] += 1
        if _re.search(r"asyncio|async def|await\s", content):
            key_patterns["asyncio"] += 1
        if _re.search(r"(?:def\s+test_|class\s+\w*Test)", content):
            key_patterns["tests"] += 1

    total_lines = sum(
        len(open(f, encoding="utf-8", errors="ignore").read().splitlines())
        for f in all_files
    )

    result = {
        "directory": args.dir,
        "total_files": len(all_files),
        "total_size_kb": round(total_size / 1024, 1),
        "total_lines": total_lines,
        "by_extension": dict(ext_counts.most_common(15)),
        "python": {
            "files": len(py_files),
            "lines": py_lines,
            "classes": py_classes,
            "functions": py_functions,
            "top_imports": py_imports.most_common(20),
        },
        "patterns_found": dict(key_patterns.most_common(10)),
    }

    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# 3. PATTERNS — extract recurring code patterns
# ═══════════════════════════════════════════════════════════════

# Pattern templates
PATTERN_REGEXES = {
    "decorator": re.compile(r"@(\w+(?:\.\w+)*)\s*\n\s*(?:async\s+)?def\s+\w+"),
    "class_decorator": re.compile(r"@(\w+(?:\.\w+)*)\s*\n\s*class\s+\w+"),
    "context_manager": re.compile(r"(?:async\s+)?with\s+\w+(?:\.\w+)*\s*(?:as\s+\w+)?\s*:"),
    "list_comprehension": re.compile(r"\[.*for\s+\w+\s+in\s+\w+(?:\s+if\s+.*)?\]"),
    "dict_comprehension": re.compile(r"\{.*:\s*.*\s+for\s+\w+\s+in\s+\w+(?:\s+if\s+.*)?\}"),
    "try_except": re.compile(r"try\s*:\s*\n\s+.*\n\s*except\s"),
    "type_hint_function": re.compile(r"(?:async\s+)?def\s+\w+\(.*\)\s*->\s*\w+"),
    "dataclass": re.compile(r"@dataclass"),
    "enum": re.compile(r"class\s+\w+\(.*Enum.*\)"),
    "pytest_fixture": re.compile(r"@pytest\.fixture"),
}


def cmd_patterns(args):
    files = _walk(args.dir, ".py")
    pattern_counter: Counter[str] = Counter()
    examples: Dict[str, List[str]] = {}
    for name, regex in PATTERN_REGEXES.items():
        examples[name] = []

    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue
        for name, regex in PATTERN_REGEXES.items():
            matches = regex.findall(content) if regex.groups == 0 else regex.finditer(content)
            if isinstance(matches, list):
                count = len(matches)
            else:
                count = sum(1 for _ in matches)
            if count > 0:
                pattern_counter[name] += count
            # Collect first 2 examples per pattern
            if len(examples[name]) < 2 and count > 0:
                for m in regex.finditer(content):
                    snippet = m.group()[:120].replace("\n", " ↵ ")
                    examples[name].append(snippet)
                    if len(examples[name]) >= 2:
                        break

    # Filter by min frequency
    result = []
    for name, count in pattern_counter.most_common():
        if count >= args.min_frequency:
            ex = examples.get(name, [])
            entry = {"pattern": name, "occurrences": count}
            if ex:
                entry["examples"] = ex
            result.append(entry)

    if args.output:
        import json
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"✅ Patterns écrits dans {args.output}")
    else:
        print(f"📐 PATTERNS ({sum(p['occurrences'] for p in result)} total)")
        print()
        for p in result:
            print(f"  [{p['occurrences']:4d}x] {p['pattern']}")
            for ex in p.get("examples", []):
                print(f"        {ex}")
            print()


# ═══════════════════════════════════════════════════════════════
# 4. LIST-FILES
# ═══════════════════════════════════════════════════════════════

def cmd_list_files(args):
    files = _walk(args.dir, args.ext)
    total = len(files)
    total_size = sum(os.path.getsize(f) for f in files)
    print(f"📂 {args.dir}: {total} fichiers (extension: {args.ext})")
    print(f"   Taille totale: {total_size / 1024:.1f} KB")
    print()
    for fp in files:
        rel = os.path.relpath(fp, args.dir)
        size = os.path.getsize(fp)
        print(f"  {rel:60s} {size:>7,} B")


# ═══════════════════════════════════════════════════════════════
# 5. SAMPLE
# ═══════════════════════════════════════════════════════════════

def cmd_sample(args):
    files = _walk(args.dir, args.ext)
    if not files:
        print(f"Aucun fichier trouvé dans {args.dir}")
        return

    sample = files[: args.n]
    print(f"📄 Échantillon ({len(sample)} fichiers):")
    print()
    for fp in sample:
        rel = os.path.relpath(fp, args.dir)
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            lines = content.splitlines()
        except Exception:
            lines = []

        print(f"━━━ {rel} ({len(lines)} lignes, {os.path.getsize(fp):,} B) ━━━")
        # Show first 15 lines
        for l in lines[:15]:
            print(f"  {l}")
        if len(lines) > 15:
            print(f"  … ({len(lines) - 15} lignes supplémentaires)")
        print()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def register_cli(subparsers):
    """Register corpus as a CLI subcommand."""
    p = subparsers.add_parser("corpus", help="Explorer le corpus Code_base")
    p.add_argument("--dir", "-d", default="Code_base", help="Répertoire du corpus (défaut: Code_base)")
    sub = p.add_subparsers(dest="command", required=True)

    p_s = sub.add_parser("search", help="Recherche full-text par regex")
    p_s.add_argument("--query", "-q", required=True)
    p_s.add_argument("--ext", default=".py")
    p_s.add_argument("--limit", "-l", type=int, default=30)
    p_s.set_defaults(func=cmd_search)

    sub.add_parser("stats", help="Statistiques du corpus").set_defaults(func=cmd_stats)

    p_p = sub.add_parser("patterns", help="Extraction de patrons récurrents")
    p_p.add_argument("--min-frequency", "-f", type=int, default=3)
    p_p.add_argument("--output", "-o", help="Fichier JSON de sortie")
    p_p.set_defaults(func=cmd_patterns)

    p_l = sub.add_parser("list-files", help="Lister les fichiers")
    p_l.add_argument("--ext", default="*.py")
    p_l.set_defaults(func=cmd_list_files)

    p_sa = sub.add_parser("sample", help="Échantillon de fichiers")
    p_sa.add_argument("--n", type=int, default=5)
    p_sa.add_argument("--ext", default=".py")
    p_sa.set_defaults(func=cmd_sample)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Outils pour le corpus Code_base")
    parser.add_argument("--dir", "-d", default="Code_base", help="Répertoire du corpus")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()