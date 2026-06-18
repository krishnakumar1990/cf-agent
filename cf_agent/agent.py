"""CLI entry point for cf-agent."""

import json
import re
from pathlib import Path

import click

from . import auth, config, environments
from . import client
from . import tools as t


def _cfg():
    cfg = config.load_config()
    try:
        auth.get_token(cfg)
    except SystemExit:
        raise click.ClickException("Not logged in. Run `cf-agent login` first.")
    return cfg


def _print_json(data):
    click.echo(json.dumps(data, indent=2))


def _looks_like_file_path(value: str) -> bool:
    """Return True when a long-text input looks like a file path."""
    return (
        value.startswith(("~/", "./", "../", "/"))
        or "/" in value
        or "\\" in value
    )


def _read_markdown_value(value: str) -> str:
    """Read long-text content from file when the value is a path-like input."""
    candidate = Path(value.strip("'\"")).expanduser()
    if candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    if _looks_like_file_path(value):
        raise click.ClickException(
            f"Long-text value looks like a file path but file was not found/readable: {value}"
        )
    return value


def _model_schema_fields(cfg: dict, model_path: str) -> list[dict]:
    """Return model schema fields from pre-fetched mapping or API fallback."""
    schema_fields = environments.MODEL_SCHEMAS.get(model_path, [])
    if schema_fields:
        return schema_fields

    # API fallback for model paths not present in pre-fetched schemas.
    models_data = t.list_models(cfg, path=model_path, limit=50)
    model_items = models_data.get("items", [])
    model_item = next((m for m in model_items if m.get("path") == model_path), None)
    if not model_item:
        return []
    model_id = model_item.get("id")
    if not model_id:
        return []
    try:
        model_schema = t.get_model(cfg, id=model_id)
    except SystemExit as exc:
        raise click.ClickException(f"Unable to load model schema for validation: {exc}")
    return model_schema.get("fields", [])


def _schema_map(schema_fields: list[dict]) -> dict[str, dict]:
    return {f.get("name", ""): f for f in schema_fields if f.get("name")}


def _enum_allowed_values(field_def: dict) -> set[str]:
    raw_values = (
        field_def.get("values")
        or field_def.get("enumValues")
        or field_def.get("allowedValues")
        or []
    )
    allowed = set()
    for val in raw_values:
        if isinstance(val, dict):
            allowed.add(str(val.get("value") or val.get("key") or ""))
        else:
            allowed.add(str(val))
    return {v for v in allowed if v}


def _asset_exists(cfg: dict, asset_path: str) -> bool:
    """Validate that a DAM asset path exists on the active AEM environment."""
    return client.resource_exists(cfg, asset_path)


def _validate_single_value(cfg: dict, field_def: dict, value: str) -> str:
    """Apply model-level validation rules and return normalized value."""
    name = field_def.get("name", "")
    ftype = field_def.get("fieldType") or field_def.get("type", "text")
    max_len = field_def.get("maxLength") or field_def.get("maxSize")
    regex = field_def.get("customValidationRegex", "")
    err_msg = field_def.get("customErrorMessage", f"Invalid value for '{name}'.")

    if ftype == "long-text":
        value = _read_markdown_value(value)

    if ftype == "boolean" and value.lower() not in ("true", "false"):
        raise click.ClickException(f"Field '{name}' expects true or false.")

    if max_len and len(value) > int(max_len):
        raise click.ClickException(
            f"Field '{name}' exceeds max length {max_len} (got {len(value)})."
        )

    if regex and not re.match(regex, value):
        raise click.ClickException(f"Field '{name}': {err_msg}")

    if ftype == "content-reference":
        root = field_def.get("root", "/content/dam").rstrip("/")
        if root != "/content/dam" and not value.startswith("/"):
            value = f"{root}/{value}"
        if not _asset_exists(cfg, value):
            raise click.ClickException(f"Referenced asset does not exist in AEM: {value}")

    return value


def _validate_field_values(cfg: dict, field_def: dict, values: list[str]) -> list[str]:
    """Validate one field's values and return normalized values."""
    name = field_def.get("name", "")
    multiple = field_def.get("multiple", False)
    ftype = field_def.get("fieldType") or field_def.get("type", "text")

    if multiple:
        normalized = [v.strip() for v in values if v is not None and str(v).strip()]
        if field_def.get("required") and not normalized:
            raise click.ClickException(f"Field '{name}' is required.")
    else:
        first = values[0] if values else ""
        first = first.strip() if isinstance(first, str) else str(first)
        if not first and field_def.get("required"):
            raise click.ClickException(f"Field '{name}' is required.")
        normalized = [first] if first else []

    if ftype == "enumeration" and normalized:
        allowed = _enum_allowed_values(field_def)
        invalid = [v for v in normalized if v not in allowed]
        if invalid:
            invalid_list = ", ".join(invalid)
            raise click.ClickException(
                f"Field '{name}' has invalid option(s): {invalid_list}."
            )

    return [_validate_single_value(cfg, field_def, v) for v in normalized]


