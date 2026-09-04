"""
CodeBackendWorker — Deterministic code quality and analysis backend.

Provides static analysis and transformation helpers that complement the
existing code generation workers (deterministic_coder, polyglot_coder):

1. complexity   : cyclomatic complexity & LOC metrics
2. style_check  : PEP-8 style hints (indentation, naming, line length)
3. security     : security smell detection (hardcoded secrets, unsafe calls)
4. refactor     : simple AST-based refactoring (unused import removal)

All operations are deterministic, pure Python — no LLM involved.

Message contract (bus):
    request : Message(type="task", destination="code_backend", payload={
                  "task_id": str,
                  "code": str,
                  "language": str,         # "python" | "javascript" | ...
                  "mode": "complexity"|"style_check"|"security"|"refactor",
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "code_backend",
                  "valid": bool, "result": dict|None,
                  "error_message": str (when invalid)
              })
"""
from __future__ import annotations

import ast
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("CodeBackendWorker")

# SECURITY: patterns that often indicate hardcoded secrets
_SECRET_PATTERNS = [
    re.compile(r"(?:password|passwd|pwd|secret|token|api_key|apikey)\s*=\s*['\"][^'\"]{8,}['\"]", re.I),
    re.compile(r"(?:AWS|GCP|AZURE|OPENAI|ANTHROPIC|CLAUDE)_?(?:SECRET|KEY|TOKEN|ACCESS|ID)\s*=\s*['\"][^'\"]+['\"]"),
    re.compile(r"https?://[^@\s]+:[^@\s]+@"),  # URL-embedded credentials
]

# Naming convention patterns
_CAMEL_CASE = re.compile(r"^[a-z]+[A-Z][a-zA-Z0-9]*$")
_SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
_UPPER_CASE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PASCAL_CASE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")


def _cyclomatic_complexity(tree: ast.Module) -> int:
    """Compute McCabe cyclomatic complexity for a module."""
    complexity = 1  # base
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.For, ast.AsyncFor)):
            complexity += 1
        elif isinstance(node, ast.Try):
            complexity += len(node.handlers)
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
        elif isinstance(node, ast.Match):
            complexity += len(node.cases)
        elif isinstance(node, (ast.And, ast.Or)):
            complexity += 1
    return complexity


def _loc_metrics(tree: ast.Module, source_lines: int) -> Dict[str, int]:
    """Count lines of code metrics."""
    classes = sum(1 for _ in ast.walk(tree) if isinstance(_, ast.ClassDef))
    functions = sum(1 for _ in ast.walk(tree)
                    if isinstance(_, (ast.FunctionDef, ast.AsyncFunctionDef)))
    imports = sum(1 for _ in ast.walk(tree)
                  if isinstance(_, (ast.Import, ast.ImportFrom)))
    return {
        "total_lines": source_lines,
        "classes": classes,
        "functions": functions,
        "imports": imports,
    }


def _security_scan(code: str) -> List[Dict[str, Any]]:
    """Scan code for security smells."""
    issues = []
    for pattern in _SECRET_PATTERNS:
        for m in pattern.finditer(code):
            line_no = code[:m.start()].count("\n") + 1
            issues.append({
                "line": line_no,
                "type": "potential_secret",
                "severity": "high",
                "detail": f"Possible hardcoded secret at line {line_no} "
                          f"(pattern: {pattern.pattern[:40]}…)",
            })

    # Check for dangerous function calls
    if "eval(" in code:
        issues.append({
            "line": 0,
            "type": "dangerous_call",
            "severity": "high",
            "detail": "Use of eval() — potential code injection risk.",
        })
    if "exec(" in code:
        issues.append({
            "line": 0,
            "type": "dangerous_call",
            "severity": "high",
            "detail": "Use of exec() — potential code injection risk.",
        })
    if "pickle.loads(" in code or "pickle.load(" in code:
        issues.append({
            "line": 0,
            "type": "dangerous_call",
            "severity": "medium",
            "detail": "Unsafe deserialization with pickle — potential RCE risk.",
        })

    return issues


def _style_check(code: str) -> List[Dict[str, Any]]:
    """Check Python code for style issues (PEP-8 hints)."""
    hints = []
    lines = code.splitlines()

    for i, line in enumerate(lines):
        # Line length > 100
        if len(line) > 100:
            hints.append({
                "line": i + 1,
                "type": "line_too_long",
                "severity": "low",
                "detail": f"Line {i+1}: {len(line)} chars (max 100).",
            })

    # Check naming conventions via AST
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            # Function names should be snake_case
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _CAMEL_CASE.match(node.name) and not node.name.startswith("_"):
                    hints.append({
                        "line": node.lineno,
                        "type": "naming_convention",
                        "severity": "low",
                        "detail": f"Function '{node.name}' should be snake_case.",
                    })
            # Class names should be PascalCase
            elif isinstance(node, ast.ClassDef):
                if not _PASCAL_CASE.match(node.name):
                    hints.append({
                        "line": node.lineno,
                        "type": "naming_convention",
                        "severity": "low",
                        "detail": f"Class '{node.name}' should be PascalCase.",
                    })
    except SyntaxError:
        pass

    return hints


