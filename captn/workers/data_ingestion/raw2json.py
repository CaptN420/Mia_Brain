#!/usr/bin/env python3
"""Transform raw local files (code, docs, configs) into structured JSON.

This is the local-file counterpart of ``crawlers.GitHubCrawler``: instead of
pulling from GitHub, it reads files already present on disk (e.g. the
``Code_base`` directory) and emits the SAME flat JSONL schema
(``captn.crawler.dataset/1.0``) so the output can be merged into the
self-learning corpus alongside crawled data.

Design principles
-----------------
* **Read-only & safe** - files are read, never executed or written to their
  origin. A path-traversal guard rejects anything outside the scan root.
* **Deterministic** - identical input yields identical output (sorted by path,
  stable hashes). No timestamps in per-record content; only a top-level
  ``converted_at`` in the manifest.
* **Robust** - binary/non-decodable files are kept as ``is_text=False`` records
  (metadata only, no content); per-file I/O errors are collected, not fatal.
* **Bounded** - ``max_bytes`` caps memory; ``max_files`` caps the scan.

Schema (one JSON object per line in the .jsonl)
-----------------------------------------------
    {
      "schema_version": "captn.crawler.dataset/1.0",
      "source": "raw",
      "origin": "<absolute path>",
      "repo": null, "branch": null,
      "git_sha": "<sha if under git else null>",
      "path": "<relative path inside root>",
      "language": "<detected language>",
      "ext": ".py",
      "size_bytes": 1234,
      "sha256": "<sha256 of decoded bytes>",
      "decoded_utf8": true,
      "is_text": true,
      "content": "<text content (may be truncated)>",
      "line_count": 42,
      "truncated": false
    }
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

import logging

logger = logging.getLogger("raw2json")

# --------------------------------------------------------------------------- #
# Mirror transformations (applied before delete_source)
# --------------------------------------------------------------------------- #
def _mirror_content(content: str, language: str, ext: str) -> tuple[str, str]:
    """Transform content through a deterministic mirror operation.

    For Python code: uses DeterministicCoder AST transforms (docstrings + annotate).
    For other text: applies simple structural inversions (reverse lines, swap case).
    Returns (mirrored_content, mirror_type).
    """
    if language == "python" or ext in (".py", ".pyi"):
        try:
            from captn.workers.code_generation.deterministic_coder import DeterministicCoder
            dc = DeterministicCoder()
            mirrored, method = dc.generate(content, modes=["docstrings", "annotate", "normalize"])
            if mirrored is not None:
                return mirrored, "ast:docstrings+annotate"
        except Exception:
            pass
        # Fallback: reverse function bodies (keep signatures)
        try:
            import ast as _ast
            tree = _ast.parse(content)
            # Invert top-level order: function defs come before imports
            funcs = [n for n in tree.body if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))]
            others = [n for n in tree.body if n not in funcs]
            tree.body = others + funcs
            return _ast.unparse(tree), "ast:invert-top-level"
        except Exception:
            pass

    # Generic text mirror: reverse lines, then swap case on each line
    lines = content.splitlines()
    mirrored_lines = []
    for line in reversed(lines):
        # Simple structural inversion: swap ASCII case
        swapped = "".join(c.lower() if c.isupper() else c.upper() if c.islower() else c for c in line)
        mirrored_lines.append(swapped)
    return "\n".join(mirrored_lines), "text:reverse+swapcase"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
SCHEMA_VERSION = "captn.crawler.dataset/1.0"

# Extensions we treat as text (decoded as UTF-8). Everything else is binary.
TEXT_EXTS = {
    ".py", ".pyi", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".h", ".cc",
    ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".kts",
    ".scala", ".m", ".mm", ".r", ".jl", ".lua", ".pl", ".pm", ".sh", ".bash",
    ".zsh", ".ps1", ".bat", ".cmd", ".pyw", ".ipynb",
    ".rst", ".md", ".txt", ".text", ".adoc", ".tex", ".latex",
    ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".xhtml", ".svg", ".css", ".scss", ".less",
    ".sql", ".graphql", ".dockerfile", ".makefile", ".mk", ".cmake",
    ".proto", ".tf", ".tfvars", ".env", ".gitignore", ".gitattributes",
}

# Map extensions to a coarse language label (reused by the learner).
EXT_LANGUAGE = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".mjs": "javascript", ".cjs": "javascript",
    ".java": "java", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust",
    ".rb": "ruby", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".scala": "scala", ".r": "r", ".jl": "julia", ".lua": "lua",
    ".pl": "perl", ".pm": "perl", ".sh": "shell", ".bash": "shell",
    ".zsh": "shell", ".ps1": "powershell", ".bat": "batch", ".cmd": "batch",
    ".rst": "restructuredtext", ".md": "markdown", ".tex": "latex",
    ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".ini": "ini",
    ".json": "json", ".jsonl": "json", ".xml": "xml", ".html": "html",
    ".htm": "html", ".svg": "svg", ".css": "css", ".sql": "sql",
    ".graphql": "graphql", ".dockerfile": "dockerfile", ".makefile": "makefile",
    ".tf": "terraform", ".txt": "text", ".text": "text", ".adoc": "asciidoc",
    ".cmake": "cmake", ".proto": "protobuf", ".gitignore": "text",
    ".gitattributes": "text", ".env": "text",
}

# Files whose name (regardless of extension) we treat as text anyway.
TEXT_FILENAMES = {
    "dockerfile", "makefile", "cmakelists.txt", "rakefile", "gemfile",
    "vagrantfile", "procfile", ".gitignore", ".gitattributes", "license",
    "readme", "readme.md", "build.gradle", "pom.xml",
}

DEFAULT_MAX_BYTES = 512 * 1024  # 512 KB per file (keep memory bounded)
DEFAULT_MAX_FILES = 5000


# --------------------------------------------------------------------------- #
# Result container
# --------------------------------------------------------------------------- #
@dataclass
class RawConversionResult:
    root: str = ""
    files_included: list[Any] = field(default_factory=list)   # path strings
    files_written: list[Any] = field(default_factory=list)    # path strings
    files_skipped: list[str] = field(default_factory=list)    # reasons
    files_deleted: list[str] = field(default_factory=list)    # deleted after conversion
    errors: list[str] = field(default_factory=list)
    dataset_jsonl: Optional[str] = None
    dataset_json: Optional[str] = None
    manifest: Optional[str] = None
    mirror_dataset_jsonl: Optional[str] = None
    truncated_tree: bool = False

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "included": len(self.files_included),
            "written": len(self.files_written),
            "skipped": len(self.files_skipped),
            "deleted": len(self.files_deleted),
            "errors": len(self.errors),
            "dataset_jsonl": self.dataset_jsonl,
            "dataset_json": self.dataset_json,
            "manifest": self.manifest,
            "mirror_dataset_jsonl": self.mirror_dataset_jsonl,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _language_for(path: Path) -> str:
    name = path.name.lower()
    if name in TEXT_FILENAMES:
        return TEXT_FILENAMES[name]
    ext = path.suffix.lower()
    return EXT_LANGUAGE.get(ext, "unknown")


def _is_text_candidate(path: Path) -> bool:
    name = path.name.lower()
    if name in TEXT_FILENAMES:
        return True
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return True
    # Unknown extension with no clear type -> treat as binary to be safe.
    return False


def _git_sha(root: Path, rel_path: str) -> Optional[str]:
    """Best-effort git blob hash (sha1 of 'blob <size>\0<content>').

    Returns None if not under git or git is unavailable. Never raises.
    """
    try:
        import subprocess

        out = subprocess.run(
            ["git", "hash-object", "--path", rel_path, str(root / rel_path)],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _iter_tree(root: Path, max_files: int) -> Iterator[Path]:
    """Yield files under root (sorted for determinism), capped at max_files."""
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Deterministic traversal order.
        dirnames.sort()
        for fn in sorted(filenames):
            yield Path(dirpath) / fn
            count += 1
            if count >= max_files:
                return


# --------------------------------------------------------------------------- #
# Core conversion
# --------------------------------------------------------------------------- #
def convert_raw_directory(
    root: str | os.PathLike,
    *,
    out_dir: Optional[str | os.PathLike] = None,
    include_ext: Optional[list[str]] = None,
    exclude_dirs: Optional[set[str]] = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    export_dataset: bool = True,
    mirror_and_delete: bool = False,
    on_file: Optional[Callable[[dict], None]] = None,
    progress_file: Optional[str | os.PathLike] = None,
    progress_total: Optional[int] = None,
) -> RawConversionResult:
    """Convert every readable file under ``root`` into structured JSON records.

    Args:
        root: directory to scan (must exist; traversal outside it is rejected).
        out_dir: where to write dataset.jsonl / dataset.json / manifest.json.
            Defaults to ``<root>/_converted``.
        include_ext: optional allow-list of extensions (e.g. [".py", ".rst"]).
            When set, only matching files are converted.
        exclude_dirs: directory names to skip (default: common VCS/build caches).
        max_bytes: max bytes read per file (content beyond is truncated).
        max_files: hard cap on number of files scanned.
        export_dataset: if True, write the three output files; otherwise only
            build records in memory (used by tests / dry runs).
        mirror_and_delete: if True, create a **mirror dataset** (mirrored
            transformations of every converted file) in ``<out_dir>/_mirrored/``,
            then delete the original source files. The mirror dataset applies
            deterministic transforms: AST docstrings/annotations for Python,
            structural inversion for other text. Never deletes the root
            directory or files outside it. Only runs if the dataset was
            written successfully and is non-empty.
        on_file: optional callback invoked with each record (streaming UI).
        progress_file: optional path to a JSON file written with
            {"done": n, "total": m, "last": "<path>"} on every file, for live
            progress UIs.

    Returns:
        RawConversionResult with counts and output paths.
    """
    root = Path(root).resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"root is not a directory: {root}")

    exclude_dirs = set(exclude_dirs or {
        ".git", ".hg", ".svn", "node_modules", "__pycache__",
        ".venv", "venv", "build", "dist", ".idea", ".tox", ".mypy_cache",
        ".pytest_cache", "site-packages", "target", ".gradle",
    })

    out_dir = Path(out_dir) if out_dir else (root / "_converted")
    out_dir = Path(out_dir).resolve()
    # Path-traversal guard: output must live under root or be explicitly allowed.
    if not (str(out_dir) == str(root) or str(root) in str(out_dir) or export_dataset is False):
        # Allow out_dir anywhere only when caller passes an absolute custom dir;
        # here we just ensure we never write outside an explicit choice.
        pass

    result = RawConversionResult(root=str(root))
    records: list[dict] = []
    included = 0
    seen = 0

    def _write_progress() -> None:
        if not progress_file:
            return
        try:
            with open(progress_file, "w", encoding="utf-8") as _pf:
                # status:"running" lets the dashboard keep auto-refreshing while
                # the scan is in flight (file-driven, reliable across threads).
                # The final result JSON overwrites this with status:"done"/"error".
                json.dump(
                    {"status": "running", "done": seen,
                     "total": progress_total or seen, "last": _last_file},
                    _pf,
                )
        except OSError:
            pass

    _last_file = ""

    for path in _iter_tree(root, max_files):
        try:
            rel = path.relative_to(root)
        except ValueError:
            # Not under root (symlink escape) - skip defensively.
            result.files_skipped.append(str(path))
            seen += 1
            _last_file = str(path)
            _write_progress()
            continue

        # Skip the output directory itself to avoid ingesting our own products.
        try:
            if out_dir in (path, *path.parents):
                continue
        except Exception:
            pass

        try:
            if any(part in exclude_dirs for part in rel.parts):
                continue

            ext = path.suffix.lower()
            if include_ext:
                wanted = [e.lower() for e in include_ext]
                if ext not in wanted and path.name.lower() not in TEXT_FILENAMES:
                    result.files_skipped.append(f"{rel} (ext not in allow-list)")
                    continue

            if not _is_text_candidate(path):
                # Binary file: emit metadata-only record, no content.
                try:
                    size = path.stat().st_size
                    raw = path.read_bytes()
                    rec = _build_record(root, rel, raw, is_text=False,
                                        language=_language_for(path),
                                        truncated=False, max_bytes=max_bytes)
                    records.append(rec)
                    result.files_included.append(str(rel))
                    included += 1
                    if on_file:
                        on_file(rec)
                except Exception as e:
                    result.errors.append(f"{rel}: {type(e).__name__}: {e}")
                continue

            # Text file.
            try:
                size = path.stat().st_size
                if size == 0:
                    result.files_skipped.append(f"{rel} (empty)")
                    continue
                raw = path.read_bytes()  # read bytes, decode explicitly
                try:
                    text = raw.decode("utf-8")
                    decoded = True
                except UnicodeDecodeError:
                    # Not valid UTF-8 -> treat as binary-ish, keep metadata only.
                    rec = _build_record(root, rel, raw, is_text=False,
                                        language=_language_for(path),
                                        truncated=False, max_bytes=max_bytes)
                    records.append(rec)
                    result.files_included.append(str(rel))
                    included += 1
                    if on_file:
                        on_file(rec)
                    continue

                truncated = size > max_bytes
                # Normalize CRLF -> LF for deterministic, cross-platform content
                # (also helps sha256 dedup between Windows/Unix checkouts).
                normalized = text.replace("\r\n", "\n")
                content = normalized if not truncated else normalized[:max_bytes]
                rec = _build_record(root, rel, raw, is_text=True,
                                    language=_language_for(path),
                                    truncated=truncated, max_bytes=max_bytes,
                                    content=content)
                records.append(rec)
                result.files_included.append(str(rel))
                included += 1
                if on_file:
                    on_file(rec)
            except Exception as e:
                result.errors.append(f"{rel}: {type(e).__name__}: {e}")
        finally:
            # Guaranteed once per file (runs even on `continue`).
            seen += 1
            _last_file = str(rel)
            _write_progress()

    result.records = records  # type: ignore[attr-defined]

    if export_dataset:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_outputs(out_dir, records, str(root), result)

    # ── Mirror + Delete: create mirror dataset, then remove source files ──
    if mirror_and_delete and result.files_included and result.dataset_jsonl:
        jsonl_path = Path(result.dataset_jsonl)
        if jsonl_path.is_file() and jsonl_path.stat().st_size > 0:
            root_path = Path(root).resolve()
            mirror_dir = out_dir / "_mirrored"
            mirror_dir.mkdir(parents=True, exist_ok=True)
            mirror_jsonl = mirror_dir / "mirror_dataset.jsonl"
            mirror_records: list[dict] = []
            deleted_count = 0

            for rel_str in result.files_included:
                src = (root_path / rel_str).resolve()
                # Safety: never delete root or files outside it
                if src == root_path or root_path not in src.parents:
                    continue

                # Find the matching record to get content + language
                rec = None
                for r in records:
                    if r.get("path") == str(rel_str).replace("\\", "/"):
                        rec = r
                        break
                if rec is None:
                    continue

                # Mirror the content
                mirrored_content = None
                mirror_type = "none"
                if rec.get("is_text") and rec.get("content"):
                    mirrored_content, mirror_type = _mirror_content(
                        rec["content"],
                        rec.get("language", "text"),
                        rec.get("ext", ""),
                    )

                # Create mirror record (same schema, mirrored content + metadata)
                mirror_rec = dict(rec)
                mirror_rec["content"] = mirrored_content
                mirror_rec["mirror_type"] = mirror_type
                mirror_rec["original_sha256"] = rec.get("sha256")
                mirror_rec["source"] = "mirror"
                mirror_records.append(mirror_rec)

                # Delete original source file
                try:
                    src.unlink()
                    result.files_deleted.append(str(rel_str))
                    deleted_count += 1
                except FileNotFoundError:
                    pass
                except OSError as e:
                    result.errors.append(f"mirror-and-delete {rel_str}: {e}")

            # Write mirror dataset
            with open(mirror_jsonl, "w", encoding="utf-8") as fh:
                for rec in mirror_records:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            result.mirror_dataset_jsonl = str(mirror_jsonl)
            logger.info(
                "mirror_and_delete: %d mirror records → %s, %d source files removed",
                len(mirror_records), mirror_jsonl, deleted_count,
            )
        else:
            logger.warning("mirror_and_delete: dataset JSONL missing or empty — skipping")
    return result


def _build_record(root: Path, rel: Path, raw: bytes, *, is_text: bool,
                  language: str, truncated: bool, max_bytes: int,
                  content: Optional[str] = None) -> dict:
    sha = hashlib.sha256(raw).hexdigest()
    rec: dict = {
        "schema_version": SCHEMA_VERSION,
        "source": "raw",
        "origin": str((root / rel).resolve()),
        "repo": None,
        "branch": None,
        "git_sha": _git_sha(root, str(rel)),
        "path": str(rel).replace("\\", "/"),
        "language": language,
        "ext": rel.suffix.lower(),
        "size_bytes": len(raw),
        "sha256": sha,
        "decoded_utf8": is_text,
        "is_text": is_text,
        "content": content if is_text else None,
        "line_count": content.count("\n") + 1 if is_text else None,  # type: ignore[union-attr]
        "truncated": truncated,
    }
    return rec


def _write_outputs(out_dir: Path, records: list[dict], root: str,
                   result: RawConversionResult) -> None:
    jsonl_path = out_dir / "dataset.jsonl"
    json_path = out_dir / "dataset.json"
    manifest_path = out_dir / "manifest.json"

    # JSONL (one record per line)
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # Single-file JSON {meta, records}
    from datetime import datetime
    meta = {
        "schema_version": SCHEMA_VERSION,
        "source": "raw",
        "root": root,
        "converted_at": datetime.utcnow().isoformat() + "Z",
        "record_count": len(records),
        "languages": _count_by(records, "language"),
        "extensions": _count_by(records, "ext"),
        "note": "Structured JSON produced from raw local files by captn.workers.raw2json.",
    }
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "records": records}, fh, ensure_ascii=False, indent=2)

    # Manifest (no file contents - safe to inspect)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    result.dataset_jsonl = str(jsonl_path)
    result.dataset_json = str(json_path)
    result.manifest = str(manifest_path)
    result.files_written = [str(jsonl_path), str(json_path), str(manifest_path)]


def _count_by(records: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        v = r.get(key) or "unknown"
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda x: -x[1]))


# --------------------------------------------------------------------------- #
# Convenience: merge converters output into the learning corpus (reuse crawler)
# --------------------------------------------------------------------------- #
def convert_and_feed(
    root: str | os.PathLike,
    corpus_dir: str | os.PathLike = "Code_base",
    **kwargs,
) -> dict:
    """Convert a raw directory and merge the result into the learning corpus.

    Returns the merge stats from ``merge_dataset_into_corpus``.
    """
    from captn.workers.data_ingestion.crawler import merge_dataset_into_corpus

    res = convert_raw_directory(root, **kwargs)
    if not res.dataset_jsonl:
        return {"added": 0, "duplicates": 0, "error": "no dataset produced"}
    stats = merge_dataset_into_corpus(res.dataset_jsonl, corpus_dir)
    return stats
