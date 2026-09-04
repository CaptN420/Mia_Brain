#!/usr/bin/env python3
"""
changelog.py — Générateur de notes de version depuis git.

Parse git log → catégorise par conventional commits → Markdown/JSON.

Usage:
    python -m tools.changelog
    python -m tools.changelog --from v0.1.0 --to HEAD
    python -m tools.changelog --format json
    python -m tools.changelog --output CHANGELOG.md
    python -m tools.changelog --append  # ajoute à CHANGELOG.md existant
"""

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── Conventional Commit patterns ───

COMMIT_TYPES = {
    "feat": {"emoji": "✨", "title": "Features", "order": 1},
    "fix": {"emoji": "🐛", "title": "Bug Fixes", "order": 2},
    "docs": {"emoji": "📝", "title": "Documentation", "order": 3},
    "refactor": {"emoji": "♻️", "title": "Refactoring", "order": 4},
    "perf": {"emoji": "⚡", "title": "Performance", "order": 5},
    "test": {"emoji": "🧪", "title": "Tests", "order": 6},
    "style": {"emoji": "💄", "title": "Style", "order": 7},
    "chore": {"emoji": "🔧", "title": "Chores", "order": 8},
    "ci": {"emoji": "🔁", "title": "CI/CD", "order": 9},
    "build": {"emoji": "🏗️", "title": "Build", "order": 10},
    "revert": {"emoji": "⏪", "title": "Reverts", "order": 11},
    "security": {"emoji": "🔒", "title": "Security", "order": 0},
    "breaking": {"emoji": "💥", "title": "Breaking Changes", "order": -1},
}

# Patterns
_COMMIT_PATTERN = re.compile(
    r'^(?P<type>\w+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:\s*(?P<description>.+)',
    re.MULTILINE
)
_HASH_PATTERN = re.compile(r'^[0-9a-f]{7,40}')
_BODY_PATTERN = re.compile(r'^[\s]{4}(.*)')


@dataclass
class CommitEntry:
    hash: str
    type: str
    scope: Optional[str]
    description: str
    body: Optional[str] = None
    breaking: bool = False
    author: Optional[str] = None
    date: Optional[str] = None

    def is_important(self) -> bool:
        return self.type in ("feat", "fix", "security", "breaking") or self.breaking

    def to_markdown(self) -> str:
        type_info = COMMIT_TYPES.get(self.type, {"emoji": "🔹", "title": self.type})
        emoji = type_info["emoji"]
        scope_str = f"**{self.scope}:** " if self.scope else ""
        breaking_mark = "💥 " if (self.breaking or self.type == "breaking") else ""
        return f"- {emoji} {breaking_mark}{scope_str}{self.description} ({self.hash[:7]})"


@dataclass
class Changelog:
    from_ref: Optional[str]
    to_ref: str
    entries: List[CommitEntry] = field(default_factory=list)
    version: Optional[str] = None
    date: str = ""

    def grouped(self) -> Dict[str, List[CommitEntry]]:
        groups: Dict[str, List[CommitEntry]] = defaultdict(list)
        for entry in self.entries:
            key = "breaking" if entry.breaking or entry.type == "breaking" else entry.type
            groups[key].append(entry)
        return dict(groups)

    def to_markdown(self) -> str:
        """Génère le changelog en Markdown."""
        lines = []
        version_str = self.version or self.to_ref
        date_str = self.date or datetime.now().strftime("%Y-%m-%d")
        lines.append(f"## {version_str} ({date_str})\n")

        groups = self.grouped()
        sorted_types = sorted(
            groups.keys(),
            key=lambda t: COMMIT_TYPES.get(t, {"order": 99})["order"]
        )

        for t in sorted_types:
            type_info = COMMIT_TYPES.get(t, {"emoji": "🔹", "title": t.capitalize()})
            lines.append(f"### {type_info['emoji']} {type_info['title']}\n")
            for entry in sorted(groups[t], key=lambda e: (e.scope or "", e.description)):
                lines.append(entry.to_markdown())
            lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        data = {
            "version": self.version or self.to_ref,
            "date": self.date or datetime.now().strftime("%Y-%m-%d"),
            "from": self.from_ref,
            "to": self.to_ref,
            "total_commits": len(self.entries),
            "groups": {},
        }
        for t, entries in self.grouped().items():
            data["groups"][t] = [
                {
                    "hash": e.hash[:7],
                    "scope": e.scope,
                    "description": e.description,
                    "breaking": e.breaking or e.type == "breaking",
                }
                for e in entries
            ]
        return json.dumps(data, indent=2, ensure_ascii=False)


