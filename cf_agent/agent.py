"""CLI entry point for cf-agent."""

import contextlib
import itertools
import json
import re
import sys
import threading
import time
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


# ── output styling ──────────────────────────────────────────────────────────────
# click.secho / click.style automatically strip ANSI colour codes when the output
# is not a TTY (pipes, redirects, --json), so these are safe to use unconditionally.

def _header(msg: str) -> None:
    """Section header — bold cyan."""
    click.secho(msg, fg="cyan", bold=True)


def _success(msg: str) -> None:
    """Success / confirmation — bold green."""
    click.secho(msg, fg="green", bold=True)


def _failure(msg: str) -> None:
    """Validation error / failure — red."""
    click.secho(msg, fg="red")


def _hint(msg: str) -> None:
    """Secondary guidance / hint — dim grey."""
    click.secho(msg, fg="bright_black")


# ── interactive navigation ────────────────────────────────────────────────────
# Typing one of these at any interactive prompt goes back to the previous step.
# A ":" prefix is used so it can never collide with a real field value.
BACK_WORDS = {":back", ":b"}


class _Sentinel:
    """Distinct non-string return values for interactive prompts."""

    def __init__(self, label: str):
        self._label = label

    def __repr__(self) -> str:
        return self._label


BACK = _Sentinel("<BACK>")  # user wants the previous step
SKIP = _Sentinel("<SKIP>")  # step doesn't apply (e.g. uuid when not INSTALLABLE)


def _is_back(raw) -> bool:
    return isinstance(raw, str) and raw.strip().lower() in BACK_WORDS


def _back_hint() -> None:
    _hint("  (type :back to return to the previous step)")


@contextlib.contextmanager
def _spinner(message: str):
    """Animated spinner shown on stderr while a slow (usually network) block runs.

    On a non-TTY (piped output, CI, tests) it degrades to printing the message
    once with no animation, so logs stay clean. The spinner line is cleared on
    exit whether the block returns or raises, so any error/prompt that follows
    starts on a fresh line.
    """
    stream = sys.stderr
    if not stream.isatty():
        click.echo(message, err=True)
        yield
        return

    done = threading.Event()
    frames = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def _spin():
        while not done.is_set():
            stream.write("\r" + click.style(next(frames), fg="cyan") + " " + message)
            stream.flush()
            time.sleep(0.08)

    worker = threading.Thread(target=_spin, daemon=True)
    worker.start()
    try:
        yield
    finally:
        done.set()
        worker.join()
        stream.write("\r" + " " * (len(message) + 4) + "\r")
        stream.flush()


def _field_heading(label: str, ftype: str, required: bool) -> None:
    """Consistent, coloured field prompt heading."""
    tag = (
        click.style(" (required)", fg="yellow")
        if required
        else click.style(" (optional, Enter to skip)", fg="bright_black")
    )
    click.echo(
        "\n  "
        + click.style("Field : ", fg="cyan", bold=True)
        + click.style(label, bold=True)
        + click.style(f"  [{ftype}]", fg="blue")
        + tag
    )


def _looks_like_file_path(value: str) -> bool:
    """Return True when a long-text input looks like a file path."""
    # Multi-line input is markdown content, never a path — this also prevents
    # already-loaded content (e.g. a guide containing /content/dam/ image links)
    # from being mistaken for a path when re-checked during validation.
    if "\n" in value:
        return False
    return (
        value.startswith(("~/", "./", "../", "/"))
        or "/" in value
        or "\\" in value
    )


def _read_markdown_value(value: str) -> str:
    """Read long-text content from a file when the value is a path; otherwise return the
    value unchanged (inline markdown).

    Path-likeness is checked FIRST so already-loaded, multi-line content is never probed
    against the filesystem — calling Path(huge_string).exists() raises OSError('File name
    too long'). The filesystem probe is also guarded so an over-long single-line value is
    treated as inline content instead of crashing.
    """
    if not _looks_like_file_path(value):
        return value

    not_found = click.ClickException(
        f"Long-text value looks like a file path but file was not found/readable: {value}"
    )

    try:
        # expanduser() raises RuntimeError/ValueError for a bad "~user" — e.g. "~Documents/…"
        # is read as user 'Documents', which has no home dir. A mistyped path should be a
        # clean "not found" error, not a crash.
        candidate = Path(value.strip("'\"")).expanduser()
    except (RuntimeError, ValueError):
        raise not_found

    try:
        if candidate.exists() and candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    except OSError:
        return value  # not a usable path (e.g. too long) — treat as inline content
    raise not_found  # reuse the same clear "file not found" message


# NOTE: content_type ("connector"/"plugin") is a model-level constant — the model's
# hidden content_type field carries a fixed value and the marketplace GraphQL derives
# it automatically from the chosen model. It is NOT a per-fragment field and must not
# be sent in the create payload (AEM rejects unknown fields). No CLI action needed.


def _encode_model_id(model_path: str) -> str:
    """Model IDs are the base64url-encoding of the model path (no padding)."""
    import base64
    return base64.urlsafe_b64encode(model_path.encode()).decode().rstrip("=")


# Per-process cache so a single command fetches each model schema at most once.
_SCHEMA_CACHE: dict[str, list[dict]] = {}


def _model_schema_fields(cfg: dict, model_path: str) -> list[dict]:
    """Fetch model schema fields live from AEM — the single source of truth.

    The AEM Content Fragment Model carries every validation rule (required,
    maxLength, customValidationRegex, enum values, content-reference root,
    long-text mimeType). The CLI validates against this directly rather than
    maintaining its own copy, so a rule change in AEM needs no CLI change.
    """
    if model_path in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[model_path]

    model_id = _encode_model_id(model_path)
    try:
        model_schema = t.get_model(cfg, id=model_id)
    except SystemExit as exc:
        raise click.ClickException(
            f"Unable to load model schema from AEM for '{model_path}': {exc}"
        )
    fields = model_schema.get("fields", [])
    _SCHEMA_CACHE[model_path] = fields
    return fields


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


def _extract_dam_asset_refs(markdown_text: str) -> list[str]:
    """Return the AEM DAM asset paths (/content/dam/...) referenced in markdown.

    Covers markdown images/links — ``![alt](target)`` and ``[text](target)`` —
    and HTML ``<img src="...">``. Only references that resolve to a DAM path are
    returned; relative paths and non-AEM external URLs are ignored (we can't
    verify those against the DAM). Full AEM URLs are accepted — the path is
    extracted from the ``/content/dam/`` segment onward.
    """
    candidates: list[str] = []
    # Markdown images and links: the optional leading '!' covers both.
    for m in re.finditer(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)", markdown_text):
        candidates.append(m.group(1))
    # HTML <img src="..."> / src='...'
    for m in re.finditer(r"""<img[^>]*\bsrc\s*=\s*["']([^"']+)["']""", markdown_text, re.IGNORECASE):
        candidates.append(m.group(1))

    dam_paths: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        raw = raw.strip().strip("<>")
        idx = raw.find("/content/dam/")
        if idx == -1:
            continue
        # Take from /content/dam/ onward, dropping any trailing title/anchor/query.
        path = raw[idx:].split()[0].split("#")[0].split("?")[0]
        if path not in seen:
            seen.add(path)
            dam_paths.append(path)
    return dam_paths


def _validate_markdown_asset_refs(cfg: dict, markdown_text: str) -> list[str]:
    """Return the referenced DAM assets that are missing from AEM (empty if all exist)."""
    return [p for p in _extract_dam_asset_refs(markdown_text) if not _asset_exists(cfg, p)]


