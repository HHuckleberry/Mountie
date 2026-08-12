# Mountie

A small GUI for mounting and managing network shares (SMB/CIFS, AFP, NFS,
SFTP, WebDAV) via GVfs.

It runs on any desktop with GVfs and a secret service, and follows the
desktop's own light/dark preference and accent color through the XDG
settings portal — so it looks at home on [COSMIC][cosmic], GNOME or KDE
alike.

- **Add / edit / remove shares** for any host and protocol GVfs supports.
- **One-click toggle** to mount/unmount each share (no `sudo`/`pkexec` needed
  — mounts land in the per-user GVfs mount namespace via `gio`).
- **Credentials saved to your system keyring** (via `libsecret`/GNOME
  Keyring), never written to disk in plaintext.
- **System / Light / Dark themes**, selectable from the appearance button
  and remembered between runs. The default, System, follows the desktop's
  light/dark preference live via the XDG settings portal — the same source
  COSMIC itself drives — and picks up your COSMIC accent color. Because it
  reads the portal rather than the Qt palette, it stays correct without
  `qt5ct`/`qt6ct` and inside a Flatpak sandbox.

[cosmic]: https://github.com/pop-os/cosmic-epoch

## Requirements

- Python 3.9+
- PyQt5
- PyGObject, with the `Gio` and `Secret` (libsecret) typelibs available
- A running secret service (GNOME Keyring, KWallet via a Secret Service
  bridge, etc.)
- GVfs, with backends installed for whichever protocols you use (e.g.
  `gvfs-backends` for SMB/AFP/NFS on Debian/Ubuntu-based distros)

## Install

```sh
./install.sh
```

This installs into a virtualenv at `~/.local/share/mountie/venv` and
registers a launcher entry under `~/.local/share/applications/` pointed at
it, so **Mountie** shows up in your app launcher right away.

A venv is used because most distributions now mark the system Python as
externally managed (PEP 668), which makes `pip install --user` fail. It is
created with `--system-site-packages` so it reuses your distribution's PyQt5
and PyGObject rather than building PyGObject from source.

To install without a launcher entry, or to package it yourself:

```sh
pip install .
```

This installs a `mountie` console script. To add a launcher entry
manually:

```sh
cp data/io.github.HHuckleberry.Mountie.desktop ~/.local/share/applications/
```

(Adjust `Exec=` in that file if `mountie` isn't on your `PATH`, e.g.
when installed into a venv.)

## Where your shares end up

GVfs mounts land at `/run/user/<uid>/gvfs/smb-share:server=…,share=…`, which
is precise and unusable. So each mounted share also gets a symlink in one
predictable directory — `~/Shares/<label>` by default:

```
~/Shares/home -> /run/user/1000/gvfs/smb-share:server=192.168.2.8,share=home
```

Links appear on mount and are removed on unmount, and stale ones are cleaned
up at startup. Anything that isn't a symlink is left strictly alone, so
pointing `link_dir` at a directory holding real files can't destroy them.

To change the location, set `link_dir` in
`~/.config/mountie/config.json`. Using `/mnt` needs a one-time setup,
because `/mnt` is root-owned and this app never asks for root:

```sh
sudo mkdir -p /mnt/shares && sudo chown "$USER" /mnt/shares
```

Then set `"link_dir": "/mnt/shares"`. Set `"links_enabled": false` to turn
the feature off.

## Usage

Run `mountie`, then **Add Share** with a label, host, share/path, and
optional credentials. Flip the toggle to mount or unmount. Status badges
show connected/disconnected/error state; use the refresh button to
re-check all shares against what's actually mounted.

## TODO

- [x] Allow passwordless and anonymous shares to mount without requiring a
  saved keyring password.
- [x] After a failed mount or unmount, query the real mount state and keep the
  toggle, status badge, and symlink synchronized with it.
- [x] When deleting a mounted share, wait for unmounting to finish and handle
  failures before removing its configuration, keyring entry, and symlink.
- [x] Safely handle edits to mounted shares, including unmounting the old
  target and removing or renaming its previous symlink.
- [x] Prevent normalized share labels from producing duplicate symlink names,
  or include a stable unique suffix in each generated name.
- [x] Catch and report configuration, filesystem, and secret-service errors
  instead of terminating the application.
- [x] Validate host and share/path input and construct properly encoded URIs,
  including correct handling for IPv6 addresses and reserved characters.

## Before publishing a fork

`APP_ID` in `mountie/app.py` is the single source of truth for the
libsecret schema, the desktop entry, and the icon name. If you fork this,
change it to a reverse-DNS ID you control — for a GitHub-hosted fork that's
`io.github.<account>.<repo>`, which must resolve to a reachable repository —
so your build doesn't collide with anyone else's over stored secrets.

Renaming strands any credentials already saved under the old ID. To carry
them over:

```sh
python3 scripts/migrate_keyring.py io.github.old.AppId
```

That copies rather than moves, so the old entries stay put and re-running is
harmless.

## License

GPL-3.0-only. See [LICENSE](LICENSE).
