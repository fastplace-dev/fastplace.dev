"""Key & environment secret commands — APP_KEY generation, env encryption (spec #32, #55)."""

from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

import typer

from fastplace.cli.database import _ensure_example_keys, _upsert_env_lines
from fastplace.console import console

keys_app = typer.Typer(help="Application key management.")


def _project_root() -> Path:
    """The cwd when it is a Fastplace project; a friendly exit otherwise."""
    root = Path.cwd()
    if not (root / "asgi.py").is_file():
        console.print(
            "[red]not inside a Fastplace project[/] — run this from a project root "
            "(the directory containing asgi.py)."
        )
        raise typer.Exit(code=1)
    return root


def _active_app_key(env_text: str) -> str | None:
    """The non-empty, uncommented APP_KEY value in .env text, if any.

    ``fastplace new`` ships a generated key, so an active value means one was
    deliberately chosen — silently replacing it would invalidate every signed
    session, CSRF token, and JWT the app ever issued.
    """
    for line in env_text.splitlines():
        match = re.match(r"^\s*APP_KEY\s*=\s*(\S+)\s*$", line)
        if match is not None:
            return match.group(1)
    return None


@keys_app.command("key:generate")
def key_generate(
    show: bool = typer.Option(
        False, "--show", help="Print a fresh key to stdout without writing .env."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace an already-set APP_KEY (the previous .env is kept as .env.bak).",
    ),
) -> None:
    """Generate an APP_KEY secret into .env (--show prints one without writing)."""
    root = _project_root()
    key = secrets.token_urlsafe(48)  # 64 url-safe chars — the `fastplace new` format
    if show:
        # Raw value only, markup off — pipeable straight into a .env edit.
        console.print(key, markup=False, highlight=False)
        return

    env = root / ".env"
    if not env.exists():
        example = root / ".env.example"
        text = example.read_text() if example.exists() else ""
    else:
        # One read serves the refusal check, the backup, and the rewrite.
        text = env.read_text()
        current = _active_app_key(text)
        if current is not None and not force:
            console.print("[red]refusing to overwrite[/] APP_KEY — already set in .env")
            console.print(
                "re-run with [cyan]--force[/] to rotate it "
                "(the previous .env is kept as .env.bak)"
            )
            raise typer.Exit(code=1)
        if current is not None and text.strip():
            shutil.copyfile(env, env.parent / (env.name + ".bak"))
    env.write_text(_upsert_env_lines(text, {"APP_KEY": key}))
    # .env.example documents the key — a commented placeholder, never the secret.
    _ensure_example_keys(root / ".env.example", {"APP_KEY": ""})
    console.print("[green]set[/] APP_KEY in .env")