def _validate_single_value(cfg: dict, field_def: dict, value: str) -> str:
    """Apply model-level validation rules and return normalized value."""
    name = field_def.get("name", "")
    ftype = field_def.get("fieldType") or field_def.get("type", "text")
    max_len = field_def.get("maxLength") or field_def.get("maxSize")
    regex = field_def.get("customValidationRegex", "")
    err_msg = field_def.get("customErrorMessage", f"Invalid value for '{name}'.")

    if ftype == "long-text":
        value = _read_markdown_value(value)
        missing = _validate_markdown_asset_refs(cfg, value)
        if missing:
            listed = "\n  - ".join(missing)
            raise click.ClickException(
                f"Field '{name}' references AEM asset(s) that do not exist in the DAM:\n  - {listed}"
            )

    if ftype == "boolean" and value.lower() not in ("true", "false"):
        raise click.ClickException(f"Field '{name}' expects true or false.")

    if max_len and len(value) > int(max_len):
        raise click.ClickException(
            f"Field '{name}' exceeds max length {max_len} (got {len(value)})."
        )

    if name in ANY_URL_FIELDS:
        # Accept any valid URL, overriding the model's stricter host-whitelist regex.
        if value and not _ANY_URL_RE.match(value):
            raise click.ClickException(
                f"Field '{name}' must be a valid URL (e.g. https://example.com/watch)."
            )
    elif regex and not re.match(regex, value):
        raise click.ClickException(f"Field '{name}': {err_msg}")

    if ftype == "content-reference":
        root = _effective_content_root(field_def)
        if root != "/content/dam" and not value.startswith("/"):
            value = f"{root}/{value}"
        if not _asset_exists(cfg, value):
            raise click.ClickException(f"Referenced asset does not exist in AEM: {value}")

    return value


# Fields removed from the marketplace models but possibly still present on a not-yet-
# redeployed environment. The CLI treats them as gone: never prompted, and dropped if
# supplied via -f (so no stale data is written). Once every environment is redeployed
# without these fields this set can be emptied.
DEPRECATED_FIELDS = {"redirects"}

# The installation-UUID field was renamed installation_uuid -> installation_asset_uuid.
# Until the model rename is deployed everywhere, the CLI treats both names as the same
# logical field (existing data / un-redeployed envs use the old name).
INSTALLATION_UUID_FIELDS = ("installation_asset_uuid", "installation_uuid")

# Fields the CLI requires at CREATE time (only if the model actually has them), beyond
# what the model marks required. A content guide is mandatory for every new connector /
# plugin. This is create-only — updates never force it (a fragment already has one).
REQUIRED_ON_CREATE = {"content_guide"}

# Fields that accept ANY valid URL (product decision) — the CLI overrides a stricter
# host-whitelist regex the model may still carry until it is redeployed. `video` used to
# be locked to specific hosts (Vimeo/YouTube/…); it can now be any valid URL.
ANY_URL_FIELDS = {"video"}
_ANY_URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


# Enum fields where the CLI also accepts NEW free-text values, even though the AEM
# authoring UI is pick-only. The model enum supplies the suggestion list; new values
# must be Title Case (matching the curated list's casing).
FREE_TEXT_ENUM_FIELDS = {"solution_tags"}
_TITLE_CASE_TAG = re.compile(r"^[A-Z0-9][A-Za-z0-9]*([ &/-]+[A-Z0-9][A-Za-z0-9]*)*$")


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
        if name in FREE_TEXT_ENUM_FIELDS:
            # Allow values outside the list, but enforce the curated Title Case format.
            bad = [v for v in normalized if not _TITLE_CASE_TAG.match(v)]
            if bad:
                raise click.ClickException(
                    f"Field '{name}': new tags must be Title Case "
                    f"(e.g. 'Retail Store Services'). Invalid: {', '.join(bad)}."
                )
        else:
            allowed = _enum_allowed_values(field_def)
            invalid = [v for v in normalized if v not in allowed]
            if invalid:
                invalid_list = ", ".join(invalid)
                raise click.ClickException(
                    f"Field '{name}' has invalid option(s): {invalid_list}."
                )

    return [_validate_single_value(cfg, field_def, v) for v in normalized]


# System/brand slug tokens that are common English words — skipped in the
# name-contains-system check to avoid false positives (e.g. google-calendar).
_GENERIC_SYSTEM_TOKENS = {
    "calendar", "drive", "box", "cloud", "graph", "workspace", "service",
    "desk", "power", "automate", "one", "entra", "intune", "sharepoint",
    "app", "digitalworkplace",
}


