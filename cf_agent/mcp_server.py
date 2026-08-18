"""MCP server exposing cf-agent's AEM Content Fragment operations to an agent.

Runs over stdio, so any MCP-capable client (Claude Code, Claude Desktop, …) can
drive the same operations the CLI performs. It reuses the CLI's own validation
rather than calling the raw AEM handlers, so an agent cannot create a fragment
the CLI would have rejected.

Deliberately **not** exposed: ``delete_fragment`` and ``publish_fragments``.
Both are destructive or externally visible, and are left to a human at the CLI.

Authentication is shared with the CLI — the server reads ``~/.cf-agent`` and
whatever ``cf-agent login`` stored there. Adobe tokens are short-lived and the
OAuth app currently issues no refresh token, so an unattended agent will hit an
expired token roughly daily; the tools surface that as a clear, actionable
error rather than a stack trace.

Run it with::

    cf-agent mcp
"""

import json

import click

from mcp.server import MCPServer

from . import agent as _agent
from . import client, config, tools as t

server = MCPServer(
    name="cf-agent",
    instructions=(
        "Manage Adobe AEM Content Fragments for the Moveworks Marketplace.\n\n"
        "Typical flow: list_models to find a model, list_fragments or "
        "search_fragments to find existing content, get_fragment for detail, "
        "then create_fragment or update_fragment to change it.\n\n"
        "Field rules (required, enums, max length, slug format) come live from "
        "the AEM model and are enforced on write — read them with get_model "
        "before composing fields.\n\n"
        "Deleting and publishing are not available here by design; ask the user "
        "to run those from the CLI."
    ),
)


def _cfg() -> dict:
    """Load config and confirm a usable token, as a normal exception.

    ``config.load_config`` and ``auth.get_token`` raise SystemExit, which would
    terminate the server process instead of failing one tool call.
    """
    try:
        cfg = config.load_config()
    except SystemExit as exc:
        raise RuntimeError(
            f"cf-agent is not configured ({exc}). Ask the user to run `cf-agent login`."
        )

    try:
        from . import auth

        auth.get_token(cfg)
    except SystemExit:
        raise RuntimeError(
            "Not logged in, or the Adobe token has expired. Ask the user to run "
            "`cf-agent login` — this needs an interactive browser sign-in and "
            "cannot be done from here."
        )
    return cfg


def _run(fn, *args, **kwargs):
    """Call a cf-agent operation, converting its CLI-shaped errors to text."""
    try:
        return fn(*args, **kwargs)
    except click.ClickException as exc:
        raise RuntimeError(exc.format_message())
    except SystemExit as exc:
        raise RuntimeError(str(exc))


# ── read ──────────────────────────────────────────────────────────────────────

@server.tool()
def list_fragments(path: str | None = None, limit: int = 10,
                   cursor: str | None = None) -> str:
    """List content fragments, optionally filtered by DAM folder path.

    Returns JSON. Use `cursor` from a previous response to page.
    """
    cfg = _cfg()
    return json.dumps(_run(t.list_fragments, cfg, path=path, limit=limit, cursor=cursor), indent=2)


@server.tool()
def get_fragment(id: str) -> str:
    """Get one content fragment by UUID, including its ETag and field values."""
    cfg = _cfg()
    return json.dumps(_run(t.get_fragment, cfg, id=id), indent=2)


@server.tool()
def search_fragments(query: str, path: str | None = None, limit: int = 10) -> str:
    """Full-text search for content fragments.

    Note: the underlying AEM search endpoint is unavailable on some
    environments — prefer list_fragments with a path when it fails.
    """
    cfg = _cfg()
    return json.dumps(_run(t.search_fragments, cfg, query=query, path=path, limit=limit), indent=2)


@server.tool()
def list_models(path: str | None = None, limit: int = 25) -> str:
    """List available Content Fragment Models (connector, plugin, …)."""
    cfg = _cfg()
    return json.dumps(_run(t.list_models, cfg, path=path, limit=limit), indent=2)


