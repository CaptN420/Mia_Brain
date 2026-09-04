#!/usr/bin/env python3
"""CLI commands: thinker, mirror, deterministic, autogen, fixgen (code generation)."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("cli._codegen")

# ── Alchimie manager helper ────────────────────────────────────────
_ALCHIMIE_MANAGER = None


def _load_alchimie_manager():
    global _ALCHIMIE_MANAGER
    if _ALCHIMIE_MANAGER is not None:
        return _ALCHIMIE_MANAGER
    try:
        from alchimie_library_manager import AlchimieLibraryManager  # type: ignore[import-not-found]

        alchimie_dir = Path(__file__).resolve().parent.parent.parent / "alchimie" / "library"
        if alchimie_dir.exists():
            _ALCHIMIE_MANAGER = AlchimieLibraryManager(str(alchimie_dir))
            return _ALCHIMIE_MANAGER
    except Exception as e:
        logger.warning("Alchimie manager not available: %s", e)
    return None


# ── Command: thinker ───────────────────────────────────────────────
def _cmd_thinker(args):
    from captn.runtime.thinker import Thinker  # type: ignore[import-not-found]

    alchimie = _load_alchimie_manager()
    thinker = Thinker(alchimie_manager=alchimie)

    if args.findings == "-":
        worker_results = json.load(sys.stdin)
    else:
        with open(args.findings) as f:
            worker_results = json.load(f)

    if not isinstance(worker_results, list):
        worker_results = [worker_results]

    report = thinker.synthesize(worker_results, alchimie_version=args.alchimie_version)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        logger.info("Report written to %s", args.output)
    else:
        print(json.dumps(report, indent=2))


# ── Command: mirror ────────────────────────────────────────────────
def _cmd_mirror(args):
    from captn.runtime.mirror_agent import MirrorAgent  # type: ignore[import-not-found]

    alchimie = _load_alchimie_manager()
    mirror = MirrorAgent(bus=None, alchimie_manager=alchimie)
    mirror.initialize()

    task_id = args.task_id or "cli-mirror-001"
    output = mirror.process_hypothesis(args.hypothesis, task_id=task_id)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        logger.info("Mirror result written to %s", args.output)
    else:
        print(json.dumps(output, indent=2))


# ── Command: deterministic ─────────────────────────────────────────
def _cmd_deterministic(args):
    from captn.workers.code_generation.deterministic_coder import DeterministicCoder  # type: ignore[import-not-found]

    dc = DeterministicCoder()

    if args.code:
        source = args.code
    elif args.file:
        with open(args.file) as f:
            source = f.read()
    else:
        source = sys.stdin.read()

    modes = args.modes if args.modes else ["docstrings", "annotate", "normalize"]

    logger.info("Transforming with modes: %s", modes)
    code, method = dc.generate(source, modes=modes)

    if code is None:
        logger.error("Transform failed: %s", method)
        sys.exit(1)

    if args.output:
        with open(args.output, "w") as f:
            f.write(code)
        logger.info("Generated code written to %s", args.output)
    else:
        print(f"# Method: {method}\n")
        print(code)

    if args.write_dir:
        result = dc.generate_to_file(source, args.file or "stdin.py", args.write_dir, modes=modes)
        print(json.dumps(result, indent=2))


# ── Command: autogen ───────────────────────────────────────────────
def _cmd_autogen(args):
    from captn.workers.code_generation.autogen import run_autogen_loop  # type: ignore[import-not-found]

    result = run_autogen_loop(
        dataset_jsonl=args.dataset,
        ollama_url=args.ollama_url,
        model=args.model,
        iterations=args.iterations,
        sample_per_iter=args.sample_per_iter,
        out_dir=args.out_dir,
        feed_corpus=args.feed_corpus,
        corpus_dir=args.corpus_dir,
        seed=args.seed,
        llm_as_fallback=not args.no_llm_fallback,
    )

    print(json.dumps(result.to_dict(), indent=2))

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        logger.info("Autogen result written to %s", args.output)


# ── Command: fixgen ────────────────────────────────────────────────
def _cmd_fixgen(args):
    from captn.runtime.base import Message  # type: ignore[import-not-found]
    from captn.workers.code_generation.fix_generator import FixGenerator  # type: ignore[import-not-found]

    fixgen = FixGenerator(bus=None)
    fixgen.initialize()

    message = Message(
        sender="cli",
        destination="fix_generator",
        type="task",
        payload={
            "task_id": args.task_id,
            "pgm": args.pgm,
        },
    )

    result = fixgen.execute(message)
    if result:
        print(f"✅ Fix généré pour task_id={args.task_id}")
        if hasattr(result, "to_dict"):
            print(json.dumps(result.to_dict(), indent=2))
        elif isinstance(result, dict):
            print(json.dumps(result, indent=2))
        else:
            print(result)
    else:
        logger.info("FixGenerator executed (no result payload).")


# ── Registration ───────────────────────────────────────────────────
def register_cli(subparsers) -> None:
    # thinker
    p = subparsers.add_parser("thinker", help="Synthesize worker findings")
    p.add_argument("--findings", "-f", required=True,
                   help="JSON file with worker results (or '-' for stdin)")
    p.add_argument("--alchimie-version", help="Alchimie version to include in report")
    p.add_argument("--output", "-o", help="Output file (default: stdout)")
    p.set_defaults(func=_cmd_thinker)

    # mirror
    p = subparsers.add_parser("mirror", help="Mirror reasoning (MirrorAgent)")
    p.add_argument("--hypothesis", "-H", required=True, help="Hypothesis to mirror")
    p.add_argument("--task-id", help="Task ID (default: auto)")
    p.add_argument("--output", "-o", help="Output file (default: stdout)")
    p.set_defaults(func=_cmd_mirror)

    # deterministic
    p = subparsers.add_parser("deterministic", help="Deterministic AST transforms")
    p.add_argument("--code", "-c", help="Source code string")
    p.add_argument("--file", "-f", help="Source file path")
    p.add_argument("--modes", "-m", nargs="+",
                   choices=["docstrings", "annotate", "normalize"],
                   default=["docstrings", "annotate", "normalize"],
                   help="Transform modes")
    p.add_argument("--output", "-o", help="Output file for generated code")
    p.add_argument("--write-dir", help="Also write via generate_to_file to this directory")
    p.set_defaults(func=_cmd_deterministic)

    # autogen
    p = subparsers.add_parser("autogen", help="Run autogen loop (LLM fallback)")
    p.add_argument("--dataset", "-d", default="mirror/dataset.jsonl", help="Dataset JSONL path")
    p.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL")
    p.add_argument("--model", default="qwen2:1.5b", help="Ollama model")
    p.add_argument("--iterations", "-i", type=int, default=3, help="Number of iterations")
    p.add_argument("--sample-per-iter", "-s", type=int, default=4, help="Samples per iteration")
    p.add_argument("--out-dir", default="generated", help="Output directory")
    p.add_argument("--feed-corpus", action="store_true", help="Feed validated code to corpus")
    p.add_argument("--corpus-dir", default="Code_base", help="Corpus directory")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument("--no-llm-fallback", action="store_true", help="Disable LLM fallback")
    p.add_argument("--output", "-o", help="Output file for result summary")
    p.set_defaults(func=_cmd_autogen)

    # fixgen
    p = subparsers.add_parser("fixgen", help="Generate fix template")
    p.add_argument("--task-id", required=True, help="Task ID")
    p.add_argument("--pgm", required=True, help="Problem graph model (description)")
    p.set_defaults(func=_cmd_fixgen)