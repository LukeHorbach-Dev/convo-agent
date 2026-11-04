"""Prints the CHANGELOG.md section for a version (the GitHub release's notes, and Convo's "What's new")."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assistant.changelog import changelog_notes  # noqa: E402

notes = changelog_notes(sys.argv[1], Path(__file__).resolve().parent.parent / "CHANGELOG.md")
if not notes:
    sys.exit(f"CHANGELOG.md has no '## {sys.argv[1]}' section")
print(notes)
