#!/usr/bin/env python3
"""Regenerates scripts/install-native-mount-helper.sh as a single,
self-contained file - no sibling files, so it works no matter how a user
obtained it (git checkout, downloaded from a GitHub release, or exported
from inside the Flatpak by Mountie itself via Settings).

This is a maintainer tool, not something Mountie runs itself. Re-run it and
commit the result whenever data/native-mount-helper/mountie-mount-helper or
its .policy file change. tests/test_native_mount_helper.py cross-checks the
checked-in script against both sources and fails if they've drifted apart.

    python3 scripts/generate_native_mount_installer.py
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "data" / "native-mount-helper"
WRAPPER_SRC = SOURCE_DIR / "mountie-mount-helper"
POLICY_SRC = SOURCE_DIR / "io.github.HHuckleberry.Mountie.policy"
OUTPUT_PATH = REPO_ROOT / "scripts" / "install-native-mount-helper.sh"

WRAPPER_PLACEHOLDER = "__MOUNTIE_WRAPPER_CONTENT__"
POLICY_PLACEHOLDER = "__MOUNTIE_POLICY_CONTENT__"

# Quoted heredoc delimiters ('MOUNTIE_..._EOF') are load-bearing: they stop
# the shell from expanding $variables or `command` substitutions inside the
# embedded Python/XML, which both contain plenty of $ and (in the wrapper's
# f-strings) braces.
TEMPLATE = """#!/usr/bin/env bash
# GENERATED FILE - do not edit by hand. Edit
# data/native-mount-helper/mountie-mount-helper and
# data/native-mount-helper/io.github.HHuckleberry.Mountie.policy instead,
# then run scripts/generate_native_mount_installer.py.
#
# Installs the native-mount privileged helper and its polkit policy onto the
# HOST. Run this once with sudo - identically whether Mountie itself is
# installed natively or as a Flatpak, since a Flatpak sandbox cannot write
# /usr/share/polkit-1/actions or install a host binary itself. Self-contained
# on purpose: no sibling files, so this works wherever you got it from (git
# checkout, a GitHub release, or exported from inside the Flatpak by Mountie
# itself in Settings). Safe to re-run after a Mountie upgrade changes the
# wrapper; it always overwrites both files. See docs/native-mount-backend.md
# for what this actually grants.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run this with sudo" >&2
    exit 1
fi

WRAPPER_DEST="/usr/libexec/mountie-mount-helper"
POLICY_DEST="/usr/share/polkit-1/actions/io.github.HHuckleberry.Mountie.policy"

case "${1:-install}" in
    install)
        ;;
    --uninstall)
        rm -f -- "$WRAPPER_DEST" "$POLICY_DEST"
        echo "Removed $WRAPPER_DEST and $POLICY_DEST."
        exit 0
        ;;
    *)
        echo "usage: $0 [--uninstall]" >&2
        exit 2
        ;;
esac

if ! command -v pkexec >/dev/null 2>&1; then
    echo "error: pkexec was not found. Install polkit first" \\
         "(e.g. 'apt install policykit-1' or 'dnf install polkit')" \\
         "and re-run this script." >&2
    exit 1
fi

# Not fatal: the wrapper and policy can still be installed ahead of time,
# but native mounts will fail with a clear error until this is present.
if ! command -v mount.cifs >/dev/null 2>&1; then
    echo "warning: mount.cifs was not found. Native SMB/CIFS mounts will fail" \\
         "until cifs-utils is installed" \\
         "(e.g. 'apt install cifs-utils' or 'dnf install cifs-utils')." >&2
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

cat > "$WORKDIR/mountie-mount-helper" <<'MOUNTIE_WRAPPER_EOF'
__MOUNTIE_WRAPPER_CONTENT__MOUNTIE_WRAPPER_EOF

cat > "$WORKDIR/io.github.HHuckleberry.Mountie.policy" <<'MOUNTIE_POLICY_EOF'
__MOUNTIE_POLICY_CONTENT__MOUNTIE_POLICY_EOF

install -Dm755 "$WORKDIR/mountie-mount-helper" "$WRAPPER_DEST"
install -Dm644 "$WORKDIR/io.github.HHuckleberry.Mountie.policy" "$POLICY_DEST"

echo "Installed $WRAPPER_DEST and $POLICY_DEST."
echo "Native mount is now available as a per-share option in Mountie (Add Share > Mount using)."
"""


def build_script():
    wrapper = WRAPPER_SRC.read_text(encoding="utf-8")
    policy = POLICY_SRC.read_text(encoding="utf-8")
    if not wrapper.endswith("\n"):
        wrapper += "\n"
    if not policy.endswith("\n"):
        policy += "\n"
    return (
        TEMPLATE
        .replace(WRAPPER_PLACEHOLDER, wrapper)
        .replace(POLICY_PLACEHOLDER, policy)
    )


def main():
    OUTPUT_PATH.write_text(build_script(), encoding="utf-8")
    OUTPUT_PATH.chmod(0o755)
    print(f"Wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
