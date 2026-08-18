"""Native kernel-CIFS mount backend: pkexec + a small root-owned wrapper.

No GVfs/Gio/PyQt import here on purpose. mount_share()/unmount_share() call
on_done directly from a background thread; every caller (mounts.py, reached
from mountie/app/window.py's on_toggle) already wraps on_done in a Qt signal
emit (see Bridge in mountie/app/components/shares.py), so the queued delivery
to the main thread is handled the same way mountie/update_check.py's
UpdateChecker already does it for its own background thread. That keeps this
module plain-unittest-testable with no QApplication required.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from mountie.settings import APP_ID

logger = logging.getLogger(__name__)

FLATPAK_MARKER = Path("/.flatpak-info")
WRAPPER_PATH = "/usr/libexec/mountie-mount-helper"  # must match the .policy
                                                     # file's exec.path annotation
WRAPPER_TIMEOUT_SECONDS = 30

# Where io.github.HHuckleberry.Mountie.yml's mountie module installs the
# self-contained installer inside the Flatpak (see the build-commands there).
BUNDLED_INSTALLER_PATH = Path("/app/share/mountie/install-native-mount-helper.sh")
HELPER_CHECK_TIMEOUT_SECONDS = 5


def is_sandboxed(marker=FLATPAK_MARKER):
    """True when running inside the Flatpak sandbox."""
    return marker.exists()


def base_runtime_dir(uid=None):
    return Path(f"/run/user/{uid if uid is not None else os.getuid()}/mountie")


def mountpoint_for(share_id, uid=None):
    return base_runtime_dir(uid) / share_id


def is_helper_installed(*, run_fn=subprocess.run, sandboxed_fn=is_sandboxed, access_fn=os.access):
    """Best-effort check for whether the privileged wrapper is installed on
    the host. Paths like /usr/... resolve inside the Flatpak runtime, not
    the real host, from inside the sandbox - so a sandboxed check has to ask
    the host via flatpak-spawn rather than stat the path directly. This only
    tests existence/executability, never runs the wrapper itself, so it
    needs no pkexec prompt."""
    if sandboxed_fn():
        try:
            result = run_fn(
                ["flatpak-spawn", "--host", "test", "-x", WRAPPER_PATH],
                capture_output=True, timeout=HELPER_CHECK_TIMEOUT_SECONDS,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0
    return access_fn(WRAPPER_PATH, os.X_OK)


def _xdg_data_dir():
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


def host_data_dir(*, sandboxed_fn=is_sandboxed, home=None):
    """The path a terminal ON THE HOST would use to reach the same directory
    this process sees as its own XDG data dir. Identical to _xdg_data_dir()
    for a native install; under Flatpak it's ~/.var/app/<app-id>/data -
    the same directory, bind-mounted, seen from two different vantage
    points, since a host terminal can't resolve the sandbox-internal path."""
    home = home or Path.home()
    if sandboxed_fn():
        return home / ".var" / "app" / APP_ID / "data"
    return _xdg_data_dir()


def bundled_installer_source(*, bundled_path=BUNDLED_INSTALLER_PATH):
    """Where the self-contained installer script can be read from inside
    the running process, for either distribution."""
    if bundled_path.exists():
        return bundled_path
    # Native install: alongside this checkout's own scripts/ directory.
    return Path(__file__).resolve().parent.parent / "scripts" / "install-native-mount-helper.sh"


def export_installer_for_host(*, sandboxed_fn=is_sandboxed, source=None, xdg_data_dir=None, home=None):
    """Copy the installer somewhere the user can actually run it with sudo
    from a host terminal. Returns (write_path, host_display_path): the same
    file, seen from inside this process and from the host. For a native
    install those are literally the same path; under Flatpak, host_path
    uses the ~/.var/app/<id>/data convention regardless of what
    sandbox-internal path this process wrote through (see host_data_dir)."""
    source = source or bundled_installer_source()
    data_dir = xdg_data_dir or _xdg_data_dir()
    destination = data_dir / "mountie" / "install-native-mount-helper.sh"
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    os.chmod(destination, 0o755)
    if sandboxed_fn():
        host_path = (
            host_data_dir(sandboxed_fn=sandboxed_fn, home=home)
            / "mountie" / "install-native-mount-helper.sh"
        )
    else:
        host_path = destination
    return destination, host_path


def _is_cifs_mount(path, mountinfo_path="/proc/self/mountinfo"):
    """Confirm a live mount at `path` is actually a cifs mount, not something
    unrelated that happens to occupy the same deterministic path."""
    target = str(path)
    try:
        with open(mountinfo_path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        # /proc/self/mountinfo should always exist on Linux; if it doesn't,
        # fall back to trusting os.path.ismount rather than failing closed.
        return True
    for line in lines:
        fields = line.split(" - ", 1)
        if len(fields) != 2:
            continue
        mount_point = fields[0].split()[4]
        if mount_point != target:
            continue
        fstype = fields[1].split()[0]
        return fstype == "cifs"
    return False


def is_mounted(config, *, ismount_fn=os.path.ismount, cifs_check_fn=_is_cifs_mount):
    path = mountpoint_for(config["id"])
    return ismount_fn(str(path)) and cifs_check_fn(path)


def local_path(share, *, ismount_fn=os.path.ismount, cifs_check_fn=_is_cifs_mount):
    """Return the path Mountie mounted a native share at, or None."""
    path = mountpoint_for(share["id"])
    if ismount_fn(str(path)) and cifs_check_fn(path):
        return str(path)
    return None


def _wrapper_argv(*args, sandboxed):
    if sandboxed:
        return ["flatpak-spawn", "--host", "pkexec", WRAPPER_PATH, *args]
    return ["pkexec", WRAPPER_PATH, *args]


def _write_credentials_file(directory, username, password, domain):
    for field, value in (
        ("username", username), ("password", password), ("domain", domain)
    ):
        if any(character in value for character in ("\r", "\n", "\0")):
            raise ValueError(
                f"Native mount {field} cannot contain line breaks or null characters."
            )
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="creds-", dir=str(directory))
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(f"username={username}\n")
            if domain:
                handle.write(f"domain={domain}\n")
            handle.write(f"password={password}\n")
    except Exception:
        os.unlink(path)
        raise
    return path


