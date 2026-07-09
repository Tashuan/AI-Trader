"""
Generic LLM Client for the Arena Narrative Engine.

Supports plug-and-play with:
  - Ollama (local, http://localhost:11434)
  - OpenAI-compatible APIs (base URL + API key)
  - Anthropic

Configuration via environment variables:
  ARENA_LLM_PROVIDER   = ollama | openai | anthropic  (default: ollama)
  ARENA_LLM_BASE_URL   = override base URL
  ARENA_LLM_API_KEY    = API key (required for openai/anthropic)
  ARENA_LLM_MODEL      = model name (default: llama3 for ollama, gpt-4o-mini for openai, claude-3-5-sonnet-20241022 for anthropic)
"""

import json
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger("arena.llm")

_DEFAULTS = {
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "llama3",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "model": "claude-3-5-sonnet-20241022",
    },
}


class LLMClient:
    """Generic LLM client that auto-configures from environment variables."""

    def __init__(self) -> None:
        self.provider = os.getenv("ARENA_LLM_PROVIDER", "ollama").lower().strip()
        self.base_url = os.getenv("ARENA_LLM_BASE_URL") or _DEFAULTS.get(self.provider, _DEFAULTS["ollama"])["base_url"]
        self.api_key = os.getenv("ARENA_LLM_API_KEY", "")
        self.model = os.getenv("ARENA_LLM_MODEL") or _DEFAULTS.get(self.provider, _DEFAULTS["ollama"])["model"]

    @property
    def is_configured(self) -> bool:
        if self.provider == "ollama":
            return True
        return bool(self.api_key)

    def generate(self, prompt: str, system: str = "", max_tokens: int = 500, temperature: float = 0.8) -> Optional[str]:
        """Generate text from a prompt. Returns None on failure."""
        if not self.is_configured:
            logger.debug("LLM not configured, skipping generation")
            return None
        try:
            if self.provider == "ollama":
                return self._generate_ollama(prompt, system, max_tokens, temperature)
            elif self.provider == "openai":
                return self._generate_openai(prompt, system, max_tokens, temperature)
            elif self.provider == "anthropic":
                return self._generate_anthropic(prompt, system, max_tokens, temperature)
            else:
                logger.warning(f"Unknown LLM provider: {self.provider}")
                return None
        except Exception as e:
            logger.error(f"LLM generation error ({self.provider}): {e}")
            return None

    def _generate_ollama(self, prompt: str, system: str, max_tokens: int, temperature: float) -> Optional[str]:
        resp = requests.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "system": system,
                "stream": False,
                "options": {"num_predict": max_tokens, "temperature": temperature},
            },
            timeout=30,
        )
        if resp.ok:
            return resp.json().get("response", "").strip()
        logger.error(f"Ollama error: {resp.status_code} {resp.text[:200]}")
        return None

    def _generate_openai(self, prompt: str, system: str, max_tokens: int, temperature: float) -> Optional[str]:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=30,
        )
        if resp.ok:
            choices = resp.json().get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
        logger.error(f"OpenAI error: {resp.status_code} {resp.text[:200]}")
        return None

    def _generate_anthropic(self, prompt: str, system: str, max_tokens: int, temperature: float) -> Optional[str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        resp = requests.post(
            f"{self.base_url}/v1/messages",
            headers=headers,
            json=body,
            timeout=30,
        )
        if resp.ok:
            content = resp.json().get("content", [])
            if content:
                return content[0].get("text", "").strip()
        logger.error(f"Anthropic error: {resp.status_code} {resp.text[:200]}")
        return None


_singleton: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    return _singleton
