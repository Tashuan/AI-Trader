#!/usr/bin/env python3
"""
schwab_oauth_flow.py — One-time OAuth authorization for Schwab API.

This script performs the initial OAuth 2.0 authorization code flow to obtain
a refresh token. Once you have a refresh token, SchwabProvider handles the
ongoing access token refresh automatically.

Usage:
  1. Set env vars: SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET
  2. Run: python schwab_oauth_flow.py
  3. Open the printed URL in your browser, log in to Schwab
  4. Schwab redirects back to https://127.0.0.1:8182 — we capture the code
  5. The refresh token is saved to ~/.config/devin/schwab_tokens.json

Prerequisites:
  - Register at developer.schwab.com
  - Create an app with redirect URI: https://127.0.0.1:8182
  - Schwab requires HTTPS for callback URLs; this script generates a
    self-signed certificate at runtime for the local listener.
"""

import json
import os
import ssl
import sys
import tempfile
import subprocess
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

from schwab_auth import _TOKEN_URL, _TOKEN_FILE

REDIRECT_URI = "https://127.0.0.1:8182"
AUTH_BASE = "https://api.schwabapi.com/v1/oauth/authorize"
LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8182


def _generate_self_signed_cert(tmp_dir: Path) -> tuple[Path, Path]:
    """Generate a self-signed cert + key using openssl.

    Returns (cert_path, key_path). Falls back to Python's ssl module
    if openssl is not available (though openssl is standard on macOS/Linux).
    """
    cert_path = tmp_dir / "schwab_callback.crt"
    key_path = tmp_dir / "schwab_callback.key"

    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_path),
        "-out", str(cert_path),
        "-days", "1", "-nodes",
        "-subj", "/CN=127.0.0.1",
        "-addext", "subjectAltName=IP:127.0.0.1",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    except FileNotFoundError:
        print("ERROR: openssl not found. Install it via Homebrew: brew install openssl")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to generate self-signed cert: {e.stderr.decode()}")
        sys.exit(1)

    return cert_path, key_path


def build_auth_url(client_id: str) -> str:
    """Build the authorization URL for the browser."""
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
    }
    return f"{AUTH_BASE}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    """Exchange authorization code for refresh + access tokens."""
    body = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()

    req = urllib.request.Request(_TOKEN_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
    })

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback."""

    captured_code = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            CallbackHandler.captured_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization successful!</h1><p>You can close this tab.</p>")
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h1>Authorization failed: {error}</h1>".encode())

    def log_message(self, *args):
        pass  # Suppress default logging


def main():
    client_id = os.getenv("SCHWAB_CLIENT_ID")
    client_secret = os.getenv("SCHWAB_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("ERROR: Set SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET env vars first.")
        print("  export SCHWAB_CLIENT_ID='your_client_id'")
        print("  export SCHWAB_CLIENT_SECRET='your_client_secret'")
        sys.exit(1)

    auth_url = build_auth_url(client_id)
    print(f"\n{'='*60}")
    print("  Schwab OAuth Authorization Flow")
    print(f"{'='*60}")
    print(f"\n1. Open this URL in your browser:\n")
    print(f"   {auth_url}\n")
    print("2. Log in to your Schwab account and authorize the app.")
    print(f"3. You'll be redirected to {REDIRECT_URI} — we'll capture the code.")
    print("   (Your browser will warn about the self-signed cert — click Advanced → Proceed.)\n")

    # Generate self-signed cert for HTTPS callback listener
    with tempfile.TemporaryDirectory(prefix="schwab_oauth_") as tmp:
        tmp_dir = Path(tmp)
        cert_path, key_path = _generate_self_signed_cert(tmp_dir)

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

        server = HTTPServer((LOCAL_HOST, LOCAL_PORT), CallbackHandler)
        server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)
        print(f"   Waiting for callback on {REDIRECT_URI}...")
        server.handle_request()  # Handle one request (the callback)

    code = CallbackHandler.captured_code
    if not code:
        print("\nERROR: No authorization code received.")
        sys.exit(1)

    print(f"\n   Got authorization code ({len(code)} chars)")
    print("   Exchanging for tokens...")

    try:
        tokens = exchange_code(code, client_id, client_secret)
    except Exception as e:
        print(f"\nERROR: Token exchange failed: {e}")
        sys.exit(1)

    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    expires_in = tokens.get("expires_in", 1800)

    if not refresh_token:
        print("\nERROR: No refresh_token in response.")
        print(f"   Response: {json.dumps(tokens, indent=2)}")
        sys.exit(1)

    # Save tokens
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    import time
    payload = {
        "access_token": access_token,
        "access_expiry": time.time() + expires_in,
        "refresh_token": refresh_token,
    }
    _TOKEN_FILE.write_text(json.dumps(payload, indent=2))

    print(f"\n{'='*60}")
    print("  SUCCESS! Tokens saved to:")
    print(f"  {_TOKEN_FILE}")
    print(f"{'='*60}")
    print(f"\n  Refresh token: {refresh_token[:20]}...{refresh_token[-10:]}")
    print(f"  Access token expires in {expires_in}s")
    print(f"\n  You can now use SchwabProvider — it will auto-refresh tokens.")
    print(f"  Set SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET in your environment")
    print(f"  for the provider to work.\n")


if __name__ == "__main__":
    main()
