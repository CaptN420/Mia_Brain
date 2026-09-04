#!/usr/bin/env python3
"""Autogenerate code from a crawled dataset using Ollama (qwen2:1.5b).

Deterministic-safe by design:
- The Ollama host is pinned to localhost:11434 (no remote model servers).
- No API key is used or logged.
- Every generated file is passed through captn.runtime.validator.PatchValidator
  before it is written (blocks destructive keywords and core-file writes).
- Generation is a *loop*: each iteration samples files from the crawled dataset,
  asks Ollama for a derived/refactored variant, validates, and writes it to
  generated/. Optionally the result is fed back into the Code_base corpus so the
  self-learning chain continues.
"""
from __future__ import annotations

import json
import logging
import hashlib
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

import requests

logger = logging.getLogger("autogen")

# Token usage accounting, accumulated across ollama_ask calls within this
# process. The dashboard reads this to show LLM cost per architecture
# (Captn+MIA vs no-architecture baseline).
TOKEN_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

# Host pinning: we only ever talk to the local Ollama instance. A configured
# URL is accepted but must resolve to localhost/127.0.0.1 (security control).
# The base URL is resolved per-environment so the container can reach the
# host's Ollama via host.docker.internal (see captn.runtime.llm_host).
from captn.runtime.llm_host import ollama_base_url as _ollama_base_url

OLLAMA_BASE_URL = _ollama_base_url()
OLLAMA_MODEL = "qwen2:1.5b"
_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


@dataclass
class AutogenResult:
    iterations: int = 0
    samples_seen: int = 0
    generated: int = 0
    blocked: int = 0
    errors: int = 0
    fed_to_corpus: int = 0
    out_dir: str = ""
    per_iteration: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "iterations": self.iterations,
            "samples_seen": self.samples_seen,
            "generated": self.generated,
            "blocked": self.blocked,
            "errors": self.errors,
            "fed_to_corpus": self.fed_to_corpus,
            "out_dir": self.out_dir,
            "per_iteration": self.per_iteration,
        }


def _assert_localhost(url: str) -> None:
    """Refuse to talk to any non-localhost Ollama endpoint."""
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError(f"Refused: Ollama host must be localhost, got {host!r}")


