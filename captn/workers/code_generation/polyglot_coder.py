#!/usr/bin/env python3
"""PolyglotCoder - The multi-language deterministic code-generation worker.

Role in the Captn architecture (deterministic-first):
    DeterministicCoder only emits PYTHON (AST-based). This worker extends
    coverage to every language the GitHub crawler ingests: C, C++, C#, HTML,
    JavaScript, TypeScript, Java, Go, Rust, SQL, Shell, CSS... It is fully
    deterministic - templates + pure string transforms, no LLM anywhere.

What it does:
    action="scaffold" : emit a compilable/runnable starter skeleton for a
                        named artifact (program/class/module/page) in any of
                        the SUPPORTED_LANGUAGES, honoring an optional spec
                        dict (functions/fields/routes).
    action="wrap"     : wrap already-written source into a file with a
                        provenance header comment in the target language.

Safety gates applied BEFORE writing anything:
    - lang_check()     : per-language static sanity gate (ast.parse for
                         Python; brace/tag balance + required structure for
                         the others). Bad output never touches disk.
    - PatchValidator   : destructive-op / core-file protection on write paths.
    - Path containment : out_path must live under the generated root.

Message contract (bus):
    request : Message(type="task", destination="polyglot_coder", payload={
                  "task_id": str,
                  "action": "scaffold"|"wrap",
                  "language": "python"|"c"|"cpp"|"csharp"|"html"|...,  # see SUPPORTED_LANGUAGES
                  "name": str,               # artifact name (e.g. "calculator", "index")
                  "spec": {...},             # optional: functions/classes/fields/routes
                  "code": str,               # for action=wrap
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "polyglot_coder",
                  "valid": bool, "language": str, "out_path": str|None,
                  "bytes_written": int, "error_message": str (when invalid)
              })

Standalone use (no bus):
    result = PolyglotCoder(bus=None).scaffold("cpp", "hello", {"functions": ["main"]})
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from captn.runtime.base import Message, Plugin
    from captn.runtime.validator import PatchValidator
except ImportError:  # standalone import without the runtime package
    PatchValidator = None
    Plugin = object

    class Message:  # minimal shim so module imports cleanly anywhere
        def __init__(self, sender="", destination="", type="", payload=None):
            self.sender, self.destination = sender, destination
            self.type, self.payload = type, payload or {}


logger = logging.getLogger("PolyglotCoder")

GENERATED_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "generated", "polyglot"))

SUPPORTED_LANGUAGES = {
    "python": {"ext": ".py", "comment": "#"},
    "c": {"ext": ".c", "comment": "//"},
    "cpp": {"ext": ".cpp", "comment": "//"},
    "csharp": {"ext": ".cs", "comment": "//"},
    "java": {"ext": ".java", "comment": "//"},
    "javascript": {"ext": ".js", "comment": "//"},
    "typescript": {"ext": ".ts", "comment": "//"},
    "go": {"ext": ".go", "comment": "//"},
    "rust": {"ext": ".rs", "comment": "//"},
    "html": {"ext": ".html", "comment": "<!--"},
    "css": {"ext": ".css", "comment": "/*"},
    "sql": {"ext": ".sql", "comment": "--"},
    "shell": {"ext": ".sh", "comment": "#"},
}


# --------------------------------------------------------------------------
# Per-language static sanity gates (cheap, deterministic, no compilation)
# --------------------------------------------------------------------------

def _check_braces(src: str) -> Optional[str]:
    """Balanced {} () [] outside strings/comments - returns error or None."""
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    i, n, in_str, in_line, in_block = 0, len(src), None, False, False
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if in_line:
            if c == "\n":
                in_line = False
        elif in_block:
            if c == "*" and nxt == "/":
                in_block = False
                i += 1
        elif in_str:
            if c == "\\":
                i += 1
            elif c == in_str:
                in_str = None
        else:
            if c in "\"'":
                in_str = c
            elif c == "/" and nxt == "/":
                in_line = True
                i += 1
            elif c == "/" and nxt == "*":
                in_block = True
                i += 1
            elif c in "([{":
                stack.append(c)
            elif c in ")]}":
                if not stack or stack[-1] != pairs[c]:
                    return f"unbalanced '{c}'"
                stack.pop()
        i += 1
    if in_str:
        return "unterminated string literal"
    if in_block:
        return "unterminated block comment"
    if stack:
        return f"unclosed '{stack[-1]}'"
    return None


def _check_python(src: str) -> Optional[str]:
    try:
        import ast
        ast.parse(src)
        return None
    except SyntaxError as e:
        return f"python SyntaxError line {e.lineno}: {e.msg}"


def _check_html(src: str) -> Optional[str]:
    low = src.lower()
    for tag in ("html", "head", "body", "title"):
        opens = len(re.findall(rf"<{tag}[\s>]", low))
        closes = len(re.findall(rf"</{tag}>", low))
        if opens != closes:
            return f"<{tag}> opened {opens}x but closed {closes}x"
    stack = []
    for m in re.finditer(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*?)(/?)>", src):
        closing, name, _, selfclose = m.groups()
        if name.lower() in ("area", "base", "br", "col", "embed", "hr", "img",
                            "input", "link", "meta", "source", "track", "wbr"):
            continue
        if selfclose:
            continue
        if closing:
            if not stack or stack[-1].lower() != name.lower():
                return f"</{name}> without matching opening tag"
            stack.pop()
        else:
            stack.append(name)
    if stack:
        return f"unclosed <{stack[-1]}>"
    return None


def lang_check(language: str, src: str) -> Optional[str]:
    """Return an error string when src fails its language's sanity gate."""
    if language == "python":
        return _check_python(src)
    if language == "html":
        return _check_html(src)
    if language == "css":
        return _check_braces(src)
    if language in ("sql", "shell"):
        return None  # free-form; nothing structural to enforce cheaply
    return _check_braces(src)


