"""Databricks token resolution for the parity CLI.

``DATABRICKS_TOKEN`` if set (CI); otherwise mint one from the CLI's OAuth
cache (local U2M). Same two-path resolution parvum's exporter already uses
for the identical problem — a local Python process reading Databricks over
the SQL Statement API rather than through the CLI's own commands.
"""

from __future__ import annotations

import json
import os
import subprocess


class DatabricksAuthError(RuntimeError):
    """No usable Databricks credential could be found."""


def resolve_token(host: str) -> str:
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    if token:
        return token
    try:
        minted = subprocess.run(
            ["databricks", "auth", "token", "--host", host, "--output", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(minted.stdout)["access_token"]
    except (OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as exc:
        raise DatabricksAuthError(
            "no DATABRICKS_TOKEN and the databricks CLI could not mint one "
            f"(run `databricks auth login`): {exc}"
        ) from exc
