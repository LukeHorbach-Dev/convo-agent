"""The notes for one version from CHANGELOG.md: Convo's "What's new", and the GitHub release's description.
(No third-party imports, so the release workflow can use it before anything is installed.)"""
from __future__ import annotations

import re
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def changelog_notes(version: str, path: Path | None = None) -> str | None:
    try:
        text = (path or CHANGELOG).read_text()
    except OSError:
        return None
    m = re.search(rf"^## {re.escape(version)}[ \t]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None
