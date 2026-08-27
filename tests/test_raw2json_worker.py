"""Tests for captn.workers.raw2json_worker (the raw->JSON Captn Plugin).

Drives the worker through a real MessageBus: success path, error paths
(missing root, bad directory), and the feed_corpus merge path. No network,
no Ollama.
"""

import os
import tempfile

from captn.runtime.base import Message
from captn.runtime.runtime import MessageBus
from captn.workers.raw2json_worker import Raw2JsonWorker


def _make_tree():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.py"), "w", encoding="utf-8") as fh:
        fh.write("def f():\n    return 1\n")
    with open(os.path.join(d, "notes.md"), "w", encoding="utf-8") as fh:
        fh.write("# Hi\n")
    with open(os.path.join(d, "data.bin"), "wb") as fh:
        fh.write(b"\x00\x01\x02")
    return d


def _collect(bus):
    received = []
    bus.subscribe("captn", lambda m: received.append(m))
    return received


def test_worker_success():
    d = _make_tree()
    bus = MessageBus()
    received = _collect(bus)
    w = Raw2JsonWorker(bus)
    assert w.initialize() is True
    w.execute(Message(sender="test", destination="raw2json", type="task",
                       payload={"task_id": "T1", "root": d,
                                 "include_ext": [".py", ".md"],
                                 "export_dataset": True}))
    assert received and received[-1].type == "response"
    md = received[-1].payload["data"]["metadata"]
    assert len(md["files_included"]) >= 2
    assert os.path.exists(md["dataset_jsonl"])
    assert len(md["files_skipped"]) >= 1  # the binary is skipped


def test_worker_error_missing_root():
    bus = MessageBus()
    received = _collect(bus)
    w = Raw2JsonWorker(bus)
    w.execute(Message(sender="test", destination="raw2json", type="task",
                      payload={"task_id": "T2"}))
    assert received and received[-1].type == "error"
    assert "root" in received[-1].payload["error_message"].lower()


def test_worker_error_bad_dir():
    bus = MessageBus()
    received = _collect(bus)
    w = Raw2JsonWorker(bus)
    w.execute(Message(sender="test", destination="raw2json", type="task",
                      payload={"task_id": "T3", "root": "C:/no/such/dir"}))
    assert received and received[-1].type == "error"


def test_worker_feed_corpus():
    d = _make_tree()
    corpus = tempfile.mkdtemp()
    bus = MessageBus()
    received = _collect(bus)
    w = Raw2JsonWorker(bus)
    w.execute(Message(sender="test", destination="raw2json", type="task",
                      payload={"task_id": "T4", "root": d,
                                "include_ext": [".py"], "feed_corpus": True,
                                "corpus_dir": corpus}))
    assert received and received[-1].type == "response"
    assert os.path.exists(os.path.join(corpus, "dataset.jsonl"))
