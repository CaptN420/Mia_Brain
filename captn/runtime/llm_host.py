"""Resolve the local LLM (Ollama) base URL across host vs container.

Security intent (unchanged): the app only ever talks to the *local* model
server. What "local" means depends on where we run:

  * On the dev host (Windows):        http://localhost:11434
  * Inside the Docker container:       http://host.docker.internal:11434
    (Docker maps host.docker.internal -> the host gateway, so the container
     reaches the Ollama/LM Studio instance running on the Windows host.)

The container case is selected when the env var OLLAMA_HOST is set (the
compose file sets it) OR when we detect we are inside a container.
"""
from __future__ import annotations

import os

DEFAULT_PORT = 11434


def ollama_base_url() -> str:
    """Return the Ollama base URL to use in the current environment."""
    explicit = os.environ.get("OLLAMA_HOST")
    if explicit:
        # Accept http://host:port or bare host:port; normalize to http://.
        if "://" not in explicit:
            explicit = "http://" + explicit
        return explicit.rstrip("/")

    # In-container detection: Docker/OCI sets /.dockerenv; also honor a general
    # IN_CONTAINER flag. Fall back to host.docker.internal in that case.
    in_container = os.path.exists("/.dockerenv") or os.environ.get("IN_CONTAINER") == "1"
    if in_container:
        return f"http://host.docker.internal:{DEFAULT_PORT}"

    return f"http://localhost:{DEFAULT_PORT}"


def lm_studio_base_url() -> str:
    """LM Studio runs on :1234 with the same host-resolution rule."""
    explicit = os.environ.get("LM_STUDIO_HOST")
    if explicit:
        if "://" not in explicit:
            explicit = "http://" + explicit
        return explicit.rstrip("/")
    in_container = os.path.exists("/.dockerenv") or os.environ.get("IN_CONTAINER") == "1"
    if in_container:
        return "http://host.docker.internal:1234"
    return "http://localhost:1234"