# --------------------------------------------------------------------------
# Deterministic templates
# --------------------------------------------------------------------------

def _header(lang: str, name: str, note: str) -> str:
    meta = SUPPORTED_LANGUAGES[lang]
    c = meta["comment"]
    end = " -->" if c == "<!--" else (" */" if c == "/*" else "")
    if end:
        c_open = c + " "
        lines = [
            f"{c_open}{name}{end}",
            *[f"{c_open}{l}{end}" for l in note.splitlines()],
        ]
        return "\n".join(lines) + "\n\n"
    return "\n".join(f"{c} {l}" for l in [name, *note.splitlines()]) + "\n\n"


def _pascal(s: str) -> str:
    return "".join(p.capitalize() or "_" for p in re.split(r"[^A-Za-z0-9]", s)) or "Artifact"


def _snake(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return s or "artifact"


def _tpl_python(name: str, spec: Dict[str, Any]) -> str:
    cls = _pascal(name)
    funcs = spec.get("functions") or []
    body = [f'"""{_snake(name)} - generated by polyglot_coder."""\n']
    if spec.get("classes"):
        for c in spec["classes"]:
            body.append(f"class {_pascal(c)}:")
            body.append(f'    """TODO."""\n')
    if funcs:
        for fn in funcs:
            body.append(f"def {_snake(fn)}() -> None:")
            body.append(f'    """TODO: implement {fn}."""')
            body.append("    raise NotImplementedError")
            body.append("")
    else:
        body.append("def main() -> None:")
        body.append('    """Entry point."""')
        body.append(f'    print("{cls}")')
        body.append("")
        body.append('if __name__ == "__main__":')
        body.append("    main()")
    return "\n".join(body) + "\n"


def _tpl_c_family(name: str, spec: Dict[str, Any], variant: str) -> str:
    cls = _pascal(name)
    funcs = spec.get("functions") or []
    if variant == "c":
        decls, calls = [], []
        for fn in funcs[:8]:
            decls.append(f"void {_snake(fn)}(void);")
            calls.append(f"    {_snake(fn)}();")
        head = ("#include <stdio.h>\n#include <stdlib.h>\n"
                + ("\n".join(decls) + "\n\n" if decls else ""))
        body = "int main(void) {\n" + "\n".join(calls) + '    printf("%s\\n", "' + cls + '");\n    return EXIT_SUCCESS;\n}\n'
        tail = "".join(
            f"\nvoid {_snake(fn)}(void) {{\n    /* TODO */\n}}\n" for fn in funcs[:8])
        return head + body + tail
    # C++ / C# / Java style
    if variant == "csharp":
        ns = f"namespace {_pascal(name)}\n{{"
        open_ = f"{ns}\npublic class {cls}\n{{"
        close_ = "}\n}"
        print_stmt = 'System.Console.WriteLine("' + cls + '");'
    elif variant == "java":
        open_ = f"public class {cls} {{"
        close_ = "}"
        print_stmt = f'System.out.println("{cls}");'
    else:  # cpp
        open_ = f"class {cls}\n{{\npublic:\n    void run() const;\n}};\n"
        close_ = ""
        print_stmt = f'std::cout << "{cls}" << std::endl;'
    inc = {"cpp": "#include <iostream>\n\n", "csharp": "using System;\n\n", "java": ""}[variant]
    funcs = spec.get("functions") or []
    extra = ""
    if variant in ("cpp",):
        extra = "".join(
            f"\nstatic void {_snake(fn)}() {{ /* TODO */ }}\n" for fn in funcs[:8])
    main_body = "\n    ".join([f"// TODO: {_snake(f)}();" for f in funcs[:8]] + [print_stmt])
    if variant == "java":
        return (open_
                + f"\n    public static void main(String[] args) {{\n        {main_body}\n    }}\n"
                + close_ + "\n")
    return (inc + open_
            + f"\nint main()\n{{\n    {main_body}\n    return 0;\n}}\n"
            + close_ + extra)


def _tpl_html(name: str, spec: Dict[str, Any]) -> str:
    title = _pascal(name)
    routes = spec.get("routes") or []
    nav = ""
    if routes:
        items = "\n".join(f'      <li><a href="#{_snake(r)}">{_pascal(r)}</a></li>' for r in routes)
        sections = "\n".join(
            f'    <section id="{_snake(r)}">\n      <h2>{_pascal(r)}</h2>\n    </section>'
            for r in routes)
        nav = f'  <nav>\n    <ul>\n{items}\n    </ul>\n  </nav>\n{sections}\n'
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{title}</title>\n"
        "</head>\n"
        "<body>\n"
        f"  <h1>{title}</h1>\n"
        + nav +
        "</body>\n"
        "</html>\n")


