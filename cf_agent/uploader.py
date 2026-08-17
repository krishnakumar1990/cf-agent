"""Local-file → AEM asset upload via an S3 staging hop.

AEM's Assets Author API has no direct binary-upload surface reachable with the
CLI's user OAuth token — it can only *pull* an asset from a URL
(``POST /adobe/assets/import/fromUrl``). So a local file is uploaded to an S3
staging bucket and a short-lived pre-signed GET URL is handed to AEM to pull
from.

The staged object is deleted once the import finishes. The CLI's IAM user holds
``s3:DeleteObject`` **only under the ``aem-assets/`` prefix**, so
``AWS_S3_STAGING_PREFIX`` must stay inside that prefix for cleanup to work —
staging anywhere else still uploads fine but leaves the object behind forever.
Cleanup is best-effort: a failure warns rather than failing the upload, and the
pre-signed URL expires after ``_PRESIGN_TTL_SECONDS`` regardless.

The whole S3 dependency is contained in this module. If/when an in-AEM upload
broker exists, only ``stage_and_import`` needs to change — the ``asset upload``
command and its output contract stay the same.

Non-secret staging settings may be set as a shell environment variable *or* as
a ``KEY=VALUE`` line in ``~/.cf-agent/config`` (env var wins):

    AWS_S3_STAGING_BUCKET    (required) — bucket the CLI may write to
    AWS_S3_STAGING_REGION    (default: us-west-2)
    AWS_S3_STAGING_PREFIX    (default: aem-assets/) — key prefix; must stay
                             inside aem-assets/ or cleanup cannot delete

The AWS key/secret are *credentials*, so they are sourced separately and never
from the plaintext config file (see ``_resolve_credentials``): shell env vars
first, then boto3's own chain (a shared ``~/.aws`` profile, SSO, or an instance
role).
"""

import mimetypes
import os
import re
import time
import uuid
from pathlib import Path

import click
import httpx

from . import auth, client

# How long the pre-signed GET URL stays valid — long enough for AEM to pull a
# large file, short enough that a URL leaked from a log goes stale quickly. It
# also backstops cleanup: if the post-import delete fails, the object stops
# being fetchable once this elapses.
_PRESIGN_TTL_SECONDS = 900

# Import-job polling. AEM returns 202 + a job URL; the asset is not in the DAM
# until that job reports COMPLETED.
_POLL_TRIES = 30
_POLL_INTERVAL_SECONDS = 2

_DEFAULT_REGION = "us-west-2"

# Must stay inside the prefix the IAM policy allows s3:DeleteObject on, or the
# post-import cleanup silently degrades to "leave it there forever".
_DELETABLE_PREFIX = "aem-assets/"
_DEFAULT_PREFIX = _DELETABLE_PREFIX


def _setting(cfg: dict, key: str, default: str = None) -> str:
    """Read a staging setting from the environment first, then the config file."""
    value = os.environ.get(key) or cfg.get(key) or default
    return value.strip() if isinstance(value, str) else value


def _staging_config(cfg: dict) -> dict:
    bucket = _setting(cfg, "AWS_S3_STAGING_BUCKET")
    if not bucket:
        raise click.ClickException(
            "Asset upload needs an S3 staging bucket.\n"
            "Set AWS_S3_STAGING_BUCKET (and AWS credentials) in your environment "
            "or in ~/.cf-agent/config. See `cf-agent asset upload --help`."
        )

    prefix = _setting(cfg, "AWS_S3_STAGING_PREFIX", _DEFAULT_PREFIX)
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    return {
        "bucket": bucket,
        "region": _setting(cfg, "AWS_S3_STAGING_REGION", _DEFAULT_REGION),
        "prefix": prefix,
    }


def _resolve_credentials() -> dict:
    """Find AWS credentials for the staging bucket, most-explicit source first.

    1. Shell environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
       AWS_SESSION_TOKEN) — handy for CI/automation.
    2. Nothing → empty, so boto3 falls back to its own chain (a shared ~/.aws
       profile, SSO, or an instance role).

    Secrets are never read from the plaintext ~/.cf-agent/config file.
    """
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        creds = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        session_token = os.environ.get("AWS_SESSION_TOKEN")
        if session_token:
            creds["aws_session_token"] = session_token
        return creds
    return {}


