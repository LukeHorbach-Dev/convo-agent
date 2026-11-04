"""Updates from GitHub: Convo looks at the releases of its GitHub project, and installs a newer one when Luke clicks
Update.

How an update goes (nothing happens without the click):
  1. download that release's code (convo-<version>.tar.gz) and check it against the release's SHA256SUMS.txt
  2. unpack it into ~/Library/Application Support/Convo/versions/<version>
  3. install any new parts it needs (only when its requirements.lock changed), and check it starts
  4. back up the database, then switch to it and restart Convo; the window reconnects by itself

The version Convo came from is kept, so "Undo update" switches straight back. The app picks which copy to run with the
files `current` (the downloaded version in use) and `previous` next to the versions folder. Installing a new Convo.app by
hand always wins over downloaded updates (see mac/Convo).

The GitHub project is private, so Convo needs a GitHub key that can only read it (AGENT_GITHUB_TOKEN in .env, saved
from Settings, owner-only like the AI key). The key is only ever sent to api.github.com.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from typing import Callable

import httpx

from . import __version__
from .changelog import changelog_notes  # noqa: F401  (used by app.py through here)

DEFAULT_REPO = "LukeHorbach-Dev/convo-agent"
API = os.environ.get("CONVO_GITHUB_API", "https://api.github.com")      # changeable only for testing
CHECK_EVERY_S = 6 * 3600
KEEP_VERSIONS = 3
CODE = Path(__file__).resolve().parent.parent          # the folder this copy of Convo runs from
TOKEN_RX = re.compile(r"^(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})$")


def parse_version(v: str) -> tuple[int, ...]:
    """'5.0.0' > '5.0.0-preview.2' > '5.0.0-preview.1' > '4.1.1'. Unknown shapes sort lowest."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-(?:preview|beta|rc)\.?(\d+))?", (v or "").strip())
    if not m:
        return (0,)
    major, minor, patch, pre = m.groups()
    return (int(major), int(minor), int(patch)) + ((0, int(pre)) if pre else (1, 0))


