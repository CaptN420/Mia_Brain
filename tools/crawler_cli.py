"""captn-crawl — auto-generate a local copy of an open-source repo's code.

Thin, secure CLI around :class:`GitHubCrawler`. It crawls a public GitHub
repository and writes its (filtered) source tree to disk. Designed to plug
into the Captn data pipeline as a "github" source.

Examples
--------
  # Mirror the Python source of a repo under ./mirror
  python tools/crawler_cli.py octocat/Hello-World --dest ./mirror

  # Only pull Python + Markdown, respect existing files
  python tools/crawler_cli.py psf/requests --match ".*\\.(py|md)$"

  # Preview without writing (dry run)
  python tools/crawler_cli.py owner/repo --dry-run

  # Authenticated (higher rate limit) via env var
  GITHUB_TOKEN=ghp_xxx python tools/crawler_cli.py owner/repo

Security
--------
- Only github.com / raw.githubusercontent.com are contacted.
- Repo specs are strictly parsed; arbitrary URLs are rejected.
- A token is read from GITHUB_TOKEN or --token, never printed or logged.
"""

from __future__ import annotations

import argparse
import os
import sys
import json

# Make the project root importable when run directly.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_HERE)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def _load_dotenv_local() -> None:
    """Load GITHUB_TOKEN (and any other vars) from a local .env file.

    Dependency-free: no python-dotenv needed. Only injects vars that are not
    already present in the environment, so an exported GITHUB_TOKEN always wins.
    Errors are ignored so the crawler still runs unauthenticated if .env is absent.
    """
    dotenv_path = os.path.join(_PROJECT_ROOT, ".env")
    try:
        with open(dotenv_path, "r", encoding="utf-8") as _fh:
            for _line in _fh:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
                if _key and _key not in os.environ:
                    os.environ[_key] = _val
    except FileNotFoundError:
        pass


_load_dotenv_local()

from captn.workers.data_ingestion.crawler import GitHubCrawler, RateLimitError, CrawlError  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="captn-crawl",
        description="Auto-generate a local copy of an open-source repo's code from GitHub.",
    )
    p.add_argument("repo", nargs="?", help="owner/name, a github URL, or an ssh URL (omit when using --queue)")
    p.add_argument("--queue", help="path to a text file: one repo per line (batch crawl)")
    p.add_argument("--branch", "-b", help="branch/ref to crawl (default: repo default)")
    p.add_argument("--dest", "-d", help="output directory (default: <cwd>/<owner>__<name>)")
    p.add_argument("--overwrite", action="store_true", help="replace files that already exist")
    p.add_argument("--match", "-m", help="regex; only paths matching are fetched")
    p.add_argument("--dry-run", action="store_true", help="discover + filter but write nothing")
    p.add_argument("--token", help="GitHub token (prefer GITHUB_TOKEN env var)")
    p.add_argument("--max-files", type=int, default=400, help="max files to fetch (default 400)")
    p.add_argument("--max-depth", type=int, default=14, help="max path depth (default 14)")
    p.add_argument("--include-ext", nargs="*", help="file extensions to include (e.g. .py .md)")
    p.add_argument("--no-dataset", action="store_true", help="skip AI-ready dataset.jsonl/json export")
    p.add_argument("--learn", action="store_true",
                   help="after crawling, merge the dataset into the project code-learning corpus "
                        "(Code_base/ by default)")
    p.add_argument("--corpus-dir", help="corpus directory for --learn (default: <root>/Code_base)")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of text")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    token = args.token or os.environ.get("GITHUB_TOKEN")

    # Resolve the repo list: queue file (one per line) or a single repo arg.
    repos: List[str] = []
    if args.queue:
        try:
            with open(args.queue, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        repos.append(line)
        except OSError as e:
            print(f"ERROR: cannot read queue file: {e}", file=sys.stderr)
            return 2
    elif args.repo:
        repos = [args.repo.strip()]
    if not repos:
        print("ERROR: provide a REPO or --queue <file>.", file=sys.stderr)
        return 2

    export_dataset = (not args.no_dataset) or args.learn
    crawler = GitHubCrawler(
        token=token,
        max_files=args.max_files,
        max_depth=args.max_depth,
        include_ext=args.include_ext,
    )

    # --learn on a multi-repo queue merges each dataset into the corpus.
    corpus_dir = args.corpus_dir or os.path.join(_PROJECT_ROOT, "Code_base")

    results = []  # (repo, ok, summary)
    for idx, repo in enumerate(repos, 1):
        dest = args.dest or os.path.join(_PROJECT_ROOT, "crawled", repo.replace("/", "__"))
        try:
            result = crawler.crawl(
                repo_spec=repo,
                branch=args.branch,
                dest=dest,
                overwrite=args.overwrite,
                match=args.match,
                dry_run=args.dry_run,
                export_dataset=export_dataset,
            )
        except (RateLimitError, CrawlError) as e:
            results.append((repo, False, f"ERROR: {e}"))
            continue
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            return 130

        learn_stats = None
        if args.learn and not args.dry_run and result.dataset_jsonl:
            from captn.workers.data_ingestion.crawler import merge_dataset_into_corpus
            try:
                learn_stats = merge_dataset_into_corpus(result.dataset_jsonl, corpus_dir)
            except Exception as le:
                learn_stats = f"corpus-err: {le}"

        if args.json:
            print(json.dumps({"ok": True, "repo": repo, **result.to_dict()}, indent=2))
        else:
            verb = "Discovered" if args.dry_run else "Written"
            learn_note = ""
            if isinstance(learn_stats, dict):
                learn_note = f" | 🧠 +{learn_stats['added']} (dup {learn_stats['duplicates']})"
            elif isinstance(learn_stats, str):
                learn_note = f" | {learn_stats}"
            print(f"[{idx}/{len(repos)}] {verb} {len(result.files_written)} files "
                  f"({result.bytes_written} bytes) from {result.repo}@{result.branch} "
                  f"in {result.elapsed_s:.2f}s{learn_note}")
            if result.dataset_jsonl:
                print(f"           Dataset: {result.dataset_jsonl}")
            if result.truncated_tree:
                print("           WARNING: GitHub returned a truncated tree.")
            if result.errors:
                for e in result.errors[:5]:
                    print(f"           - {e}")
        results.append((repo, True, "ok"))

    ok = sum(1 for _, s, _ in results if s)
    print(f"\nQueue done: {ok} succeeded, {len(results) - ok} failed, {len(results)} total.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