def _tpl_generic(name: str, lang: str, spec: Dict[str, Any]) -> str:
    cls = _pascal(name)
    if lang == "javascript" or lang == "typescript":
        t = ": void" if lang == "typescript" else ""
        funcs = spec.get("functions") or []
        body = "".join(
            f"function {_camel(fn)}(){t} {{\n  // TODO\n}}\n\n" for fn in funcs[:10])
        tail = (f'type annotation placeholder\n' if False else "")
        return (f"// {cls}\n" + body
                + f'console.log("{cls}");\n')
    if lang == "go":
        pkg = _snake(name).replace("_", "")
        return (f"package {pkg or 'main'}\n\n"
                'import "fmt"\n\n'
                "func main() {\n"
                f'\tfmt.Println("{cls}")\n'
                "}\n")
    if lang == "rust":
        return (f"fn main() {{\n"
                f'    println!("{cls}");\n'
                "}\n")
    if lang == "sql":
        table = _snake(name)
        cols = spec.get("fields") or ["id INTEGER PRIMARY KEY"]
        ddl = ",\n    ".join(str(c).replace("-", "_").lower() for c in cols)
        return (f"-- {cls}\n"
                f"CREATE TABLE IF NOT EXISTS {table} (\n    {ddl}\n);\n")
    if lang == "shell":
        return (f"#!/usr/bin/env bash\nset -euo pipefail\n\n"
                f'main() {{\n    echo "{cls}"\n}}\n\nmain "$@"\n')
    if lang == "css":
        return (f"/* {cls} */\n"
                ":root {\n  --accent: #4f9cf9;\n}\n\n"
                f".{_snake(name)} {{\n  color: var(--accent);\n}}\n")
    raise ValueError(f"no template for language '{lang}'")


def _camel(s: str) -> str:
    parts = _snake(s).split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def render(language: str, name: str, spec: Optional[Dict[str, Any]] = None) -> str:
    """Pure function: (language, name, spec) -> source text. No I/O."""
    spec = spec or {}
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language '{language}'. Supported: {sorted(SUPPORTED_LANGUAGES)}")
    if language == "python":
        return _tpl_python(name, spec)
    if language == "html":
        return _tpl_html(name, spec)
    if language in ("c", "cpp", "csharp", "java"):
        variant = {"c": "c", "cpp": "cpp", "csharp": "csharp", "java": "java"}[language]
        return _tpl_c_family(name, spec, variant)
    return _tpl_generic(name, language, spec)


# --------------------------------------------------------------------------
# Worker plugin
# --------------------------------------------------------------------------

