"""Credential storage (macOS Keychain via keyring) and projects.toml display mapping."""
from __future__ import annotations

import keyring
import tomlkit

from aicosts.paths import projects_toml

SERVICE = "aicosts"


def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass


def require_secret(name: str, hint: str) -> str:
    value = get_secret(name)
    if not value:
        raise SystemExit(
            f"Missing credential '{name}'. Set it with:\n"
            f"    aicosts keys set {name}\n"
            f"({hint})"
        )
    return value


def load_projects() -> dict:
    """Read projects.toml: maps provider IDs to display labels and budget tags.

    Format:
        [[project]]
        label = "openclaw-agent"
        budget_usd_per_day = 10.0
        anthropic_workspace_ids = ["wrkspc_..."]
        openai_project_ids = ["proj_..."]
        openai_api_key_ids = ["key_..."]
        twilio_subaccount_sids = ["AC..."]
    """
    p = projects_toml()
    if not p.exists():
        return {"project": []}
    return tomlkit.parse(p.read_text()).unwrap()


def project_label_for(
    projects_doc: dict,
    *,
    provider: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
    api_key_id: str | None = None,
    subaccount_sid: str | None = None,
) -> str | None:
    """Return the human label for a usage row, if one is configured."""
    for entry in projects_doc.get("project", []):
        if provider == "anthropic" and workspace_id and workspace_id in entry.get("anthropic_workspace_ids", []):
            return entry.get("label")
        if provider == "openai":
            if project_id and project_id in entry.get("openai_project_ids", []):
                return entry.get("label")
            if api_key_id and api_key_id in entry.get("openai_api_key_ids", []):
                return entry.get("label")
        if provider == "twilio" and subaccount_sid and subaccount_sid in entry.get("twilio_subaccount_sids", []):
            return entry.get("label")
    return None