def _validate_cross_field_rules(field_values: dict[str, list[str]], model_path: str):
    """Validate business rules spanning multiple fields."""
    availability = (field_values.get("availability") or [""])[0]
    install_uuid = (field_values.get("installation_uuid") or [""])[0]
    if availability == "INSTALLABLE" and not install_uuid:
        raise click.ClickException(
            "Field 'installation_uuid' is required when availability is INSTALLABLE."
        )


def _normalize_and_validate_fields(
    cfg: dict,
    model_path: str,
    raw_fields: list[dict],
    *,
    require_all_required: bool,
) -> list[dict]:
    """Validate fields against schema and return normalized API field payload."""
    schema_fields = _model_schema_fields(cfg, model_path)
    if not schema_fields:
        raise click.ClickException(
            f"Could not load schema for model '{model_path}'. Cannot validate field names/values."
        )

    schema = _schema_map(schema_fields)
    normalized: list[dict] = []
    by_name: dict[str, list[str]] = {}

    for field in raw_fields:
        name = (field.get("name") or "").strip()
        if not name:
            raise click.ClickException("Field entries must include a non-empty 'name'.")
        if name not in schema:
            raise click.ClickException(f"Unknown field name '{name}' for model '{model_path}'.")

        field_def = schema[name]
        raw_values = field.get("values")
        if raw_values is None:
            value = field.get("value")
            raw_values = [value] if value is not None else []

        cast_values = [str(v) for v in raw_values if v is not None]
        if field_def.get("multiple") and len(cast_values) == 1 and "," in cast_values[0]:
            cast_values = [v.strip() for v in cast_values[0].split(",")]
        cleaned_values = _validate_field_values(cfg, field_def, cast_values)
        by_name[name] = cleaned_values

        entry = {
            "name": name,
            "type": field_def.get("fieldType") or field_def.get("type", "text"),
            "values": cleaned_values,
        }
        if entry["type"] == "long-text" and field_def.get("mimeType"):
            entry["mimeType"] = field_def["mimeType"]
        normalized.append(entry)

    if require_all_required:
        missing = [
            f.get("name")
            for f in schema_fields
            if f.get("required") and not by_name.get(f.get("name", ""))
        ]
        if missing:
            raise click.ClickException(
                f"Missing required field(s): {', '.join(sorted(missing))}."
            )

    _validate_cross_field_rules(by_name, model_path)
    return normalized


def _validate_slug_or_fail(slug: str, *, field_label: str = "slug"):
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug or ""):
        raise click.ClickException(
            f"{field_label} must be lowercase kebab-case (e.g. my-plugin-name)."
        )


def _check_duplicate_slug(
    cfg: dict,
    slug: str,
    model_path: str,
    search_folder: str = "",
    *,
    exclude_fragment_id: str = "",
) -> None:
    """Raise ClickException if another fragment in AEM already carries this slug value."""
    folder = search_folder.rstrip("/") or environments.MODEL_DEFAULTS.get(model_path, "")
    try:
        results = t.search_fragments(cfg, query=slug, path=folder or None, limit=50)
    except (Exception, SystemExit):
        return  # any search failure must not block the create/update

    for fragment in results.get("items", []):
        if exclude_fragment_id and fragment.get("id") == exclude_fragment_id:
            continue

        frag_path = fragment.get("path", "")

        # Prefer exact slug-field check when the search response includes field data.
        frag_fields = fragment.get("fields", [])
        if frag_fields:
            slug_field = next((f for f in frag_fields if f.get("name") == "slug"), None)
            if slug_field and slug in (slug_field.get("values") or []):
                raise click.ClickException(
                    f"Slug '{slug}' is already in use by an existing fragment: {frag_path}\n"
                    "Choose a unique slug."
                )
        else:
            # Fall back: the JCR node name (last path segment) equals the slug in practice.
            if frag_path.rstrip("/").endswith(f"/{slug}"):
                raise click.ClickException(
                    f"Slug '{slug}' is already in use by an existing fragment: {frag_path}\n"
                    "Choose a unique slug."
                )


# ── CLI ────────────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """cf-agent — CLI for AEM Content Fragments."""


