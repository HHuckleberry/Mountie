"""Read-only ISO image mounting through the host's UDisks command-line client."""

import json
import hashlib
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path


COMMAND_TIMEOUT = 120


def _sandboxed():
    return Path("/.flatpak-info").exists()


def _command(*args):
    if _sandboxed():
        return ["flatpak-spawn", "--host", *args]
    return list(args)


def _state_path(source_id):
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    safe_id = hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()
    return runtime / "mountie" / f"iso-{safe_id}.json"


def _source_key(value):
    """Return a stable identity without resolving a Flatpak portal path."""
    path = Path(value).expanduser().absolute()
    return str(path if _sandboxed() else path.resolve())


def _mount_source_path(value, run_fn=subprocess.run):
    """Translate a document-portal grant to its real host backing file.

    UDisks cannot retain a loop device backed by the FUSE document proxy: the
    kernel reports it with a ``(deleted)`` suffix. The host portal CLI provides
    the authorized file's origin path without broad filesystem access.
    """
    source = _source_key(value)
    if not _sandboxed() or "/doc/" not in source:
        return source
    try:
        result = run_fn(
            _command("flatpak", "document-info", source),
            capture_output=True, text=True, timeout=COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return source
    if result.returncode == 0:
        match = re.search(r"^origin:\s*(.+)$", result.stdout or "", re.MULTILINE)
        if match:
            return match.group(1).strip()
    return source


def _read_state(config):
    try:
        state = json.loads(_state_path(config["id"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if state.get("source") != _source_key(config["path"]):
        return None
    return state


def _write_state(config, device, mountpoint):
    path = _state_path(config["id"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "source": _source_key(config["path"]),
        "device": device,
        "mountpoint": mountpoint,
    }), encoding="utf-8")


def local_path(config):
    state = _read_state(config)
    if not state or not os.path.ismount(state.get("mountpoint", "")):
        return None
    return state.get("mountpoint")


def is_mounted(config):
    state = _read_state(config)
    return bool(state and os.path.ismount(state.get("mountpoint", "")))


def _run(args, run_fn=subprocess.run):
    return run_fn(
        _command(*args), capture_output=True, text=True, timeout=COMMAND_TIMEOUT
    )


def _failure(result, action):
    detail = (result.stderr or result.stdout or "").strip() or "no output"
    return False, "image mount failed", f"Could not {action} ISO image: {detail}"


def mount_image(config, on_done, *, run_fn=subprocess.run):
    def worker():
        source = Path(config.get("path", "")).expanduser()
        if not source.is_file():
            on_done(False, "image not found", f"ISO image not found: {source}")
            return
        if shutil.which("flatpak-spawn" if _sandboxed() else "udisksctl") is None:
            on_done(False, "backend unavailable", "ISO mounting requires UDisks2 (udisksctl).")
            return
        try:
            setup = _run([
                "udisksctl", "loop-setup", "--read-only", "--file",
                _mount_source_path(config["path"], run_fn),
            ], run_fn)
            if setup.returncode:
                on_done(*_failure(setup, "attach"))
                return
            match = re.search(r"(/dev/loop\d+)", setup.stdout or "")
            if not match:
                on_done(False, "image mount failed", "Could not identify the ISO loop device.")
                return
            device = match.group(1)
            mounted = _run(["udisksctl", "mount", "--block-device", device, "--options", "ro"], run_fn)
            if mounted.returncode:
                _run(["udisksctl", "loop-delete", "--block-device", device], run_fn)
                on_done(*_failure(mounted, "mount"))
                return
            point = re.search(r" at (.+?)\.?\s*$", mounted.stdout or "")
            if not point:
                _run(["udisksctl", "unmount", "--block-device", device], run_fn)
                _run(["udisksctl", "loop-delete", "--block-device", device], run_fn)
                on_done(False, "image mount failed", "Could not identify the ISO mount location.")
                return
            _write_state(config, device, point.group(1).rstrip("."))
            on_done(True, None, None)
        except (OSError, subprocess.TimeoutExpired) as error:
            on_done(False, "image mount failed", f"Could not mount ISO image: {error}")

    threading.Thread(target=worker, daemon=True).start()


def unmount_image(config, on_done, *, run_fn=subprocess.run):
    def worker():
        state = _read_state(config)
        if not state:
            on_done(False, "unmount failed", "Could not find this ISO image's loop device.")
            return
        try:
            result = _run(["udisksctl", "unmount", "--block-device", state["device"]], run_fn)
            if result.returncode:
                on_done(*_failure(result, "unmount"))
                return
            deleted = _run(["udisksctl", "loop-delete", "--block-device", state["device"]], run_fn)
            if deleted.returncode:
                on_done(*_failure(deleted, "detach"))
                return
            try:
                _state_path(config["id"]).unlink()
            except FileNotFoundError:
                pass
            on_done(True, None, None)
        except (OSError, subprocess.TimeoutExpired, KeyError) as error:
            on_done(False, "unmount failed", f"Could not unmount ISO image: {error}")

    threading.Thread(target=worker, daemon=True).start()