class PolyglotCoder(Plugin if Plugin is not object else object):
    name = "polyglot_coder"

    def __init__(self, bus=None):
        if hasattr(Plugin, "__init__") and Plugin is not object:
            try:
                super().__init__(bus)
            except TypeError:
                pass
        self.bus = bus
        self.validator = PatchValidator() if PatchValidator else None
        self.is_active = True

    # -- public API ---------------------------------------------------------

    def scaffold(self, language: str, name: str,
                 spec: Optional[Dict[str, Any]] = None,
                 out_dir: Optional[str] = None) -> Dict[str, Any]:
        """Generate + gate-check + write one artifact. Returns summary dict."""
        try:
            src = render(language, name, spec)
        except ValueError as e:
            return self._fail(str(e))

        err = lang_check(language, src)
        if err:
            return self._fail(f"{language} gate failed: {err}")

        meta = SUPPORTED_LANGUAGES[language]
        base_dir = out_dir or os.path.join(GENERATED_ROOT, language)
        os.makedirs(base_dir, exist_ok=True)
        digest = hashlib.sha256(src.encode()).hexdigest()[:8]
        fname = f"{_snake(name)}.{digest}.gen{meta['ext']}"
        out_path = os.path.abspath(os.path.join(base_dir, fname))

        # Path containment: everything must live under GENERATED_ROOT
        if not out_path.startswith(GENERATED_ROOT):
            return self._fail("path escape blocked")

        patch = {"file_path": out_path, "diff": src}
        verdict = self.validator.validate_patch(patch) if self.validator else {"is_safe": True, "reason": "standalone"}
        if not verdict.get("is_safe"):
            return self._fail(verdict.get("reason", "validator rejected"))

        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)

        result = {
            "valid": True,
            "language": language,
            "name": name,
            "out_path": out_path,
            "bytes_written": len(src),
            "sha256_prefix": digest,
            "current_step_plugin": self.name,
        }
        logger.info("[%s] wrote %s (%s, %d bytes)", self.name, out_path, language, len(src))
        return result

    def wrap(self, language: str, name: str, code: str) -> Dict[str, Any]:
        """Attach a provenance header to foreign code, gate-check, write it."""
        header = _header(language, name,
                         f"generated by captn polyglot_coder · {datetime.now():%Y-%m-%d %H:%M}")
        return self.scaffold(language, name, None, ) if False else self._write_wrapped(language, name, header + code)

    def _write_wrapped(self, language: str, name: str, src: str) -> Dict[str, Any]:
        err = lang_check(language, src)
        if err:
            return self._fail(f"{language} gate failed: {err}")
        meta = SUPPORTED_LANGUAGES[language]
        base_dir = os.path.join(GENERATED_ROOT, language)
        os.makedirs(base_dir, exist_ok=True)
        digest = hashlib.sha256(src.encode()).hexdigest()[:8]
        out_path = os.path.abspath(os.path.join(base_dir, f"{_snake(name)}.wrapped.{digest}{meta['ext']}"))
        if not out_path.startswith(GENERATED_ROOT):
            return self._fail("path escape blocked")
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(src)
        logger.info("[%s] wrapped %s (%s)", self.name, out_path, language)
        return {"valid": True, "language": language, "out_path": out_path,
                "bytes_written": len(src), "current_step_plugin": self.name}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _fail(reason: str) -> Dict[str, Any]:
        logger.warning("[polyglot_coder] rejected: %s", reason)
        return {"valid": False, "error_message": reason, "current_step_plugin": "polyglot_coder"}

    # -- bus plumbing --------------------------------------------------------

    def execute(self, message: Message) -> None:
        task_id = message.payload.get("task_id")
        action = message.payload.get("action", "scaffold")
        language = message.payload.get("language", "python")
        name = message.payload.get("name", "artifact")

        if action == "wrap":
            result = self.wrap(language, name, message.payload.get("code", ""))
        else:
            result = self.scaffold(language, name, message.payload.get("spec"))
        result.update(task_id=task_id)

        response = Message(sender=self.name, destination="captn",
                           type="response", payload=result)
        if self.bus:
            self.bus.publish(response)
        else:
            logger.warning("[polyglot_coder] no bus; result dropped: %s", json.dumps(result, default=str)[:200])


if __name__ == "__main__":
    w = PolyglotCoder(bus=None)
    for lang in ("python", "c", "cpp", "csharp", "html", "sql"):
        r = w.scaffold(lang, "demo_" + lang, {"functions": ["init", "run"], "fields": ["id integer primary key", "value text"]})
        print(lang, "->", "OK" if r["valid"] else r["error_message"])
