"""Tool definitions and handlers for the AEM Content Fragments agent."""

import json

from . import client

# ── handlers ──────────────────────────────────────────────────────────────────

def list_fragments(cfg, *, path=None, limit=10, cursor=None, references=None):
    params = {"limit": limit}
    if path:
        params["path"] = path
    if cursor:
        params["cursor"] = cursor
    if references:
        params["references"] = references
    r = client.request(cfg, "GET", "/cf/fragments", params=params)
    return r.json()


def get_fragment(cfg, *, id):
    r = client.request(cfg, "GET", f"/cf/fragments/{id}")
    etag = r.headers.get("ETag", "")
    data = r.json()
    data["_etag"] = etag
    return data


def search_fragments(cfg, *, query, path=None, limit=10):
    params = {"query": query, "limit": limit}
    if path:
        params["path"] = path
    r = client.request(cfg, "GET", "/cf/fragments/search", params=params)
    return r.json()


def create_fragment(cfg, *, parentPath, modelId, name, title=None, fields=None):
    # API expects parentPath relative to /content/dam
    if parentPath.startswith("/content/dam"):
        parentPath = parentPath[len("/content/dam"):]

    body: dict = {"parentPath": parentPath, "modelId": modelId, "name": name}
    if title:
        body["title"] = title
    if fields:
        # Accept list (correct API format) or legacy dict → convert to list
        if isinstance(fields, dict):
            body["fields"] = [
                {"name": k, "type": "text", "values": v if isinstance(v, list) else [v]}
                for k, v in fields.items()
            ]
        else:
            body["fields"] = fields
    r = client.request(cfg, "POST", "/cf/fragments", json=body)
    return r.json()


def update_fragment(cfg, *, id, etag, patch_operations):
    headers = {"If-Match": etag}
    r = client.request(
        cfg, "PATCH", f"/cf/fragments/{id}",
        content_type="application/json-patch+json",
        headers=headers,
        content=json.dumps(patch_operations),
    )
    return r.json()


def delete_fragment(cfg, *, id, etag):
    headers = {"If-Match": etag}
    client.request(cfg, "DELETE", f"/cf/fragments/{id}", content_type="", headers=headers)
    return {"status": "deleted", "id": id}


def publish_fragments(cfg, *, ids):
    body = [{"id": i} for i in ids]
    r = client.request(cfg, "POST", "/cf/fragments/publish", json=body)
    return r.json() if r.status_code != 204 else {"status": "published"}


def list_models(cfg, *, path=None, limit=10):
    params = {"limit": limit}
    if path:
        params["path"] = path
    r = client.request(cfg, "GET", "/cf/models", params=params)
    return r.json()


def get_model(cfg, *, id):
    r = client.request(cfg, "GET", f"/cf/models/{id}")
    return r.json()


def list_variations(cfg, *, fragment_id):
    r = client.request(cfg, "GET", f"/cf/fragments/{fragment_id}/variations")
    return r.json()


def copy_fragment(cfg, *, id, destination_path, deep=False):
    body = {"destinationPath": destination_path, "deep": deep}
    r = client.request(cfg, "POST", f"/cf/fragments/{id}/copy", json=body)
    return r.json()


# ── tool definitions ───────────────────────────────────────────────────────────

DEFINITIONS = [
    {
        "name": "list_fragments",
        "description": "List content fragments. Optionally filter by folder path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Folder path to filter by"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
                "cursor": {"type": "string", "description": "Pagination cursor"},
                "references": {"type": "string", "description": "Include references: DIRECT or TRANSITIVE"},
            },
        },
    },
    {
        "name": "get_fragment",
        "description": "Get a single content fragment by ID, including its ETag for updates.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "Fragment UUID"}},
            "required": ["id"],
        },
    },
    {
        "name": "search_fragments",
        "description": "Full-text search for content fragments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "path": {"type": "string", "description": "Scope search to folder path"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "create_fragment",
        "description": "Create a new content fragment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "parentPath": {"type": "string", "description": "Parent folder path"},
                "modelPath": {"type": "string", "description": "Content Fragment Model path"},
                "name": {"type": "string", "description": "Fragment name (slug)"},
                "title": {"type": "string", "description": "Fragment title"},
                "fields": {"type": "object", "description": "Initial field values"},
            },
            "required": ["parentPath", "modelPath", "name"],
        },
    },
    {
        "name": "update_fragment",
        "description": "Update a content fragment using JSON Patch operations. Requires ETag from get_fragment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Fragment UUID"},
                "etag": {"type": "string", "description": "ETag value from get_fragment"},
                "patch_operations": {
                    "type": "array",
                    "description": "JSON Patch operations (RFC 6902)",
                    "items": {"type": "object"},
                },
            },
            "required": ["id", "etag", "patch_operations"],
        },
    },
    {
        "name": "delete_fragment",
        "description": "Delete a content fragment. Requires ETag from get_fragment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Fragment UUID"},
                "etag": {"type": "string", "description": "ETag value from get_fragment"},
            },
            "required": ["id", "etag"],
        },
    },
    {
        "name": "publish_fragments",
        "description": "Publish one or more content fragments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}, "description": "List of fragment UUIDs"},
            },
            "required": ["ids"],
        },
    },
    {
        "name": "list_models",
        "description": "List available Content Fragment Models.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Filter by folder path"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
            },
        },
    },
    {
        "name": "list_variations",
        "description": "List all variations of a content fragment.",
        "input_schema": {
            "type": "object",
            "properties": {"fragment_id": {"type": "string", "description": "Fragment UUID"}},
            "required": ["fragment_id"],
        },
    },
    {
        "name": "copy_fragment",
        "description": "Copy a content fragment to a new location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Fragment UUID"},
                "destination_path": {"type": "string", "description": "Destination folder path"},
                "deep": {"type": "boolean", "description": "Deep copy including referenced fragments"},
            },
            "required": ["id", "destination_path"],
        },
    },
]

HANDLERS = {
    "list_fragments": list_fragments,
    "get_fragment": get_fragment,
    "search_fragments": search_fragments,
    "create_fragment": create_fragment,
    "update_fragment": update_fragment,
    "delete_fragment": delete_fragment,
    "publish_fragments": publish_fragments,
    "list_models": list_models,
    "list_variations": list_variations,
    "copy_fragment": copy_fragment,
}
