#!/usr/bin/env python3
"""Non-destructively exercise one share already configured in Mountie.

No credential values or remote directory entries are printed. If the share
starts disconnected, the check mounts it temporarily and restores that state
afterwards. A share that starts mounted is never disconnected.
"""

import argparse
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

from mountie.credentials import CredentialError, get_password
from mountie.mounts import is_mounted, mount_share, share_uri, unmount_share
from mountie.settings import ConfigError, load_config


TIMEOUT_SECONDS = 30


def wait_for(start):
    """Run one Mountie async operation with a bounded GLib main loop."""
    loop = GLib.MainLoop()
    outcome = {}

    def done(success, status, error):
        outcome.update(success=success, status=status, error=error)
        loop.quit()

    def timeout():
        outcome.update(
            success=False,
            status="timeout",
            error=f"Operation did not finish within {TIMEOUT_SECONDS} seconds.",
        )
        loop.quit()
        return GLib.SOURCE_REMOVE

    timeout_id = GLib.timeout_add_seconds(TIMEOUT_SECONDS, timeout)

    def begin():
        start(done)
        return GLib.SOURCE_REMOVE

    GLib.idle_add(begin)
    loop.run()
    if outcome.get("status") != "timeout":
        GLib.source_remove(timeout_id)
    return outcome


def probe_root(share):
    """Open the remote root asynchronously without reading or printing names."""
    loop = GLib.MainLoop()
    cancellable = Gio.Cancellable()
    outcome = {}
    remote = Gio.File.new_for_uri(share_uri(share))

    def finished(source, result):
        try:
            enumerator = source.enumerate_children_finish(result)
            enumerator.close(None)
            outcome.update(success=True, error=None)
        except GLib.Error as error:
            outcome.update(success=False, error=error.message)
        loop.quit()

    def timeout():
        outcome.update(
            success=False,
            error=f"Share did not respond within {TIMEOUT_SECONDS} seconds.",
        )
        cancellable.cancel()
        loop.quit()
        return GLib.SOURCE_REMOVE

    timeout_id = GLib.timeout_add_seconds(TIMEOUT_SECONDS, timeout)
    remote.enumerate_children_async(
        "standard::type",
        Gio.FileQueryInfoFlags.NONE,
        GLib.PRIORITY_DEFAULT,
        cancellable,
        finished,
    )
    loop.run()
    if not cancellable.is_cancelled():
        GLib.source_remove(timeout_id)
    return outcome


def select_share(shares, label):
    if label is None:
        return shares[0] if len(shares) == 1 else None
    return next((share for share in shares if share["label"] == label), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("label", nargs="?", help="configured share label")
    args = parser.parse_args()

    try:
        shares = load_config()["shares"]
    except ConfigError as error:
        print(f"FAIL: {error}")
        return 1
    share = select_share(shares, args.label)
    if share is None:
        print("FAIL: specify exactly one configured share label.")
        return 2

    originally_mounted = is_mounted(share)
    print(
        f"Testing {share['label']!r}: "
        f"originally {'mounted' if originally_mounted else 'disconnected'}"
    )

    mounted_for_test = False
    try:
        if not originally_mounted:
            try:
                password = get_password(share["id"]) or ""
            except CredentialError as error:
                print(f"FAIL: {error}")
                return 1
            result = wait_for(
                lambda done: mount_share(share, password, done)
            )
            if not result.get("success"):
                print(f"FAIL: {result.get('error') or result.get('status')}")
                return 1
            mounted_for_test = True

        probe = probe_root(share)
        if not probe.get("success"):
            print(f"FAIL: mounted share root could not be opened: {probe.get('error')}")
            return 1
        print("PASS: mount state and remote root access are healthy.")
        return 0
    finally:
        if mounted_for_test:
            cleanup = wait_for(lambda done: unmount_share(share, done))
            if cleanup.get("success"):
                print("Restored original disconnected state.")
            else:
                print(
                    "WARNING: could not restore the original disconnected state: "
                    f"{cleanup.get('error') or cleanup.get('status')}",
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