@server.tool()
def get_model_schema(model_path: str) -> str:
    """Get a model's field definitions — the authoritative validation rules.

    Read this before composing fields for create_fragment or update_fragment:
    it carries required flags, enum values, max lengths and regex rules.
    `model_path` is the DAM path of the model, e.g.
    /conf/marketplace/settings/dam/cfm/models/marketplace-connector.
    """
    cfg = _cfg()
    return json.dumps(_run(_agent._model_schema_fields, cfg, model_path), indent=2)


@server.tool()
def list_variations(fragment_id: str) -> str:
    """List all variations of a content fragment."""
    cfg = _cfg()
    return json.dumps(_run(t.list_variations, cfg, fragment_id=fragment_id), indent=2)


@server.tool()
def asset_exists(dam_path: str) -> str:
    """Check whether an asset exists in the AEM DAM at a /content/dam/... path."""
    cfg = _cfg()
    exists = _run(client.resource_exists, cfg, dam_path)
    return json.dumps({"path": dam_path, "exists": exists})


# ── write (create / update only) ──────────────────────────────────────────────

@server.tool()
def create_fragment(parent_path: str, model_path: str, name: str,
                    fields: dict, title: str | None = None) -> str:
    """Create a content fragment.

    `fields` maps field name to a string value, e.g.
    {"slug": "acme", "description": "...", "logo": "acme.svg"}.
    Values are validated against the live AEM model first — required fields,
    enums, lengths, slug format — and the call fails with the reason if invalid.
    """
    cfg = _cfg()
    field_args = tuple(f"{k}={v}" for k, v in (fields or {}).items())
    built = _run(_agent._build_fields_from_args, cfg, field_args, model_path,
                 require_all_required=True)
    model_id = _agent._encode_model_id(model_path)
    result = _run(t.create_fragment, cfg, parentPath=parent_path, modelId=model_id,
                  name=name, title=title, fields=built)
    return json.dumps(result, indent=2)


@server.tool()
def update_fragment(id: str, model_path: str, fields: dict) -> str:
    """Update an existing fragment's fields.

    `fields` maps field name to a new string value. Values are validated against
    the live AEM model. The current ETag is fetched immediately before the write,
    so a concurrent edit made between that fetch and the write is overwritten —
    for careful edits, read the fragment first and confirm with the user.

    Immutable fields (slug, systems, availability) and review-locked fragments
    are rejected by AEM.
    """
    cfg = _cfg()
    field_args = tuple(f"{k}={v}" for k, v in (fields or {}).items())
    built = _run(_agent._build_fields_from_args, cfg, field_args, model_path,
                 require_all_required=False)
    current = _run(t.get_fragment, cfg, id=id)
    etag = current.get("_etag", "")
    patch = [{"op": "replace", "path": "/fields", "value": built}]
    return json.dumps(_run(t.update_fragment, cfg, id=id, etag=etag,
                           patch_operations=patch), indent=2)


@server.tool()
def copy_fragment(id: str, destination_path: str, deep: bool = False) -> str:
    """Copy a fragment to another folder. `deep` also copies referenced fragments."""
    cfg = _cfg()
    return json.dumps(_run(t.copy_fragment, cfg, id=id,
                           destination_path=destination_path, deep=deep), indent=2)


@server.tool()
def upload_asset(local_path: str, dam_folder: str, name: str | None = None) -> str:
    """Upload a local file into the AEM DAM and return its /content/dam/... path.

    Use this when a logo or content-guide image a fragment needs is not in the
    DAM yet. Requires AWS staging credentials — if none are configured, ask the
    user to run `cf-agent asset credentials set`.
    """
    from . import uploader

    cfg = _cfg()
    if not uploader.credentials_available():
        raise RuntimeError(
            "No AWS credentials available for asset staging. Ask the user to run "
            "`cf-agent asset credentials set`."
        )
    result = _run(uploader.stage_and_import, cfg, local_path, dam_folder, name)
    return json.dumps({"dam_path": result["dam_path"], "asset_id": result.get("asset_id")}, indent=2)


def main() -> None:
    """Entry point for `cf-agent mcp`."""
    server.run()
