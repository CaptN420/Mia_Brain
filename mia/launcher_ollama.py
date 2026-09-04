#!/usr/bin/env python3
"""MIA-LABS Launcher with Ollama/qwen2:1.5b Fallback and Full Evolution Loop"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import requests

from mia.equation_library import (
    _normalized_equation,
    _validate_equations_with_tests,
    _add_equations_to_library,
    _archive_equations_to_alchimie,
    _save_shared_memory,
    _generate_library_summary,
)

# Ollama configuration - using localhost:11434 with qwen2:1.5b
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2:1.5b"


def ollama_ask(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    num_predict: int = 120,
    raw: bool = False,
) -> str:
    """Ask Ollama/qwen2:1.5b directly via REST API (no API key needed)."""
    import requests
    
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            return f"[OLLAMA_ERROR] HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        usage = data.get("usage") or {}
        try:
            from captn.workers.code_generation.autogen import TOKEN_USAGE
            TOKEN_USAGE["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            TOKEN_USAGE["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            TOKEN_USAGE["total_tokens"] += int(usage.get("total_tokens") or 0)
            TOKEN_USAGE["calls"] += 1
        except Exception:
            pass
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return content.strip()
    except Exception as e:
        return f"[OLLAMA_ERROR] {str(e)[:200]}"

def ollama_as_fallback(failure_context: dict) -> dict:
    """
    Wraps Ollama as a Captn-compatible FallbackLLM.
    Returns a recovery proposal dict that can be published to the message bus.
    """
    messages = failure_context.get("prompt", [])
    if not messages:
        messages = [
            {"role": "system", "content": "You are an emergency recovery agent."},
            {"role": "user", "content": json.dumps(failure_context, ensure_ascii=False)},
        ]
    response = ollama_ask(messages, temperature=0.3, num_predict=200)
    return {
        "type": "recovery_proposal",
        "diagnosis": response[:500],
        "new_hypothesis": response[:300],
        "proposed_strategy": "ollama_fallback_recovery",
        "expected_effect": "Generate recovery proposal from Ollama",
        "confidence": 0.5,
        "requires_validation": True,
    }


def run_full_evolution_loop(
    turns: int = 1,
    session_name: str | None = None,
    max_cycles: int = 10,
    save_library: bool = True,
    safety: bool = False,
    safety_strict: bool = False,
) -> dict[str, Any]:
    """
    Run the full evolution loop using Ollama/qwen2:1.5b.
    Equations that pass all tests are added to the library.
    
    Returns library summary with approved equations count.
    """
    # Ensure mia/ is on sys.path for MIA-LABS imports
    mia_dir = Path(__file__).parent
    if str(mia_dir) not in sys.path:
        sys.path.insert(0, str(mia_dir))
    
    from shared_memory import SharedResearchMemory
    from variable_debate_orchestrator import VariableDebateOrchestrator
    from equation_debate_orchestrator import EquationDebateOrchestrator
    
    # Use workspace_security for path validation
    from workspace_security import get_workspace_dir, ensure_session_dir
    
    base_dir = get_workspace_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    
    # Resolve session
    if session_name:
        session_path = ensure_session_dir(base_dir / session_name)
    else:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_path = ensure_session_dir(base_dir / f"alchemy_session_{timestamp}")
    
    # Initialize shared memory
    shared = SharedResearchMemory(session_path)
    
    library_stats = {
        "total_cycles": 0,
        "equations_generated": 0,
        "equations_passed_tests": 0,
        "equations_added_to_library": 0,
        "cycles_completed": 0,
    }
    
    print(f"🚀 Starting Full Evolution Loop")
    print(f"   Session: {session_path.name}")
    print(f"   Model: {OLLAMA_MODEL} via Ollama")
    print(f"   Max cycles: {max_cycles}")
    print(f"   Turns per cycle: {turns}")
    print("=" * 60)
    
    for cycle in range(1, max_cycles + 1):
        print(f"\n📡 CYCLE {cycle}/{max_cycles}")
        print("-" * 40)
        
        # PHASE 1: Variable Discovery
        print(f"[PHASE 1] Variable Discovery (turns={turns})")
        try:
            var_orch = VariableDebateOrchestrator(_cfg(session_name=session_path.name))
            var_orch.run(turns=turns)
            var_result = "completed"
        except Exception as e:
            var_result = f"failed: {str(e)[:80]}"
        
        # PHASE 2: Equation Synthesis
        print(f"[PHASE 2] Equation Synthesis (turns={turns})")
        try:
            eq_orch = EquationDebateOrchestrator(_cfg(session_name=session_path.name))
            eq_orch.run(turns=turns)
            eq_result = "completed"
        except Exception as e:
            eq_result = f"failed: {str(e)[:80]}"
        
        # PHASE 3: Validate equations against tests
        print(f"[PHASE 3] Validate equations against tests")
        approved_eqs, partial_eqs = _validate_equations_with_tests(session_path, shared)
        
        # PHASE 3b: DIVERSIFICATION - generate structurally distinct variants
        # from validated equations. Variants re-enter the same validation +
        # safety flow as normal equations (deterministic worker, no LLM).
        # The NoRepetitionWorker registry feeds the diversifier so exhausted
        # structures are never regenerated.
        try:
            from diversifier_worker import DiversifierWorker
            from no_repetition_worker import NoRepetitionWorker
            no_repeat = NoRepetitionWorker(session_path)
            worker = DiversifierWorker(approved_variables=shared.get_approved_variables())
            known_sigs = set()
            for e in approved_eqs + partial_eqs:
                known_sigs.add(worker._signature(str(e.get("equation", "") or "")))
            known_sigs |= no_repeat.known_signatures()
            variants = worker.diversify(
                approved_eqs,
                existing_signatures=known_sigs,
                max_per_parent=2,
                max_total=8,
            )
            if variants:
                print(f"[PHASE 3b] Diversifier: {len(variants)} variantes structurelles générées")
                for v in variants[:4]:
                    print(f"   ↳ {v['equation'][:60]}  ({v['mutation']})")
                # Variants must pass the deterministic equation validation too
                valid_variants = []
                for v in variants:
                    eval_res = eq_orch._evaluate_equation_structure(
                        f"Objet calculé : débit net\nÉquation : {v['equation'].split('=',1)[1].strip()}\nLiens :\n- {v.get('mutation','variante')}\n"
                    )
                    if eval_res.get("is_valid"):
                        valid_variants.append(v)
                approved_eqs = approved_eqs + valid_variants
                library_stats["variants_generated"] = library_stats.get("variants_generated", 0) + len(variants)
        except Exception as exc:
            print(f"[PHASE 3b] Diversifier indisponible: {type(exc).__name__}: {exc}")
        
        # PHASE 3c: NO-REPETITION GATE - session-wide anti-duplication.
        # Rejects equations whose structure or symbol-set was already seen
        # too many times across ALL cycles (persistent registry).
        try:
            from no_repetition_worker import NoRepetitionWorker
            if "no_repeat" not in dir():
                no_repeat = NoRepetitionWorker(session_path)
            nr_result = no_repeat.filter_batch(approved_eqs)
            kept = nr_result["accepted"]
            dropped = nr_result["rejected"]
            library_stats.setdefault("repetition_blocked", 0)
            library_stats["repetition_blocked"] += len(dropped)
            for d in dropped[:4]:
                print(f"  🔁 NoRepeat [rejet] {d.get('equation', '')[:50]} ({d['no_repeat_check']['reason'][:70]})")
            approved_eqs = kept
            metrics = no_repeat.diversity_metrics()
            library_stats["diversity_ratio"] = metrics["diversity_ratio"]
            print(f"[PHASE 3c] NoRepeat: {len(kept)} gardées, {len(dropped)} rejetées "
                  f"(diversité: {metrics['diversity_ratio']}, structures distinctes: {metrics['distinct_structures']})")
        except Exception as exc:
            print(f"[PHASE 3c] NoRepeat indisponible: {type(exc).__name__}: {exc}")
        
        # Count results
        new_approved = len(approved_eqs) - library_stats["equations_added_to_library"]
        library_stats["equations_generated"] += len(approved_eqs) + len(partial_eqs)
        library_stats["equations_passed_tests"] += len(approved_eqs)
        
        # PHASE 4: SAFETY GATE (optional) - test before archiving (once per cycle).
        # safe -> archive | dangerous/quarantine -> NEVER archived
        if save_library and approved_eqs:
            if safety:
                from library_safety_gate import SafetyGate
                gate = SafetyGate(strictness="strict" if safety_strict else "normal")
                buckets = gate.test_batch(approved_eqs)
                library_stats.setdefault("safety_blocked", 0)
                library_stats["safety_blocked"] += len(buckets["dangerous"]) + len(buckets["quarantine"])
                for blocked in buckets["dangerous"] + buckets["quarantine"]:
                    st = blocked.get("safety_test", {})
                    print(f"  🚫 SafetyGate [{st.get('verdict')}] {blocked.get('equation', '')[:50]} "
                          f"({'; '.join(st.get('reasons', []))[:80]})")
                archivable = buckets["safe"]
            else:
                print("  ⏭️ SafetyGate désactivé (--safety non fourni): archivage direct")
                archivable = approved_eqs
            if archivable:
                added = _add_equations_to_library(session_path, archivable)
                library_stats["equations_added_to_library"] += added
                archived = _archive_equations_to_alchimie(session_path, archivable)
                if archived:
                    library_stats["equations_archived_to_alchimie"] = (
                        library_stats.get("equations_archived_to_alchimie", 0) + archived
                    )
                    print(f"  📜 {archived} équation(s) déposée(s) dans alchimie/new_data/")
        
        library_stats["equations_generated"] += len(approved_eqs) + len(partial_eqs)
        library_stats["cycles_completed"] = cycle
        
        print(f"  ✅ Approved equations this cycle: {len(approved_eqs)}")
        print(f"  ⚠️ Partial equations this cycle: {len(partial_eqs)}")
        print(f"  📚 Total in library: {library_stats['equations_added_to_library']}")
        
        # Check if we should continue
        total_approved = library_stats["equations_added_to_library"]
        print(f"\n📊 Library Progress: {total_approved} approved equations")
        
        # Goal: 50+ approved equations
        if total_approved >= 50:
            print(f"🎯 Library goal reached! {total_approved} approved equations.")
            break
        
        # Small pause between cycles
        if cycle < max_cycles:
            print(f"⏳ Pausing before next cycle...")
            time.sleep(2)
    
    # Generate final library summary
    _generate_library_summary(session_path, library_stats)
    
    print("\n" + "=" * 60)
    print(f"✨ FULL EVOLUTION LOOP COMPLETE")
    print(f"   Cycles completed: {library_stats['cycles_completed']}")
    print(f"   Equations generated: {library_stats['equations_generated']}")
    print(f"   Equations passed tests: {library_stats['equations_passed_tests']}")
    print(f"   Equations in library: {library_stats['equations_added_to_library']}")
    print(f"   Session: {session_path.name}")
    print("=" * 60)
    
    return library_stats


def _cfg(
    *,
    session_name: str | None = None,
    **kwargs: Any,
) -> Any:
    """Create RuntimeConfig for the session."""
    from pathlib import Path
    
    # RuntimeConfig is in mia/config.py
    mia_dir = Path(__file__).parent
    if str(mia_dir) not in sys.path:
        sys.path.insert(0, str(mia_dir))
    from config import RuntimeConfig
    
    # Use workspace_security's get_workspace_dir() as base
    from workspace_security import get_workspace_dir, ensure_session_dir
    
    base_dir = get_workspace_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    
    if session_name:
        session_dir = ensure_session_dir(base_dir / session_name)
    else:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = ensure_session_dir(base_dir / f"alchemy_session_{timestamp}")
    
    cfg = RuntimeConfig(base_dir=base_dir, session_dir=session_dir)
    # Override api_url and api_key for Ollama
    cfg.api_url = "http://localhost:11434/api/chat"
    cfg.api_key = None
    # Override ALL model names to qwen2:1.5b (only model installed in Ollama)
    for attr_name in dir(cfg):
        if attr_name.startswith("model_") and isinstance(getattr(cfg, attr_name), str):
            setattr(cfg, attr_name, "qwen2:1.5b")
    return cfg


def main():
    """Main entry point for the MIA-LABS launcher with Ollama fallback."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="MIA-LABS Launcher with Ollama/qwen2:1.5b Fallback"
    )
    parser.add_argument(
        "--mode",
        choices=["full-evolve", "loop", "single", "status"],
        default="full-evolve",
        help="Operation mode",
    )
    parser.add_argument(
        "--session",
        help="Session name (creates new if not provided)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=10,
        help="Maximum evolution cycles (default: 10)",
    )
    parser.add_argument(
        "--turns",
        type=int,
        default=1,
        help="Turns per cycle (default: 1)",
    )
    parser.add_argument(
        "--save-library",
        action="store_true",
        default=True,
        help="Save approved equations to library (default: True)",
    )
    parser.add_argument(
        "--safety",
        action="store_true",
        default=False,
        help="Enable the pre-archive safety gate (blocks dangerous/quarantine equations)",
    )
    parser.add_argument(
        "--safety-strict",
        action="store_true",
        default=False,
        help="Strict safety mode: only whitelisted physics domains pass (implies --safety)",
    )
    parser.add_argument(
        "--ollama-model",
        default="qwen2:1.5b",
        help="Ollama model to use (default: qwen2:1.5b)",
    )
    parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="Ollama server URL (default: http://localhost:11434)",
    )
    
    args = parser.parse_args()
    
    # Update module-level globals
    global OLLAMA_MODEL, OLLAMA_BASE_URL
    OLLAMA_MODEL = args.ollama_model
    # Strip /v1/chat/completions suffix if user passed the full URL
    raw_url = args.ollama_url
    for suffix in ["/v1/chat/completions", "/v1", "/api"]:
        if raw_url.endswith(suffix):
            raw_url = raw_url[: -len(suffix)]
            break
    OLLAMA_BASE_URL = raw_url
    
    print(f"🦙 MIA-LABS Launcher")
    print(f"   Model: {OLLAMA_MODEL}")
    print(f"   Ollama URL: {OLLAMA_BASE_URL}")
    print(f"   Mode: {args.mode}")
    print(f"   Session: {args.session or 'new'}")
    print(f"   Max cycles: {args.cycles}")
    print(f"   Turns per cycle: {args.turns}")
    print()
    
    if args.mode == "status":
        # Show library status (reads the session's library_summary.json)
        from workspace_security import get_workspace_dir
        session_name = args.session or _latest_session_dir()
        summary_path = get_workspace_dir() / session_name / "library_summary.json"
        if summary_path.exists():
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            summary = data.get("library_summary", {})
            print(f"📚 Library Status ({session_name}):")
            print(f"   Approved equations in library: {summary.get('total_equations_in_library', '?')}")
            print(f"   Equations passed tests: {summary.get('total_equations_passed_tests', '?')}")
            print(f"   Equations generated: {summary.get('total_equations_generated', '?')}")
            print(f"   Cycles: {summary.get('total_cycles', '?')}")
        else:
            print("📚 No library found. Run a full-evolve mode first.")
    
    elif args.mode == "single":
        # Single cycle run
        result = run_full_evolution_loop(
            turns=args.turns,
            session_name=args.session,
            max_cycles=1,
            save_library=args.save_library,
            safety=args.safety or args.safety_strict,
            safety_strict=args.safety_strict,
        )
        print(f"\n📊 Single cycle result: {result['equations_added_to_library']} equations in library")
    
    elif args.mode == "loop":
        # Continuous loop until interrupted
        print(f"🔄 Starting continuous evolution loop (Ctrl+C to stop)")
        try:
            run_full_evolution_loop(
                turns=args.turns,
                session_name=args.session,
                max_cycles=args.cycles,
                save_library=args.save_library,
                safety=args.safety or args.safety_strict,
                safety_strict=args.safety_strict,
            )
        except KeyboardInterrupt:
            print("\n🛑 Loop stopped by user")
    
    else:  # full-evolve
        # Full evolution loop with library building
        result = run_full_evolution_loop(
            turns=args.turns,
            session_name=args.session,
            max_cycles=args.cycles,
            save_library=args.save_library,
            safety=args.safety or args.safety_strict,
            safety_strict=args.safety_strict,
        )
        
        # Final summary
        print("\n" + "=" * 60)
        print("🏁 LAUNCHER SUMMARY")
        print("=" * 60)
        print(f"  Cycles completed: {result['cycles_completed']}")
        print(f"  Equations generated: {result['equations_generated']}")
        print(f"  Equations passed tests: {result['equations_passed_tests']}")
        print(f"  Equations in library: {result['equations_added_to_library']}")
        if result.get("safety_blocked"):
            print(f"  🚫 Blocked by safety gate: {result['safety_blocked']}")
        
        # Show library location (session library_summary.json lives in the session dir)
        import json as _json
        from workspace_security import get_workspace_dir
        session_name = args.session or _latest_session_dir()
        lib_data_path = get_workspace_dir() / session_name / "library_summary.json"
        if lib_data_path.exists():
            lib_data = _json.loads(lib_data_path.read_text(encoding="utf-8"))
            summary = lib_data.get("library_summary", {})
            print(f"  Library path: {lib_data_path}")
            approved = len(lib_data.get("approved_equations", []))
            print(f"  Approved equations in summary: {approved}")
            print(f"  Total cycles: {summary.get('total_cycles', '?')}")


def _latest_session_dir() -> str:
    """Find the most recent alchemy_session_* directory in the workspace."""
    from workspace_security import get_workspace_dir
    base = get_workspace_dir()
    sessions = sorted(
        (d for d in base.iterdir() if d.is_dir() and d.name.startswith("alchemy_session_")),
        key=lambda d: d.name,
    )
    return sessions[-1].name if sessions else ""


if __name__ == "__main__":
    main()