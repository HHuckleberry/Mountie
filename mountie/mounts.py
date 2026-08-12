import os
import re
from pathlib import Path
from urllib.parse import quote

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from mountie.settings import DEFAULT_LINK_DIR, DEFAULT_PROTOCOL, PROTOCOLS


def validate_share(config):
    """Return a user-facing validation error, or None when a share is valid."""
    if not (config.get("label") or "").strip():
        return "A label is required."
    host = (config.get("host") or "").strip()
    if not host:
        return "A host or IP address is required."
    if any(character.isspace() for character in host) or any(
        character in host for character in "/?#@"
    ):
        return "The host contains characters that are not valid in a network address."
    if not (config.get("share") or "").strip("/").strip():
        return "A share name or path is required."
    if config.get("protocol", DEFAULT_PROTOCOL) not in dict(PROTOCOLS):
        return "The selected protocol is not supported."
    try:
        _uri_host(host)
    except (UnicodeError, ValueError) as error:
        return str(error)
    return None


def _uri_host(host):
    host = host.strip()
    if host.startswith("["):
        match = re.fullmatch(r"\[([^]]+)](?::([0-9]+))?", host)
        if not match:
            raise ValueError("The host contains an invalid bracketed IPv6 address.")
        if match.group(2) and not 1 <= int(match.group(2)) <= 65535:
            raise ValueError("The host has an invalid port number.")
        return host.replace("%", "%25")
    if host.count(":") > 1:
        return f"[{host.replace('%', '%25')}]"
    if ":" in host:
        hostname, port = host.rsplit(":", 1)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("The host has an invalid port number.")
        return f"{hostname.encode('idna').decode('ascii')}:{port}"
    return host.encode("idna").decode("ascii")


def share_uri(config):
    validation_error = validate_share(config)
    if validation_error:
        raise ValueError(validation_error)
    protocol = config.get("protocol", DEFAULT_PROTOCOL)
    host = _uri_host(config["host"])
    path = quote(config["share"].strip("/"), safe="/")
    return f"{protocol}://{host}/{path}/"


def link_dir(config):
    return Path(config.get("link_dir", DEFAULT_LINK_DIR)).expanduser()


def link_name(share):
    """Return a filesystem-safe directory name derived from a share label."""
    raw = (share.get("label") or share.get("share") or share["id"]).strip()
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.") or share["id"]


def link_path(config, share):
    return link_dir(config) / link_name(share)


def link_name_collision(config, candidate, exclude_id=None):
    candidate_name = link_name(candidate)
    return any(
        share.get("id") != exclude_id and link_name(share) == candidate_name
        for share in config.get("shares", [])
    )


def local_path(share):
    """Return the path where GVfs mounted a share, or None."""
    try:
        mount = Gio.File.new_for_uri(share_uri(share)).find_enclosing_mount(None)
    except (GLib.Error, ValueError):
        return None
    return mount.get_default_location().get_path()


def update_link(config, share):
    """Point a share's stable link at its current mount."""
    if not config.get("links_enabled", True):
        return None
    target = local_path(share)
    if target is None:
        remove_link(config, share)
        return None

    path = link_path(config, share)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if os.readlink(path) == target:
                return path
            path.unlink()
        elif path.exists():
            return None
        path.symlink_to(target)
        return path
    except OSError:
        return None


def remove_link(config, share):
    path = link_path(config, share)
    try:
        if path.is_symlink():
            path.unlink()
    except OSError:
        pass


def prune_links(config):
    directory = link_dir(config)
    if not directory.is_dir():
        return
    ours = {link_name(share) for share in config["shares"]}
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name in ours and entry.is_symlink() and not entry.exists():
            try:
                entry.unlink()
            except OSError:
                pass


class CredMountOperation(Gio.MountOperation):
    def __init__(self, username, password):
        super().__init__()
        self._username = username or ""
        self._password = password or ""
        self.connect("ask-password", self._on_ask_password)

    def _on_ask_password(self, operation, message, default_user, default_domain, flags):
        if flags & Gio.AskPasswordFlags.NEED_USERNAME:
            operation.set_username(self._username)
        if flags & Gio.AskPasswordFlags.NEED_PASSWORD:
            operation.set_password(self._password)
        operation.set_password_save(Gio.PasswordSave.NEVER)
        operation.reply(Gio.MountOperationResult.HANDLED)


def is_mounted(config):
    try:
        gfile = Gio.File.new_for_uri(share_uri(config))
        gfile.find_enclosing_mount(None)
        return True
    except (GLib.Error, ValueError):
        return False


def classify_mount_error(error):
    """Return a short UI status and a helpful dialog title for a GVfs error."""
    io_error = Gio.io_error_quark()
    categories = (
        (Gio.IOErrorEnum.PERMISSION_DENIED, "authentication failed", "Authentication failed"),
        (Gio.IOErrorEnum.NOT_FOUND, "share not found", "Share not found"),
        (Gio.IOErrorEnum.HOST_NOT_FOUND, "host not found", "Host not found"),
        (Gio.IOErrorEnum.CONNECTION_REFUSED, "connection refused", "Connection refused"),
        (Gio.IOErrorEnum.NETWORK_UNREACHABLE, "network unreachable", "Network unreachable"),
        (Gio.IOErrorEnum.HOST_UNREACHABLE, "host unreachable", "Host unreachable"),
        (Gio.IOErrorEnum.TIMED_OUT, "connection timed out", "Connection timed out"),
    )
    for code, status, title in categories:
        if error.matches(io_error, code):
            return status, title

    message = (error.message or "").lower()
    if any(term in message for term in (
        "authentication failed", "permission denied", "access denied",
        "invalid password", "logon failure",
    )):
        return "authentication failed", "Authentication failed"
    if any(term in message for term in (
        "share not found", "no such file", "does not exist",
    )):
        return "share not found", "Share not found"
    return "error", "Could not connect"


def mount_share(config, password, on_done):
    """Mount a share and report (success, status, error_message)."""
    try:
        gfile = Gio.File.new_for_uri(share_uri(config))
    except ValueError as error:
        on_done(False, "invalid share", f"Invalid share: {error}")
        return
    operation = CredMountOperation(config.get("username", ""), password)

    def callback(source, result):
        try:
            source.mount_enclosing_volume_finish(result)
            on_done(True, None, None)
        except GLib.Error as error:
            status, title = classify_mount_error(error)
            on_done(False, status, f"{title}: {error.message}")

    gfile.mount_enclosing_volume(
        Gio.MountMountFlags.NONE, operation, None, callback
    )


def unmount_share(config, on_done):
    try:
        gfile = Gio.File.new_for_uri(share_uri(config))
        mount = gfile.find_enclosing_mount(None)
    except (GLib.Error, ValueError) as error:
        message = error.message if isinstance(error, GLib.Error) else str(error)
        on_done(False, "unmount failed", f"Could not disconnect: {message}")
        return

    def callback(source, result):
        try:
            source.unmount_with_operation_finish(result)
            on_done(True, None, None)
        except GLib.Error as error:
            on_done(False, "unmount failed", f"Could not disconnect: {error.message}")

    mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, callback)
