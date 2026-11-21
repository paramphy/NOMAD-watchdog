"""Helpers for manual NOMAD integration tests.

The server URL is read from config/config.yaml and the PAT from .env.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from nomad_experiment_watcher.config import load_config
from nomad_experiment_watcher.nomad.client import NomadClient


def project_root_from_here() -> Path:
    candidates = [Path.cwd(), *Path.cwd().parents]
    candidates.append(Path(__file__).resolve().parents[3])
    for candidate in candidates:
        if (candidate / "config" / "config.yaml").is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Could not locate config/config.yaml. Run the test from the project."
    )


def create_configured_client():
    root = project_root_from_here()
    config = load_config(root / "config" / "config.yaml")
    settings = config.nomad_settings or {}

    env_file = Path(settings.get("env_file", ".env"))
    if not env_file.is_absolute():
        env_file = root / env_file
    load_dotenv(env_file, override=False)

    token_env_var = settings.get("token_env_var", "NOMAD_PAT")
    token = os.getenv(token_env_var)
    if not token:
        raise RuntimeError(
            f"{token_env_var} is not configured. Create the local .env file."
        )

    client = NomadClient(
        base_url=settings.get("base_url"),
        token=token,
        timeout=float(settings.get("request_timeout_seconds", 30)),
        verify_ssl=bool(settings.get("verify_ssl", True)),
    )
    return config, client
