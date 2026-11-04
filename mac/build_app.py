"""Builds Convo.app (and the zip that's shared) from this folder.

    python3 mac/build_app.py [OUT_DIR]                  -> what a GitHub release holds (see .github/workflows/release.yml)
    python3 mac/build_app.py OUT_DIR --with-my-files    -> a Convo.app for Luke by hand, with his own files as seeds

The app is a small Mac app bundle: its program (mac/Convo) picks the copy of Convo to run (the one inside the app, or a
newer one downloaded by Convo's updater) and hands over to mac/start.sh, which sets Convo up on first open
(mac/setup.sh, in Terminal), then starts it and opens its window. Your data stays in ~/Library/Application Support/Convo,
so new versions never lose anything.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")


def version() -> str:
    return re.search(r'__version__ = "([^"]+)"', (ROOT / "assistant" / "__init__.py").read_text()).group(1)


def stage_code(code: Path, seeds: bool) -> None:
    """Convo's code as it runs on the Mac: the same folder goes inside Convo.app and into the update download."""
    if not (ROOT / "assistant" / "web" / "dist" / "index.html").exists():
        sys.exit("The Command Centre isn't built. Run: cd web && npm ci && npm run build")
    (code / "mac").mkdir(parents=True)
    for pkg in ("agent", "assistant"):
        shutil.copytree(ROOT / pkg, code / pkg, ignore=SKIP)
    for f in ("requirements.lock", "CHANGELOG.md"):
        shutil.copy(ROOT / f, code / f)
    for f in ("setup.sh", "start.sh"):
        shutil.copy(ROOT / "mac" / f, code / "mac" / f)
        (code / "mac" / f).chmod(0o755)
    shutil.copy(ROOT / "mac" / "icon.png", code / "mac" / "icon.png")
    (code / "VERSION").write_text(version() + "\n")
    seed = code / "seed"
    seed.mkdir()
    # Personal files (About me, never-contact list, intro list) only go into a copy built for Luke by hand, never
    # into what's published on GitHub. A new install without them starts from the example About me.
    shutil.copy(ROOT / "data" / "profile.example.json", seed / "profile.example.json")
    if seeds:
        for f in ("profile.json", "never.txt", "intro-list.txt"):
            if (ROOT / "data" / f).exists():
                shutil.copy(ROOT / "data" / f, seed / f)


def build(out: Path, seeds: bool = False) -> dict[str, Path]:
    """Convo.app, Convo-<v>.zip (to install), convo-<v>.tar.gz (what updates download) and SHA256SUMS.txt."""
    v = version()
    app = out / "Convo.app"
    if app.exists():
        shutil.rmtree(app)
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    res = contents / "Resources"
    res.mkdir()
    (contents / "Info.plist").write_text((ROOT / "mac" / "Info.plist").read_text().replace("__VERSION__", v))
    shutil.copy(ROOT / "mac" / "Convo", contents / "MacOS" / "Convo")
    (contents / "MacOS" / "Convo").chmod(0o755)
    shutil.copy(ROOT / "mac" / "AppIcon.icns", res / "AppIcon.icns")
    stage_code(res / "app", seeds)
    (out / "Read me first.txt").write_text((ROOT / "mac" / "READ-ME.txt").read_text().replace("__VERSION__", v))
    z = out / f"Convo-{v}.zip"
    z.unlink(missing_ok=True)
    # The system zip keeps the program's "can run" permission, which the Mac needs.
    subprocess.run(["zip", "-qry", z.name, "Convo.app", "Read me first.txt"], cwd=out, check=True)
    files = {"app": app, "zip": z}
    if not seeds:
        tgz = out / f"convo-{v}.tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            for p in sorted((res / "app").rglob("*")):
                tf.add(p, arcname=str(p.relative_to(res / "app")), recursive=False)
        sums = out / "SHA256SUMS.txt"
        sums.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in (z, tgz)))
        files.update(tgz=tgz, sums=sums)
    return files


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = Path(args[0] if args else ROOT / "dist").resolve()
    out.mkdir(parents=True, exist_ok=True)
    files = build(out, seeds="--with-my-files" in sys.argv)
    for k, p in files.items():
        print(f"{k:5} {p}")