# ── auth ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--preset", default=None, type=click.Path(exists=True), help="Path to a shared .env file with pre-filled Adobe credentials.")
def login(preset):
    """Authenticate via browser (Adobe IMS user OAuth)."""
    click.echo("Setting up cf-agent credentials.\n")

    pre = {}
    if preset:
        from pathlib import Path
        for line in Path(preset).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            pre[key.strip()] = value.strip()
        click.echo(f"Loaded shared config from {preset}\n")

    client_id = pre.get("ADOBE_CLIENT_ID") or click.prompt("Adobe Client ID")
    client_secret = pre.get("ADOBE_CLIENT_SECRET") or click.prompt("Adobe Client Secret", hide_input=True)
    scopes = pre.get("ADOBE_SCOPES") or click.prompt(
        "Adobe scopes",
        default="openid,AdobeID,aem.fragments.management,aem.folders",
    )
    redirect_uri = pre.get("ADOBE_REDIRECT_URI") or click.prompt(
        "Redirect URI (must match Adobe Developer Console)",
        default="https://aem-agent-callback.vercel.app/callback",
    )

    cfg_values = {
        "ADOBE_CLIENT_ID": client_id,
        "ADOBE_CLIENT_SECRET": client_secret,
        "ADOBE_SCOPES": scopes,
        "ADOBE_REDIRECT_URI": redirect_uri,
    }
    config.save_config(cfg_values)
    click.echo("Credentials saved.\n")

    cfg = config.load_config()
    auth.browser_login(cfg)

    sites_url = environments.prompt_environment_selection()
    cfg_values["ADOBE_SITES_API_BASE_URL"] = sites_url
    config.save_config(cfg_values)
    click.echo("Environment saved.\n")


@cli.command()
def logout():
    """Clear stored OAuth tokens."""
    config.clear_tokens()
    click.echo("Tokens cleared. Run `cf-agent login` to authenticate again.")


@cli.command()
def whoami():
    """Show the identity and org carried by the current access token."""
    import base64
    import time

    cfg = config.load_config()
    token = auth.get_token(cfg)

    # Decode JWT payload (no signature verification needed — we just want to read claims)
    try:
        payload_b64 = token.split(".")[1]
        # Pad to a multiple of 4
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        claims = {}

    email    = claims.get("email") or claims.get("user_id", "unknown")
    org      = claims.get("as", "") or claims.get("iss", "")
    exp      = claims.get("exp")
    scope    = claims.get("scope", "")
    client   = cfg.get("ADOBE_CLIENT_ID", "")
    env_url  = cfg.get("ADOBE_SITES_API_BASE_URL", "none selected")

    click.echo(f"User:        {email}")
    click.echo(f"IMS org:     {org}")
    click.echo(f"Client ID:   {client}")
    if exp:
        remaining = int(exp - time.time())
        status = f"expires in {remaining}s" if remaining > 0 else "EXPIRED"
        click.echo(f"Token:       {status}")
    click.echo(f"Scopes:      {scope}")
    click.echo(f"Environment: {env_url}")


# ── env group ─────────────────────────────────────────────────────────────────

@cli.group()
def env():
    """Manage AEM environment selection."""


@env.command("list")
def env_list():
    """List all available AEM environments."""
    cfg = config.load_config()
    current = cfg.get("ADOBE_SITES_API_BASE_URL", "")
    click.echo("")
    for i, e in enumerate(environments.ENVIRONMENTS, 1):
        marker = " (current)" if e["url"] == current else ""
        click.echo(f"  {i}. {e['label']:<6}  {e['url']}{marker}")


@env.command("select")
def env_select():
    """Interactively switch to a different AEM environment."""
    cfg = config.load_config()
    current = cfg.get("ADOBE_SITES_API_BASE_URL", "")
    url = environments.prompt_environment_selection(current)
    cfg["ADOBE_SITES_API_BASE_URL"] = url
    config.save_config({k: v for k, v in cfg.items() if k in config.REQUIRED_CONFIG + ["ADOBE_SITES_API_BASE_URL"]})
    click.echo(f"Switched to: {url}")


@env.command("current")
def env_current():
    """Show the currently active AEM environment."""
    cfg = config.load_config()
    url = cfg.get("ADOBE_SITES_API_BASE_URL")
    if not url:
        click.echo("No environment selected. Run `cf-agent env select`.")
        return
    label = next((e["label"] for e in environments.ENVIRONMENTS if e["url"] == url), "custom")
    click.echo(f"{label}  {url}")


# ── fragments group ────────────────────────────────────────────────────────────

@cli.group()
def fragments():
    """Manage Content Fragments."""


@fragments.command("list")
@click.option("--path", default=None, help="Filter by folder path")
@click.option("--limit", default=10, show_default=True, help="Max results")
@click.option("--cursor", default=None, help="Pagination cursor")
@click.option("--references", default=None, type=click.Choice(["DIRECT", "TRANSITIVE"]), help="Include references")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def list_fragments(path, limit, cursor, references, as_json):
    """List content fragments."""
    cfg = _cfg()
    data = t.list_fragments(cfg, path=path, limit=limit, cursor=cursor, references=references)
    if as_json:
        _print_json(data)
        return
    items = data.get("items", [])
    if not items:
        click.echo("No fragments found.")
        return
    click.echo(f"{'ID':<38}  {'Title':<40}  Path")
    click.echo("-" * 100)
    for f in items:
        click.echo(f"{f.get('id', ''):<38}  {f.get('title', ''):<40}  {f.get('path', '')}")
    cursor_next = data.get("cursor")
    if cursor_next:
        click.echo(f"\nNext page: --cursor {cursor_next}")


