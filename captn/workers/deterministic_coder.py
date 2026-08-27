"""DeterministicCoder - The deterministic code-generation worker.

Role in the Captn architecture (deterministic-first):
    This worker produces derived Python code WITHOUT any LLM. It is the
    primary code source for the autogen pipeline; the LLM is only a
    last-resort fallback (cfg.llm_as_fallback).

What it does (all transforms are pure AST -> reproducible byte-for-byte):
    1. docstrings   : add module/class/function docstrings where missing
    2. annotate     : add TODO-free type-hint scaffolds for untyped function
                      parameters/returns using conservative inference
                      (Any is never guessed; unknown types stay unannotated)
    3. normalize    : re-format via ast.unparse (stable formatting)
    4. guard        : wrap top-level mutable default arguments... (not done -
                      behaviour must stay identical, see gate below)

Safety gates applied BEFORE writing anything:
    - PatchValidator  : destructive-op / core-file protection
    - behaviorally_equivalent : generated AST (minus docstrings/annotations)
      must be structurally identical to the source, guaranteeing the
      transform can never change program behaviour.

Message contract (bus):
    request : Message(type="task", destination="deterministic_coder", payload={
                  "task_id": str,
                  "code": str,                # source to transform
                  "src_path": str,            # original path (for naming/provenance)
                  "modes": ["docstrings","annotate","normalize"],  # optional subset
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "deterministic_coder",
                  "valid": bool, "code": str|None, "method": str,
                  "out_path": str|None, "error_message": str (when invalid)
              })
"""
from __future__ import annotations

import ast
import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from captn.runtime.base import Message, Plugin
from captn.runtime.validator import PatchValidator
from captn.workers.autogen import behaviorally_equivalent

logger = logging.getLogger("Runtime")


