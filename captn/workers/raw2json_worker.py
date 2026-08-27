#!/usr/bin/env python3
"""Captn worker: transform raw local files into structured JSON.

This worker wraps :mod:`captn.workers.raw2json` as a first-class Captn
``Plugin`` so it can be driven by the message bus (same shape as
``CrawlerWorker``). It reads a directory of raw source/doc/config files and
emits a structured dataset (``captn.crawler.dataset/1.0`` schema) compatible
with the learning corpus and the autogen loop.

Security notes:
    - Read-only scan: never executes or writes inside the scanned tree.
    - Output is written to ``out_dir`` (default ``<root>/_converted``); the
      tool never overwrites the input.
    - ``feed_corpus`` writes to an explicit corpus dir only; it never writes
      secrets, API keys, or arbitrary paths supplied by a message.
    - All input paths are resolved to absolute and validated before use.
    - Errors are caught per-message so a bad payload never kills the bus.

Message payload (all optional except ``root``)::

    {
      "task_id": "1003",
      "root": "Code_base",                # directory of raw files (REQUIRED)
      "include_ext": [".py", ".rst"],     # optional allow-list
      "out_dir": "./_converted",          # optional output dir
      "export_dataset": true,             # emit dataset.jsonl/json/manifest
      "feed_corpus": false,               # merge result into a corpus dir
      "corpus_dir": "Code_base",          # used only if feed_corpus
      "progress_file": null,              # optional path for live progress
      "max_bytes": 512000,
      "max_files": 20000
    }
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("Workers")


class Raw2JsonWorker:
    """Captn ``Plugin`` that converts raw files into structured JSON.

    Inherits the same informal contract as ``CrawlerWorker``:
    ``name``, ``__init__(bus)``, ``initialize()``, ``execute(message)``,
    ``shutdown()``. Publishes a ``UniversalData`` response to ``destination="captn"``.
    """

    name = "raw2json"

    def __init__(self, bus=None):
        self.bus = bus
        self.is_active = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def initialize(self) -> bool:
        # No external connection needed; verify the module imports.
        try:
            from captn.workers import raw2json  # noqa: F401
        except Exception as e:  # pragma: no cover - defensive
            logger.error(f"[{self.name}] Init failed: {e}")
            return False
        logger.info(f"[{self.name}] Ready (raw -> structured JSON).")
        self.is_active = True
        return True

    # ------------------------------------------------------------------ #
    # Message handling
    # ------------------------------------------------------------------ #
    def execute(self, message) -> None:
        from captn.runtime.base import Message  # local import avoids cycles
        from captn.runtime.schemas import UniversalData

        payload = getattr(message, "payload", {}) or {}
        # The orchestrator builds Task(**payload); worker-specific args
        # (root, include_ext, ...) may live in a nested `payload` dict.
        nested = payload.get("payload")
        if isinstance(nested, dict):
            merged = {**nested, **payload}
        else:
            merged = payload
        task_id = payload.get("task_id")

        root = merged.get("root")
        if not root:
            self._error(task_id, "Missing 'root' in payload (directory of raw files).")
            return

        root_abs = os.path.abspath(root)
        if not os.path.isdir(root_abs):
            self._error(task_id, f"root is not a directory or does not exist: {root_abs}")
            return

        include_ext = merged.get("include_ext")
        if include_ext is not None and not isinstance(include_ext, (list, tuple)):
            self._error(task_id, "'include_ext' must be a list of extensions (e.g. ['.py', '.md']).")
            return

        out_dir = merged.get("out_dir")
        feed_corpus = bool(merged.get("feed_corpus", False))
        corpus_dir = merged.get("corpus_dir")
        progress_file = merged.get("progress_file")

        try:
            from captn.workers.raw2json import convert_raw_directory

            result = convert_raw_directory(
                root_abs,
                out_dir=out_dir,
                include_ext=list(include_ext) if include_ext else None,
                export_dataset=bool(payload.get("export_dataset", True)),
                progress_file=progress_file,
                max_bytes=int(payload.get("max_bytes", 512_000)),
                max_files=int(payload.get("max_files", 20_000)),
            )
        except Exception as e:  # never kill the bus thread
            self._error(task_id, f"Unexpected raw2json error: {type(e).__name__}: {e}")
            return

        # Optional: feed the structured output into a learning corpus.
        fed = 0
        corpus_jsonl = None
        if feed_corpus:
            try:
                from captn.workers.crawler import merge_dataset_into_corpus

                corpus_target = os.path.abspath(corpus_dir) if corpus_dir else root_abs
                corpus_jsonl = merge_dataset_into_corpus(
                    result.dataset_jsonl,
                    corpus_target,
                    corpus_jsonl_name="dataset.jsonl",
                )
                fed = result.files_included
            except Exception as e:
                self._error(task_id, f"feed_corpus failed: {type(e).__name__}: {e}")
                # Still report the conversion result below.

        data = UniversalData(
            source_type="raw",
            content={"raw_conversion_result": result.to_dict()},
            metadata={
                "root": result.root,
                "files_included": result.files_included,
                "files_skipped": result.files_skipped,
                "files_error": result.errors,
                "dataset_jsonl": result.dataset_jsonl,
                "dataset_json": result.dataset_json,
                "manifest_json": result.manifest,
                "feed_corpus": feed_corpus,
                "corpus_jsonl": corpus_jsonl,
                "fed_records": fed,
            },
        )

        if self.bus is not None:
            # Pass the structured dataset forward as the next step's input.
            # Downstream steps (e.g. extractor) consume `file_path`, so we hand
            # them the dataset JSONL - the structured artifact of this worker.
            self.bus.publish(Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "valid": True,
                    "data": data.__dict__,
                    "file_path": result.dataset_jsonl,
                    "written_paths": list(result.files_written),
                    "dataset_jsonl": result.dataset_jsonl,
                    "dataset_json": result.dataset_json,
                    "manifest_json": result.manifest,
                    "fed_records": fed,
                },
            ))
        else:
            logger.info(f"[{self.name}] (no bus) {result.to_dict()}")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
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
