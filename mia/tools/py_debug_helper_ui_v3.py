# -*- coding: utf-8 -*-

import ast
import re
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

APP_TITLE = "Py Debug Helper UI"
DEFAULT_GEOMETRY = "1180x760"


class DebugAnalyzer:
    def read_file(self, filepath: Path) -> str:
        return filepath.read_text(encoding="utf-8", errors="replace")

    def parse_range(self, text: str, total_lines: int):
        text = (text or "").strip()
        if not text:
            return None

        m = re.fullmatch(r"(\d+)\s*:\s*(\d+)", text)
        if not m:
            raise ValueError("Range must look like 1700:1760")

        start = int(m.group(1))
        end = int(m.group(2))
        if start < 1 or end < 1:
            raise ValueError("Range values must be >= 1")
        if start > end:
            raise ValueError("Range start must be <= end")
        if start > total_lines:
            raise ValueError("Range start is beyond file length")
        end = min(end, total_lines)
        return start, end

    def line_indent(self, line: str) -> int:
        expanded = line.expandtabs(4)
        return len(expanded) - len(expanded.lstrip(" "))

    def show_context_lines(self, lines: list[str], lineno: int, window: int = 2) -> list[str]:
        start = max(1, lineno - window)
        end = min(len(lines), lineno + window)
        out = []
        for i in range(start, end + 1):
            prefix = ">>" if i == lineno else "  "
            out.append(f"{prefix} {i:4}: {lines[i - 1].rstrip()}")
        return out

    def explain_syntax_error(self, msg: str, line: str) -> str:
        stripped = (line or "").strip()

        if "expected an indented block" in msg:
            return "Il manque une indentation après un def / if / try / except / for / while."
        if "unexpected indent" in msg:
            return "Il y a trop d'espaces, ou la ligne n'est pas au bon niveau dans le bloc."
        if "unindent does not match any outer indentation level" in msg:
            return "Tu mélanges probablement tabs et espaces, ou un bloc est réaligné au mauvais niveau."
        if "invalid syntax" in msg:
            if re.search(r"\bif\b[^#\n]*=[^=]", stripped) and "==" not in stripped:
                return "Tu as probablement utilisé '=' dans un if. Utilise '==' pour comparer, ou fais l'assignation sur une ligne séparée."
            if stripped.startswith("except ") and not stripped.endswith(":"):
                return "Le except semble incomplet. Vérifie surtout le ':' à la fin."
            if stripped.startswith("if ") and not stripped.endswith(":"):
                return "Le if semble incomplet. Vérifie surtout le ':' à la fin."
            if stripped.startswith("for ") and not stripped.endswith(":"):
                return "Le for semble incomplet. Vérifie surtout le ':' à la fin."
            return "Syntaxe invalide. Vérifie ':', '=', parenthèses, guillemets et mots-clés."
        if "was never closed" in msg:
            return "Une parenthèse, un crochet, une accolade ou un guillemet n'est pas fermé."
        return "Erreur de syntaxe générale. Vérifie la ligne, puis les 2 ou 3 lignes au-dessus."

    def scan_common_patterns(self, lines: list[str], start_line: int = 1, end_line: int | None = None):
        findings = []
        if end_line is None:
            end_line = len(lines)

        for lineno in range(start_line, end_line + 1):
            raw = lines[lineno - 1]
            line = raw.rstrip("\n")
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if "self." in stripped:
                findings.append((lineno, "Pattern suspect", "Tu as 'self.self'. Ça devrait presque toujours être juste 'self.'."))

            if ".items(str)" in stripped:
                findings.append((lineno, "Pattern suspect", "dict.items() ne prend pas d'argument ici. Utilise '.items()'."))

            if re.search(r"\bif\b[^#\n]*=[^=]", stripped):
                findings.append((lineno, "Pattern suspect", "Assignation probable dans un if. Utilise '=' hors du if, ou '==' pour comparer."))

            if re.search(r'\bfor\s+"[^"]+"\s*,', stripped):
                findings.append((lineno, "Pattern suspect", "Dans un for, il faut une variable, pas une string. Exemple: for eq, row in ..."))

            if stripped.startswith("return ") and re.search(r"\breturn\s+[A-Za-z]+\s+[A-Za-z]+", stripped):
                findings.append((lineno, "Texte brut suspect", "Après 'return', on dirait du texte plutôt qu'une vraie expression Python."))

            if stripped.startswith(("if ", "for ", "while ", "def ", "try:", "except ", "elif ", "else:", "finally:")) and stripped.endswith(":"):
                if lineno < len(lines):
                    next_line = lines[lineno]
                    if next_line.strip():
                        current_indent = self.line_indent(line)
                        next_indent = self.line_indent(next_line)
                        if next_indent <= current_indent:
                            findings.append((lineno, "Indent suspecte", "Le bloc suivant ne semble pas plus indenté que la ligne de contrôle."))

            if "\t" in raw:
                findings.append((lineno, "Tab détecté", "Il y a un tab sur cette ligne. Mélanger tabs et espaces casse souvent l'indentation."))

        return findings

    def analyze(self, filepath: Path, line_range: str = ""):
        result = {
            "ok": True,
            "errors": [],
            "findings": [],
            "file": str(filepath),
            "line_count": 0,
            "active_range": None,
        }

        if not filepath.exists():
            result["ok"] = False
            result["errors"].append("File not found.")
            return result

        source = self.read_file(filepath)
        lines = source.splitlines()
        result["line_count"] = len(lines)

        try:
            active_range = self.parse_range(line_range, len(lines))
        except Exception as exc:
            result["ok"] = False
            result["errors"].append(f"Invalid range: {exc}")
            return result

        result["active_range"] = active_range

        try:
            ast.parse(source)
        except SyntaxError as e:
            result["ok"] = False
            line_text = e.text.rstrip("\n") if e.text else ""
            err = {
                "lineno": e.lineno,
                "offset": e.offset,
                "message": e.msg,
                "code": line_text,
                "hint": self.explain_syntax_error(e.msg, line_text),
                "context": self.show_context_lines(lines, e.lineno or 1, window=2) if e.lineno else [],
            }
            result["errors"].append(err)
        except Exception as exc:
            result["ok"] = False
            result["errors"].append({
                "lineno": None,
                "offset": None,
                "message": f"Unexpected parser error: {exc}",
                "code": "",
                "hint": "Le parser a échoué d'une façon inattendue.",
                "context": traceback.format_exc().splitlines(),
            })

        start_line = 1
        end_line = len(lines)
        if result["active_range"]:
            start_line, end_line = result["active_range"]

        result["findings"] = self.scan_common_patterns(lines, start_line=start_line, end_line=end_line)
        return result


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(DEFAULT_GEOMETRY)
        self.minsize(980, 640)

        self.analyzer = DebugAnalyzer()

        self.file_var = tk.StringVar()
        self.range_var = tk.StringVar()
        self.dark_var = tk.BooleanVar(value=True)

        self._setup_style()
        self._build_ui()
        self.apply_theme()

    def _setup_style(self):
        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Fichier Python").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(top, textvariable=self.file_var)
        entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        browse = ttk.Button(top, text="Browse", command=self.pick_file)
        browse.grid(row=1, column=1, sticky="ew")

        ttk.Label(top, text="Range optionnelle (ex: 1700:1760)").grid(row=0, column=2, sticky="w", padx=(16, 0))
        range_entry = ttk.Entry(top, textvariable=self.range_var, width=20)
        range_entry.grid(row=1, column=2, sticky="ew", padx=(16, 8))

        analyze_btn = ttk.Button(top, text="Analyze", command=self.run_analysis)
        analyze_btn.grid(row=1, column=3, sticky="ew")

        clear_btn = ttk.Button(top, text="Clear", command=self.clear_output)
        clear_btn.grid(row=1, column=4, sticky="ew", padx=(8, 0))

        dark_cb = ttk.Checkbutton(top, text="Dark mode", variable=self.dark_var, command=self.apply_theme)
        dark_cb.grid(row=2, column=0, sticky="w", pady=(10, 0))

        top.columnconfigure(0, weight=1)
        top.columnconfigure(2, weight=0)

        middle = ttk.Panedwindow(self, orient="horizontal")
        middle.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = ttk.Frame(middle)
        right = ttk.Frame(middle)
        middle.add(left, weight=1)
        middle.add(right, weight=2)

        ttk.Label(left, text="Results").pack(anchor="w")
        self.results_list = tk.Listbox(left, exportselection=False)
        self.results_list.pack(fill="both", expand=True, pady=(6, 0))
        self.results_list.bind("<<ListboxSelect>>", self.on_result_selected)

        ttk.Label(right, text="Details").pack(anchor="w")
        self.details = ScrolledText(right, wrap="word", undo=False)
        self.details.pack(fill="both", expand=True, pady=(6, 0))

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w")

        self.current_payload = []

    def apply_theme(self):
        dark = self.dark_var.get()

        if dark:
            bg = "#1e1e1e"
            panel = "#252526"
            fg = "#f1f1f1"
            muted = "#aaaaaa"
            select_bg = "#3a3d41"
            insert_bg = "#f1f1f1"
        else:
            bg = "#f3f3f3"
            panel = "#ffffff"
            fg = "#111111"
            muted = "#555555"
            select_bg = "#d7e8ff"
            insert_bg = "#111111"

        self.configure(bg=bg)
        self.style.configure("TFrame", background=bg)
        self.style.configure("TLabel", background=bg, foreground=fg)
        self.style.configure("TButton", padding=6)
        self.style.configure("TCheckbutton", background=bg, foreground=fg)
        self.style.configure("TEntry", fieldbackground=panel, foreground=fg)

        self.results_list.configure(
            bg=panel,
            fg=fg,
            selectbackground=select_bg,
            selectforeground=fg,
            highlightthickness=1,
            highlightbackground=muted,
            relief="flat",
        )
        self.details.configure(
            bg=panel,
            fg=fg,
            insertbackground=insert_bg,
            selectbackground=select_bg,
            relief="flat",
            highlightthickness=1,
            highlightbackground=muted,
        )

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="Select Python file",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if path:
            self.file_var.set(path)

    def clear_output(self):
        self.results_list.delete(0, tk.END)
        self.details.delete("1.0", tk.END)
        self.current_payload = []
        self.status_var.set("Cleared.")

    def add_result(self, label: str, payload: dict):
        self.results_list.insert(tk.END, label)
        self.current_payload.append(payload)

    def run_analysis(self):
        self.clear_output()
        raw_path = self.file_var.get().strip()
        if not raw_path:
            messagebox.showwarning("Missing file", "Choose a Python file first.")
            return

        filepath = Path(raw_path).expanduser()
        result = self.analyzer.analyze(filepath, self.range_var.get())

        if isinstance(result["errors"], list) and result["errors"] and isinstance(result["errors"][0], str):
            self.status_var.set("Invalid input.")
            self.details.insert("1.0", "\n".join(result["errors"]))
            return

        if result["errors"]:
            for err in result["errors"]:
                label = f"Syntax error — line {err.get('lineno') or '?'}"
                self.add_result(label, {"type": "error", "data": err})
        else:
            self.add_result("Syntax OK", {"type": "ok", "data": {"message": "No syntax errors found."}})

        findings = result["findings"]
        for lineno, title, msg in findings:
            label = f"{title} — line {lineno}"
            self.add_result(label, {"type": "finding", "data": {"lineno": lineno, "title": title, "message": msg, "file": result['file']}})

        range_text = "whole file"
        if result["active_range"]:
            range_text = f"lines {result['active_range'][0]}:{result['active_range'][1]}"

        self.status_var.set(
            f"Done. {len(result['errors'])} syntax error(s), {len(findings)} finding(s), scan on {range_text}."
        )

        if self.current_payload:
            self.results_list.selection_clear(0, tk.END)
            self.results_list.selection_set(0)
            self.on_result_selected()

    def on_result_selected(self, event=None):
        selection = self.results_list.curselection()
        if not selection:
            return

        index = selection[0]
        payload = self.current_payload[index]
        self.details.delete("1.0", tk.END)

        kind = payload["type"]
        data = payload["data"]

        if kind == "ok":
            self.details.insert("1.0", data["message"])
            return

        if kind == "error":
            lines = []
            lines.append("Syntax error")
            lines.append("")
            lines.append(f"Line:    {data.get('lineno')}")
            lines.append(f"Column:  {data.get('offset')}")
            lines.append(f"Message: {data.get('message')}")
            if data.get("code"):
                lines.append(f"Code:    {data.get('code')}")
            lines.append("")
            lines.append(f"Hint: {data.get('hint')}")
            if data.get("context"):
                lines.append("")
                lines.append("Context:")
                lines.extend(data["context"])
            self.details.insert("1.0", "\n".join(lines))
            return

        if kind == "finding":
            lines = []
            lines.append(data.get("title", "Finding"))
            lines.append("")
            lines.append(f"Line: {data.get('lineno')}")
            lines.append(f"Message: {data.get('message')}")
            self.details.insert("1.0", "\n".join(lines))
            return


    def main():
        app = App()
        app.mainloop()

if __name__ == "__main__":
    main()