def classify_native_setup_error(detail, sandboxed):
    """Return (status, title) for a missing pkexec/flatpak-spawn launcher."""
    if sandboxed:
        return (
            "native mount unavailable",
            "flatpak-spawn is unavailable. Confirm the Flatpak has "
            "--talk-name=org.freedesktop.Flatpak and that flatpak-spawn "
            "is installed on the host.",
        )
    return (
        "native mount unavailable",
        "pkexec is not installed. Native mounts require polkit; install "
        "it, then run scripts/install-native-mount-helper.sh with sudo.",
    )


def classify_native_mount_error(result):
    """Return (status, title) for a failed wrapper invocation, mirroring
    mounts.classify_mount_error's shape."""
    text = f"{(result.stderr or '').lower()} {(result.stdout or '').lower()}"
    if "refusing to mount an invalid source" in text:
        return (
            "invalid share",
            "Invalid share. Native mount only accepts a plain hostname or "
            "IPv4 address (no port, no IPv6 literal) and a share path.",
        )
    if any(term in text for term in (
        "not authorized", "authentication is required", "dismissed",
        "authorization could not be obtained",
    )):
        return "authentication cancelled", "Authentication cancelled"
    if any(term in text for term in ("unknown action", "no polkit", "not configured")):
        return (
            "native mount not set up",
            "Native mount is not set up. Run scripts/install-native-"
            "mount-helper.sh with sudo on this machine, then try again.",
        )
    if any(term in text for term in ("permission denied", "mount error(13)", "logon failure")):
        return "authentication failed", "Authentication failed"
    if any(term in text for term in ("no such file or directory", "mount error(2)")):
        return "share not found", "Share not found"
    if any(term in text for term in ("host is down", "no route to host", "mount error(112)")):
        return "host unreachable", "Host unreachable"
    if any(term in text for term in ("network is unreachable", "mount error(101)")):
        return "network unreachable", "Network unreachable"
    if "connection timed out" in text or "mount error(110)" in text:
        return "connection timed out", "Connection timed out"
    if any(term in text for term in ("connection refused", "mount error(111)")):
        return "connection refused", "Connection refused"
    if "mount.cifs" in text and ("not found" in text or "no such file" in text):
        return (
            "native mount unavailable",
            "mount.cifs is not installed. Install cifs-utils on the host.",
        )
    return "error", "Could not connect"


def _run_wrapper(args, *, run_fn, sandboxed_fn, which_fn):
    sandboxed = sandboxed_fn()
    launcher = "flatpak-spawn" if sandboxed else "pkexec"
    if which_fn(launcher) is None:
        status, title = classify_native_setup_error(launcher, sandboxed)
        return False, status, title
    argv = _wrapper_argv(*args, sandboxed=sandboxed)
    try:
        result = run_fn(argv, capture_output=True, text=True, timeout=WRAPPER_TIMEOUT_SECONDS)
    except FileNotFoundError as error:
        status, title = classify_native_setup_error(str(error), sandboxed)
        return False, status, title
    except subprocess.TimeoutExpired:
        return (
            False,
            "error",
            "Native mount timed out waiting for the privileged helper. "
            "Check for a stuck authentication prompt.",
        )
    if result.returncode == 0:
        return True, None, None
    status, title = classify_native_mount_error(result)
    detail = (result.stderr or result.stdout or "").strip() or "no output"
    return False, status, f"{title}: {detail}"


def mount_share(config, password, on_done, *, run_fn=subprocess.run,
                 sandboxed_fn=is_sandboxed, which_fn=shutil.which):
    """Mount a share via the kernel cifs driver and report
    (success, status, error_message), matching mounts.mount_share's contract.
    """
    share_id = config["id"]
    source = f"//{config['host']}/{config['share'].strip('/')}"
    mountpoint = mountpoint_for(share_id)
    runtime_dir = base_runtime_dir()

    def worker():
        creds_path = None
        try:
            mountpoint.mkdir(mode=0o700, parents=True, exist_ok=True)
            creds_path = _write_credentials_file(
                runtime_dir, config.get("username", ""), password, config.get("domain", "")
            )
            ok, status, message = _run_wrapper(
                ["mount", source, str(mountpoint), creds_path],
                run_fn=run_fn, sandboxed_fn=sandboxed_fn, which_fn=which_fn,
            )
        except (OSError, ValueError) as error:
            ok, status, message = False, "error", f"Could not prepare native mount: {error}"
        finally:
            if creds_path:
                try:
                    os.unlink(creds_path)
                except OSError:
                    pass
        on_done(ok, status, message)

    threading.Thread(target=worker, daemon=True).start()


def unmount_share(config, on_done, *, run_fn=subprocess.run,
                   sandboxed_fn=is_sandboxed, which_fn=shutil.which):
    """Unmount a natively-mounted share, matching mounts.unmount_share's
    contract."""
    mountpoint = mountpoint_for(config["id"])

    def worker():
        ok, status, message = _run_wrapper(
            ["unmount", str(mountpoint)],
            run_fn=run_fn, sandboxed_fn=sandboxed_fn, which_fn=which_fn,
        )
        on_done(ok, status, message)

    threading.Thread(target=worker, daemon=True).start()
