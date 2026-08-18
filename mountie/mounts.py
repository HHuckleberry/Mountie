import os
import re
import logging
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from mountie import iso_mount, native_mount
from mountie.settings import (
    BACKEND_NATIVE,
    DEFAULT_BACKEND,
    DEFAULT_LINK_DIR,
    DEFAULT_PROTOCOL,
    PROTOCOLS,
    SOURCE_ISO,
    SOURCE_NETWORK,
)

logger = logging.getLogger(__name__)

NETWORK_SCHEMES = frozenset(key for key, _label in PROTOCOLS)


def _safe_mount_uri(uri):
    """Return a display-safe URI with userinfo, query, and fragment removed."""
    try:
        parts = urlsplit(uri)
        hostname = parts.hostname
        if not hostname:
            return None
        host = f"[{hostname}]" if ":" in hostname else hostname
        if parts.port is not None:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme.lower(), host, parts.path, "", ""))
    except (TypeError, ValueError):
        return None


def _mount_uri_key(uri):
    safe_uri = _safe_mount_uri(uri)
    if safe_uri is None:
        return None
    parts = urlsplit(safe_uri)
    return (
        parts.scheme.lower(),
        parts.netloc.lower(),
        unquote(parts.path).rstrip("/"),
    )


def external_network_mounts(configured_shares, mounts=None):
    """Describe mounted network locations not already configured in Mountie."""
    configured = set()
    for share in configured_shares:
        try:
            configured.add(_mount_uri_key(share_uri(share)))
        except ValueError:
            continue

    if mounts is None:
        mounts = Gio.VolumeMonitor.get().get_mounts()
    found = []
    seen = set()
    for mount in mounts:
        try:
            uri = mount.get_default_location().get_uri()
            key = _mount_uri_key(uri)
        except (AttributeError, GLib.Error):
            continue
        if key is None or key[0] not in NETWORK_SCHEMES:
            continue
        if key in configured or key in seen:
            continue
        safe_uri = _safe_mount_uri(uri)
        if safe_uri is None:
            continue
        seen.add(key)
        try:
            name = mount.get_name()
        except (AttributeError, GLib.Error):
            name = ""
        display_name = str(name or safe_uri)
        parts = urlsplit(safe_uri)
        share_path = unquote(parts.path).strip("/")
        connection = {"name": display_name, "uri": safe_uri}
        if share_path:
            # Passwords are deliberately absent. A username embedded in an
            # SFTP/FTP URI is safe to prefill separately, never in display.
            original_parts = urlsplit(uri)
            connection["config"] = {
                "protocol": parts.scheme.lower(),
                "label": display_name,
                "host": parts.netloc,
                "share": share_path,
                "domain": "",
                "username": unquote(original_parts.username or ""),
            }
        found.append(connection)
    return sorted(found, key=lambda item: (item["name"].casefold(), item["uri"]))


def validate_share(config):
    """Return a user-facing validation error, or None when a share is valid."""
    if not (config.get("label") or "").strip():
        return "A label is required."
    if config.get("kind") == SOURCE_ISO:
        path = Path(config.get("path", "")).expanduser()
        if not path.is_file():
            return "Choose an ISO image that exists."
        if path.suffix.casefold() != ".iso":
            return "The selected file must have an .iso extension."
        return None
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
    if (
        config.get("backend", DEFAULT_BACKEND) == BACKEND_NATIVE
        and config.get("protocol", DEFAULT_PROTOCOL) != "smb"
    ):
        return "Native mount only supports the SMB/CIFS protocol."
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
    # ISO (and any future non-network) entries pass validate_share() on
    # their own terms - a network scheme can't be built from one, so this
    # has to be rejected here rather than relying on the host/share access
    # below to fail loudly. Every caller already treats ValueError as
    # "skip this entry" (see external_network_mounts, discovery.py).
    if config.get("kind", SOURCE_NETWORK) != SOURCE_NETWORK:
        raise ValueError("This entry is not a network share.")
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
    raw = (share.get("label") or share.get("share") or "").strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    if name:
        return name
    # The ID comes from persisted configuration and may have been edited by
    # hand. Sanitize it too; returning it raw here permits labels such as
    # ".." plus an ID like "../../target" to escape the link directory.
    fallback = re.sub(
        r"[^A-Za-z0-9._-]+", "-", str(share.get("id", ""))
    ).strip("-.")
    return fallback or "share"


def link_path(config, share):
    return link_dir(config) / link_name(share)


def link_name_collision(config, candidate, exclude_id=None):
    candidate_name = link_name(candidate)
    return any(
        share.get("id") != exclude_id and link_name(share) == candidate_name
        for share in config.get("shares", [])
    )


