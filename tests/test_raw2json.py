"""Offline tests for captn.workers.raw2json (raw files -> structured JSON).

No network, no Ollama, no live corpus writes. Builds a tiny temp tree and
asserts the converter produces correct, schema-compatible records.
"""
import json
import os
import sys

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from captn.workers import raw2json as m  # noqa: E402


def _make_tree(root):
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hello')\n")
    with open(os.path.join(root, "b.md"), "w", encoding="utf-8") as fh:
        fh.write("# Title\n\nbody\n")
    with open(os.path.join(root, "c.txt"), "w", encoding="utf-8") as fh:
        fh.write("plain text\n")
    # A binary file -> metadata-only record, no content.
    with open(os.path.join(root, "d.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02\xff")
    # Empty file -> skipped.
    open(os.path.join(root, "e.py"), "w", encoding="utf-8").close()
    # A nested dir with one file.
    sub = os.path.join(root, "sub")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "f.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")


def test_convert_basic(tmp_path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    _make_tree(str(src))
    res = m.convert_raw_directory(str(src), out_dir=str(out))
    # a.py, b.md, c.txt, d.bin, sub/f.py included = 5 ; e.py skipped (empty) = 1
    assert len(res.files_included) == 5
    assert len(res.files_skipped) == 1
    assert res.dataset_jsonl is not None
    assert os.path.exists(res.dataset_jsonl)

    records = [json.loads(l) for l in open(res.dataset_jsonl, encoding="utf-8")]
    assert len(records) == 5
    # Schema compatibility with the crawler output.
    assert all(r["schema_version"] == "captn.crawler.dataset/1.0" for r in records)
    assert all(r["source"] == "raw" for r in records)
    # Binary file has is_text False and no content.
    bin_rec = [r for r in records if r["path"].endswith("d.bin")][0]
    assert bin_rec["is_text"] is False
    assert bin_rec["content"] is None
    # Text file has content + line_count + sha256 + git_sha (or None).
    py_rec = [r for r in records if r["path"] == "a.py"][0]
    assert py_rec["is_text"] is True
    assert py_rec["content"] == "print('hello')\n"
    assert py_rec["language"] == "python"
    assert len(py_rec["sha256"]) == 64


def test_include_ext_filter(tmp_path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    _make_tree(str(src))
    res = m.convert_raw_directory(str(src), out_dir=str(out), include_ext=[".py"])
    # Only a.py and sub/f.py match .py
    assert len(res.files_included) == 2
    assert all(r["ext"] == ".py" for r in
               [json.loads(l) for l in open(res.dataset_jsonl, encoding="utf-8")])


def test_progress_file(tmp_path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    _make_tree(str(src))
    pf = tmp_path / "prog.json"
    res = m.convert_raw_directory(
        str(src), out_dir=str(out), progress_file=str(pf), progress_total=6
    )
    data = json.loads(pf.read_text(encoding="utf-8"))
    assert data["done"] == 6
    assert data["total"] == 6
    assert "last" in data


def test_non_directory_raises():
    with pytest.raises(ValueError):
        m.convert_raw_directory("C:/does/not/exist/at/all")
