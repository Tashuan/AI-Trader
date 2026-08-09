"""
schwab_auth.py — Schwab API OAuth 2.0 token manager.

Handles the refresh-token flow: exchanges a long-lived refresh token for
short-lived access tokens (30-min TTL), auto-refreshes before expiry, and
persists tokens to disk so the bot survives restarts without re-auth.

Token lifecycle:
  - access_token: 30-minute TTL, auto-refreshed
  - refresh_token: 7-day TTL (Trader API), must be renewed via OAuth flow
  - Tokens persisted to ~/.config/devin/schwab_tokens.json
"""

import json
import os
import time
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

logger = logging.getLogger("SchwabAuth")

# Schwab OAuth endpoints
_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"

# Token persistence path
_TOKEN_DIR = Path.home() / ".config" / "devin"
_TOKEN_FILE = _TOKEN_DIR / "schwab_tokens.json"

# Refresh when < this many seconds remain on access token
_REFRESH_BUFFER = 60


class SchwabOAuth:
    """OAuth 2.0 token manager with auto-refresh and disk persistence."""

    def __init__(self, client_id: str, client_secret: str,
                 refresh_token: Optional[str] = None,
                 token_file: Optional[Path] = None):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_file = token_file or _TOKEN_FILE
        self._access_token: Optional[str] = None
        self._access_expiry: float = 0.0
        self._refresh_token = refresh_token

        # Try loading persisted tokens
        self._load_persisted()

        # Fall back to env var if no persisted refresh token
        if not self._refresh_token:
            self._refresh_token = os.getenv("SCHWAB_REFRESH_TOKEN")

        if not self._refresh_token:
            logger.warning(
                "No refresh token found. Run schwab_oauth_flow.py first, "
                "or set SCHWAB_REFRESH_TOKEN env var."
            )

    # ── Public API ────────────────────────────────────────────

    def get_access_token(self) -> Optional[str]:
        """Return a valid access token, refreshing if necessary."""
        if self._access_token and time.time() < self._access_expiry - _REFRESH_BUFFER:
            return self._access_token
        return self._refresh()

    @property
    def is_configured(self) -> bool:
        """True if we have a refresh token to work with."""
        return bool(self._refresh_token)

    # ── Token Refresh ─────────────────────────────────────────

    def _refresh(self) -> Optional[str]:
        """Exchange refresh token for a new access token."""
        if not self._refresh_token:
            logger.error("Cannot refresh — no refresh token configured")
            return None

        body = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }).encode()

        req = urllib.request.Request(
            _TOKEN_URL, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            logger.error(f"Token refresh failed: HTTP {e.code} — {e.read()[:200]}")
            return None
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            return None

        self._access_token = data.get("access_token")
        expires_in = data.get("expires_in", 1800)  # Default 30 min
        self._access_expiry = time.time() + expires_in

        # Some responses include a new refresh token
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            logger.info("Refresh token updated by server")

        self._persist()
        logger.info(f"Access token refreshed (expires in {expires_in}s)")
        return self._access_token

    # ── Persistence ───────────────────────────────────────────

    def _persist(self) -> None:
        """Save tokens to disk for restart survival."""
        try:
            self._token_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "access_token": self._access_token,
                "access_expiry": self._access_expiry,
                "refresh_token": self._refresh_token,
            }
            self._token_file.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            logger.warning(f"Could not persist tokens: {e}")

    def _load_persisted(self) -> None:
        """Load tokens from disk if available."""
        try:
            if self._token_file.exists():
                data = json.loads(self._token_file.read_text())
                self._access_token = data.get("access_token")
                self._access_expiry = data.get("access_expiry", 0.0)
                if not self._refresh_token:
                    self._refresh_token = data.get("refresh_token")
                logger.debug("Loaded persisted Schwab tokens")
        except Exception as e:
            logger.warning(f"Could not load persisted tokens: {e}")


def from_env() -> Optional[SchwabOAuth]:
    """Build a SchwabOAuth from environment variables. Returns None if not configured."""
    client_id = os.getenv("SCHWAB_CLIENT_ID")
    client_secret = os.getenv("SCHWAB_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return SchwabOAuth(client_id, client_secret)
