"""Encrypted storage for AWS staging credentials via the OS keychain.

Credentials set through ``cf-agent asset credentials set`` are stored in the
operating system's secret store — macOS Keychain, Windows Credential Manager,
or the Linux Secret Service — which keeps them **encrypted at rest and gated by
the OS**, never in a plaintext file. Only a process running as the same user,
through this application's service name, can read them back.

The whole ``keyring`` dependency is contained here so the rest of the CLI (and
tests) don't need a keychain backend present.
"""

import json
import sys

import click

_SERVICE = "cf-agent"
_ACCOUNT = "aws-staging-credentials"

# Windows Credential Manager caps a credential blob at CRED_MAX_CREDENTIAL_BLOB_SIZE
# (2560 bytes), and keyring writes the value as UTF-16 — so the practical ceiling is
# ~1280 characters. macOS Keychain has no comparable limit, which means a payload
# with a long AWS session token can store fine on a Mac and fail on Windows. Check it
# on every platform so the two behave the same way.
_MAX_WINDOWS_BLOB_CHARS = 1280

# Backends that store secrets in plaintext. keyring only selects these if someone has
# installed `keyrings.alt`, but that would silently defeat the point of using a keychain.
_INSECURE_BACKENDS = {"PlaintextKeyring", "EncryptedKeyring", "UncryptedFileKeyring"}


def _load_keyring(required: bool = True):
    """Return the keyring module, or None.

    ``required=True`` raises a friendly error if the package is missing — used by
    the interactive set/clear commands. ``required=False`` returns None instead —
    used on the read path so an upload can still fall back to env vars / the boto3
    chain when keyring isn't installed.
    """
    try:
        import keyring
    except ImportError:
        if required:
            raise click.ClickException(
                "Storing credentials securely requires the 'keyring' package.\n"
                "Install it with:  pip install keyring"
            )
        return None
    return keyring


def _backend_error(exc: Exception) -> click.ClickException:
    hint = (
        "On macOS this uses the login Keychain and on Windows the Credential "
        "Manager — both should work out of the box. On Windows, make sure "
        "'pywin32-ctypes' installed alongside keyring. On Linux install a Secret "
        "Service provider (e.g. gnome-keyring)."
    )
    return click.ClickException(
        f"No usable OS keychain backend is available ({exc}).\n{hint}\n"
        "Alternatively set the AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
        "environment variables instead."
    )


def backend_name() -> str:
    """Human-readable name of the keychain backend keyring would use, or None."""
    keyring = _load_keyring(required=False)
    if keyring is None:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    name = backend.__class__.__name__
    module = backend.__class__.__module__
    if "macOS" in module or "OS_X" in module:
        return f"macOS Keychain ({name})"
    if "Windows" in module:
        return f"Windows Credential Manager ({name})"
    if "SecretService" in module or "kwallet" in module.lower():
        return f"Linux Secret Service ({name})"
    return f"{module}.{name}"


def _check_backend_secure() -> None:
    """Warn if keyring picked a backend that does not actually encrypt."""
    keyring = _load_keyring(required=False)
    if keyring is None:
        return
    try:
        name = keyring.get_keyring().__class__.__name__
    except Exception:
        return
    if name in _INSECURE_BACKENDS:
        click.secho(
            f"  ⚠ keyring selected the '{name}' backend, which does NOT encrypt "
            "secrets at rest. Uninstall 'keyrings.alt' so the OS keychain is used "
            "instead, or use environment variables.",
            fg="yellow",
        )


def set_aws_credentials(access_key_id: str, secret_access_key: str,
                        session_token: str = None) -> None:
    """Encrypt and store the credential set in the OS keychain."""
    keyring = _load_keyring(required=True)
    payload = {
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
    }
    if session_token:
        payload["session_token"] = session_token

    blob = json.dumps(payload)
    if len(blob) > _MAX_WINDOWS_BLOB_CHARS:
        message = (
            f"These credentials serialise to {len(blob)} characters, over the "
            f"{_MAX_WINDOWS_BLOB_CHARS}-character limit the Windows Credential "
            "Manager enforces (a long AWS session token is the usual cause)."
        )
        if sys.platform == "win32":
            raise click.ClickException(
                message + "\nUse the AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / "
                "AWS_SESSION_TOKEN environment variables for this credential instead."
            )
        # Storing would succeed here but fail for a teammate on Windows, so say so
        # rather than let the difference surface on someone else's machine.
        click.secho(f"  ⚠ {message} It will store on this machine but not on Windows.",
                    fg="yellow")

    _check_backend_secure()

    try:
        keyring.set_password(_SERVICE, _ACCOUNT, blob)
    except Exception as exc:
        raise _backend_error(exc)


def get_aws_credentials():
    """Return the stored credential dict, or None if nothing is stored / no keyring.

    Never raises on a missing backend — the caller treats None as "not set" and
    falls through to other credential sources.
    """
    keyring = _load_keyring(required=False)
    if keyring is None:
        return None

    try:
        raw = keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception:
        return None

    if not raw:
        return None

    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None

    if not data.get("access_key_id") or not data.get("secret_access_key"):
        return None
    return data


def clear_aws_credentials() -> bool:
    """Delete the stored credential set. Returns True if something was removed."""
    keyring = _load_keyring(required=True)
    import keyring.errors  # resolved before the try so the except clause is safe

    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
        return True
    except keyring.errors.PasswordDeleteError:
        # Raised by both the macOS and Windows backends when nothing is stored.
        return False
    except Exception as exc:
        raise _backend_error(exc)