def _s3_client(stg: dict):
    try:
        import boto3  # imported lazily so the CLI works without boto3 installed
        from botocore.config import Config
    except ImportError:
        raise click.ClickException(
            "Asset upload requires boto3. Install it with:\n"
            "    pip install 'cf-agent[upload]'   (or: pip install boto3)"
        )

    # Virtual-host addressing + SigV4 pinned to the bucket's own region. Without
    # an explicit region boto3 would sign against the global s3.amazonaws.com
    # host, which S3 rejects with a 403 for a bucket that lives elsewhere.
    return boto3.client(
        "s3",
        region_name=stg["region"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
        **_resolve_credentials(),
    )


def _s3_error(exc: Exception, action: str) -> str:
    """Turn a boto3/botocore exception into a short, actionable message."""
    text = str(exc)
    if "AccessDenied" in text:
        match = re.search(r"perform:\s*(\S+)", text)
        denied = match.group(1) if match else "the required S3 action"
        return (
            f"{action}: access denied for {denied}. Grant that action to the "
            "CLI's IAM user on the staging bucket/prefix (s3:PutObject and "
            "s3:GetObject are both required)."
        )
    return f"{action}: {text}"


def stage_file(cfg: dict, local_path: str, dest_name: str = None, on_status=None) -> dict:
    """Upload ``local_path`` to the S3 staging bucket and pre-sign a GET URL.

    Returns ``{"bucket", "key", "url", "content_type", "size"}``. Callers are
    responsible for removing the object afterwards via ``cleanup_staged``;
    ``stage_and_import`` does this in a ``finally`` block.
    """
    path = Path(local_path).expanduser()
    if not path.is_file():
        raise click.ClickException(f"File not found: {local_path}")

    stg = _staging_config(cfg)
    s3 = _s3_client(stg)

    name = dest_name or path.name
    # A random segment keeps concurrent uploads of the same file name from
    # clobbering each other — the staged key is throwaway, only the DAM name matters.
    key = f"{stg['prefix']}{uuid.uuid4().hex}/{name}"
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    if on_status:
        on_status(f"Staging to s3://{stg['bucket']}/{key}")

    try:
        s3.upload_file(
            str(path),
            stg["bucket"],
            key,
            ExtraArgs={"ContentType": content_type},
        )
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": stg["bucket"], "Key": key},
            ExpiresIn=_PRESIGN_TTL_SECONDS,
        )
    except Exception as exc:
        raise click.ClickException(_s3_error(exc, "Could not stage file to S3"))

    return {
        "bucket": stg["bucket"],
        "key": key,
        "url": url,
        "content_type": content_type,
        "size": path.stat().st_size,
    }


def cleanup_staged(cfg: dict, staged: dict, on_status=None) -> bool:
    """Delete the staged S3 object. Best-effort — never fatal.

    The IAM policy grants s3:DeleteObject only under ``_DELETABLE_PREFIX``, so a
    staging prefix outside it produces an AccessDenied here. That is a
    misconfiguration worth surfacing (the object would linger indefinitely), but
    the asset is already in AEM by this point, so it must not fail the upload.
    """
    try:
        s3 = _s3_client({"region": _setting(cfg, "AWS_S3_STAGING_REGION", _DEFAULT_REGION)})
        s3.delete_object(Bucket=staged["bucket"], Key=staged["key"])
        if on_status:
            on_status(f"Removed staged object s3://{staged['bucket']}/{staged['key']}")
        return True
    except Exception as exc:
        detail = ""
        if not staged["key"].startswith(_DELETABLE_PREFIX):
            detail = (
                f" The staging prefix is outside '{_DELETABLE_PREFIX}', which is the "
                "only prefix this IAM user may delete from — set "
                f"AWS_S3_STAGING_PREFIX to '{_DELETABLE_PREFIX}'."
            )
        click.secho(
            f"  ⚠ Could not delete staged object s3://{staged['bucket']}/{staged['key']}: "
            f"{exc}.{detail} Remove it manually.",
            fg="yellow",
        )
        return False


def preflight_readable(url: str) -> None:
    """Raise a clear error if the staged object can't be read via its pre-signed URL.

    Uses a 1-byte ranged GET so it's cheap. A network hiccup is ignored (the AEM
    import remains the real arbiter); an HTTP 4xx means AEM's fetch would fail the
    same way, so we stop early with an actionable message.
    """
    try:
        resp = httpx.get(url, headers={"Range": "bytes=0-0"}, timeout=15)
    except httpx.HTTPError:
        return

    if resp.status_code >= 400:
        detail = ""
        if "AccessDenied" in resp.text:
            detail = (
                "\nThe staging credential needs s3:GetObject on the bucket/prefix "
                "— a write-only key won't work."
            )
        raise click.ClickException(
            f"The staged file isn't readable via its pre-signed URL (S3 returned "
            f"{resp.status_code}), so AEM cannot fetch it either.{detail}"
        )


