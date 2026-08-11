#!/usr/bin/env bash
# Installs Mountie and registers it with the desktop launcher.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${MOUNTIE_VENV:-$HOME/.local/share/mountie/venv}"
APP_ID="io.github.HHuckleberry.Mountie"
APPS_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_SRC="$REPO_DIR/data/$APP_ID.desktop"
DESKTOP_DST="$APPS_DIR/$APP_ID.desktop"
BIN_PATH="$VENV_DIR/bin/mountie"

# Most distros now mark the system Python as externally managed (PEP 668),
# so a plain 'pip install --user' is refused. A dedicated venv sidesteps
# that. --system-site-packages lets it reuse the distro's PyQt5 and
# PyGObject, which matters because building PyGObject from a wheel needs
# gobject-introspection dev headers that most desktops don't have.
python3 -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade "$REPO_DIR"

# Fail loudly here rather than leaving a launcher entry that points at
# nothing.
if [ ! -x "$BIN_PATH" ]; then
    echo "error: install finished but $BIN_PATH is missing." >&2
    exit 1
fi

# The icon has to land in the hicolor theme under the app ID before the
# desktop entry's Icon= key can resolve to anything.
mkdir -p "$ICON_DIR"
cp "$REPO_DIR/data/$APP_ID.svg" "$ICON_DIR/$APP_ID.svg"

# Exec uses the venv's absolute path rather than a bare 'mountie',
# so the launcher entry works regardless of the desktop session's PATH.
mkdir -p "$APPS_DIR"
sed "s|^Exec=.*|Exec=$BIN_PATH|" "$DESKTOP_SRC" > "$DESKTOP_DST"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -qtf "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR"
fi

echo "Installed to $VENV_DIR"
echo "'Mountie' should now appear in your application launcher."
