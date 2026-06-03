"""CLI entry point for cf-agent."""

import json

import click

from . import auth, config, environments
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
            # Treat as a file path if it looks like one (strip accidental surrounding quotes)
            from pathlib import Path
            candidate = Path(value.strip("'\"")).expanduser()
            if candidate.exists() and candidate.is_file():
                content = candidate.read_text(encoding="utf-8")
                click.echo(f"  Read {len(content)} characters from {candidate}")
                return content
            # Otherwise treat as inline content
            return value

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
    name = click.prompt("Fragment name (slug, no spaces)")

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
        "modelId":    model_id,
        "name":       name,
        "title":      title or None,
        "fields":     fields_list or None,
    }


def _build_fields_from_args(field_args: tuple, model_path: str) -> list:
    """Convert -f name=value flags to the API fields array using pre-fetched schema for types."""
    from pathlib import Path

    schema_map = {f["name"]: f for f in environments.MODEL_SCHEMAS.get(model_path, [])}
    fields_list = []
    for arg in field_args:
        name, _, value = arg.partition("=")
        name  = name.strip()
        value = value.strip().strip("'\"")
        field_def = schema_map.get(name, {})
        ftype    = field_def.get("type", "text")
        multiple = field_def.get("multiple", False)

        # For long-text fields, treat value as a file path if the file exists
        if ftype == "long-text":
            candidate = Path(value).expanduser()
            if candidate.exists() and candidate.is_file():
                value = candidate.read_text(encoding="utf-8")

        # Apply path prefix for content-reference fields with a specific folder
        elif ftype == "content-reference":
            root = field_def.get("root", "/content/dam").rstrip("/")
            if root != "/content/dam" and not value.startswith("/"):
                value = f"{root}/{value}"

        values_list = [v.strip() for v in value.split(",")] if multiple else [value]
        entry = {"name": name, "type": ftype, "values": values_list}
        if ftype == "long-text" and field_def.get("mimeType"):
            entry["mimeType"] = field_def["mimeType"]
        fields_list.append(entry)
    return fields_list


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
    else:
        if not parent_path or not model_path or not name:
            raise click.UsageError(
                "Requires --parent-path, --model-path, and --name. Use -i for interactive mode."
            )
        import base64
        model_id_enc = base64.urlsafe_b64encode(model_path.encode()).decode().rstrip("=")

        if field_args:
            parsed_fields = _build_fields_from_args(field_args, model_path)
        elif fields:
            try:
                parsed_fields = json.loads(fields)
            except json.JSONDecodeError as e:
                raise click.ClickException(f"Invalid JSON for --fields: {e}")
        else:
            parsed_fields = None

        params = {
            "parentPath": parent_path,
            "modelId":    model_id_enc,
            "name":       name,
            "title":      title,
            "fields":     parsed_fields,
        }

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

    if title:
        patch_ops.append({"op": "replace", "path": "/title", "value": title})

    if field_args:
        from pathlib import Path as _Path
        model_path   = fragment.get("model", {}).get("path", "")
        schema_map   = {f["name"]: f for f in environments.MODEL_SCHEMAS.get(model_path, [])}
        field_index  = {f["name"]: i for i, f in enumerate(fragment.get("fields", []))}

        for arg in field_args:
            name, _, value = arg.partition("=")
            name  = name.strip()
            value = value.strip().strip("'\"")
            field_def = schema_map.get(name, {})
            ftype     = field_def.get("type", "text")
            multiple  = field_def.get("multiple", False)

            if ftype == "long-text":
                candidate = _Path(value).expanduser()
                if candidate.exists() and candidate.is_file():
                    value = candidate.read_text(encoding="utf-8")
            elif ftype == "content-reference":
                root = field_def.get("root", "/content/dam").rstrip("/")
                if root != "/content/dam" and not value.startswith("/"):
                    value = f"{root}/{value}"

            values_list = [v.strip() for v in value.split(",")] if multiple else [value]

            if name in field_index:
                idx = field_index[name]
                patch_ops.append({"op": "replace", "path": f"/fields/{idx}/values", "value": values_list})
            else:
                entry = {"name": name, "type": ftype, "values": values_list}
                if ftype == "long-text" and field_def.get("mimeType"):
                    entry["mimeType"] = field_def["mimeType"]
                patch_ops.append({"op": "add", "path": "/fields/-", "value": entry})

    if patch:
        try:
            patch_ops.extend(json.loads(patch))
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON for --patch: {e}")

    if not patch_ops:
        raise click.UsageError("Provide at least one of: --title, -f name=value, or --patch.")

    data = t.update_fragment(cfg, id=id, etag=etag, patch_operations=patch_ops)
    if as_json:
        _print_json(data)
        return
    click.echo(f"Updated: {data.get('id')}  {data.get('path')}")


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
