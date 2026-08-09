"""Deterministic, redacted effective-configuration export to local JSON files.

Stores one JSON file per agent/runner under config/agents/<sanitized_key>.json,
containing the effective configuration actually used by live and backtest
execution — not a raw database dump. Writes are atomic (temp file + fsync +
replace). A failed backup must not corrupt a successful database write.

Excludes: database internals, trade history, positions, session state,
passwords, bearer tokens, API keys, and other credential-like fields.
Includes defensive redaction for secret-like keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "config", "agents",
)

_SECRET_PATTERNS = re.compile(
    r"password|token|secret|api_key|apikey|credential|auth",
    re.IGNORECASE,
)


def _sanitize_agent_key(name: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_-]", "", name.replace(" ", "_")).lower()
    return key or "unknown_agent"


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if _SECRET_PATTERNS.search(k) else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(item) for item in obj]
    return obj


def _content_hash(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _build_export_payload(
    agent_name: str,
    agent_id: int | None,
    effective_params: dict[str, Any],
    shared_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = dict(effective_params)
    meta = params.pop("_meta", {})

    payload = {
        "agent_name": agent_name,
        "agent_id": agent_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "schema_name": meta.get("schema_name", ""),
        "display_name": meta.get("display_name", ""),
        "parity_status": meta.get("parity_status", "unknown"),
        "effective_strategy_params": _redact(params),
        "shared_config": _redact(shared_config or {}),
    }
    hash_payload = {k: v for k, v in payload.items() if k != "exported_at"}
    payload["content_hash"] = _content_hash(hash_payload)
    return payload


def _atomic_write(filepath: str, data: dict) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    raw = json.dumps(data, sort_keys=True, indent=2, default=str) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(filepath), suffix=".tmp", prefix=os.path.basename(filepath)
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def backup_agent_config(
    agent_name: str,
    agent_id: int | None = None,
    effective_params: dict[str, Any] | None = None,
    shared_config: dict[str, Any] | None = None,
) -> str | None:
    if effective_params is None:
        logger.warning("No effective_params provided for %s, skipping backup", agent_name)
        return None

    try:
        payload = _build_export_payload(agent_name, agent_id, effective_params, shared_config)
        key = _sanitize_agent_key(agent_name)
        filepath = os.path.join(_CONFIG_DIR, f"{key}.json")
        _atomic_write(filepath, payload)
        logger.info("Config backup written for %s → %s (hash=%s)", agent_name, filepath, payload["content_hash"])
        return payload["content_hash"]
    except Exception as exc:
        logger.error("Config backup failed for %s: %s", agent_name, exc)
        return None


def reconcile_agent_config(
    agent_name: str,
    agent_id: int | None,
    effective_params: dict[str, Any],
    shared_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare DB effective config with local JSON backup. Returns health status."""
    key = _sanitize_agent_key(agent_name)
    filepath = os.path.join(_CONFIG_DIR, f"{key}.json")
    db_payload = _build_export_payload(agent_name, agent_id, effective_params, shared_config)
    db_hash = db_payload["content_hash"]

    if not os.path.exists(filepath):
        return {"agent": agent_name, "status": "missing", "db_hash": db_hash, "file_hash": None}

    try:
        with open(filepath, "r") as f:
            file_data = json.load(f)
        file_hash = file_data.get("content_hash", "")
        if file_hash != db_hash:
            return {"agent": agent_name, "status": "stale", "db_hash": db_hash, "file_hash": file_hash}
        return {"agent": agent_name, "status": "ok", "db_hash": db_hash, "file_hash": file_hash}
    except (json.JSONDecodeError, KeyError) as exc:
        return {"agent": agent_name, "status": "malformed", "db_hash": db_hash, "file_hash": None, "error": str(exc)}


def restore_agent_config(
    agent_name: str,
    filepath: str | None = None,
) -> dict[str, Any] | None:
    """Load and validate a local JSON backup file for restore to DB.

    Returns the effective_strategy_params dict if valid, or None if the file
    is missing/malformed. Does NOT write to the database — the caller must
    use the existing validated PATCH endpoint after explicit user confirmation.
    """
    key = _sanitize_agent_key(agent_name)
    path = filepath or os.path.join(_CONFIG_DIR, f"{key}.json")
    if not os.path.exists(path):
        logger.warning("Restore: file not found for %s at %s", agent_name, path)
        return None

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        logger.error("Restore: malformed JSON for %s: %s", agent_name, exc)
        return None

    params = data.get("effective_strategy_params")
    if not isinstance(params, dict):
        logger.error("Restore: no effective_strategy_params in file for %s", agent_name)
        return None

    for key_name in params:
        if _SECRET_PATTERNS.search(key_name):
            logger.error("Restore: secret-like key '%s' found in backup, rejecting", key_name)
            return None

    return params


def list_backups() -> list[dict[str, Any]]:
    """List all config backup files with their metadata."""
    results = []
    if not os.path.exists(_CONFIG_DIR):
        return results
    for fname in sorted(os.listdir(_CONFIG_DIR)):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_CONFIG_DIR, fname)
        try:
            with open(path, "r") as f:
                data = json.load(f)
            results.append({
                "filename": fname,
                "agent_name": data.get("agent_name", "?"),
                "schema_name": data.get("schema_name", "?"),
                "content_hash": data.get("content_hash", "?"),
                "exported_at": data.get("exported_at", "?"),
            })
        except (json.JSONDecodeError, KeyError):
            results.append({"filename": fname, "agent_name": "?", "status": "malformed"})
    return results
