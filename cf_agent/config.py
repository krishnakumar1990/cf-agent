"""
Config and token storage.

Credentials (~/.cf-agent/config):
  ADOBE_CLIENT_ID, ADOBE_CLIENT_SECRET, ADOBE_SCOPES, ADOBE_REDIRECT_URI
  ADOBE_SITES_API_BASE_URL  — set after login via environment selector

Tokens (~/.cf-agent/tokens)  — written after browser login:
  ACCESS_TOKEN, REFRESH_TOKEN, TOKEN_EXPIRES_AT
"""

import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".cf-agent"
CONFIG_FILE = CONFIG_DIR / "config"
TOKEN_FILE = CONFIG_DIR / "tokens"

ADOBE_AUTHORIZE_URL = "https://ims-na1.adobelogin.com/ims/authorize/v2"
ADOBE_TOKEN_URL = "https://ims-na1.adobelogin.com/ims/token/v3"

REQUIRED_CONFIG = [
    "ADOBE_CLIENT_ID",
    "ADOBE_CLIENT_SECRET",
    "ADOBE_SCOPES",
    "ADOBE_REDIRECT_URI",
]


def _parse_file(path: Path) -> dict:
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip().rstrip("\r")
                if not line or line.startswith("#"):
                    continue
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    except FileNotFoundError:
        pass
    return result


def _write_file(path: Path, values: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for k, v in values.items():
            f.write(f"{k}={v}\n")
    path.chmod(0o600)


def load_config() -> dict:
    """Load app credentials. Raises if any required key is missing."""
    cfg = {}
    cfg.update(_parse_file(CONFIG_FILE))
    for key in REQUIRED_CONFIG:
        if key in os.environ:
            cfg[key] = os.environ[key]

    missing = [k for k in REQUIRED_CONFIG if not cfg.get(k)]
    if missing:
        raise SystemExit(
            f"Missing configuration: {', '.join(missing)}\n"
            "Run `cf-agent login` to set up your credentials."
        )

    return cfg


def save_config(values: dict) -> None:
    _write_file(CONFIG_FILE, values)


def load_tokens() -> dict:
    """Return stored tokens, or empty dict if none saved yet."""
    return _parse_file(TOKEN_FILE)


def save_tokens(access_token: str, refresh_token: str, expires_at: float) -> None:
    _write_file(TOKEN_FILE, {
        "ACCESS_TOKEN": access_token,
        "REFRESH_TOKEN": refresh_token,
        "TOKEN_EXPIRES_AT": str(int(expires_at)),
    })


def clear_tokens() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
