"""Builds Convo.app (and the zip that's shared) from this folder.

    python3 mac/build_app.py [OUT_DIR]        -> OUT_DIR/Convo.app and OUT_DIR/Convo-<version>.zip

The app is a small Mac app bundle: its program (mac/Convo) sets Convo up on first open (mac/setup.sh, in Terminal),
then starts it and opens its window. Convo's code travels inside the app; your data stays in
~/Library/Application Support/Convo, so a newer app replaces the old one without losing anything.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "assistant" / "__init__.py").read_text()).group(1)


def build(out: Path) -> tuple[Path, Path]:
    v = version()
    app = out / "Convo.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    res = contents / "Resources"
    code = res / "app"
    (code / "mac").mkdir(parents=True)
    (contents / "Info.plist").write_text((ROOT / "mac" / "Info.plist").read_text().replace("__VERSION__", v))
    shutil.copy(ROOT / "mac" / "Convo", contents / "MacOS" / "Convo")
    (contents / "MacOS" / "Convo").chmod(0o755)
    shutil.copy(ROOT / "mac" / "AppIcon.icns", res / "AppIcon.icns")
    for pkg in ("agent", "assistant"):
        shutil.copytree(ROOT / pkg, code / pkg, ignore=SKIP)
    shutil.copy(ROOT / "requirements.lock", code / "requirements.lock")
    shutil.copy(ROOT / "mac" / "setup.sh", code / "mac" / "setup.sh")
    (code / "mac" / "setup.sh").chmod(0o755)
    (code / "VERSION").write_text(v + "\n")
    seed = code / "seed"
    seed.mkdir()
    for f in ("profile.json", "never.txt", "intro-list.txt"):
        src = ROOT / "data" / f
        if src.exists():
            shutil.copy(src, seed / f)
    # Read me, next to the app in the zip.
    (out / "Read me first.txt").write_text((ROOT / "mac" / "READ-ME.txt").read_text().replace("__VERSION__", v))
    z = out / f"Convo-{v}.zip"
    if z.exists():
        z.unlink()
    # The system zip keeps the program's "can run" permission, which the Mac needs.
    subprocess.run(["zip", "-qry", z.name, "Convo.app", "Read me first.txt"], cwd=out, check=True)
    return app, z


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "dist").resolve()
    out.mkdir(parents=True, exist_ok=True)
    app, z = build(out)
    print(f"built {app}\nzip   {z}")