def local_path(share):
    """Return the path where a share is mounted, or None."""
    if share.get("kind") == SOURCE_ISO:
        return iso_mount.local_path(share)
    if share.get("backend", DEFAULT_BACKEND) == BACKEND_NATIVE:
        return native_mount.local_path(share)
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
    def __init__(self, username, password, domain=""):
        super().__init__()
        self._username = username or ""
        self._password = password or ""
        self._domain = domain or ""
        self._credential_prompts = 0
        self.credentials_rejected = False
        self.connect("ask-password", self._on_ask_password)

    def _on_ask_password(self, operation, message, default_user, default_domain, flags):
        self._credential_prompts += 1
        logger.info(
            "GVfs requested credentials (attempt %d): username=%s domain=%s "
            "password=%s anonymous=%s",
            self._credential_prompts,
            bool(flags & Gio.AskPasswordFlags.NEED_USERNAME),
            bool(flags & Gio.AskPasswordFlags.NEED_DOMAIN),
            bool(flags & Gio.AskPasswordFlags.NEED_PASSWORD),
            bool(flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED),
        )
        # A second prompt means the server rejected the credentials just
        # submitted. Re-sending them makes GVfs ask again indefinitely.
        if self._credential_prompts > 1:
            self.credentials_rejected = True
            logger.warning("GVfs rejected the supplied credentials")
            operation.reply(Gio.MountOperationResult.ABORTED)
            return
        if flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED:
            operation.set_anonymous(False)
        if flags & Gio.AskPasswordFlags.NEED_USERNAME:
            operation.set_username(self._username)
        if flags & Gio.AskPasswordFlags.NEED_DOMAIN:
            # Blank is intentional: shares outside a domain/workgroup should
            # continue to use the server/backend's suggested default.
            operation.set_domain(self._domain or default_domain or "")
        if flags & Gio.AskPasswordFlags.NEED_PASSWORD:
            operation.set_password(self._password)
        operation.set_password_save(Gio.PasswordSave.NEVER)
        operation.reply(Gio.MountOperationResult.HANDLED)


def is_mounted(config):
    if config.get("kind") == SOURCE_ISO:
        return iso_mount.is_mounted(config)
    if config.get("backend", DEFAULT_BACKEND) == BACKEND_NATIVE:
        return native_mount.is_mounted(config)
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
        (Gio.IOErrorEnum.NOT_SUPPORTED, "backend unavailable", "GVfs backend unavailable"),
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
    if config.get("kind") == SOURCE_ISO:
        iso_mount.mount_image(config, on_done)
        return
    if config.get("backend", DEFAULT_BACKEND) == BACKEND_NATIVE:
        validation_error = validate_share(config)
        if validation_error:
            on_done(False, "invalid share", f"Invalid share: {validation_error}")
            return
        native_mount.mount_share(config, password, on_done)
        return
    try:
        gfile = Gio.File.new_for_uri(share_uri(config))
    except ValueError as error:
        on_done(False, "invalid share", f"Invalid share: {error}")
        return
    logger.info("Mounting %s", gfile.get_uri())
    operation = CredMountOperation(
        config.get("username", ""), password, config.get("domain", "")
    )

    def callback(source, result):
        try:
            source.mount_enclosing_volume_finish(result)
            on_done(True, None, None)
        except GLib.Error as error:
            logger.warning("Mount failed for %s: %s", gfile.get_uri(), error.message)
            if operation.credentials_rejected:
                on_done(
                    False,
                    "authentication failed",
                    "Authentication failed: The server rejected the saved credentials. "
                    "Edit the share and enter the correct username and password.",
                )
            else:
                status, title = classify_mount_error(error)
                on_done(False, status, f"{title}: {error.message}")

    gfile.mount_enclosing_volume(
        Gio.MountMountFlags.NONE, operation, None, callback
    )


def unmount_share(config, on_done):
    if config.get("kind") == SOURCE_ISO:
        iso_mount.unmount_image(config, on_done)
        return
    if config.get("backend", DEFAULT_BACKEND) == BACKEND_NATIVE:
        native_mount.unmount_share(config, on_done)
        return
    try:
        gfile = Gio.File.new_for_uri(share_uri(config))
        mount = gfile.find_enclosing_mount(None)
    except (GLib.Error, ValueError) as error:
        message = error.message if isinstance(error, GLib.Error) else str(error)
        on_done(False, "unmount failed", f"Could not disconnect: {message}")
        return
    logger.info("Unmounting %s", gfile.get_uri())

    def callback(source, result):
        try:
            source.unmount_with_operation_finish(result)
            on_done(True, None, None)
        except GLib.Error as error:
            logger.warning("Unmount failed for %s: %s", gfile.get_uri(), error.message)
            on_done(False, "unmount failed", f"Could not disconnect: {error.message}")

    mount.unmount_with_operation(Gio.MountUnmountFlags.NONE, None, None, callback)
