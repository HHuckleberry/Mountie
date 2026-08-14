"""Passive discovery of network services exposed by the desktop's GVfs backend."""

import re
from dataclasses import dataclass, replace
from typing import Optional, Tuple
from urllib.parse import unquote, urlsplit, urlunsplit

from gi.repository import Gio, GLib

from mountie.mounts import (
    CredMountOperation,
    _mount_uri_key,
    classify_mount_error,
    share_uri,
)
from mountie.settings import PROTOCOLS


DISCOVERY_ROOT_URI = "network:///"
DISCOVERY_SCHEMES = frozenset({"network", "dns-sd"})
SUPPORTED_SCHEMES = frozenset(key for key, _label in PROTOCOLS)
ATTRIBUTES = ",".join((
    Gio.FILE_ATTRIBUTE_STANDARD_NAME,
    Gio.FILE_ATTRIBUTE_STANDARD_DISPLAY_NAME,
    Gio.FILE_ATTRIBUTE_STANDARD_TYPE,
    Gio.FILE_ATTRIBUTE_STANDARD_ICON,
    Gio.FILE_ATTRIBUTE_STANDARD_SYMBOLIC_ICON,
    Gio.FILE_ATTRIBUTE_STANDARD_TARGET_URI,
))

FALLBACK_ICONS = {
    "smb": ("network-server-symbolic", "network-server"),
    "sftp": ("folder-remote-symbolic", "folder-remote"),
    "ftp": ("folder-remote-symbolic", "folder-remote"),
    "ftps": ("folder-remote-symbolic", "folder-remote"),
    "nfs": ("folder-remote-symbolic", "folder-remote"),
    "afp": ("folder-remote-symbolic", "folder-remote"),
    "dav": ("folder-remote-symbolic", "folder-remote"),
    "davs": ("folder-remote-symbolic", "folder-remote"),
    "container": ("network-workgroup-symbolic", "network-workgroup"),
    "server": ("network-server-symbolic", "network-server"),
    "share": ("folder-remote-symbolic", "folder-remote"),
}


@dataclass(frozen=True)
class DiscoveryResult:
    key: str
    name: str
    uri: str
    protocol: Optional[str]
    host: str
    path: str
    kind: str
    icon_names: Tuple[str, ...]
    initial: Optional[dict]
    configured: bool = False

    @property
    def importable(self):
        return self.kind == "share" and self.initial is not None and not self.configured

    @property
    def can_prefill(self):
        return self.initial is not None and not self.configured


def _safe_discovery_uri(uri):
    """Return a display-safe supported URI, stripping all user information."""
    try:
        parts = urlsplit(uri or "")
        scheme = parts.scheme.lower()
        if scheme not in SUPPORTED_SCHEMES | DISCOVERY_SCHEMES:
            return None
        hostname = parts.hostname
        if scheme not in DISCOVERY_SCHEMES and not hostname:
            return None
        if hostname:
            host = f"[{hostname}]" if ":" in hostname else hostname
            if parts.port is not None:
                host = f"{host}:{parts.port}"
        else:
            host = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit((scheme, host, parts.path or "/", "", ""))
    except (TypeError, ValueError):
        return None


def _icon_names(icon):
    """Extract only local theme names; never load advertised paths or URLs."""
    if icon is None or not hasattr(icon, "get_names"):
        return ()
    try:
        names = icon.get_names()
    except (AttributeError, GLib.Error):
        return ()
    return tuple(
        name for name in names
        if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.+-]+", name)
    )


def result_from_info(info, child_uri):
    """Normalize one enumerated GVfs entry into a safe discovery result."""
    try:
        target_uri = info.get_attribute_string(
            Gio.FILE_ATTRIBUTE_STANDARD_TARGET_URI
        ) or child_uri
        name = info.get_display_name() or "Network service"
        symbolic = info.get_symbolic_icon()
        regular = info.get_icon()
    except (AttributeError, GLib.Error):
        return None

    uri = _safe_discovery_uri(target_uri)
    if uri is None:
        return None
    parts = urlsplit(uri)
    scheme = parts.scheme.lower()
    host = parts.netloc
    path = unquote(parts.path).strip("/")
    protocol = scheme if scheme in SUPPORTED_SCHEMES else None
    if scheme in DISCOVERY_SCHEMES:
        kind = "container"
    elif path:
        kind = "share"
    else:
        kind = "server"

    icons = _icon_names(symbolic) or _icon_names(regular)
    if not icons:
        icons = FALLBACK_ICONS.get(protocol or kind, FALLBACK_ICONS[kind])

    initial = None
    if protocol:
        initial = {
            "protocol": protocol,
            "label": str(name),
            "host": host,
            "share": path,
            "domain": "",
            "username": "",
        }
    key = _mount_uri_key(uri) or (scheme, host.casefold(), path.rstrip("/"))
    return DiscoveryResult(
        key="|".join(str(part) for part in key),
        name=str(name),
        uri=uri,
        protocol=protocol,
        host=host,
        path=path,
        kind=kind,
        icon_names=icons,
        initial=initial,
    )


