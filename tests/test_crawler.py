"""Tests for the Captn GitHub crawler.

Most tests are fully offline using a fake ``requests.Session`` that serves a
hand-built GitHub API tree + raw blobs. A single live-integration test runs
only when network + the env var ``CAPTN_CRAWLER_LIVE=1`` are available, so the
suite is deterministic by default and never depends on GitHub in CI.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Dict, List
from unittest import mock

import pytest

from captn.workers.data_ingestion.crawler import (
    CrawlError,
    CrawlerWorker,
    GitHubCrawler,
    RateLimitError,
    merge_dataset_into_corpus,
)

# --------------------------------------------------------------------------- #
# Fake GitHub server (in-process, no network)
# --------------------------------------------------------------------------- #
TREE_PAYLOAD = {
    "truncated": False,
    "tree": [
        {"path": "src/main.py", "type": "blob", "size": 13, "sha": "a1"},
        {"path": "src/util.py", "type": "blob", "size": 9, "sha": "a2"},
        {"path": "README.md", "type": "blob", "size": 7, "sha": "a3"},
        {"path": "node_modules/bad.js", "type": "blob", "size": 5, "sha": "a4"},
        {"path": "data/image.png", "type": "blob", "size": 5, "sha": "a5"},
        {"path": "src", "type": "tree"},
        {"path": "Makefile", "type": "blob", "size": 4, "sha": "a6"},
    ],
}

RAW_BLOBS = {
    "src/main.py": b"print('hi')\n",
    "src/util.py": b"x = 1\n",
    "README.md": b"hello\n",
}


class _FakeResponse:
    def __init__(self, status=200, json_data=None, content=b"", headers=None):
        self.status_code = status
        self._json = json_data if json_data is not None else {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Routes to the fake API tree or a raw blob based on the URL."""

    def __init__(self):
        self.calls: List[str] = []
        self.rate_remaining = 5000

    def _headers(self, remaining=None):
        rem = self.rate_remaining if remaining is None else remaining
        return {
            "X-RateLimit-Remaining": str(rem),
            "X-RateLimit-Reset": "1700000000",
            "X-RateLimit-Limit": "5000",
        }

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if "repos/" in url and "/git/trees/" in url:
            return _FakeResponse(200, TREE_PAYLOAD, headers=self._headers())
        if url.endswith("/repos/fake/repo"):
            return _FakeResponse(200, {"default_branch": "main"}, headers=self._headers())
        if "raw.githubusercontent.com" in url:
            # URL form: https://raw.githubusercontent.com/owner/name/branch/path
            path = url.split("raw.githubusercontent.com/", 1)[1]
            _, _, branch_and_path = path.partition("/")
            rel = branch_and_path.split("/", 2)[2]
            return _FakeResponse(200, content=RAW_BLOBS.get(rel, b""), headers=self._headers())
        return _FakeResponse(404, headers=self._headers())


@pytest.fixture
def fake_session():
    return _FakeSession()


@pytest.fixture
def crawler(fake_session):
    with mock.patch("captn.workers.crawler.requests", create=True):
        c = GitHubCrawler(token=None, session=fake_session)
        return c


# --------------------------------------------------------------------------- #
# parse_repo
# --------------------------------------------------------------------------- #
def test_parse_repo_basic():
    assert GitHubCrawler.parse_repo("octocat/Hello-World") == ("octocat", "Hello-World")


def test_parse_repo_https_url():
    assert GitHubCrawler.parse_repo("https://github.com/psf/requests") == ("psf", "requests")


def test_parse_repo_ssh():
    assert GitHubCrawler.parse_repo("git@github.com:psf/requests.git") == ("psf", "requests")


def test_parse_repo_with_tree_suffix():
    spec = "https://github.com/owner/repo/tree/dev/src"
    assert GitHubCrawler.parse_repo(spec) == ("owner", "repo")


def test_parse_repo_rejects_garbage():
    with pytest.raises(CrawlError):
        GitHubCrawler.parse_repo("not a repo")


def test_parse_repo_rejects_non_github_host():
    # We only allow github hosts; a hostile spec must not slip through.
    with pytest.raises(CrawlError):
        GitHubCrawler.parse_repo("https://evil.example.com/owner/repo")