@keys_app.command("key:rotate")
def key_rotate(
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation."),
) -> None:
    """Rotate APP_KEY: back up, decrypt .env.encrypted under the old key, re-encrypt under the new."""
    from fastplace.auth.encryption import decrypt, encrypt
    from fastplace.config import config, load_env

    root = _project_root()
    load_env()

    env_path = root / ".env"
    text = env_path.read_text(encoding="utf-8")
    old_key = _active_app_key(text)
    if old_key is None:
        console.print("[red]no APP_KEY found in .env[/] — run [bold]fastplace key:generate[/] first.")
        raise typer.Exit(code=1)

    # A destructive command guards unless the environment explicitly says so.
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Rotate APP_KEY in production? Signed URLs, JWTs and 2FA data under the old key stop working.")
    ):
        console.print("[red]aborted[/] — nothing was rotated")
        raise typer.Exit(code=1)

    encrypted_path = root / ".env.encrypted"
    encrypted_bak = root / ".env.encrypted.bak"
    plaintext: str | None = None
    had_encrypted = encrypted_path.is_file()

    # Back up before anything mutates; decrypt under the old key BEFORE the
    # new key is written so a wrong-key hard stop leaves nothing rotated.
    shutil.copyfile(env_path, root / ".env.bak")
    if had_encrypted:
        shutil.copyfile(encrypted_path, encrypted_bak)
        try:
            plaintext = decrypt(encrypted_path.read_text(encoding="utf-8").strip(), key=old_key)
        except ValueError as exc:
            console.print(
                "[red]ciphertext not under current APP_KEY — nothing was rotated.[/]\n"
                f".env is untouched; the backup is at {encrypted_bak.name}. "
                "If .env.encrypted was encrypted under an older key, decrypt it with that key first."
            )
            raise typer.Exit(code=1) from exc

    new_key = secrets.token_urlsafe(48)
    env_path.write_text(_upsert_env_lines(text, {"APP_KEY": new_key}), encoding="utf-8")

    if had_encrypted and plaintext is not None:
        encrypted_path.write_text(encrypt(plaintext, key=new_key) + "\n", encoding="utf-8")

    console.print("[green]APP_KEY rotated.[/] backup: .env.bak")
    if had_encrypted:
        console.print(".env.encrypted re-encrypted under the new key (backup: .env.encrypted.bak)")
    console.print(
        "\n[bold]INVALIDATED[/] (raw-string HMAC under the old key):\n"
        "  - JWT bearer tokens — users must re-login\n"
        "  - signed URLs (email verification, password reset) — regenerate\n"
        "  - 2FA ciphertexts (users.two_factor_secret / two_factor_recovery_codes,\n"
        "    HKDF two_factor derivation) — [red]re-encrypt these columns under the old\n"
        "    key BEFORE cutover, or affected users must re-enroll 2FA[/]\n"
        "[bold]PRESERVED[/] (no APP_KEY involvement — opaque IDs + sha256-at-rest):\n"
        "  - sessions, CSRF tokens, remember-me tokens, personal access tokens"
    )


@keys_app.command("env:encrypt")
def env_encrypt(
    key: str | None = typer.Option(
        None, "--key", help="Encrypt with this key instead of APP_KEY (never written to .env)."
    ),
    force: bool = typer.Option(False, "--force", help="Skip the production confirmation prompt."),
) -> None:
    """Encrypt .env into .env.encrypted, base64-armored (the original .env is kept)."""
    from fastplace.auth.encryption import encrypt
    from fastplace.config import config, load_env
    from fastplace.errors import ConfigurationError

    root = _project_root()
    load_env()
    # A destructive command guards unless the environment explicitly says so.
    if str(config("APP_ENV", default="production")).lower() == "production" and not (
        force or typer.confirm("Encrypt the production .env file?")
    ):
        console.print("[red]aborted[/] — the .env was left untouched")
        raise typer.Exit(code=1)

    env = root / ".env"
    if not env.exists():
        console.print("[red]no .env found[/] — nothing to encrypt")
        raise typer.Exit(code=1)
    try:
        token = encrypt(env.read_text(), key=key)
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    (root / ".env.encrypted").write_text(token + "\n")
    console.print("[green]encrypted[/] .env → .env.encrypted (the original .env is kept)")


@keys_app.command("env:decrypt")
def env_decrypt(
    key: str | None = typer.Option(None, "--key", help="Decrypt with this key instead of APP_KEY."),
) -> None:
    """Restore .env from .env.encrypted (a drifted .env is kept as .env.bak)."""
    from fastplace.auth.encryption import decrypt
    from fastplace.config import load_env
    from fastplace.errors import ConfigurationError

    root = _project_root()
    load_env()
    encrypted = root / ".env.encrypted"
    if not encrypted.exists():
        console.print("[red]no .env.encrypted found[/] — run env:encrypt first")
        raise typer.Exit(code=1)
    try:
        text = decrypt(encrypted.read_text().strip(), key=key)
    except ConfigurationError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(
            f"[red]cannot decrypt .env.encrypted:[/] {exc} — wrong key or a rotated APP_KEY"
        )
        raise typer.Exit(code=1) from exc

    env = root / ".env"
    current = env.read_text() if env.exists() else None
    if current == text:
        console.print("[dim]already in sync[/] — .env matches .env.encrypted")
        return
    if current is not None and current.strip():
        shutil.copyfile(env, env.parent / (env.name + ".bak"))
    env.write_text(text)
    console.print("[green]restored[/] .env from .env.encrypted")