class DeterministicCoder(Plugin):
    """Codeur 100% déterministe : mêmes entrées -> mêmes sorties, jamais de LLM."""

    name = "deterministic_coder"

    def __init__(self, bus=None):
        self.bus = bus
        self.validator = PatchValidator()

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing deterministic code generator "
                    f"(no LLM, AST-only transforms).")
        return True

    def shutdown(self) -> bool:
        return True

    # ------------------------------------------------------------------ #
    # Transform passes                                                    #
    # ------------------------------------------------------------------ #

    def _pass_docstrings(self, tree: ast.Module) -> bool:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and not ast.get_docstring(node):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                doc = ast.Expr(ast.Constant(value=f"{node.name.capitalize()} ({kind})."))
                node.body.insert(0, doc)
                changed = True
            elif isinstance(node, ast.Module) and not ast.get_docstring(node):
                node.body.insert(0, ast.Expr(ast.Constant(value="Auto-documented module.")))
                changed = True
        return changed

    def _pass_annotate(self, tree: ast.Module) -> bool:
        """Add conservative type annotations.

        Only annotates when the type is UNAMBIGUOUS from a literal default or
        a trivially-safe body pattern. Anything uncertain stays unannotated
        (never guesses Any) so the result stays honest and stable.
        """
        changed = False

        def infer_from_default(default: ast.expr) -> Optional[str]:
            if isinstance(default, ast.Constant):
                if default.value is None:
                    return None  # Optional[...] unknown -> skip
                if isinstance(default.value, bool):
                    return "bool"
                if isinstance(default.value, int):
                    return "int"
                if isinstance(default.value, float):
                    return "float"
                if isinstance(default.value, str):
                    return "str"
                if isinstance(default.value, bytes):
                    return "bytes"
            if isinstance(default, (ast.List,)):
                return "list"
            if isinstance(default, (ast.Dict,)):
                return "dict"
            if isinstance(default, (ast.Tuple,)):
                return "tuple"
            if isinstance(default, ast.Set):
                return "set"
            return None

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # --- parameters with literal defaults ---
            args = list(node.args.posonlyargs) + list(node.args.args) + \
                list(node.args.kwonlyargs)
            defaults = list(node.args.defaults)
            kw_defaults = [d for d in node.args.kw_defaults]
            # positional defaults align to the tail of posonly+args
            offset = len(args) - len(defaults)

            def arg_name(a: ast.arg) -> str:
                return a.arg

            for i, a in enumerate(args):
                if a.annotation is not None:
                    continue
                idx = i - offset
                inferred = None
                if 0 <= idx < len(defaults) and defaults[idx] is not None:
                    inferred = infer_from_default(defaults[idx])
                elif a in node.args.kwonlyargs:
                    j = node.args.kwonlyargs.index(a)
                    if j < len(kw_defaults) and kw_defaults[j] is not None:
                        inferred = infer_from_default(kw_defaults[j])
                if inferred:
                    a.annotation = ast.Name(id=inferred, ctx=ast.Load())
                    changed = True

            # --- return annotation for trivial bodies ---
            if node.returns is None:
                rets = [s for s in ast.walk(node)
                        if isinstance(s, ast.Return) and s.value is not None]
                if rets and all(
                    isinstance(r.value, ast.Constant) and r.value.value is None
                    for r in rets
                ) and not any(isinstance(s, ast.Yield) for s in ast.walk(node)):
                    # every explicit return returns None-ish constant
                    node.returns = ast.Constant(value=None)
                    changed = True

        return changed

    def _pass_normalize(self, tree: ast.Module) -> bool:
        # ast.unparse always normalizes; nothing extra to mark.
        return False

    PASSES = {
        "docstrings": "_pass_docstrings",
        "annotate": "_pass_annotate",
        "normalize": "_pass_normalize",
    }

    # ------------------------------------------------------------------ #
    # Core generation                                                     #
    # ------------------------------------------------------------------ #

    def generate(
        self,
        code: str,
        modes: Optional[List[str]] = None,
    ) -> Tuple[Optional[str], str]:
        """Transform ``code`` deterministically.

        Returns (generated_code, method) or (None, reason).
        """
        modes = modes or ["docstrings", "annotate", "normalize"]
        unknown = [m for m in modes if m not in self.PASSES]
        if unknown:
            return None, f"unknown-modes:{','.join(unknown)}"

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None, "syntax-error"

        changed = False
        for m in modes:
            fn = getattr(self, self.PASSES[m])
            try:
                if fn(tree):
                    changed = True
            except Exception as e:  # a pass must never crash the worker
                logger.warning(f"[{self.name}] pass {m} failed: {e}")

        if not changed and "normalize" not in modes:
            return None, "nothing-to-do"

        try:
            out = ast.unparse(tree)
        except Exception:
            return None, "unparse-failed"

        # Behavioural identity gate (ignores docstrings/annotations by design).
        if not behaviorally_equivalent(code, out):
            return None, "equivalence-failed"

        method = "deterministic:" + "+".join(modes)
        return out, method

    def generate_to_file(
        self,
        code: str,
        src_path: str,
        out_dir: str | os.PathLike,
        modes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate + validate + write atomically. Returns a summary dict."""
        summary: Dict[str, Any] = {
            "valid": False, "code": None, "method": "", "out_path": None,
            "error_message": "",
        }
        gen, method = self.generate(code, modes=modes)
        if gen is None:
            summary["error_message"] = method
            return summary

        src_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
        stem = Path(src_path).stem or "gen"
        out_path = Path(out_dir) / f"{stem}.{src_hash}.dc.py"

        patch = {"file_path": str(out_path), "diff": gen}
        v = self.validator.validate_patch(patch)
        if not v.get("is_safe"):
            summary["error_message"] = f"Safety Violation: {v.get('reason')}"
            return summary

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(out_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(gen + ("" if gen.endswith("\n") else "\n"))
            os.replace(tmp, out_path)  # atomic on same filesystem
        except OSError as e:
            summary["error_message"] = f"write failed: {e}"
            return summary

        summary.update(valid=True, code=gen, method=method, out_path=str(out_path))
        return summary

    # ------------------------------------------------------------------ #
    # Plugin bus interface                                                #
    # ------------------------------------------------------------------ #

    def execute(self, message: Message) -> None:
        task_id = message.payload.get("task_id")
        code = message.payload.get("code") or ""
        src_path = message.payload.get("src_path") or "unknown.py"
        modes = message.payload.get("modes")

        logger.info(f"[{self.name}] Generating deterministic variant for {src_path} "
                    f"(task {task_id})")
        summary = self.generate_to_file(code, src_path,
                                        out_dir=os.path.join(
                                            os.path.dirname(__file__), "..", "..",
                                            "generated"),
                                        modes=modes)
        summary.update(task_id=task_id, current_step_plugin=self.name)
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload=summary,
        )
        if self.bus:
            self.bus.publish(response)
        else:
            logger.warning(f"[{self.name}] no bus; result dropped: {summary}")
