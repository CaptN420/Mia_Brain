from typing import Dict, Any, List, Optional
import logging
import os
import re

logger = logging.getLogger("Validator")

class PatchValidator:
    """
    The Inspector. Ensures that proposed patches are safe to apply before they are written to disk.

    N-05: replaced the trivially-bypassed literal blacklist with AST-based
    analysis + anchored filename matching. NOTE: this is a heuristic safety
    gate for the demo pipeline - it is NOT a full security sandbox. A
    determined adversary can still write code that evades static checks;
    keep human review for anything touching production systems.
    """

    # Anchored basename matching (was substring-anywhere before).
    _CORE_BASENAMES = {"launcher.py", "runtime.py", "main.py", "base.py"}

    def __init__(self):
        pass

    def _is_critical_path(self, file_path: str) -> bool:
        """True only if the FILE's own basename is a protected core file.
        'my_runtime.py' no longer matches; 'runtime.py' in any dir does."""
        try:
            base = os.path.basename(file_path).lower()
            return base in self._CORE_BASENAMES
        except Exception:
            return False

    def _scan_dangerous_code(self, diff: str) -> Optional[str]:
        """AST-parse the patch and reject genuinely dangerous constructs."""
        try:
            import ast
            tree = ast.parse(diff)
        except SyntaxError:
            # Not parseable as Python - fall back to conservative keyword scan.
            keywords = [r"\brm\s+-rf\b", r"\bshutil\.rmtree\b", r"\bos\.remove\b",
                        r"\bos\.unlink\b", r"\bdel\s+\w+", r"\bformat\s+c:\b"]
            for kw in keywords:
                if re.search(kw, diff or ""):
                    return f"unparseable patch contains destructive pattern '{kw}'"
            return None

        for node in ast.walk(tree):
            # Imports of modules that enable filesystem/OS destruction.
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("ctypes", "subprocess") and "subprocess" in alias.name:
                        return f"patch imports subprocess (shell execution)"
            if isinstance(node, ast.ImportFrom) and node.module == "ctypes":
                return "patch imports ctypes"
            # Attribute calls: os.remove / shutil.rmtree / shutil.rmtree-like.
            if isinstance(node, ast.Attribute):
                name = node.attr
                if name in ("rmtree", "remove", "unlink", "system", "popen"):
                    return f"patch calls .{name}() (destructive/shell operation)"
            # Bare calls named exec/eval/system.
            if isinstance(node, ast.Call):
                fn = node.func
                fname = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if fname in ("exec", "eval", "system", "popen"):
                    return f"patch calls {fname}()"
        return None

    def validate_patch(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform a 'pre-flight' check on a patch.
        Returns a dict with 'is_safe' (bool) and 'reason' (str).
        """
        logger.info(f"Validating patch for {patch.get('file_path')}")

        file_path = patch.get("file_path", "unknown")

        # Rule 1: Check if this is a new file creation or existing file modification
        file_exists = os.path.exists(file_path)

        # Rule 2: Critical File Protection (anchored to real core basenames)
        if file_exists and self._is_critical_path(file_path):
            return {
                "is_safe": False,
                "reason": f"Critical File Protection: {file_path} is a core system file. Manual intervention required."
            }

        # Rule 3: AST-based scan for destructive operations in the diff.
        danger = self._scan_dangerous_code(patch.get("diff", "") or "")
        if danger:
            return {
                "is_safe": False,
                "reason": f"Security Alert: {danger}."
            }

        return {
            "is_safe": True,
            "reason": "Pre-flight checks passed."
        }