def _remove_unused_imports(code: str) -> str:
    """Remove imports that are not used in the code (simple heuristic)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code

    # Collect names used in the module
    used_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)

    # Collect import aliases and check if used
    import_lines: List[int] = []
    for i, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                local_name = name.split(".")[0]
                if local_name not in used_names and local_name != name.split(".")[0]:
                    pass
                is_used = any(
                    n.id == local_name
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and n is not node
                )
                if not is_used:
                    import_lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local_name = alias.asname or alias.name
                is_used = any(
                    n.id == local_name
                    for n in ast.walk(tree)
                    if isinstance(n, ast.Name) and n is not node
                )
                if not is_used:
                    import_lines.append(node.lineno)

    if not import_lines:
        return code

    # Remove unused import lines (line-based)
    lines = code.splitlines()
    # Sort and dedup line numbers in reverse so indices don't shift
    for lineno in sorted(set(import_lines), reverse=True):
        if 0 < lineno <= len(lines):
            lines.pop(lineno - 1)

    return "\n".join(lines)


class CodeBackendWorker:
    """Worker d'analyse de code déterministe : métriques, style, sécurité, refactoring.
    (no LLM, pure Python avec module ``ast``)
    """

    name = "code_backend"

    def __init__(self, bus=None):
        self.bus = bus

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Backend d'analyse de code initialisé.")
        return True

    def shutdown(self) -> bool:
        return True

    # ── Core methods ──────────────────────────────────────────────────

    def analyze_complexity(self, code: str, language: str = "python") -> Dict[str, Any]:
        """Compute code complexity metrics."""
        lines = code.splitlines()
        if language == "python":
            try:
                tree = ast.parse(code)
                complexity = _cyclomatic_complexity(tree)
                loc = _loc_metrics(tree, len(lines))
                return {
                    "valid": True,
                    "cyclomatic_complexity": complexity,
                    "loc": loc,
                    "complexity_rating": "low" if complexity < 10
                    else "medium" if complexity < 20
                    else "high",
                    "error": None,
                }
            except SyntaxError as e:
                return {"valid": False, "error": str(e)}
        else:
            # Generic line-based metrics for other languages
            non_blank = sum(1 for l in lines if l.strip())
            return {
                "valid": True,
                "total_lines": len(lines),
                "code_lines": non_blank,
                "complexity_rating": "unknown",
                "note": f"Language '{language}' — line-based metrics only.",
                "error": None,
            }

    def style_check(self, code: str, language: str = "python") -> Dict[str, Any]:
        """Check code for style issues."""
        if language == "python":
            hints = _style_check(code)
            score = max(0, 100 - len(hints) * 10)
            return {
                "valid": True,
                "hints": hints,
                "score": score,
                "error": None,
            }
        return {
            "valid": True,
            "hints": [],
            "score": 100,
            "note": f"Style checks not implemented for '{language}'.",
            "error": None,
        }

    def security_scan(self, code: str, language: str = "python") -> Dict[str, Any]:
        """Scan code for security issues."""
        issues = _security_scan(code)
        score = max(0, 100 - len(issues) * 25)
        return {
            "valid": True,
            "issues": issues,
            "score": score,
            "error": None,
        }

    def refactor(self, code: str, language: str = "python") -> Dict[str, Any]:
        """Apply simple AST-based refactorings."""
        if language == "python":
            cleaned = _remove_unused_imports(code)
            changed = cleaned != code
            return {
                "valid": True,
                "code": cleaned,
                "changed": changed,
                "changes": ["Removed unused imports"] if changed else [],
                "error": None,
            }
        return {
            "valid": True,
            "code": code,
            "changed": False,
            "changes": [],
            "note": f"Refactoring not implemented for '{language}'.",
            "error": None,
        }

    # ── Plugin bus interface ──────────────────────────────────────────

    def execute(self, message) -> None:
        from captn.runtime.base import Message
        task_id = message.payload.get("task_id")
        code = message.payload.get("code", "")
        language = message.payload.get("language", "python")
        mode = message.payload.get("mode", "complexity")

        logger.info(f"[{self.name}] Analysing code ({mode}) for task {task_id}")

        result = None
        error = None
        try:
            if mode == "complexity":
                result = self.analyze_complexity(code, language)
            elif mode == "style_check":
                result = self.style_check(code, language)
            elif mode == "security":
                result = self.security_scan(code, language)
            elif mode == "refactor":
                result = self.refactor(code, language)
            else:
                error = f"Unknown mode: {mode}"
        except Exception as e:
            error = str(e)

        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "valid": error is None and (result is None or result.get("valid", True)),
                "result": result,
                "error_message": error,
            },
        )
        if self.bus:
            self.bus.publish(response)