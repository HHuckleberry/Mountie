"""Checks GitHub for a newer release. Never downloads or installs anything -
only reports a version and a URL for the user to act on."""

import json
import logging
import re
import threading
import urllib.error
import urllib.request

from PyQt5 import QtCore

logger = logging.getLogger(__name__)

RELEASES_API_URL = "https://api.github.com/repos/HHuckleberry/Mountie/releases/latest"
REQUEST_TIMEOUT_SECONDS = 10


def _parse_version(text):
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_update_available(current_version, remote_version):
    current = _parse_version(current_version)
    remote = _parse_version(remote_version)
    if current is None or remote is None:
        return False
    return remote > current


def fetch_latest_release():
    """Return {"version": ..., "url": ...} for the latest GitHub release, or None."""
    request = urllib.request.Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub's API rejects requests with no User-Agent.
            "User-Agent": "Mountie-update-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as error:
        logger.info("Update check failed: %s", error)
        return None
    tag = payload.get("tag_name")
    url = payload.get("html_url")
    if not tag or not url:
        return None
    return {"version": tag.lstrip("v"), "url": url}


class UpdateChecker(QtCore.QObject):
    """Runs the network check off the GUI thread and reports back on it."""

    # Emits the release dict when a newer version exists, or None otherwise.
    finished = QtCore.pyqtSignal(object)

    def check(self, current_version):
        thread = threading.Thread(
            target=self._run, args=(current_version,), daemon=True
        )
        thread.start()

    def _run(self, current_version):
        release = fetch_latest_release()
        if release is None:
            # fetch_latest_release() already logged the specific failure.
            self.finished.emit(None)
        elif is_update_available(current_version, release["version"]):
            logger.info(
                "Update check: running %s, %s is available",
                current_version, release["version"],
            )
            self.finished.emit(release)
        else:
            logger.info(
                "Update check: running %s, up to date (latest is %s)",
                current_version, release["version"],
            )
            self.finished.emit(None)
