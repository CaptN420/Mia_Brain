    # Architect: Célestin Martin
# Project: Captn - Deterministic Agent Runtime

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class RuntimeConfig:
    base_dir: Path
    session_dir: Path | None = None
    
    # OpenAI-compatible API configuration - OLLAMA (deterministic runtime)
    api_url: str = "http://localhost:11434/api/chat"
    request_timeout: int = 60
    max_api_retries: int = 1
    retry_backoff_seconds: float = 0.7
    api_key: str | None = None  # Ollama needs no API key
    
    # GPU configuration - GPU 1 (3080 12GB) first, then GPU 0 (3070 Ti 8GB)
    gpu_devices: str = "1,0"

    # Model configurations - ALL AGENTS USE qwen2:1.5b (only model installed)
    # Deterministic single-model policy: every agent and validator uses the same
    # installed model so the workflow never fails with "model not found" (HTTP 404).
    model_aurelius: str = "qwen2:1.5b"
    model_basilide: str = "qwen2:1.5b"
    model_chymicus: str = "qwen2:1.5b"
    model_archiviste: str = "qwen2:1.5b"
    model_sentinelle: str = "qwen2:1.5b"
    model_hermes: str = "qwen2:1.5b"
    model_hermetica: str = "qwen2:1.5b"
    model_synthetiseur: str = "qwen2:1.5b"
    model_reviseur: str = "qwen2:1.5b"
    model_variable_validator: str = "qwen2:1.5b"
    model_equation_validator: str = "qwen2:1.5b"
    model_final_validator: str = "qwen2:1.5b"
    
    # Prediction configurations
    num_predict_debate: int = 150
    num_predict_validation: int = 80
    num_predict_archive: int = 200
    num_predict_sentinelle: int = 120
    num_predict_support: int = 100
    num_predict_synth: int = 180
    num_predict_hermes: int = 200

    # Temperature configurations
    temperature_debate: float = 0.7
    temperature_support: float = 0.3
    temperature_hermes: float = 0.5
    
    # History configuration
    max_history_messages: int = 16
    
    # --- Deterministic runtime policy ---
    # Validators/guards NEVER call the LLM - they are pure deterministic gates.
    # Debate agents use the LLM only to propose; validation is always deterministic.
    # LLM is used ONLY as last-resort fallback when deterministic tools give no answer.
    llm_as_fallback: bool = True
    enable_role_guard_fallbacks: bool = True
    # FULL DETERMINISTIC MODE: when False (default), NO agent ever calls the LLM.
    # All agent proposals come from deterministic template generators.
    llm_enabled: bool = False
    
    # Agent model routing function (optional)
    model_for_agent: Callable[[str], str] | None = None
