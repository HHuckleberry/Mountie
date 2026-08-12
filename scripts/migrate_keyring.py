#!/usr/bin/env python3
"""Copies saved share passwords from a previous APP_ID's libsecret schema to
the current one.

The libsecret schema name is derived from APP_ID, so renaming the app ID
(as required before a Flathub submission) leaves existing credentials
stranded under the old name. This copies them across. It never prints a
secret, and never deletes anything: the old entries are left in place, so
re-running is harmless and nothing is lost if the migration is wrong.

    python3 scripts/migrate_keyring.py io.github.old.AppId
"""

import sys

# Imported first: it pins the Secret typelib version, which has to happen
# before gi.repository.Secret is pulled in.
from mountie.credentials import SECRET_SCHEMA
from mountie.settings import APP_ID, load_config

from gi.repository import Secret


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    old_id = sys.argv[1]
    if old_id == APP_ID:
        print(f"Old and current app ID are both {APP_ID}; nothing to do.")
        return 0

    old_schema = Secret.Schema.new(
        old_id, Secret.SchemaFlags.NONE,
        {"share_id": Secret.SchemaAttributeType.STRING},
    )

    shares = load_config()["shares"]
    if not shares:
        print("No shares configured; nothing to migrate.")
        return 0

    migrated = skipped = missing = 0
    for share in shares:
        share_id, label = share["id"], share.get("label", share["id"])
        if Secret.password_lookup_sync(SECRET_SCHEMA, {"share_id": share_id}, None):
            print(f"  = {label}: already present under {APP_ID}")
            skipped += 1
            continue
        secret = Secret.password_lookup_sync(old_schema, {"share_id": share_id}, None)
        if secret is None:
            print(f"  ! {label}: no saved password under {old_id}")
            missing += 1
            continue
        Secret.password_store_sync(
            SECRET_SCHEMA, {"share_id": share_id}, Secret.COLLECTION_DEFAULT,
            "Mountie credentials", secret, None,
        )
        print(f"  + {label}: migrated")
        migrated += 1

    print(f"\n{migrated} migrated, {skipped} already present, {missing} not found.")
    print(f"Old entries under {old_id} were left untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
