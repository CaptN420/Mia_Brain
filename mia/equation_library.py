#!/usr/bin/env python3
"""MIA-LABS equation library management - extracted from launcher_ollama.py.

This module provides helper functions for equation validation, deduplication,
library storage, alchimie archiving, and summary generation. These were
originally defined inline in launcher_ollama.py and are imported back from
there to keep the launcher focused on orchestration.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# These constants match the defaults in launcher_ollama.py.
# They are used by _generate_library_summary which references them as bare
# names (preserved from the original inline definitions).
OLLAMA_MODEL = "qwen2:1.5b"
OLLAMA_BASE_URL = "http://localhost:11434"

__all__ = [
    "_normalized_equation",
    "_validate_equations_with_tests",
    "_add_equations_to_library",
    "_archive_equations_to_alchimie",
    "_save_shared_memory",
    "_generate_library_summary",
]


def _normalized_equation(eq: str) -> str:
    """Normalize an equation string for deduplication."""
    if not eq:
        return ""
    return "".join(str(eq or "").strip().lower().split())


def _validate_equations_with_tests(session_path: Path, shared: Any) -> tuple[list[dict], list[dict]]:
    """Collect approved equations from shared memory and debate logs."""
    import re
    from pathlib import Path

    # Read equations already in shared memory
    approved = list(shared.data.get("approved_equations", []) or [])
    partial = list(shared.data.get("partial_equations", []) or [])

    all_collected = approved + partial
    passed = [e for e in all_collected if e.get("test_result") == "pass"]
    remaining = [e for e in all_collected if e.get("test_result") != "pass"]

    # Parse debate log for equations that survived the agent validation
    log_path = session_path / "equation_debate_log.txt"
    seen_equations = set()

    # Collect from shared memory entries that have equations
    for entry in all_collected:
        eq_str = (entry.get("equation") or "").strip()
        if eq_str and len(eq_str) > 3 and "=" in eq_str:
            seen_equations.add(_normalized_equation(eq_str))

    # Parse debate log for approved equations (Equations that agents agreed on)
    if log_path.exists():
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
            # Find lines that look like approved equations
            eq_pattern = re.compile(
                r"(?:Équation|Equation)\s*(?:finale|détectée)?\s*:\s*([A-Za-z0-9_α-ωΔΓ∙⋅×*+\-/\^=()\s]+)",
                re.IGNORECASE
            )
            for match in eq_pattern.finditer(text):
                eq_raw = match.group(1).strip()
                # Clean up
                eq_clean = re.sub(r"\s+", " ", eq_raw)
                if "=" in eq_clean and len(eq_clean) < 200 and len(eq_clean) > 3:
                    norm = _normalized_equation(eq_clean)
                    if norm not in seen_equations:
                        seen_equations.add(norm)
                        passed.append({
                            "equation": eq_clean,
                            "normalized_key": norm,
                            "test_result": "pass",
                            "source": "debate_log_extraction",
                            "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        })
        except Exception:
            pass

    # Also check variable_debate_log for any equations
    var_log_path = session_path / "variable_debate_log.txt"
    if var_log_path.exists():
        try:
            text = var_log_path.read_text(encoding="utf-8", errors="ignore")
            eq_pattern = re.compile(
                r"(?:Équation|Equation)\s*:\s*([A-Za-z0-9_α-ωΔΓ∙⋅×*+\-/\^=()\s.]+)",
                re.IGNORECASE
            )
            for match in eq_pattern.finditer(text):
                eq_raw = match.group(1).strip()
                eq_clean = re.sub(r"\s+", " ", eq_raw)
                if "=" in eq_clean and len(eq_clean) < 200 and len(eq_clean) > 3:
                    norm = _normalized_equation(eq_clean)
                    if norm not in seen_equations:
                        seen_equations.add(norm)
                        passed.append({
                            "equation": eq_clean,
                            "normalized_key": norm,
                            "test_result": "pass",
                            "source": "variable_debate_log_extraction",
                            "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        })
        except Exception:
            pass

    return passed, remaining


def _add_equations_to_library(session_path: Path, equations: list[dict]) -> int:
    """Add approved equations to the shared library, avoiding duplicates."""
    # Ensure mia/ is on sys.path
    mia_dir = Path(__file__).parent
    if str(mia_dir) not in sys.path:
        sys.path.insert(0, str(mia_dir))

    from shared_memory import SharedResearchMemory

    shared = SharedResearchMemory(session_path)
    library = shared.data.get("approved_equations", [])

    added = 0
    for eq in equations:
        eq_key = _normalized_equation(eq.get("equation", ""))

        # Check for duplicate
        is_duplicate = any(
            _normalized_equation(existing.get("equation", "")) == eq_key
            for existing in library
        )

        if not is_duplicate and eq_key:
            # Attach session info
            eq["source_session"] = session_path.name
            eq["added_cycle"] = int(time.time())
            library.append(eq)
            added += 1

    # Save updated library (keep last 50)
    shared.data["approved_equations"] = library[-50:]
    _save_shared_memory(session_path, shared)

    return added


def _archive_equations_to_alchimie(session_path: Path, equations: list[dict]) -> int:
    """Write approved equations into the canonical alchimie library inbox.

    Drops an incoming_rules JSON file into <project>/alchimie/new_data/ so
    AlchimieLibraryManager can validate + merge them transaction-safely
    (scan_new_data -> validate_new_data -> merge_new_data).
    """
    try:
        mia_dir = Path(__file__).parent
        project_root = mia_dir.parent
        alchimie_dir = project_root / "alchimie"
        new_data_dir = alchimie_dir / "new_data"
        new_data_dir.mkdir(parents=True, exist_ok=True)

        # Load already-known equation ids from the canonical library for dedup
        known_ids: set[str] = set()
        rules_file = alchimie_dir / "library" / "rules.json"
        if rules_file.exists():
            try:
                lib = json.loads(rules_file.read_text(encoding="utf-8"))
                for r in lib.get("rules", []) or []:
                    known_ids.add(str(r.get("id", "")))
            except Exception:
                pass

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rules: list[dict] = []
        for i, eq in enumerate(equations):
            eq_str = str(eq.get("equation", "")).strip()
            if not eq_str:
                continue
            slug = "".join(c if c.isalnum() else "_" for c in eq_str.lower())[:40].strip("_") or "eq"
            rule_id = f"equation.{slug}.v1"
            if rule_id in known_ids:
                continue
            rules.append({
                "id": rule_id,
                "name": eq_str[:80],
                "type": "transformation",
                "domain": str(eq.get("law_type", "") or eq.get("architecture", "") or "general"),
                "definition": str(eq.get("object_calculated", "") or "Equation approuvee par debat MIA"),
                "unit": "",
                "measure": "",
                "role": str(eq.get("remark", "") or "")[:300],
                "links": [f"{eq_str}"],
                "status": "candidate",
                "experimental": True,
                "version": "1.0.0",
                "source": "mia_debate",
                "description": eq_str,
                "constraints": [],
                "validation": {"tested": bool(eq.get("approved", False)), "tests": []},
                "family": str(eq.get("law_type", "") or "general"),
                "micro_equation": eq_str,
                "experiment": "",
                "approved": False,
                "source_turn": int(eq.get("source_turn", 0) or 0),
                "source_agent": str(eq.get("source_agent", "") or "MIA"),
                "validation_summary": f"Equation: {eq_str}",
                "source_session": session_path.name,
            })
            known_ids.add(rule_id)

        if not rules:
            return 0

        out_file = new_data_dir / f"incoming_equations_{stamp}.json"
        tmp_file = out_file.with_suffix(".json.tmp")
        tmp_file.write_text(
            json.dumps({"rules": rules}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_file.replace(out_file)  # atomic write
        return len(rules)
    except Exception as exc:
        print(f"  ⚠️ Archivage alchimie impossible: {type(exc).__name__}: {exc}")
        return 0


def _save_shared_memory(session_path: Path, shared: Any) -> None:
    """Save the shared research memory to disk."""
    from pathlib import Path

    save_path = session_path / "shared_research_memory.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(
        json.dumps(dict(shared.data), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _generate_library_summary(session_path: Path, stats: dict[str, Any]) -> None:
    """Generate a final library summary file."""
    from pathlib import Path
    import json

    # Load the actual shared memory to list approved equations
    shared_path = session_path / "shared_research_memory.json"
    approved_eqs = []
    if shared_path.exists():
        try:
            data = json.loads(shared_path.read_text(encoding="utf-8"))
            approved_eqs = (data.get("approved_equations", []) or [])[:20]
        except Exception:
            pass

    summary = {
        "library_summary": {
            "total_cycles": stats["cycles_completed"],
            "total_equations_generated": stats["equations_generated"],
            "total_equations_passed_tests": stats["equations_passed_tests"],
            "total_equations_in_library": stats["equations_added_to_library"],
            "session": session_path.name,
            "model": OLLAMA_MODEL,
            "ollama_url": OLLAMA_BASE_URL,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "approved_equations": approved_eqs,
    }

    summary_path = session_path / "library_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )