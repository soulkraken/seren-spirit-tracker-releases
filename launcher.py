"""Stable launcher and updater for the packaged Seren Spirit Tracker."""

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile


APP_EXE_NAME = "Seren Spirit Tracker App.exe"
CONFIG_NAME = "updater_config.json"
VERSION_NAME = "version.json"
USER_AGENT = "SerenSpiritTracker-Updater/1.0"

MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONERROR = 0x00000010
MB_ICONINFORMATION = 0x00000040
IDYES = 6


def install_root():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = install_root()
APP_DIR = ROOT / "app"
APP_EXE = APP_DIR / APP_EXE_NAME
CONFIG_PATH = ROOT / CONFIG_NAME
VERSION_PATH = APP_DIR / VERSION_NAME


def message_box(text, title, flags=MB_OK):
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return default


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def request_bytes(url, timeout=15):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_latest_release(repository):
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    return json.loads(request_bytes(url).decode("utf-8"))


def find_asset(release, predicate):
    for asset in release.get("assets", []):
        name = str(asset.get("name", ""))
        if predicate(name):
            return asset
    return None


def safe_extract(zip_path, destination):
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise ValueError("The update contains an unsafe file path.") from exc
        archive.extractall(destination)


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_update(release):
    update_asset = find_asset(
        release,
        lambda name: (
            name.startswith("Seren-Spirit-Tracker-Update-v")
            and name.endswith(".zip")
        ),
    )
    if not update_asset:
        raise RuntimeError("This release does not contain an updater package.")

    checksum_asset = find_asset(
        release,
        lambda name: name == f"{update_asset['name']}.sha256",
    )
    if not checksum_asset:
        raise RuntimeError("This release does not contain an update checksum.")

    message_box(
        "The update will now download and install. This may take a minute.",
        "Seren Spirit Tracker Update",
        MB_OK | MB_ICONINFORMATION,
    )

    with tempfile.TemporaryDirectory(prefix="seren_tracker_update_") as temp_name:
        temp_dir = Path(temp_name)
        archive_path = temp_dir / update_asset["name"]
        archive_path.write_bytes(request_bytes(update_asset["browser_download_url"], 120))

        checksum_text = request_bytes(
            checksum_asset["browser_download_url"], 30
        ).decode("utf-8", errors="replace")
        expected_hash_match = re.search(r"\b[a-fA-F0-9]{64}\b", checksum_text)
        if not expected_hash_match:
            raise RuntimeError("The published checksum is invalid.")

        expected_hash = expected_hash_match.group(0).lower()
        if file_sha256(archive_path) != expected_hash:
            raise RuntimeError("The downloaded update failed checksum verification.")

        extract_dir = temp_dir / "extracted"
        extract_dir.mkdir()
        safe_extract(archive_path, extract_dir)
        new_app_dir = extract_dir / "app"
        new_app_exe = new_app_dir / APP_EXE_NAME
        if not new_app_exe.is_file() or not (new_app_dir / VERSION_NAME).is_file():
            raise RuntimeError("The downloaded update has an unexpected layout.")

        backup_dir = ROOT / "app.update-backup"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

        try:
            if APP_DIR.exists():
                APP_DIR.replace(backup_dir)
            shutil.move(str(new_app_dir), str(APP_DIR))
        except Exception:
            if APP_DIR.exists():
                shutil.rmtree(APP_DIR, ignore_errors=True)
            if backup_dir.exists():
                backup_dir.replace(APP_DIR)
            raise
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)


def launch_tracker():
    if not APP_EXE.is_file():
        message_box(
            f"The tracker application was not found:\n{APP_EXE}",
            "Seren Spirit Tracker",
            MB_OK | MB_ICONERROR,
        )
        return

    subprocess.Popen([str(APP_EXE)], cwd=str(APP_DIR))


def check_for_update():
    config = load_json(CONFIG_PATH, {}) or {}
    repository = str(config.get("github_repository", "")).strip().strip("/")
    if (
        not config.get("check_for_updates", True)
        or not repository
        or repository.startswith("REPLACE_ME/")
    ):
        return

    installed = load_json(VERSION_PATH, {}) or {}
    installed_version = version_tuple(installed.get("version", "0.0.0"))
    if installed_version is None:
        return

    # A failed check is intentionally silent so offline launches stay quick.
    try:
        release = fetch_latest_release(repository)
        latest_version = version_tuple(release.get("tag_name", ""))
    except Exception:
        return

    if latest_version is None or latest_version <= installed_version:
        return

    release_name = release.get("name") or release.get("tag_name")
    notes = str(release.get("body") or "No release notes were provided.").strip()
    if len(notes) > 1200:
        notes = notes[:1197] + "..."

    answer = message_box(
        f"{release_name} is available.\n\n{notes}\n\n"
        "Would you like to download and install it now?",
        "Seren Spirit Tracker Update Available",
        MB_YESNO | MB_ICONINFORMATION,
    )
    if answer != IDYES:
        return

    try:
        install_update(release)
    except Exception as exc:
        message_box(
            f"The update could not be installed. The existing version will open.\n\n{exc}",
            "Seren Spirit Tracker Update Failed",
            MB_OK | MB_ICONERROR,
        )


def main():
    check_for_update()
    launch_tracker()


if __name__ == "__main__":
    main()