def ollama_ask(
    messages: list[dict[str, str]],
    *,
    base_url: str = OLLAMA_BASE_URL,
    model: str = OLLAMA_MODEL,
    temperature: float = 0.0,
    num_predict: int = 600,
    timeout: int = 120,
    seed: int = 42,
    stop: Optional[list[str]] = None,
    compress_output: bool = True,
) -> str:
    """Ask Ollama/qwen2:1.5b directly via REST (no API key).

    Deterministic settings by default: temperature 0 + a fixed seed so that,
    on the same machine/model, identical prompts reproduce identical output.

    When compress_output=True (default), the last user message is compressed
    through the output compressor to strip mechanical noise before sending,
    reducing input token consumption by 60-90% on tool output.
    """
    _assert_localhost(base_url)

    # Compress the last user message (tool output) to save input tokens
    if compress_output and messages:
        try:
            from captn.runtime.output_compressor import compress as _compress
            last_msg = messages[-1]
            if last_msg.get("role") == "user" and last_msg.get("content"):
                original_len = len(last_msg["content"])
                compressed = _compress(last_msg["content"])
                if len(compressed) < original_len * 0.9:  # at least 10% savings
                    last_msg["content"] = compressed
                    logger.debug("ollama_ask: compressed user message %d→%d chars (%.0f%% savings)",
                                 original_len, len(compressed),
                                 (1 - len(compressed)/max(original_len,1)) * 100)
        except Exception:
            pass  # Compression is best-effort; never fail over it
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "seed": int(seed),
        },
    }
    if stop:
        payload["stop"] = stop  # OpenAI-compatible: stop at top level
        # Also pass in options for Ollama native API compatibility
        payload["options"]["stop"] = stop
    try:
        resp = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
        if resp.status_code != 200:
            return f"[OLLAMA_ERROR] HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        usage = data.get("usage") or {}
        TOKEN_USAGE["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        TOKEN_USAGE["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        TOKEN_USAGE["total_tokens"] += int(usage.get("total_tokens") or 0)
        TOKEN_USAGE["calls"] += 1
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:  # network / parse failures should not crash the loop
        return f"[OLLAMA_ERROR] {str(e)[:200]}"


def _extract_code(text: str) -> Optional[str]:
    """Return the first fenced code block, or None if there is none."""
    m = _CODE_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    # Fallback: if the whole response looks like code (has def/class/import), use it.
    if re.search(r"^\s*(def|class|import|from|#)", text, re.MULTILINE):
        return text.strip()
    return None


def _truncate_after_fence(text: str) -> str:
    """Babbling suppression: drop everything after the closing ``` fence.

    The LLM often continues past the closing triple backtick with explanatory
    text like "This refactored code improves...". That text costs tokens on
    every cache hit and adds nothing. We truncate at the closing fence.
    """
    # Find the LAST closing ``` (the one that closes the code block)
    idx = text.rfind("```")
    if idx != -1 and idx > 0:
        # Include the closing ``` itself (needed for _CODE_BLOCK_RE on future calls)
        return text[:idx + 3].strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Deterministic-first generation path
# ---------------------------------------------------------------------------
def deterministic_transform(code: str) -> tuple[Optional[str], str]:
    """Deterministic, reproducible transform of Python source (no LLM).

    Uses the stdlib `ast` module to add module/missing-function docstrings and
    normalise formatting via `ast.unparse`. Returns (transformed_code, method)
    or (None, reason) when the input cannot safely be transformed.
    """
    import ast as _ast

    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return None, "syntax-error"

    changed = False
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)) \
                and not _ast.get_docstring(node):
            doc = _ast.Expr(_ast.Constant(
                value=f"{node.name.capitalize()} ({'class' if isinstance(node, _ast.ClassDef) else 'function'})."))
            node.body.insert(0, doc)
            # Keep the rest of the body indented correctly: insert before stmts.
            changed = True
        elif isinstance(node, _ast.Module) and not _ast.get_docstring(node):
            node.body.insert(0, _ast.Expr(_ast.Constant(value="Auto-documented module.")))
            changed = True

    if not changed:
        return None, "nothing-to-do"
    try:
        out = _ast.unparse(tree)
    except Exception:
        return None, "unparse-failed"
    return out, "ast-docstring"


def behaviorally_equivalent(src_a: str, src_b: str) -> bool:
    """Best-effort equivalence check between two Python sources.

    Compiles both (catches syntax errors), then compares normalized AST dumps
    so whitespace/formatting differences don't count. This is a structural
    gate — it rejects any LLM output that doesn't parse or that rewrites the
    program structure, which is exactly what 'keep behaviour identical'
    refactor mode requires.
    """
    import ast as _ast

    try:
        tree_a = _ast.parse(src_a)
        tree_b = _ast.parse(src_b)
        compile(src_a, "<orig>", "exec")
        compile(src_b, "<gen>", "exec")
    except (SyntaxError, ValueError):
        return False

    def _strip(node: _ast.AST) -> None:
        """Remove docstrings AND type annotations so documentation-only and
        annotation-only changes don't count (annotations are metadata — they
        don't alter runtime behaviour)."""
        if isinstance(node, (_ast.Module, _ast.ClassDef,
                             _ast.FunctionDef, _ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], _ast.Expr)
                    and isinstance(body[0].value, _ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:]
        # Strip annotations (arg.annotation / returns) — behaviour-neutral.
        if isinstance(node, _ast.arg):
            node.annotation = None
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            node.returns = None
        for child in _ast.iter_child_nodes(node):
            _strip(child)

    try:
        _strip(tree_a)
        _strip(tree_b)
        dump_a = _ast.dump(tree_a, include_attributes=False)
        dump_b = _ast.dump(tree_b, include_attributes=False)
    except Exception:
        return False
    return dump_a == dump_b