def is_preview(v: str) -> bool:
    return "-" in v


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_uv() -> str | None:
    found = shutil.which("uv")
    for c in (found, Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv", "/opt/homebrew/bin/uv", "/usr/local/bin/uv"):
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


class UpdateError(Exception):
    """Something Luke can read."""


class Updates:
    def __init__(self, home: Path | None, token: Callable[[], str | None], previews: Callable[[], bool] = lambda: False,
                 repo: str = DEFAULT_REPO, current: str = __version__, http: httpx.Client | None = None,
                 log: Callable[[str, str], None] = lambda kind, text: None, backup: Callable[[str], object] = lambda label: None,
                 restart: Callable[[Path], None] | None = None, python: str = sys.executable, run=subprocess.run):
        self.home = Path(home) if home else None
        self.token, self.previews, self.repo, self.current = token, previews, repo, current
        self.http = http or httpx.Client(timeout=httpx.Timeout(20, read=60), follow_redirects=True)
        self.log, self.backup, self._restart, self.python, self.run = log, backup, restart, python, run
        self.lock = threading.Lock()
        self.state = {"status": "idle", "latest": None, "error": None, "progress": None, "checked_at": None}
        self._told: set[str] = set()

    # ------------------------------------------------------------------ what the screens show
    @property
    def can_install(self) -> bool:
        return self.home is not None

    def previous(self) -> str | None:
        if not self.home:
            return None
        v = self._read("previous")
        return v if v and v != self.current and (self.home / "versions" / v / "VERSION").exists() else None

    def summary(self) -> dict:
        latest = self.state["latest"]
        return {**self.state, "current": self.current, "repo": self.repo, "connected": bool(self.token()),
                "can_install": self.can_install, "previous": self.previous(),
                "available": bool(latest and parse_version(latest["version"]) > parse_version(self.current)),
                "why_not": None if self.can_install else
                "This copy runs straight from its code folder, so it updates with git rather than from here."}

    # ------------------------------------------------------------------ GitHub
    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Convo-updater"}
        t = self.token()
        if t:
            h["Authorization"] = f"Bearer {t}"
        return h

    def _get(self, url: str, **kw) -> httpx.Response:
        try:
            r = self.http.get(url, headers={**self._headers(), **kw.pop("headers", {})}, **kw)
        except httpx.HTTPError as e:
            raise UpdateError("Couldn't reach GitHub. Check the internet connection and try again.") from e
        if r.status_code == 401:
            raise UpdateError("GitHub says the key isn't valid any more (it may have expired). Make a new one in "
                              "Settings, Updates.")
        if r.status_code in (403, 404):
            if not self.token():
                raise UpdateError("Convo's GitHub project is private, so Convo needs your GitHub key to see updates. "
                                  "Add it in Settings, Updates.")
            if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
                raise UpdateError("GitHub asked Convo to wait a while before looking again.")
            raise UpdateError(f"GitHub didn't let this key read {self.repo}. When you make the key, choose that "
                              "project under 'Only select repositories' and give it 'Contents: Read-only'.")
        if r.status_code >= 400:
            raise UpdateError(f"GitHub had a problem ({r.status_code}). Try again in a few minutes.")
        return r

    def check(self) -> dict:
        """Ask GitHub for the newest release this copy should offer."""
        with self.lock:
            if self.state["status"] in ("downloading", "installing"):
                return self.summary()
            self.state.update(status="checking", error=None)
        try:
            releases = self._get(f"{API}/repos/{self.repo}/releases", params={"per_page": 30}).json()
            want_previews = self.previews() or is_preview(self.current)
            best = None
            for rel in releases:
                v = (rel.get("tag_name") or "").lstrip("v")
                if rel.get("draft") or parse_version(v) == (0,) or (rel.get("prerelease") and not want_previews):
                    continue
                if best is None or parse_version(v) > parse_version(best["version"]):
                    assets = {a["name"]: {"url": a["url"], "size": a.get("size", 0)} for a in rel.get("assets", [])}
                    best = {"version": v, "notes": (rel.get("body") or "").strip(), "published": rel.get("published_at"),
                            "prerelease": bool(rel.get("prerelease")), "page": rel.get("html_url"), "assets": assets}
            newer = best if best and parse_version(best["version"]) > parse_version(self.current) else None
            self.state.update(status="available" if newer else "up_to_date", latest=best, checked_at=time.time())
            if newer and newer["version"] not in self._told:
                self._told.add(newer["version"])
                self.log("info", f"Convo {newer['version']} is ready to install (Settings, Updates).")
        except UpdateError as e:
            self.state.update(status="error", error=str(e), checked_at=time.time())
        return self.summary()

    # ------------------------------------------------------------------ installing
    def start_install(self) -> dict:
        if not self.can_install:
            raise UpdateError(self.summary()["why_not"])
        latest = self.state["latest"]
        if not latest or parse_version(latest["version"]) <= parse_version(self.current):
            raise UpdateError("There's no newer version to install. Press 'Check for updates' first.")
        with self.lock:
            if self.state["status"] in ("downloading", "installing", "restarting"):
                raise UpdateError("An update is already being installed.")
            self.state.update(status="downloading", error=None, progress=0)
        threading.Thread(target=self._install_safely, args=(latest,), name="update", daemon=True).start()
        return self.summary()

    def _install_safely(self, release: dict) -> None:
        try:
            self.install(release)
        except UpdateError as e:
            self.state.update(status="error", error=str(e), progress=None)
            self.log("warn", f"The update to {release['version']} didn't install: {e} Convo carries on as it was.")
        except Exception as e:                       # never leave the screen saying "installing" forever
            self.state.update(status="error", error=f"The update stopped unexpectedly ({e}).", progress=None)
            self.log("warn", f"The update to {release['version']} stopped unexpectedly ({e}). Convo carries on as it was.")

    def install(self, release: dict) -> Path:
        v = release["version"]
        assets = release.get("assets") or {}
        name = f"convo-{v}.tar.gz"
        if name not in assets or "SHA256SUMS.txt" not in assets:
            raise UpdateError(f"The {v} release on GitHub is missing its files. Claude needs to publish it again.")
        work = self.home / "updates"
        work.mkdir(parents=True, exist_ok=True)
        sums = self._get(assets["SHA256SUMS.txt"]["url"], headers={"Accept": "application/octet-stream"}).text
        want = next((line.split()[0] for line in sums.splitlines() if line.strip().endswith(name)), None)
        if not want:
            raise UpdateError(f"The {v} release's checksum list doesn't mention {name}.")
        tgz = work / name
        self._download(assets[name]["url"], tgz, assets[name].get("size") or 0)
        if sha256(tgz) != want:
            tgz.unlink(missing_ok=True)
            raise UpdateError("The download didn't match GitHub's checksum (it may have been cut short). Try again.")

        self.state.update(status="installing", progress=None)
        versions = self.home / "versions"
        target = versions / v
        partial = versions / f".{v}.partial"
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(parents=True)
        with tarfile.open(tgz) as tf:
            tf.extractall(partial, filter="data")
        if (partial / "VERSION").read_text().strip() != v or not (partial / "assistant" / "__init__.py").exists():
            shutil.rmtree(partial, ignore_errors=True)
            raise UpdateError(f"The {v} download doesn't contain Convo {v}.")
        shutil.rmtree(target, ignore_errors=True)
        partial.rename(target)
        tgz.unlink(missing_ok=True)

        self._parts_for(target)
        self._check_starts(target, v)
        self.backup(f"before-update-{v}")
        self._switch(v)
        self._tidy()
        self.log("good", f"Convo {v} is installed. Restarting.")
        self.state.update(status="restarting", progress=None)
        if self._restart:
            self._restart(target)
        return target

    def _download(self, url: str, dest: Path, size: int) -> None:
        tmp = dest.with_suffix(".part")
        try:
            with self.http.stream("GET", url, headers={**self._headers(), "Accept": "application/octet-stream"}) as r:
                if r.status_code >= 400:
                    self._get(url)                  # turns the status into a readable UpdateError
                total = int(r.headers.get("content-length") or size or 0)
                done = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 16):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            self.state["progress"] = min(99, int(done * 100 / total))
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise UpdateError("The download stopped part way (check the internet connection) and nothing was changed.") from e
        tmp.replace(dest)

    def _lock_file(self) -> Path:
        return Path(self.python).parent.parent / ".convo-lock"

    def _parts_for(self, code: Path) -> None:
        """Install the Python parts the new version lists, only if they changed (most updates skip this)."""
        lock = code / "requirements.lock"
        if not lock.exists():
            return
        want = sha256(lock)
        have = self._lock_file().read_text().strip() if self._lock_file().exists() else None
        if have == want:
            return
        uv = find_uv()
        if not uv:
            raise UpdateError("Convo couldn't find uv to install the new parts. Download Convo again from GitHub instead.")
        self.state["progress"] = None
        r = self.run([uv, "pip", "install", "-q", "-p", self.python, "-r", str(lock)], capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise UpdateError("Installing the new parts didn't work: " + (r.stderr or r.stdout or "").strip()[-400:])
        self.run([self.python, "-m", "playwright", "install", "chromium"], capture_output=True, text=True, timeout=900)
        self._lock_file().write_text(want + "\n")

    def _check_starts(self, code: Path, v: str) -> None:
        env = {**os.environ, "PYTHONPATH": str(code), "PYTHONDONTWRITEBYTECODE": "1"}
        r = self.run([self.python, "-c", "import assistant, assistant.app, assistant.hub; print(assistant.__version__)"],
                     capture_output=True, text=True, timeout=120, env=env, cwd=str(self.home))
        if r.returncode != 0 or r.stdout.strip() != v:
            raise UpdateError(f"Convo {v} didn't start in a test run, so Convo stayed on {self.current}. "
                              + (r.stderr or "").strip()[-300:])

    def _read(self, name: str) -> str | None:
        try:
            return (self.home / name).read_text().strip() or None
        except OSError:
            return None

    def _write(self, name: str, value: str) -> None:
        tmp = self.home / f".{name}.tmp"
        tmp.write_text(value + "\n")
        tmp.replace(self.home / name)

    def _switch(self, v: str) -> None:
        if self.current != v:
            self._write("previous", self.current)
            if CODE.parent.name != "versions" and not (self.home / "versions" / self.current).exists():
                # The copy inside Convo.app: remember it so Undo can go back to it.
                try:
                    shutil.copytree(CODE, self.home / "versions" / self.current,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "seed"))
                except OSError:
                    pass
        self._write("current", v)
        self._write(".installed", v)

    def _tidy(self) -> None:
        versions = self.home / "versions"
        keep = {self._read("current"), self._read("previous")}
        old = sorted((p for p in versions.iterdir() if p.is_dir() and not p.name.startswith(".")),
                     key=lambda p: parse_version(p.name), reverse=True)
        for p in old[KEEP_VERSIONS:]:
            if p.name not in keep:
                shutil.rmtree(p, ignore_errors=True)

    def undo(self) -> dict:
        """Go back to the version before the last update."""
        prev = self.previous()
        if not prev:
            raise UpdateError("There's no earlier version kept to go back to.")
        code = self.home / "versions" / prev
        self.state.update(status="installing", error=None)
        try:
            self._parts_for(code)
            self._check_starts(code, prev)
        except UpdateError as e:
            self.state.update(status="error", error=str(e))
            raise
        self.backup(f"before-undo-to-{prev}")
        self._switch(prev)
        self.log("info", f"Going back to Convo {prev}. Restarting.")
        self.state.update(status="restarting")
        if self._restart:
            self._restart(code)
        return self.summary()

    # ------------------------------------------------------------------ in the background
    def watch(self, stop: threading.Event, first_after_s: float = 30) -> None:
        if stop.wait(first_after_s):
            return
        while True:
            if self.token() or self.state["status"] != "error":
                self.check()
            if stop.wait(CHECK_EVERY_S):
                return


def restart_into(code: Path, home: Path, port: int, quit_now: Callable[[], None]) -> None:
    """Start Convo again from `code` once this one has let go of its port, then quit this one."""
    logs = Path(os.environ.get("CONVO_LOGS", home / "data" / "logs"))
    logs.mkdir(parents=True, exist_ok=True)
    out = open(logs / "convo.log", "ab")
    env = {**os.environ, "PYTHONPATH": str(code), "CONVO_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.Popen([sys.executable, "-m", "assistant", "run", "--wait-for-port"], stdout=out, stderr=out,
                     stdin=subprocess.DEVNULL, cwd=str(home), env=env, start_new_session=True)
    threading.Timer(1.0, quit_now).start()


def demo_tarball(version: str) -> bytes:
    """A tiny release payload, for tests."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("VERSION", version + "\n"), ("assistant/__init__.py", f'__version__ = "{version}"\n'),
                           ("requirements.lock", "fastapi==0.115.6\n")):
            raw = data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
    return buf.getvalue()