def _start_import(cfg: dict, url: str, dam_folder: str, dest_name: str) -> str:
    """POST /adobe/assets/import/fromUrl → returns the job status URL."""
    token = auth.get_token(cfg)
    assets_base = client._assets_base_url(cfg)
    if not assets_base:
        raise click.ClickException(
            "Could not derive the Assets API URL from the selected environment."
        )

    import_url = f"{assets_base}/import/fromUrl"
    resp = httpx.post(
        import_url,
        json={
            "files": [
                {
                    "url": url,
                    "fileName": dest_name,
                }
            ],
            "folder": dam_folder.rstrip("/"),
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Adobe-Accept-Experimental": "1",
        },
        timeout=60,
    )

    if resp.status_code >= 400:
        raise click.ClickException(
            f"AEM rejected the import (HTTP {resp.status_code}):\n{resp.text}"
        )

    job_url = resp.headers.get("location")
    if not job_url:
        raise click.ClickException(
            "AEM accepted the import but returned no job URL to poll."
        )
    return job_url


def _await_import(cfg: dict, job_url: str, on_status=None) -> None:
    """Poll the import job until it reaches a terminal state."""
    token = auth.get_token(cfg)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Adobe-Accept-Experimental": "1",
    }

    for _ in range(_POLL_TRIES):
        try:
            resp = httpx.get(job_url, headers=headers, timeout=30)
        except httpx.HTTPError as exc:
            raise click.ClickException(f"Could not read import job status: {exc}")

        if resp.status_code >= 400:
            raise click.ClickException(
                f"Could not read import job status: HTTP {resp.status_code}\n{resp.text}"
            )

        try:
            body = resp.json()
        except ValueError:
            body = {}

        data = body.get("data", body)
        state = str(data.get("state") or data.get("status") or "").upper()

        # `progress` is an object (not a percentage): it carries the per-file
        # counters — total / imported / failed / skipped — plus a `step` label.
        progress = data.get("progress")
        if not isinstance(progress, dict):
            progress = {}

        if on_status and progress:
            step = str(progress.get("step") or state or "running").lower()
            on_status(
                f"Import {step} — {progress.get('imported', 0)}/{progress.get('total', '?')} file(s)"
            )

        if state in ("COMPLETED", "COMPLETE", "SUCCESS", "SUCCEEDED"):
            # A job can reach COMPLETED with individual files having failed, so
            # the terminal state alone is not proof the asset landed.
            failed = progress.get("failed") or 0
            if failed:
                raise click.ClickException(
                    f"AEM import finished but {failed} of "
                    f"{progress.get('total', '?')} file(s) failed: "
                    f"{data.get('errors') or 'no detail provided'}"
                )
            return

        if state in ("FAILED", "ERROR", "CANCELLED"):
            raise click.ClickException(
                f"AEM import failed: {data.get('errors') or resp.text}"
            )

        time.sleep(_POLL_INTERVAL_SECONDS)

    raise click.ClickException(
        "AEM import did not finish in time. It may still complete — verify with "
        "`cf-agent asset exists`."
    )


def _lookup_asset_id(cfg: dict, dam_path: str):
    """Best-effort assetId lookup so the caller can print/act on it. None if unknown."""
    try:
        token = auth.get_token(cfg)
        assets_base = client._assets_base_url(cfg)
        resp = httpx.post(
            f"{assets_base}/search",
            params={"allowUnsafeSearch": "true"},
            json={
                "query": [{"term": {"repositoryMetadata.repo:path": [dam_path]}}],
                "limit": 1,
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "X-Adobe-Accept-Experimental": "1",
            },
            timeout=15,
        )
        if resp.status_code == 200:
            results = resp.json().get("hits", {}).get("results", [])
            if results:
                return results[0].get("assetId")
    except Exception:
        pass
    return None


def stage_and_import(cfg: dict, local_path: str, dam_folder: str,
                     dest_name: str = None, on_status=None) -> dict:
    """Upload ``local_path`` to AEM under ``dam_folder`` as ``dest_name``.

    Returns ``{"dam_path": ..., "asset_id": ...|None, "staged": {...}}``.

    The S3 staging object is removed before returning, success or failure — see
    ``cleanup_staged`` for the prefix constraint on that.

    ``on_status`` — optional callback(str) for progress messages.
    """
    name = dest_name or Path(local_path).name
    dam_path = f"{dam_folder.rstrip('/')}/{name}"
    staged = stage_file(cfg, local_path, name, on_status=on_status)

    try:
        preflight_readable(staged["url"])

        if on_status:
            on_status(f"Importing into AEM at {dam_path}")

        job_url = _start_import(cfg, staged["url"], dam_folder, name)
        _await_import(cfg, job_url, on_status=on_status)
    finally:
        # Always clean up: a failed import leaves the same orphan a successful
        # one would, and this is the only chance to remove it.
        staged["deleted"] = cleanup_staged(cfg, staged, on_status=on_status)

    return {
        "dam_path": dam_path,
        "asset_id": _lookup_asset_id(cfg, dam_path),
        "staged": staged,
    }
