"""GitHub open-source code crawler for Captn.

This worker pulls the source tree of a public open-source repository from
GitHub and **auto-generates** a local copy of its code on disk. It is the
networked counterpart to the local :class:`ProjectScanner` and feeds the same
``UniversalData`` schema (``source_type="github"``) that the rest of the Captn
pipeline already understands.

Design notes (security-first):
- Only ``github.com`` / ``raw.githubusercontent.com`` hosts are ever contacted.
  Repo specs are strictly parsed; no arbitrary URL is fetched blindly.
- GitHub API rate limits are read from response headers and surfaced as a
  clear :class:`RateLimitError` instead of a confusing 403.
- A token is optional (raises the unauthenticated 60 req/h ceiling to 5000).
  It is read from ``GITHUB_TOKEN`` or an explicit argument, never logged.
- Binary blobs, oversized files, vendored dirs, and depth are bounded by
  configurable, safe-by-default limits to avoid filling the disk or hanging.
- The code is deterministic given the same inputs (no LLM in the crawl path).

Importable without the message bus (``GitHubCrawler`` is a standalone class);
``CrawlerWorker`` is the optional Captn ``Plugin`` wrapper.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("Runtime")

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard dependency
    requests = None  # type: ignore

API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"

# Source code + docs we care about by default. Binary/media types are excluded.
DEFAULT_INCLUDE_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".cc", ".h", ".hpp",
    ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".bash", ".zsh",
    ".ps1", ".sql", ".r", ".jl", ".lua", ".dart", ".vue",
    ".md", ".txt", ".rst", ".adoc",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env.example",
    ".xml", ".html", ".css", ".dockerfile",
}

# Directories we never descend into (vendored / build / VCS noise).
DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", "target", ".idea", ".vscode", ".gradle",
    "vendor", "bin", "obj", ".tox", ".mypy_cache", ".pytest_cache",
}

DEFAULT_MAX_FILES = 400
DEFAULT_MAX_BYTES = 300_000      # per-file ceiling before we skip
DEFAULT_MAX_DEPTH = 14           # path depth (number of '/')
DEFAULT_TIMEOUT = 25             # seconds per HTTP request

_USER_AGENT = "CaptnCrawler/1.0 (+https://github.com/CaptN420/CaptN-BRAIN)"

# SECURITY: token sources are read defensively and NEVER logged. Precedence:
#   1. explicit `token=` argument
#   2. GITHUB_TOKEN env var
#   3. .captn/github_token file (gitignored — see .gitignore)
# `rem.txt` is intentionally NOT a token source (it is the dashboard auth token).
def resolve_github_token(explicit: Optional[str] = None) -> Optional[str]:
    """Return a GitHub token from the safest available source, else None.

    The token is never printed, logged, or embedded in any error message.
    """
    if explicit:
        return explicit.strip() or None
    env_tok = os.environ.get("GITHUB_TOKEN")
    if env_tok:
        return env_tok.strip() or None
    # Gitignored local file (never committed to the repo). Walk UP from this
    # file to the project root (handles arbitrary nesting) and check .captn/.
    _here = os.path.abspath(__file__)
    _candidates = []
    _cur = _here
    for _ in range(8):  # bound the walk
        _cur = os.path.dirname(_cur)
        _candidates.append(os.path.join(_cur, ".captn", "github_token"))
    _candidates.append(os.path.join(os.getcwd(), ".captn", "github_token"))
    for path in _candidates:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    tok = fh.read().strip()
                if tok:
                    return tok
        except OSError:
            continue
    return None

# Extension -> human language label (used in the AI-ready JSON dataset).
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".mjs": "javascript", ".cjs": "javascript",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".cpp": "cpp", ".cc": "cpp", ".h": "c", ".hpp": "cpp",
    ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
    ".scala": "scala", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".sql": "sql", ".r": "r", ".jl": "julia",
    ".lua": "lua", ".dart": "dart", ".vue": "vue",
    ".md": "markdown", ".txt": "text", ".rst": "restructuredtext", ".adoc": "asciidoc",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".xml": "xml", ".html": "html",
    ".css": "css", ".dockerfile": "dockerfile",
}

# Text-like extensions we are willing to embed as decoded 'content' in the dataset.
# Everything else (archives, images, fonts, pdfs) is recorded as metadata only.
_TEXT_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".rb", ".php",
    ".swift", ".scala", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".r", ".jl",
    ".lua", ".dart", ".vue", ".md", ".txt", ".rst", ".adoc", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".xml", ".html", ".css"}

# Files that commonly carry the project's licensing terms (recorded in dataset).
_LICENSE_FILENAMES = {"license", "licence", "copying", "unlicense",
    "license.md", "license.txt", "copying.lesser"}


def _lang_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext:
        return _LANG_BY_EXT.get(ext, "unknown")
    base = os.path.basename(path).lower()
    if base == "dockerfile":
        return "dockerfile"
    if base == "makefile":
        return "makefile"
    if base in _LICENSE_FILENAMES:
        return "license"
    if base.endswith(".dockerfile"):
        return "dockerfile"
    return "unknown"


def _sha256_hex(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()


def build_dataset_record(
    repo: str,
    branch: str,
    path: str,
    content: bytes,
    sha: Optional[str] = None,
) -> Dict:
    """Build one AI-ready dataset record for a single source file.

    The record is intentionally flat + newline-stable so it serialises cleanly
    to both JSONL (one object per line) and a JSON array.
    """
    ext = os.path.splitext(path)[1].lower()
    is_text = ext in _TEXT_EXT
    try:
        text = content.decode("utf-8") if is_text else None
        decode_ok = True
    except UnicodeDecodeError:
        text = None
        decode_ok = False

    record = {
        "repo": repo,
        "branch": branch,
        "path": path,
        "language": _lang_for(path),
        "ext": ext or "",
        "size_bytes": len(content),
        "git_sha": sha,
        "sha256": _sha256_hex(content),
        "decoded_utf8": decode_ok,
        "is_text": is_text,
    }
    if is_text and text is not None:
        record["content"] = text
        record["line_count"] = text.count("\n") + (0 if text.endswith("\n") or text == "" else 1)
    else:
        record["content"] = None  # binary/text-undecodable -> do not embed
    return record


class RateLimitError(RuntimeError):
    """Raised when the GitHub API rate limit is exhausted."""


class CrawlError(RuntimeError):
    """Raised for unrecoverable crawl failures (bad repo, auth, network)."""


@dataclass
class WrittenFile:
    """One file the crawler wrote to disk (or would write, in dry-run)."""
    rel_path: str
    abs_path: str
    size: int
    sha: Optional[str] = None


@dataclass
class CrawlResult:
    """Aggregate outcome of a crawl."""

    repo: str
    branch: str
    dest: str
    files_written: List[WrittenFile] = field(default_factory=list)
    files_included: List[WrittenFile] = field(default_factory=list)
    files_skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    truncated_tree: bool = False
    total_blobs_seen: int = 0
    bytes_written: int = 0
    elapsed_s: float = 0.0
    dataset_jsonl: Optional[str] = None
    dataset_json: Optional[str] = None
    manifest_json: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "repo": self.repo,
            "branch": self.branch,
            "dest": self.dest,
            "total_blobs_seen": self.total_blobs_seen,
            "files_written": len(self.files_written),
            "files_included": len(self.files_included),
            "bytes_written": self.bytes_written,
            "files_skipped": self.files_skipped,
            "truncated_tree": self.truncated_tree,
            "errors": self.errors,
            "elapsed_s": round(self.elapsed_s, 3),
            "dataset_jsonl": self.dataset_jsonl,
            "dataset_json": self.dataset_json,
            "manifest_json": self.manifest_json,
            "written": [
                {"rel_path": w.rel_path, "size": w.size, "sha": w.sha}
                for w in self.files_written
            ],
        }


# --------------------------------------------------------------------------- #
# Core crawler (framework-agnostic, injectable HTTP session for testing)
# --------------------------------------------------------------------------- #
class GitHubCrawler:
    """Fetch a public GitHub repo's source tree to a local directory.

    Usage::

        crawler = GitHubCrawler(token=os.environ.get("GITHUB_TOKEN"))
        result = crawler.crawl("octocat/Hello-World", dest="./mirror")
    """

    def __init__(
        self,
        token: Optional[str] = None,
        session: Optional["requests.Session"] = None,
        timeout: int = DEFAULT_TIMEOUT,
        user_agent: str = _USER_AGENT,
        max_files: int = DEFAULT_MAX_FILES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_depth: int = DEFAULT_MAX_DEPTH,
        include_ext: Optional[Iterable[str]] = None,
        exclude_dirs: Optional[Iterable[str]] = None,
    ) -> None:
        if requests is None and session is None:
            raise CrawlError(
                "The 'requests' package is required. Install with: pip install requests"
            )
        self.token = token or resolve_github_token()
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.max_depth = max_depth
        self.include_ext = {
            e.lower() if e.startswith(".") else "." + e.lower()
            for e in (include_ext or DEFAULT_INCLUDE_EXT)
        }
        self.exclude_dirs = {d.strip("/") for d in (exclude_dirs or DEFAULT_EXCLUDE_DIRS)}
        self._session = session or self._build_session()

    # -- HTTP plumbing ------------------------------------------------------ #
    def _build_session(self) -> "requests.Session":
        s = requests.Session()  # type: ignore[attr-defined]
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        s.headers.update(headers)
        return s

    def _api_get(self, url: str, params: Optional[Dict] = None) -> Dict:
        """GET a GitHub API endpoint with rate-limit + error handling."""
        r = self._session.get(url, params=params, timeout=self.timeout)
        self._assert_rate_limit(r)
        if r.status_code == 404:
            raise CrawlError(f"Repository not found or not public: {url}")
        if r.status_code in (401, 403) and not self._has_remaining(r):
            self._assert_rate_limit(r)  # raises RateLimitError with detail
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _has_remaining(r: "requests.Response") -> bool:
        rem = r.headers.get("X-RateLimit-Remaining")
        return rem is None or int(rem) > 0

    @staticmethod
    def _assert_rate_limit(r: "requests.Response") -> None:
        rem = r.headers.get("X-RateLimit-Remaining")
        if rem is not None and int(rem) <= 0:
            reset = r.headers.get("X-RateLimit-Reset")
            when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(reset))) if reset else "unknown"
            raise RateLimitError(
                f"GitHub API rate limit exhausted. Resets at {when}. "
                f"Set GITHUB_TOKEN to raise the limit to 5000 req/h."
            )

    # -- repo spec parsing ------------------------------------------------- #
    _GITHUB_HOSTS = {"github.com", "www.github.com"}

    @classmethod
    def parse_repo(cls, spec: str) -> Tuple[str, str]:
        """Parse 'owner/name', an https URL, or an ssh URL into (owner, name).

        Only GitHub hosts are accepted; a non-GitHub spec is rejected so the
        crawler never contacts an attacker-controlled host.
        """
        s = (spec or "").strip()
        if not s:
            raise CrawlError("Empty repository specification.")

        # SSH form: git@github.com:owner/name(.git)
        ssh = re.match(r"^[\w.-]+@([\w.-]+):([\w./-]+)", s)
        if ssh:
            host, path = ssh.group(1), ssh.group(2)
            if host not in cls._GITHUB_HOSTS:
                raise CrawlError(f"Only github.com is supported (got host '{host}').")
            s = path

        # Strip scheme + host for https/ssh URL forms
        s = re.sub(r"^[a-zA-Z][\w+.-]*://", "", s)          # scheme://
        # Now possibly "github.com/owner/name" or "host:port/owner/name"
        if "/" in s and not s.startswith("/"):
            host_part, _, rest = s.partition("/")
            # A host contains a dot or colon; 'owner/name' has neither.
            if ("." in host_part or ":" in host_part) and host_part not in rest:
                if host_part.split(":")[0] not in cls._GITHUB_HOSTS:
                    raise CrawlError(f"Only github.com is supported (got host '{host_part}').")
                s = rest

        s = s.strip("/")
        s = re.sub(r"\.git$", "", s)                          # drop .git suffix
        parts = [p for p in s.split("/") if p]
        # Keep only owner/name; ignore /tree/<branch>/..., /blob/..., etc.
        if len(parts) >= 2:
            owner, name = parts[0], parts[1]
        else:
            raise CrawlError(
                f"Cannot parse repository from '{spec}'. Expected 'owner/name'."
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise CrawlError(f"Invalid owner/name in '{spec}'.")
        return owner, name

    # -- discovery + fetch ------------------------------------------------- #
    def default_branch(self, owner: str, name: str) -> str:
        data = self._api_get(f"{API_ROOT}/repos/{owner}/{name}")
        return data.get("default_branch") or "main"

    def _should_include(self, path: str, size: Optional[int]) -> Tuple[bool, str]:
        """Return (include?, reason)."""
        norm = path.replace("\\", "/")
        parts = norm.split("/")
        depth = len(parts)
        if depth > self.max_depth:
            return False, f"depth>{self.max_depth}"
        # excluded directory segment?
        for seg in parts[:-1]:
            if seg in self.exclude_dirs:
                return False, f"excluded_dir:{seg}"
        # extension filter
        ext = os.path.splitext(norm)[1].lower()
        if self.include_ext and ext not in self.include_ext:
            return False, f"ext:{ext or '(none)'}"
        if size is not None and size > self.max_bytes:
            return False, f"size>{self.max_bytes}"
        return True, "ok"

    def _fetch_raw(self, owner: str, name: str, branch: str, path: str) -> bytes:
        """Fetch file content via raw.githubusercontent.com (fast path)."""
        url = f"{RAW_ROOT}/{owner}/{name}/{branch}/{path}"
        r = self._session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.content

    def crawl(
        self,
        repo_spec: str,
        branch: Optional[str] = None,
        dest: Optional[str] = None,
        overwrite: bool = False,
        match: Optional[str] = None,
        dry_run: bool = False,
        on_file: Optional[Callable[[str, bool], None]] = None,
        max_files: Optional[int] = None,
        max_bytes: Optional[int] = None,
        max_depth: Optional[int] = None,
        include_ext: Optional[Iterable[str]] = None,
        export_dataset: bool = True,
    ) -> CrawlResult:
        """Crawl ``repo_spec`` and write its source tree under ``dest``.

        Parameters
        ----------
        repo_spec: 'owner/name', a github URL, or an ssh URL.
        branch:    ref to crawl (defaults to the repo's default branch).
        dest:      output directory (defaults to './<owner>__<name>').
        overwrite: if False, skip files that already exist.
        match:     optional regex; only paths matching are fetched.
        dry_run:   discover + filter but do not write to disk.
        on_file:   callback(rel_path, written: bool) for live progress.
        max_files, max_bytes, max_depth, include_ext: optional per-call
            overrides of the crawler's construction-time limits.
        export_dataset: if True (default), also write dataset.jsonl /
            dataset.json / manifest.json (AI-ready structured output) next to
            the mirrored files.
        """
        # Resolve effective limits (per-call override > constructor value).
        max_files = int(max_files) if max_files is not None else self.max_files
        max_bytes = int(max_bytes) if max_bytes is not None else self.max_bytes
        max_depth = int(max_depth) if max_depth is not None else self.max_depth
        if include_ext is not None:
            include_ext = {
                e.lower() if e.startswith(".") else "." + e.lower()
                for e in include_ext
            }
        else:
            include_ext = self.include_ext

        start = time.time()
        owner, name = self.parse_repo(repo_spec)
        repo = f"{owner}/{name}"
        branch = branch or self.default_branch(owner, name)
        dest = dest or os.path.join(os.getcwd(), f"{owner}__{name}")
        dest = os.path.abspath(dest)
        match_re = re.compile(match) if match else None

        result = CrawlResult(repo=repo, branch=branch, dest=dest)

        tree_url = f"{API_ROOT}/repos/{owner}/{name}/git/trees/{branch}?recursive=1"
        logger.info(f"[crawler] Discovering tree for {repo}@{branch}")
        tree = self._api_get(tree_url)
        if tree.get("truncated"):
            result.truncated_tree = True
            logger.warning(
                "[crawler] Tree was truncated by GitHub (too large). "
                "Results may be incomplete; raise max_files or narrow with --match."
            )

        blobs = [t for t in tree.get("tree", []) if t.get("type") == "blob"]
        result.total_blobs_seen = len(blobs)

        # Local helper using the resolved per-call limits.
        def _include(path: str, size: Optional[int]) -> Tuple[bool, str]:
            norm = path.replace("\\", "/")
            parts = norm.split("/")
            if len(parts) > max_depth:
                return False, f"depth>{max_depth}"
            for seg in parts[:-1]:
                if seg in self.exclude_dirs:
                    return False, f"excluded_dir:{seg}"
            ext = os.path.splitext(norm)[1].lower()
            if include_ext and ext not in include_ext:
                return False, f"ext:{ext or '(none)'}"
            if size is not None and size > max_bytes:
                return False, f"size>{max_bytes}"
            return True, "ok"

        for entry in blobs:
            path = entry["path"]
            size = entry.get("size")
            include, reason = _include(path, size)
            if not include:
                result.files_skipped.append(f"{path}  ({reason})")
                if on_file:
                    on_file(path, False)
                continue
            if match_re and not match_re.search(path):
                result.files_skipped.append(f"{path}  (no --match)")
                if on_file:
                    on_file(path, False)
                continue

            # Only files we actually fetch count toward the max_files budget.
            # Skipped/filtered entries do not consume it.
            if len(result.files_written) >= max_files:
                logger.info(f"[crawler] Reached max_files={max_files}; stopping.")
                break

            try:
                content = self._fetch_raw(owner, name, branch, path)
            except Exception as e:  # raw failed; record and continue
                msg = f"{path}: fetch failed ({type(e).__name__}: {e})"
                result.errors.append(msg)
                logger.warning(f"[crawler] {msg}")
                if on_file:
                    on_file(path, False)
                continue

            rel = path.replace("\\", "/")
            abs_path = os.path.join(dest, *rel.split("/"))
            written = False
            if not dry_run:
                if os.path.exists(abs_path) and not overwrite:
                    result.files_skipped.append(f"{path}  (exists, --overwrite to replace)")
                    # Still include already-present files in the dataset/record set
                    # so a re-run over an existing mirror is not silently empty.
                    result.files_included.append(
                        WrittenFile(rel_path=rel, abs_path=abs_path, size=os.path.getsize(abs_path), sha=entry.get("sha"))
                    )
                    if on_file:
                        on_file(path, False)
                    continue
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "wb") as fh:
                    fh.write(content)
                written = True
                result.bytes_written += len(content)
                wf = WrittenFile(rel_path=rel, abs_path=abs_path, size=len(content), sha=entry.get("sha"))
                result.files_written.append(wf)
                result.files_included.append(wf)
            else:
                result.files_included.append(
                    WrittenFile(rel_path=rel, abs_path=abs_path, size=len(content), sha=entry.get("sha"))
                )
            if on_file:
                on_file(path, written)

        result.elapsed_s = time.time() - start
        logger.info(
            f"[crawler] Done: {len(result.files_written)} written, "
            f"{len(result.files_included)} included, "
            f"{len(result.files_skipped)} skipped, {len(result.errors)} errors "
            f"in {result.elapsed_s:.2f}s"
        )
        if export_dataset and not dry_run:
            self._write_dataset(result, owner, name, branch)
        elif export_dataset and dry_run:
            logger.info(
                "[crawler] Dry-run: dataset export skipped "
                "(no files were written to disk)."
            )
        return result

    # -- AI-ready dataset export ------------------------------------------ #
    def _write_dataset(self, result: "CrawlResult", owner: str, name: str, branch: str) -> None:
        """Write dataset.jsonl, dataset.json, and a manifest next to the mirror.

        dataset.jsonl  : one JSON object per line (streaming/training friendly)
        dataset.json   : {meta, records:[...]} (single-file, easy to load)
        manifest.json  : crawl summary + per-language counts
        """
        records: List[Dict] = []
        lang_counts: Dict[str, int] = {}
        license_text = None
        for w in result.files_included:
            try:
                with open(w.abs_path, "rb") as fh:
                    content = fh.read()
            except OSError:
                continue
            rec = build_dataset_record(result.repo, branch, w.rel_path, content, w.sha)
            records.append(rec)
            lang_counts[rec["language"]] = lang_counts.get(rec["language"], 0) + 1
            base = os.path.basename(w.rel_path).lower()
            if license_text is None and (base in _LICENSE_FILENAMES or base.startswith("license")):
                if rec.get("content"):
                    license_text = rec["content"]

        meta = {
            "schema_version": "captn.crawler.dataset/1.0",
            "source": "github",
            "repo": result.repo,
            "branch": branch,
            "crawled_at": datetime.utcnow().isoformat() + "Z",
            "crawler": "Captn GitHubCrawler",
            "record_count": len(records),
            "languages": lang_counts,
            "license_text": license_text,
            "truncated_tree": result.truncated_tree,
            "mirror_dir": result.dest,
        }

        # JSONL (one record per line) - the canonical training format.
        os.makedirs(result.dest, exist_ok=True)  # safe even if crawl skipped makedirs
        jsonl_path = os.path.join(result.dest, "dataset.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Single-file JSON ({meta, records}).
        json_path = os.path.join(result.dest, "dataset.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"meta": meta, "records": records}, fh, ensure_ascii=False, indent=2)

        # Manifest (no file contents - small, always safe to inspect).
        manifest_path = os.path.join(result.dest, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2)

        result.dataset_jsonl = jsonl_path
        result.dataset_json = json_path
        result.manifest_json = manifest_path
        logger.info(
            f"[crawler] Dataset written: {len(records)} records -> "
            f"{jsonl_path} (+ dataset.json, manifest.json)"
        )


# --------------------------------------------------------------------------- #
# Captn Plugin wrapper
# --------------------------------------------------------------------------- #
class CrawlerWorker:
    """Captn ``Plugin`` that crawls a GitHub repo and emits ``UniversalData``.

    Message payload (all optional except ``repo``)::
        {
          "task_id": "1002",
          "repo": "owner/name",          # or a github URL / ssh URL
          "branch": "main",              # optional
          "dest": "./mirror",            # optional output dir
          "overwrite": false,            # optional
          "match": "src/.*\\.py$",       # optional path regex
          "include_ext": [".py", ".md"]  # optional
        }
    """

    name = "crawler"

    def __init__(self, bus=None):
        self.bus = bus
        self.is_active = False
        self.crawler: Optional[GitHubCrawler] = None

    def initialize(self) -> bool:
        token = os.environ.get("GITHUB_TOKEN")
        try:
            self.crawler = GitHubCrawler(token=token)
        except CrawlError as e:
            logger.error(f"[{self.name}] Init failed: {e}")
            return False
        logger.info(f"[{self.name}] Connected to GitHub API (token={'set' if token else 'unset'}).")
        self.is_active = True
        return True

    def execute(self, message) -> None:
        from captn.runtime.base import Message  # local import avoids cycles
        from captn.runtime.schemas import UniversalData

        payload = getattr(message, "payload", {}) or {}
        task_id = payload.get("task_id")
        repo = payload.get("repo") or payload.get("repo_url")
        if not repo:
            self._error(task_id, "Missing 'repo' in payload (expected 'owner/name' or a GitHub URL).")
            return

        include_ext = payload.get("include_ext")
        try:
            crawler = self.crawler or GitHubCrawler(token=os.environ.get("GITHUB_TOKEN"))
            result = crawler.crawl(
                repo_spec=str(repo),
                branch=payload.get("branch"),
                dest=payload.get("dest"),
                overwrite=bool(payload.get("overwrite", False)),
                match=payload.get("match"),
                dry_run=bool(payload.get("dry_run", False)),
                include_ext=payload.get("include_ext"),
                export_dataset=bool(payload.get("export_dataset", True)),
            )
        except (RateLimitError, CrawlError) as e:
            self._error(task_id, str(e))
            return
        except Exception as e:  # never kill the bus thread
            self._error(task_id, f"Unexpected crawler error: {type(e).__name__}: {e}")
            return

        data = UniversalData(
            source_type="github",
            content={"crawl_result": result.to_dict()},
            metadata={
                "repo": result.repo,
                "branch": result.branch,
                "dest": result.dest,
                "files_written": len(result.files_written),
                "bytes_written": result.bytes_written,
                "dataset_jsonl": result.dataset_jsonl,
                "dataset_json": result.dataset_json,
                "manifest_json": result.manifest_json,
            },
        )
        if self.bus is not None:
            self.bus.publish(Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "data": data.__dict__,
                    "written_paths": [w.abs_path for w in result.files_written],
                    "dataset_jsonl": result.dataset_jsonl,
                    "dataset_json": result.dataset_json,
                },
            ))
        else:
            logger.info(f"[{self.name}] (no bus) {result.to_dict()}")

    def _error(self, task_id, msg: str) -> None:
        logger.error(f"[{self.name}] {msg}")
        if self.bus is not None:
            from captn.runtime.base import Message
            self.bus.publish(Message(
                sender=self.name,
                destination="captn",
                type="error",
                payload={"task_id": task_id, "error_message": msg},
            ))

    def shutdown(self) -> bool:
        self.is_active = False
        return True


# --------------------------------------------------------------------------- #
# Self code-learning: merge a crawled dataset into the project code corpus
# --------------------------------------------------------------------------- #
def dedup_corpus(
    corpus_dir: str,
    *,
    corpus_jsonl_name: str = "dataset.jsonl",
    corpus_json_name: str = "dataset.json",
) -> Dict[str, int]:
    """Remove duplicate records from the corpus files (in place).

    AUDIT: the merge dedups by ``sha256`` OR ``path`` — but records whose
    sha256 field is missing fall back to path-keying, so the SAME content
    crawled twice from different paths could slip through as two records.
    This pass keys strictly on CONTENT hash (sha256 of ``content``, computed
    if absent), keeping the first occurrence of each.

    Returns {"before": n, "after": m, "removed": k}.
    """
    corpus_dir = os.path.abspath(corpus_dir)
    corpus_jsonl = os.path.join(corpus_dir, corpus_jsonl_name)
    if not os.path.exists(corpus_jsonl):
        return {"before": 0, "after": 0, "removed": 0}

    import hashlib as _hl

    def content_key(rec: Dict) -> str:
        # ALWAYS key on the actual content hash — a stored sha256 field may
        # differ between two records with identical content (different crawl
        # provenance), which is exactly the duplicate case we must catch.
        c = rec.get("content") or ""
        return "content:" + _hl.sha256(c.encode("utf-8")).hexdigest()

    seen: set = set()
    kept: List[Dict] = []
    before = 0
    with open(corpus_jsonl, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            before += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # corrupt line counts as removed
            key = content_key(rec)
            if key in seen:
                continue
            seen.add(key)
            kept.append(rec)

    removed = before - len(kept)
    if removed > 0:
        # Rewrite JSONL atomically.
        tmp = corpus_jsonl + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for rec in kept:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for _attempt in range(5):
            try:
                os.replace(tmp, corpus_jsonl)
                break
            except (OSError, PermissionError):
                if _attempt == 4:
                    raise
                time.sleep(0.3 * (_attempt + 1))

        # Keep dataset.json in sync when present.
        corpus_json = os.path.join(corpus_dir, corpus_json_name)
        if os.path.exists(corpus_json):
            try:
                with open(corpus_json, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict) and isinstance(data.get("records"), list):
                    seen2 = set()
                    recs2 = []
                    for rec in data["records"]:
                        k = content_key(rec)
                        if k not in seen2:
                            seen2.add(k)
                            recs2.append(rec)
                    data["records"] = recs2
                    if isinstance(data.get("meta"), dict):
                        data["meta"]["record_count"] = len(recs2)
                    tmp2 = corpus_json + ".tmp"
                    with open(tmp2, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, ensure_ascii=False, indent=2)
                    os.replace(tmp2, corpus_json)
            except (OSError, json.JSONDecodeError):
                pass  # JSON mirror is best-effort

    logger.info(f"[crawler] Corpus dedup: {before} -> {len(kept)} ({removed} removed).")
    return {"before": before, "after": len(kept), "removed": removed}


def merge_dataset_into_corpus(
    dataset_jsonl: str,
    corpus_dir: str,
    *,
    corpus_jsonl_name: str = "dataset.jsonl",
    corpus_json_name: str = "dataset.json",
    agg_manifest_name: str = "corpus_manifest.json",
    dry_run: bool = False,
    dedup_after: bool = True,
) -> Dict[str, int]:
    """Merge a crawled ``dataset.jsonl`` into the project code-learning corpus.

    The corpus lives at ``Code_base/dataset.jsonl`` (+ ``dataset.json``) and is the
    source the deterministic worker chain (extractor -> normalizer -> translator ->
    validator) trains/learns from. Records are deduplicated by ``sha256`` so
    re-merging the same repo (or overlapping files) is idempotent.

    Args:
        dataset_jsonl: path to the freshly-crawled ``dataset.jsonl``
        corpus_dir: directory of the corpus (created if missing)
        dry_run: if True, only count; write nothing

    Returns:
        {"incoming": n, "existing": n, "added": n, "duplicates": n}
    """
    corpus_dir = os.path.abspath(corpus_dir)
    os.makedirs(corpus_dir, exist_ok=True)
    corpus_jsonl = os.path.join(corpus_dir, corpus_jsonl_name)
    corpus_json = os.path.join(corpus_dir, corpus_json_name)
    agg_manifest = os.path.join(corpus_dir, agg_manifest_name)

    # Load existing records (dedup key = sha256 of content).
    existing: Dict[str, Dict] = {}
    if os.path.exists(corpus_jsonl):
        with open(corpus_jsonl, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = rec.get("sha256") or rec.get("path")
                if key:
                    existing[key] = rec

    # Read incoming records.
    incoming: List[Dict] = []
    with open(dataset_jsonl, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                incoming.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    added: List[Dict] = []
    duplicates = 0
    for rec in incoming:
        key = rec.get("sha256") or rec.get("path")
        if key in existing:
            duplicates += 1
            continue
        existing[key] = rec
        added.append(rec)

    stats = {
        "incoming": len(incoming),
        "existing": len(existing) - len(added),
        "added": len(added),
        "duplicates": duplicates,
    }
    logger.info(
        f"[crawler] Corpus merge: {stats['added']} added, "
        f"{stats['duplicates']} duplicate, corpus now {len(existing)} records."
    )
    if dry_run:
        return stats

    # Atomic write: build new files in memory, then replace.
    # Retry the os.replace a few times: on Windows a concurrent reader/renamer
    # (e.g. autogen feeding dataset.autogen.jsonl, or another --learn merge) can
    # momentarily lock the target and raise PermissionError. A short backoff
    # makes the merge resilient instead of crashing.
    new_jsonl = os.path.join(corpus_dir, corpus_jsonl_name + ".tmp")
    with open(new_jsonl, "w", encoding="utf-8") as fh:
        for rec in existing.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    for _attempt in range(5):
        try:
            os.replace(new_jsonl, corpus_jsonl)
            break
        except (OSError, PermissionError):
            if _attempt == 4:
                raise
            time.sleep(0.3 * (_attempt + 1))

    # Single-file JSON {meta, records}
    lang_counts: Dict[str, int] = {}
    repos: Dict[str, int] = {}
    for rec in existing.values():
        lang = rec.get("language", "unknown")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
        repo = rec.get("repo", "unknown")
        repos[repo] = repos.get(repo, 0) + 1
    agg_meta = {
        "schema_version": "captn.crawler.dataset/1.0",
        "source": "github",
        "merged_at": datetime.utcnow().isoformat() + "Z",
        "corpus_dir": corpus_dir,
        "record_count": len(existing),
        "repos": repos,
        "languages": lang_counts,
        "note": "Aggregated self-learning corpus. Built by merging crawled datasets.",
    }
    new_json = os.path.join(corpus_dir, corpus_json_name + ".tmp")
    with open(new_json, "w", encoding="utf-8") as fh:
        json.dump({"meta": agg_meta, "records": list(existing.values())}, fh, ensure_ascii=False, indent=2)
    for _attempt in range(5):
        try:
            os.replace(new_json, corpus_json)
            break
        except (OSError, PermissionError):
            if _attempt == 4:
                raise
            time.sleep(0.3 * (_attempt + 1))

    # Aggregated manifest (no file contents - safe to inspect).
    for _attempt in range(5):
        try:
            with open(agg_manifest, "w", encoding="utf-8") as fh:
                json.dump(agg_meta, fh, ensure_ascii=False, indent=2)
            break
        except (OSError, PermissionError):
            if _attempt == 4:
                raise
            time.sleep(0.3 * (_attempt + 1))

    dedup_stats: Dict[str, int] = {}
    if dedup_after and not dry_run:
        # Smart content-level dedup pass (catches same-content records that
        # slipped through path-based keying). Runs automatically after each
        # merge unless explicitly disabled.
        dedup_stats = dedup_corpus(corpus_dir, corpus_jsonl_name=corpus_jsonl_name,
                                   corpus_json_name=corpus_json_name)
        stats["dedup_removed"] = dedup_stats.get("removed", 0)

    return stats


# Allow `from captn.workers.data_ingestion.crawler import Crawler` as an alias.
Crawler = GitHubCrawler
