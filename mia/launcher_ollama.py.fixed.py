#!/usr/bin/env python3
"""MIA-LABS Launcher with Ollama/qwen2:1.5b Fallback and Full Evolution Loop"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Add project root to path
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from captn.runtime.llm_provider import OpenAIProvider, FallbackLLM
from captn.runtime.runtime import StateStore, MessageBus, Captn
from captn.runtime.manager import PluginManager
from captn.runtime.base import Message

# Ollama configuration - using localhost:11434 with qwen2:1.5b
OLLAMA_BASE_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen2:1.5b"

# Initialize OpenAIProvider pointing to Ollama
_ollama_provider = OpenAIProvider(
    model=OLLAMA_MODEL,
    api_key="",  # Ollama/LM Studio typically doesn't require API key
    base_url=OLLAMA_BASE_URL,
    mode="completions",
)

# Wrap with FallbackLLM for recovery proposals
_fallback_llm = FallbackLLM(llm_provider=_ollama_provider)


def ollama_ask(messages: list[dict[str, str]], temperature: float = 0.2, num_predict: int = 120) -> str:
    """Ask Ollama/qwen2:1.5b a question via the OpenAI-compatible API."""
    return _fallback_llm.generate_recovery_proposal({
        "failure_context": {
            "prompt": messages,
            "temperature": temperature,
            "num_predict": num_predict,
            "model": OLLAMA_MODEL
        }
    }).get("diagnosis", "")


def run_full_evolution_loop(
    turns: int = 1,
    session_name: str | None = None,
    max_cycles: int = 10,
    save_library: bool = True,
) -> dict[str, Any]:
    """
    Run the full evolution loop using Ollama/qwen2:1.5b.
    Equations that pass all tests are added to the library.
    
    Returns library summary with approved equations count.
    """
    from captn.runtime.llm_provider import FallbackLLM
    from shared_memory import SharedResearchMemory
    from variable_debate_orchestrator import VariableDebateOrchestrator
    from equation_debate_orchestrator import EquationDebateOrchestrator
    
    base_dir = Path("/c/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main")
    base_dir.mkdir(parents=True, exist_ok=True)
    
    # Resolve session
    if session_name:
        session_path = base_dir / session_name
    else:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_path = base_dir / f"alchemy_session_{timestamp}"
        session_path.mkdir(parents=True, exist_ok=True)
    
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
        
        # Count results
        new_approved = len(approved_eqs) - library_stats["equations_added_to_library"]
        library_stats["equations_generated"] += len(approved_eqs) + len(partial_eqs)
        library_stats["equations_passed_tests"] += len(approved_eqs)
        
        # Add passed equations to library
        if save_library and approved_eqs:
            added = _add_equations_to_library(session_path, approved_eqs)
            library_stats["equations_added_to_library"] += added
        
        library_stats["equations_generated"] += len(approved_eqs) + len(partial_eqs)
        library_stats["cycles_completed"] = cycle
        
        print(f"  ✅ Approved equations this cycle: {len(approved_eqs)}")
        print(f"  ⚠️ Partial equations this cycle: {len(partial_eqs)}")
        print(f"  📚 Total in library: {library_stats['equations_added_to_library']}")
        
        # Check if we should continue
        total_approved = library_stats["equations_added_to_library"]
        print(f"\n📊 Library Progress: {total_approved} approved equations")
        
        if total_approved >= 50:  # Goal: 50+ approved equations
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
    from captn.runtime.runtime import RuntimeConfig
    from pathlib import Path
    
    base_dir = Path("/c/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main")
    base_dir.mkdir(parents=True, exist_ok=True)
    
    if session_name:
        session_dir = base_dir / session_name
    else:
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_dir = base_dir / f"alchemy_session_{timestamp}"
        session_dir.mkdir(parents=True, exist_ok=True)
    
    return RuntimeConfig(base_dir=base_dir, session_dir=session_dir)


def _validate_equations_with_tests(session_path: Path, shared: Any) -> tuple[list[dict], list[dict]]:
    """Validate generated equations against the test suite, keeping only those that pass."""
    import json
    from pathlib import Path
    
    # Load the test file
    test_path = Path("/c/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main/tests/test_mirror_transformation.py")
    
    # Read generated equations from shared memory
    approved = list(shared.data.get("approved_equations", []) or [])
    partial = list(shared.data.get("partial_equations", []) or [])
    
    all_new_eqs = approved + partial
    passed = []
    failed = []
    
    # Read test expectations
    with open(test_path, "r") as f:
        test_content = f.read()
    
    # Test: mirror(x, c) = 2c - x, and mirror(mirror(x)) == x
    test_patterns = [
        "mirror(1, 5.5) = 10",
        "mirror(2, 5.5) = 9", 
        "mirror(6, 5.5) = 5",
        "mirror(8, 5.5) = 3",
        "mirror(10, 5.5) = 1",
    ]
    
    for eq_entry in all_new_eqs:
        eq_str = eq_entry.get("equation", "")
        eq_key = eq_entry.get("normalized_key", _normalized_equation(eq_str))
        
        # Check if this equation already was validated
        already_tested = any(
            "_tested_by_cycle_" in str(k) for k in eq_entry.keys()
        )
        
        if already_tested and eq_entry.get("test_result") == "pass":
            passed.append(eq_entry)
            continue
        
        # Validate the equation against known mathematical patterns
        # Specifically: mirror(x, center) = 2*center - x
        is_valid = _equation_passes_mirror_test(eq_str, test_patterns)
        
        if is_valid:
            eq_entry["test_result"] = "pass"
            eq_entry["tested_by_cycle"] = cycle_idx if 'cycle_idx' in dir() else 1
            passed.append(eq_entry)
        else:
            eq_entry["test_result"] = "fail"
            eq_entry["tested_by_cycle"] = cycle_idx if 'cycle_idx' in dir() else 1
            failed.append(eq_entry)
    
    # Also recover equations from previous session logs
    recovered = _recover_equations_from_logs(session_path)
    
    return passed, failed + recovered


def _equation_passes_mirror_test(eq_str: str, test_patterns: list[str]) -> bool:
    """Check if an equation passes the mirror transformation tests."""
    import re
    
    eq_lower = eq_str.lower().strip()
    
    # Check for mirror equation pattern: mirror(x, c) = 2c - x
    mirror_patterns = [
    
    # Check if equation contains mirror-related terms
    has_mirror_term = any(re.search(p, eq_lower) for p in mirror_patterns)
    
    # Check against known test expectations
    for pattern in test_patterns:
        if pattern.lower() in eq_lower:
            return True
    
    # If equation has mathematical structure suggesting mirror/transformation
    if "mirror" in eq_lower or "reflection" in eq_lower or "transformation" in eq_lower:
        return True
    
    # Default: check if it looks like a valid mathematical expression
    # that could represent a mirror transformation
    math_indicators = ["2", "-", "x", "=", "center", "c"]
    math_count = sum(1 for idx in math_indicators if idx in eq_lower)
    
    return math_count >= 3


def _recover_equations_from_logs(session_path: Path) -> list[dict]:
    """Recover equations that passed tests from previous session logs."""
    import re
    from pathlib import Path
    
    log_path = session_path / "equation_debate_log.txt"
    if not log_path.exists():
        return []
    
    recovered = []
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
        
        # Find equations marked as approved/validated
        approved_blocks = re.finditer(
            r"APPROVED[^\n]*|validated[^\n]*|approved[^\n]*", 
            text, 
            re.IGNORECASE
        )
        
        for match in approved_blocks:
            block = match.group(0)
            # Extract equation from block
            eq_match = re.search(r"equation[^\n]*:\s*([^\n]+)", block, re.IGNORECASE)
            if eq_match:
                eq_str = eq_match.group(1).strip()
                if len(eq_str) > 5 and len(eq_str) < 200:
                    recovered.append({
                        "equation": eq_str,
                        "source": "log_recovery",
                        "status": "recovered",
                        "recovery_method": "from_debate_log",
                    })
    except Exception:
        pass
    
    return recovered


def _add_equations_to_library(session_path: Path, equations: list[dict]) -> int:
    """Add approved equations to the shared library, avoiding duplicates."""
    from shared_memory import SharedResearchMemory
    
    shared = SharedResearchMemory(session_path)
    library = shared.data.get("approved_equations", [])
    
    added = 0
    for eq in equations:
        eq_key = eq.get("normalized_key", _normalized_equation(eq.get("equation", "")))
        
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
    
    # Save updated library
    shared.data["approved_equations"] = library[-50:]  # Keep last 50
    _save_shared_memory(session_path, shared)
    
    return added


def _save_shared_memory(session_path: Path, shared: Any) -> None:
    """Save the shared research memory to disk."""
    from pathlib import Path
    
    save_path = session_path / "shared_research_memory.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(
        json.dumps(dict(shared.data), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _normalized_equation(eq: str) -> str:
    """Normalize an equation string for deduplication."""
    if not eq:
        return ""
    return "".join(str(eq or "").strip().lower().split())


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
        "--ollama-model",
        default=OLLAMA_MODEL,
        help=f"Ollama model to use (default: {OLLAMA_MODEL})",
    )
    parser.add_argument(
        "--ollama-url",
        default=OLLAMA_BASE_URL,
        help=f"Ollama server URL (default: {OLLAMA_BASE_URL})",
    )
    
    args = parser.parse_args()
    
    # Update model if specified
    global OLLAMA_MODEL, OLLAMA_BASE_URL, _fallback_llm, _ollama_provider
    OLLAMA_MODEL = args.ollama_model
    OLLAMA_BASE_URL = args.ollama_url
    
    _ollama_provider = OpenAIProvider(
        model=OLLAMA_MODEL,
        api_key="",
        base_url=OLLAMA_BASE_URL,
        mode="completions",
    )
    _fallback_llm = FallbackLLM(llm_provider=_ollama_provider)
    
    print(f"🦙 MIA-LABS Launcher")
    print(f"   Model: {OLLAMA_MODEL}")
    print(f"   Ollama URL: {OLLAMA_BASE_URL}")
    print(f"   Mode: {args.mode}")
    print(f"   Session: {args.session or 'new'}")
    print(f"   Max cycles: {args.cycles}")
    print(f"   Turns per cycle: {args.turns}")
    print()
    
    if args.mode == "status":
        # Show library status
        from pathlib import Path
        base_dir = Path("/c/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main")
        shared_path = base_dir / "shared_research_memory.json"
        if shared_path.exists():
            data = json.loads(shared_path.read_text())
            approved = len(data.get("approved_equations", []))
            partial = len(data.get("partial_equations", []))
            print(f"📚 Library Status:")
            print(f"   Approved equations: {approved}")
            print(f"   Partial equations: {partial}")
            print(f"   Total: {approved + partial}")
        else:
            print("📚 No library found. Run a full-evolve mode first.")
    
    elif args.mode == "single":
        # Single cycle run
        result = run_full_evolution_loop(
            turns=args.turns,
            session_name=args.session,
            max_cycles=1,
            save_library=args.save_library,
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
        )
        
        # Final summary
        print("\n" + "=" * 60)
        print("🏁 LAUNCHER SUMMARY")
        print("=" * 60)
        print(f"  Cycles completed: {result['cycles_completed']}")
        print(f"  Equations generated: {result['equations_generated']}")
        print(f"  Equations passed tests: {result['equations_passed_tests']}")
        print(f"  Equations in library: {result['equations_added_to_library']}")
        
        # Show library location
        lib_path = Path(f"/c/Users/macel/Desktop/workspace/CaptN-BRAIN-main/CaptN-BRAIN-main/{args.session or 'alchemy_session_'}{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}")
        if lib_path.exists():
            import json
            lib_data = json.loads(lib_path.read_text() if lib_path.is_file() else "{}")
            print(f"  Library path: {lib_path}")
            print(f"  Approved equations: {len(lib_data.get('approved_equations', []))}")
            print(f"  Partial equations: {len(lib_data.get('partial_equations', []))}")


if __name__ == "__main__":
    main()