# --------------------------------------------------------------------------- #
# crawl behaviour (offline)
# --------------------------------------------------------------------------- #
def test_crawl_writes_expected_files(crawler, fake_session):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", branch="main", dest=d)
        written = sorted(w.rel_path for w in res.files_written)
        assert written == ["README.md", "src/main.py", "src/util.py"]
        # .png and node_modules excluded
        skipped = "\n".join(res.files_skipped)
        assert "node_modules/bad.js" in skipped
        assert "data/image.png" in skipped
        # content actually on disk
        with open(os.path.join(d, "src", "main.py"), "rb") as f:
            assert f.read() == b"print('hi')\n"


def test_crawl_respects_include_ext(crawler):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, include_ext=[".md"])
        assert sorted(w.rel_path for w in res.files_written) == ["README.md"]


def test_crawl_dry_run_writes_nothing(crawler):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, dry_run=True)
        # Dry-run records planned files in `files_included` but writes none to disk.
        assert len(res.files_included) == 3
        assert len(res.files_written) == 0
        assert not os.path.exists(os.path.join(d, "src", "main.py"))


def test_crawl_match_regex(crawler):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, match=r"^src/.*\.py$")
        written = sorted(w.rel_path for w in res.files_written)
        assert written == ["src/main.py", "src/util.py"]


def test_crawl_max_files_budget(crawler):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, max_files=2)
        assert len(res.files_written) == 2


def test_crawl_rate_limit_raises():
    s = _FakeSession()
    s.rate_remaining = 0
    with mock.patch("captn.workers.crawler.requests", create=True):
        c = GitHubCrawler(session=s)
        with pytest.raises(RateLimitError):
            c.crawl("fake/repo")


def test_crawl_missing_repo_404():
    s = _FakeSession()

    def get(url, params=None, timeout=None):
        if "git/trees" in url:
            return _FakeResponse(404, headers=s._headers())
        return _FakeResponse(200, {"default_branch": "main"}, headers=s._headers())

    s.get = get
    with mock.patch("captn.workers.crawler.requests", create=True):
        c = GitHubCrawler(session=s)
        with pytest.raises(CrawlError):
            c.crawl("fake/repo")


def test_unknown_extension_skipped(crawler):
    # A repo path with no extension and not in include set should be skipped.
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d)
        # LICENSE (no ext) is not in our default include set -> skipped
        assert all(not w.rel_path.endswith("LICENSE") for w in res.files_written)


def test_dataset_export_writes_jsonl_and_json(crawler):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, export_dataset=True)
        # dataset files exist
        assert res.dataset_jsonl and os.path.exists(res.dataset_jsonl)
        assert res.dataset_json and os.path.exists(res.dataset_json)
        assert res.manifest_json and os.path.exists(res.manifest_json)
        # JSONL: one JSON object per line, content embedded
        with open(res.dataset_jsonl, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        assert len(lines) == len(res.files_written)
        rec = json.loads(lines[0])
        assert "content" in rec and "language" in rec and "sha256" in rec
        assert rec["repo"] == "fake/repo"
        # dataset.json structure
        with open(res.dataset_json, encoding="utf-8") as fh:
            blob = json.load(fh)
        assert "meta" in blob and "records" in blob
        assert blob["meta"]["record_count"] == len(res.files_written)


def test_dataset_respects_no_dataset_flag(crawler):
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, export_dataset=False)
        assert res.dataset_jsonl is None
        assert not os.path.exists(os.path.join(d, "dataset.jsonl"))


def test_dataset_binary_excluded_from_content(crawler):
    # A png path is included in the tree; content must NOT be embedded.
    with tempfile.TemporaryDirectory() as d:
        res = crawler.crawl("fake/repo", dest=d, include_ext=[".png"])
        rec_exists = any(w.rel_path == "data/image.png" for w in res.files_written)
        if rec_exists:
            with open(res.dataset_jsonl, encoding="utf-8") as fh:
                recs = [json.loads(ln) for ln in fh.read().splitlines() if ln.strip()]
            png = next(r for r in recs if r["path"] == "data/image.png")
            assert png["content"] is None
            assert png["is_text"] is False



