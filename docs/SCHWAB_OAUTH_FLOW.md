# Schwab API OAuth Token Flow

This document captures the full OAuth 2.0 authorization code flow used to
obtain a long-lived **refresh token** for the Schwab Trader API. Once the
refresh token is saved, the `SchwabProvider` (in `agents/schwab_auth.py`)
handles ongoing access-token refresh automatically — the manual flow below
only needs to be repeated if the refresh token is revoked or expires.

---

## 1. Prerequisites

1. **Schwab Developer App** registered at <https://developer.schwab.com>
   - App must have the **Trader API** product enabled.
   - Redirect URI must be exactly: `https://127.0.0.1:8182`
     (Schwab requires HTTPS — HTTP callbacks are rejected.)
2. **Client credentials** stored in `.env` at the repo root:
   ```sh
   SCHWAB_CLIENT_ID=your_client_id
   SCHWAB_CLIENT_SECRET=your_client_secret
   ```
3. **openssl** installed (used to mint a self-signed cert for the local
   HTTPS listener). On macOS this is available by default.

---

## 2. Files Involved

| File | Purpose |
|------|---------|
| `agents/schwab_oauth_flow.py` | One-time interactive OAuth flow script. |
| `agents/schwab_auth.py` | `SchwabProvider` — runtime auth + token refresh. Exposes `_TOKEN_URL` and `_TOKEN_FILE`. |
| `~/.config/devin/schwab_tokens.json` | Persisted token payload (access + refresh). |
| `.env` | Holds `SCHWAB_CLIENT_ID` / `SCHWAB_CLIENT_SECRET`. |

---

## 3. The Flow (Step by Step)

### Step 1 — Launch the script

```sh
cd /Users/tashuanspence/Development/ai-trader
set -a && source .env && set +a
python agents/schwab_oauth_flow.py
```

The script:
- Generates a self-signed TLS certificate in a temp directory.
- Starts an HTTPS listener on `https://127.0.0.1:8182` (timeout: 30 min).
- Prints the Schwab authorization URL.

### Step 2 — Authorize in the browser

1. Open the printed URL in a browser.
2. Log in to Schwab and approve the app.
3. Schwab redirects to `https://127.0.0.1:8182/?code=...`.
4. The browser will warn about the self-signed cert — click
   **Advanced → Proceed to 127.0.0.1 (unsafe)**.
5. The local listener captures the `code` query parameter and responds
   with a success page. You can close the tab.

### Step 3 — Token exchange

The script POSTs to Schwab's token endpoint
(`https://api.schwabapi.com/v1/oauth/token`) with:

- **Headers:**
  - `Authorization: Basic base64(client_id:client_secret)`
  - `Content-Type: application/x-www-form-urlencoded`
- **Body (form-encoded):**
  - `grant_type=authorization_code`
  - `code=<captured code>`
  - `redirect_uri=https://127.0.0.1:8182`

> **Important:** Schwab rejects requests that include `client_id` /
> `client_secret` in the body. Credentials must be supplied via HTTP Basic
> auth only. Sending them in the body results in HTTP 401 Unauthorized.

### Step 4 — Tokens saved

On success the response contains `access_token`, `refresh_token`, and
`expires_in` (typically 1800s / 30 min). The script writes them to:

```
~/.config/devin/schwab_tokens.json
```

Format:
```json
{
  "access_token": "...",
  "access_expiry": 1757817600.0,
  "refresh_token": "..."
}
```

### Step 5 — Verify

```sh
python -c "
import json, os, requests
tok = json.load(open(os.path.expanduser('~/.config/devin/schwab_tokens.json')))
r = requests.get('https://api.schwabapi.com/trader/v1/userPreference',
    headers={'Authorization': f'Bearer {tok[\"access_token\"]}'})
print('status:', r.status_code)
print(r.text[:300])
"
```

A `200` response with account data confirms the tokens are valid.

---

## 4. Runtime Token Refresh

`SchwabProvider` (in `agents/schwab_auth.py`) loads the saved refresh token
on startup and automatically mints a new access token whenever the cached
one expires. The refresh request uses:

- **Headers:**
  - `Authorization: Basic base64(client_id:client_secret)`
  - `Content-Type: application/x-www-form-urlencoded`
- **Body:**
  - `grant_type=refresh_token`
  - `refresh_token=<saved refresh token>`

No user interaction is required for refreshes.

---

## 5. Common Issues & Fixes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Local listener exits immediately | `server.handle_request()` blocks once with no timeout | Set `server.timeout` and loop until code captured or 30-min deadline (already implemented). |
| HTTP 401 on token exchange | `client_id`/`client_secret` sent in body | Use HTTP Basic auth header only; remove credentials from body. |
| HTTP 400 `invalid_grant` | Authorization code already used or expired | Codes are single-use and expire in ~60s. Re-run the full flow. |
| Browser refuses self-signed cert | Chrome/Safari strict cert policy | Click **Advanced → Proceed anyway**. Safari may require reloading the URL after trusting. |
| `refresh_token` missing from response | App not approved for Trader API | Confirm the developer.schwab.com app has the Trader API product enabled. |
| Refresh token stops working after ~7 days | Schwab refresh tokens expire if unused | Run any Schwab API call at least once per week; the auto-refresh in `SchwabProvider` handles this. |

---

## 6. Re-running the Flow

If the refresh token is revoked, lost, or expired, repeat Section 3 in
full. The old `schwab_tokens.json` will be overwritten with the new
payload. No other configuration needs to change.

---

## 7. Security Notes

- `schwab_tokens.json` contains live credentials — never commit it to git.
  It lives outside the repo at `~/.config/devin/`.
- `.env` holds client secrets — also git-ignored.
- The self-signed cert is generated in a temp directory and deleted when
  the script exits.
- The local listener binds to `127.0.0.1` only (not `0.0.0.0`).
