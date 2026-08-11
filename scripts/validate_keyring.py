#!/usr/bin/env python3
"""Round-trips a throwaway secret through the same libsecret schema the app
uses, to confirm credentials actually land in the system keyring rather
than silently falling back to nothing. Never prints the secret value."""

import sys
import uuid

from mountie.app import SECRET_SCHEMA
from gi.repository import Secret


def main():
    test_id = f"mountie-selftest-{uuid.uuid4().hex}"
    test_secret = uuid.uuid4().hex

    Secret.password_store_sync(
        SECRET_SCHEMA, {"share_id": test_id}, Secret.COLLECTION_DEFAULT,
        "Mountie keyring self-test", test_secret, None,
    )

    fetched = Secret.password_lookup_sync(SECRET_SCHEMA, {"share_id": test_id}, None)
    Secret.password_clear_sync(SECRET_SCHEMA, {"share_id": test_id}, None)

    cleared = Secret.password_lookup_sync(SECRET_SCHEMA, {"share_id": test_id}, None)

    if fetched != test_secret:
        print("FAIL: stored secret did not round-trip through the keyring")
        return 1
    if cleared is not None:
        print("FAIL: secret was not removed from the keyring after clear")
        return 1

    print("PASS: store / lookup / clear all round-tripped correctly through the system keyring")
    return 0


if __name__ == "__main__":
    sys.exit(main())