def _validate_cross_field_rules(
    field_values: dict[str, list[str]], model_path: str, *, is_create: bool = False
):
    """Validate business rules spanning multiple fields.

    AEM's model API has no cross-field validation, so these rules are enforced
    only here (CLI / API path) and, for the UI path, in the authoring behavior
    JS clientlib. Keep the two in sync.

    Relationship rules (slug↔systems, name↔systems) apply only at creation —
    slug and systems are immutable afterward, so re-checking them on update would
    wrongly block edits to legacy fragments.
    """
    availability = (field_values.get("availability") or [""])[0]
    # Accept either field name so this works before AND after the installation_uuid ->
    # installation_asset_uuid model rename is deployed (existing fragments store the old
    # name; a redeployed model uses the new one).
    install_uuid = ""
    for _k in INSTALLATION_UUID_FIELDS:
        if field_values.get(_k):
            install_uuid = field_values[_k][0]
            break

    if availability == "INSTALLABLE":
        if not install_uuid:
            raise click.ClickException(
                "installation_asset_uuid is required when availability is INSTALLABLE."
            )
    elif availability in ("VALIDATED", "IDEA", "BUILT_IN"):
        if install_uuid:
            raise click.ClickException(
                f"installation_asset_uuid must not be set when availability is {availability} "
                "(only INSTALLABLE assets may carry an installation UUID)."
            )

    systems = [s for s in (field_values.get("systems") or []) if s]
    slug = (field_values.get("slug") or [""])[0]
    name = (field_values.get("marketplace_name") or [""])[0]

    # Plugin-only relationship rules (connectors carry no `systems`).
    if is_create and systems:
        # slug must start with one of the plugin's systems, e.g. workday-request-time-off
        if slug and not any(slug == s or slug.startswith(s + "-") for s in systems):
            raise click.ClickException(
                f"Plugin slug '{slug}' must start with one of its systems "
                f"({', '.join(systems)}) — e.g. '{systems[0]}-<action>'."
            )
        # marketplace_name should describe the action, not the system (warn — heuristic)
        if name:
            nl = " " + re.sub(r"[^a-z0-9 ]", " ", name.lower()) + " "
            sys_tokens = {
                w for s in systems for w in s.split("-")
                if len(w) >= 3 and w not in _GENERIC_SYSTEM_TOKENS
            }
            hit = next((w for w in sys_tokens if f" {w} " in nl), None)
            if hit:
                click.echo(
                    f"Warning: marketplace_name '{name}' contains the system name "
                    f"'{hit}'. Plugin titles should describe the action, not the system."
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
        if name in DEPRECATED_FIELDS:
            continue  # removed field — silently drop so nothing stale is sent
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
        required_names = {f.get("name") for f in schema_fields if f.get("required")}
        # Fields the CLI additionally requires on create (only those the model actually has).
        required_names |= {n for n in REQUIRED_ON_CREATE if n in schema}
        missing = [n for n in required_names if not by_name.get(n)]
        if missing:
            raise click.ClickException(
                f"Missing required field(s): {', '.join(sorted(missing))}."
            )

    _validate_cross_field_rules(by_name, model_path, is_create=require_all_required)
    return normalized


# Fields that must never be changed once a fragment exists — identity fields. Availability
# IS editable on update (a plugin can move e.g. IDEA -> INSTALLABLE); the availability↔UUID
# interdependency is handled in the edit loop and the cross-field rules.
IMMUTABLE_ON_UPDATE = {"slug", "systems"}

# Fields a user may edit on update, per the marketplace guide journeys (keyed by model
# name). Anything not listed is locked on update — the interactive loop won't offer it
# and -f/--patch on it is rejected. `installation_asset_uuid` applies once the model
# rename is deployed (on an un-redeployed env the field is still `installation_uuid`).
EDITABLE_ON_UPDATE = {
    "marketplace-plugin": {
        "marketplace_name", "description", "purple_chat_link",
        "solution_tags", "content_guide", "availability",
        *INSTALLATION_UUID_FIELDS,  # both names during the rename transition
    },
    "marketplace-connector": {"description", "logo", "content_guide"},
}


def _editable_fields_for(model_path: str) -> set | None:
    """Return the journey's editable field set for a model, or None if the model is
    unknown (in which case no allow-list is enforced)."""
    name = model_path.rstrip("/").rsplit("/", 1)[-1]
    return EDITABLE_ON_UPDATE.get(name)


def _validate_slug_or_fail(slug: str, *, field_label: str = "slug"):
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", slug or ""):
        raise click.ClickException(
            f"{field_label} must be lowercase kebab-case (e.g. my-plugin-name)."
        )


def _folder_for_model(model_path: str, search_folder: str = "") -> str:
    return search_folder.rstrip("/") or environments.MODEL_DEFAULTS.get(model_path, "")


def _fragment_json_url(cfg: dict, fragment_id: str) -> str:
    """The CF Sites API URL that returns this fragment's JSON.

    Requires the OAuth bearer token (not browser-clickable) — it's the same endpoint
    the CLI calls, handy as a curl target or for reference.
    """
    base = (cfg.get("ADOBE_SITES_API_BASE_URL") or "").rstrip("/")
    return f"{base}/cf/fragments/{fragment_id}" if base and fragment_id else ""


def _emit_fragment_json_url(cfg: dict, fragment_id: str) -> None:
    """Print the fragment's JSON URL (+ a note that it needs the token)."""
    url = _fragment_json_url(cfg, fragment_id)
    if not url:
        return
    click.echo(click.style("JSON:    ", fg="green", bold=True) + url)
    _hint(f"  (needs your token — or run: cf-agent fragments get {fragment_id} --json)")


def _check_duplicate_slug(
    cfg: dict,
    slug: str,
    model_path: str,
    search_folder: str = "",
    *,
    exclude_fragment_id: str = "",
) -> None:
    """Raise ClickException if a fragment already exists at this slug in the model's folder.

    Single targeted call, no folder scan: GET /cf/fragments?path=<folder>/<slug>. Given a
    full asset path, the CF list endpoint returns just that fragment (items=[…]) or nothing
    (items=[]). The fragment's JCR node name equals its slug in this system, so an exact
    path match is an exact slug match. A listing failure fails open — AEM still rejects a
    duplicate node name at create time as the final backstop.
    """
    folder = _folder_for_model(model_path, search_folder)
    if not folder:
        return

    frag_path = f"{folder}/{slug}"
    try:
        results = t.list_fragments(cfg, path=frag_path, limit=5)
    except (Exception, SystemExit):
        return

    for fragment in results.get("items", []):
        if exclude_fragment_id and fragment.get("id") == exclude_fragment_id:
            continue
        if fragment.get("path", "").rstrip("/") == frag_path:
            raise click.ClickException(
                f"Slug '{slug}' is already in use by an existing fragment: {frag_path}\n"
                "Choose a unique slug."
            )


def _check_logo_unique(cfg: dict, logo_path: str, folder: str, *, exclude_id: str = "") -> None:
    """Raise if another fragment in `folder` already uses this logo (one-to-one mapping).

    Scans the folder once and compares each fragment's `logo` value to `logo_path`. Only
    applies where a logo field exists (connectors). Best-effort: a scan failure fails open
    so it never blocks a write on a transient error.
    """
    logo_path = (logo_path or "").rstrip("/")
    if not logo_path or not folder:
        return

    cursor = None
    try:
        for _ in range(60):  # cap: 60 pages * 50
            r = t.list_fragments(cfg, path=folder, limit=50, cursor=cursor)
            for f in r.get("items", []):
                if exclude_id and f.get("id") == exclude_id:
                    continue
                logo_field = next((x for x in f.get("fields", []) if x.get("name") == "logo"), None)
                values = [str(v).rstrip("/") for v in (logo_field.get("values") if logo_field else []) or []]
                if logo_path in values:
                    other = f.get("path", "").rstrip("/").rsplit("/", 1)[-1]
                    raise click.ClickException(
                        f"Logo '{logo_path}' is already used by '{other}'.\n"
                        "Each connector needs a unique logo (one-to-one) — choose a different file."
                    )
            cursor = r.get("cursor")
            if not cursor:
                break
    except click.ClickException:
        raise                       # the uniqueness violation — propagate
    except (Exception, SystemExit):
        return                      # scan failure — fail open, don't block the write


# ── CLI ────────────────────────────────────────────────────────────────────────

_ANSI_RE = re.compile("\x1b\\[[0-9;]*m")  # CSI SGR (color) sequences


def _enable_prompt_history() -> None:
    """Give interactive prompts line editing + up/down arrow history recall.

    click.prompt calls input(), which automatically uses the readline module for
    line editing and history the moment readline is imported. We also load/save a
    small history file so arrow-up recalls values typed in previous runs. Hidden
    prompts (e.g. the client secret at login) use getpass, which bypasses readline,
    so secrets are never added to history. Best-effort — silently skipped where
    readline is unavailable (e.g. bare Windows without pyreadline3).

    On macOS the stdlib `readline` is backed by BSD libedit, whose default bindings
    mishandle up/down history (the current line isn't replaced cleanly). We prefer
    the `gnureadline` drop-in (real GNU readline); if only libedit is present, we
    bind the arrow keys explicitly so history navigation replaces the line.
    """
    try:
        import gnureadline as readline
        # Make gnureadline THE readline for this process, so the input() hook can't be
        # reverted to libedit if some later code does `import readline`.
        sys.modules["readline"] = readline
    except ImportError:
        try:
            import readline
        except ImportError:
            return
        if "libedit" in (readline.__doc__ or ""):
            readline.parse_and_bind("bind ^[[A ed-prev-history")
            readline.parse_and_bind("bind ^[[B ed-next-history")

    # Colored prompts (click.style) embed ANSI escape codes. readline counts those
    # invisible bytes as visible width unless they're wrapped in \001..\002, which
    # throws off cursor math — up/down history recall then erases from the wrong
    # column and leaves stale characters on the line. Wrap the visible-prompt hook
    # once so every click.prompt gets readline-safe, zero-width-marked color codes.
    if not getattr(click.termui, "_cf_rlsafe_wrapped", False):
        _orig_vpf = click.termui.visible_prompt_func

        def _rlsafe_prompt(prompt: str = "") -> str:
            return _orig_vpf(_ANSI_RE.sub("\001\\g<0>\002", prompt))

        click.termui.visible_prompt_func = _rlsafe_prompt
        click.termui._cf_rlsafe_wrapped = True

    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        readline.read_history_file(str(config.HISTORY_FILE))
    except OSError:
        pass  # no history file yet, or unreadable — start fresh
    readline.set_history_length(1000)

    import atexit

    def _save_history() -> None:
        try:
            readline.write_history_file(str(config.HISTORY_FILE))
        except OSError:
            pass

    atexit.register(_save_history)


@click.group()
def cli():
    """cf-agent — CLI for AEM Content Fragments."""
    _enable_prompt_history()


# ── auth ──────────────────────────────────────────────────────────────────────

DEFAULT_ADOBE_SCOPES = "AdobeID,aem.folders,aem.assets.author,openid,aem.fragments.management"
DEFAULT_REDIRECT_URI = "https://aem-agent-callback.vercel.app/callback"


@cli.command()
@click.option("--preset", default=None, type=click.Path(exists=True), help="Path to a shared .env file with pre-filled Adobe credentials.")
def login(preset):
    """Authenticate via browser (Adobe IMS user OAuth)."""
    _header("Setting up cf-agent credentials.\n")

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
    # Scopes and redirect URI use fixed defaults (no prompt); a --preset file can still override them.
    scopes = pre.get("ADOBE_SCOPES") or DEFAULT_ADOBE_SCOPES
    redirect_uri = pre.get("ADOBE_REDIRECT_URI") or DEFAULT_REDIRECT_URI

    cfg_values = {
        "ADOBE_CLIENT_ID": client_id,
        "ADOBE_CLIENT_SECRET": client_secret,
        "ADOBE_SCOPES": scopes,
        "ADOBE_REDIRECT_URI": redirect_uri,
    }
    config.save_config(cfg_values)
    _success("Credentials saved.\n")

    cfg = config.load_config()
    auth.browser_login(cfg)

    sites_url = environments.prompt_environment_selection()
    cfg_values["ADOBE_SITES_API_BASE_URL"] = sites_url
    config.save_config(cfg_values)
    _success("Environment saved.\n")


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

    def _row(k, v):
        click.echo(click.style(f"{k:<12}", fg="cyan", bold=True) + str(v))

    _row("User:", email)
    _row("IMS org:", org)
    _row("Client ID:", client)
    if exp:
        remaining = int(exp - time.time())
        if remaining > 0:
            click.echo(click.style(f"{'Token:':<12}", fg="cyan", bold=True) + click.style(f"expires in {remaining}s", fg="green"))
        else:
            click.echo(click.style(f"{'Token:':<12}", fg="cyan", bold=True) + click.style("EXPIRED", fg="red"))
    _row("Scopes:", scope)
    _row("Environment:", env_url)


# ── asset group ─────────────────────────────────────────────────────────────────

# DAM folders for marketplace assets (logos matches the connector model's logo field root).
LOGO_ROOT = "/content/dam/marketplace/logos"
IMAGE_ROOT = "/content/dam/marketplace/images"

# Fallback DAM roots for content-reference fields, keyed by field name. Used when the
# model's own root is the generic "/content/dam" (e.g. not yet set to a specific folder),
# so users can type just a file name (logo -> /content/dam/marketplace/logos/<file>). The
# model's root always wins when it points to a specific folder.
_CONTENT_REF_ROOT_DEFAULTS = {
    "logo": LOGO_ROOT,
    "image": IMAGE_ROOT,
    "images": IMAGE_ROOT,
}


def _effective_content_root(field: dict) -> str:
    """Resolve the DAM folder to prepend for a content-reference field.

    Prefer the model's root; if that's the generic "/content/dam", fall back to a
    known per-field default so file-name-only entry works even before the model is
    redeployed with a specific rootPath.
    """
    root = (field.get("root") or "/content/dam").rstrip("/")
    if root == "/content/dam":
        return _CONTENT_REF_ROOT_DEFAULTS.get(field.get("name", ""), root)
    return root


@cli.group()
def asset():
    """Check and upload assets in the AEM DAM."""


@asset.command("upload")
@click.argument("local_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--logo", is_flag=True, help=f"Upload into the marketplace logos folder ({LOGO_ROOT}).")
@click.option("--image", is_flag=True, help=f"Upload into the marketplace images folder ({IMAGE_ROOT}).")
@click.option("--root", default=None, metavar="DAM_PATH", help="Destination DAM folder (e.g. /content/dam/marketplace/screenshots).")
@click.option("--name", "dest_name", default=None, help="File name to use in the DAM (defaults to the local file name).")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def asset_upload(local_file, logo, image, root, dest_name, as_json):
    """Upload a local file into the AEM DAM via an S3 staging hop.

    The file is uploaded to the S3 staging bucket, a short-lived pre-signed GET
    URL is generated, and AEM is asked to pull the asset from that URL.

    The staged S3 object is deleted once the import finishes.

    The staging bucket, region and prefix ship with the app — there is nothing to
    configure. Set AWS_S3_STAGING_BUCKET / _REGION / _PREFIX (env var or
    ~/.cf-agent/config) only to point at a different environment. The prefix must
    stay inside aem-assets/, the only prefix the CLI's IAM user may delete from.

    AWS credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, the OS
    keychain (`cf-agent asset credentials set`), or boto3's own chain.
    """
    from . import uploader

    if sum(bool(x) for x in (logo, image, root)) > 1:
        raise click.ClickException("Use only one of --logo, --image, or --root.")

    if root:
        dam_folder = root.rstrip("/")
    elif logo:
        dam_folder = LOGO_ROOT
    elif image:
        dam_folder = IMAGE_ROOT
    else:
        raise click.ClickException(
            "Choose a destination folder with --logo, --image, or --root."
        )

    cfg = _cfg()
    on_status = None if as_json else lambda m: _hint(f"  {m}")

    result = uploader.stage_and_import(
        cfg, local_file, dam_folder, dest_name, on_status=on_status
    )

    if as_json:
        _print_json(result)
    else:
        _success(f"✓ Uploaded: {result['dam_path']}")
        if result.get("asset_id"):
            _hint(f"  assetId: {result['asset_id']}")


@asset.group("credentials")
def asset_credentials():
    """Manage the AWS credentials used for asset upload staging."""


@asset_credentials.command("set")
@click.option("--access-key-id", default=None, help="AWS access key ID (prompted if omitted).")
@click.option("--secret-access-key", default=None, help="AWS secret access key (prompted if omitted).")
@click.option("--session-token", default=None, help="AWS session token, for temporary credentials.")
def asset_credentials_set(access_key_id, secret_access_key, session_token):
    """Store AWS credentials in the OS keychain (encrypted at rest).

    Values are kept in the macOS Keychain / Windows Credential Manager / Linux
    Secret Service — never in a plaintext file and never in the repo. Omit the
    options to be prompted; the secret is hidden as you paste it, so it does not
    land in your shell history.
    """
    from . import secretstore

    access_key_id = access_key_id or click.prompt("AWS Access Key ID")
    secret_access_key = secret_access_key or click.prompt(
        "AWS Secret Access Key", hide_input=True
    )

    secretstore.set_aws_credentials(
        access_key_id.strip(), secret_access_key.strip(),
        (session_token or "").strip() or None,
    )
    _success("AWS credentials stored in the OS keychain.")
    _hint("  They are used automatically by `cf-agent asset upload`.")


@asset_credentials.command("show")
def asset_credentials_show():
    """Show whether credentials are stored, without revealing the secret."""
    from . import secretstore

    backend = secretstore.backend_name() or "none available"

    stored = secretstore.get_aws_credentials()
    if not stored:
        _failure("No AWS credentials stored in the OS keychain.")
        _hint(f"  Keychain backend : {backend}")
        _hint("  Set them with: cf-agent asset credentials set")
        return

    akid = stored["access_key_id"]
    masked = f"{akid[:4]}...{akid[-4:]}" if len(akid) > 8 else "(set)"
    _success("AWS credentials are stored in the OS keychain.")
    click.echo(f"  Keychain backend : {backend}")
    click.echo(f"  Access Key ID    : {masked}")
    click.echo(f"  Secret Access Key: (hidden)")
    if stored.get("session_token"):
        click.echo(f"  Session Token    : (set)")


@asset_credentials.command("clear")
def asset_credentials_clear():
    """Remove the stored AWS credentials from the OS keychain."""
    from . import secretstore

    if secretstore.clear_aws_credentials():
        _success("AWS credentials removed from the OS keychain.")
    else:
        _hint("No AWS credentials were stored.")


@asset.command("exists")
@click.argument("asset_ref")
@click.option("--logo", is_flag=True, help=f"Resolve ASSET_REF against the marketplace logos folder ({LOGO_ROOT}).")
@click.option("--image", is_flag=True, help=f"Resolve ASSET_REF against the marketplace images folder ({IMAGE_ROOT}).")
@click.option("--root", default=None, metavar="DAM_PATH", help="DAM folder to resolve a bare file name against (e.g. /content/dam/marketplace/screenshots).")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def asset_exists(asset_ref, logo, image, root, as_json):
    """Check whether a logo or image exists in the AEM DAM.

    ASSET_REF may be a full DAM path (e.g. /content/dam/marketplace/logos/foo.png),
    or a bare file name when --logo, --image, or --root is supplied.

    Exits 0 if the asset exists, 1 if it does not — handy for scripting.
    """
    cfg = _cfg()

    if sum(bool(x) for x in (logo, image, root)) > 1:
        raise click.ClickException("Use only one of --logo, --image, or --root.")

    ref = asset_ref.strip()
    if root:
        base = root.rstrip("/")
    elif logo:
        base = LOGO_ROOT
    elif image:
        base = IMAGE_ROOT
    else:
        base = None

    if ref.startswith("/"):
        path = ref
    elif base:
        path = f"{base}/{ref}"
    else:
        raise click.ClickException(
            "Provide a full DAM path (e.g. /content/dam/marketplace/logos/foo.png), "
            "or a file name together with --logo, --image, or --root."
        )

    exists = client.resource_exists(cfg, path)

    if as_json:
        _print_json({"path": path, "exists": exists})
    elif exists:
        _success(f"✓ Asset exists: {path}")
    else:
        _failure(f"✗ Asset not found: {path}")

    if not exists:
        raise SystemExit(1)


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
        marker = click.style("  (current)", fg="green") if e["url"] == current else ""
        click.echo(
            "  " + click.style(f"{i}.", fg="cyan")
            + " " + click.style(f"{e['label']:<6}", bold=True)
            + click.style(f"  {e['url']}", fg="bright_black") + marker
        )


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
    _field_heading(label, "enumeration", required)
    for i, opt in enumerate(options, 1):
        click.echo("    " + click.style(f"{i:>2}.", fg="cyan") + f" {opt['label']}")
    if multiple:
        _hint("  Select one or more numbers separated by commas.")

    while True:
        raw = click.prompt(click.style("  Choice", fg="cyan"), default="", show_default=False).strip()
        if _is_back(raw):
            return BACK
        if not raw:
            if required:
                _failure("  This field is required.")
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

        _failure(f"  Enter a number between 1 and {len(options)}." +
                 (" Separate multiple with commas." if multiple else ""))



def _prompt_solution_tags(cfg: dict, field: dict) -> str | None:
    """solution_tags: pick from the model's enum values and/or type a new Title Case
    tag. Suggestions come from the CF model enum (read via the models API — no
    GraphQL). Returns a comma-joined string (or None if skipped)."""
    label    = field.get("label") or field.get("name", "")
    required = field.get("required", False)
    options  = _parse_enum_options(field.get("values") or field.get("enumValues") or [])

    req_tag = " (required)" if required else " (optional, Enter to skip)"
    click.echo(f"\n  Field : {label}  [pick from list or type new]{req_tag}")
    if options:
        click.echo("  Enter numbers and/or type new Title Case tags, comma-separated:")
        for i, o in enumerate(options, 1):
            click.echo(f"    {i:>2}. {o['label']}")
    else:
        click.echo("  Enter one or more Title Case tags, comma-separated (e.g. IT, Retail Store Services).")

    while True:
        raw = click.prompt("  Value", default="", show_default=False).strip()
        if _is_back(raw):
            return BACK
        if not raw:
            if required:
                click.echo("  This field is required.")
                continue
            return None

        tokens = [t.strip() for t in raw.split(",")]
        tokens = [t for t in tokens if t]
        if not tokens:
            click.echo("  Enter at least one number from the list, or a new Title Case tag.")
            continue

        chosen: list[str] = []
        error = ""
        for tok in tokens:
            # A plain number selects from the list (single number needs no comma).
            if tok.isdigit():
                n = int(tok)
                if 1 <= n <= len(options):
                    chosen.append(options[n - 1]["value"])
                else:
                    error = f"  '{tok}' is not in the list — pick a number between 1 and {len(options)}."
                    break
                continue

            # Several numbers typed without commas, e.g. "1 2 3" — guide, don't guess.
            parts = tok.split()
            if len(parts) > 1 and all(p.isdigit() for p in parts):
                error = (f"  Separate multiple selections with commas — try '{','.join(parts)}' "
                         f"(not spaces).")
                break

            # Otherwise it's a new custom tag: must be text, in Title Case.
            if not any(ch.isalpha() for ch in tok):
                error = f"  A custom tag must be text, not just numbers: '{tok}'."
                break
            if not _TITLE_CASE_TAG.match(tok):
                error = (f"  New tags must be Title Case (e.g. 'Retail Store Services'). "
                         f"Invalid: {tok}")
                break
            chosen.append(tok)

        if error:
            click.echo(error)
            continue

        seen: set[str] = set()
        tags = [c for c in chosen if not (c in seen or seen.add(c))]
        return ",".join(tags)


def _prompt_field_value(cfg: dict, field: dict) -> str | None:

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
        _field_heading(label, "long-text / markdown", required)
        description = field.get("description", "").strip()
        if description:
            _hint(f"  Hint  : {description}")
        _hint("  Provide a file path (recommended for markdown, e.g. ~/guide.md).")
        _hint("  Pasting multi-line markdown directly may fail due to shell interpretation.")
        while True:
            value = click.prompt(click.style("  Value or file path", fg="cyan"), default="", show_default=False).strip()
            if _is_back(value):
                return BACK
            if not value:
                if required:
                    _failure("  This field is required.")
                    continue
                return None
            try:
                content = _read_markdown_value(value)
            except click.ClickException as exc:
                _failure(f"  {exc.format_message()}")
                continue
            if content != value:
                _success("  Loaded markdown content from file.")
            # Verify any AEM asset/image references inside the markdown exist now,
            # so a broken link can be fixed before the whole form is submitted.
            missing = _validate_markdown_asset_refs(cfg, content)
            if missing:
                _failure("  The markdown references AEM asset(s) not found in the DAM:")
                for p in missing:
                    _failure(f"    - {p}")
                _hint("  Fix the reference(s) in the file, then re-enter the path.")
                continue
            return content

    # All other types — text prompt with hints and validation
    import re
    _field_heading(label, ftype, required)

    description = field.get("description", "").strip()
    if description:
        _hint(f"  Hint  : {description}")

    if ftype == "boolean":
        _hint("  Enter: true or false")
    elif ftype == "content-reference":
        root = _effective_content_root(field)
        if root != "/content/dam":
            _hint(f"  Path prefix: {root}/")
            _hint("  Enter the file name only (e.g. my-logo.svg)")
    elif ftype == "fragment-reference":
        _hint("  Expected: Content Fragment UUID or path")
    elif ftype in ("date", "date-time"):
        _hint("  Expected: YYYY-MM-DD  or  YYYY-MM-DDTHH:MM:SSZ")

    max_len = field.get("maxLength") or field.get("maxSize")
    if max_len:
        _hint(f"  Max length: {max_len} characters")

    regex   = field.get("customValidationRegex", "")
    err_msg = field.get("customErrorMessage", "Invalid value.")

    while True:
        value = click.prompt(click.style("  Value", fg="cyan"), default="", show_default=False).strip()
        if _is_back(value):
            return BACK
        if not value:
            if required:
                _failure("  This field is required.")
                continue
            return None

        if ftype == "boolean" and value.lower() not in ("true", "false"):
            _failure("  Enter true or false.")
            continue

        if max_len and len(value) > max_len:
            _failure(f"  Too long — max {max_len} characters (entered {len(value)}).")
            continue

        if regex and not re.match(regex, value):
            _failure(f"  {err_msg}")
            continue

        # Prepend root prefix for content-reference fields with a specific folder
        if ftype == "content-reference":
            root = _effective_content_root(field)
            if root != "/content/dam" and not value.startswith("/"):
                value = f"{root}/{value}"
            # Verify the asset exists in AEM now, so a typo can be corrected in
            # place instead of failing after the whole form is filled in.
            if not _asset_exists(cfg, value):
                _failure(f"  Asset not found in AEM: {value}. Please enter a valid asset name.")
                continue
            # Inline one-to-one logo check: re-prompt here rather than failing at the end.
            uniq_folder = field.get("_uniqueness_folder")
            if uniq_folder:
                try:
                    with _spinner("Checking logo is unique..."):
                        _check_logo_unique(cfg, value, uniq_folder,
                                           exclude_id=field.get("_uniqueness_exclude_id", ""))
                except click.ClickException as exc:
                    _failure(f"  {exc.format_message()}")
                    continue

        return value


def _interactive_create(cfg) -> dict:
    """Walk the user through creating a fragment step by step."""
    # ── pick model ────────────────────────────────────────────────────────────
    with _spinner("Fetching available models..."):
        models_data = t.list_models(cfg, limit=50)
    model_items = models_data.get("items", [])
    if not model_items:
        raise click.ClickException("No models found on this environment.")

    _header("\nAvailable models:")
    for i, m in enumerate(model_items, 1):
        title = m.get("title", "").strip()
        path  = m.get("path", "")
        name  = path.rstrip("/").rsplit("/", 1)[-1]
        label = title if title else name
        click.echo("  " + click.style(f"{i}.", fg="cyan") + f" {label}")

    while True:
        raw = click.prompt(click.style(f"\nSelect model [1-{len(model_items)}]", fg="cyan"), default="1")
        try:
            idx = int(raw)
            if 1 <= idx <= len(model_items):
                chosen_model = model_items[idx - 1]
                break
        except ValueError:
            pass
        _failure(f"Please enter a number between 1 and {len(model_items)}.")

    model_path  = chosen_model["path"]
    model_id    = chosen_model.get("id", "")
    model_label = (chosen_model.get("title") or "").strip() or model_path.rstrip("/").rsplit("/", 1)[-1]
    _success(f"Model: {model_label}")

    # ── resolve schema live from AEM (single source of truth) ────────────────
    schema_fields: list = []
    title_required = True

    if schema_fields:
        _hint(f"  Schema loaded ({len(schema_fields)} fields, pre-fetched).")
    elif model_id:
        try:
            model_schema = t.get_model(cfg, id=model_id)
            schema_fields = model_schema.get("fields", [])
            title_required = model_schema.get("titleRequired", True)
            _SCHEMA_CACHE[model_path] = schema_fields
            _hint(f"  Schema loaded ({len(schema_fields)} fields, live from AEM).")
        except SystemExit:
            _hint("  (Could not load schema — proceeding without field validation.)")

    # ── basic fragment details ────────────────────────────────────────────────
    click.echo("")
    default_parent = environments.MODEL_DEFAULTS.get(model_path, "")
    if default_parent:
        # Each model has a fixed home folder (environments.MODEL_DEFAULTS) — use it
        # silently so the user has one less thing to see or answer.
        parent_path = default_parent
    else:
        # No known default (unregistered model) — fall back to asking.
        parent_path = click.prompt(
            click.style("Parent folder path", fg="cyan"),
            prompt_suffix=": ",
        )

    # ── step-driven prompting (type ":back" to return to the previous step) ───
    # Answers are kept in `answers`, keyed by step, so going back re-shows what you
    # entered (Enter keeps it). Steps that don't apply — e.g. the installation UUID
    # when availability isn't INSTALLABLE — are skipped in whichever direction you
    # are moving, and their stale answer is dropped.
    schema = _schema_map(schema_fields) if schema_fields else {}
    systems_field = schema.get("systems")
    promptable = [
        f for f in schema_fields
        if f.get("name") not in ("slug", "systems")
        and f.get("name") not in DEPRECATED_FIELDS
    ] if schema_fields else []

    answers: dict = {}

    def _sys_values() -> list:
        return [v.strip() for v in (answers.get("__systems") or "").split(",") if v.strip()]

    def _prompt_with_prev(field: dict, prev):
        """Prompt one model field; a blank entry keeps `prev` when there is one."""
        if prev is not None:
            _hint(f"  previously: {prev}   —   Enter to keep")
            field = {**field, "required": False}
        val = (_prompt_solution_tags(cfg, field) if field.get("name") == "solution_tags"
               else _prompt_field_value(cfg, field))
        if val is BACK:
            return BACK
        if val is None and prev is not None:
            return prev
        return val

    def _step_systems(prev):
        return _prompt_with_prev(systems_field, prev)

    def _step_name(prev):
        sys_vals = _sys_values()
        prefix = f"{sys_vals[0]}-" if sys_vals else ""
        # If systems changed on a back-step, a previous name may no longer fit.
        if prev and prefix and not prev.startswith(prefix):
            _hint(f"  systems changed — previous name '{prev}' no longer starts with "
                  f"'{prefix}', please re-enter")
            prev = None
        while True:
            if prev:
                _hint(f"  previously: {prev}   —   Enter to keep")
            typed = click.prompt(
                click.style("Fragment name (slug, kebab-case)", fg="cyan"),
                prompt_suffix=f": {prefix}", default="", show_default=False,
            ).strip()
            if _is_back(typed):
                return BACK
            if not typed:
                if prev:
                    return prev
                _failure("Fragment name is required.")
                continue
            # Don't double the prefix if the user typed it (or the bare system) anyway.
            candidate = (typed if (prefix and (typed.startswith(prefix) or typed in sys_vals))
                         else f"{prefix}{typed}")
            try:
                _validate_slug_or_fail(candidate, field_label="Fragment name")
                with _spinner("Checking slug availability..."):
                    _check_duplicate_slug(cfg, candidate, model_path, parent_path)
                return candidate
            except click.ClickException as exc:
                _failure(exc.format_message())

    def _step_title(prev):
        _field_heading("Fragment title", "text", title_required)
        while True:
            if prev:
                _hint(f"  previously: {prev}   —   Enter to keep")
            val = click.prompt(click.style("  Title", fg="cyan"),
                               default="", show_default=False).strip()
            if _is_back(val):
                return BACK
            if not val:
                if prev:
                    return prev
                if title_required:
                    _failure("  Title is required for this model.")
                    continue
            return val

    def _make_field_step(field_def: dict):
        def _step(prev):
            fname = field_def.get("name")
            # installation UUID applies only when availability is INSTALLABLE.
            if fname in INSTALLATION_UUID_FIELDS:
                if (answers.get("availability") or "") != "INSTALLABLE":
                    return SKIP
                return _prompt_with_prev({**field_def, "required": True}, prev)
            # content_guide is mandatory on create — prompt as required, re-prompt on blank.
            if fname in REQUIRED_ON_CREATE:
                return _prompt_with_prev({**field_def, "required": True}, prev)
            # logo: validate one-to-one uniqueness inline (before the next step).
            if fname == "logo":
                return _prompt_with_prev({**field_def, "_uniqueness_folder": parent_path}, prev)
            return _prompt_with_prev(field_def, prev)
        return _step

    steps: list = []
    if systems_field:
        steps.append(("__systems", _step_systems))
    steps.append(("__name", _step_name))
    steps.append(("__title", _step_title))
    for f in promptable:
        steps.append((f["name"], _make_field_step(f)))

    _header("\nEnter fragment details:")
    _back_hint()

    i, direction = 0, 1
    while i < len(steps):
        key, step_fn = steps[i]
        result = step_fn(answers.get(key))
        if result is SKIP:
            answers.pop(key, None)          # drop a now-inapplicable answer
            i += direction
            if i < 0:
                i, direction = 0, 1
            continue
        if result is BACK:
            if i == 0:
                _hint("  Already at the first step.")
                direction = 1
                continue
            direction = -1
            i -= 1
            continue
        answers[key] = result
        direction = 1
        i += 1

    name  = answers.get("__name") or ""
    title = answers.get("__title") or ""
    systems_values = _sys_values()

    # ── assemble the field payload ────────────────────────────────────────────
    fields_list: list = []
    if schema_fields:
        # `slug` mirrors the fragment name; `systems` was collected as its own step.
        if any(f.get("name") == "slug" for f in schema_fields):
            fields_list.append({"name": "slug", "type": "text", "values": [name]})
        if systems_values:
            sftype = systems_field.get("fieldType") or systems_field.get("type", "text")
            fields_list.append({"name": "systems", "type": sftype, "values": systems_values})
        for f in promptable:
            fname = f.get("name")
            value = answers.get(fname)
            if value is None:
                continue
            ftype    = f.get("fieldType") or f.get("type", "text")
            multiple = f.get("multiple", False)
            # Multi-value fields come back as a comma-separated string.
            values_list = [v.strip() for v in value.split(",")] if multiple else [value]
            entry = {"name": fname, "type": ftype, "values": values_list}
            if ftype == "long-text" and f.get("mimeType"):
                entry["mimeType"] = f["mimeType"]
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


def _resolve_fragment_by_slug(cfg: dict, slug: str, model_path: str) -> str:
    """Return the fragment id for a slug in a model's folder (single targeted call)."""
    folder = _folder_for_model(model_path)
    if not folder:
        raise click.ClickException(
            f"Could not determine the folder for model '{model_path}'. Pass an id instead."
        )
    frag_path = f"{folder}/{slug}"
    results = t.list_fragments(cfg, path=frag_path, limit=5)
    for f in results.get("items", []):
        if f.get("path", "").rstrip("/") == frag_path and f.get("id"):
            return f["id"]
    raise click.ClickException(f"No fragment found with slug '{slug}' in {folder}.")


def _pick_numbered(label: str, count: int) -> int:
    """Prompt for a 1-based selection in [1, count]; returns the chosen index."""
    while True:
        raw = click.prompt(click.style(f"{label} [1-{count}]", fg="cyan"), default="1")
        if _is_back(raw):
            return BACK
        try:
            i = int(raw)
            if 1 <= i <= count:
                return i
        except ValueError:
            pass
        _failure(f"Enter a number between 1 and {count}.")


def _interactive_select_fragment(cfg: dict) -> str:
    """Pick a model, optionally filter, list matching fragments, and select one.

    Returns the chosen fragment id — so a user never has to know/type a UUID.
    """
    with _spinner("Fetching models..."):
        models = t.list_models(cfg, limit=50).get("items", [])
    if not models:
        raise click.ClickException("No models found on this environment.")

    # Outer loop = model choice; inner loop = filter + selection. ":back" at the
    # selection returns to the filter, and ":back" at the filter returns to the model.
    while True:
        _header("\nSelect model:")
        for i, m in enumerate(models, 1):
            name = m.get("path", "").rstrip("/").rsplit("/", 1)[-1]
            click.echo("  " + click.style(f"{i}.", fg="cyan") + f" {m.get('title') or name}")
        _back_hint()
        picked = _pick_numbered("Model", len(models))
        if picked is BACK:
            _hint("  Already at the first step.")
            continue
        model = models[picked - 1]
        model_path = model["path"]
        folder = environments.MODEL_DEFAULTS.get(model_path) or model_path

        chosen_id = _select_fragment_in_folder(cfg, folder)
        if chosen_id is BACK:
            continue          # back to model choice
        return chosen_id


_PICKER_PAGE = 20  # entries shown per page in the fragment picker


def _matching_fragments(cfg: dict, folder: str, term: str) -> list[dict]:
    """Return [{id, slug, title}] in a folder whose slug/title STARTS WITH `term`.

    Prefix match (not substring) so 'work' surfaces workday-… plugins rather than every
    slug that merely contains 'work'. An exact-slug filter takes a single targeted call
    and returns just that one; otherwise the folder is scanned (the AEM search endpoint
    is unusable). `term` must already be lowercased.
    """
    if term:
        try:  # exact-slug fast path — one call, no scan
            r = t.list_fragments(cfg, path=f"{folder}/{term}", limit=5)
            for f in r.get("items", []):
                if f.get("path", "").rstrip("/") == f"{folder}/{term}":
                    return [{"id": f.get("id"), "slug": term, "title": f.get("title", "") or ""}]
        except (Exception, SystemExit):
            pass

    matches: list[dict] = []
    cursor = None
    with _spinner("Loading fragments..."):
        for _ in range(60):  # cap: 60 pages * 50
            r = t.list_fragments(cfg, path=folder, limit=50, cursor=cursor)
            for f in r.get("items", []):
                slug = f.get("path", "").rstrip("/").rsplit("/", 1)[-1]
                title = f.get("title", "") or ""
                if not term or slug.lower().startswith(term) or title.lower().startswith(term):
                    matches.append({"id": f.get("id"), "slug": slug, "title": title})
            cursor = r.get("cursor")
            if not cursor:
                break
    matches.sort(key=lambda m: m["slug"])  # alphabetical
    return matches


def _select_fragment_in_folder(cfg: dict, folder: str):
    """Filter (prefix) + pick a fragment inside a folder, paginated. Returns an id or BACK."""
    while True:  # filter loop
        term = click.prompt(
            click.style("Filter by name/slug (Enter for all)", fg="cyan"),
            default="", show_default=False,
        ).strip()
        if _is_back(term):
            return BACK

        matches = _matching_fragments(cfg, folder, term.lower())
        if not matches:
            where = f"starting with '{term}' " if term else ""
            _failure(f"  No entries {where}here. Try a different filter (Enter lists all).")
            continue
        if term:
            _hint("  Tip: slugs start with the connector/system name — e.g. type 'workday'.")

        pages = (len(matches) + _PICKER_PAGE - 1) // _PICKER_PAGE
        page = 0
        back_to_filter = False
        while True:  # pagination + selection loop
            start = page * _PICKER_PAGE
            chunk = matches[start:start + _PICKER_PAGE]
            hdr = f"\n{len(matches)} match(es)" + (f"  —  page {page + 1}/{pages}" if pages > 1 else "") + ":"
            _header(hdr)
            for i, m in enumerate(chunk, start + 1):
                label = m["slug"] + (f"   ({m['title']})" if m["title"] else "")
                click.echo("  " + click.style(f"{i:>3}.", fg="cyan") + f" {label}")
            nav = []
            if page + 1 < pages:
                nav.append("n=next")
            if page > 0:
                nav.append("p=prev")
            hint = f"Select 1-{len(matches)}" + (f" ({', '.join(nav)})" if nav else "") + " or :back"
            raw = click.prompt(click.style(hint, fg="cyan"), default="", show_default=False).strip()
            low = raw.lower()
            if _is_back(raw):
                back_to_filter = True
                break
            if low in ("n", "next") and page + 1 < pages:
                page += 1
                continue
            if low in ("p", "prev") and page > 0:
                page -= 1
                continue
            if raw.isdigit() and 1 <= int(raw) <= len(matches):
                chosen = matches[int(raw) - 1]
                _success(f"Editing: {chosen['slug']}")
                return chosen["id"]
            _failure(f"  Enter a number 1-{len(matches)}"
                     + (", n/p to page," if pages > 1 else "") + " or :back.")
        if back_to_filter:
            continue  # re-prompt the filter


def _format_current_value(field: dict, values: list) -> str:
    """Compact one-line display of a field's current value for the edit prompt.

    Long-text (markdown guide) content is summarised rather than dumped, and any long
    single value is truncated, so the prompt stays readable.
    """
    if not values:
        return "(empty)"
    joined = ", ".join(values)
    ftype = field.get("fieldType") or field.get("type", "")
    if ftype == "long-text":
        first_line = next((ln.strip() for ln in joined.splitlines() if ln.strip()), "")
        preview = (first_line[:50] + "…") if len(first_line) > 50 else first_line
        return f"existing content — {len(joined)} chars" + (f' (starts: "{preview}")' if preview else "")
    return (joined[:77] + "…") if len(joined) > 80 else joined


def _is_review_locked(fragment: dict) -> bool:
    """True if the fragment was sent to review (reviewRequired = true), which locks it
    in AEM's approval workflow so it can't be modified until the review is released."""
    for f in fragment.get("fields", []):
        if f.get("name") == "reviewRequired":
            return any(str(v).strip().lower() == "true" for v in (f.get("values") or []))
    return False


def _interactive_edit_fields(cfg: dict, schema_fields: list, current_values: dict, allowed: set | None,
                             folder: str = "", exclude_id: str = "") -> tuple:
    """Prompt each editable field showing its current value (Enter keeps it).

    Each entered value is validated inline (asset existence, regex, maxLength, enum,
    markdown DAM refs) so a bad value is caught and re-prompted immediately — not after
    the whole form is filled in. Returns a tuple of NAME=VALUE strings for changed
    fields. Only fields in `allowed` (the journey's editable set) are offered; if
    `allowed` is None, fall back to everything that isn't immutable/deprecated/system.
    """
    if allowed is not None:
        editable = [f for f in schema_fields if f.get("name") in allowed]
    else:
        skip = IMMUTABLE_ON_UPDATE | DEPRECATED_FIELDS | {"content_type"}
        editable = [f for f in schema_fields if f.get("name") not in skip]
    _header("\nEdit fields — press Enter to skip (keep the current value):")
    _back_hint()

    def _eff_availability() -> str:
        """The availability that will apply after this edit — a pending change wins."""
        if "availability" in pending:
            return pending["availability"]
        return (current_values.get("availability") or [""])[0]

    def _has_current_uuid() -> bool:
        return any(current_values.get(u) not in (None, [], [""]) for u in INSTALLATION_UUID_FIELDS)

    # Index cursor (not a for-loop) so ":back" can return to the previous field.
    # `pending` holds edits made so far, keyed by field, so revisiting a field shows
    # what you already typed and lets you clear it by pressing Enter.
    pending: dict = {}
    i, direction = 0, 1
    while i < len(editable):
        f = editable[i]
        name = f.get("name")

        # Availability↔UUID interdependency: the installation UUID applies ONLY when
        # availability is INSTALLABLE. Otherwise skip it — and if the fragment currently
        # carries a UUID, drop it (a downgrade to VALIDATED/IDEA must clear the asset).
        if name in INSTALLATION_UUID_FIELDS:
            if _eff_availability() != "INSTALLABLE":
                pending[name] = "" if _has_current_uuid() else pending.get(name, None)
                if pending.get(name) is None:
                    pending.pop(name, None)
                i += direction
                if i < 0:
                    i, direction = 0, 1
                continue

        cur_disp = _format_current_value(f, current_values.get(name, []))
        _hint(f"\n  current: {cur_disp}   —   Enter to keep")
        if name in pending:
            _hint(f"  pending edit: {pending[name]}   —   Enter to discard it")

        # Prompt exactly like create (numbered enums, solution_tags list+free-text, guide
        # file path). Fields are optional here so a blank entry keeps the current value —
        # EXCEPT a newly-INSTALLABLE plugin with no UUID yet, which must supply one.
        required = False
        if name in INSTALLATION_UUID_FIELDS and _eff_availability() == "INSTALLABLE":
            required = not (_has_current_uuid() or name in pending)
        field_opt = {**f, "required": required}
        if name == "logo" and folder:
            # validate one-to-one logo uniqueness inline (exclude this fragment itself)
            field_opt["_uniqueness_folder"] = folder
            field_opt["_uniqueness_exclude_id"] = exclude_id
        if name == "solution_tags":
            val = _prompt_solution_tags(cfg, field_opt)
        else:
            val = _prompt_field_value(cfg, field_opt)

        if val is BACK:
            if i == 0:
                _hint("  Already at the first field.")
                direction = 1
                continue
            direction = -1
            i -= 1
            continue
        if val is None:
            pending.pop(name, None)  # blank = keep current (and drop any pending edit)
        else:
            pending[name] = val
        direction = 1
        i += 1

    if not pending:
        raise click.ClickException("No changes entered — nothing to update.")
    return tuple(f"{k}={v}" for k, v in pending.items())


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

    # Duplicate-slug guard: scan the model's folder before writing.
    slug_entry = next((f for f in (params.get("fields") or []) if f.get("name") == "slug"), None)
    if slug_entry and slug_entry.get("values"):
        with _spinner("Checking slug availability..."):
            _check_duplicate_slug(
                cfg,
                slug_entry["values"][0],
                model_path,
                params.get("parentPath", ""),
            )

    # One-to-one logo guard (connectors): reject a logo already used by another connector.
    logo_entry = next((f for f in (params.get("fields") or []) if f.get("name") == "logo"), None)
    if logo_entry and logo_entry.get("values"):
        with _spinner("Checking logo is unique..."):
            _check_logo_unique(cfg, logo_entry["values"][0], params.get("parentPath", ""))

    with _spinner("Creating fragment in AEM..."):
        data = t.create_fragment(cfg, **params)
    if as_json:
        _print_json(data)
        return
    _success(f"\nCreated: {data.get('id')}")
    click.echo(click.style("Path:    ", fg="green", bold=True) + f"{data.get('path')}")
    _emit_fragment_json_url(cfg, data.get("id"))


@fragments.command("update")
@click.argument("id", required=False)
@click.option("-i", "--interactive", "interactive", is_flag=True,
              help="Pick the fragment from a list (no id needed).")
@click.option("--slug", default=None, help="Resolve the fragment by slug (needs --model-path).")
@click.option("--model-path", "model_path_opt", default=None, help="Model path for --slug lookup.")
@click.option("--title",  default=None, help="New fragment title")
@click.option("-f", "--field", "field_args", multiple=True, metavar="NAME=VALUE",
              help="Field value as name=value. Repeatable. Multi-value: comma-separate.")
@click.option("--patch",  default=None, help="Raw JSON Patch array (advanced)")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def update_fragment(id, interactive, slug, model_path_opt, title, field_args, patch, as_json):
    """Update a content fragment.

    Identify the fragment by id, by --slug (+ --model-path), or interactively with -i.

    Examples:\n
      update -i -f description="New desc."          # pick from a list, no id\n
      update --slug workday-hr-connector --model-path "$CONN_M" -f description="New desc."\n
      update <id> --title "New Title"\n
      update <id> -f slug="new-slug" -f description="New desc."
    """
    cfg = _cfg()

    # Resolve the fragment id when not given directly.
    if not id:
        if interactive:
            id = _interactive_select_fragment(cfg)
        elif slug:
            if not model_path_opt:
                raise click.UsageError("--slug requires --model-path.")
            id = _resolve_fragment_by_slug(cfg, slug, model_path_opt)
        else:
            raise click.UsageError(
                "Provide a fragment id, or use -i to pick one, or --slug with --model-path."
            )

    fragment = t.get_fragment(cfg, id=id)

    # Fail fast BEFORE prompting: a fragment sent to review is locked in AEM, so AEM
    # would reject the write at the end anyway. Tell the user now instead of after they
    # fill in the whole form.
    if _is_review_locked(fragment):
        raise click.ClickException(
            f"'{fragment.get('title') or id}' is in review and locked — it can't be edited "
            "until the review is completed or cancelled in AEM.\n"
            "Ask a reviewer/admin to release it (or, in AEM, complete/cancel its review), "
            "then try again."
        )

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

    editable = _editable_fields_for(model_path)

    # -i with no explicit -f/--title/--patch → prompt the editable fields inline.
    if interactive and not field_args and not title and not patch:
        frag_folder = "/".join(fragment.get("path", "").rstrip("/").split("/")[:-1])
        field_args = _interactive_edit_fields(cfg, schema_fields, effective_values, editable,
                                              folder=frag_folder, exclude_id=id)

    if title:
        patch_ops.append({"op": "replace", "path": "/title", "value": title})

    if field_args:
        normalized_fields = _build_fields_from_args(
            cfg,
            field_args,
            model_path,
            require_all_required=False,
        )
        frag_parent = "/".join(fragment.get("path", "").rstrip("/").split("/")[:-1])
        # Duplicate-slug guard: only fires when the slug field is being changed.
        for entry in normalized_fields:
            if entry["name"] == "slug" and entry.get("values"):
                _check_duplicate_slug(cfg, entry["values"][0], model_path, frag_parent,
                                      exclude_fragment_id=id)
            # One-to-one logo guard: only fires when the logo is being changed (exclude self).
            if entry["name"] == "logo" and entry.get("values"):
                with _spinner("Checking logo is unique..."):
                    _check_logo_unique(cfg, entry["values"][0], frag_parent, exclude_id=id)
        for entry in normalized_fields:
            name = entry["name"]
            if name in IMMUTABLE_ON_UPDATE:
                raise click.ClickException(
                    f"Field '{name}' cannot be changed after creation."
                )
            if editable is not None and name not in editable:
                raise click.ClickException(
                    f"Field '{name}' is not editable on update. "
                    f"Editable fields: {', '.join(sorted(editable))}."
                )
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
                if normalized["name"] in IMMUTABLE_ON_UPDATE:
                    raise click.ClickException(
                        f"Field '{normalized['name']}' cannot be changed after creation."
                    )
                op = {**op, "value": normalized}
                effective_values[normalized["name"]] = normalized["values"]

            elif match_values and op_type in ("add", "replace"):
                idx = int(match_values.group(1))
                if idx >= len(fragment_fields):
                    raise click.ClickException(f"Patch path '{path}' references unknown field index.")
                field_name = fragment_fields[idx].get("name", "")
                if field_name not in schema:
                    raise click.ClickException(f"Unknown model field in patch index {idx}: '{field_name}'.")
                if field_name in IMMUTABLE_ON_UPDATE:
                    raise click.ClickException(
                        f"Field '{field_name}' cannot be changed after creation."
                    )
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
                if normalized["name"] in IMMUTABLE_ON_UPDATE:
                    raise click.ClickException(
                        f"Field '{normalized['name']}' cannot be changed after creation."
                    )
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

    _validate_cross_field_rules(effective_values, model_path, is_create=False)

    if not patch_ops:
        raise click.UsageError("Provide at least one of: --title, -f name=value, or --patch.")

    data = t.update_fragment(cfg, id=id, etag=etag, patch_operations=patch_ops)
    if as_json:
        _print_json(data)
        return
    click.echo(f"Updated: {data.get('id')}  {data.get('path')}")
    _emit_fragment_json_url(cfg, data.get("id"))


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
