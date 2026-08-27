"""Tests for smart corpus dedup (dedup_corpus + merge dedup_after)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from captn.workers.crawler import dedup_corpus, merge_dataset_into_corpus


def _rec(content, path, sha=None):
    r = {"content": content, "path": path, "language": "python", "ext": ".py"}
    if sha:
        r["sha256"] = sha
    return r


def test_dedup_removes_same_content_different_paths(tmp_path):
    """The core bug: identical content under two paths = duplicate."""
    corpus = tmp_path / "dataset.jsonl"
    recs = [
        _rec("print('hello')", "repoA/util.py"),
        _rec("print('hello')", "repoB/util.py"),  # same content, different path
        _rec("print('world')", "repoA/other.py"),
    ]
    with open(corpus, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")

    stats = dedup_corpus(str(tmp_path))
    assert stats["before"] == 3
    assert stats["removed"] == 1
    assert stats["after"] == 2

    lines = [json.loads(l) for l in open(corpus, encoding="utf-8") if l.strip()]
    assert len(lines) == 2
    # first occurrence kept (repoA)
    assert lines[0]["path"] == "repoA/util.py"


def test_dedup_noop_when_clean(tmp_path):
    corpus = tmp_path / "dataset.jsonl"
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(json.dumps(_rec("aaa", "a.py")) + "\n")
        f.write(json.dumps(_rec("bbb", "b.py")) + "\n")
    stats = dedup_corpus(str(tmp_path))
    assert stats["removed"] == 0


def test_dedup_handles_missing_sha256(tmp_path):
    """Records without sha256 field must still dedup by content."""
    corpus = tmp_path / "dataset.jsonl"
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(json.dumps({"path": "x.py", "content": "same"}) + "\n")
        f.write(json.dumps({"path": "y.py", "content": "same"}) + "\n")
    stats = dedup_corpus(str(tmp_path))
    assert stats["removed"] == 1


def test_dedup_missing_file_safe(tmp_path):
    stats = dedup_corpus(str(tmp_path))
    assert stats == {"before": 0, "after": 0, "removed": 0}


def test_merge_runs_auto_dedup(tmp_path):
    """merge_dataset_into_corpus(dedup_after=True default) cleans the corpus."""
    corpus_dir = tmp_path / "Code_base"
    corpus_dir.mkdir()
    # Pre-seed corpus with a record whose content will also be in the incoming set.
    seed = _rec("shared content", "old/path.py", sha="sha256:AAA")
    with open(corpus_dir / "dataset.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(seed) + "\n")
    # Incoming: same content but different sha256+path -> old keying kept both.
    ds = tmp_path / "incoming.jsonl"
    incoming = [
        _rec("shared content", "new/path.py", sha="sha256:BBB"),
        _rec("other content", "new/other.py", sha="sha256:CCC"),
    ]
    with open(ds, "w", encoding="utf-8") as f:
        for r in incoming:
            f.write(json.dumps(r) + "\n")

    stats = merge_dataset_into_corpus(str(ds), str(corpus_dir), dedup_after=True)
    assert stats["added"] == 2          # old keying accepted both
    assert stats.get("dedup_removed", 0) >= 1  # smart pass caught the dupe

    lines = [json.loads(l) for l in open(corpus_dir / "dataset.jsonl", encoding="utf-8") if l.strip()]
    contents = [r["content"] for r in lines]
    assert contents.count("shared content") == 1  # only ONE copy survives


def test_merge_can_disable_auto_dedup(tmp_path):
    corpus_dir = tmp_path / "Code_base"
    corpus_dir.mkdir()
    seed = _rec("shared content", "old/path.py", sha="sha256:AAA")
    with open(corpus_dir / "dataset.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(seed) + "\n")
    ds = tmp_path / "incoming.jsonl"
    with open(ds, "w", encoding="utf-8") as f:
        f.write(json.dumps(_rec("shared content", "new/path.py", sha="sha256:BBB")) + "\n")

    stats = merge_dataset_into_corpus(str(ds), str(corpus_dir), dedup_after=False)
    assert "dedup_removed" not in stats  # opt-out respected
