import httpx
import click

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
    import json as _json

    msg = f"AEM API error {resp.status_code} {resp.reason_phrase} — {method} {url}"

    hint = _STATUS_HINTS.get(resp.status_code, "")
    if hint:
        msg += f"\n{hint}"

    try:
        body = resp.json()
    except Exception:
        raw = resp.text.strip()
        if raw:
            msg += f"\n\nResponse body:\n{raw}"
        return msg

    # Top-level title / detail
    if body.get("title"):
        msg += f"\n\nError: {body['title']}"
    if body.get("detail"):
        msg += f"\n{body['detail']}"

    # Field-level validation errors — AEM uses several different key names.
    errors = (
        body.get("validationStatus")
        or body.get("errors")
        or body.get("invalidParams")
        or body.get("details")
        or body.get("violations")
        or []
    )
    if errors:
        msg += "\n\nField errors:"
        for e in errors:
            if not isinstance(e, dict):
                msg += f"\n  • {e}"
                continue
            field = (
                e.get("property")
                or e.get("field")
                or e.get("name")
                or e.get("param")
                or e.get("pointer")
                or ""
            )
            reason = (
                e.get("message")
                or e.get("reason")
                or e.get("detail")
                or e.get("description")
                or ""
            )
            invalid = e.get("invalidValue")
            line = f"  • {field}: {reason}" if field else f"  • {reason}"
            if invalid is not None:
                line += f"  (got: {_json.dumps(invalid)})"
            msg += f"\n{line}"

    # Always append the full raw body for 4xx/5xx so nothing AEM sends is hidden.
    msg += f"\n\nFull response body:\n{_json.dumps(body, indent=2)}"

    return msg


def request(cfg: dict, method: str, path: str, content_type: str = "application/json", **kwargs) -> httpx.Response:
    token = auth.get_token(cfg)
    base_url = cfg.get("ADOBE_SITES_API_BASE_URL")
    if not base_url:
        raise click.ClickException(
            "No AEM environment selected. Run `cf-agent env select` to choose an environment."
        )
    base = base_url.rstrip("/")
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers["X-Adobe-Accept-Experimental"] = "1"
    if content_type and method.upper() not in ("GET", "HEAD", "DELETE"):
        headers["Content-Type"] = content_type

    resp = httpx.request(method, f"{base}{path}", headers=headers, timeout=30, **kwargs)
    if resp.status_code == 204:
        return resp
    if resp.is_error:
        raise click.ClickException(_format_error(resp, method, f"{base}{path}"))
    return resp


def _assets_base_url(cfg: dict) -> str:
    """Derive the Assets Author API base URL from the Sites API URL.

    Sites URL:  https://{bucket}.adobeaemcloud.com/adobe/sites
    Assets URL: https://{bucket}.adobeaemcloud.com/adobe/assets
    """
    sites_url = cfg.get("ADOBE_SITES_API_BASE_URL", "")
    return sites_url.rstrip("/").replace("/adobe/sites", "/adobe/assets", 1)


def _resource_exists_via_assets_api(token: str, assets_base: str, dam_path: str) -> bool | None:
    """Use the Assets Author API search endpoint to check DAM asset existence.

    Returns True/False when the API responds, or None when the call fails so
    the caller can fall back to the HEAD/GET strategy.
    """
    search_url = f"{assets_base}/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Adobe-Accept-Experimental": "1",
    }
    body = {
        "query": [
            {
                # "term" = exact unanalyzed match — prevents fuzzy hits like
                # workday.svg matching a search for workday1.svg.
                "term": {
                    "text": dam_path,
                    "fields": ["repositoryMetadata.repo:path"],
                }
            }
        ],
        "limit": 5,
    }
    try:
        resp = httpx.post(
            search_url,
            params={"allowUnsafeSearch": "true"},
            json=body,
            headers=headers,
            timeout=15,
        )
    except httpx.HTTPError:
        return None

    if resp.status_code == 200:
        results = resp.json().get("hits", {}).get("results", [])
        for r in results:
            meta = r.get("repositoryMetadata", {})
            if meta.get("repo:path") == dam_path:
                return True
        if results:
            return None
        return False
    # 401/403 on the Assets Author API means the OAuth scopes don't cover it.
    # Fall through to the REST API fallback instead of blocking the operation.
    return None  # unexpected status or auth gap — let caller fall back


def resource_exists(cfg: dict, resource_path: str) -> bool:
    """Check whether an author-tier AEM DAM resource exists.

    Strategy:
    1. Assets Author API search (POST /adobe/assets/search) — structured and reliable.
    2. Fall back to AEM Assets REST API (/api/assets/{path}.json).
    """
    token = auth.get_token(cfg)
    base_url = cfg.get("ADOBE_SITES_API_BASE_URL")
    if not base_url:
        raise click.ClickException(
            "No AEM environment selected. Run `cf-agent env select` to choose an environment."
        )

    path = resource_path if resource_path.startswith("/") else f"/{resource_path}"

    # ── Primary: Assets Author API search ────────────────────────────────────
    assets_base = _assets_base_url(cfg)
    if assets_base:
        result = _resource_exists_via_assets_api(token, assets_base, path)
        if result is not None:
            return result

    # ── Fallback: AEM Assets REST API (/api/assets/{path}.json) ──────────────
    # This endpoint is purpose-built for DAM asset management and reliably
    # returns 404 for non-existent assets. It does NOT suffer from Sling's
    # path-traversal 200 behaviour (which affects raw /content/dam/ paths).
    base = base_url.rstrip("/")
    author_root = base.split("/adobe/sites", 1)[0]

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Adobe-Accept-Experimental": "1",
    }

    if path.startswith("/content/dam/"):
        relative = path[len("/content/dam/"):]
        url = f"{author_root}/api/assets/{relative}.json"
    else:
        url = f"{author_root}{path}"

    try:
        resp = httpx.request("GET", url, headers=headers, timeout=15)
    except httpx.HTTPError:
        return False

    if resp.status_code == 200:
        ct = resp.headers.get("content-type", "")
        if "text/html" in ct:
            return False
        return True
    if resp.status_code in (401, 403):
        # Both the Assets Author API and the REST API are inaccessible with
        # the current token scopes. Warn and skip — AEM will validate on write.
        click.echo(
            f"Warning: cannot verify asset path '{resource_path}' (API returned 403 — "
            f"token scopes do not cover asset lookup). AEM may reject at publish time "
            f"if the path does not exist in DAM.",
            err=True,
        )
        return True
    return False