@fragments.command("get")
@click.argument("id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def get_fragment(id, as_json):
    """Get a content fragment by ID."""
    cfg = _cfg()
    data = t.get_fragment(cfg, id=id)
    if as_json:
        _print_json(data)
        return
    click.echo(f"ID:     {data.get('id')}")
    click.echo(f"Title:  {data.get('title')}")
    click.echo(f"Path:   {data.get('path')}")
    click.echo(f"Model:  {data.get('model', {}).get('path', '')}")
    click.echo(f"ETag:   {data.get('_etag')}")
    fields = data.get("fields", [])
    if fields:
        click.echo("\nFields:")
        for field in fields:
            click.echo(f"  {field.get('name')}: {field.get('values')}")


@fragments.command("search")
@click.argument("query")
@click.option("--path", default=None, help="Scope search to folder path")
@click.option("--limit", default=10, show_default=True, help="Max results")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def search_fragments(query, path, limit, as_json):
    """Full-text search for content fragments."""
    cfg = _cfg()
    data = t.search_fragments(cfg, query=query, path=path, limit=limit)
    if as_json:
        _print_json(data)
        return
    items = data.get("items", [])
    if not items:
        click.echo("No results.")
        return
    click.echo(f"{'ID':<38}  {'Title':<40}  Path")
    click.echo("-" * 100)
    for f in items:
        click.echo(f"{f.get('id', ''):<38}  {f.get('title', ''):<40}  {f.get('path', '')}")


def _parse_enum_options(raw_values: list) -> list[dict]:
    """Normalise enum entries to {"label": str, "value": str}."""
    options = []
    for v in raw_values:
        if isinstance(v, dict):
            options.append({
                "label": str(v.get("key") or v.get("label") or v.get("value", "")),
                "value": str(v.get("value") or v.get("key", "")),
            })
        else:
            options.append({"label": str(v), "value": str(v)})
    return options


def _prompt_enum(label: str, options: list[dict], required: bool, multiple: bool) -> str | None:
    """Numbered selector for enumeration fields. Returns None if skipped."""
    req_tag = " (required)" if required else " (optional, Enter to skip)"
    multi_tag = "  Select one or more numbers separated by commas." if multiple else ""

    click.echo(f"\n  Field : {label}  [enumeration]{req_tag}")
    for i, opt in enumerate(options, 1):
        click.echo(f"    {i:>2}. {opt['label']}")
    if multi_tag:
        click.echo(multi_tag)

    while True:
        raw = click.prompt("  Choice", default="", show_default=False).strip()
        if not raw:
            if required:
                click.echo("  This field is required.")
                continue
            return None

        try:
            if multiple:
                indices = [int(x.strip()) for x in raw.split(",")]
                if all(1 <= i <= len(options) for i in indices):
                    return ",".join(options[i - 1]["value"] for i in indices)
            else:
                idx = int(raw)
                if 1 <= idx <= len(options):
                    return options[idx - 1]["value"]
        except ValueError:
            pass

        click.echo(f"  Enter a number between 1 and {len(options)}." +
                   (" Separate multiple with commas." if multiple else ""))


def _prompt_field_value(field: dict) -> str | None:
    """Prompt the user for a single model field value. Returns None if skipped."""
    name     = field.get("name", "")
    label    = field.get("label") or name
    ftype    = field.get("fieldType") or field.get("type", "text")
    required = field.get("required", False)
    multiple = field.get("multiple", False)

    raw_values: list = (
        field.get("values")
        or field.get("enumValues")
        or field.get("allowedValues")
        or []
    )

    # Enumeration — numbered selector
    if ftype == "enumeration" and raw_values:
        options = _parse_enum_options(raw_values)
        return _prompt_enum(label, options, required, multiple)

    # Long-text — offer file path or inline
    if ftype == "long-text":
        req_tag = " (required)" if required else " (optional, Enter to skip)"
        click.echo(f"\n  Field : {label}  [long-text / markdown]{req_tag}")
        description = field.get("description", "").strip()
        if description:
            click.echo(f"  Hint  : {description}")
        click.echo("  Provide a file path (recommended for markdown, e.g. ~/guide.md).")
        click.echo("  Pasting multi-line markdown directly may fail due to shell interpretation.")
        while True:
            value = click.prompt("  Value or file path", default="", show_default=False).strip()
            if not value:
                if required:
                    click.echo("  This field is required.")
                    continue
                return None
            try:
                content = _read_markdown_value(value)
            except click.ClickException as exc:
                click.echo(f"  {exc.format_message()}")
                continue
            if content != value:
                click.echo("  Loaded markdown content from file.")
            return content

    # All other types — text prompt with hints and validation
    import re
    req_tag = " (required)" if required else " (optional, Enter to skip)"
    click.echo(f"\n  Field : {label}  [{ftype}]{req_tag}")

    description = field.get("description", "").strip()
    if description:
        click.echo(f"  Hint  : {description}")

    if ftype == "boolean":
        click.echo("  Enter: true or false")
    elif ftype == "content-reference":
        root = field.get("root", "/content/dam").rstrip("/")
        if root != "/content/dam":
            click.echo(f"  Path prefix: {root}/")
            click.echo("  Enter the file name only (e.g. my-logo.svg)")
    elif ftype == "fragment-reference":
        click.echo("  Expected: Content Fragment UUID or path")
    elif ftype in ("date", "date-time"):
        click.echo("  Expected: YYYY-MM-DD  or  YYYY-MM-DDTHH:MM:SSZ")

    max_len = field.get("maxLength") or field.get("maxSize")
    if max_len:
        click.echo(f"  Max length: {max_len} characters")

    regex   = field.get("customValidationRegex", "")
    err_msg = field.get("customErrorMessage", "Invalid value.")

    while True:
        value = click.prompt("  Value", default="", show_default=False).strip()
        if not value:
            if required:
                click.echo("  This field is required.")
                continue
            return None

        if ftype == "boolean" and value.lower() not in ("true", "false"):
            click.echo("  Enter true or false.")
            continue

        if max_len and len(value) > max_len:
            click.echo(f"  Too long — max {max_len} characters (entered {len(value)}).")
            continue

        if regex and not re.match(regex, value):
            click.echo(f"  {err_msg}")
            continue

        # Prepend root prefix for content-reference fields with a specific folder
        if ftype == "content-reference":
            root = field.get("root", "/content/dam").rstrip("/")
            if root != "/content/dam" and not value.startswith("/"):
                value = f"{root}/{value}"

        return value


def _interactive_create(cfg) -> dict:
    """Walk the user through creating a fragment step by step."""
    # ── pick model ────────────────────────────────────────────────────────────
    click.echo("\nFetching available models...")
    models_data = t.list_models(cfg, limit=50)
    model_items = models_data.get("items", [])
    if not model_items:
        raise click.ClickException("No models found on this environment.")

    click.echo("\nAvailable models:")
    for i, m in enumerate(model_items, 1):
        title = m.get("title", "").strip()
        path  = m.get("path", "")
        name  = path.rstrip("/").rsplit("/", 1)[-1]
        label = title if title else name
        click.echo(f"  {i}. {label}")

    while True:
        raw = click.prompt(f"\nSelect model [1-{len(model_items)}]", default="1")
        try:
            idx = int(raw)
            if 1 <= idx <= len(model_items):
                chosen_model = model_items[idx - 1]
                break
        except ValueError:
            pass
        click.echo(f"Please enter a number between 1 and {len(model_items)}.")

    model_path  = chosen_model["path"]
    model_id    = chosen_model.get("id", "")
    model_label = (chosen_model.get("title") or "").strip() or model_path.rstrip("/").rsplit("/", 1)[-1]
    click.echo(f"Model: {model_label}")

    # ── resolve schema: use pre-fetched first, fall back to API ──────────────
    schema_fields: list = environments.MODEL_SCHEMAS.get(model_path, [])
    title_required = True

    if schema_fields:
        click.echo(f"  Schema loaded ({len(schema_fields)} fields, pre-fetched).")
    elif model_id:
        try:
            model_schema = t.get_model(cfg, id=model_id)
            schema_fields = model_schema.get("fields", [])
            title_required = model_schema.get("titleRequired", True)
            click.echo(f"  Schema loaded ({len(schema_fields)} fields, from API).")
        except SystemExit:
            click.echo("  (Could not load schema — proceeding without field validation.)")

    # ── basic fragment details ────────────────────────────────────────────────
    click.echo("")
    default_parent = environments.MODEL_DEFAULTS.get(model_path, "")
    parent_path = click.prompt(
        "Parent folder path",
        default=default_parent if default_parent else None,
        prompt_suffix=" [default shown, Enter to accept]: " if default_parent else ": ",
    )
    while True:
        name = click.prompt("Fragment name (slug, kebab-case)").strip()
        try:
            _validate_slug_or_fail(name, field_label="Fragment name")
            break
        except click.ClickException as exc:
            click.echo(exc.format_message())

    # Title — validate based on schema rules
    title_req_tag = " (required)" if title_required else " (optional, Enter to skip)"
    click.echo(f"\n  Fragment title{title_req_tag}")
    while True:
        title = click.prompt("  Title", default="", show_default=False).strip()
        if not title and title_required:
            click.echo("  Title is required for this model.")
            continue
        break

    # ── field prompts ─────────────────────────────────────────────────────────
    fields_list: list = []
    if schema_fields:
        click.echo(f"\nEnter values for {len(schema_fields)} field(s):")
        for field in schema_fields:
            value = _prompt_field_value(field)
            if value is None:
                continue
            ftype    = field.get("fieldType") or field.get("type", "text")
            multiple = field.get("multiple", False)
            # Multi-value fields come back as comma-separated string from _prompt_enum
            values_list = [v.strip() for v in value.split(",")] if multiple else [value]
            entry = {"name": field["name"], "type": ftype, "values": values_list}
            # Preserve mimeType for long-text fields
            if ftype == "long-text" and field.get("mimeType"):
                entry["mimeType"] = field["mimeType"]
            fields_list.append(entry)
    elif not model_id:
        click.echo("  (No schema available — fragment will be created without initial field values.)")

    return {
        "parentPath": parent_path,
        "modelPath":  model_path,
        "modelId":    model_id,
        "name":       name,
        "title":      title or None,
        "fields":     fields_list or None,
    }


def _build_fields_from_args(cfg: dict, field_args: tuple, model_path: str, *, require_all_required: bool) -> list:
    """Convert -f name=value flags to validated API fields array."""
    raw_fields = []
    for arg in field_args:
        if "=" not in arg:
            raise click.ClickException(f"Invalid -f/--field value '{arg}'. Expected NAME=VALUE.")
        name, _, value = arg.partition("=")
        name = name.strip()
        value = value.strip().strip("'\"")
        raw_fields.append({"name": name, "values": [value]})

    return _normalize_and_validate_fields(
        cfg,
        model_path,
        raw_fields,
        require_all_required=require_all_required,
    )


@fragments.command("create")
@click.option("-i", "--interactive", "interactive", is_flag=True, help="Prompt for each value interactively")
@click.option("--parent-path", default=None, help="Parent folder path")
@click.option("--model-path",  default=None, help="Content Fragment Model path")
@click.option("--name",        default=None, help="Fragment name (slug)")
@click.option("--title",       default=None, help="Fragment title")
@click.option("-f", "--field", "field_args", multiple=True, metavar="NAME=VALUE",
              help="Field value as name=value. Repeatable. Multi-value: comma-separate values.")
@click.option("--fields",      default=None, help="Fields as a raw JSON array (advanced)")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def create_fragment(interactive, parent_path, model_path, name, field_args, fields, title, as_json):
    """Create a content fragment.

    Three modes:\n
      -i                   Interactive guided prompts.\n
      -f name=value        Simple key=value flags (type inferred from schema).\n
      --fields '[...]'     Raw JSON array (advanced).
    """
    cfg = _cfg()

    if interactive:
        params = _interactive_create(cfg)
        model_path = params.get("modelPath", "")
        _validate_slug_or_fail(params.get("name", ""), field_label="Fragment name")
        params["fields"] = _normalize_and_validate_fields(
            cfg,
            model_path,
            params.get("fields") or [],
            require_all_required=True,
        )
    else:
        if not parent_path or not model_path or not name:
            raise click.UsageError(
                "Requires --parent-path, --model-path, and --name. Use -i for interactive mode."
            )
        _validate_slug_or_fail(name, field_label="Fragment name")
        import base64
        model_id_enc = base64.urlsafe_b64encode(model_path.encode()).decode().rstrip("=")

        if field_args:
            parsed_fields = _build_fields_from_args(
                cfg,
                field_args,
                model_path,
                require_all_required=True,
            )
        elif fields:
            try:
                parsed_fields = json.loads(fields)
            except json.JSONDecodeError as e:
                raise click.ClickException(f"Invalid JSON for --fields: {e}")
            if not isinstance(parsed_fields, list):
                raise click.ClickException("--fields must be a JSON array of field objects.")
            parsed_fields = _normalize_and_validate_fields(
                cfg,
                model_path,
                parsed_fields,
                require_all_required=True,
            )
        else:
            parsed_fields = _normalize_and_validate_fields(
                cfg,
                model_path,
                [],
                require_all_required=True,
            )

        params = {
            "parentPath": parent_path,
            "modelId":    model_id_enc,
            "name":       name,
            "title":      title,
            "fields":     parsed_fields,
        }

    params.pop("modelPath", None)

    # Duplicate-slug guard: search AEM before writing.
    slug_entry = next((f for f in (params.get("fields") or []) if f.get("name") == "slug"), None)
    if slug_entry and slug_entry.get("values"):
        _check_duplicate_slug(
            cfg,
            slug_entry["values"][0],
            model_path,
            params.get("parentPath", ""),
        )

    data = t.create_fragment(cfg, **params)
    if as_json:
        _print_json(data)
        return
    click.echo(f"\nCreated: {data.get('id')}")
    click.echo(f"Path:    {data.get('path')}")


@fragments.command("update")
@click.argument("id")
@click.option("--title",  default=None, help="New fragment title")
@click.option("-f", "--field", "field_args", multiple=True, metavar="NAME=VALUE",
              help="Field value as name=value. Repeatable. Multi-value: comma-separate.")
@click.option("--patch",  default=None, help="Raw JSON Patch array (advanced)")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def update_fragment(id, title, field_args, patch, as_json):
    """Update a content fragment.

    Examples:\n
      --title \"New Title\"\n
      -f slug=\"new-slug\" -f description=\"New desc.\"\n
      --patch '[{\"op\":\"replace\",\"path\":\"/title\",\"value\":\"...\"}]'
    """
    cfg = _cfg()
    fragment = t.get_fragment(cfg, id=id)
    etag = fragment.get("_etag")
    if not etag:
        raise click.ClickException("Could not retrieve ETag for fragment.")

    patch_ops = []
    model_path = fragment.get("model", {}).get("path", "")
    schema_fields = _model_schema_fields(cfg, model_path)
    schema = _schema_map(schema_fields)
    fragment_fields = fragment.get("fields", [])
    field_index = {f.get("name"): i for i, f in enumerate(fragment_fields) if f.get("name")}
    effective_values = {
        f.get("name"): [str(v) for v in (f.get("values") or [])]
        for f in fragment_fields
        if f.get("name")
    }

    if title:
        patch_ops.append({"op": "replace", "path": "/title", "value": title})

    if field_args:
        normalized_fields = _build_fields_from_args(
            cfg,
            field_args,
            model_path,
            require_all_required=False,
        )
        # Duplicate-slug guard: only fires when the slug field is being changed.
        for entry in normalized_fields:
            if entry["name"] == "slug" and entry.get("values"):
                frag_parent = "/".join(fragment.get("path", "").rstrip("/").split("/")[:-1])
                _check_duplicate_slug(
                    cfg,
                    entry["values"][0],
                    model_path,
                    frag_parent,
                    exclude_fragment_id=id,
                )
        for entry in normalized_fields:
            name = entry["name"]
            if name in field_index:
                idx = field_index[name]
                patch_ops.append({"op": "replace", "path": f"/fields/{idx}/values", "value": entry["values"]})
            else:
                patch_ops.append({"op": "add", "path": "/fields/-", "value": entry})
            effective_values[name] = entry["values"]

    if patch:
        try:
            raw_patch_ops = json.loads(patch)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON for --patch: {e}")
        if not isinstance(raw_patch_ops, list):
            raise click.ClickException("--patch must be a JSON array of patch operations.")

        for op in raw_patch_ops:
            if not isinstance(op, dict):
                raise click.ClickException("Each --patch operation must be a JSON object.")
            op_type = op.get("op")
            path = op.get("path", "")
            if op_type not in ("add", "replace", "remove", "move", "copy", "test"):
                raise click.ClickException(f"Invalid patch operation '{op_type}'.")

            match_values = re.match(r"^/fields/(\d+)/values$", path)
            match_field = re.match(r"^/fields/(\d+)$", path)

            if path == "/fields/-" and op_type == "add":
                value = op.get("value")
                if not isinstance(value, dict):
                    raise click.ClickException("Patch add at /fields/- must include a field object value.")
                normalized = _normalize_and_validate_fields(
                    cfg,
                    model_path,
                    [value],
                    require_all_required=False,
                )[0]
                op = {**op, "value": normalized}
                effective_values[normalized["name"]] = normalized["values"]

            elif match_values and op_type in ("add", "replace"):
                idx = int(match_values.group(1))
                if idx >= len(fragment_fields):
                    raise click.ClickException(f"Patch path '{path}' references unknown field index.")
                field_name = fragment_fields[idx].get("name", "")
                if field_name not in schema:
                    raise click.ClickException(f"Unknown model field in patch index {idx}: '{field_name}'.")
                raw_values = op.get("value")
                if isinstance(raw_values, list):
                    values_input = [str(v) for v in raw_values]
                elif raw_values is None:
                    values_input = []
                else:
                    values_input = [str(raw_values)]
                normalized_values = _validate_field_values(cfg, schema[field_name], values_input)
                op = {**op, "value": normalized_values}
                effective_values[field_name] = normalized_values

            elif match_values and op_type == "remove":
                idx = int(match_values.group(1))
                if idx >= len(fragment_fields):
                    raise click.ClickException(f"Patch path '{path}' references unknown field index.")
                field_name = fragment_fields[idx].get("name", "")
                effective_values[field_name] = []

            elif match_field and op_type in ("add", "replace"):
                value = op.get("value")
                if not isinstance(value, dict):
                    raise click.ClickException(f"Patch operation for '{path}' must include a field object value.")
                normalized = _normalize_and_validate_fields(
                    cfg,
                    model_path,
                    [value],
                    require_all_required=False,
                )[0]
                op = {**op, "value": normalized}
                effective_values[normalized["name"]] = normalized["values"]

            elif match_field and op_type == "remove":
                idx = int(match_field.group(1))
                if idx >= len(fragment_fields):
                    raise click.ClickException(f"Patch path '{path}' references unknown field index.")
                field_name = fragment_fields[idx].get("name", "")
                if field_name:
                    effective_values.pop(field_name, None)

            patch_ops.append(op)

    _validate_cross_field_rules(effective_values, model_path)

    if not patch_ops:
        raise click.UsageError("Provide at least one of: --title, -f name=value, or --patch.")

    data = t.update_fragment(cfg, id=id, etag=etag, patch_operations=patch_ops)
    if as_json:
        _print_json(data)
        return
    click.echo(f"Updated: {data.get('id')}  {data.get('path')}")


@fragments.command("validate")
@click.option("--model-path", required=True, help="Content Fragment Model path")
@click.option("--name", default=None, help="Fragment name (slug) to validate")
@click.option("-f", "--field", "field_args", multiple=True, metavar="NAME=VALUE",
              help="Field value as name=value. Repeatable. Multi-value: comma-separate.")
@click.option("--fields", default=None, help="Fields as a raw JSON array (advanced)")
@click.option("--partial", is_flag=True,
              help="Allow partial payloads (skip required-field completeness checks).")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def validate_fragment_payload(model_path, name, field_args, fields, partial, as_json):
    """Dry-run validate fragment payload against model rules without writing to AEM."""
    cfg = _cfg()

    if name:
        _validate_slug_or_fail(name, field_label="Fragment name")

    if field_args and fields:
        raise click.UsageError("Use either -f/--field or --fields, not both.")

    if field_args:
        normalized_fields = _build_fields_from_args(
            cfg,
            field_args,
            model_path,
            require_all_required=not partial,
        )
    elif fields:
        try:
            parsed_fields = json.loads(fields)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON for --fields: {e}")
        if not isinstance(parsed_fields, list):
            raise click.ClickException("--fields must be a JSON array of field objects.")
        normalized_fields = _normalize_and_validate_fields(
            cfg,
            model_path,
            parsed_fields,
            require_all_required=not partial,
        )
    else:
        normalized_fields = _normalize_and_validate_fields(
            cfg,
            model_path,
            [],
            require_all_required=not partial,
        )

    result = {
        "status": "ok",
        "modelPath": model_path,
        "name": name,
        "partial": partial,
        "fields": normalized_fields,
    }
    if as_json:
        _print_json(result)
        return

    click.echo("Validation passed.")
    click.echo(f"Model:  {model_path}")
    if name:
        click.echo(f"Name:   {name}")
    click.echo(f"Fields: {len(normalized_fields)}")
    if partial:
        click.echo("Mode:   partial (required-field completeness not enforced)")


@fragments.command("delete")
@click.argument("id")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def delete_fragment(id, yes):
    """Delete a content fragment (auto-fetches ETag)."""
    cfg = _cfg()
    if not yes:
        click.confirm(f"Delete fragment {id}?", abort=True)
    fragment = t.get_fragment(cfg, id=id)
    etag = fragment.get("_etag")
    if not etag:
        raise click.ClickException("Could not retrieve ETag for fragment.")
    t.delete_fragment(cfg, id=id, etag=etag)
    click.echo(f"Deleted: {id}")


@fragments.command("publish")
@click.argument("ids", nargs=-1, required=True)
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def publish_fragments(ids, as_json):
    """Publish one or more content fragments by ID."""
    cfg = _cfg()
    data = t.publish_fragments(cfg, ids=list(ids))
    if as_json:
        _print_json(data)
        return
    click.echo(f"Published {len(ids)} fragment(s).")


@fragments.command("copy")
@click.argument("id")
@click.option("--destination", required=True, help="Destination folder path")
@click.option("--deep", is_flag=True, help="Deep copy including referenced fragments")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def copy_fragment(id, destination, deep, as_json):
    """Copy a content fragment to a new location."""
    cfg = _cfg()
    data = t.copy_fragment(cfg, id=id, destination_path=destination, deep=deep)
    if as_json:
        _print_json(data)
        return
    click.echo(f"Copied to: {data.get('path')}")


@fragments.command("variations")
@click.argument("id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def list_variations(id, as_json):
    """List variations of a content fragment."""
    cfg = _cfg()
    data = t.list_variations(cfg, fragment_id=id)
    if as_json:
        _print_json(data)
        return
    items = data.get("items", [])
    if not items:
        click.echo("No variations found.")
        return
    click.echo(f"{'Name':<30}  Title")
    click.echo("-" * 60)
    for v in items:
        click.echo(f"{v.get('name', ''):<30}  {v.get('title', '')}")


# ── models group ───────────────────────────────────────────────────────────────

@cli.group()
def models():
    """Manage Content Fragment Models."""


@models.command("list")
@click.option("--path", default=None, help="Filter by folder path")
@click.option("--limit", default=10, show_default=True, help="Max results")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def list_models(path, limit, as_json):
    """List available Content Fragment Models."""
    cfg = _cfg()
    data = t.list_models(cfg, path=path, limit=limit)
    if as_json:
        _print_json(data)
        return
    items = data.get("items", [])
    if not items:
        click.echo("No models found.")
        return
    click.echo(f"{'Title':<40}  Path")
    click.echo("-" * 80)
    for m in items:
        click.echo(f"{m.get('title', ''):<40}  {m.get('path', '')}")
