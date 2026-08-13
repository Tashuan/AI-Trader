"""Shared helpers for standalone, configuration-driven strategy experiments."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return a recursive merge without mutating either input."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_json_config(path: str | Path, defaults: dict[str, Any]) -> dict[str, Any]:
    """Load a JSON strategy config and merge it over canonical defaults."""
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as handle:
        override = json.load(handle)
    if not isinstance(override, dict):
        raise ValueError(f"Strategy config must be an object: {config_path}")
    return deep_merge(defaults, override)


def require_range(value: Any, name: str, minimum: float | None = None,
                  maximum: float | None = None) -> float:
    """Validate and return a numeric config value."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if minimum is not None and number < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return number