def _run_git(*args: str, cwd: str = ".") -> str:
    """Exécute une commande git et retourne stdout."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            capture_output=True, text=True, check=True, cwd=cwd,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # Git peut échouer si pas de commits
        if "fatal" in e.stderr.lower():
            return ""
        raise
    except FileNotFoundError:
        print("Erreur: git n'est pas installé ou n'est pas dans le PATH", file=sys.stderr)
        sys.exit(1)


def _parse_commit_log(log_entry: str) -> Optional[CommitEntry]:
    """Parse une entrée de git log formatée."""
    lines = log_entry.strip().split("\n")
    if not lines:
        return None

    # First line: hash and subject
    first = lines[0]
    parts = first.split(" ", 1)
    if len(parts) < 2:
        return None

    commit_hash = parts[0]
    subject = parts[1]

    # Parse conventional commit
    match = _COMMIT_PATTERN.match(subject)
    if not match:
        m = _HASH_PATTERN.match(subject)
        if m:
            # Maybe a merge commit or raw hash
            return None
        return None

    type_ = match.group("type")
    scope = match.group("scope")
    breaking = bool(match.group("breaking"))
    description = match.group("description").strip()

    # Body (rest of lines)
    body_lines = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped:
            body_lines.append(stripped)

    body = "\n".join(body_lines) if body_lines else None

    # Detect breaking changes from body
    if "BREAKING CHANGE" in (body or ""):
        breaking = True

    return CommitEntry(
        hash=commit_hash,
        type=type_ or "chore",
        scope=scope,
        description=description,
        body=body,
        breaking=breaking,
    )


def generate(from_ref: Optional[str] = None, to_ref: str = "HEAD",
             cwd: str = ".") -> Changelog:
    """Génère un changelog entre deux refs git."""

    # Récupérer le tag de version si to_ref est HEAD
    version = None
    if to_ref == "HEAD":
        try:
            version = _run_git("describe", "--tags", "--abbrev=0", cwd=cwd)
            if not version:
                version = None
        except Exception:
            version = None

    # Get log range
    if from_ref:
        log_range = f"{from_ref}..{to_ref}"
    else:
        # Si pas de from_ref, prendre tout
        log_range = to_ref

    # Git log format
    raw_log = _run_git("log", log_range, "--oneline", "--format=%H %s", cwd=cwd)
    if not raw_log:
        return Changelog(from_ref=from_ref, to_ref=to_ref, version=version)

    entries = []
    for line in raw_log.split("\n"):
        entry = _parse_commit_log(line)
        if entry:
            entries.append(entry)

    # Date
    date = datetime.now().strftime("%Y-%m-%d")

    return Changelog(
        from_ref=from_ref,
        to_ref=to_ref,
        entries=entries,
        version=version,
        date=date,
    )


def append_to_file(changelog: Changelog, filepath: str = "CHANGELOG.md") -> None:
    """Ajoute une nouvelle version au début du CHANGELOG.md existant."""
    path = Path(filepath)
    new_section = changelog.to_markdown()

    if path.exists():
        content = path.read_text(encoding="utf-8")
        # Find the first ## in the existing content
        idx = content.find("\n## ")
        if idx != -1:
            header = content[:idx]
            rest = content[idx:]
            path.write_text(f"{header}\n\n{new_section}\n{rest}", encoding="utf-8")
        else:
            path.write_text(f"{new_section}\n{content}", encoding="utf-8")
    else:
        path.write_text(f"# Changelog\n\n{new_section}", encoding="utf-8")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Générateur de notes de version depuis git"
    )
    parser.add_argument("--from", "-f", dest="from_ref", default=None,
                        help="Tag/commit de départ (défaut: premier commit)")
    parser.add_argument("--to", "-t", default="HEAD",
                        help="Tag/commit de fin (défaut: HEAD)")
    parser.add_argument("--format", "-F", choices=["markdown", "json"],
                        default="markdown", help="Format de sortie")
    parser.add_argument("--output", "-o", help="Fichier de sortie")
    parser.add_argument("--append", "-a", action="store_true",
                        help="Ajouter au début du CHANGELOG.md existant")
    parser.add_argument("--dir", "-d", default=".",
                        help="Répertoire du projet git (défaut: .)")
    args = parser.parse_args()

    changelog = generate(from_ref=args.from_ref, to_ref=args.to, cwd=args.dir)

    if not changelog.entries:
        print("Aucun commit trouvé dans cette plage.")
        sys.exit(0)

    if args.append:
        append_to_file(changelog, args.output or "CHANGELOG.md")
        print(f"Changelog ajouté à {args.output or 'CHANGELOG.md'}", file=sys.stderr)
    elif args.output:
        if args.format == "json":
            Path(args.output).write_text(changelog.to_json(), encoding="utf-8")
        else:
            Path(args.output).write_text(changelog.to_markdown(), encoding="utf-8")
        print(f"Changelog écrit dans {args.output}", file=sys.stderr)
    else:
        if args.format == "json":
            print(changelog.to_json())
        else:
            print(changelog.to_markdown())


def register_cli(subparsers):
    """Register changelog as a CLI subcommand."""
    p = subparsers.add_parser("changelog", help="Notes de version depuis git")
    p.add_argument("--from", "-f", dest="from_ref", default=None)
    p.add_argument("--to", default="HEAD")
    p.add_argument("--format", "-F", choices=["markdown", "json"], default="markdown")
    p.add_argument("--output", "-o")
    p.add_argument("--append", "-a", action="store_true")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()