def discover_network_async(
        configured_shares, root_uri=DISCOVERY_ROOT_URI, cancellable=None,
        on_done=None):
    """Asynchronously enumerate one GVfs discovery level.

    Gio's blocking enumerator is not reliably interruptible for a stalled
    remote backend. Using its native async API lets the GLib main context
    deliver cancellation without leaking a permanently blocked Python thread.
    ``on_done`` receives ``(results, error_text)`` exactly once.
    """
    cancellable = cancellable or Gio.Cancellable()
    on_done = on_done or (lambda _results, _error: None)
    root = Gio.File.new_for_uri(root_uri)
    configured = set()
    for share in configured_shares:
        try:
            key = _mount_uri_key(share_uri(share))
        except ValueError:
            continue
        if key:
            configured.add("|".join(str(part) for part in key))

    state = {"enumerator": None, "results": [], "seen": set(), "finished": False}

    def finish(error=""):
        if state["finished"]:
            return
        state["finished"] = True
        enumerator = state["enumerator"]
        if enumerator is not None:
            try:
                enumerator.close_async(GLib.PRIORITY_DEFAULT, None, None, None)
            except GLib.Error:
                pass
        results = sorted(
            state["results"], key=lambda result: (result.name.casefold(), result.uri)
        )
        on_done(results, error)

    def cancelled(error):
        return error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)

    def next_finished(enumerator, async_result, _user_data=None):
        try:
            infos = enumerator.next_files_finish(async_result)
        except GLib.Error as error:
            finish("" if cancelled(error) else error.message)
            return
        if not infos:
            finish()
            return
        for info in infos:
            child = enumerator.get_child(info)
            result = result_from_info(info, child.get_uri())
            if result is None or result.key in state["seen"]:
                continue
            state["seen"].add(result.key)
            if result.key in configured:
                result = replace(result, configured=True)
            state["results"].append(result)
        enumerator.next_files_async(
            50, GLib.PRIORITY_DEFAULT, cancellable, next_finished, None
        )

    def enumerated(source, async_result, _user_data=None):
        try:
            enumerator = source.enumerate_children_finish(async_result)
        except GLib.Error as error:
            finish("" if cancelled(error) else error.message)
            return
        state["enumerator"] = enumerator
        enumerator.next_files_async(
            50, GLib.PRIORITY_DEFAULT, cancellable, next_finished, None
        )

    root.enumerate_children_async(
        ATTRIBUTES,
        Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
        GLib.PRIORITY_DEFAULT,
        cancellable,
        enumerated,
        None,
    )
    return cancellable


def authenticate_network_uri_async(
        uri, credentials, cancellable=None, on_done=None):
    """Mount a discovered server with one-use credentials before browsing it.

    The password is passed only through ``Gio.MountOperation`` and GVfs is
    explicitly told not to save it. ``on_done`` receives an empty string on
    success or a user-facing error message on failure.
    """
    cancellable = cancellable or Gio.Cancellable()
    on_done = on_done or (lambda _error: None)
    safe_uri = _safe_discovery_uri(uri)
    if safe_uri is None or urlsplit(safe_uri).scheme not in SUPPORTED_SCHEMES:
        on_done("The advertised server address is not supported.")
        return cancellable

    server = Gio.File.new_for_uri(safe_uri)
    operation = CredMountOperation(
        credentials.get("username", ""),
        credentials.get("password", ""),
        credentials.get("domain", ""),
    )

    def mounted(source, async_result, _user_data=None):
        try:
            source.mount_enclosing_volume_finish(async_result)
        except GLib.Error as error:
            if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                on_done("")
            elif error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                on_done("")
            elif operation.credentials_rejected:
                on_done("Authentication failed: The server rejected those credentials.")
            else:
                _status, title = classify_mount_error(error)
                on_done(f"{title}: {error.message}")
            return
        on_done("")

    server.mount_enclosing_volume(
        Gio.MountMountFlags.NONE, operation, cancellable, mounted, None
    )
    return cancellable