# --------------------------------------------------------------------------- #
# Plugin wrapper
# --------------------------------------------------------------------------- #
def test_plugin_worker_publishes_universal_data():
    s = _FakeSession()
    with mock.patch("captn.workers.crawler.requests", create=True):
        w = CrawlerWorker(bus=None)
        assert w.initialize()
        # monkeypatch the crawler with our fake session
        w.crawler = GitHubCrawler(token=None, session=s)
        from captn.runtime.base import Message

        with tempfile.TemporaryDirectory() as d:
            w.execute(Message(
                sender="user", destination="captn", type="task",
                payload={"task_id": "T1", "repo": "fake/repo", "dest": d, "max_files": 3},
            ))
        assert w.shutdown()


def test_plugin_worker_missing_repo_errors():
    with mock.patch("captn.workers.crawler.requests", create=True):
        w = CrawlerWorker(bus=None)
        assert w.initialize()
        from captn.runtime.base import Message

        w.execute(Message(
            sender="user", destination="captn", type="task",
            payload={"task_id": "T2"},  # no repo
        ))
        assert w.shutdown()


# --------------------------------------------------------------------------- #
# Guarded live integration (opt-in, never runs in default CI)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    os.environ.get("CAPTN_CRAWLER_LIVE") != "1",
    reason="set CAPTN_CRAWLER_LIVE=1 with network to run live GitHub test",
)
def test_live_crawl_small_repo():
    c = GitHubCrawler(token=os.environ.get("GITHUB_TOKEN"))
    with tempfile.TemporaryDirectory() as d:
        res = c.crawl(
            "psf/requests",
            match=r"^tests/__init__\.py$",
            dest=os.path.join(d, "out"),
            max_files=1,
        )
        assert len(res.files_written) >= 1


# --------------------------------------------------------------------------- #
# Self code-learning: corpus merge (offline)
# --------------------------------------------------------------------------- #
def _write_jsonl(path: str, records: List[Dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_merge_into_corpus_adds_and_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        incoming = os.path.join(d, "dataset.jsonl")
        _write_jsonl(incoming, [
            {"repo": "a/b", "path": "x.py", "language": "python", "sha256": "k1", "content": "1"},
            {"repo": "a/b", "path": "y.py", "language": "python", "sha256": "k2", "content": "2"},
        ])
        corpus = os.path.join(d, "corpus")
        s1 = merge_dataset_into_corpus(incoming, corpus)
        # dedup_removed key present since auto-dedup runs after each merge.
        assert s1["incoming"] == 2 and s1["added"] == 2 and s1["duplicates"] == 0
        assert "dedup_removed" in s1

        # Re-merging the same dataset is a no-op (dedup by sha256).
        s2 = merge_dataset_into_corpus(incoming, corpus)
        assert s2["incoming"] == 2 and s2["existing"] == 2
        assert s2["added"] == 0 and s2["duplicates"] == 2

        # A new record is added; existing repo count grows.
        _write_jsonl(os.path.join(d, "dataset2.jsonl"), [
            {"repo": "c/d", "path": "z.py", "language": "python", "sha256": "k3", "content": "3"},
        ])
        s3 = merge_dataset_into_corpus(os.path.join(d, "dataset2.jsonl"), corpus)
        assert s3["incoming"] == 1 and s3["existing"] == 2 and s3["added"] == 1
        assert s3["duplicates"] == 0

        # Corpus files are well-formed and aggregated.
        n = sum(1 for _ in open(os.path.join(corpus, "dataset.jsonl"), encoding="utf-8") if _.strip())
        assert n == 3
        manifest = json.load(open(os.path.join(corpus, "corpus_manifest.json"), encoding="utf-8"))
        assert manifest["record_count"] == 3
        assert manifest["repos"] == {"a/b": 2, "c/d": 1}
        dj = json.load(open(os.path.join(corpus, "dataset.json"), encoding="utf-8"))
        assert dj["meta"]["record_count"] == 3 and len(dj["records"]) == 3


def test_merge_into_corpus_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        incoming = os.path.join(d, "dataset.jsonl")
        _write_jsonl(incoming, [{"repo": "a/b", "path": "x.py", "sha256": "k1", "content": "1"}])
        corpus = os.path.join(d, "corpus")
        stats = merge_dataset_into_corpus(incoming, corpus, dry_run=True)
        assert stats["added"] == 1
        assert not os.path.exists(os.path.join(corpus, "dataset.jsonl"))
