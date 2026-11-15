"""Resolve one complete model profile and expose a credential-free identity."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


IDENTITY_FIELDS = frozenset({"LLM_API_BASE_URL", "LLM_API_KEY", "LLM_MODEL"})
SOURCE_FIELDS = frozenset({"LLM_CONFIG_FILE", "LLM_CONFIG_SOURCE"})


def resolve_profile(root: Path, explicit_file: Path | None = None) -> tuple[dict[str, str], str, str | None]:
    """Never mix a project's URL/key/model with values inherited from another app.

    Environment-only deployments must opt in. File values are literal: dotenv
    interpolation could otherwise silently import an unrelated process secret.
    """
    selected = explicit_file or os.environ.get("LLM_CONFIG_FILE")
    mode = os.environ.get("LLM_CONFIG_SOURCE", "project")
    if mode not in {"project", "environment"}:
        raise ValueError("LLM_CONFIG_SOURCE must be project or environment")
    if selected is None and mode == "environment":
        return {key: os.environ.get(key, "") for key in IDENTITY_FIELDS}, "environment", None
    path = Path(selected) if selected is not None else root / ".env"
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if selected is not None and not path.is_file():
        raise ValueError("The explicitly selected LLM configuration file does not exist")
    values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    profile = {key: values.get(key) or "" for key in IDENTITY_FIELDS}
    if any("${" in value for value in profile.values()):
        raise ValueError("LLM configuration values must be literal; environment interpolation is disabled")
    return profile, "explicit_file" if selected is not None else "project_file", str(path)


def api_identity(settings) -> dict:
    """No key, userinfo, query, fragment or possibly secret URL path in audit data."""
    base = settings.llm_base_url.rstrip("/")
    try:
        parts = urlsplit(base)
        host = parts.hostname or ""
        if ":" in host:
            host = "[" + host + "]"
        origin = f"{parts.scheme}://{host}" if parts.scheme and host else None
        if origin and parts.port is not None:
            origin += f":{parts.port}"
    except ValueError:
        origin = None
    key_hash = hashlib.sha256(settings.llm_api_key.encode()).hexdigest() if settings.llm_api_key else None
    identity = hashlib.sha256(json.dumps([base, settings.llm_model, key_hash],
                                        separators=(",", ":")).encode()).hexdigest()[:24]
    return {"origin": origin, "model": settings.llm_model,
            "source": settings.llm_config_source, "source_file": settings.llm_config_file,
            "key_present": bool(settings.llm_api_key), "key_fingerprint": key_hash[:12] if key_hash else None,
            "identity": identity, "transport": "environment" if settings.llm_trust_env else "direct"}
