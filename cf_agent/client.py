import httpx

from . import auth

_STATUS_HINTS = {
    401: "Access token is invalid or expired. Run `cf-agent login` to re-authenticate.",
    403: (
        "Permission denied. Your Adobe ID may not be provisioned on this environment.\n"
        "  • Try a different environment: cf-agent env select\n"
        "  • Or ask an AEM admin to add your user to the correct group on this environment."
    ),
    404: "Resource not found. Check the ID or path you provided.",
    412: "Precondition failed. The fragment may have been modified — retry the operation.",
}


def _format_error(resp: httpx.Response, method: str, url: str) -> str:
    msg = f"AEM API error {resp.status_code} {resp.reason_phrase} — {method} {url}"

    hint = _STATUS_HINTS.get(resp.status_code, "")
    if hint:
        msg += f"\n{hint}"

    # Parse structured error body from AEM
    try:
        body = resp.json()
    except Exception:
        raw = resp.text.strip()
        if raw:
            msg += f"\n\nResponse: {raw}"
        return msg

    # Top-level title / detail
    if body.get("title"):
        msg += f"\n\nError: {body['title']}"
    if body.get("detail"):
        msg += f"\n{body['detail']}"

    # Field-level validation errors
    errors = body.get("errors") or body.get("details") or body.get("invalidParams") or []
    if errors:
        msg += "\n\nField errors:"
        for e in errors:
            field = e.get("name") or e.get("field") or e.get("param", "")
            reason = e.get("message") or e.get("reason") or e.get("detail", "")
            msg += f"\n  • {field}: {reason}" if field else f"\n  • {reason}"

    return msg


def request(cfg: dict, method: str, path: str, content_type: str = "application/json", **kwargs) -> httpx.Response:
    token = auth.get_token(cfg)
    base_url = cfg.get("ADOBE_SITES_API_BASE_URL")
    if not base_url:
        raise SystemExit(
            "No AEM environment selected.\n"
            "Run `cf-agent env select` to choose an environment."
        )
    base = base_url.rstrip("/")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers["X-Adobe-Accept-Experimental"] = "1"
    if content_type:
        headers["Content-Type"] = content_type

    resp = httpx.request(method, f"{base}{path}", headers=headers, timeout=30, **kwargs)
    if resp.status_code == 204:
        return resp
    if resp.is_error:
        raise SystemExit(_format_error(resp, method, f"{base}{path}"))
    return resp
