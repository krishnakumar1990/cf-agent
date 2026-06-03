"""
OAuth Authorization Code flow for user-level authentication.

Flow:
  1. Find a free local port.
  2. Open browser to Adobe IMS authorize URL (with port in state/redirect).
  3. Local HTTP server catches the callback redirect from the Vercel relay.
  4. Exchange auth code for access_token + refresh_token.
  5. Cache tokens in memory; silently refresh using refresh_token when expired.
"""

import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import config

_cache: dict = {}  # keys: access_token, expires_at


def _free_port() -> int:
    """Ask the OS for a free port by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _exchange_code(cfg: dict, code: str, redirect_uri: str) -> dict:
    resp = httpx.post(
        config.ADOBE_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": cfg["ADOBE_CLIENT_ID"],
            "client_secret": cfg["ADOBE_CLIENT_SECRET"],
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if not resp.is_success:
        raise SystemExit(
            f"Token exchange failed ({resp.status_code}):\n{resp.text}"
        )
    return resp.json()


def _refresh_access_token(cfg: dict, refresh_token: str) -> dict:
    resp = httpx.post(
        config.ADOBE_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": cfg["ADOBE_CLIENT_ID"],
            "client_secret": cfg["ADOBE_CLIENT_SECRET"],
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def browser_login(cfg: dict) -> None:
    """Open browser, catch callback, exchange code, save tokens."""
    port = _free_port()
    redirect_uri = cfg["ADOBE_REDIRECT_URI"]
    # Encode port in state so Vercel relay can forward to the right local port
    # without URLSearchParams re-encoding the auth code
    state = f"{port}.{secrets.token_urlsafe(16)}"

    authorize_url = config.ADOBE_AUTHORIZE_URL + "?" + urlencode({
        "client_id": cfg["ADOBE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "scope": cfg["ADOBE_SCOPES"],
        "response_type": "code",
        "state": state,
    })

    received: dict = {}
    server_ready = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # suppress request logs

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)
            received["code"] = params.get("code", [None])[0]
            received["error"] = params.get("error", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Login complete. Return to your terminal.</h2></body></html>")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    httpd = HTTPServer(("localhost", port), _Handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    print(f"Opening browser for Adobe login (listening on port {port})...")
    webbrowser.open(authorize_url)
    httpd._BaseServer__is_shut_down.wait(timeout=120)

    if received.get("error"):
        raise SystemExit(f"Login failed: {received['error']}")
    if not received.get("code"):
        raise SystemExit("Login timed out or was cancelled.")

    print("Auth code received, exchanging for tokens...")
    token_data = _exchange_code(cfg, received["code"], redirect_uri)

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = int(token_data.get("expires_in", 3600))
    expires_at = time.time() + expires_in - 60

    config.save_tokens(access_token, refresh_token, expires_at)
    _cache.update({"access_token": access_token, "refresh_token": refresh_token, "expires_at": expires_at})
    print("Login successful. Tokens saved.")
    return access_token


def get_token(cfg: dict) -> str:
    """Return a valid access token, refreshing silently if needed."""
    now = time.time()

    # Check in-memory cache first
    if _cache.get("access_token") and now < _cache.get("expires_at", 0):
        return _cache["access_token"]

    # Try file-stored tokens
    stored = config.load_tokens()
    if stored.get("ACCESS_TOKEN") and now < float(stored.get("TOKEN_EXPIRES_AT", 0)):
        _cache.update({
            "access_token": stored["ACCESS_TOKEN"],
            "refresh_token": stored.get("REFRESH_TOKEN", ""),
            "expires_at": float(stored["TOKEN_EXPIRES_AT"]),
        })
        return stored["ACCESS_TOKEN"]

    # Try to silently refresh
    refresh_token = _cache.get("refresh_token") or stored.get("REFRESH_TOKEN", "")
    if refresh_token:
        try:
            token_data = _refresh_access_token(cfg, refresh_token)
            access_token = token_data["access_token"]
            new_refresh = token_data.get("refresh_token", refresh_token)
            expires_in = int(token_data.get("expires_in", 3600))
            expires_at = now + expires_in - 60
            config.save_tokens(access_token, new_refresh, expires_at)
            _cache.update({"access_token": access_token, "refresh_token": new_refresh, "expires_at": expires_at})
            return access_token
        except Exception:
            pass

    raise SystemExit("Not logged in. Run `cf-agent login` first.")