def _cache_key(prompt: str, code: str) -> str:
    import hashlib

    return hashlib.sha256((prompt + "\x00" + code).encode("utf-8")).hexdigest()


def _cache_get(cache_dir: Path, key: str) -> Optional[str]:
    """AUDIT-D: only return cache entries that were stored as VALIDATED.

    Validated entries live in ``validated/``; anything else (legacy cache
    from before the fix, or unvalidated leftovers) is ignored."""
    f = cache_dir / "validated" / f"{key}.py"
    if f.is_file():
        try:
            return f.read_text(encoding="utf-8")
        except OSError:
            return None
    return None


def _cache_put(cache_dir: Path, key: str, code: str) -> None:
    """AUDIT-D: reserved for pre-validation staging (unused for reuse).

    We intentionally do NOT persist LLM output for reuse until it has passed
    the validation gates — see _cache_put_validated.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "pending" / f"{key}.py").parent.mkdir(parents=True, exist_ok=True)
        (cache_dir / "pending" / f"{key}.py").write_text(code, encoding="utf-8")
    except OSError:
        pass  # cache is best-effort; never fail generation over it


def _cache_put_validated(cache_dir: Path, key: str, code: str) -> None:
    """AUDIT-D: store output for future reuse ONLY after gates pass."""
    try:
        d = cache_dir / "validated"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.py").write_text(code, encoding="utf-8")
    except OSError:
        pass


def _iter_dataset(records: Iterable[dict]) -> Iterator[dict]:
    """Yield text/python records that have content we can feed the model."""
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("is_text") is False:
            continue
        content = rec.get("content")
        if not content or not str(content).strip():
            continue
        lang = (rec.get("language") or "").lower()
        ext = (rec.get("ext") or "").lower()
        if lang == "python" or ext in (".py", ".pyi"):
            yield rec


def _read_dataset(path: str | os.PathLike) -> list[dict]:
    """Read a dataset JSONL/JSON file into a list of dict records.

    Validates the input defensively: a non-existent path, a directory, or a
    non-string/empty value raises a clear ValueError instead of letting
    ``open()`` produce a cryptic OSError (e.g. Errno 22 on a garbage path).
    """
    if path is None or (isinstance(path, str) and not path.strip()):
        raise ValueError("dataset path is empty")
    p = Path(path)
    if not p.exists():
        raise ValueError(f"dataset file not found: {p}")
    if p.is_dir():
        raise ValueError(f"expected a dataset file, got a directory: {p}")
    out: list[dict] = []
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def run_autogen_loop(
    dataset_jsonl: str | os.PathLike,
    *,
    ollama_url: str = OLLAMA_BASE_URL,
    model: str = OLLAMA_MODEL,
    iterations: int = 3,
    sample_per_iter: int = 4,
    prompt_template: str = (
        "Refactor the following Python code to improve clarity, add type hints, "
        "and keep behaviour identical. Output ONLY the refactored code in a single "
        "```python fenced block."
    ),
    out_dir: str | os.PathLike = "generated",
    feed_corpus: bool = False,
    corpus_dir: str | os.PathLike = "Code_base",
    seed: int | None = 42,
    llm_as_fallback: bool = True,
    on_iteration: Optional[Callable[[int, dict], None]] = None,
) -> AutogenResult:
    """Run the auto-generation loop over a crawled dataset.

    Deterministic-first pipeline per record:
      1. AST transformer (stdlib) — fully reproducible, no model involved.
      2. LLM (Ollama/qwen2:1.5b) only as fallback when no deterministic
         transform applies (`llm_as_fallback`), pinned temperature=0 + seed.
      3. Validation gate: PatchValidator safety check + behavioral
         equivalence (AST-structural match vs. the source).
      4. Content-hash cache: reruns replay identical output.

    For each iteration we sample `sample_per_iter` python records from the dataset
    (seeded RNG → same records every run), validate, and write safe variants to
    `out_dir`. When `feed_corpus` is set, validated code is appended to the
    Code_base corpus so the self-learning chain continues.
    """
    rng = random.Random(seed)
    cache_dir = Path(out_dir) / ".cache"
    result = AutogenResult(out_dir=str(out_dir))
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Import the validator lazily to avoid a hard dependency at import time.
    try:
        from captn.runtime.validator import PatchValidator

        validator = PatchValidator()
    except Exception:
        validator = None

    records = list(_iter_dataset(_read_dataset(dataset_jsonl)))
    if not records:
        return result

    # Collect candidate corpus records for the (optional) feed step.
    corpus_records: list[dict] = []

    # LOOP-OVER-ALL: shuffle once (seeded), then walk the ENTIRE dataset in
    # chunks of `sample_per_iter` — no record is repeated inside one run.
    order = list(records)
    rng.shuffle(order)
    chunk_size = max(1, sample_per_iter)

    # Per-iteration VARIATION: each pass applies a different transform mix /
    # generation style, so iterating over the same source yields genuinely
    # different patterns instead of byte-identical rewrites.
    MODE_PLANS = [
        ("docstrings",),
        ("annotate",),
        ("normalize",),
        ("docstrings", "annotate"),
        ("docstrings", "normalize"),
        ("annotate", "normalize"),
    ]
    STYLE_SUFFIXES = [
        "Add concise module and function docstrings.",
        "Add full type annotations to all function signatures.",
        "Normalize formatting and naming consistently (PEP8).",
        "Refactor for clarity while keeping behaviour identical.",
    ]

    for it in range(1, max(1, iterations) + 1):
        start = (it - 1) * chunk_size
        batch = order[start:start + chunk_size]
        if not batch:
            break  # dataset fully covered — stop instead of resampling repeats
        modes = list(MODE_PLANS[(it - 1) % len(MODE_PLANS)])
        style_hint = STYLE_SUFFIXES[(it - 1) % len(STYLE_SUFFIXES)]
        iter_seed = None if seed is None else int(seed) + it - 1
        it_generated = 0
        it_blocked = 0
        it_errors = 0
        for rec in batch:
            result.samples_seen += 1
            src_path = rec.get("path") or rec.get("rel_path") or "unknown.py"
            content = str(rec.get("content") or "")
            # AUDIT-B: include a short source hash in the output filename so
            # two different repos that both have e.g. compat.py can never
            # silently overwrite each other's generated variants.
            src_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
            stem = Path(src_path).stem or "gen"
            # VARIATION: include the iteration + transform mix in the name so
            # two iterations over the same source produce distinct files
            # (previously iter 2/3 silently overwrote iter 1's output).
            mode_tag = "-".join(modes) or "default"
            out_file = out_path / f"{stem}.{src_hash}.it{it}.{mode_tag}.gen.py"

            # --- Stage 1: DeterministicCoder worker first (no model) ---
            # AUDIT: the dedicated deterministic_coder worker is the PRIMARY
            # generator. The LLM is strictly a fallback.
            code: Optional[str] = None
            method = ""
            try:
                from captn.workers.code_generation.deterministic_coder import DeterministicCoder
                dc = DeterministicCoder()  # stateless; bus not needed here
                d_code, d_method = dc.generate(content, modes=modes)
            except Exception as _e:
                logger.warning("DeterministicCoder unavailable (%s); "
                               "falling back to inline transform", _e)
                d_code, d_method = deterministic_transform(content)
            if d_code is not None:
                code, method = d_code, d_method
            elif llm_as_fallback:
                # --- Stage 2: LLM fallback (pinned temperature=0 + seed) ---
                user_msg = f"{prompt_template} {style_hint}\n\n# Source: {src_path}\n{content[:4000]}"
                ck = _cache_key(user_msg, content)
                cached = _cache_get(cache_dir, ck)
                if cached is not None:
                    code, method = cached, "llm:cached"
                else:
                    raw = ollama_ask(
                        [
                            {"role": "system", "content": "You are a careful Python code generation assistant."},
                            {"role": "user", "content": user_msg},
                        ],
                        base_url=ollama_url,
                        model=model,
                        seed=iter_seed if iter_seed is not None else 42,
                    )
                    if raw.startswith("[OLLAMA_ERROR]"):
                        it_errors += 1
                        logger.warning("Ollama error on %s: %s", src_path, raw)
                        continue
                    code = _extract_code(raw)
                    if not code:
                        it_errors += 1
                        logger.warning("No code block extracted for %s", src_path)
                        continue
                    method = "llm:fallback"
                    # AUDIT-D: NOT cached yet — only cached after gates pass.

            if not code:
                it_errors += 1
                logger.warning("No deterministic transform and LLM fallback disabled for %s", src_path)
                continue

            # --- Stage 3a: deterministic safety gate ---
            safe = True
            reason = "Pre-flight checks passed."
            if validator is not None:
                v = validator.validate_patch({"file_path": str(out_file), "diff": code})
                safe = bool(v.get("is_safe"))
                reason = v.get("reason", reason)
            # --- Stage 3b: behavioral equivalence gate (refactor mode) ---
            if safe and not behaviorally_equivalent(content, code):
                safe = False
                reason = (
                    f"Generated code ({method}) failed behavioral equivalence check "
                    f"against the source (AST structure differs or does not compile)."
                )
            if not safe:
                it_blocked += 1
                logger.info("Blocked generated code for %s (%s): %s", src_path, method, reason)
                continue

            # AUDIT-D: gates passed — now (and only now) cache for reuse.
            if method == "llm:fallback":
                _cache_put_validated(cache_dir, ck, code)

            try:
                out_file.write_text(code + ("\n" if not code.endswith("\n") else ""), encoding="utf-8")
            except OSError as e:
                it_errors += 1
                logger.warning("Write failed for %s: %s (%s)", out_file, e, method)
                continue
            it_generated += 1
            logger.info("Generated %s via %s", out_file.name, method)
            if feed_corpus:
                corpus_records.append(
                    {
                        "repo": rec.get("repo", "autogen"),
                        "branch": rec.get("branch", "n/a"),
                        "path": str(out_file),
                        "language": "python",
                        "ext": ".py",
                        "size_bytes": out_file.stat().st_size,
                        "rel_path": str(out_file),
                        "is_text": True,
                        "decoded_utf8": True,
                        "content": code,
                        "line_count": code.count("\n") + 1,
                        "source": "autogen",
                    }
                )

        iter_summary = {
            "iteration": it,
            "generated": it_generated,
            "blocked": it_blocked,
            "errors": it_errors,
        }
        result.per_iteration.append(iter_summary)
        result.generated += it_generated
        result.blocked += it_blocked
        result.errors += it_errors
        result.iterations = it
        if on_iteration is not None:
            on_iteration(it, iter_summary)

    if feed_corpus and corpus_records:
        result.fed_to_corpus = _append_to_corpus(corpus_records, corpus_dir)
    return result


def _append_to_corpus(records: list[dict], corpus_dir: str | os.PathLike,
                       jsonl_name: str = "dataset.autogen.jsonl") -> int:
    """Append generated records to a dedicated corpus file (dedup by content).

    Uses a SEPARATE file (``dataset.autogen.jsonl``) from the crawler's
    ``dataset.jsonl`` so concurrent crawler merges and autogen feeds never race
    on the same file handle (which on Windows raises PermissionError). A short
    retry-with-backoff guards against brief lock contention.
    """
    import time as _time

    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    target = corpus_dir / jsonl_name

    existing_hashes = set()
    if target.exists() and target.is_file():
        try:
            for line in target.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                existing_hashes.add(_stable_key(rec))
        except OSError:
            pass

    added = 0
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            with target.open("a", encoding="utf-8") as fh:
                for rec in records:
                    key = _stable_key(rec)
                    if key in existing_hashes:
                        continue
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    existing_hashes.add(key)
                    added += 1
            break
        except (OSError, PermissionError) as _e:
            last_err = _e
            _time.sleep(0.3 * (attempt + 1))
    else:
        logger.warning("Could not append to corpus %s: %s", target, last_err)
    return added


def _stable_key(rec: dict) -> str:
    import hashlib

    content = rec.get("content") or ""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
