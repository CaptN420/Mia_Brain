from __future__ import annotations
import re
from pathlib import Path

import json
import subprocess

from action_monitor import SecurityStop, log_effect
import sys
import threading
from workspace_security import get_workspace_dir
from shared_memory import SharedResearchMemory
from typing import Any

import wx
import wx.lib.scrolledpanel as scrolled

def normalize_target_equation(value) -> str:
    if callable(value):
        try:
            value = value()
        except Exception:
            return ""
    value = str(value or "").strip()
    m = re.match(r"^T\d+\s*\|\s*(.+)$", value)
    if m:
        return m.group(1).strip()
    if " | " in value:
        return value.split(" | ", 1)[1].strip()
    return value

UI_THEME = {
    "frame_bg": wx.Colour(24, 26, 32),
    "panel_bg": wx.Colour(30, 34, 42),
    "panel_alt": wx.Colour(36, 40, 50),
    "text": wx.Colour(230, 235, 245),
    "muted": wx.Colour(170, 178, 194),
    "border": wx.Colour(70, 78, 94),
    "input_bg": wx.Colour(26, 30, 36),

    "ready": wx.Colour(125, 230, 150),
    "approved": wx.Colour(123, 197, 255),
    "partial": wx.Colour(245, 214, 92),
    "fallback": wx.Colour(255, 170, 80),
    "blocked": wx.Colour(255, 107, 107),

    "ready_bg": wx.Colour(18, 50, 30),
    "approved_bg": wx.Colour(18, 36, 56),
    "partial_bg": wx.Colour(54, 46, 18),
    "fallback_bg": wx.Colour(60, 34, 18),
    "blocked_bg": wx.Colour(60, 20, 20),

    "root_text": wx.Colour(220, 220, 220),
}

BASE_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = get_workspace_dir()
WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
LAUNCHER_FILE = BASE_DIR / "launcher.py"

def local_session_dir(session_name: str) -> Path:
    return WORKSPACE_DIR / str(session_name).strip()

LANGS = {
    "en": {
        "app_title": "Alchemy Launcher UI",
        "tab_controls": "Controls",
        "tab_science": "Scientific",
        "tab_equations": "Equations",
        "tab_variables": "Variables",
        "tab_eqtest": "Equation Test",
        "tab_logs": "Logs",
        "status_ready": "Status: ready",
        "language": "Language",
        "mode": "Mode",
        "session_strategy": "Session Strategy",
        "session_selected": "Selected Session",
        "session_latest": "Latest Session",
        "session_new": "New Session",
        "working_session": "Working Session",
        "session": "Session",
        "refresh_sessions": "Refresh Sessions",
        "params": "Parameters",
        "cycles": "Cycles",
        "variable_turns": "Variable Turns",
        "equation_turns": "Equation Turns",
        "mutation_turns": "Mutation Turns",
        "lineage_limit": "Lineage Limit",
        "steps": "Steps",
        "prepare_steps": "Prepare Steps",
        "loop_profile": "Loop Profile",
        "merge_sessions": "Merge Sessions",
        "multi_merge_sessions": "Multi-Merge Sessions",
        "session_a": "Session A",
        "session_b": "Session B",
        "selected_sessions_for_multi_merge": "Selected sessions for multi-merge:",
        "perform_multi_merge": "Perform Multi-Merge",
        "target_session": "Target Session",
        "target_new": "New Session",
        "target_existing": "Existing Target Session",
        "preview": "Command Preview",
        "test_launcher": "Test Launcher",
        "run": "Run",
        "stop": "Stop",
        "reset": "Reset All",
        "use_active": "Use Active Session",
        "refresh_view": "Refresh View",
        "scientific_summary": "Scientific Summary",
        "equation_filter": "Equation Filter",
        "variable_filter": "Variable Filter",
        "all": "All",
        "approved": "Approved",
        "partial": "Partial",
        "candidate": "Candidate",
        "linked": "Linked",
        "unused": "Unused",
        "needs_repair": "Needs Repair",
        "mutable": "Mutable",
        "copy_equation": "Copy Equation",
        "prepare_repair": "Prepare Repair",
        "prepare_mutation": "Prepare Mutation",
        "copy_variable": "Copy Variable",
        "like": "Like",
        "dislike": "Dislike",
        "variable_prepare_repair": "Prepare Repair from Variable",
        "variable_prepare_mutation": "Prepare Mutation from Variable",
        "use_selected_equation": "Use Selected Equation",
        "run_test": "Run Test",
        "copy_report": "Copy Report",
        "tab_feature_notes": "Feature Notes",
        "open_science": "Open in Scientific",
        "idle": "idle",
        "running": "running...",
        "done": "done",
        "stopped": "stopped",
        "error": "error",
    },
    "fr": {
        "app_title": "Interface du lanceur Alchemy",
        "tab_controls": "Contrôles",
        "tab_science": "Scientifique",
        "tab_equations": "Équations",
        "tab_variables": "Variables",
        "tab_eqtest": "Equation Test",
        "tab_logs": "Logs",
        "status_ready": "Statut : prêt",
        "language": "Langue",
        "mode": "Mode",
        "session_strategy": "Stratégie de session",
        "session_selected": "Session sélectionnée",
        "session_latest": "Dernière session",
        "session_new": "Nouvelle session",
        "working_session": "Session de travail",
        "session": "Session",
        "refresh_sessions": "Rafraîchir les sessions",
        "params": "Paramètres",
        "cycles": "Cycles",
        "variable_turns": "Tours variables",
        "equation_turns": "Tours équations",
        "mutation_turns": "Tours mutation",
        "lineage_limit": "Limite de lignée",
        "steps": "Étapes",
        "prepare_steps": "Étapes préparation",
        "loop_profile": "Profil de loop",
        "merge_sessions": "Fusion de sessions",
        "multi_merge_sessions": "Multi-Fusion de Sessions",
        "session_a": "Session A",
        "session_b": "Session B",
        "selected_sessions_for_multi_merge": "Sessions sélectionnées pour multi-fusion:",
        "perform_multi_merge": "Exécuter Multi-Fusion",
        "target_session": "Session cible",
        "target_new": "Nouvelle session",
        "target_existing": "Session cible existante",
        "preview": "Aperçu de commande",
        "test_launcher": "Tester le launcher",
        "run": "Lancer",
        "stop": "Arrêter",
        "reset": "Reset total",
        "use_active": "Utiliser session active",
        "refresh_view": "Rafraîchir la vue",
        "scientific_summary": "Résumé scientifique",
        "equation_filter": "Filtre équations",
        "variable_filter": "Filtre variables",
        "all": "Toutes",
        "approved": "Approuvées",
        "partial": "Partielles",
        "candidate": "Candidates",
        "linked": "Liées",
        "unused": "Inutilisées",
        "needs_repair": "À réparer",
        "mutable": "Mutables",
        "copy_equation": "Copier équation",
        "prepare_repair": "Préparer repair",
        "prepare_mutation": "Préparer mutation",
        "copy_variable": "Copier variable",
        "like": "Like",
        "dislike": "Dislike",
        "variable_prepare_repair": "Préparer repair depuis variable",
        "variable_prepare_mutation": "Préparer mutation depuis variable",
        "use_selected_equation": "Utiliser équation sélectionnée",
        "run_test": "Lancer test",
        "copy_report": "Copier rapport",
        "tab_feature_notes": "Notes de roadmap",
        "open_science": "Ouvrir dans Scientifique",
        "idle": "prêt",
        "running": "exécution...",
        "done": "terminé",
        "stopped": "arrêté",
        "error": "erreur",
    },
}

MODES = [
    "loop", "full", "both", "full-evolve",
    "variables", "equations", "mutation", "repair",
    "auto-mutation", "auto-repair",
    "prepare-mutation", "auto-prepare-mutation", "auto-consolidate-mutation",
    "lineages", "status", "merge-sessions"
]


class LauncherFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title=LANGS["en"]["app_title"], size=(1120, 860))
        self.lang = "en"
        self.process: subprocess.Popen[str] | None = None
        self.stop_requested = False
        self.prepared_target_equation = ""
        self.active_session_name = ""
        self.sessions: list[str] = []
        self.current_equation_rows: list[dict[str, Any]] = []
        self.current_variable_rows: list[dict[str, Any]] = []
        self.current_test_rows: list[dict[str, Any]] = []
        self.current_test_all_rows: list[dict[str, Any]] = []
        self.current_report_state = "idle"
        self.legend_chips: list[wx.StaticText] = []

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(root)

        header = wx.BoxSizer(wx.VERTICAL)
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.status_label = wx.StaticText(panel, label=self.tr("status_ready"))
        self.status_label.Wrap(520)
        top.Add(self.status_label, 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)

        lang_box = wx.BoxSizer(wx.HORIZONTAL)
        self.language_label = wx.StaticText(panel, label=self.tr("language"))
        lang_box.Add(self.language_label, 0, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 6)
        self.language_choice = wx.Choice(panel, choices=["English", "Français"])
        self.language_choice.SetSelection(0)
        self.language_choice.Bind(wx.EVT_CHOICE, self.on_language_changed)
        lang_box.Add(self.language_choice, 0, wx.ALIGN_CENTER_VERTICAL)
        top.Add(lang_box, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 8)
        header.Add(top, 0, wx.EXPAND)

        badge_grid = wx.FlexGridSizer(2, 2, 8, 8)
        badge_grid.AddGrowableCol(0, 1)
        badge_grid.AddGrowableCol(1, 1)
        self.run_state_badge = wx.TextCtrl(panel, value="RUN STATE: IDLE", style=wx.TE_READONLY | wx.BORDER_NONE)
        self.session_badge = wx.TextCtrl(panel, value="Session: -", style=wx.TE_READONLY | wx.BORDER_NONE)
        self.target_badge = wx.TextCtrl(panel, value="Target: -", style=wx.TE_READONLY | wx.BORDER_NONE)
        self.engine_badge = wx.TextCtrl(panel, value="Engine: -", style=wx.TE_READONLY | wx.BORDER_NONE)
        for badge in [self.run_state_badge, self.session_badge, self.target_badge, self.engine_badge]:
            badge.SetMinSize((260, -1))
            badge_grid.Add(badge, 1, wx.EXPAND)
        header.Add(badge_grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        legend_box = wx.StaticBox(panel, label="Color Legend")
        self.legend_box = legend_box
        legend_sizer = wx.StaticBoxSizer(legend_box, wx.HORIZONTAL)
        for text, color in [
            ("Ready / Strong Approved", UI_THEME["ready"]),
            ("Approved", UI_THEME["approved"]),
            ("Partial / Candidate", UI_THEME["partial"]),
            ("Fallback / Pending", UI_THEME["fallback"]),
            ("Needs Repair / Blocked", UI_THEME["blocked"]),
        ]:
            chip = wx.StaticText(panel, label=f"■ {text}")
            chip.SetForegroundColour(color)
            self.legend_chips.append(chip)
            legend_sizer.Add(chip, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        header.Add(legend_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        root.Add(header, 0, wx.EXPAND)

        self.notebook = wx.Notebook(panel)
        root.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 8)

        self.controls_tab = scrolled.ScrolledPanel(self.notebook, style=wx.TAB_TRAVERSAL)
        self.controls_tab.SetupScrolling(scroll_x=False, scroll_y=True)
        self.science_tab = wx.Panel(self.notebook)
        self.equations_tab = wx.Panel(self.notebook)
        self.variables_tab = wx.Panel(self.notebook)
        self.eqtest_tab = wx.Panel(self.notebook)
        self.logs_tab = wx.Panel(self.notebook)
        self.notebook.AddPage(self.controls_tab, self.tr("tab_controls"))
        self.notebook.AddPage(self.science_tab, self.tr("tab_science"))
        self.notebook.AddPage(self.equations_tab, self.tr("tab_equations"))
        self.notebook.AddPage(self.variables_tab, self.tr("tab_variables"))
        self.notebook.AddPage(self.eqtest_tab, self.tr("tab_eqtest"))
        self.notebook.AddPage(self.logs_tab, self.tr("tab_logs"))

        self.build_controls_tab()
        self.build_science_tab()
        self.build_equations_tab()
        self.build_variables_tab()
        self.build_eqtest_tab()
        self.build_logs_tab()

        self.refresh_sessions()
        self.update_mode_view()
        self.update_cmd_preview()
        self.refresh_header_badges()
        self.apply_theme()
        self.Layout()
        self.Centre()

    def tr(self, key: str) -> str:
        return LANGS.get(self.lang, LANGS["en"]).get(key, key)

    def _safe_set_colors(self, widget, bg=None, fg=None) -> None:
        try:
            try:
                widget.SetThemeEnabled(False)
            except Exception:
                pass
            try:
                widget.SetBackgroundStyle(wx.BG_STYLE_PAINT)
            except Exception:
                pass

            if bg is not None:
                try:
                    widget.SetOwnBackgroundColour(bg)
                except Exception:
                    pass
                widget.SetBackgroundColour(bg)

            if fg is not None:
                try:
                    widget.SetOwnForegroundColour(fg)
                except Exception:
                    pass
                widget.SetForegroundColour(fg)

            try:
                widget.Refresh()
                widget.Update()
            except Exception:
                pass
        except Exception:
            pass

    def _apply_theme_recursive(self, widget) -> None:
        try:
            import wx.lib.scrolledpanel as scrolled
        except Exception:
            scrolled = None

        try:
            if isinstance(widget, wx.Frame):
                self._safe_set_colors(widget, UI_THEME["frame_bg"], UI_THEME["text"])
            elif scrolled is not None and isinstance(widget, scrolled.ScrolledPanel):
                self._safe_set_colors(widget, UI_THEME["panel_bg"], UI_THEME["text"])
            elif isinstance(widget, wx.Panel):
                self._safe_set_colors(widget, UI_THEME["panel_bg"], UI_THEME["text"])
            elif isinstance(widget, wx.SplitterWindow):
                self._safe_set_colors(widget, UI_THEME["panel_bg"], UI_THEME["text"])
            elif isinstance(widget, wx.Notebook):
                self._safe_set_colors(widget, UI_THEME["panel_bg"], UI_THEME["text"])
                try:
                    for i in range(widget.GetPageCount()):
                        page = widget.GetPage(i)
                        if page is not None:
                            self._safe_set_colors(page, UI_THEME["panel_bg"], UI_THEME["text"])
                except Exception:
                    pass
            elif isinstance(widget, wx.StaticBox):
                self._safe_set_colors(widget, UI_THEME["panel_bg"], UI_THEME["text"])
            elif isinstance(widget, wx.StaticText):
                self._safe_set_colors(widget, None, UI_THEME["text"])
            elif isinstance(widget, wx.TextCtrl):
                bg = UI_THEME["input_bg"]
                if widget.GetWindowStyle() & wx.BORDER_NONE:
                    bg = UI_THEME["panel_alt"]
                if widget.GetWindowStyle() & wx.TE_READONLY:
                    bg = UI_THEME["input_bg"]
                self._safe_set_colors(widget, bg, UI_THEME["text"])
                try:
                    widget.SetDefaultStyle(wx.TextAttr(UI_THEME["text"], bg))
                except Exception:
                    pass
            elif isinstance(widget, (wx.Choice, wx.ComboBox, wx.ListBox, wx.TreeCtrl)):
                self._safe_set_colors(widget, UI_THEME["input_bg"], UI_THEME["text"])
            elif isinstance(widget, wx.Button):
                self._safe_set_colors(widget, UI_THEME["panel_alt"], UI_THEME["text"])
        except Exception:
            pass

        try:
            children = widget.GetChildren()
        except Exception:
            children = []
        for child in children:
            self._apply_theme_recursive(child)

    def apply_legend_theme(self) -> None:
        for chip, color_key in zip(
            self.legend_chips,
            ["ready", "approved", "partial", "fallback", "blocked"],
        ):
            try:
                chip.SetForegroundColour(UI_THEME[color_key])
            except Exception:
                pass

    def _semantic_pair(self, key: str) -> tuple[wx.Colour, wx.Colour]:
        mapping = {
            "ready": (UI_THEME["ready_bg"], UI_THEME["ready"]),
            "approved": (UI_THEME["approved_bg"], UI_THEME["approved"]),
            "partial": (UI_THEME["partial_bg"], UI_THEME["partial"]),
            "fallback": (UI_THEME["fallback_bg"], UI_THEME["fallback"]),
            "blocked": (UI_THEME["blocked_bg"], UI_THEME["blocked"]),
            "neutral": (UI_THEME["panel_alt"], UI_THEME["text"]),
        }
        return mapping.get(key, mapping["neutral"])

    def _semantic_key_for_text(self, text: str) -> str:
        if not text:
            return "neutral"

        t = text.lower()
        ui_noise = [
            "session", "engine", "target", "filter", "view",
            "controls", "scientific", "logs", "parameters",
            "cycles", "turns", "strategy", "language",
        ]
        if any(word in t for word in ui_noise):
            return "neutral"
        if "ready for mutation" in t:
            return "approved"
        if "approved" in t or "stable" in t:
            return "approved"
        if "candidate" in t or "partial" in t:
            return "partial"
        if "fallback" in t or "pending" in t:
            return "fallback"
        if "repair" in t or "blocked" in t or "failed" in t:
            return "blocked"
        return "neutral"

    def _polish_control(self, ctrl) -> None:
        if ctrl is None:
            return
        try:
            if isinstance(ctrl, wx.ListBox):
                try:
                    ctrl.SetSelectionBackground(UI_THEME["approved_bg"])
                    ctrl.SetSelectionForeground(UI_THEME["approved"])
                except Exception:
                    pass
            elif isinstance(ctrl, wx.TreeCtrl):
                try:
                    ctrl.SetSelectionBackground(UI_THEME["approved_bg"])
                    ctrl.SetSelectionForeground(UI_THEME["approved"])
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_semantic_foreground_to_textctrl(self, ctrl) -> None:
        if ctrl is None:
            return
        try:
            raw = ctrl.GetValue()
        except Exception:
            return

        lines = raw.splitlines()
        try:
            ctrl.Freeze()
        except Exception:
            pass
        try:
            ctrl.SetValue("")
            for line in lines:
                key = self._semantic_key_for_text(line)
                _, fg = self._semantic_pair(key)
                if key == "neutral":
                    fg = UI_THEME["text"]
                try:
                    ctrl.SetDefaultStyle(wx.TextAttr(fg, UI_THEME["input_bg"]))
                except Exception:
                    pass
                ctrl.AppendText(line + "\n")
        except Exception:
            try:
                ctrl.SetValue(raw)
            except Exception:
                pass
        finally:
            try:
                ctrl.Thaw()
            except Exception:
                pass

    def _apply_plain_dark_textctrl(self, ctrl) -> None:
        if ctrl is None:
            return
        try:
            raw = ctrl.GetValue()
        except Exception:
            return
        try:
            ctrl.Freeze()
        except Exception:
            pass
        try:
            ctrl.SetValue("")
            try:
                ctrl.SetDefaultStyle(wx.TextAttr(UI_THEME["text"], UI_THEME["input_bg"]))
            except Exception:
                pass
            if raw:
                ctrl.AppendText(raw)
        except Exception:
            try:
                ctrl.SetValue(raw)
            except Exception:
                pass
        finally:
            try:
                ctrl.Thaw()
            except Exception:
                pass

    def force_refresh_theme(self) -> None:
        try:
            self.Layout()
            self.Refresh()
            self.Update()
        except Exception:
            pass

    def apply_theme(self) -> None:
        self._apply_theme_recursive(self)
        self.apply_legend_theme()

        # Keep normal UI labels neutral. Do NOT spread semantic blue everywhere.
        neutral_label_names = [
            "language_label", "science_view_label", "science_graph_label",
            "eq_filter_label", "eq_tree_label", "eq_list_label",
            "var_filter_label", "var_tree_label", "var_list_label",
            "test_filter_label", "test_tree_label", "test_list_label",
            "session_strategy_label", "session_label", "cycles_label",
            "variable_turns_label", "equation_turns_label", "mutation_turns_label",
            "lineage_limit_label", "steps_label", "prepare_steps_label"
        ]
        for name in neutral_label_names:
            ctrl = getattr(self, name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, None, UI_THEME["text"])

        # Secondary labels stay muted.
        for name in ["status_label", "current_selection_text", "merge_hint_label"]:
            ctrl = getattr(self, name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, None, UI_THEME["muted"])

        # Header badges only: semantic background colors live here.
        target_value = self.target_badge.GetValue() if hasattr(self, "target_badge") else ""
        target_key = "neutral" if str(target_value).strip() in {"Target: -", "-", ""} else "partial"
        badge_specs = [
            ("run_state_badge", self._semantic_key_for_text(self.run_state_badge.GetValue() if hasattr(self, "run_state_badge") else "")),
            ("session_badge", "approved"),
            ("target_badge", target_key),
            ("engine_badge", self._semantic_key_for_text(self.engine_badge.GetValue() if hasattr(self, "engine_badge") else "")),
        ]
        for name, key in badge_specs:
            ctrl = getattr(self, name, None)
            if ctrl is None:
                continue
            bg, fg = self._semantic_pair(key)
            self._safe_set_colors(ctrl, bg, fg)
            try:
                ctrl.SetDefaultStyle(wx.TextAttr(fg, bg))
            except Exception:
                pass

        # Trees and lists keep a stable dark base.
        for ctrl_name in ["lineage_tree", "eq_tree", "var_tree", "test_tree"]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["input_bg"], UI_THEME["text"])
                self._polish_control(ctrl)

        for ctrl_name in ["eq_list", "var_list", "test_list"]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["input_bg"], UI_THEME["text"])
                self._polish_control(ctrl)

        # Detail areas: dark background, semantic foreground only.
        # Keep semantic coloring focused on equation/variable/test/scientific content.
        for ctrl_name in ["science_text", "lineage_detail", "eq_details", "var_details", "test_report"]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["input_bg"], UI_THEME["text"])
                self._apply_plain_dark_textctrl(ctrl)

        # Plain dark text areas.
        for ctrl_name in ["log_output", "cmd_preview"]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["input_bg"], UI_THEME["text"])
                self._apply_plain_dark_textctrl(ctrl)

        # Dropdowns / combos / choices remain neutral.
        for ctrl_name in [
            "mode_choice", "loop_profile_choice", "session_choice",
            "science_session", "science_view_choice", "eq_session", "eq_filter",
            "var_session", "var_filter", "test_session", "test_equation_choice",
            "test_filter_choice", "language_choice"
        ]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["input_bg"], UI_THEME["text"])

        # Radio groups / misc controls
        for ctrl_name in ["session_mode", "merge_target_mode"]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["panel_bg"], UI_THEME["text"])

        # Buttons remain neutral.
        for ctrl_name in [
            "open_science_button", "refresh_button",
            "science_use_active", "science_refresh",
            "eq_use_active", "eq_refresh", "eq_copy_btn", "eq_like_btn", "eq_dislike_btn", "eq_prepare_repair_btn", "eq_prepare_mutation_btn",
            "var_use_active", "var_refresh", "var_copy_btn", "var_like_btn", "var_dislike_btn", "var_prepare_repair_btn", "var_prepare_mutation_btn",
            "test_use_active", "test_use_selected_equation", "test_refresh", "test_run", "test_copy_report_btn", "test_prepare_repair_btn", "test_prepare_mutation_btn",
            "clear_logs_button", "test_button", "run_button", "stop_button", "reset_button"
        ]:
            ctrl = getattr(self, ctrl_name, None)
            if ctrl is not None:
                self._safe_set_colors(ctrl, UI_THEME["panel_alt"], UI_THEME["text"])

        # Static boxes neutral too.
        for box_name in ["legend_box", "mode_box", "profile_box", "session_box", "params_box", "prepare_box", "merge_box"]:
            box = getattr(self, box_name, None)
            if box is not None:
                self._safe_set_colors(box, UI_THEME["panel_bg"], UI_THEME["text"])

        # Equation Test status labels: semantic foreground only.
        for name in ["test_state_label", "test_verdict_label", "test_selected_label", "test_state_label_right"]:
            ctrl = getattr(self, name, None)
            if ctrl is not None:
                key = self._semantic_key_for_text(ctrl.GetLabel())
                _, fg = self._semantic_pair(key)
                self._safe_set_colors(ctrl, None, fg)

        wx.CallAfter(self.force_refresh_theme)

    def build_controls_tab(self) -> None:
        panel = self.controls_tab
        v = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(v)

        # Mode box
        self.mode_box = wx.StaticBox(panel, label=self.tr("mode"))
        mode_sizer = wx.StaticBoxSizer(self.mode_box, wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.mode_choice = wx.Choice(panel, choices=MODES)
        self.mode_choice.SetSelection(0)
        self.mode_choice.Bind(wx.EVT_CHOICE, self.on_mode_changed)
        row.Add(self.mode_choice, 0, wx.ALL, 6)
        self.open_science_button = wx.Button(panel, label=self.tr("open_science"))
        self.open_science_button.Bind(wx.EVT_BUTTON, lambda evt: self.notebook.SetSelection(1))
        row.Add(self.open_science_button, 0, wx.ALL, 6)
        mode_sizer.Add(row, 0, wx.EXPAND)
        v.Add(mode_sizer, 0, wx.EXPAND | wx.ALL, 8)

        self.profile_box = wx.StaticBox(panel, label=self.tr("loop_profile"))
        profile_sizer = wx.StaticBoxSizer(self.profile_box, wx.VERTICAL)
        profile_row = wx.BoxSizer(wx.HORIZONTAL)
        self.loop_profile_choice = wx.Choice(panel, choices=["Custom", "Mini", "Normal", "Deep"])
        self.loop_profile_choice.SetSelection(2)
        self.loop_profile_choice.Bind(wx.EVT_CHOICE, self.on_loop_profile_changed)
        profile_row.Add(self.loop_profile_choice, 0, wx.ALL, 6)
        profile_sizer.Add(profile_row, 0, wx.EXPAND)
        v.Add(profile_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Session strategy
        self.session_box = wx.StaticBox(panel, label=self.tr("working_session"))
        sess_sizer = wx.StaticBoxSizer(self.session_box, wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 8, 8)
        grid.AddGrowableCol(1, 1)
        self.session_strategy_label = wx.StaticText(panel, label=self.tr("session_strategy"))
        grid.Add(self.session_strategy_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.session_strategy = wx.Choice(panel, choices=[self.tr("session_selected"), self.tr("session_latest"), self.tr("session_new")])
        self.session_strategy.SetSelection(0)
        self.session_strategy.Bind(wx.EVT_CHOICE, self.on_any_input_changed)
        grid.Add(self.session_strategy, 1, wx.EXPAND)
        self.session_label = wx.StaticText(panel, label=self.tr("session"))
        grid.Add(self.session_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.session_choice = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.session_choice.Bind(wx.EVT_COMBOBOX, self.on_any_input_changed)
        grid.Add(self.session_choice, 1, wx.EXPAND)
        sess_sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 4)
        self.refresh_button = wx.Button(panel, label=self.tr("refresh_sessions"))
        self.refresh_button.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_sessions())
        sess_sizer.Add(self.refresh_button, 0, wx.ALL, 4)
        v.Add(sess_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Parameters
        self.params_box = wx.StaticBox(panel, label=self.tr("params"))
        params_sizer = wx.StaticBoxSizer(self.params_box, wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 8, 8)
        grid.AddGrowableCol(1, 1)
        self.cycles_label = wx.StaticText(panel, label=self.tr("cycles"))
        self.cycles_input = wx.TextCtrl(panel, value="1")
        self.variable_turns_label = wx.StaticText(panel, label=self.tr("variable_turns"))
        self.variable_turns_input = wx.TextCtrl(panel, value="1")
        self.equation_turns_label = wx.StaticText(panel, label=self.tr("equation_turns"))
        self.equation_turns_input = wx.TextCtrl(panel, value="1")
        self.mutation_turns_label = wx.StaticText(panel, label=self.tr("mutation_turns"))
        self.mutation_turns_input = wx.TextCtrl(panel, value="1")
        self.lineage_limit_label = wx.StaticText(panel, label=self.tr("lineage_limit"))
        self.lineage_limit_input = wx.TextCtrl(panel, value="5")
        self.steps_label = wx.StaticText(panel, label=self.tr("steps"))
        self.steps_input = wx.TextCtrl(panel, value="3")
        self.prepare_steps_label = wx.StaticText(panel, label=self.tr("prepare_steps"))
        self.prepare_steps_input = wx.TextCtrl(panel, value="3")
        for label, ctrl in [
            (self.cycles_label, self.cycles_input),
            (self.variable_turns_label, self.variable_turns_input),
            (self.equation_turns_label, self.equation_turns_input),
            (self.mutation_turns_label, self.mutation_turns_input),
            (self.lineage_limit_label, self.lineage_limit_input),
            (self.steps_label, self.steps_input),
            (self.prepare_steps_label, self.prepare_steps_input),
        ]:
            ctrl.Bind(wx.EVT_TEXT, self.on_any_input_changed)
            grid.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
        params_sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 4)
        v.Add(params_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Merge
        self.merge_box = wx.StaticBox(panel, label=self.tr("merge_sessions"))
        merge_sizer = wx.StaticBoxSizer(self.merge_box, wx.VERTICAL)
        grid = wx.FlexGridSizer(0, 2, 8, 8)
        grid.AddGrowableCol(1, 1)
        self.session_a_label = wx.StaticText(panel, label=self.tr("session_a"))
        self.session_a_choice = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.session_a_choice.Bind(wx.EVT_COMBOBOX, self.on_any_input_changed)
        self.session_b_label = wx.StaticText(panel, label=self.tr("session_b"))
        self.session_b_choice = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.session_b_choice.Bind(wx.EVT_COMBOBOX, self.on_any_input_changed)
        self.merge_hint_label = wx.StaticText(panel, label="")
        for label, ctrl in [
            (self.session_a_label, self.session_a_choice),
            (self.session_b_label, self.session_b_choice),
        ]:
            grid.Add(label, 0, wx.ALIGN_CENTER_VERTICAL)
            grid.Add(ctrl, 1, wx.EXPAND)
        merge_sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 4)
        
        # Multi-Merge section
        self.multi_merge_box = wx.StaticBox(panel, label=self.tr("multi_merge_sessions"))
        multi_merge_sizer = wx.StaticBoxSizer(self.multi_merge_box, wx.VERTICAL)
        multi_grid = wx.FlexGridSizer(0, 2, 8, 8)
        multi_grid.AddGrowableCol(1, 1)
        
        self.multi_merge_label = wx.StaticText(panel, label=self.tr("selected_sessions_for_multi_merge"))
        
        # Select All / Deselect All buttons in a horizontal sizer
        select_deselect_box = wx.BoxSizer(wx.HORIZONTAL)
        self.select_all_btn = wx.Button(panel, label="Select All")
        self.select_all_btn.Bind(wx.EVT_BUTTON, self.on_select_all_sessions)
        select_deselect_box.Add(self.select_all_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)
        
        self.deselect_all_btn = wx.Button(panel, label="Deselect All")
        self.deselect_all_btn.Bind(wx.EVT_BUTTON, self.on_deselect_all_sessions)
        select_deselect_box.Add(self.deselect_all_btn, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 4)

        self.multi_merge_checklist = wx.CheckListBox(panel, choices=[])
        self.multi_merge_checklist.Bind(wx.EVT_CHECKLISTBOX, self.on_any_input_changed)
        
        multi_grid.Add(self.multi_merge_label, 0, wx.EXPAND | wx.ALL, 2)
        multi_grid.Add(select_deselect_box, 0, wx.EXPAND | wx.ALL, 2)
        multi_grid.Add(self.multi_merge_checklist, 1, wx.EXPAND | wx.ALL, 4)

        
        # Perform Multi-Merge button
        self.perform_multi_merge_btn = wx.Button(panel, label=self.tr("perform_multi_merge"))
        self.perform_multi_merge_btn.Bind(wx.EVT_BUTTON, self.on_perform_multi_merge)
        
        multi_merge_sizer.Add(multi_grid, 0, wx.EXPAND | wx.ALL, 4)
        multi_merge_sizer.Add(self.perform_multi_merge_btn, 0, wx.ALL | wx.ALIGN_CENTER, 8)
        
        v.Add(merge_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        v.Add(multi_merge_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        # Preview + buttons
        self.preview_box = wx.StaticBox(panel, label=self.tr("preview"))
        preview_sizer = wx.StaticBoxSizer(self.preview_box, wx.VERTICAL)
        self.cmd_preview = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        self.cmd_preview.SetMinSize((-1, 80))
        preview_sizer.Add(self.cmd_preview, 1, wx.EXPAND | wx.ALL, 4)
        v.Add(preview_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.test_button = wx.Button(panel, label=self.tr("test_launcher"))
        self.test_button.Bind(wx.EVT_BUTTON, self.on_test_launcher)
        self.run_button = wx.Button(panel, label=self.tr("run"))
        self.run_button.Bind(wx.EVT_BUTTON, self.on_run)
        self.stop_button = wx.Button(panel, label=self.tr("stop"))
        self.stop_button.Bind(wx.EVT_BUTTON, self.on_stop)
        self.stop_button.Enable(False)
        self.reset_button = wx.Button(panel, label=self.tr("reset"))
        self.reset_button.Bind(wx.EVT_BUTTON, self.on_reset)
        for b in [self.test_button, self.run_button, self.stop_button, self.reset_button]:
            btns.Add(b, 0, wx.ALL, 4)
        v.Add(btns, 0, wx.ALL, 8)

    def build_science_tab(self) -> None:
        panel = self.science_tab
        v = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(v)
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.science_session = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.science_session.Bind(wx.EVT_COMBOBOX, lambda evt: self.refresh_scientific_view())
        self.science_view_label = wx.StaticText(panel, label="View")
        self.science_view_choice = wx.Choice(panel, choices=["Equations", "Variables"])
        self.science_view_choice.SetSelection(0)
        self.science_view_choice.Bind(wx.EVT_CHOICE, lambda evt: self.refresh_scientific_view())
        self.science_use_active = wx.Button(panel, label=self.tr("use_active"))
        self.science_use_active.Bind(wx.EVT_BUTTON, self.on_use_active_science)
        self.science_refresh = wx.Button(panel, label=self.tr("refresh_view"))
        self.science_refresh.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_scientific_view())
        for c in [self.science_session, self.science_view_label, self.science_view_choice, self.science_use_active, self.science_refresh]:
            top.Add(c, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        v.Add(top, 0, wx.EXPAND)

        self.current_selection_box = wx.StaticBox(panel, label="Current Selection")
        current_selection_sizer = wx.StaticBoxSizer(self.current_selection_box, wx.VERTICAL)
        self.current_selection_text = wx.StaticText(panel, label="Session: -\nTarget: -\nEngine: -")
        current_selection_sizer.Add(self.current_selection_text, 0, wx.EXPAND | wx.ALL, 6)
        v.Add(current_selection_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.science_splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE)
        self.science_splitter.SetMinimumPaneSize(160)
        upper = wx.Panel(self.science_splitter)
        lower = wx.Panel(self.science_splitter)
        upper_sizer = wx.BoxSizer(wx.VERTICAL)
        lower_sizer = wx.BoxSizer(wx.VERTICAL)
        upper.SetSizer(upper_sizer)
        lower.SetSizer(lower_sizer)

        self.science_text = wx.TextCtrl(upper, style=wx.TE_MULTILINE | wx.TE_READONLY)
        upper_sizer.Add(self.science_text, 1, wx.EXPAND | wx.ALL, 8)

        graph_label = wx.StaticText(lower, label="Scientific Tree / Lineage")
        lower_sizer.Add(graph_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.science_graph_label = graph_label
        self.science_graph_splitter = wx.SplitterWindow(lower, style=wx.SP_LIVE_UPDATE)
        self.science_graph_splitter.SetMinimumPaneSize(180)
        tree_panel = wx.Panel(self.science_graph_splitter)
        detail_panel = wx.Panel(self.science_graph_splitter)
        tree_sizer = wx.BoxSizer(wx.VERTICAL)
        detail_sizer = wx.BoxSizer(wx.VERTICAL)
        tree_panel.SetSizer(tree_sizer)
        detail_panel.SetSizer(detail_sizer)
        self.lineage_tree = wx.TreeCtrl(tree_panel, style=wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_DEFAULT_STYLE)
        self.lineage_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_science_tree_selected)
        tree_sizer.Add(self.lineage_tree, 1, wx.EXPAND | wx.ALL, 4)
        self.lineage_detail = wx.TextCtrl(detail_panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        detail_sizer.Add(self.lineage_detail, 1, wx.EXPAND | wx.ALL, 4)
        self.science_graph_splitter.SplitVertically(tree_panel, detail_panel, 430)
        lower_sizer.Add(self.science_graph_splitter, 1, wx.EXPAND | wx.ALL, 4)

        self.science_splitter.SplitHorizontally(upper, lower, 300)
        v.Add(self.science_splitter, 1, wx.EXPAND | wx.ALL, 8)
        self.science_lineage_lookup: dict[str, dict[str, Any]] = {}

    def build_equations_tab(self) -> None:
        panel = self.equations_tab
        v = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(v)
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.eq_session = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.eq_session.Bind(wx.EVT_COMBOBOX, lambda evt: self.refresh_equations_view())
        self.eq_use_active = wx.Button(panel, label=self.tr("use_active"))
        self.eq_use_active.Bind(wx.EVT_BUTTON, self.on_use_active_equations)
        self.eq_filter_label = wx.StaticText(panel, label=self.tr("equation_filter"))
        self.eq_filter = wx.Choice(panel, choices=[self.tr("all"), self.tr("approved"), self.tr("partial"), self.tr("needs_repair"), self.tr("mutable")])
        self.eq_filter.SetSelection(0)
        self.eq_filter.Bind(wx.EVT_CHOICE, lambda evt: self.refresh_equations_view())
        self.eq_refresh = wx.Button(panel, label=self.tr("refresh_view"))
        self.eq_refresh.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_equations_view())
        for c in [self.eq_session, self.eq_use_active, self.eq_filter_label, self.eq_filter, self.eq_refresh]:
            top.Add(c, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        v.Add(top, 0, wx.EXPAND)
        self.eq_splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(self.eq_splitter)
        right = wx.Panel(self.eq_splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        left.SetSizer(left_sizer)
        right.SetSizer(right_sizer)
        self.eq_tree_label = wx.StaticText(left, label="Equation Tree")
        left_sizer.Add(self.eq_tree_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.eq_tree = wx.TreeCtrl(left, style=wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_DEFAULT_STYLE)
        self.eq_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_equation_tree_selected)
        left_sizer.Add(self.eq_tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        self.eq_list_label = wx.StaticText(left, label="Equation List")
        left_sizer.Add(self.eq_list_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.eq_list = wx.ListBox(left)
        self.eq_list.Bind(wx.EVT_LISTBOX, self.on_equation_selected)
        left_sizer.Add(self.eq_list, 1, wx.EXPAND | wx.ALL, 4)
        self.eq_details = wx.TextCtrl(right, style=wx.TE_MULTILINE | wx.TE_READONLY)
        right_sizer.Add(self.eq_details, 1, wx.EXPAND | wx.ALL, 4)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.eq_copy_btn = wx.Button(right, label=self.tr("copy_equation"))
        self.eq_copy_btn.Bind(wx.EVT_BUTTON, self.on_copy_equation)
        self.eq_like_btn = wx.Button(right, label=self.tr("like"))
        self.eq_like_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_equation_feedback(+1))
        self.eq_dislike_btn = wx.Button(right, label=self.tr("dislike"))
        self.eq_dislike_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_equation_feedback(-1))
        self.eq_prepare_repair_btn = wx.Button(right, label=self.tr("prepare_repair"))
        self.eq_prepare_repair_btn.Bind(wx.EVT_BUTTON, self.on_prepare_repair)
        self.eq_prepare_mutation_btn = wx.Button(right, label=self.tr("prepare_mutation"))
        self.eq_prepare_mutation_btn.Bind(wx.EVT_BUTTON, self.on_prepare_mutation)
        for b in [self.eq_copy_btn, self.eq_like_btn, self.eq_dislike_btn, self.eq_prepare_repair_btn, self.eq_prepare_mutation_btn]:
            btns.Add(b, 0, wx.ALL, 4)
        right_sizer.Add(btns, 0, wx.ALL, 4)
        self.eq_splitter.SplitVertically(left, right, 420)
        v.Add(self.eq_splitter, 1, wx.EXPAND | wx.ALL, 8)

    def build_variables_tab(self) -> None:
        panel = self.variables_tab
        v = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(v)
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.var_session = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.var_session.Bind(wx.EVT_COMBOBOX, lambda evt: self.refresh_variables_view())
        self.var_use_active = wx.Button(panel, label=self.tr("use_active"))
        self.var_use_active.Bind(wx.EVT_BUTTON, self.on_use_active_variables)
        self.var_filter_label = wx.StaticText(panel, label=self.tr("variable_filter"))
        self.var_filter = wx.Choice(panel, choices=[self.tr("all"), self.tr("approved"), self.tr("candidate"), self.tr("linked"), self.tr("unused")])
        self.var_filter.SetSelection(0)
        self.var_filter.Bind(wx.EVT_CHOICE, lambda evt: self.refresh_variables_view())
        self.var_refresh = wx.Button(panel, label=self.tr("refresh_view"))
        self.var_refresh.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_variables_view())
        for c in [self.var_session, self.var_use_active, self.var_filter_label, self.var_filter, self.var_refresh]:
            top.Add(c, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        v.Add(top, 0, wx.EXPAND)
        self.var_splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(self.var_splitter)
        right = wx.Panel(self.var_splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        left.SetSizer(left_sizer)
        right.SetSizer(right_sizer)
        self.var_tree_label = wx.StaticText(left, label="Variable Tree")
        left_sizer.Add(self.var_tree_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.var_tree = wx.TreeCtrl(left, style=wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_DEFAULT_STYLE)
        self.var_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_variable_tree_selected)
        left_sizer.Add(self.var_tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        self.var_list_label = wx.StaticText(left, label="Variable List")
        left_sizer.Add(self.var_list_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.var_list = wx.ListBox(left)
        self.var_list.Bind(wx.EVT_LISTBOX, self.on_variable_selected)
        left_sizer.Add(self.var_list, 1, wx.EXPAND | wx.ALL, 4)
        self.var_details = wx.TextCtrl(right, style=wx.TE_MULTILINE | wx.TE_READONLY)
        right_sizer.Add(self.var_details, 1, wx.EXPAND | wx.ALL, 4)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.var_copy_btn = wx.Button(right, label=self.tr("copy_variable"))
        self.var_copy_btn.Bind(wx.EVT_BUTTON, self.on_copy_variable)
        self.var_like_btn = wx.Button(right, label=self.tr("like"))
        self.var_like_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_variable_feedback(+1))
        self.var_dislike_btn = wx.Button(right, label=self.tr("dislike"))
        self.var_dislike_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_variable_feedback(-1))
        self.var_prepare_repair_btn = wx.Button(right, label=self.tr("variable_prepare_repair"))
        self.var_prepare_repair_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_variable_prepare("repair"))
        self.var_prepare_mutation_btn = wx.Button(right, label=self.tr("variable_prepare_mutation"))
        self.var_prepare_mutation_btn.Bind(wx.EVT_BUTTON, lambda evt: self.on_variable_prepare("mutation"))
        for b in [self.var_copy_btn, self.var_like_btn, self.var_dislike_btn, self.var_prepare_repair_btn, self.var_prepare_mutation_btn]:
            btns.Add(b, 0, wx.ALL, 4)
        right_sizer.Add(btns, 0, wx.ALL, 4)
        self.var_splitter.SplitVertically(left, right, 420)
        v.Add(self.var_splitter, 1, wx.EXPAND | wx.ALL, 8)

    def _apply_tree_item_colors(self, tree, item, payload: dict[str, Any] | None = None, *, kind: str = "equation") -> None:
        payload = payload or {}
        try:
            if kind == "root":
                tree.SetItemTextColour(item, UI_THEME["root_text"])
                tree.SetItemBold(item, True)
                return
            if kind == "bucket":
                label = str(payload.get("bucket", "") or "").lower()
                mapping = {
                    "approved": UI_THEME["ready"],
                    "candidate": UI_THEME["partial"],
                    "pending": UI_THEME["fallback"],
                    "fallback": UI_THEME["fallback"],
                    "needs_repair": UI_THEME["blocked"],
                }
                fg = mapping.get(label, UI_THEME["text"])
                tree.SetItemTextColour(item, fg)
                tree.SetItemBold(item, True)
                return
            if kind == "variable":
                bucket = str(payload.get("_bucket", payload.get("status", "")) or "").lower()
                quality = int(payload.get("quality_score", payload.get("quality", 0)) or 0)
                if bucket == "approved":
                    fg = UI_THEME["ready"]
                elif bucket in {"candidate", "linked"}:
                    fg = UI_THEME["partial"]
                else:
                    fg = UI_THEME["fallback"]
                if quality >= 90:
                    tree.SetItemBold(item, True)
                tree.SetItemTextColour(item, fg)
                return
            approved = bool(payload.get("approved", False))
            repair = bool(payload.get("repair_required", False))
            fallback = bool(payload.get("fallback_used", False))
            stable = bool(payload.get("stable_parent", False))
            mutable = approved and stable and not repair and not fallback
            partial = str(payload.get("status", "") or "").lower() == "partial"
            if repair:
                fg = UI_THEME["blocked"]
            elif mutable:
                fg = UI_THEME["ready"]
            elif approved:
                fg = UI_THEME["approved"]
            elif fallback:
                fg = UI_THEME["fallback"]
            elif partial:
                fg = UI_THEME["partial"]
            else:
                fg = UI_THEME["text"]
            tree.SetItemTextColour(item, fg)
            if mutable or approved:
                tree.SetItemBold(item, True)
        except Exception:
            pass

    def _populate_equation_tree(self, rows: list[dict[str, Any]]) -> None:
        if not hasattr(self, "eq_tree"):
            return
        self.eq_tree.DeleteAllItems()
        root = self.eq_tree.AddRoot("Equations")
        self._apply_tree_item_colors(self.eq_tree, root, kind="root")
        by_equation = {str(r.get("equation", "") or "").strip(): dict(r) for r in rows if str(r.get("equation", "") or "").strip()}
        appended = set()

        def label_for(row: dict[str, Any]) -> str:
            eq = str(row.get("equation", "") or "").strip()
            status = str(row.get("status", "") or "").strip()
            turn = int(row.get("source_turn", 0) or 0)
            flags = [f"T{turn:03d}"] if turn else []
            if status:
                flags.append(status)
            if bool(row.get("repair_required", False)):
                flags.append("repair")
            if bool(row.get("fallback_used", False)):
                flags.append("fallback")
            return eq if not flags else f"{eq} | {' | '.join(flags)}"

        def add_branch(eq: str, parent_item):
            if eq in appended:
                return
            row = by_equation.get(eq, {})
            item = self.eq_tree.AppendItem(parent_item, label_for(row))
            self.eq_tree.SetItemData(item, eq)
            self._apply_tree_item_colors(self.eq_tree, item, row, kind="equation")
            appended.add(eq)
            children = [child_eq for child_eq, child_row in by_equation.items() if str(child_row.get("parent_equation", "") or "").strip() == eq]
            for child_eq in sorted(children):
                add_branch(child_eq, item)

        roots = [eq for eq, row in by_equation.items() if not str(row.get("parent_equation", "") or "").strip() or str(row.get("parent_equation", "") or "").strip() not in by_equation]
        for eq in sorted(roots):
            add_branch(eq, root)
        orphaned = [eq for eq in by_equation if eq not in appended]
        if orphaned:
            orphan_root = self.eq_tree.AppendItem(root, "Detached / unresolved nodes")
            self._apply_tree_item_colors(self.eq_tree, orphan_root, {"bucket": "pending"}, kind="bucket")
            for eq in sorted(orphaned):
                add_branch(eq, orphan_root)
        self.eq_tree.Expand(root)
        first, cookie = self.eq_tree.GetFirstChild(root)
        if first.IsOk():
            self.eq_tree.SelectItem(first)

    def on_equation_tree_selected(self, event) -> None:
        item = event.GetItem() if event is not None else self.eq_tree.GetSelection()
        if not item or not item.IsOk():
            return
        eq = str(self.eq_tree.GetItemData(item) or "").strip()
        if not eq:
            return
        for i, row in enumerate(self.current_equation_rows):
            if str(row.get("equation", "") or "").strip() == eq:
                self.eq_list.SetSelection(i)
                self.on_equation_selected(None)
                return

    def _select_equation_tree_equation(self, equation: str) -> None:
        if not hasattr(self, "eq_tree"):
            return
        target = str(equation or "").strip()
        if not target:
            return
        root = self.eq_tree.GetRootItem()
        if not root or not root.IsOk():
            return

        def walk(item):
            data = str(self.eq_tree.GetItemData(item) or "").strip()
            if data == target:
                return item
            child, cookie = self.eq_tree.GetFirstChild(item)
            while child.IsOk():
                found = walk(child)
                if found and found.IsOk():
                    return found
                child, cookie = self.eq_tree.GetNextChild(item, cookie)
            return None

        found = walk(root)
        if found and found.IsOk():
            self.eq_tree.SelectItem(found)
            parent = self.eq_tree.GetItemParent(found)
            while parent and parent.IsOk():
                self.eq_tree.Expand(parent)
                parent = self.eq_tree.GetItemParent(parent)

    def _populate_variable_tree(self, rows: list[dict[str, Any]]) -> None:
        if not hasattr(self, "var_tree"):
            return
        self.var_tree.DeleteAllItems()
        root = self.var_tree.AddRoot("Variables")
        self._apply_tree_item_colors(self.var_tree, root, kind="root")
        grouped = {"approved": [], "candidate": [], "linked": [], "unused": []}
        for row in rows:
            grouped["approved" if bool(row.get("approved", False)) else "candidate"].append(row)
            if bool(row.get("linked", False)):
                grouped["linked"].append(row)
            if int(row.get("usage_count", 0) or 0) <= 0:
                grouped["unused"].append(row)
        first_item = None
        for bucket in ["approved", "candidate", "linked", "unused"]:
            bucket_rows = grouped.get(bucket, [])
            bucket_item = self.var_tree.AppendItem(root, bucket.capitalize())
            self.var_tree.SetItemData(bucket_item, ("bucket", bucket))
            self._apply_tree_item_colors(self.var_tree, bucket_item, {"bucket": bucket}, kind="bucket")
            seen = set()
            for row in sorted(bucket_rows, key=lambda r: str(r.get("symbol", "")).lower()):
                symbol = str(row.get("symbol", "") or "").strip()
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                extras = []
                status = str(row.get("status", "") or "").strip()
                if status:
                    extras.append(status)
                extras.append(f"score={int(row.get('score', 0) or 0)}")
                extras.append(f"usage={int(row.get('usage_count', 0) or 0)}")
                item = self.var_tree.AppendItem(bucket_item, f"{symbol} | {' | '.join(extras)}")
                self.var_tree.SetItemData(item, ("variable", symbol))
                row["_bucket"] = bucket
                self._apply_tree_item_colors(self.var_tree, item, row, kind="variable")
                if first_item is None:
                    first_item = item
            self.var_tree.Expand(bucket_item)
        self.var_tree.Expand(root)
        if first_item is not None:
            self.var_tree.SelectItem(first_item)

    def on_variable_tree_selected(self, event) -> None:
        item = event.GetItem() if event is not None else self.var_tree.GetSelection()
        if not item or not item.IsOk():
            return
        data = self.var_tree.GetItemData(item)
        if not isinstance(data, tuple) or data[0] != "variable":
            return
        symbol = str(data[1] or "").strip()
        if not symbol:
            return
        for i, row in enumerate(self.current_variable_rows):
            if str(row.get("symbol", "") or "").strip() == symbol:
                self.var_list.SetSelection(i)
                self.on_variable_selected(None)
                return

    def _select_variable_tree_symbol(self, symbol: str) -> None:
        if not hasattr(self, "var_tree"):
            return
        target = str(symbol or "").strip()
        if not target:
            return
        root = self.var_tree.GetRootItem()
        if not root or not root.IsOk():
            return

        def walk(item):
            data = self.var_tree.GetItemData(item)
            if isinstance(data, tuple) and data[0] == "variable" and str(data[1] or "").strip() == target:
                return item
            child, cookie = self.var_tree.GetFirstChild(item)
            while child.IsOk():
                found = walk(child)
                if found and found.IsOk():
                    return found
                child, cookie = self.var_tree.GetNextChild(item, cookie)
            return None

        found = walk(root)
        if found and found.IsOk():
            self.var_tree.SelectItem(found)
            parent = self.var_tree.GetItemParent(found)
            while parent and parent.IsOk():
                self.var_tree.Expand(parent)
                parent = self.var_tree.GetItemParent(parent)

    def build_eqtest_tab(self) -> None:
        panel = self.eqtest_tab
        v = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(v)
        top = wx.WrapSizer(wx.HORIZONTAL)
        self.test_session = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.test_session.Bind(wx.EVT_COMBOBOX, lambda evt: self.refresh_eqtest_view())
        self.test_use_active = wx.Button(panel, label=self.tr("use_active"))
        self.test_use_active.Bind(wx.EVT_BUTTON, self.on_use_active_eqtest)
        self.test_use_selected_equation = wx.Button(panel, label=self.tr("use_selected_equation"))
        self.test_use_selected_equation.Bind(wx.EVT_BUTTON, self.on_use_selected_equation_for_test)
        self.test_equation_choice = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.test_equation_choice.Bind(wx.EVT_COMBOBOX, self.on_test_equation_selected)
        self.test_filter_label = wx.StaticText(panel, label="Test Filter")
        self.test_filter_choice = wx.Choice(panel, choices=["All", "Ready for test", "Needs repair", "Review manually", "Ready for mutation"])
        self.test_filter_choice.SetSelection(0)
        self.test_filter_choice.Bind(wx.EVT_CHOICE, lambda evt: self.refresh_eqtest_view())
        self.test_refresh = wx.Button(panel, label=self.tr("refresh_view"))
        self.test_refresh.Bind(wx.EVT_BUTTON, lambda evt: self.refresh_eqtest_view())
        self.test_run = wx.Button(panel, label=self.tr("run_test"))
        self.test_run.Bind(wx.EVT_BUTTON, self.on_run_test)
        for c in [self.test_session, self.test_use_active, self.test_use_selected_equation, self.test_equation_choice, self.test_filter_label, self.test_filter_choice, self.test_refresh, self.test_run]:
            top.Add(c, 0, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 6)
        v.Add(top, 0, wx.EXPAND)

        state_box = wx.StaticBoxSizer(wx.VERTICAL, panel, "Equation Test Status")
        self.test_state_label = wx.StaticText(panel, label="Equation Test State: IDLE")
        self.test_state_label.Wrap(780)
        self.test_verdict_label = wx.StaticText(panel, label="Current Verdict: -")
        self.test_verdict_label.Wrap(780)
        self.test_selected_label = wx.StaticText(panel, label="Selected Equation: -")
        self.test_selected_label.Wrap(780)
        state_box.Add(self.test_state_label, 0, wx.EXPAND | wx.ALL, 4)
        state_box.Add(self.test_verdict_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        state_box.Add(self.test_selected_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        v.Add(state_box, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        self.test_splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE)
        left = wx.Panel(self.test_splitter)
        right = wx.Panel(self.test_splitter)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        right_sizer = wx.BoxSizer(wx.VERTICAL)
        left.SetSizer(left_sizer)
        right.SetSizer(right_sizer)
        self.test_tree_label = wx.StaticText(left, label="Equation Tree")
        left_sizer.Add(self.test_tree_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.test_tree = wx.TreeCtrl(left, style=wx.TR_HAS_BUTTONS | wx.TR_LINES_AT_ROOT | wx.TR_DEFAULT_STYLE)
        self.test_tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_test_tree_selected)
        left_sizer.Add(self.test_tree, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 4)
        self.test_list_label = wx.StaticText(left, label="Equation List")
        left_sizer.Add(self.test_list_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.test_list = wx.ListBox(left)
        self.test_list.Bind(wx.EVT_LISTBOX, self.on_test_equation_selected)
        left_sizer.Add(self.test_list, 1, wx.EXPAND | wx.ALL, 4)
        self.test_state_label_right = wx.StaticText(right, label="Equation Test State: IDLE")
        self.test_state_label_right.Wrap(420)
        right_sizer.Add(self.test_state_label_right, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 4)
        self.test_report = wx.TextCtrl(right, style=wx.TE_MULTILINE | wx.TE_READONLY)
        right_sizer.Add(self.test_report, 1, wx.EXPAND | wx.ALL, 4)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.test_copy_report_btn = wx.Button(right, label=self.tr("copy_report"))
        self.test_copy_report_btn.Bind(wx.EVT_BUTTON, self.on_copy_test_report)
        self.test_prepare_repair_btn = wx.Button(right, label=self.tr("prepare_repair"))
        self.test_prepare_repair_btn.Bind(wx.EVT_BUTTON, self.on_test_prepare_repair)
        self.test_prepare_mutation_btn = wx.Button(right, label=self.tr("prepare_mutation"))
        self.test_prepare_mutation_btn.Bind(wx.EVT_BUTTON, self.on_test_prepare_mutation)
        for b in [self.test_copy_report_btn, self.test_prepare_repair_btn, self.test_prepare_mutation_btn]:
            btns.Add(b, 0, wx.ALL, 4)
        right_sizer.Add(btns, 0, wx.ALL, 4)
        self.test_splitter.SplitVertically(left, right, 380)
        v.Add(self.test_splitter, 1, wx.EXPAND | wx.ALL, 8)


    def on_run_test(self, event) -> None:
        session = self.test_session.GetValue().strip()
        equation = normalize_target_equation(self.test_equation_choice.GetValue().strip() or self.current_test_equation())
        if self.test_equation_choice.GetSelection() != wx.NOT_FOUND:
            idx = self.test_equation_choice.GetSelection()
            if 0 <= idx < len(self.current_test_rows):
                self.test_list.SetSelection(idx)
                equation = normalize_target_equation(str(self.current_test_rows[idx].get("equation", "") or "")) or equation
        if not session:
            self.log("[INFO] Equation Test blocked: no session selected.")
            return
        if not equation:
            self.log("[INFO] Equation Test blocked: no equation selected.")
            return
        self.set_eqtest_state("running")
        self.test_report.SetValue(f"[RUNNING] Multi-agent debate on:\n{equation}\n")
        logs_index = max(0, self.notebook.GetPageCount() - 1)
        self.notebook.SetSelection(logs_index)
        cmd = [sys.executable, "-u", str(LAUNCHER_FILE), "equation-test", "--session", session, "--target-equation", equation]
        threading.Thread(
            target=self.run_process_thread,
            args=(cmd,),
            kwargs={"on_success": lambda: self.load_equation_test_report(session=session, equation=equation, focus_eqtest=True)},
            daemon=True,
        ).start()

    def build_logs_tab(self) -> None:
        panel = self.logs_tab
        v = wx.BoxSizer(wx.VERTICAL)
        panel.SetSizer(v)
        top = wx.BoxSizer(wx.HORIZONTAL)
        self.clear_logs_button = wx.Button(panel, label="Clear Logs")
        self.clear_logs_button.Bind(wx.EVT_BUTTON, lambda evt: self.log_output.SetValue(""))
        top.Add(self.clear_logs_button, 0, wx.ALL, 8)
        v.Add(top, 0, wx.EXPAND)
        self.log_output = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY)
        v.Add(self.log_output, 1, wx.EXPAND | wx.ALL, 8)

    def _selected_session_for_badge(self) -> str:
        for control in [self.test_session, self.eq_session, self.science_session, self.var_session, self.session_choice]:
            try:
                value = str(control.GetValue() or "").strip()
            except Exception:
                value = ""
            if value:
                return value
        return str(self.active_session_name or "").strip()

    def _selected_target_for_badge(self) -> str:
        for getter in [self.current_test_equation, self.current_equation]:
            try:
                value = normalize_target_equation(getter())
            except Exception:
                value = ""
            if value:
                return value
        try:
            return normalize_target_equation(self.prepared_target_equation)
        except Exception:
            return ""

    def _engine_badge_value(self, session_name: str) -> str:
        session_name = str(session_name or "").strip()
        if not session_name:
            return "-"
        try:
            shared = SharedResearchMemory(local_session_dir(session_name) / "shared_research_memory.json")
            decision = shared.choose_next_repair_action()
            kind = str(decision.get("kind", "none") or "none")
            target = str(decision.get("target", "") or "").strip()
            reason = str(decision.get("reason", "") or "").strip()
            if target:
                return f"{kind}:{target[:18]}"
            if reason:
                return f"{kind}:{reason[:18]}"
            return kind
        except Exception:
            return "-"

    def refresh_header_badges(self) -> None:
        session_name = self._selected_session_for_badge()
        target = self._selected_target_for_badge()
        engine = self._engine_badge_value(session_name)
        wx.CallAfter(self.session_badge.SetLabel, f"Session: {session_name or '-'}")
        wx.CallAfter(self.target_badge.SetLabel, f"Target: {(target[:48] + '…') if len(target) > 48 else (target or '-')}" )
        wx.CallAfter(self.engine_badge.SetLabel, f"Engine: {engine}")
        summary = f"Session: {session_name or '-'}\nTarget: {target or '-'}\nEngine: {engine}"
        if hasattr(self, "current_selection_text"):
            wx.CallAfter(self.current_selection_text.SetLabel, summary)

    def set_eqtest_state(self, state: str) -> None:
        self.current_report_state = str(state or "idle").lower()
        if hasattr(self, "test_state_label"):
            wx.CallAfter(self.test_state_label.SetLabel, f"Equation Test State: {self.current_report_state.upper()}")
        wx.CallAfter(self.apply_theme)
        if hasattr(self, "test_state_label_right"):
            wx.CallAfter(self.test_state_label_right.SetLabel, f"Equation Test State: {self.current_report_state.upper()}")

    def set_status(self, value: str) -> None:
        wx.CallAfter(self.status_label.SetLabel, f"Status: {value}" if self.lang == 'en' else f"Statut : {value}")
        upper = str(value or "").upper()
        if "RUN" in upper:
            run_state = "RUNNING"
        elif "DONE" in upper:
            run_state = "DONE"
        elif "STOP" in upper:
            run_state = "STOPPED"
        elif "ERROR" in upper:
            run_state = "ERROR"
        else:
            run_state = "IDLE"
        wx.CallAfter(self.run_state_badge.ChangeValue, f"RUN STATE: {run_state}")
        self.refresh_header_badges()
        wx.CallAfter(self.apply_theme)

    def log(self, text: str) -> None:
        wx.CallAfter(self.log_output.AppendText, text + "\n")

    def on_language_changed(self, event) -> None:
        self.lang = "fr" if self.language_choice.GetSelection() == 1 else "en"
        self.apply_translations()
        self.update_cmd_preview()

    def apply_translations(self) -> None:
        self.SetTitle(self.tr("app_title"))
        self.status_label.SetLabel(self.tr("status_ready"))
        self.language_label.SetLabel(self.tr("language"))
        self.notebook.SetPageText(0, self.tr("tab_controls"))
        self.notebook.SetPageText(1, self.tr("tab_science"))
        self.notebook.SetPageText(2, self.tr("tab_equations"))
        self.notebook.SetPageText(3, self.tr("tab_variables"))
        self.notebook.SetPageText(4, self.tr("tab_eqtest"))
        self.notebook.SetPageText(5, self.tr("tab_logs"))
        self.mode_box.SetLabel(self.tr("mode"))
        self.open_science_button.SetLabel(self.tr("open_science"))
        self.session_box.SetLabel(self.tr("working_session"))
        self.session_strategy_label.SetLabel(self.tr("session_strategy"))
        self.session_label.SetLabel(self.tr("session"))
        self.session_strategy.SetItems([self.tr("session_selected"), self.tr("session_latest"), self.tr("session_new")])
        self.refresh_button.SetLabel(self.tr("refresh_sessions"))
        self.params_box.SetLabel(self.tr("params"))
        self.cycles_label.SetLabel(self.tr("cycles"))
        self.variable_turns_label.SetLabel(self.tr("variable_turns"))
        self.equation_turns_label.SetLabel(self.tr("equation_turns"))
        self.mutation_turns_label.SetLabel(self.tr("mutation_turns"))
        self.lineage_limit_label.SetLabel(self.tr("lineage_limit"))
        self.steps_label.SetLabel(self.tr("steps"))
        if hasattr(self, "prepare_steps_label"): self.prepare_steps_label.SetLabel(self.tr("prepare_steps"))
        if hasattr(self, "profile_box"): self.profile_box.SetLabel(self.tr("loop_profile"))
        self.merge_box.SetLabel(self.tr("merge_sessions"))
        self.session_a_label.SetLabel(self.tr("session_a"))
        self.session_b_label.SetLabel(self.tr("session_b"))
        self.preview_box.SetLabel(self.tr("preview"))
        self.test_button.SetLabel(self.tr("test_launcher"))
        self.run_button.SetLabel(self.tr("run"))
        self.stop_button.SetLabel(self.tr("stop"))
        self.reset_button.SetLabel(self.tr("reset"))
        self.science_use_active.SetLabel(self.tr("use_active"))
        self.science_refresh.SetLabel(self.tr("refresh_view"))
        self.eq_use_active.SetLabel(self.tr("use_active"))
        self.eq_filter_label.SetLabel(self.tr("equation_filter"))
        self.eq_filter.SetItems([self.tr("all"), self.tr("approved"), self.tr("partial"), self.tr("needs_repair"), self.tr("mutable")])
        self.eq_refresh.SetLabel(self.tr("refresh_view"))
        self.eq_copy_btn.SetLabel(self.tr("copy_equation"))
        self.var_use_active.SetLabel(self.tr("use_active"))
        self.var_filter_label.SetLabel(self.tr("variable_filter"))
        self.var_filter.SetItems([self.tr("all"), self.tr("approved"), self.tr("candidate"), self.tr("linked"), self.tr("unused")])
        self.var_refresh.SetLabel(self.tr("refresh_view"))
        self.var_copy_btn.SetLabel(self.tr("copy_variable"))
        self.var_like_btn.SetLabel(self.tr("like"))
        self.var_dislike_btn.SetLabel(self.tr("dislike"))
        self.var_prepare_repair_btn.SetLabel(self.tr("variable_prepare_repair"))
        self.var_prepare_mutation_btn.SetLabel(self.tr("variable_prepare_mutation"))
        self.eq_like_btn.SetLabel(self.tr("like"))
        self.eq_dislike_btn.SetLabel(self.tr("dislike"))
        self.eq_prepare_repair_btn.SetLabel(self.tr("prepare_repair"))
        self.eq_prepare_mutation_btn.SetLabel(self.tr("prepare_mutation"))
        self.test_use_active.SetLabel(self.tr("use_active"))
        self.test_use_selected_equation.SetLabel(self.tr("use_selected_equation"))
        self.test_refresh.SetLabel(self.tr("refresh_view"))
        self.test_run.SetLabel(self.tr("run_test"))
        self.test_copy_report_btn.SetLabel(self.tr("copy_report"))
        self.test_prepare_repair_btn.SetLabel(self.tr("prepare_repair"))
        self.test_prepare_mutation_btn.SetLabel(self.tr("prepare_mutation"))
        if hasattr(self, "clear_logs_button"):
            self.clear_logs_button.SetLabel("Clear Logs" if self.lang == "en" else "Vider les logs")
        if hasattr(self, "current_selection_box"):
            self.current_selection_box.SetLabel("Current Selection" if self.lang == "en" else "Sélection courante")
        self.controls_tab.Layout(); self.science_tab.Layout(); self.equations_tab.Layout(); self.variables_tab.Layout(); self.eqtest_tab.Layout(); self.logs_tab.Layout(); self.Layout()
        self.refresh_header_badges()

    def read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def refresh_sessions(self) -> None:
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        self.sessions = sorted([p.name for p in WORKSPACE_DIR.glob("alchemy_session_*") if p.is_dir()])
        for combo in [self.session_choice, self.session_a_choice, self.session_b_choice, self.science_session, self.eq_session, self.var_session, self.test_session]:
            current = combo.GetValue() if hasattr(combo, "GetValue") else ""
            combo.SetItems(self.sessions)
            if current in self.sessions:
                combo.SetValue(current)
            elif self.sessions:
                combo.SetValue(self.sessions[-1])
        
        # Update multi-merge checklist
        if hasattr(self, 'multi_merge_checklist'):
            self.multi_merge_checklist.Clear()
            for session in self.sessions:
                self.multi_merge_checklist.Append(session)

        self.log("[INFO] Session list refreshed.")
        self.refresh_scientific_view()
        self.refresh_equations_view()
        self.refresh_variables_view()
        self.refresh_eqtest_view()
        self.update_cmd_preview()

    def selected_mode(self) -> str:
        return self.mode_choice.GetStringSelection() or MODES[0]

    def selected_session_strategy(self) -> str:
        idx = self.session_strategy.GetSelection()
        return ["selected", "latest", "new"][idx if idx >= 0 and idx < 3 else 0]

    def selected_loop_profile(self) -> str:
        if not hasattr(self, "loop_profile_choice"):
            return "custom"
        return str(self.loop_profile_choice.GetStringSelection() or "Custom").strip().lower()

    def apply_loop_profile(self, profile: str) -> None:
        profile = str(profile or "custom").strip().lower()
        presets = {
            "mini": {"cycles": "1", "variable_turns": "1", "equation_turns": "1", "mutation_turns": "1", "steps": "3", "prepare_steps": "2"},
            "normal": {"cycles": "3", "variable_turns": "1", "equation_turns": "1", "mutation_turns": "1", "steps": "8", "prepare_steps": "4"},
            "deep": {"cycles": "5", "variable_turns": "2", "equation_turns": "2", "mutation_turns": "2", "steps": "15", "prepare_steps": "6"},
        }
        values = presets.get(profile)
        if not values:
            return
        self.cycles_input.SetValue(values["cycles"])
        self.variable_turns_input.SetValue(values["variable_turns"])
        self.equation_turns_input.SetValue(values["equation_turns"])
        self.mutation_turns_input.SetValue(values["mutation_turns"])
        self.steps_input.SetValue(values["steps"])
        self.prepare_steps_input.SetValue(values["prepare_steps"])

    def on_loop_profile_changed(self, event=None) -> None:
        self.apply_loop_profile(self.selected_loop_profile())
        self.update_mode_view()
        self.update_cmd_preview()
        if event is not None:
            event.Skip()

    def on_any_input_changed(self, event=None) -> None:
        self.update_mode_view()
        self.update_cmd_preview()
        if event is not None:
            event.Skip()

    def on_mode_changed(self, event=None) -> None:
        self.prepared_target_equation = "" if self.selected_mode() not in {"repair", "mutation", "prepare-mutation"} else self.prepared_target_equation
        self.on_any_input_changed(event)

    def update_mode_view(self) -> None:
        mode = self.selected_mode()
        is_merge = mode == "merge-sessions"
        self.merge_box.Show(is_merge)
        self.session_box.Show(not is_merge)
        show_cycles = mode in {"loop", "full-evolve"}
        show_var = mode in {"loop", "full", "both", "variables", "full-evolve"}
        show_eq = mode in {"loop", "full", "both", "equations", "full-evolve"}
        show_mut = mode in {"loop", "full", "full-evolve"}
        show_lineage = mode == "lineages"
        show_steps = mode in {"auto-repair", "auto-mutation", "prepare-mutation", "auto-prepare-mutation", "auto-consolidate-mutation"}
        show_prepare_steps = mode in {"full-evolve"}
        show_profile = mode in {"loop", "full", "both", "full-evolve", "auto-repair", "auto-mutation", "prepare-mutation", "auto-prepare-mutation", "auto-consolidate-mutation"}
        self.profile_box.Show(show_profile)
        mapping = [
            (self.cycles_label, self.cycles_input, show_cycles),
            (self.variable_turns_label, self.variable_turns_input, show_var),
            (self.equation_turns_label, self.equation_turns_input, show_eq),
            (self.mutation_turns_label, self.mutation_turns_input, show_mut),
            (self.lineage_limit_label, self.lineage_limit_input, show_lineage),
            (self.steps_label, self.steps_input, show_steps),
            (self.prepare_steps_label, self.prepare_steps_input, show_prepare_steps),
        ]
        for label, ctrl, show in mapping:
            label.Show(show); ctrl.Show(show)
        self.controls_tab.Layout(); self.controls_tab.SetupScrolling(scroll_x=False, scroll_y=True); self.Layout()

    def build_command(self) -> list[str]:
        cmd = [sys.executable, "-u", str(LAUNCHER_FILE)]
        mode = self.selected_mode()
        cmd.append(mode)
        if mode == "merge-sessions":
            a = self.session_a_choice.GetValue().strip()
            b = self.session_b_choice.GetValue().strip()
            if not a or not b:
                raise ValueError("Merge requires Session A and Session B.")
            if a == b:
                raise ValueError("Session A and Session B must be different.")
            cmd += ["--session-a", a, "--session-b", b, "--create-new-session"]
            return cmd

        # positional params
        if mode == "loop":
            cmd += [self.cycles_input.GetValue().strip() or "1", self.variable_turns_input.GetValue().strip() or "1", self.equation_turns_input.GetValue().strip() or "1", self.mutation_turns_input.GetValue().strip() or "1"]
        elif mode == "full":
            cmd += [self.variable_turns_input.GetValue().strip() or "1", self.equation_turns_input.GetValue().strip() or "1", self.mutation_turns_input.GetValue().strip() or "1"]
        elif mode == "both":
            cmd += [self.variable_turns_input.GetValue().strip() or "1", self.equation_turns_input.GetValue().strip() or "1"]
        elif mode in {"variables", "equations", "mutation", "repair"}:
            cmd += [self.equation_turns_input.GetValue().strip() or "1"] if mode in {"equations", "mutation", "repair"} else [self.variable_turns_input.GetValue().strip() or "1"]
        elif mode in {"auto-repair", "auto-mutation", "prepare-mutation", "auto-prepare-mutation", "auto-consolidate-mutation"}:
            cmd += [self.steps_input.GetValue().strip() or "3"]
        elif mode == "full-evolve":
            cmd += [
                self.cycles_input.GetValue().strip() or "1",
                self.variable_turns_input.GetValue().strip() or "1",
                self.equation_turns_input.GetValue().strip() or "1",
                self.prepare_steps_input.GetValue().strip() or "3",
                self.mutation_turns_input.GetValue().strip() or "1",
            ]
        elif mode == "lineages":
            cmd += [self.lineage_limit_input.GetValue().strip() or "5"]

        strat = self.selected_session_strategy()
        if strat == "selected":
            name = self.session_choice.GetValue().strip()
            if not name:
                raise ValueError("Select a session.")
            cmd += ["--session", name]
        elif strat == "latest":
            cmd.append("--resume")
        else:
            cmd.append("--create-new-session")

        if mode in {"repair", "mutation", "prepare-mutation"} and self.prepared_target_equation.strip():
            cmd += ["--target-equation", self.prepared_target_equation.strip()]
        return cmd

    def update_cmd_preview(self) -> None:
        try:
            cmd = self.build_command()
            self.cmd_preview.SetValue(" ".join(cmd))
        except Exception as exc:
            self.cmd_preview.SetValue(f"[{type(exc).__name__}] {exc}")

    def on_test_launcher(self, event) -> None:
        cmd = [sys.executable, "-u", str(LAUNCHER_FILE), "--help"]
        self.log(f"[TEST CMD] {' '.join(cmd)}")
        try:
            proc = subprocess.run(cmd, cwd=str(WORKSPACE_DIR), capture_output=True, text=True)
            if proc.stdout:
                self.log("[TEST STDOUT]")
                self.log(proc.stdout.rstrip())
            if proc.stderr:
                self.log("[TEST STDERR]")
                self.log(proc.stderr.rstrip())
            self.log(f"[TEST FIN] Code retour = {proc.returncode}")
        except SecurityStop as exc:
            self.log(f"[SECURITY STOP] {exc}")
        except Exception as exc:
            self.log(f"[ERREUR] {type(exc).__name__}: {exc}")

    def set_running_ui(self, running: bool) -> None:
        wx.CallAfter(self.run_button.Enable, not running)
        wx.CallAfter(self.test_button.Enable, not running)
        wx.CallAfter(self.reset_button.Enable, not running)
        wx.CallAfter(self.stop_button.Enable, running)

    def run_process_thread(self, cmd: list[str], on_success=None) -> None:
        try:
            self.stop_requested = False
            self.set_running_ui(True)
            self.set_status(self.tr("running"))
            self.log("[UI] Run requested.")
            self.log(f"[CMD] {' '.join(cmd)}")
            self.log(f"[CWD] {WORKSPACE_DIR}")
            self.process = subprocess.Popen(cmd, cwd=str(WORKSPACE_DIR), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.log(line.rstrip())
            code = self.process.wait()
            self.log(f"[FIN] Code retour = {code}")
            if self.stop_requested:
                self.set_status(self.tr("stopped"))
                self.set_eqtest_state("stopped")
            elif code == 0:
                self.set_status(self.tr("done"))
                if callable(on_success):
                    try:
                        wx.CallAfter(on_success)
                    except Exception as exc:
                        self.log(f"[ERREUR] post-run callback failed: {type(exc).__name__}: {exc}")
                        self.set_eqtest_state("error")
            else:
                self.log("[INFO] Command failed; post-run callback skipped.")
                self.set_status(f"{self.tr('error')} ({code})")
                self.set_eqtest_state("error")
        except SecurityStop as exc:
            self.log(f"[SECURITY STOP] {exc}")
            self.set_eqtest_state("error")
        except Exception as exc:
            self.log(f"[ERREUR] {type(exc).__name__}: {exc}")
            self.set_status(self.tr("error"))
            self.set_eqtest_state("error")
        finally:
            self.process = None
            self.set_running_ui(False)
            wx.CallAfter(self.refresh_sessions)
            wx.CallAfter(self.notebook.SetSelection, self.notebook.GetPageCount() - 1)

    def on_run(self, event) -> None:
        try:
            cmd = self.build_command()
        except Exception as exc:
            wx.MessageBox(str(exc), "Launcher", wx.OK | wx.ICON_ERROR)
            self.update_cmd_preview()
            return
        self.notebook.SetSelection(self.notebook.GetPageCount() - 1)
        threading.Thread(target=self.run_process_thread, args=(cmd,), daemon=True).start()

    def on_stop(self, event) -> None:
        if self.process and self.process.poll() is None:
            self.stop_requested = True
            try:
                self.process.terminate()
                log_effect('termination_requested', target='active_process', risk='medium')
                self.log("[STOP] Termination requested.")
            except Exception as exc:
                self.log(f"[ERREUR] {type(exc).__name__}: {exc}")

    def on_reset(self, event) -> None:
        if wx.MessageBox("Reset all sessions and memory?", "Confirm", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        cmd = [sys.executable, "-u", str(LAUNCHER_FILE), "reset"]
        threading.Thread(target=self.run_process_thread, args=(cmd,), daemon=True).start()

    def on_select_all_sessions(self, event) -> None:
        for i in range(self.multi_merge_checklist.GetCount()):
            self.multi_merge_checklist.Check(i, True)

    def on_deselect_all_sessions(self, event) -> None:
        for i in range(self.multi_merge_checklist.GetCount()):
            self.multi_merge_checklist.Check(i, False)

    def on_perform_multi_merge(self, event) -> None:
        selected_sessions = [
            self.multi_merge_checklist.GetString(i)
            for i in range(self.multi_merge_checklist.GetCount())
            if self.multi_merge_checklist.IsChecked(i)
        ]
        if len(selected_sessions) < 2:
            wx.MessageBox("Please select at least 2 sessions to merge.", "Multi-Merge", wx.OK | wx.ICON_WARNING)
            return
        if len(selected_sessions) > 10:
            wx.MessageBox("Multi-merge supports up to 10 sessions only.", "Multi-Merge", wx.OK | wx.ICON_WARNING)
            return

        self.notebook.SetSelection(self.notebook.GetPageCount() - 1)
        cmd = [sys.executable, "-u", str(LAUNCHER_FILE), "merge-multiple", "--sessions"] + selected_sessions
        threading.Thread(target=self.run_process_thread, args=(cmd,), daemon=True).start()

    def load_shared(self, session_name: str) -> dict[str, Any]:
        if not session_name:
            return {}
        return self.read_json(local_session_dir(session_name) / "shared_research_memory.json", {})

    def save_shared(self, session_name: str, payload: dict[str, Any]) -> None:
        if not session_name:
            return
        session_dir = local_session_dir(session_name)
        (session_dir / "shared_research_memory.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


    def _build_lineage_graph(self, shared: dict[str, Any], limit: int = 12) -> str:
        rows = list(shared.get("approved_equations", []) or []) + list(shared.get("partial_equations", []) or [])
        nodes = {}
        children = {}
        for row in rows:
            eq = str(row.get("equation", "") or "").strip()
            if not eq:
                continue
            parent = str(row.get("parent_equation", "") or "").strip()
            nodes[eq] = row
            children.setdefault(eq, [])
            if parent:
                children.setdefault(parent, []).append(eq)
        if not nodes:
            return "No lineage graph available."
        roots = [eq for eq in nodes if not str(nodes[eq].get("parent_equation", "") or "").strip()]
        roots = sorted(roots, key=lambda eq: (not bool(nodes[eq].get("approved", False)), -int(nodes[eq].get("source_turn", 0) or 0), eq))
        lines = []
        seen = set()
        budget = [limit]
        def walk(eq: str, prefix: str = ""):
            if budget[0] <= 0 or eq in seen:
                return
            seen.add(eq)
            budget[0] -= 1
            row = nodes.get(eq, {})
            badge = "[A]" if bool(row.get("approved", False)) else "[P]"
            lines.append(f"{prefix}{badge} {eq[:120]}")
            kids = sorted(children.get(eq, []), key=lambda child: (not bool(nodes.get(child, {}).get("approved", False)), -int(nodes.get(child, {}).get("source_turn", 0) or 0), child))
            for child in kids:
                walk(child, prefix + "  -> ")
        for root in roots:
            walk(root)
            if budget[0] <= 0:
                break
        extra = max(0, len(nodes) - len(seen))
        if extra:
            lines.append(f"... {extra} more node(s)")
        return "\n".join(lines)


    def _populate_science_variable_tree(self, shared: dict[str, Any]) -> None:
        self.lineage_tree.DeleteAllItems()
        self.science_lineage_lookup = {}
        root = self.lineage_tree.AddRoot("Variables")
        self._apply_tree_item_colors(self.lineage_tree, root, kind="root")
        buckets = [
            ("Approved", dict(shared.get("approved_variables", {}) or {})),
            ("Candidate", dict(shared.get("candidate_variables", {}) or {})),
            ("Pending", dict(shared.get("pending_variables", {}) or {})),
        ]
        first_item = None
        for label, store in buckets:
            bucket_item = self.lineage_tree.AppendItem(root, label)
            self.lineage_tree.SetItemData(bucket_item, ("bucket", label.lower()))
            for symbol, payload in sorted(store.items(), key=lambda kv: str(kv[0]).lower()):
                row = dict(payload or {})
                row.setdefault("name", str(symbol))
                quality = row.get("quality_score", "")
                status = str(row.get("status", "") or label.lower())
                item_label = str(symbol)
                extras = []
                if status:
                    extras.append(status)
                if str(quality).strip():
                    extras.append(f"Q={quality}")
                if extras:
                    item_label += " | " + " | ".join(extras)
                item = self.lineage_tree.AppendItem(bucket_item, item_label)
                self.lineage_tree.SetItemData(item, ("variable", str(symbol)))
                self.science_lineage_lookup[f"var::{symbol}"] = row
                if first_item is None:
                    first_item = item
            self.lineage_tree.Expand(bucket_item)
        self.lineage_tree.Expand(root)
        if first_item is not None:
            self.lineage_tree.SelectItem(first_item)

    def _populate_science_lineage_tree(self, shared: dict[str, Any]) -> None:
        self.lineage_tree.DeleteAllItems()
        self.science_lineage_lookup = {}
        self.lineage_detail.SetValue("")
        rows = list(shared.get("approved_equations", []) or []) + list(shared.get("partial_equations", []) or [])
        root = self.lineage_tree.AddRoot("Session lineage")
        nodes: dict[str, dict[str, Any]] = {}
        children: dict[str, list[str]] = {}
        for row in rows:
            eq = str(row.get("equation", "") or "").strip()
            if not eq:
                continue
            parent = str(row.get("parent_equation", "") or "").strip()
            nodes[eq] = row
            children.setdefault(eq, [])
            if parent:
                children.setdefault(parent, []).append(eq)
        if not nodes:
            child = self.lineage_tree.AppendItem(root, "No lineage graph available")
            self.lineage_tree.SetItemData(child, "")
            self.lineage_tree.Expand(root)
            return
        roots = [eq for eq in nodes if not str(nodes[eq].get("parent_equation", "") or "").strip()]
        roots = sorted(roots, key=lambda eq: (not bool(nodes[eq].get("approved", False)), -int(nodes[eq].get("source_turn", 0) or 0), eq))
        appended = set()

        def label_for(eq: str) -> str:
            row = nodes.get(eq, {})
            badge = "A" if bool(row.get("approved", False)) else "P"
            turn = int(row.get("source_turn", 0) or 0)
            status = str(row.get("status", "") or "-")
            return f"[{badge}] T{turn:03d} | {eq[:96]} ({status})"

        def add_branch(parent_item, eq: str):
            if eq in appended:
                return
            appended.add(eq)
            item = self.lineage_tree.AppendItem(parent_item, label_for(eq))
            self.science_lineage_lookup[eq] = nodes.get(eq, {})
            self.lineage_tree.SetItemData(item, eq)
            kids = sorted(children.get(eq, []), key=lambda child: (not bool(nodes.get(child, {}).get("approved", False)), -int(nodes.get(child, {}).get("source_turn", 0) or 0), child))
            for child in kids:
                add_branch(item, child)

        for eq in roots:
            add_branch(root, eq)
        orphaned = [eq for eq in nodes if eq not in appended]
        if orphaned:
            orphan_root = self.lineage_tree.AppendItem(root, "Detached / unresolved nodes")
            self._apply_tree_item_colors(self.lineage_tree, orphan_root, {"bucket": "pending"}, kind="bucket")
            for eq in sorted(orphaned):
                add_branch(orphan_root, eq)
        self.lineage_tree.Expand(root)
        first, cookie = self.lineage_tree.GetFirstChild(root)
        if first.IsOk():
            self.lineage_tree.SelectItem(first)

    def on_science_tree_selected(self, event) -> None:
        try:
            item = event.GetItem() if event is not None else self.lineage_tree.GetSelection()
            if not item or not item.IsOk():
                self.lineage_detail.SetValue("")
                return
            data = self.lineage_tree.GetItemData(item)
            if isinstance(data, tuple):
                item_kind, raw_value = data
            else:
                item_kind, raw_value = "equation", data
            value = str(raw_value or "").strip()

            if item_kind == "variable":
                row = self.science_lineage_lookup.get(f"var::{value}", {}) or {}
                if not value:
                    self.lineage_detail.SetValue("Select a variable node to inspect its payload.")
                    return
                details = [
                    f"variable: {value}",
                    f"status: {row.get('status', '')}",
                    f"approved: {row.get('approved', False)}",
                    f"family: {row.get('family', '')}",
                    f"unit: {row.get('unit', '')}",
                    f"measure: {row.get('measure', '')}",
                    f"role: {row.get('role', '')}",
                    f"quality_score: {row.get('quality_score', '')}",
                    f"source_turn: {row.get('source_turn', 0)}",
                    f"validation_summary: {row.get('validation_summary', '')}",
                ]
                definition = str(row.get("definition", "") or "").strip()
                if definition:
                    details.append(f"definition: {definition}")
                notes = row.get("quality_notes", []) or []
                links = row.get("links", []) or []
                remarks = row.get("remarks", []) or []
                if notes:
                    details.append("quality_notes:\n- " + "\n- ".join(str(x) for x in notes))
                if links:
                    details.append("links:\n- " + "\n- ".join(str(x) for x in links))
                if remarks:
                    details.append("remarks:\n- " + "\n- ".join(str(x) for x in remarks))
                self.lineage_detail.SetValue("\n".join(details))
                self.refresh_header_badges()
                return

            eq = value
            row = self.science_lineage_lookup.get(eq, {}) or {}
            if not eq:
                self.lineage_detail.SetValue("Select a lineage node to inspect its scientific payload.")
                return
            details = [
                f"equation: {eq}",
                f"status: {row.get('status', '')}",
                f"approved: {row.get('approved', False)}",
                f"parent_equation: {row.get('parent_equation', '')}",
                f"stable_parent: {row.get('stable_parent', False)}",
                f"repair_required: {row.get('repair_required', False)}",
                f"fallback_used: {row.get('fallback_used', False)}",
                f"source_turn: {row.get('source_turn', 0)}",
                f"object_calculated: {row.get('object_calculated', '')}",
                f"architecture: {row.get('architecture', '')}",
                f"law_type: {row.get('law_type', '')}",
                f"mechanism: {row.get('mechanism', '')}",
                f"experiment: {row.get('experiment', '')}",
                f"validation_summary: {row.get('validation_summary', '')}",
            ]
            defs = row.get("definitions", {}) or row.get("defs", {}) or {}
            links = row.get("links", []) or []
            if defs:
                details.append("definitions:\n" + "\n".join(f"- {k}: {v}" for k, v in defs.items()))
            if links:
                details.append("links:\n" + "\n".join(f"- {x}" for x in links))
            self.lineage_detail.SetValue("\n".join(details))
            self.refresh_header_badges()
        except Exception as exc:
            self.lineage_detail.SetValue(f"[UI ERROR] lineage selection failed: {type(exc).__name__}: {exc}")

    def _scientific_selected_view(self) -> str:
        if not hasattr(self, "science_view_choice"):
            return "equations"
        value = str(self.science_view_choice.GetStringSelection() or "").strip().lower()
        if value.startswith("var"):
            return "variables"
        if value.startswith("équ") or value.startswith("equ"):
            return "equations"
        return "variables" if "variable" in value else "equations"

    def _build_scientific_variables_text(self, shared: dict[str, Any], name: str) -> str:
        approved = dict(shared.get("approved_variables", {}) or {})
        candidates = dict(shared.get("candidate_variables", {}) or {})
        pending = dict(shared.get("pending_variables", {}) or {})
        scores = dict(shared.get("variable_scores", {}) or {})
        usage = dict(shared.get("variable_usage_count", {}) or {})
        lines = [f"Session: {name}", "", "[Variables Overview]"]
        lines.append(f"Approved: {len(approved)}")
        lines.append(f"Candidate: {len(candidates)}")
        lines.append(f"Pending: {len(pending)}")
        lines.append("")
        lines.append("[Top Variables]")
        merged = []
        for bucket_name, store in [("approved", approved), ("candidate", candidates), ("pending", pending)]:
            for symbol, payload in store.items():
                row = dict(payload or {})
                row["symbol"] = str(symbol)
                row["bucket"] = bucket_name
                row["score"] = int(scores.get(symbol, 0) or 0)
                row["usage_count"] = int(usage.get(symbol, 0) or 0)
                merged.append(row)
        merged.sort(key=lambda r: (int(r.get("score", 0) or 0), int(r.get("usage_count", 0) or 0), str(r.get("symbol", ""))), reverse=True)
        for row in merged[:20]:
            lines.append(f"- {row.get('symbol', '')} | {row.get('bucket', '')} | score={row.get('score', 0)} | usage={row.get('usage_count', 0)}")
        return "\n".join(lines)

    def refresh_scientific_view(self) -> None:
        name = self.science_session.GetValue().strip()
        if hasattr(self, "lineage_detail"):
            self.lineage_detail.SetValue("")
        if not name:
            self.science_text.SetValue("")
            self._populate_science_lineage_tree({})
            self.refresh_header_badges()
            self.apply_theme()
            return
        shared = self.load_shared(name)
        if self._scientific_selected_view() == "variables":
            self.science_text.SetValue(self._build_scientific_variables_text(shared, name))
            self.science_graph_label.SetLabel("Variable Tree")
            self._populate_science_variable_tree(shared)
            self.refresh_header_badges()
            self.apply_theme()
            return
        approved_vars = len(shared.get("approved_variables", {}) or {})
        candidate_vars = len(shared.get("candidate_variables", {}) or {})
        pending_vars = len(shared.get("pending_variables", {}) or {})
        approved_eq = len(shared.get("approved_equations", []) or [])
        partial_eq = len(shared.get("partial_equations", []) or [])
        mutations = len(shared.get("mutation_history", []) or [])
        repairs = len(shared.get("repair_logs", []) or [])
        validations = len(shared.get("final_validations", []) or [])
        latest_var = (shared.get("last_validated_variable", {}) or {}).get("symbol", "-")
        latest_eq = "-"
        for bucket in [shared.get("approved_equations", []) or [], shared.get("partial_equations", []) or []]:
            if bucket:
                latest_eq = str(bucket[-1].get("equation", "-") or "-")
        rows = (shared.get("approved_equations", []) or []) + (shared.get("partial_equations", []) or [])
        nodes = approved_eq + partial_eq
        parent_nodes = sum(1 for row in rows if str(row.get("parent_equation", "") or "").strip())
        root_nodes = sum(1 for row in rows if not str(row.get("parent_equation", "") or "").strip())
        repairable_nodes = sum(1 for row in rows if bool(row.get("repair_required", False)) or str(row.get("status", "")).lower() == "partial")
        mutable_nodes = sum(1 for row in rows if bool(row.get("approved", False)) and bool(row.get("stable_parent", False)) and not bool(row.get("repair_required", False)) and not bool(row.get("fallback_used", False)))
        fallback_nodes = sum(1 for row in rows if bool(row.get("fallback_used", False)))
        stable_parent_nodes = sum(1 for row in rows if bool(row.get("stable_parent", False)))
        repeated_failures = sum(int((shared.get("equation_failures", {}) or {}).get(str(row.get("equation", "") or ""), 0) or 0) for row in rows)
        merge = shared.get("last_merge_summary", {}) or {}
        feedback = list(shared.get("reaction_feedback", []) or [])[-5:]
        try:
            decision = SharedResearchMemory(local_session_dir(name) / "shared_research_memory.json").choose_next_repair_action()
        except Exception:
            decision = {"kind": "none", "target": "", "reason": "unavailable"}
        approval_ratio = f"{approved_eq}/{max(1, approved_eq + partial_eq)}"
        text = []
        text.append(f"Session: {name}")
        text.append("")
        text.append("[Session Health]")
        text.append(f"Variables approved: {approved_vars}")
        text.append(f"Variables candidate: {candidate_vars}")
        text.append(f"Variables pending: {pending_vars}")
        text.append(f"Equations approved: {approved_eq}")
        text.append(f"Equations partial: {partial_eq}")
        text.append(f"Mutations: {mutations}")
        text.append(f"Repairs: {repairs}")
        text.append(f"Validations: {validations}")
        text.append(f"Latest variable: {latest_var}")
        text.append(f"Latest equation: {latest_eq}")
        text.append("")
        text.append("[Engine State]")
        text.append(f"Decision kind: {decision.get('kind', 'none')}")
        text.append(f"Decision target: {decision.get('target', '-') or '-'}")
        text.append(f"Decision reason: {decision.get('reason', '-') or '-'}")
        text.append(f"Fallback equations: {fallback_nodes}")
        text.append(f"Repairable equations: {repairable_nodes}")
        text.append(f"Stable parents: {stable_parent_nodes}")
        text.append("")
        text.append("[Evolution Metrics]")
        text.append(f"Lineage nodes: {nodes}")
        text.append(f"Lineage nodes with parent: {parent_nodes}")
        text.append(f"Roots / isolated: {root_nodes}")
        text.append(f"Mutable equations: {mutable_nodes}")
        text.append(f"Repeated failures: {repeated_failures}")
        text.append(f"Approval ratio: {approval_ratio}")
        if merge:
            text.append("")
            text.append("[Last Merge]")
            text.append("Sources: {', '.join(merge.get('source_sessions', []))}")
            text.append("Variables final: {merge.get('variables_final', 0)}")
            text.append("Equations final: {merge.get('equations_final', 0)}")
            text.append("Mutation links final: {merge.get('mutation_links_final', 0)}")
        if feedback:
            text.append("")
            text.append("[Recent Reactions]")
            for item in feedback:
                sign = "+" if int(item.get("delta", 0) or 0) > 0 else ""
                text.append(f"- {item.get('kind', '?')}: {sign}{item.get('delta', 0)} x{item.get('weight', 1)} | {str(item.get('target', ''))[:80]}")
        text.append("")
        text.append("[Build Priorities]")
        text.append("- Stabilisation moteur")
        text.append("- UI final propre")
        text.append("- Dashboard scientifique")
        text.append("- Quality upgrade scientifique")
        self.science_text.SetValue("\n".join(text))
        self.science_graph_label.SetLabel("Equation Lineage Tree")
        self._populate_science_lineage_tree(shared)
        self.refresh_header_badges()
        self.apply_theme()

    def _filter_equations(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        idx = self.eq_filter.GetSelection()
        if idx == 1:
            return [r for r in rows if bool(r.get("approved", False))]
        if idx == 2:
            return [r for r in rows if not bool(r.get("approved", False))]
        if idx == 3:
            return [r for r in rows if bool(r.get("repair_required", False)) or str(r.get("status", "")).lower() == "partial"]
        if idx == 4:
            return [r for r in rows if bool(r.get("approved", False)) and bool(r.get("stable_parent", False)) and not bool(r.get("repair_required", False)) and not bool(r.get("fallback_used", False))]
        return rows

    def refresh_equations_view(self) -> None:
        name = self.eq_session.GetValue().strip()
        self.current_equation_rows = []
        self.eq_list.Set([])
        self.eq_details.SetValue("")
        if hasattr(self, "eq_tree"):
            self.eq_tree.DeleteAllItems()
        if not name:
            return
        shared = self.load_shared(name)
        rows = []
        for bucket_name in ["approved_equations", "partial_equations"]:
            for row in shared.get(bucket_name, []) or []:
                payload = dict(row)
                payload["_bucket"] = bucket_name
                rows.append(payload)
        rows = self._filter_equations(rows)
        rows.sort(key=lambda r: int(r.get("source_turn", 0) or 0), reverse=True)
        self.current_equation_rows = rows
        labels = [f"T{int(r.get('source_turn', 0) or 0):03d} | {str(r.get('equation', ''))[:120]}" for r in rows]
        self.eq_list.Set(labels)
        self._populate_equation_tree(rows)
        if labels:
            self.eq_list.SetSelection(0)
            self._select_equation_tree_equation(str(rows[0].get("equation", "") or ""))
            self.on_equation_selected(None)
        self.apply_theme()

    def _equation_rows_for_shared(self, shared: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for bucket_name in ["approved_equations", "partial_equations"]:
            for row in shared.get(bucket_name, []) or []:
                payload = dict(row)
                payload["_bucket"] = bucket_name
                rows.append(payload)
        return rows

    def _variable_usage_maps(self, shared: dict[str, Any]) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        scores = dict(shared.get("variable_scores", {}) or {})
        usage = dict(shared.get("variable_usage_count", {}) or {})
        failures = dict(shared.get("variable_failures", {}) or {})
        return scores, usage, failures

    def _variable_linked(self, symbol: str, equation_rows: list[dict[str, Any]]) -> bool:
        sym = str(symbol or "").strip()
        if not sym:
            return False
        for row in equation_rows:
            defs = row.get("definitions", {}) or {}
            if sym in defs or sym.lower() in {str(k).lower() for k in defs.keys()}:
                return True
            eq = str(row.get("equation", "") or "")
            if sym and sym in eq:
                return True
        return False

    def _filter_variables(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        idx = self.var_filter.GetSelection()
        if idx == 1:
            return [r for r in rows if bool(r.get("approved", False))]
        if idx == 2:
            return [r for r in rows if not bool(r.get("approved", False))]
        if idx == 3:
            return [r for r in rows if bool(r.get("linked", False))]
        if idx == 4:
            return [r for r in rows if int(r.get("usage_count", 0) or 0) <= 0]
        return rows

    def refresh_variables_view(self) -> None:
        name = self.var_session.GetValue().strip()
        self.current_variable_rows = []
        self.var_list.Set([])
        self.var_details.SetValue("")
        if hasattr(self, "var_tree"):
            self.var_tree.DeleteAllItems()
        if not name:
            return
        shared = self.load_shared(name)
        eq_rows = self._equation_rows_for_shared(shared)
        scores, usage, failures = self._variable_usage_maps(shared)
        rows = []
        for approved, bucket_name in [(True, "approved_variables"), (False, "candidate_variables")]:
            for symbol, payload in (shared.get(bucket_name, {}) or {}).items():
                row = dict(payload or {})
                row["symbol"] = str(symbol)
                row["approved"] = approved
                row["bucket"] = bucket_name
                row["score"] = int(scores.get(symbol, scores.get(str(symbol).strip(), 0)) or 0)
                row["usage_count"] = int(usage.get(symbol, usage.get(str(symbol).strip(), 0)) or 0)
                row["failure_count"] = int(failures.get(symbol, failures.get(str(symbol).strip(), 0)) or 0)
                row["linked"] = self._variable_linked(symbol, eq_rows)
                rows.append(row)
        rows = self._filter_variables(rows)
        rows.sort(key=lambda r: (int(r.get("score", 0) or 0), int(r.get("usage_count", 0) or 0), str(r.get("symbol", ""))), reverse=True)
        self.current_variable_rows = rows
        labels = [f"{str(r.get('symbol', ''))} | score={int(r.get('score', 0) or 0)} | usage={int(r.get('usage_count', 0) or 0)}" for r in rows]
        self.var_list.Set(labels)
        self._populate_variable_tree(rows)
        if labels:
            self.var_list.SetSelection(0)
            self._select_variable_tree_symbol(str(rows[0].get("symbol", "") or ""))
            self.on_variable_selected(None)
        self.apply_theme()

    def on_variable_selected(self, event) -> None:
        idx = self.var_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_variable_rows):
            self.var_details.SetValue("")
            return
        row = self.current_variable_rows[idx]
        symbol = str(row.get("symbol", "") or "")
        if symbol:
            self._select_variable_tree_symbol(symbol)
        details = []
        for key in [
            "symbol",
            "definition",
            "unit",
            "responsibility",
            "status",
            "family",
            "approved",
            "linked",
            "score",
            "usage_count",
            "failure_count",
        ]:
            details.append(f"{key}: {row.get(key, '-')}")
        links = row.get("links", []) or []
        if links:
            details.append("links:\n- " + "\n- ".join(str(x) for x in links))
        remarks = row.get("remarks", []) or []
        if remarks:
            details.append("remarks:\n- " + "\n- ".join(str(x) for x in remarks))
        required_next = row.get("required_next", []) or []
        if required_next:
            details.append("required_next:\n- " + "\n- ".join(str(x) for x in required_next))
        self.var_details.SetValue("\n".join(details))

    def current_variable(self) -> str:
        idx = self.var_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_variable_rows):
            return ""
        return str(self.current_variable_rows[idx].get("symbol", "") or "")

    def on_copy_variable(self, event) -> None:
        value = self.current_variable()
        if not value:
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(value))
            wx.TheClipboard.Close()
        self.log("[INFO] Variable copied.")

    def _linked_equations_for_variable(self, symbol: str, session_name: str) -> list[dict[str, Any]]:
        shared = self.load_shared(session_name)
        rows = self._equation_rows_for_shared(shared)
        sym = str(symbol or "").strip()
        linked = []
        for row in rows:
            defs = row.get("definitions", {}) or {}
            eq = str(row.get("equation", "") or "")
            score = 0
            if sym in defs or sym.lower() in {str(k).lower() for k in defs.keys()}:
                score += 3
            if sym and sym in eq:
                score += 2
            if score:
                payload = dict(row)
                payload["_link_score"] = score
                linked.append(payload)
        linked.sort(key=lambda r: (int(r.get("_link_score", 0)), bool(r.get("approved", False)), bool(r.get("stable_parent", False)), -bool(r.get("repair_required", False)), int(r.get("source_turn", 0) or 0)), reverse=True)
        return linked

    def _best_equation_for_variable(self, symbol: str, session_name: str, mode: str) -> str:
        linked = self._linked_equations_for_variable(symbol, session_name)
        if not linked:
            return ""
        if mode == "repair":
            for row in linked:
                if bool(row.get("repair_required", False)) or str(row.get("status", "")).lower() == "partial":
                    return str(row.get("equation", "") or "")
        if mode == "mutation":
            for row in linked:
                if bool(row.get("approved", False)) and bool(row.get("stable_parent", False)) and not bool(row.get("repair_required", False)) and not bool(row.get("fallback_used", False)):
                    return str(row.get("equation", "") or "")
        return str(linked[0].get("equation", "") or "")


    def _reaction_weight(self, session_name: str, kind: str, target: str, delta: int) -> int:
        shared = self.load_shared(session_name)
        feedback = list(shared.get("reaction_feedback", []) or [])[-20:]
        streak = 0
        for item in reversed(feedback):
            if str(item.get("kind", "")) != kind or str(item.get("target", "")) != target:
                continue
            item_delta = int(item.get("delta", 0) or 0)
            if item_delta == 0 or (1 if item_delta > 0 else -1) != (1 if delta > 0 else -1):
                break
            streak += 1
            if streak >= 4:
                break
        return 1 + min(2, streak // 2)

    def _bump_equation_feedback(self, session_name: str, equation: str, delta: int) -> bool:
        shared = self.load_shared(session_name)
        if not shared or not equation:
            return False
        scores = dict(shared.get("equation_scores", {}) or {})
        usages = dict(shared.get("equation_usage_count", {}) or {})
        key = equation.strip()
        weight = self._reaction_weight(session_name, "equation", key, delta)
        scores[key] = int(scores.get(key, 0) or 0) + int(delta) * weight
        usages[key] = int(usages.get(key, 0) or 0) + 1
        shared["equation_scores"] = scores
        shared["equation_usage_count"] = usages
        feedback = list(shared.get("reaction_feedback", []) or [])
        feedback.append({"kind": "equation", "target": key, "delta": int(delta), "weight": weight})
        shared["reaction_feedback"] = feedback[-200:]
        self.save_shared(session_name, shared)
        return True

    def _bump_variable_feedback(self, session_name: str, symbol: str, delta: int) -> bool:
        shared = self.load_shared(session_name)
        if not shared or not symbol:
            return False
        scores = dict(shared.get("variable_scores", {}) or {})
        usages = dict(shared.get("variable_usage_count", {}) or {})
        key = symbol.strip()
        weight = self._reaction_weight(session_name, "variable", key, delta)
        scores[key] = int(scores.get(key, 0) or 0) + int(delta) * weight
        usages[key] = int(usages.get(key, 0) or 0) + 1
        shared["variable_scores"] = scores
        shared["variable_usage_count"] = usages
        feedback = list(shared.get("reaction_feedback", []) or [])
        feedback.append({"kind": "variable", "target": key, "delta": int(delta), "weight": weight})
        shared["reaction_feedback"] = feedback[-200:]
        self.save_shared(session_name, shared)
        return True

    def on_equation_feedback(self, delta: int) -> None:
        eq = self.current_equation().strip()
        session = self.eq_session.GetValue().strip()
        if not eq or not session:
            return
        if self._bump_equation_feedback(session, eq, delta):
            weight = self._reaction_weight(session, 'equation', eq, delta)
            self.log(f"[INFO] Equation feedback saved: {'+' if delta > 0 else ''}{delta} x{weight} | {eq[:120]}")
            self.refresh_equations_view()
            self.refresh_scientific_view()
            self.refresh_eqtest_view()

    def on_variable_feedback(self, delta: int) -> None:
        symbol = self.current_variable().strip()
        session = self.var_session.GetValue().strip()
        if not symbol or not session:
            return
        if self._bump_variable_feedback(session, symbol, delta):
            weight = self._reaction_weight(session, 'variable', symbol, delta)
            self.log(f"[INFO] Variable feedback saved: {'+' if delta > 0 else ''}{delta} x{weight} | {symbol}")
            self.refresh_variables_view()
            self.refresh_scientific_view()

    def on_variable_prepare(self, mode: str) -> None:
        symbol = self.current_variable().strip()
        session = self.var_session.GetValue().strip()
        if not symbol or not session:
            return
        target_eq = self._best_equation_for_variable(symbol, session, mode)
        if not target_eq:
            self.log(f"[INFO] No linked equation found for variable: {symbol}")
            return
        self.prepared_target_equation = target_eq
        self.mode_choice.SetStringSelection(mode)
        self.session_strategy.SetSelection(0)
        self.session_choice.SetValue(session)
        self.active_session_name = session
        self.update_mode_view()
        self.update_cmd_preview()
        self.notebook.SetSelection(0)
        self.log(f"[INFO] Mode {mode} prepared from variable {symbol}.")

    def on_equation_selected(self, event) -> None:
        idx = self.eq_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_equation_rows):
            self.eq_details.SetValue("")
            return
        row = self.current_equation_rows[idx]
        eq = str(row.get("equation", "") or "")
        if eq:
            self._select_equation_tree_equation(eq)
        details = []
        for key in ["equation", "status", "parent_equation", "approved", "stable_parent", "repair_required", "fallback_used", "object_calculated", "architecture", "law_type", "mechanism", "experiment", "validation_summary"]:
            details.append(f"{key}: {row.get(key, '')}")
        if row.get("links"):
            details.append("links:\n- " + "\n- ".join(str(x) for x in row.get("links", [])))
        if row.get("definitions"):
            defs = row.get("definitions", {}) or {}
            details.append("definitions:\n" + "\n".join(f"{k}: {v}" for k, v in defs.items()))
        self.eq_details.SetValue("\n".join(details))

    def current_equation(self) -> str:
        idx = self.eq_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_equation_rows):
            return ""
        return str(self.current_equation_rows[idx].get("equation", "") or "")

    def on_copy_equation(self, event) -> None:
        eq = self.current_equation()
        if not eq:
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(eq))
            wx.TheClipboard.Close()
        self.log("[INFO] Equation copied.")

    def _prepare_target(self, mode: str) -> None:
        eq = self.current_equation().strip()
        session = self.eq_session.GetValue().strip()
        if not eq or not session:
            return
        self.prepared_target_equation = eq
        self.mode_choice.SetStringSelection(mode)
        self.session_strategy.SetSelection(0)
        self.session_choice.SetValue(session)
        self.active_session_name = session
        self.update_mode_view()
        self.update_cmd_preview()
        self.notebook.SetSelection(0)
        self.log(f"[INFO] Mode {mode} prepared with selected session.")

    def on_prepare_repair(self, event) -> None:
        self._prepare_target("repair")

    def on_prepare_mutation(self, event) -> None:
        self._prepare_target("mutation")

    def _test_rows_for_session(self, name: str) -> list[dict[str, Any]]:
        shared = self.load_shared(name)
        rows = self._equation_rows_for_shared(shared)
        rows.sort(key=lambda r: int(r.get("source_turn", 0) or 0), reverse=True)
        return rows

    def _eqtest_verdict_for_row(self, row: dict[str, Any]) -> str:
        approved = bool(row.get("approved", False))
        stable_parent = bool(row.get("stable_parent", False))
        repair_required = bool(row.get("repair_required", False))
        fallback = bool(row.get("fallback_used", False))
        status = str(row.get("status", "") or "").lower()
        can_mutate = approved and stable_parent and not repair_required and not fallback
        can_repair = repair_required or (not approved) or fallback or status == "partial"
        if can_mutate:
            return "READY FOR MUTATION"
        if can_repair:
            return "NEEDS REPAIR"
        return "REVIEW MANUALLY"

    def _filter_test_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not hasattr(self, "test_filter_choice"):
            return rows
        choice = str(self.test_filter_choice.GetStringSelection() or "All")
        if choice == "All":
            return rows
        filtered: list[dict[str, Any]] = []
        for row in rows:
            verdict = self._eqtest_verdict_for_row(row)
            if choice == "Ready for mutation" and verdict == "READY FOR MUTATION":
                filtered.append(row)
            elif choice == "Needs repair" and verdict == "NEEDS REPAIR":
                filtered.append(row)
            elif choice == "Review manually" and verdict == "REVIEW MANUALLY":
                filtered.append(row)
            elif choice == "Ready for test" and verdict in {"READY FOR MUTATION", "REVIEW MANUALLY"}:
                filtered.append(row)
        return filtered

    def current_test_equation(self) -> str:
        if hasattr(self, "test_list"):
            idx = self.test_list.GetSelection()
            if idx != wx.NOT_FOUND and 0 <= idx < len(self.current_test_rows):
                return normalize_target_equation(str(self.current_test_rows[idx].get("equation", "") or ""))
        if hasattr(self, "test_equation_choice") and self.test_equation_choice.GetSelection() != wx.NOT_FOUND:
            idx = self.test_equation_choice.GetSelection()
            if 0 <= idx < len(self.current_test_rows):
                return normalize_target_equation(str(self.current_test_rows[idx].get("equation", "") or ""))
        if hasattr(self, "test_equation_choice"):
            return normalize_target_equation(self.test_equation_choice.GetValue())
        return ""

    def _populate_test_equation_choices(self, rows: list[dict[str, Any]]) -> None:
        labels = [f"T{int(r.get('source_turn', 0) or 0):03d} | {str(r.get('equation', ''))[:120]}" for r in rows]
        current = self.test_equation_choice.GetValue()
        self.test_equation_choice.SetItems(labels)
        self.test_list.Set(labels)
        if labels:
            if current in labels:
                self.test_equation_choice.SetValue(current)
                idx = labels.index(current)
            else:
                idx = 0
                self.test_equation_choice.SetValue(labels[0])
            self.test_list.SetSelection(idx)
        else:
            self.test_equation_choice.SetValue("")

    def _populate_test_equation_tree(self, rows: list[dict[str, Any]]) -> None:
        if not hasattr(self, "test_tree"):
            return
        self.test_tree.DeleteAllItems()
        root = self.test_tree.AddRoot("Equation Test Tree")
        self._apply_tree_item_colors(self.test_tree, root, kind="root")
        by_equation = {str(r.get("equation", "") or "").strip(): dict(r) for r in rows if str(r.get("equation", "") or "").strip()}
        appended = set()

        def label_for(row: dict[str, Any]) -> str:
            eq = str(row.get("equation", "") or "").strip()
            status = str(row.get("status", "") or "").strip()
            flags = []
            if status:
                flags.append(status)
            if bool(row.get("repair_required", False)):
                flags.append("repair")
            if bool(row.get("fallback_used", False)):
                flags.append("fallback")
            if bool(row.get("stable_parent", False)):
                flags.append("stable")
            return eq if not flags else f"{eq} | {' | '.join(flags)}"

        def add_branch(eq: str, parent_item):
            if eq in appended:
                return
            row = by_equation.get(eq, {})
            item = self.test_tree.AppendItem(parent_item, label_for(row))
            self.test_tree.SetItemData(item, eq)
            self._apply_tree_item_colors(self.test_tree, item, row, kind="equation")
            appended.add(eq)
            children = [
                child_eq
                for child_eq, child_row in by_equation.items()
                if str(child_row.get("parent_equation", "") or "").strip() == eq
            ]
            for child_eq in sorted(children):
                add_branch(child_eq, item)

        roots = [
            eq for eq, row in by_equation.items()
            if not str(row.get("parent_equation", "") or "").strip()
            or str(row.get("parent_equation", "") or "").strip() not in by_equation
        ]
        for eq in sorted(roots):
            add_branch(eq, root)

        orphaned = [eq for eq in by_equation if eq not in appended]
        if orphaned:
            orphan_root = self.test_tree.AppendItem(root, "Detached / unresolved nodes")
            self._apply_tree_item_colors(self.test_tree, orphan_root, {"bucket": "pending"}, kind="bucket")
            for eq in sorted(orphaned):
                add_branch(eq, orphan_root)

        self.test_tree.Expand(root)
        first, cookie = self.test_tree.GetFirstChild(root)
        if first.IsOk():
            self.test_tree.SelectItem(first)

    def on_test_tree_selected(self, event) -> None:
        item = event.GetItem() if event is not None else self.test_tree.GetSelection()
        if not item or not item.IsOk():
            return
        eq = str(self.test_tree.GetItemData(item) or "").strip()
        if not eq:
            return
        for i, row in enumerate(self.current_test_rows):
            if str(row.get("equation", "") or "").strip() == eq:
                self.test_list.SetSelection(i)
                self.test_equation_choice.SetSelection(i)
                shared = self.load_shared(self.test_session.GetValue().strip())
                self.test_report.SetValue(self._agent_report_for_row(row, shared))
                return

    def _select_test_tree_equation(self, equation: str) -> None:
        if not hasattr(self, "test_tree"):
            return
        target = str(equation or "").strip()
        if not target:
            return
        root = self.test_tree.GetRootItem()
        if not root or not root.IsOk():
            return

        def walk(item):
            data = str(self.test_tree.GetItemData(item) or "").strip()
            if data == target:
                return item
            child, cookie = self.test_tree.GetFirstChild(item)
            while child.IsOk():
                found = walk(child)
                if found and found.IsOk():
                    return found
                child, cookie = self.test_tree.GetNextChild(item, cookie)
            return None

        found = walk(root)
        if found and found.IsOk():
            self.test_tree.SelectItem(found)
            parent = self.test_tree.GetItemParent(found)
            while parent and parent.IsOk():
                self.test_tree.Expand(parent)
                parent = self.test_tree.GetItemParent(parent)

    def _agent_report_for_row(self, row: dict[str, Any], shared: dict[str, Any]) -> str:
        eq = str(row.get("equation", "") or "")
        defs = row.get("definitions", {}) or {}
        links = row.get("links", []) or []
        mechanism = str(row.get("mechanism", "") or "")
        experiment = str(row.get("experiment", "") or "")
        architecture = str(row.get("architecture", "") or "")
        obj = str(row.get("object_calculated", "") or "")
        law = str(row.get("law_type", "") or "")
        parent = str(row.get("parent_equation", "") or "")
        approved = bool(row.get("approved", False))
        stable_parent = bool(row.get("stable_parent", False))
        repair_required = bool(row.get("repair_required", False))
        fallback = bool(row.get("fallback_used", False))
        score_map = shared.get("equation_scores", {}) or {}
        usage_map = shared.get("equation_usage_count", {}) or {}
        fail_map = shared.get("equation_failures", {}) or {}
        score = score_map.get(eq, 0)
        usage = usage_map.get(eq, 0)
        failures = fail_map.get(eq, 0)
        completeness = sum(1 for value in [eq, obj, architecture, law, mechanism, experiment, parent] if str(value).strip())
        completeness += min(2, len(defs)) + min(2, len(links)) + (1 if row.get("validation_summary") else 0)
        can_mutate = approved and stable_parent and not repair_required and not fallback
        can_repair = repair_required or (not approved) or fallback or str(row.get("status", "")).lower() == "partial"
        verdict = "READY FOR MUTATION" if can_mutate else ("NEEDS REPAIR" if can_repair else "REVIEW MANUALLY")
        aurelius = []
        aurelius.append(f"Object: {obj or 'missing'}")
        aurelius.append(f"Architecture: {architecture or 'missing'}")
        aurelius.append(f"Law type: {law or 'missing'}")
        if not obj or not architecture:
            aurelius.append("Main weakness: target quantity or structure is missing.")
        basilide = []
        basilide.append(f"Mechanism: {mechanism or 'missing'}")
        basilide.append(f"Experiment: {experiment or 'missing'}")
        basilide.append(f"Definitions: {len(defs)} | Links: {len(links)}")
        if not experiment:
            basilide.append("Needs an explicit experimental protocol.")
        chymicus = []
        chymicus.append(f"Approved={approved} | Repair required={repair_required} | Fallback={fallback}")
        chymicus.append(f"Score={score} | Usage={usage} | Failures={failures}")
        if failures:
            chymicus.append("Repeated failures detected; do not trust without inspection.")
        sentinelle = []
        sentinelle.append(f"Status: {row.get('status', '') or '-'}")
        sentinelle.append(f"Stable parent: {stable_parent} | Parent present: {bool(parent)}")
        sentinelle.append(f"Mutable now: {can_mutate} | Repairable now: {can_repair}")
        hermes = []
        hermes.append(f"Equation family signal: {law or architecture or 'unclassified'}")
        hermes.append(f"Symbolic anchor: {'structured' if architecture and mechanism else 'weak'}")
        if parent:
            hermes.append("Lineage anchor available through parent equation.")
        lines = [
            f"Session: {self.test_session.GetValue().strip()}",
            "",
            f"Equation: {eq}",
            f"Verdict: {verdict}",
            f"Completeness score: {completeness}",
            "",
            "[Aurelius | structure]",
            *aurelius,
            "",
            "[Basilide | mechanism/experiment]",
            *basilide,
            "",
            "[Chymicus | critique]",
            *chymicus,
            "",
            "[Sentinelle | validation]",
            *sentinelle,
            "",
            "[Hermes | symbolic/readiness]",
            *hermes,
        ]
        if row.get("validation_summary"):
            lines += ["", "Validation summary:", str(row.get("validation_summary", ""))]
        return "\n".join(lines)

    def current_test_row(self) -> dict[str, Any]:
        idx = self.test_list.GetSelection()
        if idx == wx.NOT_FOUND and self.test_equation_choice.GetSelection() != wx.NOT_FOUND:
            idx = self.test_equation_choice.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_test_rows):
            return {}
        row = self.current_test_rows[idx] or {}
        return dict(row) if isinstance(row, dict) else {}

    def current_test_equation(self) -> str:
        row = self.current_test_row()
        equation = row.get("equation", "") if isinstance(row, dict) else ""
        return normalize_target_equation(equation)

    def load_equation_test_report(self, session: str, equation: str = "", focus_eqtest: bool = False) -> None:
        session_name = str(session or "").strip()
        if not session_name:
            return
        report_path = local_session_dir(session_name) / "equation_test_report.txt"
        if not report_path.exists():
            self.log(f"[INFO] Equation test report not found for session {session_name}.")
            return
        try:
            report = report_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            self.log(f"[ERREUR] Unable to read equation test report: {type(exc).__name__}: {exc}")
            return
        if not report:
            return
        self.refresh_eqtest_view()
        wanted = normalize_target_equation(equation)
        if wanted:
            for i, row in enumerate(self.current_test_rows):
                candidate = normalize_target_equation(str((row or {}).get("equation", "") or ""))
                if candidate == wanted:
                    self.test_list.SetSelection(i)
                    self.test_equation_choice.SetSelection(i)
                    break
        self.test_report.SetValue(report)
        if hasattr(self, "test_selected_label"):
            self.test_selected_label.SetLabel(f"Selected Equation: {equation or '-'}")
        if hasattr(self, "test_verdict_label"):
            idx = self.test_list.GetSelection()
            if idx != wx.NOT_FOUND and 0 <= idx < len(self.current_test_rows):
                self.test_verdict_label.SetLabel(f"Current Verdict: {self._eqtest_verdict_for_row(self.current_test_rows[idx])}")
            else:
                self.test_verdict_label.SetLabel("Current Verdict: report loaded")
        self.set_eqtest_state("report ready")
        if focus_eqtest:
            eqtest_index = self.notebook.FindPage(self.eqtest_tab)
            if eqtest_index != wx.NOT_FOUND:
                self.notebook.SetSelection(eqtest_index)
        self.log("[INFO] Equation test report loaded in Equation Test.")

    def refresh_eqtest_view(self) -> None:
        name = self.test_session.GetValue().strip()
        self.current_test_rows = []
        self.current_test_all_rows = []
        self.set_eqtest_state("idle")
        if hasattr(self, "test_verdict_label"):
            self.test_verdict_label.SetLabel("Current Verdict: -")
        if hasattr(self, "test_selected_label"):
            self.test_selected_label.SetLabel("Selected Equation: -")
        self.test_report.SetValue("")
        self.test_list.Set([])
        self.test_equation_choice.SetItems([])
        if not name:
            return
        rows = self._test_rows_for_session(name)
        self.current_test_all_rows = rows
        rows = self._filter_test_rows(rows)
        self.current_test_rows = rows
        self._populate_test_equation_choices(rows)
        self._populate_test_equation_tree(rows)
        if rows:
            self.on_test_equation_selected(None)
        else:
            self.test_report.SetValue("[INFO] No equations match the current test filter.")
        self.apply_theme()

    def on_test_equation_selected(self, event) -> None:
        idx = self.test_list.GetSelection()
        if idx == wx.NOT_FOUND and self.test_equation_choice.GetSelection() != wx.NOT_FOUND:
            idx = self.test_equation_choice.GetSelection()
            self.test_list.SetSelection(idx)
        elif idx != wx.NOT_FOUND:
            self.test_equation_choice.SetSelection(idx)
        if idx == wx.NOT_FOUND or idx >= len(self.current_test_rows):
            self.test_report.SetValue("")
            return
        row = self.current_test_rows[idx]
        eq = str(row.get("equation", "") or "").strip()
        self._select_test_tree_equation(eq)
        shared = self.load_shared(self.test_session.GetValue().strip())
        self.test_report.SetValue(self._agent_report_for_row(row, shared))

    def current_test_equation(self) -> str:
        idx = self.test_list.GetSelection()
        if idx != wx.NOT_FOUND and idx < len(self.current_test_rows):
            return str(self.current_test_rows[idx].get("equation", "") or "").strip()
        idx = self.test_equation_choice.GetSelection()
        if idx != wx.NOT_FOUND and idx < len(self.current_test_rows):
            return str(self.current_test_rows[idx].get("equation", "") or "").strip()
        return normalize_target_equation(self.test_equation_choice.GetValue()).strip()

    def on_use_selected_equation_for_test(self, event) -> None:
        session = self.eq_session.GetValue().strip() or self.active_session_name or self.session_choice.GetValue().strip()
        if session:
            self.test_session.SetValue(session)
        self.refresh_eqtest_view()
        eq = self.current_equation().strip()
        if not eq:
            return
        for i, row in enumerate(self.current_test_rows):
            if str(row.get("equation", "") or "").strip() == eq:
                self.test_list.SetSelection(i)
                self.test_equation_choice.SetSelection(i)
                self.on_test_equation_selected(None)
                break

    def on_copy_test_report(self, event) -> None:
        value = self.test_report.GetValue().strip()
        if not value:
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(value))
            wx.TheClipboard.Close()
        self.log("[INFO] Equation test report copied.")

    def on_test_prepare_repair(self, event) -> None:
        idx = self.test_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_test_rows):
            return
        eq = str(self.current_test_rows[idx].get("equation", "") or "").strip()
        session = self.test_session.GetValue().strip()
        if not eq or not session:
            return
        self.prepared_target_equation = eq
        self.mode_choice.SetStringSelection("repair")
        self.refresh_header_badges()
        self.session_strategy.SetSelection(0)
        self.session_choice.SetValue(session)
        self.active_session_name = session
        self.update_mode_view()
        self.update_cmd_preview()
        self.notebook.SetSelection(0)
        self.log("[INFO] Repair prepared from Equation Test.")

    def on_test_prepare_mutation(self, event) -> None:
        idx = self.test_list.GetSelection()
        if idx == wx.NOT_FOUND or idx >= len(self.current_test_rows):
            return
        eq = str(self.current_test_rows[idx].get("equation", "") or "").strip()
        session = self.test_session.GetValue().strip()
        if not eq or not session:
            return
        self.prepared_target_equation = eq
        self.mode_choice.SetStringSelection("mutation")
        self.refresh_header_badges()
        self.session_strategy.SetSelection(0)
        self.session_choice.SetValue(session)
        self.active_session_name = session
        self.update_mode_view()
        self.update_cmd_preview()
        self.notebook.SetSelection(0)
        self.log("[INFO] Mutation prepared from Equation Test.")

    def on_use_active_eqtest(self, event) -> None:
        session = self.active_session_name or self.eq_session.GetValue().strip() or self.science_session.GetValue().strip() or self.session_choice.GetValue().strip()
        if session:
            self.test_session.SetValue(session)
            self.refresh_eqtest_view()

    def on_use_active_science(self, event) -> None:
        session = self.active_session_name or self.session_choice.GetValue().strip() or self.eq_session.GetValue().strip()
        if session:
            self.science_session.SetValue(session)
            self.refresh_scientific_view()

    def on_use_active_equations(self, event) -> None:
        session = self.active_session_name or self.session_choice.GetValue().strip() or self.science_session.GetValue().strip()
        if session:
            self.eq_session.SetValue(session)
            self.refresh_equations_view()

    def on_use_active_variables(self, event) -> None:
        session = self.active_session_name or self.session_choice.GetValue().strip() or self.science_session.GetValue().strip() or self.eq_session.GetValue().strip()
        if session:
            self.var_session.SetValue(session)
            self.refresh_variables_view()


def main() -> None:
    app = wx.App(False)
    frame = LauncherFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
